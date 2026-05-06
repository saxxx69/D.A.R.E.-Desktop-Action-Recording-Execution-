"""Tests for the HITL clarification layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dare.clarification.hitl import ClarificationLayer


def _make_run(tmp_path: Path) -> Path:
    """Build a run that already has an action_graph + intent_registry."""
    run = tmp_path / "run"
    (run / "processed").mkdir(parents=True)
    (run / "docs" / "intents").mkdir(parents=True)
    (run / "assets" / "screenshots").mkdir(parents=True)
    (run / "logs").mkdir(parents=True)

    actions = [
        {
            "id": "a0", "type": "click",
            "frame": "s_000000.png",
            "target": {"hint": "buy_button", "text": "BUY", "type": "button",
                       "element_id": "x1"},
            "expected_diff": "new_element",
            "intent_file": "docs/intents/a0.md",
        },
        {
            "id": "a1", "type": "type", "value": "0.25",
            "frame": "s_000000.png",
            "target": {"hint": "lot_input", "text": "", "type": "input",
                       "element_id": "x2"},
            "expected_diff": "text_change",
            "intent_file": "docs/intents/a1.md",
        },
    ]
    (run / "processed" / "action_graph.json").write_text(
        json.dumps({"actions": actions}, indent=2), encoding="utf-8"
    )
    registry = {
        "a0": {"type": "STATIC", "description": "Click on 'BUY' (button)",
               "confirmed": False},
        "a1": {"type": "DYNAMIC", "description": "Type variable into input",
               "confirmed": False,
               "parameters": [{"name": "numeric_value", "type": "float",
                               "example_values": [0.1, 0.25, 1.0],
                               "range": [0.01, 100.0]}],
               "example_values": [0.1, 0.25, 1.0]},
    }
    (run / "processed" / "intent_registry.json").write_text(
        json.dumps(registry, indent=2), encoding="utf-8"
    )
    return run


def test_auto_clarify_marks_all_confirmed(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    summary = ClarificationLayer(run, auto_clarify=True).run()
    assert summary["confirmed"] == 2
    reg = json.loads((run / "processed" / "intent_registry.json").read_text("utf-8"))
    for entry in reg.values():
        assert entry["confirmed"] is True
    log = json.loads((run / "processed" / "clarification_log.json").read_text("utf-8"))
    assert len(log) == 2
    assert all(e["confirmed"] for e in log)


def test_auto_clarify_preserves_dynamic_examples(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    ClarificationLayer(run, auto_clarify=True).run()
    reg = json.loads((run / "processed" / "intent_registry.json").read_text("utf-8"))
    a1 = reg["a1"]
    assert a1["type"] == "DYNAMIC"
    # Examples preserved
    assert 0.25 in a1["example_values"]


def test_auto_clarify_writes_markdown_with_confirmed(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    ClarificationLayer(run, auto_clarify=True).run()
    md = (run / "docs" / "intents" / "a0.md").read_text("utf-8")
    assert "## Confermato (HITL)" in md
    assert "yes" in md.split("## Confermato (HITL)")[1].splitlines()[1]


class _ScriptedInput:
    """Replays a fixed sequence of answers; raises if exhausted."""

    def __init__(self, answers: list[str]) -> None:
        self._a = list(answers)
        self._i = 0

    def __call__(self, _prompt: str) -> str:
        ans = self._a[self._i]
        self._i += 1
        return ans


def test_interactive_keeps_user_input(tmp_path: Path) -> None:
    """User accepts STATIC for a0, switches a1 from DYNAMIC->STATIC, but
    we still verify the merge logic. We script the answers."""
    run = _make_run(tmp_path)
    answers = [
        # Action a0 (STATIC): goal, type
        "Click the buy button",  # goal
        "S",                      # static
        # Action a1 (DYNAMIC): goal, type, variable, fixed, examples
        "Type lot size",          # goal
        "D",                      # dynamic
        "lot_size",               # variable
        "field format",           # fixed
        "0.1,0.5,1.0",            # examples
    ]
    layer = ClarificationLayer(
        run,
        auto_clarify=False,
        open_screenshots=False,
        input_fn=_ScriptedInput(answers),
        output_fn=lambda *a, **k: None,
    )
    summary = layer.run()
    assert summary["confirmed"] == 2
    log = json.loads((run / "processed" / "clarification_log.json").read_text("utf-8"))
    a0 = next(e for e in log if e["action_id"] == "a0")
    assert a0["type"] == "STATIC"
    assert a0["goal"] == "Click the buy button"
    a1 = next(e for e in log if e["action_id"] == "a1")
    assert a1["type"] == "DYNAMIC"
    assert a1["variable_component"] == "lot_size"
    assert a1["fixed_component"] == "field format"
    assert a1["examples"] == ["0.1", "0.5", "1.0"]


def test_interactive_user_can_change_static_to_dynamic(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    # Make a0 (STATIC heuristic) DYNAMIC interactively;
    # a1 (DYNAMIC) keeps all defaults (5 empty answers).
    answers = [
        "Goal a0",  "D",  "ticker",  "ui",  "AAPL,GOOG",   # a0 → DYNAMIC
        "",         "",   "",        "",    "",             # a1 → DYNAMIC (defaults)
    ]
    layer = ClarificationLayer(
        run, auto_clarify=False, open_screenshots=False,
        input_fn=_ScriptedInput(answers),
        output_fn=lambda *a, **k: None,
    )
    layer.run()
    reg = json.loads((run / "processed" / "intent_registry.json").read_text("utf-8"))
    assert reg["a0"]["type"] == "DYNAMIC"
    assert reg["a0"]["example_values"] == ["AAPL", "GOOG"]


def test_missing_action_graph_raises(tmp_path: Path) -> None:
    run = tmp_path / "empty_run"
    (run / "processed").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        ClarificationLayer(run, auto_clarify=True).run()


def test_missing_registry_raises(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "processed").mkdir(parents=True)
    (run / "processed" / "action_graph.json").write_text(
        '{"actions": []}', encoding="utf-8"
    )
    with pytest.raises(FileNotFoundError):
        ClarificationLayer(run, auto_clarify=True).run()
