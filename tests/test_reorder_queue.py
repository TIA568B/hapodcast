"""Tests for the live-queue reorder planning (_plan_reorder).

Sonos has no move-queue-item action, so reordering clears and rebuilds the
queue in the requested order. _plan_reorder is the pure core: given the live
queue, the requested order, and the currently-playing id, it returns the order
to rebuild and the position to resume playback at.
"""

from custom_components.daily_podcasts import _plan_reorder


def test_simple_reorder_keeps_current_track_as_start():
    live = ["a", "b", "c"]
    # Move 'a' (currently playing) to the middle.
    order, start = _plan_reorder(live, ["b", "a", "c"], current_id="a")
    assert order == ["b", "a", "c"]
    assert start == 1  # resume on 'a' at its new index


def test_start_zero_when_nothing_playing():
    live = ["a", "b", "c"]
    order, start = _plan_reorder(live, ["c", "b", "a"], current_id=None)
    assert order == ["c", "b", "a"]
    assert start == 0


def test_start_zero_when_current_not_in_queue():
    live = ["a", "b"]
    order, start = _plan_reorder(live, ["b", "a"], current_id="gone")
    assert order == ["b", "a"]
    assert start == 0


def test_unknown_requested_ids_are_dropped():
    live = ["a", "b"]
    # Request references 'x' which isn't in the live queue -> ignored.
    order, _start = _plan_reorder(live, ["x", "b", "a"], current_id="a")
    assert order == ["b", "a"]


def test_omitted_live_ids_are_appended_not_lost():
    live = ["a", "b", "c"]
    # Request only mentions two of the three -> the missing one is appended.
    order, _start = _plan_reorder(live, ["c", "a"], current_id="a")
    assert set(order) == {"a", "b", "c"}
    assert order[:2] == ["c", "a"]
    assert "b" in order


def test_duplicate_requested_ids_collapse():
    live = ["a", "b"]
    order, _start = _plan_reorder(live, ["a", "a", "b"], current_id="a")
    assert order == ["a", "b"]


def test_identity_order_unchanged():
    live = ["a", "b", "c"]
    order, start = _plan_reorder(live, ["a", "b", "c"], current_id="b")
    assert order == live
    assert start == 1
