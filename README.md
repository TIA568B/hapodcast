# Daily Podcast Queue

A Home Assistant **custom integration** that prepares a daily "playlist" of the
podcasts that published a new episode **today** — in your chosen listening order,
silently skipping any podcast with nothing new — ready for you to **play on
demand** on a Sonos speaker when you have time.

**It never auto-plays.** Each morning it quietly *prepares* that day's playlist
(fetches feeds, works out today's episodes, records them). You then press a
button — or call the service, or trigger it from your own automation — to
actually play it. Nothing starts blasting out of a speaker at 6am.

Playback goes through **Music Assistant** (`mass.play_media`) when that's
available, and otherwise falls back to Home Assistant's built-in
`media_player.play_media`, so it works with a plain Sonos speaker even without
Music Assistant.

Because every day's playlist is **recorded**, you can also replay past days —
play catch-up after a holiday, for example — even after those episodes have aged
out of the feeds.

No external API, no cloud service — just the podcasts' own RSS feeds.
**Everything is managed from the Home Assistant UI** — no YAML editing. You add,
remove, and reorder podcasts and set the prepare time from the integration's
**Configure** screen.

---

## Install via HACS (one click)

This project is a HACS-installable custom integration. Add it to HACS with this
button, which opens HACS on **your** Home Assistant already pointed at this
repository:

[![Open this repository in HACS on your Home Assistant.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=TIA568B&repository=hapodcast&category=integration)

Then:

1. In the dialog that opens, confirm adding **TIA568B/hapodcast** as an
   **Integration** custom repository.
2. Select **Download**, then **restart Home Assistant**.
3. Go to **Settings → Devices & services → Add integration**, search for
   **Daily Podcast Queue**, and follow the setup (see [Set it up](#set-it-up)).

> The button uses a [My Home Assistant](https://my.home-assistant.io) link: it
> opens *your own* instance (the URL is stored only in your browser) and needs
> HACS already installed. If the button doesn't open the add-repository dialog,
> add it manually in HACS: **⋮ → Custom repositories →** URL
> `https://github.com/TIA568B/hapodcast`, category **Integration →** Add, then
> download it.

**Manual install (no HACS):** copy `custom_components/daily_podcasts/` into your
HA `config/custom_components/` directory and restart. Home Assistant installs the
`feedparser` dependency automatically from the integration's manifest.

---

## How it works

**Preparing (automatic, each morning — no sound):**

1. At the time you set, the integration's built-in daily schedule runs — in
   "prepare only" mode (`build_queue` with `play: false`). It never plays.
2. For each podcast in your list (in order), it fetches the RSS feed fresh over
   HTTP (cache-busting headers, so "published today" is decided on current
   data) and parses every episode's audio enclosure URL and publish date.
3. It keeps **all** episodes published **since that podcast was last prepared**,
   in your Home Assistant timezone — so a feed that drops more than one episode
   in a day contributes all of them (ordered oldest-first within that feed).
   Feeds keep your configured list order; feeds with nothing new are dropped.

   **Catch-up window (per podcast, default on):** each podcast tracks a
   high-water mark — the publish time of the newest episode it has already
   offered you. A run includes everything published after that mark, through
   now. This is **gap-proof**: whether you missed a day, a weekend, a holiday,
   or Home Assistant was down, the next run picks up exactly what you haven't
   been given yet — no weekday special-casing.
   - **First run** (no mark yet): only today's episodes, so a fresh install
     doesn't dump the back-catalogue.
   - **Max look-back** (default 18 days, Settings): caps how far back catch-up
     reaches, so a very old mark after a long outage can't queue a huge backlog.
   - **De-duplication:** episodes are tracked by their feed GUID, so an episode
     you've already been offered is never queued twice, even if its feed's
     timestamps are fuzzy.
   - **Catch-up off** (per podcast): that podcast uses **today only** instead.
   - The mark only advances when a prepare/play actually records episodes;
     replaying history and dry-runs never move it, and a feed that fails to
     fetch keeps its mark and retries next run.
4. It **records** that ordered playlist to a per-day file
   (`config/daily_podcasts_history/YYYY-MM-DD.json`).

**Playing (on demand — when you ask):**

5. When you press the button / call `daily_podcasts.build_queue` (which plays by
   default) / fire it from your own automation, it builds today's playlist the
   same way and queues it on your player, clearing the old queue first and
   playing the episodes back-to-back:
   - **With Music Assistant**: one `mass.play_media` call with the whole list,
     `enqueue: replace`.
   - **Without Music Assistant** (native Sonos): clear the queue
     (`media_player.clear_playlist`), queue the first episode with
     `enqueue: play` (which builds a real Sonos queue and starts it), then
     append the rest with `enqueue: add`, so playback advances episode to
     episode.

   To replay a previous day instead, use `daily_podcasts.play_history` (see
   [Catch-up](#catch-up-replay-past-days)).

Failures are isolated: a single unreachable or malformed feed is logged and the
rest of the list still plays. If nothing published today, it logs that and
leaves the speaker alone (and writes no history file for that day).

To replay stored days later, call `daily_podcasts.play_history` — see
[Catch-up: replay past days](#catch-up-replay-past-days).

---

## Files

```
custom_components/
  daily_podcasts/
    __init__.py        # the integration logic + services + daily scheduler
    config_flow.py     # the UI setup + options (manage) screens
    const.py           # constants
    manifest.json      # domain, version, config_flow, feedparser requirement
    services.yaml      # service definitions for the HA UI
    strings.json       # UI strings
    translations/
      en.json          # English UI translations
hacs.json              # HACS metadata (repo root)
automations.yaml       # OPTIONAL example (catch-up button); not required
configuration.yaml.snippet  # OPTIONAL YAML import path; not required
README.md
```

At runtime the integration creates `config/daily_podcasts_history/` for the
recorded playlists. That's runtime data, so it's git-ignored.

---

## Prerequisites

- **Home Assistant** with a Sonos (or other) `media_player` entity to queue onto.
- **Music Assistant is optional.** If you have it set up (podcasts added as RSS
  Feed providers, speakers showing as MA player entities), the integration uses
  `mass.play_media`. If not, it falls back to the native
  `media_player.play_media` on whatever `media_player` you select (e.g. the
  Sonos integration's own entity).
- **HACS** (for the one-click install). Not required for the manual install.
- **feedparser** — installed automatically; it's declared in the integration's
  `manifest.json` and Home Assistant installs it on startup.

---

## Set it up

Everything is done in the UI — no YAML.

1. **Settings → Devices & services → Add integration →** search **Daily Podcast
   Queue**.
2. In the setup dialog, choose your **Player** (the Sonos / Music Assistant
   `media_player`), the **time to prepare the daily playlist** (default 06:00),
   and whether to **prepare a playlist automatically each day**. (Preparing
   never plays — it just gets today's playlist ready.)
3. Finish. Then open the integration's **Configure** button to add your
   podcasts.

Check **Settings → System → Logs** for lines beginning `[daily_podcasts]`.

> **Coming from an older YAML setup?** If you still have a `daily_podcasts:`
> block in `configuration.yaml`, the integration imports it into a UI entry once
> on startup, then ignores the YAML. You can delete the block afterwards and
> manage everything from Configure. New installs don't need any YAML at all.

---

## Manage it (all in the UI)

Open **Settings → Devices & services → Daily Podcast Queue → Configure**. You get
a small menu:

- **Settings** — change the player, the time to prepare the daily playlist, the
  "prepare automatically each day" on/off switch, the **max catch-up look-back
  (days)**, and an optional timezone override.
- **Add a podcast** — enter a name and RSS feed URL, and whether to **catch up
  missed episodes** (default on; off = today only). It's added to the end of the
  list; the URL is validated.
- **Edit a podcast** — change a podcast's name, feed URL, or its catch-up
  setting.
- **Remove a podcast** — pick one from the list to delete.
- **Move a podcast earlier / later** — reorder the list; playback order follows
  it.

Changes take effect immediately (the integration reloads itself and re-arms the
daily schedule). No restart, no YAML.

**Turn off the automatic daily prepare**: uncheck "Prepare a playlist
automatically each day" in Settings. You can still prepare/play on demand any
time via the button or service.

---

## Play it (on demand)

The daily schedule only *prepares* the playlist; **you** decide when to play.
Any of these plays today's episodes on your player:

- **A dashboard button** — the recommended setup. See
  [Buttons on a dashboard](#buttons-on-a-dashboard) below.
- **Developer Tools → Actions**: pick **Daily Podcast Queue: Build and play
  today's podcast queue** (`daily_podcasts.build_queue`) and Perform action — it
  plays by default.
- **Your own automation** calling `daily_podcasts.build_queue` (e.g. tied to
  arriving home, a voice command, etc.).

Useful options on `build_queue`:

```yaml
action: daily_podcasts.build_queue
data:
  player: media_player.office   # override the configured player
  play: false                   # prepare/record only, don't play
  dry_run: true                 # log the plan, touch nothing
```

- `play: false` — prepare today's playlist without playing (same as the daily
  schedule).
- `dry_run: true` — logs which podcasts are included/skipped and in what order,
  without playing or writing history. Handy for verifying your list.

Re-running the same day is safe: it rebuilds the identical queue, replacing the
old one, so you never get duplicates.

### Buttons on a dashboard

A ready-made set of helpers + automations is included (created on your instance):

- **Play Daily Podcasts** (`input_button.play_daily_podcasts`) → plays today's
  playlist on the office Sonos.
- **Podcast Catch Up Range** (`input_select.podcast_catch_up_range`) → pick
  "Last N days" / "Everything recorded".
- **Play Podcast Catch Up** (`input_button.play_podcast_catch_up`) → plays the
  selected range.

Add those three to a dashboard card. Press **Play Daily Podcasts** when you're
ready to listen; set a range and press **Play Podcast Catch Up** to catch up.

---

## Catch-up: replay past days

Every daily run saves the playlist it built to
`config/daily_podcasts_history/YYYY-MM-DD.json`. Because the daily prepare runs
every morning regardless of whether you're home, those files pile up while
you're away with nothing for you to switch on. When you're back, replay them
with `daily_podcasts.play_history`.

Days play in **chronological order**, and within each day the original podcast
order is preserved. The whole span is queued the same way as a normal play
(`mass.play_media` if Music Assistant is available, otherwise
`media_player.play_media` replace-then-add), clearing the previous queue first —
so it's a single continuous queue, and re-running it rebuilds the same queue
rather than duplicating.

Call it from **Developer Tools → Actions** (pick **Daily Podcast Queue: Play
recorded podcast history**) or in YAML. Pick episodes with one of these
selectors (first match wins):

```yaml
# Catch up on everything since the day you left:
action: daily_podcasts.play_history
data:
  since: "2026-09-20"        # from this date through today
```

```yaml
# A specific date range:
action: daily_podcasts.play_history
data:
  start: "2026-09-20"        # either side is optional
  end: "2026-09-24"
```

```yaml
# Just one day:
action: daily_podcasts.play_history
data:
  date: "2026-09-22"
```

```yaml
# The last N recorded days:
action: daily_podcasts.play_history
data:
  days: 5
```

```yaml
# Everything recorded (omit all selectors):
action: daily_podcasts.play_history
```

Optional extras: `player:` to target a different speaker, and `dry_run: true` to
log exactly what would play without touching the speaker.

**Selector precedence:** `date` → `start`/`end` → `since` → `days` → (nothing =
all recorded days). Only days that actually have a recorded file are included;
requesting a day with no file is silently skipped.

The preset **Podcast Catch Up Range** picker (Last N days / Everything) covers
most catch-up needs. If you'd rather catch up from an **exact date**, create a
Date `input_datetime` helper and point a button at `daily_podcasts.play_history`
with `since:` set from it — the optional `automations.yaml` in this repo has a
ready-to-adapt example.

---

## Behavior guarantees (and how they're met)

| Requirement | How |
| --- | --- |
| Never auto-plays | Daily schedule runs `build_queue` with `play: false` — it only prepares/records; playback happens only on an explicit call |
| Play on demand | Button / service / your automation calls `build_queue` (plays by default) |
| Manage without YAML | Config flow + options menu (add/remove/reorder/settings) in the UI |
| Edit the list, nothing else | The ordered podcast list is the one thing you edit, from Configure |
| Daily prepare without automations.yaml | Built-in schedule via `async_track_time_change`, re-armed on options change |
| Preserve order while skipping | Included items keep original list order; skipped/failed simply omitted |
| All of a day's episodes | Every episode in the catch-up window is included per feed (not just the newest), oldest-first within the feed |
| Local-tz dates | Each episode's UTC pubDate is converted to `hass.config.time_zone` before comparing |
| Never miss anything | Per-podcast high-water mark: each run includes everything published since the last successful prepare (gap-proof across missed days/outages) |
| No duplicates | Mark advances only on recorded prepares; GUID de-dupe against recent history; replays/dry-runs don't advance it |
| Bounded backlog | Max look-back (default 18 days) caps how far catch-up reaches |
| First run is safe | No mark yet → today only, so a fresh install doesn't dump the back-catalogue |
| Per-podcast catch-up | Each podcast has a catch-up on/off flag (default on; off = today only), set from Add/Edit a podcast |
| Resilient to feed outages | A feed that fails to fetch keeps its mark and retries next run |
| Fresh data | Feeds are fetched over HTTP with no-cache headers at trigger time |
| Queue on Sonos in order | `mass.play_media` (one ordered list) when MA is present; else clear queue + first `enqueue: play` + rest `enqueue: add` so a real Sonos queue is built and advances |
| Works without Music Assistant | Falls back to native `media_player` services on the chosen player |
| Feed failure isolation | Per-feed try/except logs the error and continues |
| Nothing today | No episodes → no service call, speaker untouched, logged |
| Idempotent reruns | `enqueue: replace` rebuilds the same queue, no duplicates |
| Catch-up after time away | Each day's playlist is recorded to JSON; `play_history` replays any day/range in chronological, per-day order |
| History survives feed aging | Replay reads the recorded files, not the live feeds, so aged-out episodes still play |
| Non-blocking | Feed fetch/parse and file I/O run in the executor, off the event loop |

---

## Troubleshooting

- **Integration not found after install**: make sure you **restarted** HA after
  downloading in HACS, and that `custom_components/daily_podcasts/manifest.json`
  exists. Check the logs for load errors.
- **No `daily_podcasts.*` services**: make sure you added the integration from
  **Settings → Devices & services → Add integration**. The services register
  once the config entry is set up. Check the logs for load errors.
- **"It didn't play this morning"**: that's by design — the daily schedule only
  *prepares* the playlist (it never auto-plays). Press the **Play Daily
  Podcasts** button, or call `daily_podcasts.build_queue`, to play. The log
  shows `Daily run scheduled for HH:MM:SS` when the prepare is armed, and
  `Prepared today's playlist ... not playing now (play=False)` after it prepares.
- **The button records but doesn't play**: check the log line — if you see
  `play=False`, the call passed `play: false`. The button should call
  `build_queue` with no `play` (defaults to true) or `play: true`.
- **`feedparser` errors on startup**: HA installs it from the manifest; watch
  the startup logs. A restart usually resolves a transient install.
- **Timezone looks wrong**: this uses `hass.config.time_zone`. Set the
  `timezone:` option explicitly if you need to override it.
- **Episode not picked up**: some feeds date-stamp episodes so they land on a
  different local calendar day. Run `build_queue` with `dry_run: true` and read
  the `INCLUDE/SKIP` log lines — they show each episode's local publish date.
- **A URL won't play**: with the native fallback, episodes are sent as
  `media_content_type: music`; with Music Assistant, type is auto-detected. If a
  specific feed misbehaves, check that its `<item>` has a proper `<enclosure>`
  audio URL.
- **Which player to pick**: if Music Assistant is set up, choose its MA player
  entity for the speaker. If not, choose the speaker's native entity (e.g. the
  Sonos integration's `media_player.*`). The log line `QUEUE #n -> <player> via
  <method>` shows which service was used.
- **Catch-up plays nothing**: check that files exist in
  `config/daily_podcasts_history/` (there's no file for days nothing published,
  and none written before this was installed). Run with `dry_run: true` to see
  which days/episodes it selected. Replayed audio URLs come from the recorded
  file — if a podcast host expires old media URLs, a very old entry may 404.

---

## Notes for publishing this repo

If you later want this in the HACS **default** store (not just as a custom
repository), HACS also requires: a GitHub repo **description** and **topics**, a
GitHub **release/tag**, and brand assets (an `icon.png`) submitted to the
[home-assistant/brands](https://github.com/home-assistant/brands) repo. None of
that is needed for the custom-repository install above.
