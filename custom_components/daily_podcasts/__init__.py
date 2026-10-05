"""
Daily Podcast Queue — a Home Assistant custom integration.

Builds an ordered "playlist" of podcast episodes published *today* (in HA's
local timezone) and queues them onto a Music Assistant player (e.g. Sonos)
back-to-back, preserving the listening order defined in configuration and
silently skipping any podcast with nothing new. Every run also records the
day's playlist so past days can be replayed later (catch-up after a holiday).

Configuration (in configuration.yaml):

    daily_podcasts:
      player: media_player.office      # your Music Assistant / Sonos player
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

import asyncio
import datetime as dt
import glob
import json
import os
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import (
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_DATE,
    ATTR_DAYS,
    ATTR_DRY_RUN,
    ATTR_END,
    ATTR_PLAY,
    ATTR_PLAYER,
    ATTR_SINCE,
    ATTR_START,
    ATTR_TZ,
    CONF_AT,
    CONF_ENABLED,
    CONF_FEED_URL,
    CONF_FETCH_TIMEOUT,
    CONF_HISTORY_DIR,
    CONF_INTRADAY_ENABLED,
    CONF_INTRADAY_END_HOUR,
    CONF_INTRADAY_INTERVAL_HOURS,
    CONF_INTRADAY_START_HOUR,
    CONF_NAME,
    CONF_CATCHUP,
    CONF_MAX_LOOKBACK_DAYS,
    CONF_PLAYER,
    CONF_PODCASTS,
    CONF_TIMEZONE,
    CONF_WEEKEND_CATCHUP,
    DATE_FMT,
    DEFAULT_AT,
    DEFAULT_CATCHUP,
    DEFAULT_ENABLED,
    DEFAULT_FETCH_TIMEOUT,
    DEFAULT_HISTORY_DIR,
    DEFAULT_INTRADAY_ENABLED,
    DEFAULT_INTRADAY_END_HOUR,
    DEFAULT_INTRADAY_INTERVAL_HOURS,
    DEFAULT_INTRADAY_START_HOUR,
    DEFAULT_MAX_LOOKBACK_DAYS,
    DOMAIN,
    ATTR_POSITION,
    HWM_STORAGE_KEY,
    HWM_STORAGE_VERSION,
    LOG_PREFIX,
    MASS_DOMAIN,
    MASS_PLAY_MEDIA,
    SERVICE_BUILD_QUEUE,
    SERVICE_GET_QUEUE,
    SERVICE_LIST_PODCASTS,
    SERVICE_PLAY_HISTORY,
    SERVICE_REMOVE_FROM_QUEUE,
    SERVICE_SET_PODCASTS,
    SERVICE_SKIP_TO,
)
from .frontend import async_register_panel, async_remove_panel

import logging

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

PODCAST_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_FEED_URL): cv.url,
        # Accept both the new key and the legacy weekend_catchup (migrated on
        # read in _normalise_podcasts).
        vol.Optional(CONF_CATCHUP): cv.boolean,
        vol.Optional(CONF_WEEKEND_CATCHUP): cv.boolean,
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
                    CONF_MAX_LOOKBACK_DAYS, default=DEFAULT_MAX_LOOKBACK_DAYS
                ): cv.positive_int,
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
        vol.Optional(ATTR_PLAY): cv.boolean,
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

GET_QUEUE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_PLAYER): cv.entity_id,
    }
)

SKIP_TO_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_PLAYER): cv.entity_id,
        vol.Required(ATTR_POSITION): vol.All(int, vol.Range(min=0)),
    }
)

REMOVE_FROM_QUEUE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_PLAYER): cv.entity_id,
        vol.Required(ATTR_POSITION): vol.All(int, vol.Range(min=0)),
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


def _entry_guid(entry, audio_url: str) -> str:
    """A stable per-episode identity for de-duplication across runs.

    Prefers the RSS <guid> (feedparser exposes it as entry.id/guid); falls back
    to the audio enclosure URL, which is stable enough in practice.
    """
    guid = entry.get("id") or entry.get("guid")
    return str(guid) if guid else audio_url


def _parse_feed_episodes(raw_bytes: bytes) -> list[dict[str, Any]]:
    """Parse ALL usable episodes from a feed, each with its publish date.

    Returns a list of {guid, title, audio_url, published_dt (aware UTC)} for
    every entry that has both a parseable publish date and an audio enclosure,
    sorted oldest-first. Raises ValueError only if the feed itself is unusable
    (so a single entry missing an enclosure doesn't sink the whole feed).
    """
    import feedparser

    parsed = feedparser.parse(raw_bytes)

    if getattr(parsed, "bozo", 0) and not parsed.entries:
        raise ValueError(
            f"malformed feed ({getattr(parsed, 'bozo_exception', 'unknown error')})"
        )
    if not parsed.entries:
        raise ValueError("feed contains no entries")

    episodes: list[dict[str, Any]] = []
    for entry in parsed.entries:
        published_dt = _entry_published_utc(entry)
        if published_dt is None:
            continue
        audio_url = _extract_audio_url(entry)
        if not audio_url:
            continue
        episodes.append(
            {
                "guid": _entry_guid(entry, audio_url),
                "title": entry.get("title", "(untitled episode)"),
                "audio_url": audio_url,
                "published_dt": published_dt,
            }
        )

    if not episodes:
        raise ValueError("feed has no entries with a date and audio enclosure")

    # Oldest-first, so same-day episodes play in the order they were released.
    episodes.sort(key=lambda e: e["published_dt"])
    return episodes


def _fetch_and_parse(feed_url: str, timeout: int) -> list[dict[str, Any]]:
    """Blocking fetch + parse combined so it runs in one executor job."""
    raw = _fetch_feed_bytes(feed_url, timeout)
    return _parse_feed_episodes(raw)


# ---------------------------------------------------------------------------
# History storage (blocking file IO -> run in executor)
# ---------------------------------------------------------------------------


def _write_history_file(path: str, payload: dict[str, Any]) -> None:
    """Write a day's history, merging with any existing file for that day.

    A day's file is the log of everything served that day. We MERGE (union by
    guid, preserving order and appending new items) rather than overwrite, so a
    second run on the same day never drops episodes an earlier run recorded.
    This keeps `play_history date:<day>` complete and keeps the de-dupe set
    stable.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)

    existing: list[dict] = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                existing = (json.load(fh) or {}).get("episodes", []) or []
        except Exception:  # noqa: BLE001 - treat unreadable as empty
            existing = []

    def _key(ep):
        return ep.get("guid") or ep.get("audio_url")

    merged = list(existing)
    seen = {_key(ep) for ep in existing}
    for ep in payload.get("episodes", []) or []:
        if _key(ep) not in seen:
            merged.append(ep)
            seen.add(_key(ep))

    payload = dict(payload)
    payload["episodes"] = merged

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


