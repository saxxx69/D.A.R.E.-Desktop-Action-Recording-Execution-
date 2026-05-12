"""Keyboard buffer with quiescence-based flushing.

Keystrokes are buffered together so that a continuous typing burst becomes
a single semantic ``keyboard_input`` event with ``before_screenshot`` and
``after_screenshot`` references. The buffer is flushed when the user has
been silent on the keyboard for ``flush_idle`` seconds (default 1.5 s) —
matching the spec.

The class is thread-safe: ``feed`` may be called from the pynput listener
thread while ``maybe_flush`` is called from the watchdog thread.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class FlushedKeyboardEvent:
    """A flushed keyboard input event, ready to be serialized."""

    type: str = "keyboard_input"
    text: str = ""
    keys: list[str] = field(default_factory=list)
    start_ts: float = 0.0
    end_ts: float = 0.0
    before_screenshot: Optional[str] = None
    after_screenshot: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "text": self.text,
            "keys": list(self.keys),
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "before_screenshot": self.before_screenshot,
            "after_screenshot": self.after_screenshot,
        }


class KeyboardBuffer:
    """Thread-safe buffer that flushes after a quiescence window.

    Args:
        flush_idle: seconds of keyboard silence required before a flush
            (default 1.5).
        on_flush: optional callback invoked synchronously (under the buffer
            lock) when a flush happens. Useful in tests.
    """

    def __init__(
        self,
        flush_idle: float = 1.5,
        on_flush: Optional[Callable[[FlushedKeyboardEvent], None]] = None,
    ) -> None:
        if flush_idle <= 0:
            raise ValueError("flush_idle must be positive")
        self.flush_idle = flush_idle
        self._on_flush = on_flush
        self._lock = threading.Lock()
        self._keys: list[str] = []
        self._chars: list[str] = []   # printable chars only — joined as text
        self._start_ts: Optional[float] = None
        self._last_ts: Optional[float] = None
        self._before_screenshot: Optional[str] = None

    # ----- producer side ----------------------------------------------

    def feed(
        self,
        key_repr: str,
        char: Optional[str],
        timestamp: Optional[float] = None,
        before_screenshot: Optional[str] = None,
    ) -> None:
        """Record a keypress.

        Args:
            key_repr: a stable string for the key — e.g. ``"Key.shift"`` or
                ``"a"`` — so the original sequence can be replayed.
            char: the printable character produced by this keypress, or
                ``None`` for non-printable keys (modifiers, function keys).
            timestamp: optional override (POSIX seconds).
            before_screenshot: filename of the screenshot taken just
                before the burst started. Recorded once per burst — the
                first ``feed`` call sets it.
        """
        ts = time.time() if timestamp is None else timestamp
        with self._lock:
            if self._start_ts is None:
                self._start_ts = ts
                self._before_screenshot = before_screenshot
            self._last_ts = ts
            self._keys.append(key_repr)
            if char is not None:
                self._chars.append(char)

    # ----- consumer side ----------------------------------------------

    def is_empty(self) -> bool:
        with self._lock:
            return self._start_ts is None

    def time_since_last(self, now: Optional[float] = None) -> Optional[float]:
        """Seconds elapsed since the last keypress, or ``None`` if empty."""
        with self._lock:
            if self._last_ts is None:
                return None
            return (time.time() if now is None else now) - self._last_ts

    def maybe_flush(
        self,
        after_screenshot: Optional[str] = None,
        now: Optional[float] = None,
    ) -> Optional[FlushedKeyboardEvent]:
        """Flush the buffer if the quiescence window has elapsed.

        Returns the flushed event, or ``None`` if there is nothing to flush
        or the quiescence window has not elapsed yet.
        """
        cur = time.time() if now is None else now
        with self._lock:
            if self._start_ts is None or self._last_ts is None:
                return None
            if cur - self._last_ts < self.flush_idle:
                return None
            ev = self._build_event(after_screenshot)
            self._reset_locked()
        if self._on_flush:
            self._on_flush(ev)
        return ev

    def force_flush(
        self, after_screenshot: Optional[str] = None
    ) -> Optional[FlushedKeyboardEvent]:
        """Flush regardless of the quiescence window. Used at recorder
        shutdown to drain the final burst.
        """
        with self._lock:
            if self._start_ts is None or self._last_ts is None:
                return None
            ev = self._build_event(after_screenshot)
            self._reset_locked()
        if self._on_flush:
            self._on_flush(ev)
        return ev

    # ----- internals ---------------------------------------------------

    def _build_event(
        self, after_screenshot: Optional[str]
    ) -> FlushedKeyboardEvent:
        # Caller must hold self._lock
        assert self._start_ts is not None and self._last_ts is not None
        return FlushedKeyboardEvent(
            text="".join(self._chars),
            keys=list(self._keys),
            start_ts=self._start_ts,
            end_ts=self._last_ts,
            before_screenshot=self._before_screenshot,
            after_screenshot=after_screenshot,
        )

    def _reset_locked(self) -> None:
        # Caller must hold self._lock
        self._keys.clear()
        self._chars.clear()
        self._start_ts = None
        self._last_ts = None
        self._before_screenshot = None
