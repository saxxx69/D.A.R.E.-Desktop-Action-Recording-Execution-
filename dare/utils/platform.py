"""Platform detection and capability probing.

Cross-platform helpers used across all D.A.R.E. modules. Detects:

* Operating system (windows / linux / macos)
* Linux session type (X11 / Wayland) — critical because Wayland breaks
  ``pynput`` / ``pyautogui`` global hooks by design.
* Optional capability presence (``tesseract``, ``ffmpeg``, ``xdotool``).
* Whether the environment is headless (no display server).

All functions are read-only and side-effect free.
"""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import asdict, dataclass
from typing import Literal

OSName = Literal["windows", "linux", "macos", "unknown"]
SessionType = Literal["x11", "wayland", "headless", "n/a"]


def get_os() -> OSName:
    """Return the normalized OS name."""
    sysname = platform.system().lower()
    if sysname == "windows":
        return "windows"
    if sysname == "linux":
        return "linux"
    if sysname == "darwin":
        return "macos"
    return "unknown"


def get_linux_session() -> SessionType:
    """Detect Linux display session: ``x11``, ``wayland``, ``headless``, or
    ``n/a`` (not Linux).
    """
    if get_os() != "linux":
        return "n/a"
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return "headless"
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session in ("wayland", "x11"):
        return session  # type: ignore[return-value]
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "headless"


def is_headless() -> bool:
    """Return True if no display is available (typical on a VPS).

    On Windows / macOS the concept does not have a clean detection, so we
    conservatively return False.
    """
    if get_os() == "linux":
        return get_linux_session() == "headless"
    return False


def is_wayland() -> bool:
    """Return True if running under Wayland (input automation is severely
    limited)."""
    return get_linux_session() == "wayland"


def has_command(cmd: str) -> bool:
    """Return True if a command is available on PATH."""
    return shutil.which(cmd) is not None


def has_tesseract() -> bool:
    """Tesseract OCR engine availability."""
    return has_command("tesseract")


def has_ffmpeg() -> bool:
    """FFmpeg availability for high-quality video encoding."""
    return has_command("ffmpeg")


def has_xdotool() -> bool:
    """``xdotool`` availability — Linux X11 active-window detection."""
    return has_command("xdotool")


@dataclass(frozen=True)
class PlatformReport:
    """Snapshot of the current platform's capabilities."""

    os: OSName
    python_version: str
    arch: str
    session: SessionType
    headless: bool
    tesseract: bool
    ffmpeg: bool
    xdotool: bool

    def to_dict(self) -> dict:
        return asdict(self)


def report() -> PlatformReport:
    """Build a fresh capability snapshot."""
    return PlatformReport(
        os=get_os(),
        python_version=platform.python_version(),
        arch=platform.machine(),
        session=get_linux_session(),
        headless=is_headless(),
        tesseract=has_tesseract(),
        ffmpeg=has_ffmpeg(),
        xdotool=has_xdotool(),
    )


def warnings_for_current_platform() -> list[str]:
    """Return human-readable warnings about likely automation problems.

    The list is empty when the platform looks healthy.
    """
    msgs: list[str] = []
    rep = report()

    if rep.os == "linux" and rep.session == "wayland":
        msgs.append(
            "Wayland detected: pynput/pyautogui input capture and synthesis are "
            "severely limited. Switch to an X11 session for D.A.R.E. recording "
            "and execution."
        )
    if rep.headless:
        msgs.append(
            "No display detected (headless). Recorder and Executor require a "
            "display server. On Linux VPS use Xvfb, e.g. "
            "`xvfb-run -a python -m dare.server <command>`."
        )
    if not rep.tesseract:
        msgs.append(
            "Tesseract OCR not found on PATH. OCR features will be disabled. "
            "Install: https://tesseract-ocr.github.io/tessdoc/Installation.html"
        )
    if not rep.ffmpeg:
        msgs.append(
            "FFmpeg not found on PATH. Validator preview video will fall back "
            "to OpenCV's built-in encoder (lower quality, larger files)."
        )
    if rep.os == "linux" and rep.session == "x11" and not rep.xdotool:
        msgs.append(
            "xdotool not found. Active-window detection on Linux X11 will be "
            "limited. Install: `sudo apt install xdotool`."
        )
    return msgs
