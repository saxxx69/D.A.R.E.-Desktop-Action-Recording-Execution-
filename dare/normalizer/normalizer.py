"""Normalizer orchestrator.

Reads ``<run>/raw/raw_events.jsonl`` plus ``<run>/assets/screenshots/`` and
emits three structured artifacts under ``<run>/processed/``:

* ``ui_state.json``   — list of frames, each with its UI elements.
* ``state_diff.json`` — diffs between adjacent significant frames.
* ``action_graph.json`` — semantic actions linked to elements + expected
                          effects, ready to feed the DSL generator.

For every interactive event (mouse_click / keyboard_input) the orchestrator
also crops a small vision template around the click/input element and saves
it under ``<run>/assets/templates/`` so the executor has a fallback target.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dare.normalizer.element_matcher import bbox_relative
from dare.normalizer.state_diff_engine import StateDiff, StateDiffEngine
from dare.normalizer.ui_state_extractor import UIElement, UIStateExtractor
from dare.utils.logging import get_logger


@dataclass
class ActionGraph:
    """Container for the actions list."""

    actions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"actions": list(self.actions)}


class Normalizer:
    """Orchestrate the per-run normalization pipeline."""

    def __init__(
        self,
        run_dir: Path,
        extractor: Optional[UIStateExtractor] = None,
        diff_engine: Optional[StateDiffEngine] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.raw_path = self.run_dir / "raw" / "raw_events.jsonl"
        self.shots_dir = self.run_dir / "assets" / "screenshots"
        self.templates_dir = self.run_dir / "assets" / "templates"
        self.processed_dir = self.run_dir / "processed"
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.extractor = extractor or UIStateExtractor()
        self.diff_engine = diff_engine or StateDiffEngine()
        self.log = logger or get_logger("dare.normalizer", run_dir=self.run_dir)

    # ----- public ------------------------------------------------------

    def run(self) -> dict:
        """Execute the full normalization. Returns a summary dict."""
        if not self.raw_path.is_file():
            raise FileNotFoundError(
                f"raw_events.jsonl not found in {self.raw_path}"
            )
        events = self._load_events()
        screenshots = self._screenshots_index(events)
        ui_state = self._extract_states(screenshots)
        action_graph, diffs = self._build_action_graph(events, ui_state)

        # Persist
        (self.processed_dir / "ui_state.json").write_text(
            json.dumps(
                {"frames": [
                    {
                        "frame_id": fid,
                        "screenshot": fid,
                        "elements": [el.to_dict() for el in els],
                    } for fid, els in ui_state.items()
                ]},
                indent=2, ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )
        (self.processed_dir / "state_diff.json").write_text(
            json.dumps(
                {"diffs": [d.to_dict() for d in diffs]},
                indent=2, ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )
        (self.processed_dir / "action_graph.json").write_text(
            json.dumps(action_graph.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        summary = {
            "frames": len(ui_state),
            "elements_total": sum(len(els) for els in ui_state.values()),
            "diffs": len(diffs),
            "actions": len(action_graph.actions),
        }
        self.log.info("Normalization complete: %s", summary)
        return summary

    # ----- internals ---------------------------------------------------

    def _load_events(self) -> list[dict]:
        out: list[dict] = []
        with self.raw_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as e:
                    self.log.warning("Skipping malformed JSONL line: %s", e)
        return out

    def _screenshots_index(self, events: list[dict]) -> list[str]:
        """Return the ordered list of screenshot filenames referenced by
        events. Falls back to listing the screenshots directory if no
        events reference any (e.g. corrupt jsonl).
        """
        seen: list[str] = []
        seen_set: set[str] = set()
        for ev in events:
            t = ev.get("type")
            if t == "screenshot":
                fname = ev.get("filename")
            elif t == "mouse_click":
                fname = ev.get("screenshot")
            elif t == "keyboard_input":
                fname = ev.get("after_screenshot") or ev.get("before_screenshot")
            else:
                fname = None
            if fname and fname not in seen_set:
                seen.append(fname)
                seen_set.add(fname)
        if not seen and self.shots_dir.is_dir():
            seen = sorted(p.name for p in self.shots_dir.glob("*.png"))
        return seen

    def _extract_states(
        self, screenshots: list[str]
    ) -> dict[str, list[UIElement]]:
        out: dict[str, list[UIElement]] = {}
        for fname in screenshots:
            path = self.shots_dir / fname
            els = self.extractor.extract(path)
            out[fname] = els
            self.log.info("Extracted %d elements from %s", len(els), fname)
        return out

    def _build_action_graph(
        self,
        events: list[dict],
        ui_state: dict[str, list[UIElement]],
    ) -> tuple[ActionGraph, list[StateDiff]]:
        """Walk the event stream, link each interactive event to a target
        element on its 'before' frame, and compute the diff to its 'after'
        frame.
        """
        graph = ActionGraph()
        diffs: list[StateDiff] = []
        action_idx = 0

        # Build a sequence: for each interactive event we need (event,
        # before_frame, after_frame).
        prev_frame: Optional[str] = None
        for ev in events:
            t = ev.get("type")
            if t == "screenshot":
                # screenshots are passive index events; they update prev_frame
                prev_frame = ev.get("filename") or prev_frame
                continue
            if t == "mouse_click":
                before = prev_frame
                after = ev.get("screenshot") or before
                target_frame = before or after
                action = self._mouse_action(
                    ev, target_frame, ui_state, action_idx
                )
                if before and after and before != after:
                    diff = self.diff_engine.diff(
                        ui_state.get(before, []),
                        ui_state.get(after, []),
                        before, after,
                    )
                    diffs.append(diff)
                    action["expected_diff"] = self._summarize_diff(diff)
                graph.actions.append(action)
                action_idx += 1
                prev_frame = after
            elif t == "keyboard_input":
                before = ev.get("before_screenshot") or prev_frame
                after = ev.get("after_screenshot") or before
                action = self._keyboard_action(
                    ev, before or after, ui_state, action_idx
                )
                if before and after and before != after:
                    diff = self.diff_engine.diff(
                        ui_state.get(before, []),
                        ui_state.get(after, []),
                        before, after,
                    )
                    diffs.append(diff)
                    # Keep the semantically expected effect for type actions
                    # (text_change) unless the actual diff disagrees strongly.
                    detected = self._summarize_diff(diff)
                    if "text_change" in diff.types() or detected == "none":
                        # text_change present, or nothing detected → keep
                        # the semantic default ("text_change").
                        pass
                    else:
                        action["expected_diff"] = detected
                graph.actions.append(action)
                action_idx += 1
                prev_frame = after

        return graph, diffs

    def _mouse_action(
        self,
        ev: dict,
        frame: Optional[str],
        ui_state: dict[str, list[UIElement]],
        idx: int,
    ) -> dict:
        x, y = int(ev["x"]), int(ev["y"])
        target_el = self._find_element_at(frame, ui_state, x, y) if frame else None
        template_path: Optional[str] = None
        size = self._image_size(frame) if frame else None
        rel_pos: Optional[tuple[float, float]] = None

        if frame and size and (sw := size[0]) and (sh := size[1]):
            rel_pos = (round(x / sw, 4), round(y / sh, 4))

        if target_el and frame:
            template_path = self._save_template(frame, target_el.bbox, idx)

        return {
            "id": f"a{idx}",
            "type": "click",
            "timestamp": ev.get("timestamp"),
            "raw_xy": [x, y],
            "frame": frame,
            "target": self._target_dict(target_el, template_path, rel_pos, ev),
        }

    def _keyboard_action(
        self,
        ev: dict,
        frame: Optional[str],
        ui_state: dict[str, list[UIElement]],
        idx: int,
    ) -> dict:
        # No explicit click point — try to find an `input` element on the
        # before-frame, fall back to the most recent input element.
        target_el: Optional[UIElement] = None
        if frame:
            inputs = [e for e in ui_state.get(frame, []) if e.type == "input"]
            if inputs:
                target_el = inputs[0]
        template_path: Optional[str] = None
        size = self._image_size(frame) if frame else None
        rel_pos: Optional[tuple[float, float]] = None
        if target_el and frame and size:
            sw, sh = size
            cx, cy = target_el.center
            rel_pos = (round(cx / sw, 4), round(cy / sh, 4))
            template_path = self._save_template(frame, target_el.bbox, idx)

        return {
            "id": f"a{idx}",
            "type": "type",
            "timestamp": ev.get("start_ts"),
            "value": ev.get("text", ""),
            "keys": ev.get("keys", []),
            "frame": frame,
            "target": self._target_dict(target_el, template_path, rel_pos, ev),
            "expected_diff": "text_change",
        }

    @staticmethod
    def _find_element_at(
        frame: str,
        ui_state: dict[str, list[UIElement]],
        x: int,
        y: int,
    ) -> Optional[UIElement]:
        candidates = []
        for el in ui_state.get(frame, []):
            ex, ey, ew, eh = el.bbox
            if ex <= x <= ex + ew and ey <= y <= ey + eh:
                candidates.append((ew * eh, el))
        if not candidates:
            return None
        # Smallest enclosing element wins (most specific).
        candidates.sort(key=lambda c: c[0])
        return candidates[0][1]

    @staticmethod
    def _target_dict(
        el: Optional[UIElement],
        template_path: Optional[str],
        rel_pos: Optional[tuple[float, float]],
        ev: dict,
    ) -> dict:
        if el is None:
            return {
                "hint": "unknown",
                "vision": template_path,
                "text": "",
                "type": "unknown",
                "relative_position": list(rel_pos) if rel_pos else None,
                "confidence": 0.0,
            }
        return {
            "hint": (el.text.strip().lower().replace(" ", "_") or el.type),
            "vision": template_path,
            "text": el.text,
            "type": el.type,
            "element_id": el.id,
            "bbox": list(el.bbox),
            "relative_position": list(rel_pos) if rel_pos else None,
            "confidence": el.confidence,
        }

    def _save_template(
        self, frame: str, bbox: tuple[int, int, int, int], idx: int
    ) -> Optional[str]:
        """Crop the bbox area from the frame and save it as PNG."""
        path = self.shots_dir / frame
        if not path.is_file():
            return None
        try:
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError:
            return None
        try:
            img = Image.open(path).convert("RGB")
        except Exception as e:
            self.log.warning("Cannot open %s for template: %s", path, e)
            return None
        x, y, w, h = bbox
        x = max(0, x)
        y = max(0, y)
        x2 = min(img.width, x + w)
        y2 = min(img.height, y + h)
        if x2 <= x or y2 <= y:
            return None
        crop = img.crop((x, y, x2, y2))
        out_name = f"a{idx}_target.png"
        crop.save(self.templates_dir / out_name, format="PNG")
        return f"templates/{out_name}"

    def _image_size(self, frame: Optional[str]) -> Optional[tuple[int, int]]:
        if not frame:
            return None
        path = self.shots_dir / frame
        if not path.is_file():
            return None
        try:
            from PIL import Image  # type: ignore[import-not-found]

            with Image.open(path) as img:
                return img.size
        except ImportError:
            try:
                import cv2  # type: ignore[import-not-found]

                arr = cv2.imread(str(path))
                if arr is None:
                    return None
                h, w = arr.shape[:2]
                return (w, h)
            except ImportError:
                return None

    @staticmethod
    def _summarize_diff(diff: StateDiff) -> str:
        types = diff.types()
        # Pick the most informative single diff type for the action.
        for preferred in ("text_change", "new_element", "removed_element", "moved_element"):
            if preferred in types:
                return preferred
        return "none"
