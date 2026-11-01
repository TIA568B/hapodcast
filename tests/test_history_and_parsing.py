"""Tests for history file merge-by-guid and feed parsing."""

import datetime as dt
import json
import os

from custom_components.daily_podcasts import (
    _entry_guid,
    _entry_published_utc,
    _extract_audio_url,
    _list_history_dates,
    _normalise_podcasts,
    _parse_feed_episodes,
    _parse_iso,
    _recent_guids,
    _write_history_file,
)

UTC = dt.timezone.utc


# --- _write_history_file (merge by guid) -----------------------------------


def test_history_merge_unions_by_guid(tmp_path):
    path = os.path.join(tmp_path, "2026-01-01.json")
    _write_history_file(
        path,
        {"date": "2026-01-01", "episodes": [{"guid": "g1", "title": "a"}]},
    )
    # Second run same day adds g2 and re-offers g1 -> g1 kept once, g2 appended.
    _write_history_file(
        path,
        {
            "date": "2026-01-01",
            "episodes": [{"guid": "g1", "title": "a"}, {"guid": "g2", "title": "b"}],
        },
    )
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    assert [e["guid"] for e in payload["episodes"]] == ["g1", "g2"]


def test_history_merge_preserves_existing_order(tmp_path):
    path = os.path.join(tmp_path, "2026-01-02.json")
    _write_history_file(
        path,
        {"episodes": [{"guid": "g1"}, {"guid": "g2"}]},
    )
    _write_history_file(path, {"episodes": [{"guid": "g3"}]})
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    assert [e["guid"] for e in payload["episodes"]] == ["g1", "g2", "g3"]


def test_history_merge_falls_back_to_audio_url_key(tmp_path):
    path = os.path.join(tmp_path, "2026-01-03.json")
    _write_history_file(path, {"episodes": [{"audio_url": "u1"}]})
    _write_history_file(path, {"episodes": [{"audio_url": "u1"}, {"audio_url": "u2"}]})
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    assert [e["audio_url"] for e in payload["episodes"]] == ["u1", "u2"]


# --- _list_history_dates / _recent_guids -----------------------------------


def test_list_history_dates_ignores_non_date_files(tmp_path):
    base = str(tmp_path)
    for name in ("2026-01-01.json", "2026-01-03.json", "notes.json", "x.txt"):
        with open(os.path.join(base, name), "w", encoding="utf-8") as fh:
            fh.write("{}")
    assert _list_history_dates(base) == ["2026-01-01", "2026-01-03"]


def test_recent_guids_collects_from_recent_files(tmp_path):
    base = str(tmp_path)
    _write_history_file(
        os.path.join(base, "2026-01-01.json"), {"episodes": [{"guid": "g1"}]}
    )
    _write_history_file(
        os.path.join(base, "2026-01-02.json"), {"episodes": [{"guid": "g2"}]}
    )
    assert _recent_guids(base, days=30) == {"g1", "g2"}


# --- _parse_iso -------------------------------------------------------------


def test_parse_iso_naive_assumed_utc():
    parsed = _parse_iso("2026-01-01T12:00:00")
    assert parsed == dt.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_parse_iso_bad_input_returns_none():
    assert _parse_iso("not-a-date") is None
    assert _parse_iso(None) is None


# --- feed parsing -----------------------------------------------------------


def test_entry_guid_prefers_id_then_audio_url():
    assert _entry_guid({"id": "abc"}, "http://a.mp3") == "abc"
    assert _entry_guid({}, "http://a.mp3") == "http://a.mp3"


def test_extract_audio_url_from_enclosure():
    entry = {"enclosures": [{"type": "audio/mpeg", "href": "http://a.mp3"}]}
    assert _extract_audio_url(entry) == "http://a.mp3"


def test_entry_published_utc_from_parsed_tuple():
    entry = {"published_parsed": (2026, 1, 1, 12, 0, 0, 0, 0, 0)}
    assert _entry_published_utc(entry) == dt.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>T</title>
<item>
  <title>Episode One</title>
  <guid>ep1</guid>
  <pubDate>Mon, 01 Jan 2026 08:00:00 GMT</pubDate>
  <enclosure url="http://audio/ep1.mp3" type="audio/mpeg" length="1"/>
</item>
<item>
  <title>Episode Two</title>
  <guid>ep2</guid>
  <pubDate>Tue, 02 Jan 2026 08:00:00 GMT</pubDate>
  <enclosure url="http://audio/ep2.mp3" type="audio/mpeg" length="1"/>
</item>
</channel></rss>"""


def test_parse_feed_episodes_oldest_first():
    eps = _parse_feed_episodes(RSS)
    assert [e["guid"] for e in eps] == ["ep1", "ep2"]
    assert eps[0]["audio_url"] == "http://audio/ep1.mp3"
    assert eps[0]["published_dt"] < eps[1]["published_dt"]


# --- _normalise_podcasts ----------------------------------------------------


def test_normalise_migrates_weekend_catchup():
    out = _normalise_podcasts([{"name": "S", "feed_url": "f", "weekend_catchup": True}])
    assert out[0]["catchup"] is True
    assert "weekend_catchup" not in out[0]


def test_normalise_keeps_explicit_catchup():
    out = _normalise_podcasts(
        [{"name": "S", "feed_url": "f", "catchup": False, "weekend_catchup": True}]
    )
    assert out[0]["catchup"] is False
