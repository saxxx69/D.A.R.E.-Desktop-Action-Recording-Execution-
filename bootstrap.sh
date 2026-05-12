"""Tests for the Executor and MCP server tool wrappers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest

from dare.executor.executor import Executor, _parse_minimal_yaml
from dare.executor.retry import RetryStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_with_dsl(tmp_path: Path) -> Path:
    """Build a minimal run with a DSL file but no real screenshots."""
    run = tmp_path / "run"
    (run / "processed").mkdir(parents=True)
    (run / "logs").mkdir(parents=True)
    dsl = """version: '1.0'
context:
  environment: desktop
  resolution: adaptive
actions:
  -
    action: WAIT_FOR
    target:
      hint: buy_button
      text: BUY
      type: button
      element_id: btn_buy
      relative_position:
        - 0.5
        - 0.5
      confidence: 0.9
    timeout: 1000
  -
    action: CLICK
    id: a0
    target:
      hint: buy_button
      text: BUY
      type: button
      element_id: btn_buy
    expected_effect: new_element
  -
    action: TYPE
    id: a1
    target:
      hint: lot_input
      type: input
      element_id: inp_lot
      relative_position:
        - 0.4
        - 0.5
    value: '0.25'
    expected_effect: text_change
  -
    action: VERIFY
    condition: 'text_present:0.25'
"""
    (run / "processed" / "action.dsl.yaml").write_text(dsl, encoding="utf-8")
    return run


# ---------------------------------------------------------------------------
# Executor — dry-run mode
# ---------------------------------------------------------------------------


def test_executor_dry_run_does_not_touch_desktop(tmp_path: Path) -> None:
    run = _make_run_with_dsl(tmp_path)
    summary = Executor(run, live=False).run()
    assert summary["mode"] == "dry_run"
    assert summary["total"] == 4
    assert summary["fail"] == 0
    log = json.loads((run / "processed" / "execution_log.json").read_text("utf-8"))
    assert all(e["status"] == "dry_run" for e in log)


def test_executor_dry_run_logs_each_step(tmp_path: Path) -> None:
    run = _make_run_with_dsl(tmp_path)
    Executor(run, live=False).run()
    log = json.loads((run / "processed" / "execution_log.json").read_text("utf-8"))
    actions = [e["action"] for e in log]
    assert any("WAIT_FOR" in a for a in actions)
    assert any("CLICK" in a for a in actions)
    assert any("TYPE" in a for a in actions)
    assert any("VERIFY" in a for a in actions)


def test_executor_dry_run_substitutes_params(tmp_path: Path) -> None:
    run = _make_run_with_dsl(tmp_path)
    summary = Executor(run, live=False, params={"a1": "0.50"}).run()
    log = json.loads((run / "processed" / "execution_log.json").read_text("utf-8"))
    type_action = next(e for e in log if e["action"].startswith("TYPE"))
    assert "0.50" in type_action["action"]


def test_executor_missing_dsl_raises(tmp_path: Path) -> None:
    run = tmp_path / "no_dsl"
    (run / "processed").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        Executor(run, live=False).run()


def test_executor_handles_unknown_action(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "processed").mkdir(parents=True)
    (run / "logs").mkdir(parents=True)
    # YAML with an unknown action verb
    dsl = """version: '1.0'
actions:
  -
    action: UNKNOWN_VERB
    target:
      hint: foo
