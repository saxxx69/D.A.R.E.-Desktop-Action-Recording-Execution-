"""Idle watchdog for the recorder.

Two stop conditions are supported:

* **Inactivity timeout** — no mouse or keyboard activity for
  ``inactivity_seconds`` seconds (default 30).
* **Double-ESC hard-stop** — two ESC presses within
  ``double_esc_window`` seconds (default 0.5).

The monitor runs in its own daemon thread and invokes the supplied
``on_stop`` callback exactly once, with the reason as a string.

The class is purely time-driven: the recorder updates the monitor with
``mark_activity()`` on every input event, and ``mark_esc()`` whenever the
ESC key is pressed. The monitor itself does not hook input — that's the
recorder's job. This separation keeps the monitor trivial to unit-test
without spawning real listeners.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional


class ActivityMonitor:
    """Watchdog with idle-timeout and double-ESC hard-stop semantics.

    Args:
        on_stop: callback invoked once when a stop condition triggers.
            Receives the reason string (``"idle_timeout"`` or
            ``"double_esc"``).
        inactivity_seconds: idle threshold. Default 30.
        double_esc_window: max gap between two ESC presses to count as a
            hard-stop. Default 0.5.
        check_interval: how often the watcher loop wakes up. Default 1.0.
        clock: injectable time source for tests. Defaults to ``time.time``.
    """

    def __init__(
        self,
        on_stop: Callable[[str], None],
        inactivity_seconds: float = 30.0,
        double_esc_window: float = 0.5,
        check_interval: float = 1.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if inactivity_seconds <= 0:
            raise ValueError("inactivity_seconds must be positive")
        if double_esc_window <= 0:
            raise ValueError("double_esc_window must be positive")
        if check_interval <= 0:
            raise ValueError("check_interval must be positive")

        self._on_stop = on_stop
        self._inactivity = inactivity_seconds
        self._double_esc_window = double_esc_window
        self._check_interval = check_interval
        self._clock = clock

        self._lock = threading.Lock()
        self._last_activity = clock()
        self._last_esc: Optional[float] = None
        self._stopped = False
        self._stop_reason: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._wake = threading.Event()

    # ----- public ------------------------------------------------------

    def start(self) -> None:
        """Spawn the watcher daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._wake.clear()
        with self._lock:
            self._last_activity = self._clock()
            self._stopped = False
            self._stop_reason = None
        t = threading.Thread(
            target=self._loop, name="dare-activity-monitor", daemon=True
        )
        self._thread = t
        t.start()

    def stop(self) -> None:
        """Stop the watcher (does not invoke on_stop). Idempotent."""
        with self._lock:
            self._stopped = True
        self._wake.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=self._check_interval + 0.5)

    def mark_activity(self, now: Optional[float] = None) -> None:
        """Reset the idle counter. Called for every input event."""
        ts = self._clock() if now is None else now
        with self._lock:
            self._last_activity = ts

    def mark_esc(self, now: Optional[float] = None) -> bool:
        """Record an ESC press. Returns ``True`` if this completes a
        double-ESC and a hard-stop is triggered.

        The activity counter is also reset (ESC is still input).
        """
        ts = self._clock() if now is None else now
        triggered_stop = False
        with self._lock:
            self._last_activity = ts
            if (
                self._last_esc is not None
                and ts - self._last_esc <= self._double_esc_window
            ):
                triggered_stop = True
                self._last_esc = None  # consume both presses
            else:
                self._last_esc = ts
        if triggered_stop:
            self._fire("double_esc")
        return triggered_stop

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    @property
    def stop_reason(self) -> Optional[str]:
        with self._lock:
            return self._stop_reason

    def seconds_idle(self, now: Optional[float] = None) -> float:
        """Seconds since the last activity mark."""
        ts = self._clock() if now is None else now
        with self._lock:
            return ts - self._last_activity

    # ----- internal ----------------------------------------------------

    def _loop(self) -> None:
        while True:
            self._wake.wait(self._check_interval)
            self._wake.clear()
            with self._lock:
                if self._stopped:
                    return
                idle = self._clock() - self._last_activity
            if idle >= self._inactivity:
                self._fire("idle_timeout")
                return

    def _fire(self, reason: str) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            self._stop_reason = reason
        try:
            self._on_stop(reason)
        except Exception:
            # The monitor must never propagate user-callback errors to the
            # internal thread. Logging is the recorder's responsibility.
            pass
