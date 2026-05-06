"""Recorder — Phase 2.

Captures mouse, keyboard and screenshot events, writing them to the run's
``raw/`` directory. Auto-stops after 30 s of inactivity or a double-ESC hotkey.

Public surface:

* :class:`Recorder`          orchestrator — start / stop / event sink
* :class:`ScreenshotManager` mss-backed multi-monitor screenshot store
* :class:`ActivityMonitor`   idle watchdog with double-ESC hard-stop
* :class:`KeyboardBuffer`    1.5 s flush buffer for keystrokes
* :func:`get_active_window`  cross-platform active-window title

Lower-level components are independently testable; the orchestrator wires
them together.
"""

from dare.recorder.activity_monitor import ActivityMonitor
from dare.recorder.keyboard_buffer import KeyboardBuffer
from dare.recorder.recorder import Recorder, RecorderConfig
from dare.recorder.screenshot import ScreenshotManager, ScreenshotMetadata
from dare.recorder.window import get_active_window

__all__ = [
    "ActivityMonitor",
    "KeyboardBuffer",
    "Recorder",
    "RecorderConfig",
    "ScreenshotManager",
    "ScreenshotMetadata",
    "get_active_window",
]
