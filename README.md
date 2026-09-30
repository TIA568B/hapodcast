# Daily Podcast Queue

A Home Assistant **custom integration** that, every morning, builds a "playlist"
of the podcasts that published a new episode **today** and queues them on a Sonos
speaker through Music Assistant — in your chosen listening order, silently
skipping any podcast with nothing new.

It also **records each day's playlist**, so you can replay past days later — play
catch-up after a holiday, for example — even after those episodes have aged out
of the feeds. It runs every morning on its own schedule whether you're home or
away, so history builds up automatically; you just replay it when you're back.

No external API, no cloud service — just the podcasts' own RSS feeds.
**Everything is managed from the Home Assistant UI** — no YAML editing. You add,
remove, and reorder podcasts and set the run time from the integration's
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

1. At the time you set, the integration's built-in daily schedule runs the same
   logic as the `daily_podcasts.build_queue` service (no automation needed).
2. For each podcast in your list (in order), it fetches the RSS feed fresh over
   HTTP (cache-busting headers, so "published today" is decided on current
   data), parses the newest episode's audio enclosure URL and publish date.
3. It keeps only episodes whose publish date equals **today in your Home
   Assistant timezone**, preserving your list order and dropping the rest.
4. It **records** that ordered playlist to a per-day file
   (`config/daily_podcasts_history/YYYY-MM-DD.json`).
5. It sends the ordered list to Music Assistant in a single `mass.play_media`
   call with `enqueue: replace`, which clears the old queue and plays the
   episodes back-to-back.

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

- **Home Assistant** with the **Music Assistant** integration, your podcasts
  added as RSS Feed providers, and your Sonos speakers showing as Music
  Assistant player entities (e.g. `media_player.sonos_kitchen`).
- **HACS** (for the one-click install). Not required for the manual install.
- **feedparser** — installed automatically; it's declared in the integration's
  `manifest.json` and Home Assistant installs it on startup.

---

## Set it up

Everything is done in the UI — no YAML.

1. **Settings → Devices & services → Add integration →** search **Daily Podcast
   Queue**.
2. In the setup dialog, choose your **Player** (the Sonos / Music Assistant
   `media_player`), the **daily run time** (default 06:00), and whether it should
   **run automatically each day**.
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

- **Settings** — change the player, the daily run time, the automatic-run
  on/off switch, record-only mode, and an optional timezone override.
- **Add a podcast** — enter a name and RSS feed URL. It's added to the end of
  the list; the URL is validated.
- **Remove a podcast** — pick one from the list to delete.
- **Move a podcast earlier / later** — reorder the list; playback order follows
  it.

Changes take effect immediately (the integration reloads itself and re-arms the
daily schedule). No restart, no YAML.

**Record-only mode**: turn it on in Settings if you never want the morning
auto-play and instead always listen via catch-up. History is still recorded
every day.

**Turn the daily run off**: uncheck "Run automatically each day" in Settings.
You can still trigger it manually or via the service any time.

---

## Manually re-run it (for testing)

Any of these:

- **Developer Tools → Actions**: pick **Daily Podcast Queue: Build today's
  podcast queue** (`daily_podcasts.build_queue`), Perform action.
- YAML with overrides, e.g. a dry run against another speaker:

  ```yaml
  action: daily_podcasts.build_queue
  data:
    dry_run: true
    player: media_player.sonos_office
  ```

`dry_run: true` logs the exact queue it would build (which podcasts are
included/skipped and in what order) without touching playback or writing
history — handy for verifying your list.

Re-running the same day is safe: it rebuilds the identical queue with
`enqueue: replace`, so you never get duplicates.

---

## Catch-up: replay past days

Every daily run saves the playlist it built to
`config/daily_podcasts_history/YYYY-MM-DD.json`. Because the automation runs
every morning regardless of whether you're home, those files pile up while
you're away with nothing for you to switch on. When you're back, replay them
with `daily_podcasts.play_history`.

Days play in **chronological order**, and within each day the original podcast
order is preserved. The whole span is sent as one `mass.play_media` call with
`enqueue: replace`, so it's a single continuous queue (and re-running it rebuilds
the same queue rather than duplicating).

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

**A tidy "since I last listened" button.** The daily build is scheduled inside
the integration, so you don't need any automation for normal use. If you'd like
a one-tap catch-up button, create an `input_datetime` helper (e.g.
`input_datetime.podcasts_last_played`) and point a dashboard button / NFC tag at
`daily_podcasts.play_history` with `since:` set from that helper. The optional
`automations.yaml` in this repo has a ready-to-adapt example.

---

## Behavior guarantees (and how they're met)

| Requirement | How |
| --- | --- |
| Manage without YAML | Config flow + options menu (add/remove/reorder/settings) in the UI |
| Edit the list, nothing else | The ordered podcast list is the one thing you edit, from Configure |
| Daily run without automations.yaml | Built-in schedule via `async_track_time_change`, re-armed on options change |
| Preserve order while skipping | Included items keep original list order; skipped/failed simply omitted |
| Published-today only, local tz | Each episode's UTC pubDate is converted to `hass.config.time_zone` before comparing dates |
| Fresh data | Feeds are fetched over HTTP with no-cache headers at trigger time |
| Queue on Sonos in order | Single `mass.play_media` call, ordered `media_id` list, `enqueue: replace` |
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
- **Nothing plays in the morning**: confirm "Run automatically each day" is on
  and the run time is what you expect (Configure → Settings). The log shows
  `Daily run scheduled for HH:MM:SS` when armed.
- **`feedparser` errors on startup**: HA installs it from the manifest; watch
  the startup logs. A restart usually resolves a transient install.
- **Timezone looks wrong**: this uses `hass.config.time_zone`. Set the
  `timezone:` option explicitly if you need to override it.
- **Episode not picked up**: some feeds date-stamp episodes so they land on a
  different local calendar day. Run `build_queue` with `dry_run: true` and read
  the `INCLUDE/SKIP` log lines — they show each episode's local publish date.
- **A URL won't play**: `media_type` is intentionally omitted so Music Assistant
  auto-detects from the enclosure URL. If a specific feed misbehaves, check that
  its `<item>` has a proper `<enclosure>` audio URL.
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
