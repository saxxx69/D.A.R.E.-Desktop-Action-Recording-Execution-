"""Matching primitives shared by the extractor and the diff engine.

* :func:`bbox_iou`              — intersection-over-union of two boxes.
* :func:`bbox_relative`         — quantise a box to a resolution-independent
                                  relative form for stable hashing.
* :func:`fuzzy_text_similarity` — RapidFuzz when available, simple sequence
                                  matcher fallback otherwise.
* :func:`stable_element_id`     — content-derived ID that survives small
                                  resolution / position drift.
"""

from __future__ import annotations

import hashlib
from difflib import SequenceMatcher
from typing import Optional

# bbox = (x, y, w, h)  — top-left + width/height, ints in pixel space.
BBox = tuple[int, int, int, int]


def bbox_iou(a: BBox, b: BBox) -> float:
    """Intersection over Union of two boxes. Returns 0.0 if disjoint."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.0
    ix1 = max(ax, bx)
    iy1 = max(ay, by)
    ix2 = min(ax + aw, bx + bw)
    iy2 = min(ay + ah, by + bh)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def bbox_relative(
    bbox: BBox, image_size: tuple[int, int], grid: int = 100
) -> tuple[int, int, int, int]:
    """Quantise a pixel-space bbox to a ``grid``-step relative form.

    Used as a stable-id ingredient: a 1-pixel shift, or a screenshot at a
    different resolution, must not change the result. ``grid=100`` means
    1% steps — empirically a good trade-off between stability and
    discrimination.
    """
    x, y, w, h = bbox
    iw, ih = image_size
    if iw <= 0 or ih <= 0:
        return (0, 0, 0, 0)
    return (
        int(round(x / iw * grid)),
        int(round(y / ih * grid)),
        int(round(w / iw * grid)),
        int(round(h / ih * grid)),
    )


def fuzzy_text_similarity(a: Optional[str], b: Optional[str]) -> float:
    """Return a similarity ratio in ``[0, 1]``.

    Uses RapidFuzz if installed (faster, more robust to OCR noise), else
    falls back to ``difflib.SequenceMatcher``. Empty strings → 1.0 if both
    empty, else 0.0.
    """
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    try:
        from rapidfuzz import fuzz  # type: ignore[import-not-found]

        return float(fuzz.ratio(a, b)) / 100.0
    except ImportError:
        return SequenceMatcher(None, a, b).ratio()


def stable_element_id(
    text: str, bbox: BBox, element_type: str, image_size: tuple[int, int]
) -> str:
    """Hash content into a 12-char hex ID stable across small drift."""
    rel = bbox_relative(bbox, image_size)
    payload = f"{element_type}|{(text or '').strip().lower()}|{rel}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
