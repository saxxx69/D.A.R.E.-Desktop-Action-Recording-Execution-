"""Multi-tier video preview renderer.

Tier order (best → fallback):

1. **FFmpeg** via :mod:`ffmpeg-python` — preferred. Encodes H.264 with
   widely compatible defaults; overlays are drawn with Pillow before
   piping frames into FFmpeg.
2. **OpenCV VideoWriter** — built-in, codec ``mp4v``. Lower quality, but
   requires zero system tooling.
3. **Placeholder file** — when nothing else is available we still create
   the output path with a clear marker so the pipeline never silently
   omits an artifact.

The renderer pulls the simulation report and overlays per-frame info:
ACTION, TARGET, EXPECTED / OBSERVED, MATCH, plus a target bounding box
when present.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from dare.utils import platform as plat
from dare.utils.logging import get_logger


class VideoBackend(str, Enum):
    FFMPEG = "ffmpeg"
    OPENCV = "opencv"
    PLACEHOLDER = "placeholder"


@dataclass
class RenderResult:
    backend: VideoBackend
    path: Path
    duration_seconds: float
    fps: int
    frame_count: int
    width: int
    height: int

    def to_dict(self) -> dict:
        return {
            "backend": self.backend.value,
            "path": str(self.path),
            "duration_seconds": round(self.duration_seconds, 3),
            "fps": self.fps,
            "frame_count": self.frame_count,
            "resolution": [self.width, self.height],
        }


# ---------------------------------------------------------------------------


class VideoRenderer:
    """Render a simulation report into ``preview.mp4``.

    Args:
        screenshots_dir: directory containing the recorded frames.
        out_path:        target ``.mp4`` path.
        fps:             default 4 (each step shows for 250 ms ×
                         ``frames_per_step``).
        frames_per_step: how many video frames each simulation step
                         occupies. Default 4 → 1 second per step at fps=4.
    """

    def __init__(
        self,
        screenshots_dir: Path,
        out_path: Path,
        fps: int = 4,
        frames_per_step: int = 4,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.screenshots_dir = Path(screenshots_dir)
        self.out_path = Path(out_path)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.fps = max(1, int(fps))
        self.frames_per_step = max(1, int(frames_per_step))
        self.log = logger or get_logger("dare.validator.video")

    # ----- public ------------------------------------------------------

    def render(self, simulation: dict) -> RenderResult:
        """Render ``simulation`` (a parsed simulation.json dict) to mp4."""
        frames = self._compose_frames(simulation)
        if not frames:
            return self._write_placeholder(reason="no frames produced")

        width, height = frames[0].size
        backend = self._pick_backend()

        if backend is VideoBackend.FFMPEG:
            try:
                self._write_ffmpeg(frames, width, height)
                return RenderResult(
                    backend=backend, path=self.out_path,
                    duration_seconds=len(frames) / self.fps,
                    fps=self.fps, frame_count=len(frames),
                    width=width, height=height,
                )
            except Exception as e:
                self.log.warning("FFmpeg render failed (%s); falling back.", e)
                backend = VideoBackend.OPENCV

        if backend is VideoBackend.OPENCV:
            try:
                self._write_opencv(frames, width, height)
                return RenderResult(
                    backend=backend, path=self.out_path,
                    duration_seconds=len(frames) / self.fps,
                    fps=self.fps, frame_count=len(frames),
                    width=width, height=height,
                )
            except Exception as e:
                self.log.warning("OpenCV render failed (%s); falling back to placeholder.", e)

        return self._write_placeholder(
            reason="no encoder available",
            width=width, height=height, frame_count=len(frames),
        )

    # ----- backend selection ------------------------------------------

    def _pick_backend(self) -> VideoBackend:
        if plat.has_ffmpeg() and self._has_module("ffmpeg"):
            return VideoBackend.FFMPEG
        if self._has_module("cv2"):
            return VideoBackend.OPENCV
        return VideoBackend.PLACEHOLDER

    @staticmethod
    def _has_module(name: str) -> bool:
        try:
            __import__(name)
            return True
        except ImportError:
            return False

    # ----- frame composition ------------------------------------------

    def _compose_frames(self, simulation: dict):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            self.log.warning("Pillow not installed; cannot compose frames.")
            return []

        frames = []
        for run in simulation.get("runs", []):
            label = run.get("label", "?")
            for step in run.get("steps", []):
                base = self._load_frame_image(step.get("frame_before"))
                if base is None:
                    base = Image.new("RGB", (1280, 720), color="#101418")
                # Even out resolution across the video
                if frames and base.size != frames[0].size:
                    base = base.resize(frames[0].size, Image.LANCZOS)
                composed = self._overlay(base, step, label)
                # Repeat the same composed frame `frames_per_step` times so
                # the viewer has time to read it.
                for _ in range(self.frames_per_step):
                    frames.append(composed)
        return frames

    def _load_frame_image(self, fname: Optional[str]):
        if not fname:
            return None
        path = self.screenshots_dir / fname
        if not path.is_file():
            return None
        try:
            from PIL import Image
            return Image.open(path).convert("RGB")
        except Exception as e:
            self.log.warning("Cannot open %s: %s", path, e)
            return None

    def _overlay(self, base, step: dict, run_label: str):
        from PIL import Image, ImageDraw, ImageFont

        img = base.copy()
        draw = ImageDraw.Draw(img, "RGBA")
        # Bounding box
        bbox = step.get("target_bbox")
        if bbox and len(bbox) == 4:
            x, y, w, h = bbox
            color = (60, 220, 80, 255) if step.get("effect_match") else (240, 60, 60, 255)
            draw.rectangle([x, y, x + w, y + h], outline=color, width=4)

        # Translucent header bar
        bar_h = 110
        draw.rectangle([0, 0, img.width, bar_h], fill=(0, 0, 0, 180))
        font = self._best_font(20)
        small = self._best_font(16)

        match_str = "✔ MATCH" if step.get("effect_match") else "✘ MISMATCH"
        match_color = (90, 230, 110) if step.get("effect_match") else (240, 90, 90)
        lines = [
            (f"RUN: {run_label}", (220, 220, 230), small),
            (
                f"ACTION: {step.get('action_id')}  "
                f"{step.get('action','').upper()}  →  "
                f"{step.get('target_hint','?')}",
                (255, 255, 255), font,
            ),
            (
                f"EXPECTED: {step.get('expected_effect','none')}    "
                f"OBSERVED: {','.join(step.get('observed_effects', []) or ['(none)'])}",
                (200, 220, 240), small,
            ),
            (
                f"{match_str}    conf={step.get('effect_confidence', 0):.2f}",
                match_color, small,
            ),
        ]
        y = 10
        for text, color, fnt in lines:
            draw.text((14, y), text, fill=color, font=fnt)
            y += int(fnt.size * 1.4) + 2

        # Fail badge (bottom-right)
        if step.get("status") == "fail":
            self._fail_badge(draw, img.size, step.get("fail_reason", "fail"))
        return img

    @staticmethod
    def _best_font(size: int):
        from PIL import ImageFont
        # Try a few common system fonts; fall back to default.
        for name in ("DejaVuSans-Bold.ttf", "Arial.ttf", "Helvetica.ttc"):
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
        font = ImageFont.load_default()
        # Make sure ``font.size`` is set for our layout math
        if not hasattr(font, "size"):
            font.size = size
        return font

    @staticmethod
    def _fail_badge(draw, size, reason: str) -> None:
        w, h = size
        pad = 12
        text = f"FAIL — {reason}"
        from PIL import ImageFont
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
        except Exception:
            font = ImageFont.load_default()
        try:
            tx, ty, tw, th = font.getbbox(text)
            tw -= tx
            th -= ty
        except AttributeError:
            tw, th = (len(text) * 10, 22)
        x0 = w - tw - 3 * pad
        y0 = h - th - 3 * pad
        draw.rectangle([x0, y0, x0 + tw + 2 * pad, y0 + th + 2 * pad],
                       fill=(180, 30, 30, 220))
        draw.text((x0 + pad, y0 + pad), text, fill=(255, 255, 255), font=font)

    # ----- backends ---------------------------------------------------

    def _write_ffmpeg(self, frames, width: int, height: int) -> None:
        import io
        import ffmpeg  # type: ignore[import-not-found]
        # Encode frames as raw RGB and pipe to ffmpeg
        stream = (
            ffmpeg
            .input("pipe:", format="rawvideo", pix_fmt="rgb24",
                   s=f"{width}x{height}", framerate=self.fps)
            .output(str(self.out_path), pix_fmt="yuv420p", vcodec="libx264",
                    crf=23, preset="medium", movflags="+faststart")
            .overwrite_output()
        )
        proc = stream.run_async(pipe_stdin=True, quiet=True)
        try:
            for f in frames:
                proc.stdin.write(f.tobytes())
        finally:
            proc.stdin.close()
            proc.wait()

    def _write_opencv(self, frames, width: int, height: int) -> None:
        import cv2  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
        # mp4v is the most portable codec available without extra deps.
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(
            str(self.out_path), fourcc, float(self.fps), (width, height)
        )
        if not writer.isOpened():
            raise RuntimeError("cv2.VideoWriter failed to open")
        try:
            for f in frames:
                arr = np.array(f)
                bgr = arr[:, :, ::-1]  # RGB → BGR
                writer.write(bgr)
        finally:
            writer.release()

    def _write_placeholder(
        self,
        reason: str,
        width: int = 0, height: int = 0, frame_count: int = 0,
    ) -> RenderResult:
        # Write a tiny text marker; mark file as .mp4 even though it is not
        # — consumers should check the metadata in simulation.json.
        marker = (
            f"DARE-PREVIEW-PLACEHOLDER\n"
            f"reason: {reason}\n"
            f"install ffmpeg or opencv-python for real preview rendering\n"
        )
        self.out_path.write_text(marker, encoding="utf-8")
        self.log.warning("Wrote placeholder preview at %s (%s)", self.out_path, reason)
        return RenderResult(
            backend=VideoBackend.PLACEHOLDER,
            path=self.out_path,
            duration_seconds=0.0,
            fps=self.fps,
            frame_count=frame_count,
            width=width,
            height=height,
        )