def _recent_guids(base_dir: str, days: int) -> set[str]:
    """Collect episode GUIDs from the most recent `days` history files.

    Used to de-duplicate: an episode already recorded in a recent run is never
    offered again, even if its feed timestamp is fuzzy. Blocking (executor).
    """
    guids: set[str] = set()
    recent = _list_history_dates(base_dir)[-max(1, days):]
    for date_str in recent:
        path = os.path.join(base_dir, f"{date_str}.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:  # noqa: BLE001 - ignore unreadable history
            continue
        for ep in (payload or {}).get("episodes", []) or []:
            g = ep.get("guid") or ep.get("audio_url")
            if g:
                guids.add(str(g))
    return guids


def _history_latest_by_name(base_dir: str, days: int) -> dict[str, str]:
    """Newest recorded `published_local` ISO per podcast name, from history.

    Used to backfill high-water marks after upgrading from a version that
    recorded history but didn't track HWMs, so already-recorded episodes aren't
    re-offered. Keyed by podcast name (history doesn't store feed_url); the
    caller maps names to feed URLs. Blocking (executor).
    """
    latest: dict[str, str] = {}
    recent = _list_history_dates(base_dir)[-max(1, days):]
    for date_str in recent:
        path = os.path.join(base_dir, f"{date_str}.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:  # noqa: BLE001
            continue
        for ep in (payload or {}).get("episodes", []) or []:
            name = ep.get("name")
            pub = ep.get("published_local")
            if not name or not pub:
                continue
            prev = latest.get(name)
            if prev is None or pub > prev:
                latest[name] = pub
    return latest


def _history_latest_by_feed_url(base_dir: str, days: int) -> dict[str, str]:
    """Newest recorded `published_local` ISO per feed_url, from history.

    feed_url is the stable per-feed identity (unlike the user-facing name), so
    this is the primary source for backfilling high-water marks (H2). History
    entries written before feed_url was recorded simply lack the key and are
    skipped here; the caller falls back to name-keyed correlation for those.
    Blocking (executor).
    """
    latest: dict[str, str] = {}
    recent = _list_history_dates(base_dir)[-max(1, days):]
    for date_str in recent:
        path = os.path.join(base_dir, f"{date_str}.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:  # noqa: BLE001
            continue
        for ep in (payload or {}).get("episodes", []) or []:
            feed_url = ep.get("feed_url")
            pub = ep.get("published_local")
            if not feed_url or not pub:
                continue
            prev = latest.get(feed_url)
            if prev is None or pub > prev:
                latest[feed_url] = pub
    return latest


# ---------------------------------------------------------------------------
# Core pipeline (pure; safe to call from executor)
# ---------------------------------------------------------------------------


def _parse_iso(value) -> dt.datetime | None:
    """Parse an ISO timestamp into an aware UTC datetime, or None."""
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _build_episode_list(
    podcasts: list[dict[str, Any]],
    local_tz,
    fetch_timeout: int,
    hwm: dict[str, str],
    seen_guids: set[str],
    max_lookback_days: int,
) -> tuple[list[dict], list[dict], list[dict], dict[str, str]]:
    """Return eligible episodes per feed, in listening order.

    Catch-up model (per podcast, default on): include every episode published
    *after that podcast's high-water mark* (``hwm[feed_url]`` — the publish time
    of the newest episode previously included), through now. This picks up
    whatever was missed since the last successful prepare, no matter how many
    days elapsed, with no weekday special-casing.

    - First run (no HWM for a feed, e.g. a newly-added podcast): offers only the
      podcast's *newest* episode within the look-back window, so adding a podcast
      immediately gives you its latest episode without a backlog. The HWM is then
      set and normal "everything since" catch-up takes over.
    - A ``max_lookback_days`` floor caps how far back the window can reach, so an
      ancient HWM (long outage) can't dump a huge backlog.
    - Catch-up OFF for a podcast: window is just *today* (local date), ignoring
      the HWM for selection.
    - GUID de-duplication: episodes whose guid is already in ``seen_guids``
      (recent history) are never re-included, even if timestamps are fuzzy.
    - ALL eligible episodes are included (oldest-first within a feed); feeds keep
      configured list order.

    Returns (included, skipped, errors, new_hwm). ``new_hwm`` maps feed_url ->
    ISO publish time of the newest included episode for feeds that had matches;
    the caller advances the stored HWM from it *only* on a successful prepare.
    Feeds that errored or matched nothing are absent from new_hwm (HWM unchanged
    -> retried next run).
    """
    now_utc = dt.datetime.now(dt.timezone.utc)
    today_local = dt.datetime.now(local_tz).date()
    lookback_floor = now_utc - dt.timedelta(days=max(1, int(max_lookback_days)))

    included: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []
    new_hwm: dict[str, str] = {}

    for entry in podcasts:
        name = (entry or {}).get(CONF_NAME, "(unnamed)")
        feed_url = (entry or {}).get(CONF_FEED_URL)
        if not feed_url:
            errors.append({"name": name, "error": "missing feed_url"})
            _LOGGER.error("%s '%s': missing feed_url; skipping.", LOG_PREFIX, name)
            continue

        catchup = bool((entry or {}).get(CONF_CATCHUP, DEFAULT_CATCHUP))

        try:
            feed_episodes = _fetch_and_parse(feed_url, fetch_timeout)
        except Exception as err:  # noqa: BLE001 - isolate per-feed failures
            errors.append({"name": name, "error": str(err)})
            _LOGGER.error(
                "%s '%s': feed fetch/parse failed (%s); continuing (HWM kept).",
                LOG_PREFIX,
                name,
                err,
            )
            continue

        feed_hwm = _parse_iso(hwm.get(feed_url)) if catchup else None
        first_run = catchup and feed_hwm is None
        if catchup:
            # Window lower bound: after the HWM, but no earlier than the
            # lookback floor. First run (no HWM) considers the whole look-back
            # window but is trimmed below to just the newest episode, so a
            # freshly-added podcast immediately offers its latest episode
            # without dumping a backlog.
            if feed_hwm is None:
                lower = lookback_floor
            else:
                lower = max(feed_hwm, lookback_floor)
            window_desc = (
                f"latest since {lower.isoformat()}"
                if first_run
                else f"after {lower.isoformat()}"
            )

            # Bind lower/feed_hwm as defaults so the closure captures *this*
            # feed's values, not the loop variable (late-binding gotcha).
            def _in_window(pub, _lower=lower, _first=feed_hwm is None):
                # First run is inclusive of the lookback floor; otherwise
                # strictly after the HWM so a marked episode isn't re-offered.
                return pub >= _lower if _first else pub > _lower
        else:
            # Catch-up off: just today's local date.
            window_desc = f"today ({today_local})"

            def _in_window(pub, _tz=local_tz, _today=today_local):
                return pub.astimezone(_tz).date() == _today

        # Episodes in the window, split into new (to queue) vs already-seen
        # (de-duped). We track the newest IN-WINDOW publish time regardless of
        # de-dupe so the HWM always advances past episodes we've already
        # accounted for -- otherwise a de-duped episode would be re-offered the
        # moment it falls out of the recent-history de-dupe set.
        matched = []
        newest_in_window = None  # aware UTC datetime
        for ep in feed_episodes:
            pub = ep["published_dt"]
            if not _in_window(pub):
                continue
            if newest_in_window is None or pub > newest_in_window:
                newest_in_window = pub
            if ep["guid"] in seen_guids:
                _LOGGER.debug(
                    "%s de-dupe %r: already seen guid %s",
                    LOG_PREFIX,
                    name,
                    ep["guid"],
                )
                continue
            matched.append(ep)

        # First run for a newly-added podcast: offer only its newest episode
        # (feed_episodes/matched are oldest-first, so keep the last), so adding
        # a podcast gives you its latest episode rather than a backlog. After
        # this, the HWM is set and normal catch-up takes over.
        if first_run and len(matched) > 1:
            matched = matched[-1:]

        # Advance this feed's HWM to the newest in-window episode we saw (new or
        # de-duped). Catch-up-off feeds don't use the HWM, so leave theirs alone.
        if catchup and newest_in_window is not None:
            new_hwm[feed_url] = newest_in_window.astimezone(
                dt.timezone.utc
            ).isoformat()

        if not matched:
            skipped.append({"name": name})
            _LOGGER.info(
                "%s SKIP %r: nothing new (%s).", LOG_PREFIX, name, window_desc
            )
            continue

        for ep in matched:
            published_local_dt = ep["published_dt"].astimezone(local_tz)
            included.append(
                {
                    "name": name,
                    # feed_url is the stable identity for HWM backfill and
                    # correlation; name is only a user-facing label (H2).
                    CONF_FEED_URL: feed_url,
                    "guid": ep["guid"],
                    "title": ep["title"],
                    "audio_url": ep["audio_url"],
                    "published_local": published_local_dt.isoformat(),
                }
            )
            _LOGGER.info(
                '%s INCLUDE %r: "%s" (published %s)',
                LOG_PREFIX,
                name,
                ep["title"],
                published_local_dt.isoformat(),
            )
        if len(matched) > 1:
            _LOGGER.info(
                "%s   (%d episodes from %r)", LOG_PREFIX, len(matched), name
            )

    return included, skipped, errors, new_hwm


# ---------------------------------------------------------------------------
# Setup + services
# ---------------------------------------------------------------------------


def _normalise_podcasts(podcasts: list[dict]) -> list[dict]:
    """Migrate legacy per-podcast fields to the current shape.

    Maps the old ``weekend_catchup`` flag onto the new ``catchup`` flag when
    ``catchup`` isn't already set. Non-destructive and idempotent.
    """
    out = []
    for p in podcasts or []:
        p = dict(p or {})
        if CONF_CATCHUP not in p and CONF_WEEKEND_CATCHUP in p:
            p[CONF_CATCHUP] = bool(p[CONF_WEEKEND_CATCHUP])
        p.pop(CONF_WEEKEND_CATCHUP, None)
        out.append(p)
    return out


def _entry_config(hass: HomeAssistant) -> dict[str, Any]:
    """Return the merged config for the active config entry (options first)."""
    store = hass.data.get(DOMAIN, {})
    entry: ConfigEntry | None = store.get("entry")
    if entry is None:
        return {}
    # Options hold the live, UI-editable settings; fall back to entry.data.
    merged = dict(entry.data)
    merged.update(entry.options or {})
    merged[CONF_PODCASTS] = _normalise_podcasts(merged.get(CONF_PODCASTS))
    return merged


def _active_entry(hass: HomeAssistant) -> ConfigEntry | None:
    entry = hass.data.get(DOMAIN, {}).get("entry")
    if entry is not None:
        return entry
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register management services and perform the legacy YAML import."""
    hass.data.setdefault(DOMAIN, {})

    # --- Management services used by the Daily Podcasts sidebar panel ----
    async def handle_list_podcasts(call: ServiceCall) -> dict[str, Any]:
        """Return the current ordered podcast list for the sidebar panel."""
        cfg = _entry_config(hass)
        podcasts = []
        for p in cfg.get(CONF_PODCASTS) or []:
            podcasts.append(
                {
                    CONF_NAME: p.get(CONF_NAME, ""),
                    CONF_FEED_URL: p.get(CONF_FEED_URL, ""),
                    CONF_CATCHUP: bool(p.get(CONF_CATCHUP, DEFAULT_CATCHUP)),
                }
            )
        return {"podcasts": podcasts, "player": cfg.get(CONF_PLAYER)}

    async def handle_set_podcasts(call: ServiceCall) -> None:
        """Replace the whole ordered podcast list (add/remove/edit/reorder)."""
        entry = _active_entry(hass)
        if entry is None:
            _LOGGER.error(
                "%s set_podcasts: integration not set up yet.", LOG_PREFIX
            )
            return

        raw = call.data.get("podcasts") or []
        cleaned: list[dict] = []
        for item in raw:
            name = str((item or {}).get(CONF_NAME, "")).strip()
            url = str((item or {}).get(CONF_FEED_URL, "")).strip()
            if not name or not url:
                continue
            try:
                cv.url(url)
            except vol.Invalid:
                _LOGGER.warning(
                    "%s set_podcasts: skipping %r, invalid feed_url %r",
                    LOG_PREFIX,
                    name,
                    url,
                )
                continue
            cleaned.append(
                {
                    CONF_NAME: name,
                    CONF_FEED_URL: url,
                    CONF_CATCHUP: bool((item or {}).get(CONF_CATCHUP, DEFAULT_CATCHUP)),
                }
            )

        new_options = dict(entry.options)
        new_options[CONF_PODCASTS] = cleaned
        # Updating options triggers the OptionsFlowWithReload reload path.
        hass.config_entries.async_update_entry(entry, options=new_options)
        _LOGGER.info(
            "%s set_podcasts: saved %d podcast(s) from the sidebar panel.",
            LOG_PREFIX,
            len(cleaned),
        )

        # Reconcile the HWM dict against the configured feed_url set so a
        # removed feed drops its stale mark and a later re-add is a clean
        # first-run (M4). Done AFTER async_update_entry so the subsequent
        # reload (async_setup_entry) re-reads the now-pruned store. Best-effort:
        # skip quietly if setup hasn't loaded the lock/HWM yet, and never raise
        # out of the service handler on a save failure.
        store = hass.data.get(DOMAIN, {})
        build_lock = store.get("build_lock")
        hwm_store = store.get("hwm_store")
        if build_lock is not None and hwm_store is not None and "hwm" in store:
            configured_urls = {c[CONF_FEED_URL] for c in cleaned}
            async with build_lock:
                live = dict(hass.data[DOMAIN].get("hwm", {}))
                pruned = {
                    u: v for u, v in live.items() if u in configured_urls
                }
                if pruned != live:
                    hass.data[DOMAIN]["hwm"] = pruned
                    dropped = len(live) - len(pruned)
                    try:
                        await hwm_store.async_save(pruned)
                        _LOGGER.info(
                            "%s set_podcasts: pruned %d stale HWM entr(y/ies) "
                            "for removed feed(s).",
                            LOG_PREFIX,
                            dropped,
                        )
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.error(
                            "%s set_podcasts: failed to persist pruned HWM "
                            "(%s); in-memory state updated.",
                            LOG_PREFIX,
                            err,
                        )

    if not hass.services.has_service(DOMAIN, SERVICE_LIST_PODCASTS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_LIST_PODCASTS,
            handle_list_podcasts,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SET_PODCASTS):
        hass.services.async_register(
            DOMAIN, SERVICE_SET_PODCASTS, handle_set_podcasts
        )

    # --- One-time YAML import (legacy) -----------------------------------
    conf = config.get(DOMAIN)
    if conf and not hass.config_entries.async_entries(DOMAIN):
        _LOGGER.info(
            "%s Importing YAML configuration into a UI config entry. You can "
            "remove the `daily_podcasts:` block from configuration.yaml after.",
            LOG_PREFIX,
        )
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
            )
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry (the UI-managed path)."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["entry"] = entry

    # The management UI is a dedicated sidebar panel, not a Lovelace card.
    await async_register_panel(hass)

    # Per-podcast high-water marks (feed_url -> ISO publish time of the newest
    # episode already included), persisted across restarts via Store.
    hwm_store: Store = Store(hass, HWM_STORAGE_VERSION, HWM_STORAGE_KEY)
    hass.data[DOMAIN]["hwm_store"] = hwm_store
    hwm: dict[str, str] = (await hwm_store.async_load()) or {}
    hass.data[DOMAIN]["hwm"] = hwm

    # Single lock serialising the whole read-modify-write span of a build
    # (daily/intraday/manual) and the set_podcasts HWM reconcile, so concurrent
    # runs can't clobber each other's HWM advances or race on the Store. Created
    # with setdefault so a config reload reuses the existing lock.
    hass.data[DOMAIN].setdefault("build_lock", asyncio.Lock())

    async def _async_save_hwm() -> None:
        await hwm_store.async_save(hass.data[DOMAIN].get("hwm", {}))

    # One-time backfill: for feeds with no HWM yet, seed it from the newest
    # episode already recorded in history, so upgrading from a version that
    # recorded history but didn't track HWMs doesn't re-offer those episodes.
    cfg0 = _entry_config(hass)
    podcasts0 = cfg0.get(CONF_PODCASTS) or []
    missing = [
        p for p in podcasts0
        if p.get(CONF_FEED_URL) and p.get(CONF_FEED_URL) not in hwm
    ]
    if missing:
        base_dir0 = cfg0.get(CONF_HISTORY_DIR, DEFAULT_HISTORY_DIR)
        if not os.path.isabs(base_dir0):
            base_dir0 = hass.config.path(base_dir0)
        lookback0 = int(cfg0.get(CONF_MAX_LOOKBACK_DAYS, DEFAULT_MAX_LOOKBACK_DAYS))
        # Correlate history to feeds by feed_url (the stable identity); fall
        # back to name only for pre-upgrade history entries that predate the
        # feed_url being recorded, so old data still seeds correctly (H2).
        latest_by_url = await hass.async_add_executor_job(
            _history_latest_by_feed_url, base_dir0, lookback0
        )
        latest_by_name = await hass.async_add_executor_job(
            _history_latest_by_name, base_dir0, lookback0
        )
        changed = False
        for p in missing:
            iso = latest_by_url.get(p[CONF_FEED_URL])
            if iso is None:
                iso = latest_by_name.get(p.get(CONF_NAME))
            if iso:
                hwm[p[CONF_FEED_URL]] = iso
                changed = True
                _LOGGER.info(
                    "%s Backfilled HWM for %r from history: %s",
                    LOG_PREFIX,
                    p.get(CONF_NAME),
                    iso,
                )
        if changed:
            hass.data[DOMAIN]["hwm"] = hwm
            await _async_save_hwm()

    async def _async_queue_media(player: str, episodes: list[dict], dry_run: bool):
        """Queue the ordered episodes onto the player.

        Prefers Music Assistant's ``mass.play_media`` when that service is
        registered (it accepts the whole ordered list in one call, enqueue=replace).

        Without Music Assistant it uses the built-in ``media_player`` services,
        which it drives in a Sonos-correct way for plain HTTP enclosure URLs:

          1. ``media_player.clear_playlist`` — empty the queue first, so a
             re-run rebuilds the same queue instead of appending duplicates
             (idempotent).
          2. First episode -> ``enqueue: play`` — this adds the URL to the
             *queue* and starts it. (``enqueue: replace`` is deliberately NOT
             used: for a bare URL Sonos treats replace as a one-off stream and
             never builds a queue, so later items can't append -- the bug this
             fixes.)
          3. Remaining episodes -> ``enqueue: add`` — append to that queue in
             order, so playback advances episode to episode.
        """
        media_ids = [ep["audio_url"] for ep in episodes]
        use_mass = hass.services.has_service(MASS_DOMAIN, MASS_PLAY_MEDIA)
        method = "mass.play_media" if use_mass else "media_player.play_media"

        for index, ep in enumerate(episodes, start=1):
            _LOGGER.info(
                "%s QUEUE #%d -> %s via %s: %r \"%s\" [%s]",
                LOG_PREFIX,
                index,
                player,
                method,
                ep["name"],
                ep["title"],
                ep["audio_url"],
            )

        if dry_run:
            _LOGGER.info(
                "%s dry_run: would queue %d item(s) on %s via %s.",
                LOG_PREFIX,
                len(media_ids),
                player,
                method,
            )
            return

        if use_mass:
            # Music Assistant takes the whole ordered list in one call.
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
            return

        # Native fallback (e.g. the Sonos integration). Before touching the
        # queue, confirm the target entity exists and can actually enqueue
        # media. A common misconfiguration is pointing at the wrong entity for
        # a speaker -- e.g. the Alexa Media Player entity for a Sonos, which
        # accepts a bare play_media but cannot build a queue and fails with an
        # opaque "music is not available as a music provider" error. Catching
        # that here produces an actionable message instead.
        #
        # MediaPlayerEntityFeature.MEDIA_ENQUEUE == 2097152. Using the raw bit
        # avoids importing the media_player component just for one flag.
        MEDIA_ENQUEUE_FEATURE = 2097152

        state = hass.states.get(player)
        if state is None:
            raise HomeAssistantError(
                f"{LOG_PREFIX} Player '{player}' not found. Set a valid "
                "media_player in the Daily Podcast Queue options (Settings -> "
                "Devices & services -> Daily Podcast Queue -> Configure)."
            )

        features = state.attributes.get("supported_features", 0) or 0
        if not features & MEDIA_ENQUEUE_FEATURE:
            raise HomeAssistantError(
                f"{LOG_PREFIX} Player '{player}' does not support queueing "
                "(MEDIA_ENQUEUE). This usually means it is the wrong entity "
                "for the speaker -- e.g. an Alexa Media Player entity for a "
                "Sonos. Point the integration at the speaker's Sonos-native "
                "media_player entity instead, in Settings -> Devices & "
                "services -> Daily Podcast Queue -> Configure."
            )

        # Clear the queue first, then build a real queue: first item "play",
        # rest "add".
        try:
            await hass.services.async_call(
                "media_player",
                "clear_playlist",
                {"entity_id": player},
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001 - not fatal; continue
            _LOGGER.debug(
                "%s clear_playlist failed/unsupported on %s (%s); continuing.",
                LOG_PREFIX,
                player,
                err,
            )

        for index, media_id in enumerate(media_ids):
            enqueue = "play" if index == 0 else "add"
            try:
                await hass.services.async_call(
                    "media_player",
                    "play_media",
                    {
                        "entity_id": player,
                        "media_content_id": media_id,
                        "media_content_type": "music",
                        "enqueue": enqueue,
                    },
                    blocking=True,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.error(
                    "%s Failed to queue episode #%d on %s (%s).",
                    LOG_PREFIX,
                    index + 1,
                    player,
                    err,
                )
                raise
            # Give Sonos a moment to establish the queue after the first item
            # before appending the rest, so the appends land reliably.
            if index == 0 and len(media_ids) > 1:
                await asyncio.sleep(3)

    def _history_base_dir() -> str:
        cfg = _entry_config(hass)
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
            # History is the source of truth: if the write didn't land, re-raise
            # so the caller (_commit_build) aborts the HWM advance instead of
            # moving the mark past episodes that were never durably recorded.
            _LOGGER.error(
                "%s Failed to write history for %s (%s).",
                LOG_PREFIX,
                date_str,
                err,
            )
            raise

    # --- Core build routine (used by service + daily scheduler) ----------
    async def _run_build(player=None, tz_name=None, dry_run=False, play=True):
        """Build the catch-up playlist, record it, and optionally play.

        Includes everything new since each podcast's high-water mark (see
        _build_episode_list). On a successful, non-dry record the per-podcast
        HWM is advanced so the same episodes aren't offered again.

        play=True  -> record + play now (manual / button / default service call)
        play=False -> record only ("prepare the playlist"); the daily schedule
                      uses this so nothing starts playing on its own.
        """
        cfg = _entry_config(hass)
        podcasts = cfg.get(CONF_PODCASTS) or []
        if not podcasts:
            _LOGGER.warning(
                "%s No podcasts configured; add some in Settings -> Devices & "
                "services -> Daily Podcast Queue -> Configure.",
                LOG_PREFIX,
            )
            return

        player = player or cfg.get(CONF_PLAYER)
        tz_name = tz_name or cfg.get(CONF_TIMEZONE)
        dry_run = bool(dry_run)
        play = bool(play)
        fetch_timeout = cfg.get(CONF_FETCH_TIMEOUT, DEFAULT_FETCH_TIMEOUT)
        max_lookback = cfg.get(CONF_MAX_LOOKBACK_DAYS, DEFAULT_MAX_LOOKBACK_DAYS)
        local_tz = _resolve_local_tz(hass, tz_name)

        # Hold the build lock across the ENTIRE read-modify-write span of this
        # build -- from the HWM snapshot, through the feed I/O, to the history
        # write and HWM save in _commit_build -- so concurrent builds (daily /
        # intraday / manual) are serialised and can't clobber each other (C1).
        async with hass.data[DOMAIN]["build_lock"]:
            hwm = dict(hass.data[DOMAIN].get("hwm", {}))
            base_dir = _history_base_dir()
            seen_guids = await hass.async_add_executor_job(
                _recent_guids, base_dir, int(max_lookback)
            )

            _LOGGER.info(
                "%s Starting run: %d podcast(s), player=%s, dry_run=%s, play=%s",
                LOG_PREFIX,
                len(podcasts),
                player,
                dry_run,
                play,
            )

            included, skipped, errors, new_hwm = await hass.async_add_executor_job(
                _build_episode_list,
                podcasts,
                local_tz,
                fetch_timeout,
                hwm,
                seen_guids,
                int(max_lookback),
            )

            _LOGGER.info(
                "%s Summary: %d included, %d skipped, %d error(s).",
                LOG_PREFIX,
                len(included),
                len(skipped),
                len(errors),
            )

            today_str = dt.datetime.now(local_tz).strftime(DATE_FMT)

            async def _commit_build() -> None:
                """Record a successfully prepared/played build and advance HWM."""
                if dry_run:
                    return

                # Record only the episodes we're actually offering (merged into the
                # day's history). _async_save_history re-raises on a failed write,
                # so when there is something to record the HWM advance below is only
                # reached on a confirmed, durable history write (H3). When there is
                # nothing to record (no new episodes), the HWM may still advance for
                # episodes already present in recent history -- there is no write to
                # confirm because those episodes are already recorded.
                if included:
                    await _async_save_history(today_str, player, included)
                # Advance each feed's HWM to the newest in-window episode it saw --
                # including episodes de-duped away -- so nothing already accounted
                # for is ever re-offered, even if history later rotates out. Feeds
                # that errored are absent from new_hwm (HWM kept -> retried).
                # Re-read the LIVE hwm under the build lock and merge new_hwm into
                # it (rather than overwriting from the stale per-run snapshot) so a
                # concurrent run's advances are never clobbered (C1).
                if new_hwm:
                    live = dict(hass.data[DOMAIN].get("hwm", {}))
                    live.update(new_hwm)
                    hass.data[DOMAIN]["hwm"] = live
                    await _async_save_hwm()

            if not included:
                # Even with no queueable episodes, new_hwm may contain timestamps for
                # episodes already present in recent history. Preserve those HWM
                # updates so they are not reconsidered on every run.
                await _commit_build()

                # In play mode (the button / default service call), "nothing new"
                # usually means the scheduled 06:00 prepare already built today's
                # playlist and advanced the high-water marks. The user still wants
                # to HEAR today's playlist, so fall back to playing what was
                # prepared and recorded for today, rather than doing nothing.
                if play and not dry_run:
                    today_eps = await _load_history_episodes(today_str)
                    if today_eps:
                        _LOGGER.info(
                            "%s Nothing new to build; playing today's prepared "
                            "playlist from history (%d episode(s)).",
                            LOG_PREFIX,
                            len(today_eps),
                        )
                        await _async_queue_media(player, today_eps, dry_run)
                        return

                _LOGGER.info(
                    "%s Nothing new since last run; nothing recorded or played.",
                    LOG_PREFIX,
                )
                return

            _LOGGER.info(
                "%s Playlist (%d): %s",
                LOG_PREFIX,
                len(included),
                " -> ".join(ep["name"] for ep in included),
            )

            if not play:
                # The scheduled prepare path intentionally commits without
                # playback. A manual play path commits only after queueing succeeds.
                await _commit_build()
                _LOGGER.info(
                    "%s Prepared the playlist and recorded it to history; not "
                    "playing now (play=False). Use the service/button to play.",
                    LOG_PREFIX,
                )
                return

            # Commit ordering depends on the play backend (H1):
            #
            # - Music Assistant: mass.play_media is fire-and-queue -- a successful
            #   service return does NOT guarantee the episodes actually played, so
            #   "played" must not gate the HWM. History is the durable artifact
            #   play_history can replay, so we record + advance the HWM FIRST (on a
            #   confirmed history write) and then issue the play. If the play call
            #   later fails, the episodes are safe in history (replayable) and the
            #   HWM is correct -- no silent loss. A play failure still propagates
            #   to the caller/log but must not roll back the commit.
            #
            # - Native/Sonos fallback: _async_queue_media raises on a failed
            #   per-item play_media BEFORE any commit, so queue-then-commit keeps
            #   its existing retry-safety (a failed queue leaves the HWM unmoved).
            use_mass = hass.services.has_service(MASS_DOMAIN, MASS_PLAY_MEDIA)
            if use_mass:
                await _commit_build()
                await _async_queue_media(player, included, dry_run)
            else:
                await _async_queue_media(player, included, dry_run)
                await _commit_build()

    # --- Service: build_queue --------------------------------------------
    async def handle_build_queue(call: ServiceCall) -> None:
        # Plays by default; pass play: false to only prepare/record.
        await _run_build(
            player=call.data.get(ATTR_PLAYER),
            tz_name=call.data.get(ATTR_TZ),
            dry_run=call.data.get(ATTR_DRY_RUN, False),
            play=call.data.get(ATTR_PLAY, True),
        )

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
        cfg = _entry_config(hass)
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

    # --- Overview panel helpers ------------------------------------------
    def _next_prepare_iso() -> str | None:
        """ISO timestamp of the next scheduled daily prepare, or None if off."""
        cfg = _entry_config(hass)
        if not cfg.get(CONF_ENABLED, DEFAULT_ENABLED):
            return None
        local_tz = _resolve_local_tz(hass, cfg.get(CONF_TIMEZONE))
        at = str(cfg.get(CONF_AT, DEFAULT_AT))
        parts = at.split(":")
        while len(parts) < 3:
            parts.append("0")
        try:
            hour, minute, second = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            hour, minute, second = 6, 0, 0
        now = dt.datetime.now(local_tz)
        nxt = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
        if nxt <= now:
            nxt = nxt + dt.timedelta(days=1)
        return nxt.isoformat()

    async def _sonos_queue_ids(player: str) -> list[str] | None:
        """Return the live Sonos queue's media_content_ids, or None.

        None means the queue could not be read as a Sonos queue -- either the
        Sonos integration isn't present, the call failed, or the configured
        player is not a native Sonos entity (e.g. a Music Assistant entity for
        the same speaker). An empty list means a Sonos entity with an empty
        queue. The caller uses this distinction to avoid showing a stale
        history fallback as if it were the live queue.
        """
        if not hass.services.has_service("sonos", "get_queue"):
            return None
        try:
            resp = await hass.services.async_call(
                "sonos",
                "get_queue",
                {"entity_id": player},
                blocking=True,
                return_response=True,
            )
        except Exception as err:  # noqa: BLE001
            # A wrong (non-Sonos) entity raises here -- treat as "not a Sonos
            # queue" rather than an error.
            _LOGGER.debug(
                "%s sonos.get_queue failed on %s (%s).", LOG_PREFIX, player, err
            )
            return None
        # sonos.get_queue returns a dict keyed by entity_id. If our player key
        # is absent, this isn't a Sonos entity we can drive.
        if not isinstance(resp, dict) or player not in resp:
            return None
        items = resp.get(player) or []
        return [item.get("media_content_id", "") for item in items]

    async def handle_get_queue(call: ServiceCall) -> dict[str, Any]:
        """Return now-playing + the live queue for the Overview panel.

        The queue reflects the live Sonos queue only. Items are mapped back to
        podcast names/titles using recent recorded history, matched by audio
        URL. queue_source reports whether the queue is live, empty, or could
        not be read (e.g. the player isn't a controllable Sonos entity).
        """
        cfg = _entry_config(hass)
        player = call.data.get(ATTR_PLAYER) or cfg.get(CONF_PLAYER)

        # Build an audio_url -> {name, title} map from recent history so queue
        # items (bare MP3 URLs) can show which podcast they are.
        base_dir = _history_base_dir()
        dates = await hass.async_add_executor_job(_list_history_dates, base_dir)
        url_map: dict[str, dict[str, str]] = {}
        for date_str in dates[-7:]:  # recent week is plenty to label a queue
            eps = await _load_history_episodes(date_str)
            for ep in eps:
                url = ep.get("audio_url")
                if url:
                    url_map[url] = {
                        "name": ep.get("name", ""),
                        "title": ep.get("title", ""),
                    }

        state = hass.states.get(player) if player else None
        now_playing: dict[str, Any] = {}
        queue: list[dict[str, Any]] = []

        if state is not None:
            attrs = state.attributes
            current_id = attrs.get("media_content_id", "")
            meta = url_map.get(current_id, {})
            updated = attrs.get("media_position_updated_at")
            if hasattr(updated, "isoformat"):
                updated = updated.isoformat()
            elif updated is not None:
                updated = str(updated)
            now_playing = {
                "state": state.state,
                "podcast": meta.get("name", ""),
                "title": meta.get("title") or attrs.get("media_title", ""),
                "media_content_id": current_id,
                "position": attrs.get("media_position"),
                "duration": attrs.get("media_duration"),
                "position_updated_at": updated,
                "queue_position": attrs.get("queue_position"),
                "queue_size": attrs.get("queue_size"),
            }

            # Read ONLY the live Sonos queue. Do not substitute recorded
            # history: showing yesterday's playlist as if it were the live
            # queue makes Skip/Remove operate on positions that don't exist,
            # which is exactly the "nothing happens" confusion we want to
            # avoid. ids is None when the player isn't a controllable Sonos
            # entity (e.g. a Music Assistant entity for the same speaker).
            ids = await _sonos_queue_ids(player)
            if ids is None:
                queue_source = "unavailable"
            elif not ids:
                queue_source = "empty"
            else:
                queue_source = "live"
                for idx, url in enumerate(ids):
                    meta = url_map.get(url, {})
                    queue.append(
                        {
                            "position": idx,  # 0-based (matches play_queue)
                            "podcast": meta.get("name", ""),
                            "title": meta.get("title", ""),
                            "media_content_id": url,
                            "current": bool(url) and url == current_id,
                        }
                    )
        else:
            queue_source = "no_player"

        # Skip/Remove only make sense against a live Sonos queue.
        is_sonos = queue_source in ("live", "empty")

        return {
            "player": player,
            "now_playing": now_playing,
            "queue": queue,
            "queue_source": queue_source,
            "is_sonos": is_sonos,
            "next_prepare": _next_prepare_iso(),
            "enabled": bool(cfg.get(CONF_ENABLED, DEFAULT_ENABLED)),
            "at": str(cfg.get(CONF_AT, DEFAULT_AT)),
            "intraday_enabled": bool(
                cfg.get(CONF_INTRADAY_ENABLED, DEFAULT_INTRADAY_ENABLED)
            ),
            "intraday_interval_hours": int(
                cfg.get(
                    CONF_INTRADAY_INTERVAL_HOURS,
                    DEFAULT_INTRADAY_INTERVAL_HOURS,
                )
            ),
            "intraday_start_hour": int(
                cfg.get(CONF_INTRADAY_START_HOUR, DEFAULT_INTRADAY_START_HOUR)
            ),
            "intraday_end_hour": int(
                cfg.get(CONF_INTRADAY_END_HOUR, DEFAULT_INTRADAY_END_HOUR)
            ),
            "podcast_count": len(cfg.get(CONF_PODCASTS) or []),
            "can_skip": is_sonos
            and hass.services.has_service("sonos", "play_queue"),
            "can_remove": is_sonos
            and hass.services.has_service(
                "sonos", "remove_from_queue"
            ),
        }

    async def handle_skip_to(call: ServiceCall) -> None:
        """Jump playback to a 0-based queue position (Sonos: play_queue)."""
        cfg = _entry_config(hass)
        player = call.data.get(ATTR_PLAYER) or cfg.get(CONF_PLAYER)
        position = int(call.data.get(ATTR_POSITION, 0))
        if position < 0:
            position = 0

        if not hass.services.has_service("sonos", "play_queue"):
            raise HomeAssistantError(
                f"{LOG_PREFIX} Skipping to a queue position requires the Sonos "
                f"integration (sonos.play_queue); player '{player}' does not "
                "support it."
            )
        await hass.services.async_call(
            "sonos",
            "play_queue",
            {"entity_id": player, "queue_position": position},
            blocking=True,
        )
        _LOGGER.info(
            "%s skip_to: jumped %s to queue position %d.",
            LOG_PREFIX,
            player,
            position,
        )

    async def handle_remove_from_queue(call: ServiceCall) -> None:
        """Remove one item at a 0-based queue position (Sonos)."""
        cfg = _entry_config(hass)
        player = call.data.get(ATTR_PLAYER) or cfg.get(CONF_PLAYER)
        position = int(call.data.get(ATTR_POSITION, 0))
        if position < 0:
            position = 0

        if not hass.services.has_service("sonos", "remove_from_queue"):
            raise HomeAssistantError(
                f"{LOG_PREFIX} Removing a queue item requires the Sonos "
                f"integration (sonos.remove_from_queue); player '{player}' "
                "does not support it."
            )
        await hass.services.async_call(
            "sonos",
            "remove_from_queue",
            {"entity_id": player, "queue_position": position},
            blocking=True,
        )
        _LOGGER.info(
            "%s remove_from_queue: removed position %d from %s.",
            LOG_PREFIX,
            position,
            player,
        )

    # Register services once (they read the live entry config each call).
    if not hass.services.has_service(DOMAIN, SERVICE_BUILD_QUEUE):
        hass.services.async_register(
            DOMAIN, SERVICE_BUILD_QUEUE, handle_build_queue, schema=BUILD_QUEUE_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, SERVICE_PLAY_HISTORY):
        hass.services.async_register(
            DOMAIN,
            SERVICE_PLAY_HISTORY,
            handle_play_history,
            schema=PLAY_HISTORY_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_GET_QUEUE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_QUEUE,
            handle_get_queue,
            schema=GET_QUEUE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SKIP_TO):
        hass.services.async_register(
            DOMAIN, SERVICE_SKIP_TO, handle_skip_to, schema=SKIP_TO_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, SERVICE_REMOVE_FROM_QUEUE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REMOVE_FROM_QUEUE,
            handle_remove_from_queue,
            schema=REMOVE_FROM_QUEUE_SCHEMA,
        )

    # --- Daily scheduler (internal; no automations.yaml needed) ----------
    async def _scheduled_run(now):
        # The daily schedule only PREPARES today's playlist (records it to
        # history). It never auto-plays -- you play it on demand via the
        # service/button. Pass play=False.
        _LOGGER.info(
            "%s Scheduled daily run at %s: preparing today's playlist.",
            LOG_PREFIX,
            now,
        )
        await _run_build(play=False)

    async def _intraday_run(now):
        # Extra prepare-only run during the configured daytime window, so
        # today's recorded playlist picks up episodes that publish after the
        # daily run. Like the daily run, it NEVER plays. The interval timer
        # fires around the clock; we gate on the window here.
        cfg = _entry_config(hass)
        start = int(cfg.get(CONF_INTRADAY_START_HOUR, DEFAULT_INTRADAY_START_HOUR))
        end = int(cfg.get(CONF_INTRADAY_END_HOUR, DEFAULT_INTRADAY_END_HOUR))
        local_tz = _resolve_local_tz(hass, cfg.get(CONF_TIMEZONE))
        hour_now = dt.datetime.now(local_tz).hour
        # Window is [start, end): inclusive of start hour, exclusive of end.
        in_window = (
            start <= hour_now < end
            if start <= end
            else (hour_now >= start or hour_now < end)  # overnight window
        )
        if not in_window:
            return
        _LOGGER.info(
            "%s Intraday refresh at %s (window %02d:00-%02d:00): preparing "
            "today's playlist.",
            LOG_PREFIX,
            now,
            start,
            end,
        )
        await _run_build(play=False)

    def _arm_schedule() -> None:
        """(Re)arm the daily time trigger (and intraday interval) from options."""
        # Cancel any previous timers.
        cancel = hass.data[DOMAIN].pop("cancel_timer", None)
        if cancel:
            cancel()
        cancel_intraday = hass.data[DOMAIN].pop("cancel_intraday", None)
        if cancel_intraday:
            cancel_intraday()

        cfg = _entry_config(hass)
        if not cfg.get(CONF_ENABLED, DEFAULT_ENABLED):
            _LOGGER.info(
                "%s Daily auto-run is disabled; scheduler not armed.", LOG_PREFIX
            )
            return

        at = str(cfg.get(CONF_AT, DEFAULT_AT))
        try:
            parts = [int(p) for p in at.split(":")]
            while len(parts) < 3:
                parts.append(0)
            hour, minute, second = parts[0], parts[1], parts[2]
        except (ValueError, IndexError):
            _LOGGER.error(
                "%s Invalid trigger time %r; using %s.", LOG_PREFIX, at, DEFAULT_AT
            )
            hour, minute, second = 6, 0, 0

        hass.data[DOMAIN]["cancel_timer"] = async_track_time_change(
            hass, _scheduled_run, hour=hour, minute=minute, second=second
        )
        _LOGGER.info(
            "%s Daily run scheduled for %02d:%02d:%02d local time.",
            LOG_PREFIX,
            hour,
            minute,
            second,
        )

        # Optional intraday prepare-only refresh within a daytime window.
        if cfg.get(CONF_INTRADAY_ENABLED, DEFAULT_INTRADAY_ENABLED):
            interval = int(
                cfg.get(
                    CONF_INTRADAY_INTERVAL_HOURS,
                    DEFAULT_INTRADAY_INTERVAL_HOURS,
                )
            )
            if interval < 1:
                interval = 1
            start = int(
                cfg.get(CONF_INTRADAY_START_HOUR, DEFAULT_INTRADAY_START_HOUR)
            )
            end = int(
                cfg.get(CONF_INTRADAY_END_HOUR, DEFAULT_INTRADAY_END_HOUR)
            )
            hass.data[DOMAIN]["cancel_intraday"] = async_track_time_interval(
                hass, _intraday_run, dt.timedelta(hours=interval)
            )
            _LOGGER.info(
                "%s Intraday refresh every %dh within %02d:00-%02d:00 local "
                "time (prepare only).",
                LOG_PREFIX,
                interval,
                start,
                end,
            )

    _arm_schedule()

    # Note: the options flow subclasses OptionsFlowWithReload, so Home Assistant
    # reloads this entry automatically when options change -- which re-runs
    # async_setup_entry and re-arms the schedule. We deliberately do NOT add an
    # update listener here: combining a listener with a reloading options flow
    # is deprecated (HA 2026.6) and would double-reload.

    _LOGGER.info(
        "%s Ready. Manage it in Settings -> Devices & services -> Daily "
        "Podcast Queue -> Configure. Services: %s.%s, %s.%s",
        LOG_PREFIX,
        DOMAIN,
        SERVICE_BUILD_QUEUE,
        DOMAIN,
        SERVICE_PLAY_HISTORY,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the config entry: cancel the timers and drop stored state."""
    store = hass.data.get(DOMAIN, {})
    cancel = store.pop("cancel_timer", None)
    if cancel:
        cancel()
    cancel_intraday = store.pop("cancel_intraday", None)
    if cancel_intraday:
        cancel_intraday()
    store.pop("entry", None)
    async_remove_panel(hass)
    return True
