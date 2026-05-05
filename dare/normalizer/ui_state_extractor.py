"""UI state extraction: a screenshot → a list of structured UI elements.

Pipeline (per frame)
--------------------
1. **OCR pass** — Tesseract returns word-level boxes with confidences. We
   group adjacent words on the same line into text blocks.
2. **Vision pass** — adaptive thresholding + contour detection finds
   structural elements (buttons, inputs, panels) regardless of text.
3. **Fusion** — vision boxes that contain OCR text borrow that text and
   become typed elements (``button`` / ``input`` / ``label``); pure OCR
   regions without a corresponding vision box become standalone ``label``
   elements; pure vision boxes without text become ``panel`` elements.
4. **Stable IDs** — every element gets a content-derived ID via
   :func:`stable_element_id`.

All optional deps degrade gracefully:

* No OpenCV → vision pass skipped, OCR-only output.
* No pytesseract → OCR pass skipped, vision-only output.
* Neither → empty list, warning logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dare.normalizer.element_matcher import BBox, bbox_iou, stable_element_id


@dataclass
class UIElement:
    """A single UI element on a frame."""

    id: str
    type: str                       # button | input | label | panel
    text: str
    bbox: BBox                      # (x, y, w, h)
    confidence: float
    sources: list[str] = field(default_factory=list)  # ["vision","ocr"] etc.
    interactable: bool = True

    @property
    def center(self) -> tuple[int, int]:
        x, y, w, h = self.bbox
        return (x + w // 2, y + h // 2)

    def to_dict(self) -> dict:
        x, y, w, h = self.bbox
        return {
            "id": self.id,
            "type": self.type,
            "text": self.text,
            "bbox": [x, y, w, h],
            "center": list(self.center),
            "confidence": round(self.confidence, 4),
            "sources": list(self.sources),
            "interactable": self.interactable,
        }


# ---------------------------------------------------------------------------


class UIStateExtractor:
    """Extracts a list of :class:`UIElement` from a single screenshot.

    Args:
        min_area: minimum bbox area (pixels²) to keep. Filters noise.
        max_area_ratio: max fraction of the image a single element may
            occupy before we treat it as a backdrop and drop it.
        ocr_lang: Tesseract language string (default ``"eng"``).
        ocr_min_confidence: drop OCR words below this confidence (0–100).
        logger: optional logger.
    """

    def __init__(
        self,
        min_area: int = 200,
        max_area_ratio: float = 0.85,
        ocr_lang: str = "eng",
        ocr_min_confidence: int = 30,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.min_area = min_area
        self.max_area_ratio = max_area_ratio
        self.ocr_lang = ocr_lang
        self.ocr_min_confidence = ocr_min_confidence
        self.log = logger or logging.getLogger("dare.normalizer.extractor")
        self._cv2 = self._import_cv2()
        self._tess = self._import_tesseract()
        self._np = self._import_numpy()

    # ----- public ------------------------------------------------------

    def extract(self, image_path: Path) -> list[UIElement]:
        """Run extraction on a screenshot file."""
        if not Path(image_path).is_file():
            self.log.warning("Screenshot missing: %s", image_path)
            return []
        img = self._load(image_path)
        if img is None:
            return []
        h, w = img.shape[:2]
        size = (w, h)

        ocr_blocks = self._ocr(img) if self._tess and self._np is not None else []
        vision_boxes = (
            self._vision(img) if self._cv2 and self._np is not None else []
        )

        return self._fuse(vision_boxes, ocr_blocks, size)

    # ----- helpers -----------------------------------------------------

    @staticmethod
    def _import_cv2():
        try:
            import cv2  # type: ignore[import-not-found]

            return cv2
        except ImportError:
            return None

    @staticmethod
    def _import_tesseract():
        try:
            import pytesseract  # type: ignore[import-not-found]

            return pytesseract
        except ImportError:
            return None

    @staticmethod
    def _import_numpy():
        try:
            import numpy as np  # type: ignore[import-not-found]

            return np
        except ImportError:
            return None

    def _load(self, path: Path):
        """Return an OpenCV BGR ndarray, or None if neither cv2 nor PIL."""
        if self._cv2 is not None:
            arr = self._cv2.imread(str(path))
            if arr is None:
                self.log.warning("cv2.imread returned None for %s", path)
            return arr
        # PIL fallback — convert to BGR ndarray-shape via numpy
        try:
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError:
            self.log.error(
                "Neither OpenCV nor Pillow installed; cannot load images. "
                "Run: pip install -e .[vision]"
            )
            return None
        if self._np is None:
            return None
        img = Image.open(path).convert("RGB")
        rgb = self._np.asarray(img)
        return rgb[:, :, ::-1].copy()  # BGR

    # ----- OCR pass ----------------------------------------------------

    def _ocr(self, img) -> list[tuple[BBox, str, float]]:
        """Return text blocks as ``(bbox, text, confidence)``.

        Adjacent words on the same line (same ``block_num``+``line_num``) are
        joined into a single block so a button label like "Buy now" is one
        element, not two.
        """
        from pytesseract import Output  # type: ignore[import-not-found]

        try:
            data = self._tess.image_to_data(  # type: ignore[union-attr]
                img,
                lang=self.ocr_lang,
                output_type=Output.DICT,
            )
        except Exception as e:
            self.log.warning("OCR failed: %s", e)
            return []

        n = len(data.get("text", []))
        # Group by (block_num, par_num, line_num)
        groups: dict[tuple[int, int, int], list[int]] = {}
        for i in range(n):
            text = (data["text"][i] or "").strip()
            try:
                conf = float(data["conf"][i])
            except (ValueError, TypeError):
                conf = -1.0
            if not text or conf < self.ocr_min_confidence:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            groups.setdefault(key, []).append(i)

        out: list[tuple[BBox, str, float]] = []
        for indices in groups.values():
            words = [data["text"][i] for i in indices]
            xs = [data["left"][i] for i in indices]
            ys = [data["top"][i] for i in indices]
            xe = [data["left"][i] + data["width"][i] for i in indices]
            ye = [data["top"][i] + data["height"][i] for i in indices]
            confs = [float(data["conf"][i]) for i in indices]
            x, y = min(xs), min(ys)
            w, h = max(xe) - x, max(ye) - y
            out.append(((x, y, w, h), " ".join(words), sum(confs) / len(confs) / 100.0))
        return out

    # ----- vision pass -------------------------------------------------

    def _vision(self, img) -> list[BBox]:
        """Return candidate bounding boxes via adaptive threshold + contours."""
        cv2 = self._cv2
        np = self._np
        if cv2 is None or np is None:
            return []
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # Adaptive threshold deals with light/dark UI alike.
        thresh = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            blockSize=15, C=8,
        )
        # Close gaps so a button's outline becomes a single contour.
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
        closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=1)

        contours, _ = cv2.findContours(
            closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        h, w = img.shape[:2]
        max_area = w * h * self.max_area_ratio

        out: list[BBox] = []
        for c in contours:
            x, y, cw, ch = cv2.boundingRect(c)
            area = cw * ch
            if area < self.min_area or area > max_area:
                continue
            if cw < 10 or ch < 8:
                continue
            out.append((int(x), int(y), int(cw), int(ch)))
        return out

    # ----- fusion ------------------------------------------------------

    def _fuse(
        self,
        vision_boxes: list[BBox],
        ocr_blocks: list[tuple[BBox, str, float]],
        size: tuple[int, int],
    ) -> list[UIElement]:
        """Combine vision boxes with OCR text blocks into typed elements.

        Strategy:
        * For each OCR block, find the smallest vision box whose IoU with the
          OCR block is ≥ 0.3. If found → typed element; the vision box is
          consumed.
        * Remaining OCR blocks → standalone ``label`` elements.
        * Remaining vision boxes → ``panel`` elements.
        """
        consumed: set[int] = set()
        out: list[UIElement] = []

        for ocr_bbox, text, ocr_conf in ocr_blocks:
            best_idx = -1
            best_iou = 0.3  # threshold
            for i, vbox in enumerate(vision_boxes):
                if i in consumed:
                    continue
                iou = bbox_iou(ocr_bbox, vbox)
                if iou > best_iou:
                    best_iou = iou
                    best_idx = i
            if best_idx >= 0:
                consumed.add(best_idx)
                vbox = vision_boxes[best_idx]
                el_type = self._classify(vbox, text)
                conf = min(ocr_conf, 0.9)  # vision presence boosts cap
                el = self._make_element(
                    text=text,
                    bbox=vbox,
                    el_type=el_type,
                    confidence=conf,
                    sources=["vision", "ocr"],
                    size=size,
                )
            else:
                el = self._make_element(
                    text=text,
                    bbox=ocr_bbox,
                    el_type="label",
                    confidence=ocr_conf,
                    sources=["ocr"],
                    size=size,
                )
            out.append(el)

        for i, vbox in enumerate(vision_boxes):
            if i in consumed:
                continue
            out.append(
                self._make_element(
                    text="",
                    bbox=vbox,
                    el_type=self._classify(vbox, ""),
                    confidence=0.5,
                    sources=["vision"],
                    size=size,
                )
            )
        return out

    def _classify(self, bbox: BBox, text: str) -> str:
        """Heuristic type classification.

        * Wide and short with text → ``button``
        * Wide and short without text → ``input``
        * Anything large → ``panel``
        """
        _, _, w, h = bbox
        if h <= 0 or w <= 0:
            return "panel"
        aspect = w / h
        area = w * h
        if area > 80_000:
            return "panel"
        if 1.5 <= aspect <= 12 and h <= 80:
            return "button" if text.strip() else "input"
        return "panel"

    @staticmethod
    def _make_element(
        text: str,
        bbox: BBox,
        el_type: str,
        confidence: float,
        sources: list[str],
        size: tuple[int, int],
    ) -> UIElement:
        eid = stable_element_id(text, bbox, el_type, size)
        return UIElement(
            id=eid,
            type=el_type,
            text=text,
            bbox=bbox,
            confidence=confidence,
            sources=sources,
            interactable=el_type in ("button", "input"),
        )
