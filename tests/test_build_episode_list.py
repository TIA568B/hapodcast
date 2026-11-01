"""Tests for the core episode-selection pipeline (_build_episode_list).

These encode the data-integrity guarantees from the queue/HWM audit:
  - first run offers only the newest in-window episode (no backlog dump);
  - catch-up includes everything strictly after the HWM;
  - de-duped episodes still advance the HWM (so they aren't re-offered once
    they rotate out of the recent-history de-dupe set);
  - catch-up OFF restricts to today's local date.

Feed fetching is monkeypatched so no network is touched.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

import custom_components.daily_podcasts as mod
from custom_components.daily_podcasts import _build_episode_list

UTC = dt.timezone.utc
TZ = ZoneInfo("UTC")


def _episode(guid, when, title=None, audio_url=None):
    return {
        "guid": guid,
        "title": title or f"ep {guid}",
        "audio_url": audio_url or f"http://audio/{guid}.mp3",
        "published_dt": when,
    }


@pytest.fixture
def fake_feeds(monkeypatch):
    """Patch _fetch_and_parse to return canned per-feed episode lists."""
    feeds: dict[str, list[dict]] = {}

    def _fake(feed_url, timeout):
        if feed_url not in feeds:
            raise ValueError("no such feed")
        # The real parser returns oldest-first.
        return sorted(feeds[feed_url], key=lambda e: e["published_dt"])

    monkeypatch.setattr(mod, "_fetch_and_parse", _fake)
    return feeds


def test_first_run_offers_only_newest(fake_feeds):
    now = dt.datetime.now(UTC)
    fake_feeds["f1"] = [
        _episode("g1", now - dt.timedelta(days=3)),
        _episode("g2", now - dt.timedelta(days=2)),
        _episode("g3", now - dt.timedelta(days=1)),
    ]
    podcasts = [{"name": "Show", "feed_url": "f1", "catchup": True}]

    included, skipped, errors, new_hwm = _build_episode_list(
        podcasts, TZ, 10, hwm={}, seen_guids=set(), max_lookback_days=18
    )

    # First run: only the newest in-window episode is queued.
    assert [e["guid"] for e in included] == ["g3"]
    assert not errors
    # HWM advances to the newest episode.
    assert "f1" in new_hwm


def test_catchup_includes_everything_after_hwm(fake_feeds):
    now = dt.datetime.now(UTC)
    e1 = now - dt.timedelta(days=3)
    e2 = now - dt.timedelta(days=2)
    e3 = now - dt.timedelta(days=1)
    fake_feeds["f1"] = [
        _episode("g1", e1),
        _episode("g2", e2),
        _episode("g3", e3),
    ]
    podcasts = [{"name": "Show", "feed_url": "f1", "catchup": True}]
    # HWM is just after g1, so g2 and g3 are new.
    hwm = {"f1": (e1 + dt.timedelta(seconds=1)).isoformat()}

    included, skipped, errors, new_hwm = _build_episode_list(
        podcasts, TZ, 10, hwm=hwm, seen_guids=set(), max_lookback_days=18
    )

    assert [e["guid"] for e in included] == ["g2", "g3"]


def test_hwm_is_strictly_after_mark(fake_feeds):
    now = dt.datetime.now(UTC)
    e2 = now - dt.timedelta(days=2)
    e3 = now - dt.timedelta(days=1)
    fake_feeds["f1"] = [_episode("g2", e2), _episode("g3", e3)]
    podcasts = [{"name": "Show", "feed_url": "f1", "catchup": True}]
    # HWM exactly equals g2's publish time -> g2 must NOT be re-offered.
    hwm = {"f1": e2.isoformat()}

    included, _skipped, _errors, _new_hwm = _build_episode_list(
        podcasts, TZ, 10, hwm=hwm, seen_guids=set(), max_lookback_days=18
    )

    assert [e["guid"] for e in included] == ["g3"]


def test_deduped_episode_still_advances_hwm(fake_feeds):
    now = dt.datetime.now(UTC)
    e2 = now - dt.timedelta(days=2)
    e3 = now - dt.timedelta(days=1)
    fake_feeds["f1"] = [_episode("g2", e2), _episode("g3", e3)]
    podcasts = [{"name": "Show", "feed_url": "f1", "catchup": True}]
    hwm = {"f1": (e2 - dt.timedelta(days=1)).isoformat()}
    # g3 already seen (recent history) -> de-duped out of the queue, but the
    # HWM must still advance past it so it's not re-offered when it rotates out.
    included, _skipped, _errors, new_hwm = _build_episode_list(
        podcasts, TZ, 10, hwm=hwm, seen_guids={"g3"}, max_lookback_days=18
    )

    guids = [e["guid"] for e in included]
    assert "g3" not in guids  # de-duped
    advanced = dt.datetime.fromisoformat(new_hwm["f1"])
    assert advanced >= e3  # HWM moved past the de-duped newest episode


def test_feed_error_is_isolated_and_keeps_hwm(fake_feeds):
    now = dt.datetime.now(UTC)
    fake_feeds["good"] = [_episode("g1", now - dt.timedelta(hours=1))]
    # "bad" is not registered -> _fetch_and_parse raises for it.
    podcasts = [
        {"name": "Bad", "feed_url": "bad", "catchup": True},
        {"name": "Good", "feed_url": "good", "catchup": True},
    ]

    included, _skipped, errors, new_hwm = _build_episode_list(
        podcasts, TZ, 10, hwm={}, seen_guids=set(), max_lookback_days=18
    )

    assert [e["name"] for e in included] == ["Good"]
    assert [e["name"] for e in errors] == ["Bad"]
    # Failed feed keeps no HWM (retried next run); good feed advances.
    assert "bad" not in new_hwm
    assert "good" in new_hwm


def test_missing_feed_url_reported_as_error(fake_feeds):
    podcasts = [{"name": "NoUrl", "catchup": True}]
    included, _skipped, errors, _new_hwm = _build_episode_list(
        podcasts, TZ, 10, hwm={}, seen_guids=set(), max_lookback_days=18
    )
    assert included == []
    assert errors and errors[0]["name"] == "NoUrl"
