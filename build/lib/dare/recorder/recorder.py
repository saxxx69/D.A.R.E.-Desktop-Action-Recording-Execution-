"""Recorder orchestrator.

Wires together the screenshot manager, keyboard buffer, activity monitor and
``pynput`` listeners. The recorder is the single producer of
``raw_events.jsonl`` for a run.

Design notes
------------
* **Test-friendly core**: the recorder exposes ``feed_mouse_click``,
  ``feed_mouse_move`` and ``feed_keyboard`` methods that bypass pynput. Unit
  tests inject events directly through these; the live ``start_listeners``
  path simply forwards real input to the same methods.
* **JSONL, append-only**: each event is one line of JSON, flushed
  immediately. A crash never corrupts more than the in-flight line.
* **Thread-safe writes**: a single lock around every ``writeline``.
* **Deterministic ordering**: events carry their POSIX timestamp; consumers
  reorder by ``ts`` if needed.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from dare.recorder.activity_monitor import ActivityMonitor
from dare.recorder.keyboard_buffer import FlushedKeyboardEvent, KeyboardBuffer
from dare.recorder.screenshot import ScreenshotManager
from dare.recorder.window import get_active_window
from dare.utils import platform as plat
from dare.utils.logging import get_logger


@dataclass
class RecorderConfig:
    """Recorder behaviour knobs."""

    inactivity_seconds: float = 30.0
    double_esc_window: float = 0.5
    keyboard_flush_idle: float = 1.5
    capture_mouse_move: bool = False  # Mouse-move spam is rarely useful.
    monitor_index: int = 0


@dataclass
class _Stats:
    mouse_clicks: int = 0
    mouse_moves: int = 0
    keystrokes: int = 0
    keyboard_events: int = 0
    screenshots: int = 0
    started_at: float = 0.0
    stopped_at: float = 0.0
    stop_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "mouse_clicks": self.mouse_clicks,
            "mouse_moves": self.mouse_moves,
            "keystrokes": self.keystrokes,
            "keyboard_events": self.keyboard_events,
            "screenshots": self.screenshots,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "duration_seconds": max(0.0, self.stopped_at - self.started_at),
            "stop_reason": self.stop_reason,
        }


class Recorder:
    """Capture mouse, keyboard and screenshots into a run's raw folder.

    Lifecycle::

        rec = Recorder(run_dir, config=RecorderConfig())
        rec.start()        # spawns activity watcher; pynput listeners if live
        rec.start_listeners()   # only when running on a real desktop
        rec.wait_until_stopped()
        rec.close()        # finalises stats, releases handles

    For tests / synthetic runs, skip ``start_listeners`` and call the
    ``feed_*`` methods directly.

    Args:
        run_dir: the run folder (``<runs_root>/<run_id>``). The recorder
            writes ``raw/raw_events.jsonl`` and screenshots under
            ``assets/screenshots/``.
        config: optional :class:`RecorderConfig` (defaults are sensible).
        cursor_provider: function returning the current ``(x, y)`` cursor
            position. Defaults to a pynput-backed implementation; tests
            inject a constant.
        active_window_provider: function returning the focused window
            title. Defaults to :func:`get_active_window`; tests inject a
            constant.
        clock: injectable time source. Defaults to ``time.time``.
        logger: optional logger. A run-scoped logger is created by default.
    """

    def __init__(
        self,
        run_dir: Path,
        config: Optional[RecorderConfig] = None,
        cursor_provider: Optional[Callable[[], tuple[int, int]]] = None,
        active_window_provider: Optional[Callable[[], str]] = None,
        clock: Callable[[], float] = time.time,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.config = config or RecorderConfig()
        self._clock = clock
        self.log = logger or get_logger("dare.recorder", run_dir=self.run_dir)

        self.raw_dir = self.run_dir / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.raw_dir / "raw_events.jsonl"
        self.shots = ScreenshotManager(
            self.run_dir / "assets" / "screenshots",
            monitor_index=self.config.monitor_index,
        )
        self._cursor_provider = cursor_provider or _default_cursor_provider
        self._window_provider = active_window_provider or get_active_window

        self._kbd = KeyboardBuffer(flush_idle=self.config.keyboard_flush_idle)
        self._activity = ActivityMonitor(
            on_stop=self._on_activity_stop,
            inactivity_seconds=self.config.inactivity_seconds,
            double_esc_window=self.config.double_esc_window,
            clock=clock,
        )

        self._write_lock = threading.Lock()
        self._fp = None  # type: ignore[assignment]
        self._stopped_event = threading.Event()
        self._stats = _Stats()

        self._kbd_flusher_thread: Optional[threading.Thread] = None
        self._mouse_listener = None
        self._kbd_listener = None
        self._before_screenshot_for_burst: Optional[str] = None

    # ----- lifecycle ----------------------------------------------------

    def preflight(self) -> list[str]:
        """Return a list of human-readable problems that would block a
        successful live recording on this platform. The list is empty when
        the platform is good to go.
        """
        problems: list[str] = []
        if plat.is_wayland():
            problems.append(
                "Wayland session detected. pynput cannot reliably capture "
                "global input under Wayland. Switch to an X11 session."
            )
        if plat.is_headless():
            problems.append(
                "No display server. The recorder needs a desktop. On Linux "
                "VPS use Xvfb: xvfb-run -a python -m dare.server record."
            )
        return problems

    def start(self) -> None:
        """Open the events file, start the activity watcher and the
        keyboard-buffer flusher. Does **not** start pynput listeners — call
        :meth:`start_listeners` for that.
        """
        if self._fp is not None:
            raise RuntimeError("Recorder already started")
        self._stats.started_at = self._clock()
        self._fp = self.events_path.open("a", encoding="utf-8")
        self._activity.start()
        self._kbd_flusher_thread = threading.Thread(
            target=self._kbd_flusher_loop,
            name="dare-kbd-flusher",
            daemon=True,
        )
        self._kbd_flusher_thread.start()
        self.log.info(
            "Recorder started (idle=%.1fs, double-ESC<=%.2fs).",
            self.config.inactivity_seconds,
            self.config.double_esc_window,
        )

    def start_listeners(self) -> None:
        """Attach real ``pynput`` listeners to the OS. Skips silently if
        pynput is not installed (caller should have checked preflight)."""
        try:
            from pynput import keyboard as pyk  # type: ignore[import-not-found]
            from pynput import mouse as pym     # type: ignore[import-not-found]
        except ImportError:
            self.log.error(
                "pynput is not installed. Run: pip install -e .[recorder]"
            )
            return

        def on_click(x: int, y: int, button: Any, pressed: bool) -> None:
            if not pressed:
                return
            try:
                self.feed_mouse_click(int(x), int(y), str(button))
            except Exception as e:  # pragma: no cover - defensive
                self.log.exception("on_click failed: %s", e)

        def on_move(x: int, y: int) -> None:
            if not self.config.capture_mouse_move:
                return
            try:
                self.feed_mouse_move(int(x), int(y))
            except Exception as e:  # pragma: no cover - defensive
                self.log.exception("on_move failed: %s", e)

        def on_press(key: Any) -> None:
            try:
                key_repr, char = _key_to_repr_char(key)
                self.feed_keyboard(key_repr, char)
            except Exception as e:  # pragma: no cover - defensive
                self.log.exception("on_press failed: %s", e)

        self._mouse_listener = pym.Listener(on_click=on_click, on_move=on_move)
        self._kbd_listener = pyk.Listener(on_press=on_press)
        self._mouse_listener.start()
        self._kbd_listener.start()
        self.log.info("pynput listeners attached.")

    def wait_until_stopped(self, timeout: Optional[float] = None) -> bool:
        """Block until the activity monitor stops the recorder.

        Returns ``True`` if it stopped, ``False`` on timeout.
        """
        return self._stopped_event.wait(timeout=timeout)

    def close(self) -> None:
        """Flush remaining state and release all resources. Idempotent."""
        if self._fp is None:
            return
        # Drain final keyboard burst
        ev = self._kbd.force_flush()
        if ev is not None:
            self._write_keyboard_event(ev)
        # Tear down listeners
        if self._mouse_listener is not None:
            try:
                self._mouse_listener.stop()
            except Exception:
                pass
        if self._kbd_listener is not None:
            try:
                self._kbd_listener.stop()
            except Exception:
                pass
        self._activity.stop()
        # Stats
        self._stats.stopped_at = self._clock()
        if not self._stats.stop_reason:
            self._stats.stop_reason = "explicit_close"
        # Close jsonl
        with self._write_lock:
            try:
                self._fp.flush()
                self._fp.close()
            finally:
                self._fp = None
        # Persist stats next to the events file
        (self.raw_dir / "stats.json").write_text(
            json.dumps(self._stats.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        self.log.info(
            "Recorder closed (reason=%s, %d clicks, %d kbd events, %d shots).",
            self._stats.stop_reason,
            self._stats.mouse_clicks,
            self._stats.keyboard_events,
            self._stats.screenshots,
        )

    # ----- event injection (test-friendly + listener forwarding) -------

    def feed_mouse_click(
        self,
        x: int,
        y: int,
        button: str,
        timestamp: Optional[float] = None,
    ) -> dict:
        """Record a mouse click (every click → an immediate screenshot)."""
        ts = self._clock() if timestamp is None else timestamp
        # A click ends any in-flight typing burst — capture the "after"
        # screenshot now and flush.
        self._flush_keyboard_burst(after_ts=ts)
        meta = self._capture_screenshot(cursor=(x, y), ts=ts)
        ev = {
            "type": "mouse_click",
            "x": x,
            "y": y,
            "button": button,
            "timestamp": ts,
            "screenshot": meta.filename,
        }
        self._write_event(ev)
        self._stats.mouse_clicks += 1
        self._activity.mark_activity(ts)
        return ev

    def feed_mouse_move(
        self, x: int, y: int, timestamp: Optional[float] = None
    ) -> Optional[dict]:
        """Record a mouse move (only if ``capture_mouse_move`` is True)."""
        if not self.config.capture_mouse_move:
            return None
        ts = self._clock() if timestamp is None else timestamp
        ev = {"type": "mouse_move", "x": x, "y": y, "timestamp": ts}
        self._write_event(ev)
        self._stats.mouse_moves += 1
        self._activity.mark_activity(ts)
        return ev

    def feed_keyboard(
        self,
        key_repr: str,
        char: Optional[str],
        timestamp: Optional[float] = None,
    ) -> None:
        """Record a single keypress.

        If this is the first keystroke of a burst we capture a "before"
        screenshot. ESC is special-cased: two presses within the configured
        window trigger a hard-stop, but each press still goes through the
        buffer so the resulting event preserves the user's exact keystrokes.
        """
        ts = self._clock() if timestamp is None else timestamp
        # ESC double-tap detection
        if key_repr in ("Key.esc", "Key.escape"):
            self._activity.mark_esc(ts)
            # We do not stop here: mark_esc fires the stop callback when
            # the second press lands, which sets stop_reason and unblocks
            # wait_until_stopped().
            self._stats.keystrokes += 1
            return

        # First keystroke of a burst → capture "before" screenshot
        if self._kbd.is_empty():
            cursor = self._safe_cursor()
            meta = self._capture_screenshot(cursor=cursor, ts=ts)
            self._before_screenshot_for_burst = meta.filename
        self._kbd.feed(
            key_repr=key_repr,
            char=char,
            timestamp=ts,
            before_screenshot=self._before_screenshot_for_burst,
        )
        self._stats.keystrokes += 1
        self._activity.mark_activity(ts)

    # ----- internals ---------------------------------------------------

    def _flush_keyboard_burst(self, after_ts: float) -> None:
        """If a keyboard burst is in flight, capture the after screenshot
        and flush it as a single keyboard_input event."""
        if self._kbd.is_empty():
            return
        cursor = self._safe_cursor()
        meta = self._capture_screenshot(cursor=cursor, ts=after_ts)
        ev = self._kbd.force_flush(after_screenshot=meta.filename)
        if ev is not None:
            self._write_keyboard_event(ev)
        self._before_screenshot_for_burst = None

    def _kbd_flusher_loop(self) -> None:
        """Daemon that flushes the keyboard buffer after a quiescence
        window."""
        interval = max(0.1, self.config.keyboard_flush_idle / 3.0)
        while not self._stopped_event.is_set():
            time.sleep(interval)
            if self._kbd.is_empty():
                continue
            since = self._kbd.time_since_last()
            if since is None or since < self.config.keyboard_flush_idle:
                continue
            cursor = self._safe_cursor()
            try:
                meta = self._capture_screenshot(cursor=cursor)
            except Exception as e:  # screenshot can fail in headless tests
                self.log.warning("screenshot during flush failed: %s", e)
                meta = None
            ev = self._kbd.maybe_flush(
                after_screenshot=meta.filename if meta else None
            )
            if ev is not None:
                self._write_keyboard_event(ev)
            self._before_screenshot_for_burst = None

    def _on_activity_stop(self, reason: str) -> None:
        """Callback from ActivityMonitor when a stop condition fires."""
        self._stats.stop_reason = reason
        self._stopped_event.set()
        self.log.info("Stop condition fired: %s", reason)

    def _capture_screenshot(
        self,
        cursor: tuple[int, int],
        ts: Optional[float] = None,
    ):
        """Take a screenshot and write its metadata as a side event."""
        active = self._safe_window()
        meta = self.shots.capture(
            cursor=cursor, active_window=active, timestamp=ts
        )
        # Persist the metadata as a side event so consumers can rebuild a
        # screenshot index without re-scanning the directory.
        self._write_event({"type": "screenshot", **meta.to_dict()})
        self._stats.screenshots += 1
        return meta

    def _safe_cursor(self) -> tuple[int, int]:
        try:
            return self._cursor_provider()
        except Exception:
            return (0, 0)

    def _safe_window(self) -> str:
        try:
            return self._window_provider()
        except Exception:
            return "unknown"

    def _write_event(self, ev: dict) -> None:
        if self._fp is None:
            raise RuntimeError("Recorder is not started")
        line = json.dumps(ev, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            self._fp.write(line + "\n")
            self._fp.flush()

    def _write_keyboard_event(self, ev: FlushedKeyboardEvent) -> None:
        self._write_event(ev.to_dict())
        self._stats.keyboard_events += 1

    @property
    def stats(self) -> dict:
        return self._stats.to_dict()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _default_cursor_provider() -> tuple[int, int]:
    """Best-effort cursor location lookup using pynput.

    Returns ``(0, 0)`` if pynput is unavailable or fails.
    """
    try:
        from pynput.mouse import Controller  # type: ignore[import-not-found]
    except ImportError:
        return (0, 0)
    try:
        c = Controller()
        x, y = c.position
        return (int(x), int(y))
    except Exception:
        return (0, 0)


def _key_to_repr_char(key: Any) -> tuple[str, Optional[str]]:
    """Convert a pynput key into ``(stable_repr, printable_char_or_None)``.

    pynput delivers two flavours:
      * ``KeyCode(char='a')`` for normal characters
      * ``Key.shift`` (or ``Key.f5``, ``Key.esc``, …) for special keys
    """
    char: Optional[str] = None
    char_attr = getattr(key, "char", None)
    if isinstance(char_attr, str) and len(char_attr) == 1:
        char = char_attr
    return (str(key), char)
