"""Tests for ScreenshotManager.

The full mss path needs a display, which we don't have on CI. We test
two things instead:

1. The metadata + filename + hashing logic via a subclass that overrides
   ``_grab_raw`` to return a synthetic frame.
2. The stdlib PNG fallback (used when Pillow is missing) still produces a
   valid PNG signature.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dare.recorder.screenshot import ScreenshotManager, _stdlib_png_encode


class FakeShots(ScreenshotManager):
    """Override the OS-level grab so tests don't need a display."""

    def __init__(self, screenshots_dir: Path, frame: bytes, w: int, h: int):
        super().__init__(screenshots_dir, monitor_index=0)
        self._frame = frame
        self._w = w
        self._h = h

    def _grab_raw(self):  # type: ignore[override]
        return self._frame, self._w, self._h


@pytest.fixture
def shots_dir(tmp_path: Path) -> Path:
    return tmp_path / "shots"


def test_capture_writes_file_and_metadata(shots_dir: Path) -> None:
    # 4×3 solid red RGB frame
    frame = bytes([255, 0, 0]) * (4 * 3)
    sm = FakeShots(shots_dir, frame, 4, 3)
    meta = sm.capture(cursor=(10, 20), active_window="test_window")

    assert meta.filename == "s_000000.png"
    assert (shots_dir / meta.filename).is_file()
    assert meta.resolution == (4, 3)
    assert meta.cursor == (10, 20)
    assert meta.active_window == "test_window"
    assert len(meta.screen_hash) == 64  # sha256 hex


def test_capture_increments_counter(shots_dir: Path) -> None:
    frame = bytes([0, 255, 0]) * (2 * 2)
    sm = FakeShots(shots_dir, frame, 2, 2)
    a = sm.capture(cursor=(0, 0), active_window="a")
    b = sm.capture(cursor=(0, 0), active_window="b")
    c = sm.capture(cursor=(0, 0), active_window="c")
    assert a.filename == "s_000000.png"
    assert b.filename == "s_000001.png"
    assert c.filename == "s_000002.png"


def test_identical_frames_have_identical_hash(shots_dir: Path) -> None:
    frame = bytes([128, 128, 128]) * (5 * 5)
    sm = FakeShots(shots_dir, frame, 5, 5)
    a = sm.capture(cursor=(0, 0), active_window="x")
    b = sm.capture(cursor=(0, 0), active_window="x")
    assert a.screen_hash == b.screen_hash


def test_different_frames_have_different_hashes(shots_dir: Path) -> None:
    sm_a = FakeShots(shots_dir, bytes([0, 0, 0]) * 4, 2, 2)
    a = sm_a.capture(cursor=(0, 0), active_window="x")
    sm_b = FakeShots(shots_dir, bytes([255, 255, 255]) * 4, 2, 2)
    b = sm_b.capture(cursor=(0, 0), active_window="x")
    assert a.screen_hash != b.screen_hash


def test_metadata_to_dict_is_json_friendly(shots_dir: Path) -> None:
    import json

    frame = bytes([0, 0, 0]) * 4
    sm = FakeShots(shots_dir, frame, 2, 2)
    meta = sm.capture(cursor=(1, 2), active_window="w")
    d = meta.to_dict()
    # round-trip through JSON
    s = json.dumps(d)
    assert json.loads(s) == d
    assert isinstance(d["resolution"], list)
    assert isinstance(d["cursor"], list)


def test_stdlib_png_encoder_produces_valid_signature() -> None:
    rgb = bytes([10, 20, 30]) * (3 * 2)
    png = _stdlib_png_encode(rgb, 3, 2)
    # PNG magic bytes
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    # IHDR chunk type at offset 12
    assert png[12:16] == b"IHDR"
    # IEND chunk somewhere near the end
    assert b"IEND" in png[-12:]


def test_stdlib_png_round_trips_with_pil() -> None:
    """If Pillow is available, decode the stdlib-emitted PNG to verify it
    really is valid."""
    pytest.importorskip("PIL")
    from io import BytesIO

    from PIL import Image

    width, height = 3, 4
    # Build a deterministic RGB buffer
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels.extend([(x * 80) % 256, (y * 60) % 256, ((x + y) * 40) % 256])
    png = _stdlib_png_encode(bytes(pixels), width, height)
    img = Image.open(BytesIO(png))
    img.load()
    assert img.size == (width, height)
    assert img.mode == "RGB"
    # Round-trip: decoded pixels match the input
    assert img.tobytes() == bytes(pixels)
