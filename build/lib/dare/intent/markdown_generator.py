"""Generate per-action intent markdown files."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from dare.intent.classifier import IntentClassification


_TEMPLATE = """# ACTION INTENT

## ID
{action_id}

## Tipo
{type}

## Descrizione
{description}

## Confermato (HITL)
{confirmed}

## Parametri
{params_block}

## Esempi
{examples_block}

## Verifica
{verify}

## Fallback
retry input
"""


class IntentMarkdownGenerator:
    def __init__(self, intents_dir: Path) -> None:
        self.dir = Path(intents_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, classification: IntentClassification, verify: str = "OCR field == value") -> Path:
        params_block = self._format_params(classification)
        examples_block = self._format_examples(classification)
        body = _TEMPLATE.format(
            action_id=classification.action_id,
            type=classification.type,
            description=classification.description or "(no description)",
            confirmed="yes" if classification.confirmed else "no",
            params_block=params_block,
            examples_block=examples_block,
            verify=verify,
        )
        out = self.dir / f"{classification.action_id}.md"
        out.write_text(body, encoding="utf-8")
        return out

    def write_all(
        self, classifications: Iterable[IntentClassification]
    ) -> list[Path]:
        return [self.write(c) for c in classifications]

    @staticmethod
    def _format_params(c: IntentClassification) -> str:
        if not c.parameters:
            return "(none — STATIC)"
        lines = []
        for p in c.parameters:
            lines.append(f"- name: {p.name}")
            lines.append(f"  type: {p.type}")
            if p.range:
                lines.append(f"  range: {p.range[0]} – {p.range[1]}")
        return "\n".join(lines)

    @staticmethod
    def _format_examples(c: IntentClassification) -> str:
        ex: list = []
        for p in c.parameters:
            ex.extend(p.example_values)
        if not ex:
            return "(none)"
        return "\n".join(f"- {v}" for v in ex)
