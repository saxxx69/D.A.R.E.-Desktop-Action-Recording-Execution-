"""Action DSL generator.

Produces ``<run>/processed/action.dsl.yaml`` from
``<run>/processed/action_graph.json``.

The DSL is the executable, executor-agnostic form of the recorded sequence.
It deliberately avoids absolute coordinates as the primary source of truth
— each action carries a ``target`` block (hint + vision template + relative
position) and a verifiable ``expected_effect``.

YAML emission uses :mod:`pyyaml` if available; otherwise we emit a
hand-rolled YAML subset that is sufficient for our schema. Round-trip
identity is verified by tests.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from dare.utils.logging import get_logger


class DSLGenerator:
    """Convert ``action_graph.json`` → ``action.dsl.yaml``."""

    def __init__(
        self,
        run_dir: Path,
        wait_timeout_ms: int = 3000,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.processed = self.run_dir / "processed"
        self.action_graph_path = self.processed / "action_graph.json"
        self.dsl_path = self.processed / "action.dsl.yaml"
        self.wait_timeout_ms = wait_timeout_ms
        self.log = logger or get_logger("dare.dsl", run_dir=self.run_dir)

    # ----- public ------------------------------------------------------

    def run(self) -> dict:
        if not self.action_graph_path.is_file():
            raise FileNotFoundError(
                f"action_graph.json not found at {self.action_graph_path}"
            )
        graph = json.loads(self.action_graph_path.read_text(encoding="utf-8"))
        actions = graph.get("actions", [])
        doc = self._build(actions)
        text = self._dump_yaml(doc)
        self.dsl_path.write_text(text, encoding="utf-8")
        self.log.info("DSL written to %s (%d actions).", self.dsl_path, len(actions))
        return {"actions": len(actions), "path": str(self.dsl_path)}

    # ----- internals ---------------------------------------------------

    def _build(self, actions: list[dict]) -> dict:
        out_actions: list[dict] = []
        for a in actions:
            target = a.get("target") or {}
            target_block = self._target_block(target)
            atype = a.get("type")
            if atype == "click":
                # Wait for the target to be visible, then click it.
                out_actions.append({
                    "action": "WAIT_FOR",
                    "target": dict(target_block),
                    "timeout": self.wait_timeout_ms,
                })
                out_actions.append({
                    "action": "CLICK",
                    "id": a.get("id"),
                    "target": dict(target_block),
                    "expected_effect": a.get("expected_diff", "none"),
                })
            elif atype == "type":
                out_actions.append({
                    "action": "TYPE",
                    "id": a.get("id"),
                    "target": dict(target_block),
                    "value": a.get("value", ""),
                    "expected_effect": a.get("expected_diff", "text_change"),
                })
                # Verify by text presence after the type
                if a.get("value"):
                    out_actions.append({
                        "action": "VERIFY",
                        "condition": f"text_present:{a['value']}",
                    })
            else:
                self.log.warning("Unknown action type: %r", atype)
                continue
        return {
            "version": "1.0",
            "context": {
                "environment": "desktop",
                "resolution": "adaptive",
            },
            "actions": out_actions,
        }

    @staticmethod
    def _target_block(t: dict) -> dict:
        """Build a clean target subdocument, dropping null/empty fields."""
        out: dict[str, Any] = {}
        for key in ("hint", "text", "type", "element_id", "vision"):
            v = t.get(key)
            if v not in (None, ""):
                out[key] = v
        rel = t.get("relative_position")
        if rel:
            out["relative_position"] = list(rel)
        conf = t.get("confidence")
        if conf is not None:
            out["confidence"] = round(float(conf), 4)
        return out

    @staticmethod
    def _dump_yaml(doc: Any) -> str:
        try:
            import yaml  # type: ignore[import-not-found]

            return yaml.safe_dump(
                doc, sort_keys=False, default_flow_style=False, allow_unicode=True
            )
        except ImportError:
            return _stdlib_yaml(doc)


# ---------------------------------------------------------------------------
# Hand-rolled YAML emitter for our subset (used when pyyaml is missing).
# ---------------------------------------------------------------------------


def _stdlib_yaml(obj: Any, indent: int = 0) -> str:
    """Emit a minimal YAML subset: scalars, mappings, lists.

    Strings are quoted iff they contain characters that would otherwise be
    misparsed. Numbers/bools/None get YAML-canonical form.
    """
    return _emit(obj, indent).rstrip() + "\n"


def _emit(obj: Any, indent: int) -> str:
    pad = " " * indent
    if obj is None:
        return f"{pad}null\n"
    if isinstance(obj, bool):
        return f"{pad}{'true' if obj else 'false'}\n"
    if isinstance(obj, (int, float)):
        return f"{pad}{obj}\n"
    if isinstance(obj, str):
        return f"{pad}{_quote(obj)}\n"
    if isinstance(obj, list):
        if not obj:
            return f"{pad}[]\n"
        out = []
        for item in obj:
            inner = _emit(item, indent + 2)
            # Reattach with "- " on the first line of `inner`
            stripped = inner[indent + 2:] if inner.startswith(" " * (indent + 2)) else inner
            if isinstance(item, (dict, list)):
                # Multiline → "-\n  contents"
                first, _, rest = inner.partition("\n")
                out.append(f"{pad}-\n{inner}")
            else:
                out.append(f"{pad}- {stripped}")
        return "".join(out)
    if isinstance(obj, dict):
        if not obj:
            return f"{pad}{{}}\n"
        out = []
        for k, v in obj.items():
            key = _quote(str(k))
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{key}:\n{_emit(v, indent + 2)}")
            else:
                inner = _emit(v, 0).rstrip()
                out.append(f"{pad}{key}: {inner}\n")
        return "".join(out)
    return f"{pad}{_quote(str(obj))}\n"


def _quote(s: str) -> str:
    """Quote a string only when YAML would otherwise misparse it."""
    if s == "" or s in ("null", "true", "false", "yes", "no", "~"):
        return f'"{s}"'
    if any(c in s for c in ":#[]{}\"'\\,&*!|>%@`\n"):
        # Use double-quoted form with minimal escaping
        escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    if s[0] in " -?":
        return f'"{s}"'
    try:
        float(s)
        return f'"{s}"'  # quote so it stays a string
    except ValueError:
        pass
    return s
