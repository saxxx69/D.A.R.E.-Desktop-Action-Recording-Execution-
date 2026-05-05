"""Human-in-the-loop clarification layer.

For every action in ``action_graph.json`` the layer:

1. Shows the action's screenshot (opens in the OS image viewer) and the
   classifier's current verdict.
2. Asks the user (interactively):
   * Goal of the action (free text).
   * STATIC or DYNAMIC?
   * If DYNAMIC: which part is variable, which is fixed, example values.
3. Persists the answers to:
   * ``processed/intent_registry.json``  (entry updated in place)
   * ``clarification_log.json``           (audit log of confirmations)
   * ``docs/intents/<action_id>.md``      (markdown file rewritten)

A non-interactive mode (``--auto-clarify``) skips prompts and just stamps
the classifier's heuristic answer as confirmed — useful for scripted /
batch runs.
"""

from __future__ import annotations

import json
import logging
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dare.intent.classifier import IntentClassification, IntentParameter
from dare.intent.markdown_generator import IntentMarkdownGenerator
from dare.utils.logging import get_logger


@dataclass
class ClarificationEntry:
    """One audit record for an action."""

    action_id: str
    confirmed: bool
    type: str
    goal: str = ""
    variable_component: str = ""
    fixed_component: str = ""
    examples: list = field(default_factory=list)
    timestamp: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------


