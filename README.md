# Daily Podcast Queue for Home Assistant + Music Assistant + Sonos

Every morning this builds a "playlist" of the podcasts that published a new
episode **today** and queues them on a Sonos speaker through Music Assistant, in
your chosen listening order, silently skipping any podcast with nothing new.

It also **records each day's playlist** so you can replay past days later — play
catch-up after a holiday, for example — even after those episodes have aged out
of the feeds. The daily automation runs every morning whether you're home or
away, so history builds up on its own; you just replay it when you're back.

It's a small [pyscript](https://github.com/custom-components/pyscript) app: the
podcast list is plain YAML data that one piece of Python iterates over. No
external API, no cloud service — just the podcasts' own RSS feeds.

---

## How it works

1. At the scheduled time, the automation calls the pyscript service
   `pyscript.build_daily_podcast_queue`.
2. For each podcast in your list (in order), it fetches the RSS feed fresh over
   HTTP (cache-busting headers, so "published today" is decided on current
   data), parses the newest episode's audio enclosure URL and publish date.
3. It keeps only episodes whose publish date equals **today in your Home
   Assistant timezone**, preserving your list order and dropping the rest.
4. It **records** that ordered playlist to a per-day file
   (`config/pyscript/podcast_history/YYYY-MM-DD.json`).
5. It sends the ordered list to Music Assistant in a single
   `mass.play_media` call with `enqueue: replace`, which clears the old queue
   and plays the episodes back-to-back.

Failures are isolated: a single unreachable or malformed feed is logged and the
rest of the list still plays. If nothing published today, it logs that and
leaves the speaker alone (and writes no history file for that day).

