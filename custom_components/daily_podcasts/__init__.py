"""
Daily Podcast Queue — a Home Assistant custom integration.

Builds an ordered "playlist" of podcast episodes published *today* (in HA's
local timezone) and queues them onto a Music Assistant player (e.g. Sonos)
back-to-back, preserving the listening order defined in configuration and
silently skipping any podcast with nothing new. Every run also records the
day's playlist so past days can be replayed later (catch-up after a holiday).

Configuration (in configuration.yaml):

    daily_podcasts:
      player: media_player.sonos_kitchen
      podcasts:
        - name: The Daily
          feed_url: https://feeds.simplecast.com/54nAGcIl
        - name: Up First
          feed_url: https://feeds.npr.org/510318/podcast.xml

Services:
    daily_podcasts.build_queue   -> build + record + play today's playlist
    daily_podcasts.play_history  -> replay recorded day(s) (catch-up)
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import os
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_DATE,
    ATTR_DAYS,
    ATTR_DRY_RUN,
    ATTR_END,
    ATTR_PLAYER,
    ATTR_RECORD_ONLY,
    ATTR_SINCE,
    ATTR_START,
    ATTR_TZ,
    CONF_FEED_URL,
    CONF_FETCH_TIMEOUT,
    CONF_HISTORY_DIR,
    CONF_NAME,
    CONF_PLAYER,
    CONF_PODCASTS,
    CONF_RECORD_ONLY,
    CONF_TIMEZONE,
    DATE_FMT,
    DEFAULT_FETCH_TIMEOUT,
    DEFAULT_HISTORY_DIR,
    DEFAULT_RECORD_ONLY,
    DOMAIN,
    LOG_PREFIX,
    MASS_DOMAIN,
    MASS_PLAY_MEDIA,
    SERVICE_BUILD_QUEUE,
    SERVICE_PLAY_HISTORY,
)

import logging

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

PODCAST_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_FEED_URL): cv.url,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_PLAYER): cv.entity_id,
                vol.Required(CONF_PODCASTS): vol.All(
                    cv.ensure_list, [PODCAST_SCHEMA]
                ),
                vol.Optional(CONF_TIMEZONE): cv.string,
                vol.Optional(
                    CONF_FETCH_TIMEOUT, default=DEFAULT_FETCH_TIMEOUT
                ): cv.positive_int,
                vol.Optional(
                    CONF_HISTORY_DIR, default=DEFAULT_HISTORY_DIR
                ): cv.string,
                vol.Optional(
                    CONF_RECORD_ONLY, default=DEFAULT_RECORD_ONLY
                ): cv.boolean,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


BUILD_QUEUE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_PLAYER): cv.entity_id,
        vol.Optional(ATTR_TZ): cv.string,
        vol.Optional(ATTR_DRY_RUN): cv.boolean,
        vol.Optional(ATTR_RECORD_ONLY): cv.boolean,
    }
)

PLAY_HISTORY_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_PLAYER): cv.entity_id,
        vol.Optional(ATTR_DATE): cv.string,
        vol.Optional(ATTR_START): cv.string,
        vol.Optional(ATTR_END): cv.string,
        vol.Optional(ATTR_SINCE): cv.string,
        vol.Optional(ATTR_DAYS): cv.positive_int,
        vol.Optional(ATTR_DRY_RUN): cv.boolean,
    }
)


# ---------------------------------------------------------------------------
# Timezone handling
# ---------------------------------------------------------------------------


def _resolve_local_tz(hass: HomeAssistant, tz_name: str | None):
    """Return a tzinfo for the configured tz, HA's local tz, or system local."""
    from zoneinfo import ZoneInfo

    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "%s Invalid timezone '%s' (%s); falling back to HA local tz.",
                LOG_PREFIX,
                tz_name,
                err,
            )

    ha_tz = getattr(hass.config, "time_zone", None)
    if ha_tz:
        try:
            return ZoneInfo(ha_tz)
        except Exception:  # noqa: BLE001
            pass

    return dt.datetime.now().astimezone().tzinfo


# ---------------------------------------------------------------------------
# Feed fetching + parsing (blocking -> run in executor)
# ---------------------------------------------------------------------------