class ClarificationLayer:
    """Interactive (or auto) clarification driver."""

    def __init__(
        self,
        run_dir: Path,
        auto_clarify: bool = False,
        open_screenshots: bool = True,
        logger: Optional[logging.Logger] = None,
        # Inject IO for tests:
        input_fn=None,
        output_fn=None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.action_graph_path = self.run_dir / "processed" / "action_graph.json"
        self.registry_path = self.run_dir / "processed" / "intent_registry.json"
        self.log_path = self.run_dir / "processed" / "clarification_log.json"
        self.intents_dir = self.run_dir / "docs" / "intents"
        self.shots_dir = self.run_dir / "assets" / "screenshots"
        self.intents_dir.mkdir(parents=True, exist_ok=True)
        self.auto_clarify = auto_clarify
        self.open_screenshots = open_screenshots and not auto_clarify
        self._md_gen = IntentMarkdownGenerator(self.intents_dir)
        self._input = input_fn or input
        self._print = output_fn or print
        self.log = logger or get_logger("dare.clarification", run_dir=self.run_dir)

    # ----- public ------------------------------------------------------

    def run(self) -> dict:
        if not self.action_graph_path.is_file():
            raise FileNotFoundError(
                f"action_graph.json not found at {self.action_graph_path}"
            )
        if not self.registry_path.is_file():
            raise FileNotFoundError(
                "intent_registry.json not found — run `dare build-intents` first"
            )
        graph = json.loads(self.action_graph_path.read_text(encoding="utf-8"))
        registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
        actions = graph.get("actions", [])

        log_entries: list[ClarificationEntry] = []
        confirmed_count = 0
        for i, a in enumerate(actions):
            aid = a.get("id")
            entry_in_reg = registry.get(aid, {})
            classification = self._registry_to_classification(aid, entry_in_reg)
            if self.auto_clarify:
                clar = self._auto(classification)
            else:
                clar = self._interactive(a, classification, i + 1, len(actions))
            registry[aid] = self._merge_registry(registry.get(aid, {}), clar)
            log_entries.append(clar)
            confirmed_count += 1 if clar.confirmed else 0
            # Rewrite the markdown
            updated = self._classification_with_clar(classification, clar)
            self._md_gen.write(updated)

        # Persist
        self.registry_path.write_text(
            json.dumps(registry, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self.log_path.write_text(
            json.dumps([e.to_dict() for e in log_entries], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self.log.info(
            "Clarification complete: %d confirmed / %d total.",
            confirmed_count, len(log_entries),
        )
        return {
            "confirmed": confirmed_count,
            "total": len(log_entries),
            "auto": self.auto_clarify,
        }

    # ----- interactive -------------------------------------------------

    def _interactive(
        self,
        action: dict,
        classification: IntentClassification,
        idx: int,
        total: int,
    ) -> ClarificationEntry:
        rich = self._maybe_rich_console()
        target = action.get("target") or {}
        screenshot = action.get("frame")
        if self.open_screenshots and screenshot:
            self._open_screenshot(self.shots_dir / screenshot)

        # Header
        if rich:
            rich.rule(f"[bold]Action {idx}/{total}: {action.get('id')} ({action.get('type')})[/bold]")
        else:
            self._print(f"\n=== Action {idx}/{total}: {action.get('id')} ({action.get('type')}) ===")

        # Show context
        ctx_lines = [
            f"Frame:      {screenshot or 'n/a'}",
            f"Target:     hint={target.get('hint','?')!r} text={target.get('text','')!r} type={target.get('type','?')}",
            f"Heuristic:  {classification.type}  ({classification.description})",
            f"Expected:   {action.get('expected_diff', 'none')}",
        ]
        if action.get("type") == "type":
            ctx_lines.append(f"Value:      {action.get('value','')!r}")
        for line in ctx_lines:
            self._print(line)

        # 1. Goal
        goal = self._prompt(
            "Goal of this action (free text, ENTER to keep heuristic): ",
            default=classification.description,
        )

        # 2. STATIC / DYNAMIC
        prompt_type = (
            f"Type — [S]tatic / [D]ynamic [{classification.type[0]}]: "
        )
        ans = self._prompt(prompt_type, default=classification.type[0]).strip().lower()
        if ans.startswith("d"):
            kind = "DYNAMIC"
        elif ans.startswith("s"):
            kind = "STATIC"
        else:
            kind = classification.type

        variable_component = ""
        fixed_component = ""
        examples: list = []
        if kind == "DYNAMIC":
            variable_component = self._prompt(
                "  Variable component (what changes per run): ",
                default=(classification.parameters[0].name
                         if classification.parameters else ""),
            )
            fixed_component = self._prompt(
                "  Fixed component (what stays the same): ",
                default="UI structure",
            )
            existing_examples = []
            if classification.parameters:
                existing_examples = list(classification.parameters[0].example_values)
            ex_str = self._prompt(
                f"  Example values (comma-separated) [{','.join(map(str, existing_examples))}]: ",
                default=",".join(map(str, existing_examples)),
            )
            examples = [v.strip() for v in ex_str.split(",") if v.strip()]

        return ClarificationEntry(
            action_id=action.get("id", "?"),
            confirmed=True,
            type=kind,
            goal=goal,
            variable_component=variable_component,
            fixed_component=fixed_component,
            examples=examples,
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def _auto(self, c: IntentClassification) -> ClarificationEntry:
        examples: list = []
        var_comp = ""
        fixed_comp = "UI structure"
        if c.parameters:
            examples = list(c.parameters[0].example_values)
            var_comp = c.parameters[0].name
        return ClarificationEntry(
            action_id=c.action_id,
            confirmed=True,
            type=c.type,
            goal=c.description,
            variable_component=var_comp,
            fixed_component=fixed_comp,
            examples=examples,
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    # ----- helpers -----------------------------------------------------

    def _prompt(self, msg: str, default: str = "") -> str:
        try:
            ans = self._input(msg)
        except (EOFError, KeyboardInterrupt):
            return default
        return ans if ans else default

    def _open_screenshot(self, path: Path) -> None:
        if not path.is_file():
            return
        sys_name = platform.system().lower()
        try:
            if sys_name == "windows":
                # nosec B606 — file path is internal, not user-controlled
                import os
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys_name == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            self.log.warning("Could not open screenshot %s: %s", path, e)

    def _maybe_rich_console(self):
        try:
            from rich.console import Console  # type: ignore[import-not-found]

            return Console(file=sys.stdout)
        except ImportError:
            return None

    @staticmethod
    def _registry_to_classification(
        aid: str, entry: dict
    ) -> IntentClassification:
        params: list[IntentParameter] = []
        for p in entry.get("parameters", []) or []:
            params.append(IntentParameter(
                name=p.get("name", "value"),
                type=p.get("type", "string"),
                example_values=list(p.get("example_values", [])),
                range=list(p.get("range", []) or []) or None,
            ))
        return IntentClassification(
            action_id=aid,
            type=entry.get("type", "STATIC"),
            description=entry.get("description", ""),
            parameters=params,
            confirmed=bool(entry.get("confirmed", False)),
        )

    @staticmethod
    def _merge_registry(existing: dict, clar: ClarificationEntry) -> dict:
        out = dict(existing)
        out["type"] = clar.type
        out["description"] = clar.goal or out.get("description", "")
        out["confirmed"] = bool(clar.confirmed)
        if clar.type == "DYNAMIC":
            out.setdefault("parameters", [])
            if not out["parameters"]:
                out["parameters"] = [{
                    "name": clar.variable_component or "value",
                    "type": "string",
                    "example_values": list(clar.examples),
                }]
            else:
                out["parameters"][0]["example_values"] = list(clar.examples) or out["parameters"][0].get("example_values", [])
                if clar.variable_component:
                    out["parameters"][0]["name"] = clar.variable_component
            out["example_values"] = list(clar.examples)
            out["fixed_component"] = clar.fixed_component
        else:
            out["parameters"] = []
            out.pop("example_values", None)
            out.pop("fixed_component", None)
        return out

    @staticmethod
    def _classification_with_clar(
        c: IntentClassification, clar: ClarificationEntry
    ) -> IntentClassification:
        params = list(c.parameters)
        if clar.type == "DYNAMIC":
            if not params:
                params = [IntentParameter(
                    name=clar.variable_component or "value",
                    type="string",
                    example_values=list(clar.examples),
                )]
            else:
                params[0].example_values = list(clar.examples) or params[0].example_values
                if clar.variable_component:
                    params[0].name = clar.variable_component
        else:
            params = []
        return IntentClassification(
            action_id=c.action_id,
            type=clar.type,
            description=clar.goal or c.description,
            parameters=params,
            confirmed=clar.confirmed,
        )
