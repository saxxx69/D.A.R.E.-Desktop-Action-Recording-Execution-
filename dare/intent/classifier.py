"""Classify actions as STATIC or DYNAMIC.

Heuristics
----------
* ``click`` actions:
    * STATIC if the target text is a recognisable command verb / button
      label (``BUY``, ``SELL``, ``OK``, ``CANCEL``, …) or has any
      stable text at all.
    * DYNAMIC only if there is no text and no element_id (target unknown).
* ``type`` actions:
    * DYNAMIC if the value parses as a number, contains digits, looks like
      a path/URL, or is otherwise context-dependent.
    * STATIC if the value is a fixed phrase with no obvious variability.

The classifier produces a structured ``IntentClassification`` with parameter
metadata for the DYNAMIC case (extracted parameter name, type, range or
example values).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_PATHY_RE = re.compile(r"[\\/]|^https?://", re.IGNORECASE)
_HAS_DIGIT_RE = re.compile(r"\d")

# Common static button labels (case-insensitive). Any text starting with
# one of these is treated as STATIC — it identifies the same UI control
# regardless of context.
_STATIC_LABELS = frozenset({
    "buy", "sell", "ok", "cancel", "confirm", "submit", "save", "close",
    "next", "back", "previous", "yes", "no", "apply", "reset", "clear",
    "open", "delete", "edit", "create", "new", "login", "logout",
    "sign in", "sign out", "sign up", "register", "continue", "finish",
    "start", "stop", "pause", "resume",
})


@dataclass
class IntentParameter:
    """Description of a single parameter for a DYNAMIC action."""

    name: str
    type: str                               # float | int | string | path | url
    example_values: list = field(default_factory=list)
    range: Optional[list] = None            # [min, max] for numeric

    def to_dict(self) -> dict:
        d = {"name": self.name, "type": self.type, "example_values": list(self.example_values)}
        if self.range is not None:
            d["range"] = list(self.range)
        return d


@dataclass
class IntentClassification:
    """Result of classifying a single action."""

    action_id: str
    type: str                               # STATIC | DYNAMIC
    description: str
    parameters: list[IntentParameter] = field(default_factory=list)
    confirmed: bool = False                 # set True after HITL confirmation

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "type": self.type,
            "description": self.description,
            "parameters": [p.to_dict() for p in self.parameters],
            "confirmed": self.confirmed,
        }


# ---------------------------------------------------------------------------


def classify_action(action: dict) -> IntentClassification:
    """Classify a single action graph entry."""
    aid = action.get("id", "?")
    atype = action.get("type")
    target = action.get("target") or {}
    text = (target.get("text") or "").strip()

    if atype == "click":
        if text:
            lowered = text.lower()
            is_static_label = any(
                lowered.startswith(lbl) for lbl in _STATIC_LABELS
            )
            return IntentClassification(
                action_id=aid,
                type="STATIC",
                description=f"Click on {text!r} ({target.get('type','element')})",
            )
        # No text — still STATIC if we have an element_id (deterministic
        # by structure); only DYNAMIC if target is truly unknown.
        if target.get("element_id"):
            return IntentClassification(
                action_id=aid,
                type="STATIC",
                description=f"Click on {target.get('type','element')}",
            )
        return IntentClassification(
            action_id=aid,
            type="DYNAMIC",
            description="Click on an unidentified target",
            parameters=[IntentParameter(
                name="target_position",
                type="string",
                example_values=["center", "top-left"],
            )],
        )

    if atype == "type":
        value = str(action.get("value", ""))
        param = _classify_value(value)
        if param is None:
            return IntentClassification(
                action_id=aid,
                type="STATIC",
                description=f"Type fixed text {value!r}",
            )
        return IntentClassification(
            action_id=aid,
            type="DYNAMIC",
            description=f"Type variable value into {target.get('type','input')}",
            parameters=[param],
        )

    return IntentClassification(
        action_id=aid,
        type="STATIC",
        description=f"Action of type {atype!r}",
    )


def _classify_value(value: str) -> Optional[IntentParameter]:
    """Inspect a typed value and return a parameter description if it is
    DYNAMIC. Returns None for genuinely fixed text."""
    if not value:
        return None
    if _NUMERIC_RE.match(value):
        is_float = "." in value
        try:
            num = float(value)
        except ValueError:
            num = 0.0
        if is_float:
            # Heuristic range that adapts to the magnitude.
            lo = 0.01 if num > 0 else num * 10
            hi = max(num * 100, 100.0)
            return IntentParameter(
                name="numeric_value",
                type="float",
                range=[round(lo, 4), round(hi, 4)],
                example_values=[round(num * 0.5, 4), num, round(num * 2, 4)],
            )
        return IntentParameter(
            name="numeric_value",
            type="int",
            range=[max(int(num) - 10, 0), int(num) + 100],
            example_values=[max(int(num) - 1, 0), int(num), int(num) + 1],
        )
    if _PATHY_RE.search(value):
        kind = "url" if value.lower().startswith("http") else "path"
        return IntentParameter(
            name=f"{kind}_value",
            type=kind,
            example_values=[value],
        )
    if _HAS_DIGIT_RE.search(value):
        # Mixed alphanumeric — probably a code, ticker, identifier.
        return IntentParameter(
            name="alphanumeric_value",
            type="string",
            example_values=[value],
        )
    # Otherwise treat as a fixed phrase.
    return None


class IntentClassifier:
    """Stateless wrapper for batch classification."""

    def classify_all(self, actions: list[dict]) -> list[IntentClassification]:
        return [classify_action(a) for a in actions]
