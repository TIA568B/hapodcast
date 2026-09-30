"""
Daily Podcast Queue for Home Assistant + Music Assistant + Sonos.

This pyscript *app* builds an ordered "playlist" of podcast episodes that were
published *today* (in Home Assistant's local timezone) and queues them onto a
Music Assistant player (e.g. a Sonos speaker) back-to-back, preserving the
listening order defined in configuration and silently skipping any podcast that
has nothing new.

The podcast list and runtime options are genuine *data*: they live in the
pyscript app configuration under

    pyscript:
      apps:
        daily_podcasts:
          player: media_player.sonos_kitchen
          podcasts:
            - name: ...
              feed_url: ...

and are injected into this module as the global `pyscript.app_config`. A single
piece of logic iterates over that list, so adding, removing, or reordering a
podcast means editing one YAML list -- no code changes.

Every daily run also *records* the playlist it built to a per-day JSON file
(``<config>/pyscript/podcast_history/YYYY-MM-DD.json``). Because feeds only keep
a rolling window of recent episodes, this history lets you replay past days --
e.g. play catch-up after a holiday -- even once those episodes have aged out of
the feeds. The daily automation runs whether you're home or away, so history
accumulates on its own with nothing for you to toggle.

Exposed services (callable from HA automations, scripts, or Developer Tools):

    pyscript.build_daily_podcast_queue
        Runs the whole pipeline: refresh/fetch each feed -> parse latest episode
        -> filter to "published today" (local tz) -> record today's playlist to
        history -> queue on the configured player (unless record_only).
        Optional call-time overrides: player, tz, dry_run, record_only.

    pyscript.play_podcast_history
        Replay one or more previously recorded days, in chronological order,
        preserving each day's internal podcast order. Selectors (first match
        wins): date -> start/end -> since -> days -> (default) all recorded days
        with episodes. Optional overrides: player, dry_run.

The production daily trigger lives in automations.yaml so the schedule is
discoverable in HA's normal config (see README).
"""

import datetime as dt
import glob
import json
import os

import feedparser  # provided via pyscript `requirements` (see README)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

DEFAULTS = {
    # Ordered list of podcasts in listening order. Each entry: {name, feed_url}.
    "podcasts": [],
    # Target Music Assistant / Sonos player entity id.
    "player": "media_player.sonos_kitchen",
    # IANA timezone name used to decide what "today" means. If empty/None we
    # fall back to Home Assistant's configured local timezone.
    "timezone": None,
    # Per-feed HTTP fetch timeout (seconds).
    "fetch_timeout": 20,
    # If True, don't actually queue anything -- just log what *would* happen.
    "dry_run": False,
    # If True, the daily run records the playlist to history but does NOT play
    # it (useful if you only ever listen via catch-up / play_podcast_history).
    "record_only": False,
    # Directory (relative to the HA config dir) where daily playlists are saved.
    "history_dir": "pyscript/podcast_history",
}


def _get_app_config():
    """Return this app's config dict (from `pyscript.app_config`), or {}.

    `pyscript.app_config` is injected into the app's main module global scope by
    pyscript. It holds exactly the settings below `apps: daily_podcasts:` in the
    pyscript yaml config.
    """
    try:
        raw = pyscript.app_config  # noqa: F821 (pyscript global)
    except (NameError, AttributeError):
        return {}

    # Support both a mapping (recommended) and a single-element list form.
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else {}
    try:
        return dict(raw or {})
    except (TypeError, ValueError):
        return {}


def _resolve_config(overrides=None):
    """Merge DEFAULTS <- app config <- call-time overrides."""
    cfg = dict(DEFAULTS)
    cfg.update(_get_app_config())
    if overrides:
        cfg.update({k: v for k, v in overrides.items() if v is not None})
    return cfg


# ---------------------------------------------------------------------------
# Timezone handling
# ---------------------------------------------------------------------------