def _fetch_feed_bytes(feed_url: str, timeout: int) -> bytes:
    """Fetch raw feed bytes over HTTP, bypassing any cached copy."""
    import urllib.request

    req = urllib.request.Request(
        feed_url,
        headers={
            "User-Agent": "HomeAssistant-DailyPodcasts/1.0",
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _entry_published_utc(entry) -> dt.datetime | None:
    tp = entry.get("published_parsed") or entry.get("updated_parsed")
    if tp is None:
        return None
    return dt.datetime(*tp[:6], tzinfo=dt.timezone.utc)


def _extract_audio_url(entry) -> str | None:
    for enc in entry.get("enclosures", []) or []:
        etype = (enc.get("type") or "").lower()
        href = enc.get("href") or enc.get("url")
        if href and (etype.startswith("audio") or not etype):
            return href
    for media in entry.get("media_content", []) or []:
        etype = (media.get("type") or "").lower()
        url = media.get("url")
        if url and (etype.startswith("audio") or not etype):
            return url
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and link.get("href"):
            return link["href"]
    return None


def _parse_latest_episode(raw_bytes: bytes) -> dict[str, Any]:
    """Parse the most recently published episode. Raises ValueError if none."""
    import feedparser

    parsed = feedparser.parse(raw_bytes)

    if getattr(parsed, "bozo", 0) and not parsed.entries:
        raise ValueError(
            f"malformed feed ({getattr(parsed, 'bozo_exception', 'unknown error')})"
        )
    if not parsed.entries:
        raise ValueError("feed contains no entries")

    epoch = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    latest = max(parsed.entries, key=lambda e: _entry_published_utc(e) or epoch)

    published_dt = _entry_published_utc(latest)
    if published_dt is None:
        raise ValueError("latest entry has no parseable publish date")

    audio_url = _extract_audio_url(latest)
    if not audio_url:
        raise ValueError("latest entry has no audio enclosure")

    return {
        "title": latest.get("title", "(untitled episode)"),
        "audio_url": audio_url,
        "published_dt": published_dt,
    }


def _fetch_and_parse(feed_url: str, timeout: int) -> dict[str, Any]:
    """Blocking fetch + parse combined so it runs in one executor job."""
    raw = _fetch_feed_bytes(feed_url, timeout)
    return _parse_latest_episode(raw)


# ---------------------------------------------------------------------------
# History storage (blocking file IO -> run in executor)
# ---------------------------------------------------------------------------


def _write_history_file(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_history_file(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _list_history_dates(base_dir: str) -> list[str]:
    dates: list[str] = []
    for p in glob.glob(os.path.join(base_dir, "*.json")):
        stem = os.path.splitext(os.path.basename(p))[0]
        try:
            dt.datetime.strptime(stem, DATE_FMT)
        except ValueError:
            continue
        dates.append(stem)
    return sorted(dates)


# ---------------------------------------------------------------------------
# Core pipeline (pure; safe to call from executor)
# ---------------------------------------------------------------------------


def _build_episode_list(
    podcasts: list[dict[str, Any]], local_tz, fetch_timeout: int
) -> tuple[list[dict], list[dict], list[dict]]:
    """Iterate the ordered podcast list, returning today's episodes in order."""
    today_local = dt.datetime.now(local_tz).date()
    included: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []

    for entry in podcasts:
        name = (entry or {}).get(CONF_NAME, "(unnamed)")
        feed_url = (entry or {}).get(CONF_FEED_URL)
        if not feed_url:
            errors.append({"name": name, "error": "missing feed_url"})
            _LOGGER.error("%s '%s': missing feed_url; skipping.", LOG_PREFIX, name)
            continue

        try:
            latest = _fetch_and_parse(feed_url, fetch_timeout)
        except Exception as err:  # noqa: BLE001 - isolate per-feed failures
            errors.append({"name": name, "error": str(err)})
            _LOGGER.error(
                "%s '%s': feed fetch/parse failed (%s); continuing.",
                LOG_PREFIX,
                name,
                err,
            )
            continue

        published_local_dt = latest["published_dt"].astimezone(local_tz)
        if published_local_dt.date() == today_local:
            included.append(
                {
                    "name": name,
                    "title": latest["title"],
                    "audio_url": latest["audio_url"],
                    "published_local": published_local_dt.isoformat(),
                }
            )
            _LOGGER.info(
                '%s INCLUDE %r: "%s" (published %s)',
                LOG_PREFIX,
                name,
                latest["title"],
                published_local_dt.isoformat(),
            )
        else:
            skipped.append({"name": name})
            _LOGGER.info(
                "%s SKIP %r: latest episode from %s, not today (%s).",
                LOG_PREFIX,
                name,
                published_local_dt.date(),
                today_local,
            )

    return included, skipped, errors


# ---------------------------------------------------------------------------
# Setup + services
# ---------------------------------------------------------------------------


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Daily Podcast Queue integration from YAML."""
    conf = config.get(DOMAIN)
    if conf is None:
        _LOGGER.warning(
            "%s No `daily_podcasts:` config found; integration idle. "
            "Add a player and podcasts list to configuration.yaml.",
            LOG_PREFIX,
        )
        # Still register services so they exist, but they'll warn without config.
        conf = {}

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["config"] = conf

    async def _async_queue_media(player: str, episodes: list[dict], dry_run: bool):
        """Send the ordered episodes to Music Assistant in one call."""
        media_ids = [ep["audio_url"] for ep in episodes]
        for index, ep in enumerate(episodes, start=1):
            _LOGGER.info(
                "%s QUEUE #%d -> %s: %r \"%s\" [%s]",
                LOG_PREFIX,
                index,
                player,
                ep["name"],
                ep["title"],
                ep["audio_url"],
            )
        if dry_run:
            _LOGGER.info(
                "%s dry_run: would call %s.%s on %s with %d item(s), "
                "enqueue=replace.",
                LOG_PREFIX,
                MASS_DOMAIN,
                MASS_PLAY_MEDIA,
                player,
                len(media_ids),
            )
            return
        await hass.services.async_call(
            MASS_DOMAIN,
            MASS_PLAY_MEDIA,
            {
                "entity_id": player,
                "media_id": media_ids,
                "enqueue": "replace",
            },
            blocking=True,
        )

    def _history_base_dir() -> str:
        cfg = hass.data[DOMAIN]["config"]
        rel = cfg.get(CONF_HISTORY_DIR, DEFAULT_HISTORY_DIR)
        if os.path.isabs(rel):
            return rel
        return hass.config.path(rel)

    def _history_path(date_str: str) -> str:
        return os.path.join(_history_base_dir(), f"{date_str}.json")

    async def _async_save_history(date_str, player, episodes):
        payload = {
            "date": date_str,
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "player": player,
            "episodes": episodes,
        }
        path = _history_path(date_str)
        try:
            await hass.async_add_executor_job(_write_history_file, path, payload)
            _LOGGER.info(
                "%s Recorded %d episode(s) for %s -> %s",
                LOG_PREFIX,
                len(episodes),
                date_str,
                path,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "%s Failed to write history for %s (%s).",
                LOG_PREFIX,
                date_str,
                err,
            )

    # --- Service: build_queue --------------------------------------------
    async def handle_build_queue(call: ServiceCall) -> None:
        cfg = hass.data[DOMAIN]["config"]
        podcasts = cfg.get(CONF_PODCASTS) or []
        if not podcasts:
            _LOGGER.warning(
                "%s No podcasts configured; nothing to do.", LOG_PREFIX
            )
            return

        player = call.data.get(ATTR_PLAYER) or cfg.get(CONF_PLAYER)
        tz_name = call.data.get(ATTR_TZ) or cfg.get(CONF_TIMEZONE)
        dry_run = bool(call.data.get(ATTR_DRY_RUN, False))
        record_only = bool(
            call.data.get(ATTR_RECORD_ONLY, cfg.get(CONF_RECORD_ONLY, False))
        )
        fetch_timeout = cfg.get(CONF_FETCH_TIMEOUT, DEFAULT_FETCH_TIMEOUT)
        local_tz = _resolve_local_tz(hass, tz_name)

        _LOGGER.info(
            "%s Starting run: %d podcast(s), player=%s, dry_run=%s, "
            "record_only=%s",
            LOG_PREFIX,
            len(podcasts),
            player,
            dry_run,
            record_only,
        )

        # Blocking fetch/parse loop runs in the executor.
        included, skipped, errors = await hass.async_add_executor_job(
            _build_episode_list, podcasts, local_tz, fetch_timeout
        )

        _LOGGER.info(
            "%s Summary: %d included, %d skipped, %d error(s).",
            LOG_PREFIX,
            len(included),
            len(skipped),
            len(errors),
        )

        today_str = dt.datetime.now(local_tz).strftime(DATE_FMT)

        if included and not dry_run:
            await _async_save_history(today_str, player, included)

        if not included:
            _LOGGER.info(
                "%s No podcasts published today; leaving the player untouched.",
                LOG_PREFIX,
            )
            return

        _LOGGER.info(
            "%s Final queue order: %s",
            LOG_PREFIX,
            " -> ".join(ep["name"] for ep in included),
        )

        if record_only:
            _LOGGER.info(
                "%s record_only: saved to history, not playing now.", LOG_PREFIX
            )
            return

        await _async_queue_media(player, included, dry_run)

    # --- Service: play_history -------------------------------------------
    def _parse_date_arg(value, label) -> dt.date:
        try:
            return dt.datetime.strptime(str(value), DATE_FMT).date()
        except (ValueError, TypeError) as err:
            raise ValueError(
                f"{label} must be a YYYY-MM-DD date, got {value!r}"
            ) from err

    async def _select_history_dates(local_tz, date, start, end, since, days):
        base = _history_base_dir()
        available = await hass.async_add_executor_job(_list_history_dates, base)
        if not available:
            return []
        available_dates = [_parse_date_arg(d, "history filename") for d in available]

        if date:
            want = _parse_date_arg(date, "date")
            return [d.strftime(DATE_FMT) for d in available_dates if d == want]
        if start or end:
            lo = _parse_date_arg(start, "start") if start else dt.date.min
            hi = _parse_date_arg(end, "end") if end else dt.date.max
            return [d.strftime(DATE_FMT) for d in available_dates if lo <= d <= hi]
        if since:
            lo = _parse_date_arg(since, "since")
            today = dt.datetime.now(local_tz).date()
            return [
                d.strftime(DATE_FMT) for d in available_dates if lo <= d <= today
            ]
        if days:
            n = int(days)
            if n <= 0:
                return []
            return [d.strftime(DATE_FMT) for d in available_dates][-n:]
        return [d.strftime(DATE_FMT) for d in available_dates]

    async def _load_history_episodes(date_str: str) -> list[dict]:
        path = _history_path(date_str)
        try:
            payload = await hass.async_add_executor_job(_read_history_file, path)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "%s Could not read history for %s (%s); skipping.",
                LOG_PREFIX,
                date_str,
                err,
            )
            return []
        return (payload or {}).get("episodes", []) or []

    async def handle_play_history(call: ServiceCall) -> None:
        cfg = hass.data[DOMAIN]["config"]
        player = call.data.get(ATTR_PLAYER) or cfg.get(CONF_PLAYER)
        dry_run = bool(call.data.get(ATTR_DRY_RUN, False))
        local_tz = _resolve_local_tz(hass, cfg.get(CONF_TIMEZONE))

        try:
            selected = await _select_history_dates(
                local_tz,
                call.data.get(ATTR_DATE),
                call.data.get(ATTR_START),
                call.data.get(ATTR_END),
                call.data.get(ATTR_SINCE),
                call.data.get(ATTR_DAYS),
            )
        except ValueError as err:
            _LOGGER.error("%s play_history: %s", LOG_PREFIX, err)
            return

        if not selected:
            _LOGGER.info(
                "%s play_history: no recorded playlists match; nothing to play.",
                LOG_PREFIX,
            )
            return

        _LOGGER.info(
            "%s Catch-up: replaying %d day(s): %s",
            LOG_PREFIX,
            len(selected),
            ", ".join(selected),
        )

        combined: list[dict] = []
        for date_str in selected:
            day_eps = await _load_history_episodes(date_str)
            if not day_eps:
                _LOGGER.info(
                    "%s   %s: no episodes recorded; skipping.",
                    LOG_PREFIX,
                    date_str,
                )
                continue
            _LOGGER.info(
                "%s   %s: %s",
                LOG_PREFIX,
                date_str,
                " -> ".join(ep.get("name", "?") for ep in day_eps),
            )
            combined.extend(day_eps)

        if not combined:
            _LOGGER.info(
                "%s play_history: selected days had no episodes; nothing queued.",
                LOG_PREFIX,
            )
            return

        _LOGGER.info(
            "%s Catch-up queue: %d episode(s) across %d day(s).",
            LOG_PREFIX,
            len(combined),
            len(selected),
        )
        await _async_queue_media(player, combined, dry_run)

    hass.services.async_register(
        DOMAIN, SERVICE_BUILD_QUEUE, handle_build_queue, schema=BUILD_QUEUE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PLAY_HISTORY, handle_play_history, schema=PLAY_HISTORY_SCHEMA
    )

    _LOGGER.info(
        "%s Ready. Services: %s.%s, %s.%s",
        LOG_PREFIX,
        DOMAIN,
        SERVICE_BUILD_QUEUE,
        DOMAIN,
        SERVICE_PLAY_HISTORY,
    )
    return True
