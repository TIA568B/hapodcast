"""Regression tests for the instant-play backlog selection.

These cover the three bugs behind "pressing play resurrects episodes I've
already heard or removed":

  1. Episodes already in the played set must be excluded.
  2. Episodes whose feed is no longer configured must be excluded (a dropped
     show like Cyberwire kept reappearing from stale history).
  3. A removed episode / end-of-queue episode must be recognised as played.

The production closures (_collect_unplayed_episodes, _handle_player_state,
handle_remove_from_queue) delegate to these pure module-level helpers, so this
exercises the exact code that runs at runtime.
"""

from custom_components.daily_podcasts import (
    _drop_played_episodes,
    _episode_is_configured,
    _episode_key,
    _select_unplayed_backlog,
    _should_mark_played,
)


def _ep(name, feed_url=None, guid=None, audio_url=None):
    ep = {"name": name}
    if feed_url is not None:
        ep["feed_url"] = feed_url
    if guid is not None:
        ep["guid"] = guid
    if audio_url is not None:
        ep["audio_url"] = audio_url
    return ep


# --- _episode_key -----------------------------------------------------------


def test_episode_key_prefers_guid():
    assert _episode_key({"guid": "g1", "audio_url": "u1"}) == "g1"


def test_episode_key_falls_back_to_audio_url():
    assert _episode_key({"audio_url": "u1"}) == "u1"


def test_episode_key_empty_when_neither():
    assert _episode_key({"name": "x"}) == ""


# --- _episode_is_configured -------------------------------------------------


def test_configured_match_by_feed_url():
    ep = _ep("The Daily", feed_url="http://feed/daily")
    assert _episode_is_configured(ep, {"http://feed/daily"}, set()) is True


def test_removed_feed_excluded_by_url():
    ep = _ep("Cyberwire", feed_url="http://feed/cyberwire")
    # Cyberwire is no longer in the configured set -> excluded.
    assert _episode_is_configured(ep, {"http://feed/daily"}, set()) is False


def test_legacy_entry_without_feed_url_falls_back_to_name():
    ep = _ep("Up First")  # no feed_url (pre-upgrade history)
    assert _episode_is_configured(ep, set(), {"Up First"}) is True
    assert _episode_is_configured(ep, set(), {"The Daily"}) is False


def test_feed_url_takes_precedence_over_name():
    # Same name, but the feed_url is gone -> excluded even if the name matches.
    ep = _ep("The Daily", feed_url="http://feed/old-daily")
    assert (
        _episode_is_configured(ep, {"http://feed/new-daily"}, {"The Daily"})
        is False
    )


# --- _select_unplayed_backlog ----------------------------------------------


def test_backlog_excludes_played():
    days = [[_ep("A", feed_url="f1", guid="g1"), _ep("B", feed_url="f1", guid="g2")]]
    out = _select_unplayed_backlog(days, {"g1"}, {"f1"}, {"A", "B"})
    assert [e["guid"] for e in out] == ["g2"]


def test_backlog_excludes_removed_feed():
    days = [
        [
            _ep("The Daily", feed_url="f-daily", guid="g1"),
            _ep("Cyberwire", feed_url="f-cyber", guid="g2"),
        ]
    ]
    # Only The Daily is still configured.
    out = _select_unplayed_backlog(days, set(), {"f-daily"}, {"The Daily"})
    assert [e["guid"] for e in out] == ["g1"]


def test_backlog_dedupes_across_days():
    days = [
        [_ep("A", feed_url="f1", guid="g1")],
        [_ep("A", feed_url="f1", guid="g1"), _ep("B", feed_url="f1", guid="g2")],
    ]
    out = _select_unplayed_backlog(days, set(), {"f1"}, {"A", "B"})
    assert [e["guid"] for e in out] == ["g1", "g2"]


def test_backlog_preserves_order_oldest_day_first():
    days = [
        [_ep("A", feed_url="f1", guid="g1")],
        [_ep("B", feed_url="f1", guid="g2")],
    ]
    out = _select_unplayed_backlog(days, set(), {"f1"}, {"A", "B"})
    assert [e["guid"] for e in out] == ["g1", "g2"]


def test_backlog_skips_keyless_episodes():
    days = [[{"name": "A", "feed_url": "f1"}]]  # no guid/audio_url -> no key
    out = _select_unplayed_backlog(days, set(), {"f1"}, {"A"})
    assert out == []


def test_backlog_empty_when_all_played_or_removed():
    days = [
        [
            _ep("A", feed_url="f1", guid="g1"),  # played
            _ep("B", feed_url="gone", guid="g2"),  # removed feed
        ]
    ]
    out = _select_unplayed_backlog(days, {"g1"}, {"f1"}, {"A"})
    assert out == []


# --- _drop_played_episodes --------------------------------------------------
# Covers the prepared-playlist replay path: replaying today's recorded
# playlist must not resurrect an episode the user removed from the queue
# (which marks it played). This is the second "removed items reappear" channel.


def test_drop_played_removes_played_keeps_order():
    eps = [_ep("A", guid="g1"), _ep("B", guid="g2"), _ep("C", guid="g3")]
    out = _drop_played_episodes(eps, {"g2"})
    assert [e["guid"] for e in out] == ["g1", "g3"]


def test_drop_played_all_played_returns_empty():
    eps = [_ep("A", guid="g1"), _ep("B", guid="g2")]
    assert _drop_played_episodes(eps, {"g1", "g2"}) == []


def test_drop_played_none_played_returns_all():
    eps = [_ep("A", guid="g1"), _ep("B", guid="g2")]
    assert _drop_played_episodes(eps, set()) == eps


def test_drop_played_matches_on_audio_url_key():
    eps = [_ep("A", audio_url="u1"), _ep("B", audio_url="u2")]
    out = _drop_played_episodes(eps, {"u1"})
    assert [e["audio_url"] for e in out] == ["u2"]


# --- _should_mark_played ----------------------------------------------------


def test_mark_played_on_track_advance():
    assert _should_mark_played("u1", "u2", "playing", "playing") is True


def test_mark_played_on_end_of_queue_idle():
    # Final episode: id stays or clears and state goes idle/stopped.
    assert _should_mark_played("u1", None, "playing", "idle") is True
    assert _should_mark_played("u1", "u1", "playing", "stopped") is True
    assert _should_mark_played("u1", None, "playing", "off") is True


def test_not_marked_on_pause_same_track():
    assert _should_mark_played("u1", "u1", "playing", "paused") is False


def test_not_marked_when_nothing_was_playing():
    assert _should_mark_played(None, "u1", None, "playing") is False


def test_not_marked_on_metadata_update_same_track():
    assert _should_mark_played("u1", "u1", "playing", "playing") is False