def _resolve_local_tz(tz_name):
    """Return a tzinfo for the configured tz, HA's local tz, or system local."""
    from zoneinfo import ZoneInfo

    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception as err:  # noqa: BLE001 - log and fall through
            log.warning(
                f"[daily_podcasts] Invalid timezone '{tz_name}' ({err}); "
                "falling back to Home Assistant local timezone."
            )

    # Prefer Home Assistant's configured timezone (requires hass_is_global: true).
    try:
        ha_tz = hass.config.time_zone  # noqa: F821 (pyscript global)
        if ha_tz:
            return ZoneInfo(ha_tz)
    except Exception:  # noqa: BLE001
        pass

    # Last resort: system local timezone.
    return dt.datetime.now().astimezone().tzinfo


# ---------------------------------------------------------------------------
# Feed fetching + parsing
# ---------------------------------------------------------------------------


def _fetch_feed_bytes(feed_url, timeout):
    """Fetch raw feed bytes over HTTP, bypassing any cached copy.

    We deliberately fetch the feed ourselves (rather than trusting a periodic
    poll) so "published today" is evaluated against fresh data. Cache-busting
    headers are sent to discourage intermediate caches.
    """
    import urllib.request

    req = urllib.request.Request(
        feed_url,
        headers={
            "User-Agent": "HomeAssistant-DailyPodcasts/1.0 (+pyscript)",
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _entry_published_utc(entry):
    """Return an aware UTC datetime for a feedparser entry, or None."""
    tp = entry.get("published_parsed") or entry.get("updated_parsed")
    if tp is None:
        return None
    return dt.datetime(*tp[:6], tzinfo=dt.timezone.utc)


def _parse_latest_episode(raw_bytes):
    """Parse the most recently published episode from raw feed bytes.

    Returns {title, audio_url, published_dt (aware UTC)} or raises ValueError if
    no usable episode/enclosure can be found.
    """
    parsed = feedparser.parse(raw_bytes)

    if getattr(parsed, "bozo", 0) and not parsed.entries:
        raise ValueError(
            f"malformed feed ({getattr(parsed, 'bozo_exception', 'unknown error')})"
        )

    if not parsed.entries:
        raise ValueError("feed contains no entries")

    # Pick the newest entry by publish date rather than trusting feed ordering.
    _EPOCH = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    latest = max(parsed.entries, key=lambda e: _entry_published_utc(e) or _EPOCH)

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


def _extract_audio_url(entry):
    """Pull the best audio URL out of a feedparser entry."""
    # 1. Standard RSS <enclosure>.
    for enc in entry.get("enclosures", []) or []:
        etype = (enc.get("type") or "").lower()
        href = enc.get("href") or enc.get("url")
        if href and (etype.startswith("audio") or not etype):
            return href

    # 2. media:content links (some feeds use these instead).
    for media in entry.get("media_content", []) or []:
        etype = (media.get("type") or "").lower()
        url = media.get("url")
        if url and (etype.startswith("audio") or not etype):
            return url

    # 3. Any link marked as an enclosure.
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and link.get("href"):
            return link["href"]

    return None


# ---------------------------------------------------------------------------
# History storage
# ---------------------------------------------------------------------------
#
# Each daily run writes the ordered playlist it built to
#   <config>/pyscript/podcast_history/YYYY-MM-DD.json
# so past days can be replayed later (catch-up), even after episodes age out of
# the feeds. File I/O is blocking, so it's run via task.executor off the loop.

_DATE_FMT = "%Y-%m-%d"


def _resolve_history_dir(history_dir):
    """Return an absolute path to the history directory under the HA config."""
    # If already absolute, honor it; otherwise resolve under the HA config dir.
    if os.path.isabs(history_dir):
        return history_dir
    try:
        return hass.config.path(history_dir)  # noqa: F821 (pyscript global)
    except Exception:  # noqa: BLE001 - fall back to a relative path
        return history_dir


def _history_path(history_dir, date_str):
    return os.path.join(_resolve_history_dir(history_dir), f"{date_str}.json")


def _write_history_file(path, payload):
    """Blocking write of a single day's playlist as JSON (run via executor)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # atomic replace so a re-run never leaves a partial file


def _read_history_file(path):
    """Blocking read of a single day's playlist JSON (run via executor)."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _list_history_dates(history_dir):
    """Blocking listing of recorded dates (YYYY-MM-DD) sorted ascending."""
    base = _resolve_history_dir(history_dir)
    dates = []
    for p in glob.glob(os.path.join(base, "*.json")):
        stem = os.path.splitext(os.path.basename(p))[0]
        try:
            dt.datetime.strptime(stem, _DATE_FMT)
        except ValueError:
            continue  # ignore files that aren't a dated playlist
        dates.append(stem)
    return sorted(dates)


def _save_daily_history(history_dir, date_str, player, episodes):
    """Persist today's built playlist to history (via executor)."""
    payload = {
        "date": date_str,
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "player": player,
        "episodes": episodes,  # ordered list of {name,title,audio_url,published_local}
    }
    path = _history_path(history_dir, date_str)
    try:
        task.executor(_write_history_file, path, payload)  # noqa: F821
        log.info(
            f"[daily_podcasts] Recorded {len(episodes)} episode(s) for "
            f"{date_str} -> {path}"
        )
    except Exception as err:  # noqa: BLE001 - don't fail the run over history
        log.error(
            f"[daily_podcasts] Failed to write history for {date_str} "
            f"({err}); playback (if any) still proceeds."
        )


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


def _build_episode_list(podcasts, local_tz, fetch_timeout):
    """Iterate the ordered podcast list, returning today's episodes in order.

    Returns (included, skipped, errors). `included` preserves the order of
    `podcasts`, with non-today and failed podcasts omitted -- no reordering of
    what remains.
    """
    today_local = dt.datetime.now(local_tz).date()

    included = []
    skipped = []
    errors = []

    for entry in podcasts:
        name = (entry or {}).get("name", "(unnamed)")
        feed_url = (entry or {}).get("feed_url")

        if not feed_url:
            errors.append({"name": name, "error": "missing feed_url"})
            log.error(f"[daily_podcasts] '{name}': missing feed_url; skipping.")
            continue

        try:
            raw = _fetch_feed_bytes(feed_url, fetch_timeout)
            latest = _parse_latest_episode(raw)
        except Exception as err:  # noqa: BLE001 - isolate per-feed failures
            errors.append({"name": name, "error": str(err)})
            log.error(
                f"[daily_podcasts] '{name}': feed fetch/parse failed ({err}); "
                "continuing with the rest of the list."
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
            log.info(
                f"[daily_podcasts] INCLUDE '{name}': \"{latest['title']}\" "
                f"(published {published_local_dt.isoformat()})"
            )
        else:
            skipped.append(
                {
                    "name": name,
                    "reason": f"latest episode from {published_local_dt.date()}, "
                    f"not today ({today_local})",
                }
            )
            log.info(
                f"[daily_podcasts] SKIP '{name}': latest episode from "
                f"{published_local_dt.date()}, not today ({today_local})."
            )

    return included, skipped, errors


def _queue_on_player(player, episodes, dry_run):
    """Queue the ordered episode list on the Music Assistant player.

    mass.play_media accepts a *list* of media ids in one call, so we send the
    whole ordered list at once with enqueue=replace. That is:
      - order-preserving: the list order is the playback order;
      - atomic: one service call, no interleaving with other playback;
      - idempotent: replace wipes any previous queue, so re-running the
        automation the same day rebuilds the identical queue rather than
        appending duplicates.
    media_type is intentionally omitted so Music Assistant auto-detects the
    type from each enclosure URL.
    """
    media_ids = [ep["audio_url"] for ep in episodes]

    for index, ep in enumerate(episodes, start=1):
        log.info(
            f"[daily_podcasts] QUEUE #{index} -> {player}: "
            f"'{ep['name']}' \"{ep['title']}\" [{ep['audio_url']}]"
        )

    if dry_run:
        log.info(
            f"[daily_podcasts] dry_run: would call mass.play_media on {player} "
            f"with {len(media_ids)} item(s), enqueue=replace."
        )
        return

    # enqueue=replace clears the existing queue and starts playback of the
    # supplied ordered list; valid enqueue values are
    # play/replace/next/replace_next/add.
    service.call(  # noqa: F821 (pyscript global)
        "mass",
        "play_media",
        entity_id=player,
        media_id=media_ids,
        enqueue="replace",
        blocking=True,
    )


# ---------------------------------------------------------------------------
# Public service
# ---------------------------------------------------------------------------


@service
def build_daily_podcast_queue(player=None, tz=None, dry_run=None, record_only=None):
    """Build, record, and queue today's podcast playlist onto the player.

    Call from an automation, script, or Developer Tools > Actions:

        action: pyscript.build_daily_podcast_queue

    Optional data overrides:
        player:      media_player entity id (defaults to app config `player`)
        tz:          IANA timezone name (defaults to app config / HA local tz)
        dry_run:     true to log the plan without touching the player or history
        record_only: true to record today's playlist to history but not play it
    """
    cfg = _resolve_config(
        {
            "player": player,
            "timezone": tz,
            "dry_run": dry_run,
            "record_only": record_only,
        }
    )

    podcasts = cfg["podcasts"] or []
    target_player = cfg["player"]
    fetch_timeout = cfg["fetch_timeout"]
    is_dry_run = bool(cfg["dry_run"])
    is_record_only = bool(cfg["record_only"])
    history_dir = cfg["history_dir"]
    local_tz = _resolve_local_tz(cfg["timezone"])

    if not podcasts:
        log.warning(
            "[daily_podcasts] No podcasts configured. Add entries under the "
            "`podcasts:` list in the pyscript app config. Nothing to do."
        )
        return

    log.info(
        f"[daily_podcasts] Starting run: {len(podcasts)} podcast(s), "
        f"player={target_player}, tz={local_tz}, dry_run={is_dry_run}, "
        f"record_only={is_record_only}"
    )

    included, skipped, errors = _build_episode_list(
        podcasts, local_tz, fetch_timeout
    )

    log.info(
        f"[daily_podcasts] Summary: {len(included)} included, "
        f"{len(skipped)} skipped (nothing new), {len(errors)} error(s)."
    )
    if errors:
        for e in errors:
            log.warning(f"[daily_podcasts]   error: {e['name']}: {e['error']}")

    today_str = dt.datetime.now(local_tz).strftime(_DATE_FMT)

    # Record today's playlist to history (unless this is a dry run). We record
    # even when there's nothing today would be unusual -- only write a file when
    # there is at least one episode, so history lists real "listening days".
    if included and not is_dry_run:
        _save_daily_history(history_dir, today_str, target_player, included)

    # No episodes today -> leave existing playback alone.
    if not included:
        log.info(
            "[daily_podcasts] No podcasts published today. Leaving the player "
            "untouched; nothing was queued."
        )
        return

    order_preview = " -> ".join(ep["name"] for ep in included)
    log.info(f"[daily_podcasts] Final queue order: {order_preview}")

    if is_record_only:
        log.info(
            "[daily_podcasts] record_only: playlist saved to history; not "
            "playing now. Use pyscript.play_podcast_history to play it later."
        )
        return

    _queue_on_player(target_player, included, is_dry_run)


# ---------------------------------------------------------------------------
# History replay (catch-up)
# ---------------------------------------------------------------------------


def _parse_date_arg(value, label):
    """Parse a YYYY-MM-DD string into a date, raising ValueError with context."""
    try:
        return dt.datetime.strptime(str(value), _DATE_FMT).date()
    except (ValueError, TypeError):
        raise ValueError(f"{label} must be a YYYY-MM-DD date, got {value!r}")


def _select_history_dates(history_dir, local_tz, date, start, end, since, days):
    """Resolve which recorded dates to replay, in chronological order.

    Selector precedence (first non-empty wins):
      date            -> just that single day
      start and/or end-> inclusive range (open-ended if one side omitted)
      since           -> from `since` through today
      days            -> the last N recorded days
      (nothing)       -> every recorded day
    Only dates that actually have a recorded file are returned.
    """
    available = _list_history_dates(history_dir)
    if not available:
        return []

    available_dates = [_parse_date_arg(d, "history filename") for d in available]

    if date:
        want = _parse_date_arg(date, "date")
        return [d.strftime(_DATE_FMT) for d in available_dates if d == want]

    if start or end:
        lo = _parse_date_arg(start, "start") if start else dt.date.min
        hi = _parse_date_arg(end, "end") if end else dt.date.max
        return [
            d.strftime(_DATE_FMT) for d in available_dates if lo <= d <= hi
        ]

    if since:
        lo = _parse_date_arg(since, "since")
        today = dt.datetime.now(local_tz).date()
        return [
            d.strftime(_DATE_FMT) for d in available_dates if lo <= d <= today
        ]

    if days:
        try:
            n = int(days)
        except (ValueError, TypeError):
            raise ValueError(f"days must be an integer, got {days!r}")
        if n <= 0:
            return []
        return [d.strftime(_DATE_FMT) for d in available_dates][-n:]

    # Default: everything recorded.
    return [d.strftime(_DATE_FMT) for d in available_dates]


def _load_history_episodes(history_dir, date_str):
    """Load one day's ordered episode list from history, or [] on failure."""
    path = _history_path(history_dir, date_str)
    try:
        payload = task.executor(_read_history_file, path)  # noqa: F821
    except Exception as err:  # noqa: BLE001 - isolate per-day read failures
        log.error(
            f"[daily_podcasts] Could not read history for {date_str} "
            f"({err}); skipping that day."
        )
        return []
    episodes = (payload or {}).get("episodes", []) or []
    return episodes


@service
def play_podcast_history(
    player=None,
    date=None,
    start=None,
    end=None,
    since=None,
    days=None,
    dry_run=None,
):
    """Replay previously recorded daily playlists (catch-up after time away).

    Call from an automation, script, or Developer Tools > Actions:

        action: pyscript.play_podcast_history
        data:
          since: "2026-09-20"      # play everything recorded since this date

    Selectors (first match wins):
        date:  "YYYY-MM-DD"        -> replay just that one day
        start/end: "YYYY-MM-DD"    -> inclusive range (either side optional)
        since: "YYYY-MM-DD"        -> from that date through today
        days:  N                   -> the last N recorded days
        (none)                     -> every recorded day

    Days play in chronological order; within each day the original podcast
    order is preserved. The combined, ordered list is sent to the player in a
    single mass.play_media call with enqueue=replace (idempotent -- re-running
    rebuilds the same queue rather than duplicating it).

    Other overrides:
        player:  media_player entity id (defaults to app config `player`)
        dry_run: true to log the plan without touching the player
    """
    cfg = _resolve_config({"player": player, "dry_run": dry_run})
    target_player = cfg["player"]
    is_dry_run = bool(cfg["dry_run"])
    history_dir = cfg["history_dir"]
    local_tz = _resolve_local_tz(cfg["timezone"])

    try:
        selected_dates = _select_history_dates(
            history_dir, local_tz, date, start, end, since, days
        )
    except ValueError as err:
        log.error(f"[daily_podcasts] play_podcast_history: {err}")
        return

    if not selected_dates:
        log.info(
            "[daily_podcasts] play_podcast_history: no recorded playlists match "
            "the request; nothing to play."
        )
        return

    log.info(
        f"[daily_podcasts] Catch-up: replaying {len(selected_dates)} day(s): "
        f"{', '.join(selected_dates)}"
    )

    # Concatenate each day's episodes in chronological order, preserving the
    # per-day podcast order.
    combined = []
    for date_str in selected_dates:
        day_eps = _load_history_episodes(history_dir, date_str)
        if not day_eps:
            log.info(f"[daily_podcasts]   {date_str}: no episodes recorded; skipping.")
            continue
        names = " -> ".join(ep.get("name", "?") for ep in day_eps)
        log.info(f"[daily_podcasts]   {date_str}: {names}")
        combined.extend(day_eps)

    if not combined:
        log.info(
            "[daily_podcasts] play_podcast_history: selected days had no "
            "episodes; nothing queued."
        )
        return

    log.info(
        f"[daily_podcasts] Catch-up queue: {len(combined)} episode(s) across "
        f"{len(selected_dates)} day(s)."
    )
    _queue_on_player(target_player, combined, is_dry_run)
