"""Target-resolution retry strategies for the executor.

When an action's primary target identification fails (vision template
match below threshold), the executor walks down a fallback chain:

1. **Vision** — template match the saved crop against the live screen.
2. **OCR**    — find the target text on the live screen via Tesseract.
3. **Relative position** — fall back to the target's normalized
   ``relative_position`` (0..1, 0..1) on the current screen size.

Each strategy returns ``(x, y, confidence)`` or ``None``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class RetryStrategy:
    """Resolve a target on the current screen using vision/OCR/relative."""

    template_match_threshold: float = 0.75
    ocr_min_confidence: int = 30
    logger: Optional[logging.Logger] = None

    def __post_init__(self) -> None:
        self.log = self.logger or logging.getLogger("dare.executor.retry")

    # ----- public ------------------------------------------------------

    def resolve(
        self,
        target: dict,
        run_dir: Path,
        screen_capture,
    ) -> Optional[tuple[int, int, float]]:
        """Try strategies in order; return the first hit."""
        strategies = [
            self._by_vision,
            self._by_ocr,
            self._by_relative,
        ]
        for strat in strategies:
            try:
                hit = strat(target, run_dir, screen_capture)
            except Exception as e:
                self.log.warning("Strategy %s failed: %s", strat.__name__, e)
                continue
            if hit is not None:
                return hit
        return None

    # ----- vision template match --------------------------------------

    def _by_vision(
        self, target: dict, run_dir: Path, screen
    ) -> Optional[tuple[int, int, float]]:
        tpl = target.get("vision")
        if not tpl:
            return None
        tpl_path = run_dir / tpl
        if not tpl_path.is_file():
            return None
        try:
            import cv2  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
        except ImportError:
            return None
        if screen is None:
            return None
        screen_arr = np.asarray(screen)
        if screen_arr.ndim == 3 and screen_arr.shape[2] == 3:
            screen_bgr = cv2.cvtColor(screen_arr, cv2.COLOR_RGB2BGR)
        else:
            screen_bgr = screen_arr
        tpl_bgr = cv2.imread(str(tpl_path))
        if tpl_bgr is None:
            return None
        if tpl_bgr.shape[0] > screen_bgr.shape[0] or tpl_bgr.shape[1] > screen_bgr.shape[1]:
            return None
        result = cv2.matchTemplate(screen_bgr, tpl_bgr, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val < self.template_match_threshold:
            return None
        h, w = tpl_bgr.shape[:2]
        cx = int(max_loc[0] + w / 2)
        cy = int(max_loc[1] + h / 2)
        return (cx, cy, float(max_val))

    # ----- OCR --------------------------------------------------------

    def _by_ocr(
        self, target: dict, run_dir: Path, screen
    ) -> Optional[tuple[int, int, float]]:
        text = (target.get("text") or "").strip()
        if not text:
            return None
        try:
            import pytesseract  # type: ignore[import-not-found]
            from pytesseract import Output  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
        except ImportError:
            return None
        if screen is None:
            return None
        screen_arr = np.asarray(screen)
        try:
            data = pytesseract.image_to_data(
                screen_arr, output_type=Output.DICT
            )
        except Exception:
            return None
        n = len(data.get("text", []))
        target_lower = text.lower()
        best_conf = 0.0
        best_xy = None
        for i in range(n):
            t = (data["text"][i] or "").strip().lower()
            try:
                conf = float(data["conf"][i])
            except (ValueError, TypeError):
                conf = -1.0
            if not t or conf < self.ocr_min_confidence:
                continue
            if t == target_lower or target_lower in t or t in target_lower:
                if conf > best_conf:
                    best_conf = conf
                    cx = data["left"][i] + data["width"][i] // 2
                    cy = data["top"][i] + data["height"][i] // 2
                    best_xy = (int(cx), int(cy))
        if best_xy is None:
            return None
        return (best_xy[0], best_xy[1], best_conf / 100.0)

    # ----- relative position ------------------------------------------

    def _by_relative(
        self, target: dict, run_dir: Path, screen
    ) -> Optional[tuple[int, int, float]]:
        rel = target.get("relative_position")
        if not rel or len(rel) != 2:
            return None
        if screen is None:
            return None
        try:
            import numpy as np  # type: ignore[import-not-found]
            arr = np.asarray(screen)
            h, w = arr.shape[:2]
        except ImportError:
            try:
                w, h = screen.size  # PIL Image
            except AttributeError:
                return None
        rx, ry = rel
        x = int(round(float(rx) * w))
        y = int(round(float(ry) * h))
        # Low confidence — relative is a last-ditch fallback.
        return (x, y, 0.3)
