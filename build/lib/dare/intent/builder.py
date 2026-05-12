"""Intent builder — orchestrator for the Intent Layer.

Reads ``<run>/processed/action_graph.json``, classifies every action, and
writes:

* ``<run>/processed/intent_registry.json``
* ``<run>/docs/intents/<action_id>.md`` (one per action)

Per-action entries in ``action_graph.json`` are also annotated in place with
their ``intent_file`` reference so downstream stages can find the markdown
file directly.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from dare.intent.classifier import IntentClassification, IntentClassifier
from dare.intent.markdown_generator import IntentMarkdownGenerator
from dare.utils.logging import get_logger


class IntentBuilder:
    def __init__(
        self,
        run_dir: Path,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.action_graph_path = self.run_dir / "processed" / "action_graph.json"
        self.registry_path = self.run_dir / "processed" / "intent_registry.json"
        self.intents_dir = self.run_dir / "docs" / "intents"
        self.intents_dir.mkdir(parents=True, exist_ok=True)
        self.classifier = IntentClassifier()
        self.md_gen = IntentMarkdownGenerator(self.intents_dir)
        self.log = logger or get_logger("dare.intent", run_dir=self.run_dir)

    def run(self) -> dict:
        if not self.action_graph_path.is_file():
            raise FileNotFoundError(
                f"action_graph.json not found at {self.action_graph_path}"
            )
        graph = json.loads(self.action_graph_path.read_text(encoding="utf-8"))
        actions = graph.get("actions", [])
        classifications = self.classifier.classify_all(actions)

        # Annotate the action graph in place
        for action, c in zip(actions, classifications):
            md_rel = f"docs/intents/{c.action_id}.md"
            action["intent_file"] = md_rel
        self.action_graph_path.write_text(
            json.dumps(graph, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Write markdown files
        self.md_gen.write_all(classifications)

        # Write registry
        registry = self._build_registry(classifications)
        self.registry_path.write_text(
            json.dumps(registry, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        n_static = sum(1 for c in classifications if c.type == "STATIC")
        n_dynamic = sum(1 for c in classifications if c.type == "DYNAMIC")
        self.log.info(
            "Intent build complete: %d STATIC / %d DYNAMIC (%d markdown files).",
            n_static, n_dynamic, len(classifications),
        )
        return {
            "static": n_static,
            "dynamic": n_dynamic,
            "registry": str(self.registry_path),
            "intents_dir": str(self.intents_dir),
        }

    @staticmethod
    def _build_registry(classifications: list[IntentClassification]) -> dict:
        out: dict[str, dict] = {}
        for c in classifications:
            entry: dict = {"type": c.type, "description": c.description, "confirmed": c.confirmed}
            if c.type == "DYNAMIC":
                entry["parameters"] = [p.to_dict() for p in c.parameters]
                # Also flatten the example_values for convenience
                example_values: list = []
                for p in c.parameters:
                    example_values.extend(p.example_values)
                entry["example_values"] = example_values
            out[c.action_id] = entry
        return out
