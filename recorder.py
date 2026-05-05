"""Normalizer — Phase 3.

Converts raw events + screenshots into a structured semantic representation:

* :class:`UIStateExtractor`  — vision + OCR fusion → list of UI elements.
* :class:`StateDiffEngine`   — compute diff between two UI states.
* :class:`Normalizer`        — orchestrator: raw_events.jsonl → ui_state.json,
                               state_diff.json, action_graph.json.

Optional dependencies:

* ``opencv-python``  → contour detection (recommended).
* ``pytesseract``    → OCR text + bboxes (recommended).
* ``rapidfuzz``      → fuzzy text matching (recommended).

Each is independently optional: missing one degrades quality but never
breaks the pipeline.
"""

from dare.normalizer.element_matcher import (
    bbox_iou,
    bbox_relative,
    fuzzy_text_similarity,
    stable_element_id,
)
from dare.normalizer.normalizer import Normalizer
from dare.normalizer.state_diff_engine import StateDiff, StateDiffEngine
from dare.normalizer.ui_state_extractor import UIElement, UIStateExtractor

__all__ = [
    "Normalizer",
    "StateDiff",
    "StateDiffEngine",
    "UIElement",
    "UIStateExtractor",
    "bbox_iou",
    "bbox_relative",
    "fuzzy_text_similarity",
    "stable_element_id",
]