To replay stored days later, call `pyscript.play_podcast_history` — see
[Catch-up: replay past days](#catch-up-replay-past-days).

---

## Files

```
pyscript/
  config.yaml                      # <-- THE podcast list + player live here
  requirements.txt                 # feedparser (auto-installed by pyscript)
  podcast_history/                 # per-day recorded playlists (auto-created)
    2026-09-20.json                #   e.g. one file per day with episodes
  apps/
    daily_podcasts/
      __init__.py                  # the logic (you shouldn't need to edit this)
automations.yaml                   # the daily 6:00 AM trigger
configuration.yaml.snippet         # lines to merge into your configuration.yaml
README.md
```

`podcast_history/` is created automatically on the first day something is
recorded; it holds runtime data, so it's git-ignored.

Drop `pyscript/` into your HA `config/` directory (so you have
`config/pyscript/...`). Merge the snippet into your `configuration.yaml`, and
add the automation from `automations.yaml`.

---

## Prerequisites / dependencies

- **Home Assistant** with the **Music Assistant** integration, your podcasts
  added as RSS Feed providers, and your Sonos speakers showing as Music
  Assistant player entities (e.g. `media_player.sonos_kitchen`).
- **pyscript** — install via **HACS**. The quickest way is this one-click
  button, which opens HACS on *your* Home Assistant already pointed at the
  pyscript repository — just select **Download**:

  [![Open the pyscript repository in HACS on your Home Assistant.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=custom-components&repository=pyscript&category=integration)

  Then:
  1. Select **Download** on the pyscript page HACS opens.
  2. Restart Home Assistant.
  3. Configure it in YAML (see below). If you previously added pyscript through
     the UI flow, remove `allow_all_imports` / `hass_is_global` from the YAML —
     set those in the integration's *Configure* options instead.

  > The button uses a [My Home Assistant](https://my.home-assistant.io) link; it
  > opens *your own* instance (the URL is stored only in your browser) and
  > requires HACS to already be installed. No HACS yet? Install it first from
  > [hacs.xyz](https://www.hacs.xyz/docs/use/download/download/), then click
  > the button. If the button doesn't work, in HACS search for
  > **Pyscript Python scripting** and download it manually.
- **feedparser** — you don't install this by hand. `pyscript/requirements.txt`
  pins `feedparser==6.0.11` and pyscript installs it into its own environment on
  startup.

---

## One-time setup

1. Copy the `pyscript/` folder and `automations.yaml` into your HA `config/`.
2. Merge `configuration.yaml.snippet` into your `configuration.yaml`. The
   important lines:

   ```yaml
   pyscript: !include pyscript/config.yaml
   automation: !include automations.yaml   # HA's default config already has this
   ```

3. Open `pyscript/config.yaml` and set your **player** and **podcast list**
   (see next section).
4. Restart Home Assistant once (needed the first time so pyscript loads and
   installs feedparser). After that, editing `pyscript/config.yaml` auto-reloads
   — no restart required.

Check **Settings → System → Logs** for lines beginning `[daily_podcasts]`.

---

## What to edit

### Add / remove / reorder a podcast

Edit the `podcasts:` list in **`pyscript/config.yaml`** — this is the only place
you touch. List them in the exact order you want to hear them. Each entry needs
a `name` (used in logs) and a `feed_url` (the RSS feed).

```yaml
apps:
  daily_podcasts:
    player: media_player.sonos_kitchen
    podcasts:
      - name: The Daily
        feed_url: https://feeds.simplecast.com/54nAGcIl
      - name: Up First
        feed_url: https://feeds.npr.org/510318/podcast.xml
      # add, delete, or move lines here — nothing else changes
```

- **Add**: append a new `- name: / feed_url:` entry where you want it in the order.
- **Remove**: delete its two lines.
- **Reorder**: move the entry up or down; playback order follows list order.

No code changes anywhere else. Saving the file auto-reloads pyscript.

### Change the Sonos / Music Assistant target

Set `player:` in `pyscript/config.yaml` to your target entity, e.g.
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

### Other optional settings (in `pyscript/config.yaml`)

```yaml
apps:
  daily_podcasts:
    # timezone: "America/New_York"  # override; defaults to HA's local tz
    # fetch_timeout: 20             # per-feed HTTP timeout in seconds
    # dry_run: false                # true = log the plan, don't touch Sonos
    # history_dir: pyscript/podcast_history  # where daily playlists are saved
    # record_only: false            # true = record daily playlists but never
    #                               #        auto-play; you play them on demand
```

Set `record_only: true` if you never want the morning auto-play and instead
always listen via catch-up (see below). History is still recorded every day.

---

## Manually re-run it (for testing)

Any of these:

- **Developer Tools → Actions**: pick `pyscript.build_daily_podcast_queue`,
  Perform action.
- **Settings → Automations → Daily Podcast Queue → Run**.
- YAML / Developer Tools with overrides, e.g. a dry run against another speaker:

  ```yaml
  action: pyscript.build_daily_podcast_queue
  data:
    dry_run: true
    player: media_player.sonos_office
  ```

`dry_run: true` logs the exact queue it would build (which podcasts are
included/skipped and in what order) without touching playback — handy for
verifying your list.

Re-running the same day is safe: it rebuilds the identical queue with
`enqueue: replace`, so you never get duplicates.

---

## Catch-up: replay past days

Every daily run saves the playlist it built to
`config/pyscript/podcast_history/YYYY-MM-DD.json`. Because the automation runs
every morning regardless of whether you're home, those files pile up while
you're away with nothing for you to switch on. When you're back, replay them
with `pyscript.play_podcast_history`.

Days play in **chronological order**, and within each day the original podcast
order is preserved. The whole span is sent as one `mass.play_media` call with
`enqueue: replace`, so it's a single continuous queue (and re-running it rebuilds
the same queue rather than duplicating).

Call it from **Developer Tools → Actions** (pick `pyscript.play_podcast_history`)
or in YAML. Pick episodes with one of these selectors (first match wins):

```yaml
# Catch up on everything since the day you left:
action: pyscript.play_podcast_history
data:
  since: "2026-09-20"        # from this date through today
```

```yaml
# A specific date range:
action: pyscript.play_podcast_history
data:
  start: "2026-09-20"        # either side is optional
  end: "2026-09-24"
```

```yaml
# Just one day:
action: pyscript.play_podcast_history
data:
  date: "2026-09-22"
```

```yaml
# The last N recorded days:
action: pyscript.play_podcast_history
data:
  days: 5
```

```yaml
# Everything recorded (omit all selectors):
action: pyscript.play_podcast_history
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
| Catch-up after time away | Each day's playlist is recorded to JSON; `play_podcast_history` replays any day/range in chronological, per-day order |
| History survives feed aging | Replay reads the recorded files, not the live feeds, so aged-out episodes still play |

---

## Troubleshooting

- **No `pyscript.build_daily_podcast_queue` service**: pyscript didn't load the
  app. Confirm `pyscript: !include pyscript/config.yaml` is in
  `configuration.yaml`, the `apps: daily_podcasts:` block exists (an app only
  loads if it has a config entry), and restart once. Check logs for pyscript
  errors.
- **`feedparser` import errors**: make sure `pyscript/requirements.txt` is
  present and restart HA so pyscript installs it. Watch the logs on startup.
- **Timezone looks wrong**: this uses `hass.config.time_zone` (requires
  `hass_is_global: true`, which is in `config.yaml`). Set it explicitly with the
  `timezone:` option if needed.
- **Episode not picked up**: some feeds date-stamp episodes in a way that lands
  on a different local calendar day. Run with `dry_run: true` and read the
  `INCLUDE/SKIP` log lines — they show each episode's local publish date.
- **A URL won't play**: `media_type` is intentionally omitted so Music Assistant
  auto-detects from the enclosure URL. If a specific feed misbehaves, check that
  its `<item>` has a proper `<enclosure>` audio URL.
- **Catch-up plays nothing**: check that files exist in
  `config/pyscript/podcast_history/` (there's no file for days nothing
  published, and none written before this feature was installed). Run with
  `dry_run: true` to see which days/episodes it selected. Note that replayed
  episode audio URLs come from the recorded file — if a podcast host expires old
  media URLs, a very old entry may 404 even though the record exists.
