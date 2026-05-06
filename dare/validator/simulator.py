"""State-aware action simulator.

For each action in ``action.dsl.yaml`` the simulator:

1. Loads the ``before`` UI state from ``ui_state.json``.
2. Locates the target element with a confidence score
   (``target_confidence``).
3. Reads the recorded ``state_diff`` for the matching frame transition
   and computes ``effect_match`` between ``expected_effect`` and
   ``observed_effects``.
4. Computes ``effect_confidence = target × diff × match`` (composite).
5. For DYNAMIC actions, runs one simulation per ``example_value`` (variants).

The simulator does **not** execute anything on the real desktop — it
reasons over the recorded artifacts and emits a structured report that the
validator turns into ``simulation.json`` and (via the renderer) a
``preview.mp4``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from dare.normalizer.element_matcher import fuzzy_text_similarity
from dare.utils.logging import get_logger


@dataclass
class SimulationStep:
    """Result of simulating a single action in a single run."""

    action_id: str
    action: str
    target_hint: str
    target_found: bool
    target_confidence: float
    target_bbox: Optional[list] = None
    expected_effect: str = "none"
    observed_effects: list[str] = field(default_factory=list)
    effect_match: bool = False
    diff_confidence: float = 0.0
    match_score: float = 0.0
    effect_confidence: float = 0.0
    state_delta: Optional[dict] = None
    frame_before: Optional[str] = None
    frame_after: Optional[str] = None
    params: Optional[dict] = None
    status: str = "ok"        # ok | fail
    fail_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "action": self.action,
            "target_hint": self.target_hint,
            "target_found": self.target_found,
            "target_confidence": round(self.target_confidence, 4),
            "target_bbox": self.target_bbox,
            "expected_effect": self.expected_effect,
            "observed_effects": list(self.observed_effects),
            "effect_match": self.effect_match,
            "diff_confidence": round(self.diff_confidence, 4),
            "match_score": round(self.match_score, 4),
            "effect_confidence": round(self.effect_confidence, 4),
            "state_delta": self.state_delta,
            "frame_before": self.frame_before,
            "frame_after": self.frame_after,
            "params": self.params,
            "status": self.status,
            "fail_reason": self.fail_reason,
        }


@dataclass
class SimulationRun:
    """One run (original or variant) of the entire DSL."""

    label: str               # "original" | "variant"
    params: dict
    steps: list[SimulationStep] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "fail" if any(s.status == "fail" for s in self.steps) else "ok"

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "params": self.params,
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
        }


# ---------------------------------------------------------------------------


class Simulator:
    """Drive simulations on a normalized run."""

    def __init__(
        self,
        run_dir: Path,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.processed = self.run_dir / "processed"
        self.log = logger or get_logger("dare.validator.simulator", run_dir=self.run_dir)
        self._ui_state = self._load_ui_state()
        self._diffs = self._load_diffs()
        self._action_graph = self._load_action_graph()
        self._registry = self._load_intent_registry()

    # ----- public ------------------------------------------------------

    def simulate_all(self) -> list[SimulationRun]:
        """Return one SimulationRun per (action set × variants)."""
        actions = self._action_graph.get("actions", [])
        # Original run
        runs = [self._simulate_run("original", {}, actions)]
        # Variants for each DYNAMIC action with example_values
        for a in actions:
            aid = a.get("id")
            entry = self._registry.get(aid, {})
            if entry.get("type") != "DYNAMIC":
                continue
            for example in entry.get("example_values", []) or []:
                runs.append(
                    self._simulate_run(
                        f"variant[{aid}={example}]",
                        {aid: example},
                        actions,
                    )
                )
        return runs

    # ----- internals ---------------------------------------------------

    def _simulate_run(
        self, label: str, overrides: dict, actions: list[dict]
    ) -> SimulationRun:
        run = SimulationRun(label=label, params=dict(overrides))
        for action in actions:
            override_value = overrides.get(action.get("id"))
            run.steps.append(self._simulate_step(action, override_value))
        return run

    def _simulate_step(
        self, action: dict, override_value: Any = None
    ) -> SimulationStep:
        aid = action.get("id", "?")
        atype = action.get("type", "")
        target = action.get("target") or {}
        frame_before = action.get("frame")
        frame_after = self._infer_after_frame(frame_before)

        # Find target on before-frame
        target_conf, target_el = self._find_target(frame_before, target)
        target_bbox = list(target_el.get("bbox", [])) if target_el else None
        target_found = target_el is not None and target_conf > 0.0

        # Diff lookup
        diff = self._diff_for(frame_before, frame_after)
        observed = [c["type"] for c in (diff.get("changes", []) if diff else [])]
        expected = action.get("expected_diff", "none")

        # Match scoring
        if expected == "none" or not observed:
            effect_match = expected in observed or expected == "none" and not observed
            match_score = 1.0 if effect_match else 0.0
        else:
            effect_match = expected in observed
            match_score = 1.0 if effect_match else 0.0

        diff_conf = self._diff_confidence(diff) if diff else 0.0
        effect_conf = target_conf * diff_conf * match_score
        state_delta = self._extract_state_delta(diff, expected) if diff else None

        # Variant override → record params
        params: Optional[dict] = None
        if override_value is not None:
            params = {aid: override_value}

        step = SimulationStep(
            action_id=aid,
            action=atype,
            target_hint=target.get("hint", "unknown"),
            target_found=target_found,
            target_confidence=target_conf,
            target_bbox=target_bbox,
            expected_effect=expected,
            observed_effects=observed,
            effect_match=effect_match,
            diff_confidence=diff_conf,
            match_score=match_score,
            effect_confidence=effect_conf,
            state_delta=state_delta,
            frame_before=frame_before,
            frame_after=frame_after,
            params=params,
        )
        if not target_found:
            step.status = "fail"
            step.fail_reason = "target_not_found"
        elif not effect_match and expected != "none":
            step.status = "fail"
            step.fail_reason = f"expected {expected!r} not in observed {observed!r}"
        return step

    # ----- file IO -----------------------------------------------------

    def _load_ui_state(self) -> dict[str, list[dict]]:
        path = self.processed / "ui_state.json"
        if not path.is_file():
            return {}
        data = json.loads(path.read_text("utf-8"))
        out: dict[str, list[dict]] = {}
        for frame in data.get("frames", []):
            fid = frame.get("frame_id") or frame.get("screenshot")
            if fid:
                out[fid] = list(frame.get("elements", []))
        return out

    def _load_diffs(self) -> list[dict]:
        path = self.processed / "state_diff.json"
        if not path.is_file():
            return []
        return list(json.loads(path.read_text("utf-8")).get("diffs", []))

    def _load_action_graph(self) -> dict:
        path = self.processed / "action_graph.json"
        if not path.is_file():
            return {"actions": []}
        return json.loads(path.read_text("utf-8"))

    def _load_intent_registry(self) -> dict:
        path = self.processed / "intent_registry.json"
        if not path.is_file():
            return {}
        return json.loads(path.read_text("utf-8"))

    # ----- helpers -----------------------------------------------------

    def _infer_after_frame(self, before: Optional[str]) -> Optional[str]:
        """Find the diff record whose ``from_frame`` matches and return its
        ``to_frame``."""
        if before is None:
            return None
        for d in self._diffs:
            if d.get("from_frame") == before:
                return d.get("to_frame")
        return None

    def _diff_for(
        self, before: Optional[str], after: Optional[str]
    ) -> Optional[dict]:
        if before is None or after is None:
            return None
        for d in self._diffs:
            if d.get("from_frame") == before and d.get("to_frame") == after:
                return d
        return None

    def _find_target(
        self, frame: Optional[str], target: dict
    ) -> tuple[float, Optional[dict]]:
        """Return ``(confidence, element)`` for the best target match on
        ``frame``."""
        if not frame:
            return 0.0, None
        elements = self._ui_state.get(frame, [])
        if not elements:
            return 0.0, None
        # 1) exact element_id match
        eid = target.get("element_id")
        if eid:
            for el in elements:
                if el.get("id") == eid:
                    return float(el.get("confidence", 0.8)), el
        # 2) text fuzzy match
        text = (target.get("text") or "").strip()
        best = None
        best_score = 0.0
        if text:
            for el in elements:
                sim = fuzzy_text_similarity(text, el.get("text", ""))
                if sim > best_score:
                    best_score = sim
                    best = el
            if best and best_score >= 0.6:
                return best_score * float(best.get("confidence", 0.8)), best
        # 3) hint substring match
        hint = (target.get("hint") or "").strip().lower()
        if hint:
            for el in elements:
                etext = (el.get("text") or "").strip().lower().replace(" ", "_")
                if not etext:
                    continue  # empty text would always match — skip
                if hint in etext or etext in hint:
                    return 0.5 * float(el.get("confidence", 0.6)), el
        return 0.0, None

    @staticmethod
    def _diff_confidence(diff: dict) -> float:
        """Aggregate confidence over all changes in the diff."""
        changes = diff.get("changes", [])
        if not changes:
            return 0.5  # no changes detected — neutral
        confs = [float(c.get("confidence", 0.7)) for c in changes]
        return sum(confs) / len(confs)

    @staticmethod
    def _extract_state_delta(diff: dict, expected: str) -> Optional[dict]:
        """Extract a representative single change for the report."""
        changes = diff.get("changes", [])
        if not changes:
            return None
        for c in changes:
            if c.get("type") == expected:
                return c
        return changes[0]
