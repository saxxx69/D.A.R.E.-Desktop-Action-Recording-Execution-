"""Cross-platform active-window title detection.

Resolution order:

* Windows / macOS  → ``pygetwindow.getActiveWindow()``
* Linux X11        → ``xdotool getactivewindow getwindowname``
* Anything else    → ``"unknown"``

The function never raises: any failure is swallowed and reported as
``"unknown"`` so the recorder pipeline can keep going even on exotic setups.
"""

from __future__ import annotations

import subprocess

from dare.utils import platform as plat


def _get_via_pygetwindow() -> str:
    """Windows / macOS path. Returns ``"unknown"`` on any failure."""
    try:
        import pygetwindow  # type: ignore[import-not-found]
    except ImportError:
        return "unknown"
    try:
        win = pygetwindow.getActiveWindow()  # type: ignore[attr-defined]
    except Exception:
        return "unknown"
    if win is None:
        return "unknown"
    title = getattr(win, "title", "") or ""
    return title.strip() or "unknown"


def _get_via_xdotool() -> str:
    """Linux X11 path via the ``xdotool`` external binary."""
    if not plat.has_xdotool():
        return "unknown"
    try:
        out = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "unknown"
    title = (out.stdout or "").strip()
    return title or "unknown"


def get_active_window() -> str:
    """Return the title of the currently focused window, or ``"unknown"``.

    Never raises. Safe to call in a hot loop (the cost is one subprocess on
    Linux X11 and a tiny WinAPI call on Windows).
    """
    os_name = plat.get_os()
    if os_name in ("windows", "macos"):
        return _get_via_pygetwindow()
    if os_name == "linux" and plat.get_linux_session() == "x11":
        return _get_via_xdotool()
    return "unknown"
