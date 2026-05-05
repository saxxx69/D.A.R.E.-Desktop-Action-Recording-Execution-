"""Tests for KeyboardBuffer."""

from __future__ import annotations

from dare.recorder.keyboard_buffer import KeyboardBuffer


def test_starts_empty() -> None:
    buf = KeyboardBuffer()
    assert buf.is_empty()
    assert buf.maybe_flush(now=100.0) is None
    assert buf.force_flush() is None


def test_feeds_collect_chars() -> None:
    buf = KeyboardBuffer(flush_idle=1.5)
    buf.feed("a", "a", timestamp=1.0, before_screenshot="before.png")
    buf.feed("b", "b", timestamp=1.1)
    buf.feed("Key.shift", None, timestamp=1.2)
    buf.feed("c", "c", timestamp=1.3)
    ev = buf.force_flush(after_screenshot="after.png")
    assert ev is not None
    assert ev.text == "abc"
    assert ev.keys == ["a", "b", "Key.shift", "c"]
    assert ev.start_ts == 1.0
    assert ev.end_ts == 1.3
    assert ev.before_screenshot == "before.png"
    assert ev.after_screenshot == "after.png"


def test_no_flush_inside_quiescence_window() -> None:
    buf = KeyboardBuffer(flush_idle=1.5)
    buf.feed("x", "x", timestamp=10.0)
    # Only 1.0s passed → no flush
    assert buf.maybe_flush(now=11.0) is None
    assert not buf.is_empty()


def test_flush_after_quiescence_window() -> None:
    buf = KeyboardBuffer(flush_idle=1.5)
    buf.feed("x", "x", timestamp=10.0)
    ev = buf.maybe_flush(now=11.6, after_screenshot="after.png")
    assert ev is not None
    assert ev.text == "x"
    assert ev.after_screenshot == "after.png"
    # Buffer is reset
    assert buf.is_empty()


def test_buffer_resets_after_flush() -> None:
    buf = KeyboardBuffer(flush_idle=0.5)
    buf.feed("1", "1", timestamp=0.0)
    buf.maybe_flush(now=1.0)
    buf.feed("2", "2", timestamp=2.0, before_screenshot="b2.png")
    ev = buf.maybe_flush(now=2.6, after_screenshot="a2.png")
    assert ev is not None
    assert ev.text == "2"
    assert ev.before_screenshot == "b2.png"
    assert ev.after_screenshot == "a2.png"


def test_callback_invoked_on_flush() -> None:
    seen: list = []
    buf = KeyboardBuffer(flush_idle=0.5, on_flush=seen.append)
    buf.feed("z", "z", timestamp=0.0)
    buf.maybe_flush(now=1.0)
    assert len(seen) == 1
    assert seen[0].text == "z"


def test_time_since_last_tracks_correctly() -> None:
    buf = KeyboardBuffer()
    assert buf.time_since_last(now=5.0) is None
    buf.feed("a", "a", timestamp=5.0)
    # 1.5s after last feed
    assert buf.time_since_last(now=6.5) == 1.5


def test_invalid_flush_idle_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        KeyboardBuffer(flush_idle=0)
    with pytest.raises(ValueError):
        KeyboardBuffer(flush_idle=-1)