"""
    (run / "processed" / "action.dsl.yaml").write_text(dsl, encoding="utf-8")
    summary = Executor(run, live=False).run()
    log = json.loads((run / "processed" / "execution_log.json").read_text("utf-8"))
    assert log[0]["status"] == "skipped"


# ---------------------------------------------------------------------------
# Minimal YAML fallback parser
# ---------------------------------------------------------------------------


def test_minimal_yaml_parser_handles_dsl_schema() -> None:
    import textwrap
    text = textwrap.dedent("""\
        version: '1.0'
        actions:
          -
            action: CLICK
            id: a0
            target:
              hint: foo
              relative_position:
                - 0.5
                - 0.5
        """)
    doc = _parse_minimal_yaml(text)
    assert doc["version"] == "1.0"
    assert len(doc["actions"]) == 1
    a = doc["actions"][0]
    assert a["action"] == "CLICK"
    assert a["id"] == "a0"
    assert a["target"]["hint"] == "foo"
    assert a["target"]["relative_position"] == [0.5, 0.5]


# ---------------------------------------------------------------------------
# Retry strategy
# ---------------------------------------------------------------------------


def test_retry_relative_position_uses_screen_size(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image
    rs = RetryStrategy()
    screen = Image.new("RGB", (1000, 800), color="white")
    target = {"relative_position": [0.5, 0.5]}
    hit = rs.resolve(target, tmp_path, screen)
    assert hit is not None
    x, y, conf = hit
    assert x == 500 and y == 400
    assert 0.0 < conf <= 1.0


def test_retry_returns_none_when_screen_missing(tmp_path: Path) -> None:
    rs = RetryStrategy()
    target = {"relative_position": [0.5, 0.5]}
    assert rs.resolve(target, tmp_path, None) is None


def test_retry_returns_none_when_target_empty(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image
    rs = RetryStrategy()
    screen = Image.new("RGB", (100, 100), color="white")
    assert rs.resolve({}, tmp_path, screen) is None


# ---------------------------------------------------------------------------
# MCP server tool wrappers
# ---------------------------------------------------------------------------


def test_mcp_module_imports() -> None:
    """The mcp_server module must be importable even if fastmcp is not installed."""
    import dare.mcp_server as m
    # Tool functions must be callables
    for name in (
        "tool_record", "tool_normalize_run", "tool_generate_dsl",
        "tool_build_intents", "tool_clarify", "tool_validate_run",
        "tool_execute_run", "tool_run_full_pipeline",
    ):
        assert callable(getattr(m, name))


def test_mcp_pipeline_orchestrator_skip_record(tmp_path: Path, monkeypatch) -> None:
    """tool_run_full_pipeline with skip_record=True works on an existing run."""
    pytest.importorskip("PIL")

    # Build a run that's already been recorded (synthetic).
    monkeypatch.setenv("DARE_RUNS_ROOT", str(tmp_path / "runs"))
    from dare.runs.run_manager import RunManager
    from PIL import Image, ImageDraw

    rm = RunManager()
    rid = rm.create(notes="test").id
    rd = rm.path(rid)

    # Synthesize raw events + screenshots
    img1 = Image.new("RGB", (640, 480), color="white")
    ImageDraw.Draw(img1).rectangle([100, 100, 200, 140], outline="black", width=3)
    img1.save(rd / "assets" / "screenshots" / "s_000000.png")
    img2 = Image.new("RGB", (640, 480), color="white")
    ImageDraw.Draw(img2).rectangle([100, 100, 200, 140], outline="black", width=3)
    ImageDraw.Draw(img2).rectangle([300, 300, 500, 360], outline="green", width=3)
    img2.save(rd / "assets" / "screenshots" / "s_000001.png")

    events = [
        {"type": "screenshot", "filename": "s_000000.png"},
        {"type": "mouse_click", "x": 150, "y": 120,
         "button": "Button.left", "timestamp": 1.0,
         "screenshot": "s_000000.png"},
        {"type": "keyboard_input", "text": "0.25",
         "keys": ["0", ".", "2", "5"],
         "start_ts": 2.0, "end_ts": 2.5,
         "before_screenshot": "s_000000.png",
         "after_screenshot": "s_000001.png"},
    ]
    with (rd / "raw" / "raw_events.jsonl").open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

    from dare.mcp_server import tool_run_full_pipeline
    result = tool_run_full_pipeline(skip_record=True, run_id=rid)
    assert result["run_id"] == rid
    assert result["normalize"]["ok"] is True
    assert result["generate_dsl"]["ok"] is True
    assert result["build_intents"]["ok"] is True
    assert result["clarify"]["ok"] is True
    assert result["validate"]["ok"] is True
    # All artifacts present
    assert (rd / "processed" / "ui_state.json").is_file()
    assert (rd / "processed" / "action_graph.json").is_file()
    assert (rd / "processed" / "action.dsl.yaml").is_file()
    assert (rd / "processed" / "intent_registry.json").is_file()
    assert (rd / "validation" / "simulation.json").is_file()
    assert (rd / "validation" / "preview.mp4").is_file()


def test_mcp_execute_run_dry_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DARE_RUNS_ROOT", str(tmp_path / "runs"))
    from dare.runs.run_manager import RunManager
    from dare.mcp_server import tool_execute_run

    rm = RunManager()
    rid = rm.create(notes="test").id
    run = rm.path(rid)
    # Need a DSL
    (run / "processed").mkdir(parents=True, exist_ok=True)
    dsl = """version: '1.0'
actions:
  -
    action: CLICK
    id: a0
    target:
      hint: buy
"""
    (run / "processed" / "action.dsl.yaml").write_text(dsl, encoding="utf-8")
    res = tool_execute_run(run_id=rid, live=False)
    assert res["ok"] is True
    assert res["summary"]["mode"] == "dry_run"
