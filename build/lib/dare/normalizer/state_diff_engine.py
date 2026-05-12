"""State diff engine — compute changes between two UI states.

Matching strategy (greedy, in order):
1. **By ID** — same stable element id ⇒ matched.
2. **By IoU** — IoU ≥ 0.7 ⇒ matched (positional persistence).
3. **By fuzzy text** — same text similarity ≥ 0.85 within reasonable
   distance ⇒ matched (handles bbox drift on the same label).

Unmatched in ``before`` → ``removed_element``.
Unmatched in ``after``  → ``new_element``.
Matched pairs → emit ``text_change`` and/or ``moved_element`` as needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from dare.normalizer.element_matcher import (
    bbox_iou,
    fuzzy_text_similarity,
)
from dare.normalizer.ui_state_extractor import UIElement


@dataclass
class StateDiff:
    """Diff between two UI frames."""

    from_frame: str
    to_frame: str
    changes: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "from_frame": self.from_frame,
            "to_frame": self.to_frame,
            "changes": list(self.changes),
        }

    def types(self) -> list[str]:
        return [c["type"] for c in self.changes]


# ---------------------------------------------------------------------------


class StateDiffEngine:
    """Compute :class:`StateDiff` between two element lists."""

    def __init__(
        self,
        iou_threshold: float = 0.7,
        text_threshold: float = 0.85,
        move_distance_threshold: int = 5,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.text_threshold = text_threshold
        self.move_distance_threshold = move_distance_threshold

    # ----- public ------------------------------------------------------

    def diff(
        self,
        before: list[UIElement],
        after: list[UIElement],
        from_frame: str,
        to_frame: str,
    ) -> StateDiff:
        sd = StateDiff(from_frame=from_frame, to_frame=to_frame)
        matched_after: set[int] = set()
        # Pass 1: by stable id
        before_by_id = {e.id: i for i, e in enumerate(before)}
        after_by_id = {e.id: i for i, e in enumerate(after)}
        pairs: list[tuple[int, int]] = []
        for bi, b in enumerate(before):
            if b.id in after_by_id:
                aj = after_by_id[b.id]
                if aj not in matched_after:
                    pairs.append((bi, aj))
                    matched_after.add(aj)

        matched_before: set[int] = {bi for bi, _ in pairs}

        # Pass 2: by IoU on still-unmatched
        for bi, b in enumerate(before):
            if bi in matched_before:
                continue
            best_j = -1
            best_iou = self.iou_threshold
            for aj, a in enumerate(after):
                if aj in matched_after:
                    continue
                iou = bbox_iou(b.bbox, a.bbox)
                if iou > best_iou:
                    best_iou = iou
                    best_j = aj
            if best_j >= 0:
                pairs.append((bi, best_j))
                matched_before.add(bi)
                matched_after.add(best_j)

        # Pass 3: fuzzy text on still-unmatched
        for bi, b in enumerate(before):
            if bi in matched_before or not b.text.strip():
                continue
            best_j = -1
            best_sim = self.text_threshold
            for aj, a in enumerate(after):
                if aj in matched_after or not a.text.strip():
                    continue
                sim = fuzzy_text_similarity(b.text, a.text)
                if sim > best_sim:
                    best_sim = sim
                    best_j = aj
            if best_j >= 0:
                pairs.append((bi, best_j))
                matched_before.add(bi)
                matched_after.add(best_j)

        # Emit changes for matched pairs
        for bi, aj in pairs:
            b = before[bi]
            a = after[aj]
            if b.text != a.text:
                sd.changes.append({
                    "type": "text_change",
                    "element_id": a.id,
                    "before": b.text,
                    "after": a.text,
                    "confidence": min(b.confidence, a.confidence),
                })
            bx, by = b.center
            ax, ay = a.center
            if abs(bx - ax) + abs(by - ay) > self.move_distance_threshold:
                sd.changes.append({
                    "type": "moved_element",
                    "element_id": a.id,
                    "from": [bx, by],
                    "to": [ax, ay],
                })

        # Removed
        for bi, b in enumerate(before):
            if bi not in matched_before:
                sd.changes.append({
                    "type": "removed_element",
                    "element_id": b.id,
                    "element": b.to_dict(),
                })
        # New
        for aj, a in enumerate(after):
            if aj not in matched_after:
                sd.changes.append({
                    "type": "new_element",
                    "element_id": a.id,
                    "element": a.to_dict(),
                })
        return sd
