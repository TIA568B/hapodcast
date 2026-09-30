# Daily Podcast Queue

A Home Assistant **custom integration** that, every morning, builds a "playlist"
of the podcasts that published a new episode **today** and queues them on a Sonos
speaker through Music Assistant — in your chosen listening order, silently
skipping any podcast with nothing new.

It also **records each day's playlist**, so you can replay past days later — play
catch-up after a holiday, for example — even after those episodes have aged out
of the feeds. The daily automation runs every morning whether you're home or
away, so history builds up on its own; you just replay it when you're back.

No external API, no cloud service — just the podcasts' own RSS feeds. The
podcast list is a single ordered YAML list you edit in one place.

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
3. Add the YAML configuration and automation below.

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

1. At the scheduled time, the automation calls the service
   `daily_podcasts.build_queue`.
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
    __init__.py        # the integration logic + services
    const.py           # constants
    manifest.json      # domain, version, feedparser requirement
    services.yaml      # service definitions for the HA UI
hacs.json              # HACS metadata (repo root)
automations.yaml       # the daily 6:00 AM trigger
configuration.yaml.snippet  # YAML config to merge into configuration.yaml
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

## Configure it

After installing and restarting, add the configuration. Merge
`configuration.yaml.snippet` into your `configuration.yaml` — the core of it:

```yaml
daily_podcasts:
  player: media_player.sonos_kitchen
  podcasts:
    - name: The Daily
      feed_url: https://feeds.simplecast.com/54nAGcIl
    - name: Up First
      feed_url: https://feeds.npr.org/510318/podcast.xml
    # add, delete, or move lines here — nothing else changes
```

Then add the automation (from `automations.yaml`) so it runs each morning.
`automation: !include automations.yaml` is already in HA's default config.

Restart (or reload YAML) after editing configuration. Check
**Settings → System → Logs** for lines beginning `[daily_podcasts]`.

---

## What to edit

### Add / remove / reorder a podcast

Edit the `podcasts:` list under `daily_podcasts:` in **`configuration.yaml`** —
this is the only place you touch. List them in the exact order you want to hear
them. Each entry needs a `name` (used in logs) and a `feed_url` (the RSS feed).

- **Add**: append a new `- name: / feed_url:` entry where you want it in the order.
- **Remove**: delete its two lines.
- **Reorder**: move the entry up or down; playback order follows list order.

Then reload YAML configuration (or restart). No code changes anywhere else.

### Change the Sonos / Music Assistant target

Set `player:` under `daily_podcasts:` to your target entity, e.g.
`media_player.sonos_office`. You can also override per-run from the automation
or a service call with `data: { player: media_player.sonos_office }`.

### Change the trigger time

Edit the `at:` value in **`automations.yaml`** (default `"06:00:00"`, local
time):

```yaml
trigger:
  - platform: time
    at: "07:30:00"
```

### Other optional settings (under `daily_podcasts:`)

```yaml
daily_podcasts:
  # timezone: America/New_York          # override; defaults to HA's local tz
  # fetch_timeout: 20                    # per-feed HTTP timeout in seconds
  # history_dir: daily_podcasts_history  # where daily playlists are saved
  # record_only: false                   # true = record daily but never
  #                                       #        auto-play; play on demand
```

Set `record_only: true` if you never want the morning auto-play and instead
always listen via catch-up. History is still recorded every day.

---

## Manually re-run it (for testing)

Any of these:

- **Developer Tools → Actions**: pick **Daily Podcast Queue: Build today's
  podcast queue** (`daily_podcasts.build_queue`), Perform action.
- **Settings → Automations → Daily Podcast Queue → Run**.
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

**A tidy "since I last listened" button.** Create an `input_datetime` helper
(e.g. `input_datetime.podcasts_last_played`), point a button/NFC tag at the
commented automation in `automations.yaml`, and it will replay everything since
that date. See the example block in `automations.yaml`.

---

## Behavior guarantees (and how they're met)

| Requirement | How |
| --- | --- |
| Edit the list, nothing else | `podcasts:` is data in one YAML list; one loop iterates it |
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
- **No `daily_podcasts.*` services**: the integration loads only when
  `daily_podcasts:` is present in `configuration.yaml`. Add the config block and
  reload/restart. Check logs for a config validation error (a bad `feed_url` or
  missing `player` will fail validation).
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
