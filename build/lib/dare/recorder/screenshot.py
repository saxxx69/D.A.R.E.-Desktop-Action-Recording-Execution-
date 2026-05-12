"""Screenshot capture with rich metadata.

Wraps :mod:`mss` (preferred — fastest cross-platform path) and falls back to
:mod:`PIL.ImageGrab` if mss is missing. Each captured frame is saved as a PNG
under ``<run>/assets/screenshots/`` and is paired with a JSON-friendly
metadata record:

* ``filename``       — relative filename (``s_000123.png``)
* ``timestamp``      — POSIX seconds at capture time (``time.time()``)
* ``resolution``     — ``[width, height]`` of the captured monitor
* ``cursor``         — ``[x, y]`` global cursor position (recorder supplies)
* ``active_window``  — focused window title at capture time
* ``screen_hash``    — SHA-256 of the raw pixel buffer (for dedup)
* ``monitor_index``  — which monitor was captured (0 = full virtual screen,
                       1+ = individual monitor as enumerated by mss)

The class is **thread-safe** for concurrent captures: the underlying mss
instance is rebuilt per-thread because ``mss.mss()`` is not safe to share.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ScreenshotMetadata:
    """Metadata for a single screenshot."""

    filename: str
    timestamp: float
    resolution: tuple[int, int]
    cursor: tuple[int, int]
    active_window: str
    screen_hash: str
    monitor_index: int

    def to_dict(self) -> dict:
        # tuples become lists in JSON anyway; do it explicitly for clarity
        d = asdict(self)
        d["resolution"] = list(self.resolution)
        d["cursor"] = list(self.cursor)
        return d


class ScreenshotManager:
    """Capture and persist screenshots into a run's assets folder.

    The ``screenshots_dir`` is created if it does not exist. Filenames are
    zero-padded sequential — ``s_000000.png``, ``s_000001.png``, … — so the
    natural sort matches capture order.

    Args:
        screenshots_dir: target directory (typically
            ``<run>/assets/screenshots/``).
        monitor_index: which monitor mss should grab. ``0`` is the union of
            all monitors (the "virtual screen"); ``1+`` is each physical
            monitor. Default ``0`` matches multi-monitor recording.
    """

    def __init__(
        self,
        screenshots_dir: Path,
        monitor_index: int = 0,
    ) -> None:
        self.dir = Path(screenshots_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.monitor_index = monitor_index
        self._counter = 0
        self._lock = threading.Lock()
        # Per-thread mss instance — mss.mss() is not safe to share between
        # threads. We use a thread-local store keyed off ident.
        self._tls = threading.local()

    # ----- public ------------------------------------------------------

    def capture(
        self,
        cursor: tuple[int, int],
        active_window: str,
        timestamp: Optional[float] = None,
    ) -> ScreenshotMetadata:
        """Capture a screenshot and return its metadata.

        Args:
            cursor: global cursor position at capture time. The recorder
                fills this in from its own listener — the mss API does not
                report cursor position.
            active_window: focused window title at capture time.
            timestamp: optional override (POSIX seconds). Defaults to now.

        Raises:
            RuntimeError: if neither mss nor PIL is available.
        """
        ts = time.time() if timestamp is None else timestamp
        idx = self._next_index()
        filename = f"s_{idx:06d}.png"
        out_path = self.dir / filename

        rgb_bytes, width, height = self._grab_raw()
        out_path.write_bytes(self._encode_png(rgb_bytes, width, height))
        screen_hash = hashlib.sha256(rgb_bytes).hexdigest()

        return ScreenshotMetadata(
            filename=filename,
            timestamp=ts,
            resolution=(width, height),
            cursor=tuple(cursor),
            active_window=active_window,
            screen_hash=screen_hash,
            monitor_index=self.monitor_index,
        )

    # ----- internals ---------------------------------------------------

    def _next_index(self) -> int:
        with self._lock:
            i = self._counter
            self._counter += 1
            return i

    def _grab_raw(self) -> tuple[bytes, int, int]:
        """Return ``(rgb_bytes, width, height)`` for the configured monitor.

        Tries mss first (fastest, multi-monitor aware). Falls back to PIL.
        """
        # --- mss path ---
        try:
            sct = self._mss_for_thread()
        except ImportError:
            sct = None

        if sct is not None:
            monitors = sct.monitors  # type: ignore[union-attr]
            idx = self.monitor_index
            if idx < 0 or idx >= len(monitors):
                idx = 0
            shot = sct.grab(monitors[idx])  # type: ignore[union-attr]
            # mss returns BGRA; we want RGB for stable hashing across encoders
            raw = bytes(shot.raw)
            rgb = self._bgra_to_rgb(raw)
            return rgb, shot.width, shot.height

        # --- PIL fallback ---
        try:
            from PIL import ImageGrab  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "No screenshot backend available. Install mss "
                "(pip install mss) or Pillow (pip install Pillow)."
            ) from e

        img = ImageGrab.grab()
        if img.mode != "RGB":
            img = img.convert("RGB")
        return img.tobytes(), img.width, img.height

    def _mss_for_thread(self):
        """Return a thread-local mss instance, building one if needed."""
        sct = getattr(self._tls, "mss", None)
        if sct is not None:
            return sct
        try:
            import mss  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError("mss not installed") from e
        sct = mss.mss()
        self._tls.mss = sct
        return sct

    @staticmethod
    def _bgra_to_rgb(bgra: bytes) -> bytes:
        # mss yields B,G,R,A in that byte order; strip A and swap to R,G,B.
        # Using a memoryview avoids an intermediate copy.
        mv = memoryview(bgra)
        out = bytearray(len(bgra) // 4 * 3)
        for i in range(0, len(bgra), 4):
            j = (i // 4) * 3
            out[j] = mv[i + 2]      # R
            out[j + 1] = mv[i + 1]  # G
            out[j + 2] = mv[i]      # B
        return bytes(out)

    @staticmethod
    def _encode_png(rgb: bytes, width: int, height: int) -> bytes:
        """Encode raw RGB bytes as PNG. Uses Pillow if available; otherwise
        emits a minimal uncompressed-ish PNG via the stdlib ``zlib`` module.
        """
        try:
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError:
            return _stdlib_png_encode(rgb, width, height)
        img = Image.frombytes("RGB", (width, height), rgb)
        from io import BytesIO

        buf = BytesIO()
        img.save(buf, format="PNG", optimize=False)
        return buf.getvalue()


def _stdlib_png_encode(rgb: bytes, width: int, height: int) -> bytes:
    """Minimal stdlib-only PNG encoder (used when Pillow is missing).

    Produces a valid 8-bit-RGB PNG with a single IDAT chunk. Compression is
    light (default zlib level 6) which is fine for diagnostic frames; the
    fast path is the Pillow encoder above.
    """
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)  # filter type "None"
        raw.extend(rgb[y * stride : (y + 1) * stride])
    idat = zlib.compress(bytes(raw), 6)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
