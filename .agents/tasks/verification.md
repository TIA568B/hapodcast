# HWM Robustness Fix — Verification Note

Branch: `fix/hwm-robustness`
Primary file changed: `custom_components/daily_podcasts/__init__.py`
Scope implemented: audit findings **C1, H1, H3** (PASS 1) and **H2, M4** (PASS 2).
Explicitly NOT touched: M1, M2, M3, M5, L1, L2, L3; no test harness added; no
service schemas / `services.yaml` / `strings.json` / `translations/en.json`
changed (no public contract changed).

## What was run

1. **Byte-compile** (the honest verification available here — the repo has no
   test framework and `homeassistant`/`feedparser` are not importable in this
   environment, so the module cannot be imported directly):

   ```
   python3 -m py_compile custom_components/daily_podcasts/__init__.py
   ```

   Result: **exit 0, no output** — the file is syntactically valid. Run after
   every edit and again after the final indentation normalization of the
   `async with` build-lock block.

2. **Linters (`ruff`, `flake8`)**: NOT available in this environment.
   - `command -v ruff` / `command -v flake8` → not found.
   - `python3 -m ruff` / `python3 -m flake8` → `No module named ...`.
   - `pip install ruff` → failed (no package index reachable here).
   So no lint run was possible. Compensated with a careful manual diff read-back
   (below). Existing `# noqa: BLE001` conventions were preserved on new broad
   excepts.

## Read-back confirmation per finding

- **C1 (lock + merge, not overwrite):** `async_setup_entry` creates
  `hass.data[DOMAIN].setdefault("build_lock", asyncio.Lock())` (reused across
  reloads). `_run_build` wraps the whole read-modify-write span —
  `hwm = dict(...)` snapshot, the `_build_episode_list` executor call, and all
  three `_commit_build()` call sites (no-included branch, prepare `not play`
  branch, play branch) — inside `async with hass.data[DOMAIN]["build_lock"]:`.
  `_commit_build` no longer overwrites from the stale snapshot: it re-reads
  `live = dict(hass.data[DOMAIN].get("hwm", {}))`, `live.update(new_hwm)`,
  assigns back, then saves — so a concurrent run's advances are merged, never
  clobbered. `import asyncio` is now module-level; the redundant local import
  inside `_async_queue_media` was removed.

- **H3 (history is source of truth):** `_async_save_history`'s `except` now ends
  with `raise`, so a failed history write propagates. In `_commit_build`, when
  `included` is truthy the HWM-advance block is only reached if
  `_async_save_history` returned normally (a raise short-circuits before the
  advance). When `included` is empty, `_async_save_history` is not called and
  the HWM still advances for already-recorded episodes (existing, correct
  behavior preserved).

- **H1 (advance on confirmed record, not unverified play):** the play branch of
  `_run_build` now branches on
  `use_mass = hass.services.has_service(MASS_DOMAIN, MASS_PLAY_MEDIA)`:
  - Music Assistant: `await _commit_build()` (record + advance HWM on a
    confirmed history write) FIRST, then `await _async_queue_media(...)`. A
    later play failure cannot roll back the commit; episodes stay replayable
    from history.
  - Native/Sonos fallback: unchanged order — `await _async_queue_media(...)`
    (which raises on a failed per-item `play_media` before any commit) THEN
    `await _commit_build()`, preserving existing retry-safety.
  Dry-run and `not play` (prepare) paths are unchanged. `_async_queue_media`
  itself is unchanged.

- **H2 (identity on feed_url):** `_build_episode_list`'s `included.append({...})`
  now records `CONF_FEED_URL: feed_url` in every history entry (additive;
  backward compatible). New module-level helper `_history_latest_by_feed_url`
  returns newest `published_local` per `feed_url`, skipping entries lacking
  `feed_url` (old data). The one-time backfill now prefers
  `latest_by_url.get(p[CONF_FEED_URL])` and falls back to
  `latest_by_name.get(p.get(CONF_NAME))` for pre-upgrade history with no
  `feed_url` — graceful on old data, never crashes. `_history_latest_by_name`
  is kept for that fallback.

- **M4 (reconcile HWM on set_podcasts):** `handle_set_podcasts` (in
  `async_setup`) now, AFTER `async_update_entry`, prunes the HWM dict to the
  configured `feed_url` set under the same `build_lock`. It re-reads the live
  HWM, keeps only configured URLs, and if anything was dropped assigns the
  pruned dict and persists it via the stored `hwm_store`. It no-ops safely when
  `build_lock`/`hwm_store`/`hwm` are not yet present (options updated before
  `async_setup_entry` loaded), and the save is best-effort (`# noqa: BLE001`,
  logs on failure, never raises out of the service handler). Removing then
  re-adding a feed is therefore a clean first-run.

## Ordering / regression check

`_commit_build` ordering is preserved (write history → advance HWM) with the
advance now gated on a confirmed write. The daily scheduler (`_scheduled_run`,
`play=False`), intraday timer (`_intraday_run`, `play=False`), and manual
`build_queue` (`handle_build_queue`) all still funnel through `_run_build` and
are now serialized by the single lock. `_load_history_episodes` is defined after
`_run_build` as before; it is only referenced at call time inside the closure,
so definition order is fine. No unrelated code (M1/M2/M3/M5/L1/L2/L3, service
schemas, `config_flow.py`, `const.py`) was modified. The audit report file was
left untouched.
