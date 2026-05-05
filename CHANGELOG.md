"""Tests for the validator (simulator + renderer + orchestrator)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dare.validator.simulator import Simulator
from dare.validator.validator import Validator
from dare.validator.video_renderer import VideoRenderer, VideoBackend


def _make_normalized_run(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    (run / "processed").mkdir(parents=True)
    (run / "validation").mkdir(parents=True)
    (run / "assets" / "screenshots").mkdir(parents=True)
    (run / "logs").mkdir(parents=True)

    ui_state = {"frames": [
        {"frame_id": "s_000000.png", "screenshot": "s_000000.png", "elements": [
            {"id": "e_buy", "type": "button", "text": "BUY",
             "bbox": [100, 100, 80, 30], "center": [140, 115],
             "confidence": 0.92, "sources": ["vision", "ocr"], "interactable": True},
            {"id": "e_lot", "type": "input", "text": "",
             "bbox": [300, 200, 180, 30], "center": [390, 215],
             "confidence": 0.85, "sources": ["vision"], "interactable": True},
        ]},
        {"frame_id": "s_000001.png", "screenshot": "s_000001.png", "elements": [
            {"id": "e_buy", "type": "button", "text": "BUY",
             "bbox": [100, 100, 80, 30], "center": [140, 115],
             "confidence": 0.92, "sources": ["vision", "ocr"], "interactable": True},
            {"id": "e_lot025", "type": "input", "text": "0.25",
             "bbox": [300, 200, 180, 30], "center": [390, 215],
             "confidence": 0.88, "sources": ["vision", "ocr"], "interactable": True},
        ]},
    ]}
    state_diff = {"diffs": [{
        "from_frame": "s_000000.png", "to_frame": "s_000001.png",
        "changes": [{"type": "text_change", "element_id": "e_lot025",
                     "before": "", "after": "0.25", "confidence": 0.85}],
    }]}
    action_graph = {"actions": [
        {"id": "a0", "type": "click", "raw_xy": [140, 115], "frame": "s_000000.png",
         "target": {"hint": "buy", "text": "BUY", "type": "button",
                    "element_id": "e_buy", "bbox": [100, 100, 80, 30],
                    "relative_position": [0.1, 0.1], "confidence": 0.92},
         "expected_diff": "text_change"},
        {"id": "a1", "type": "type", "value": "0.25", "frame": "s_000000.png",
         "target": {"hint": "lot", "text": "", "type": "input",
                    "element_id": "e_lot", "bbox": [300, 200, 180, 30],
                    "relative_position": [0.3, 0.4], "confidence": 0.85},
         "expected_diff": "text_change"},
    ]}
    intent_registry = {
        "a0": {"type": "STATIC", "description": "Click BUY", "confirmed": True},
        "a1": {"type": "DYNAMIC", "description": "Type lot", "confirmed": True,
               "parameters": [{"name": "numeric_value", "type": "float",
                               "example_values": [0.1, 1.0], "range": [0.01, 10.0]}],
               "example_values": [0.1, 1.0]},
    }
    (run / "processed" / "ui_state.json").write_text(json.dumps(ui_state), "utf-8")
    (run / "processed" / "state_diff.json").write_text(json.dumps(state_diff), "utf-8")
    (run / "processed" / "action_graph.json").write_text(json.dumps(action_graph), "utf-8")
    (run / "processed" / "intent_registry.json").write_text(json.dumps(intent_registry), "utf-8")
    return run


def test_simulator_finds_target_by_element_id(tmp_path: Path) -> None:
    run = _make_normalized_run(tmp_path)
    runs = Simulator(run).simulate_all()
    a0 = next(r for r in runs if r.label == "original").steps[0]
    assert a0.target_found and a0.target_confidence > 0.5


def test_simulator_records_observed_effects(tmp_path: Path) -> None:
    run = _make_normalized_run(tmp_path)
    a1 = next(r for r in Simulator(run).simulate_all() if r.label == "original").steps[1]
    assert "text_change" in a1.observed_effects
    assert a1.effect_match is True


def test_simulator_composite_confidence(tmp_path: Path) -> None:
    run = _make_normalized_run(tmp_path)
    a1 = next(r for r in Simulator(run).simulate_all() if r.label == "original").steps[1]
    assert abs(a1.effect_confidence - a1.target_confidence * a1.diff_confidence * a1.match_score) < 1e-3


def test_simulator_one_variant_per_example(tmp_path: Path) -> None:
    run = _make_normalized_run(tmp_path)
    runs = Simulator(run).simulate_all()
    labels = sorted(r.label for r in runs)
    assert len(runs) == 3
    assert any("a1=0.1" in l for l in labels)
    assert any("a1=1.0" in l for l in labels)


def test_simulator_marks_failure_when_target_missing(tmp_path: Path) -> None:
    run = _make_normalized_run(tmp_path)
    g = json.loads((run / "processed" / "action_graph.json").read_text("utf-8"))
    g["actions"][0]["target"]["element_id"] = "missing"
    g["actions"][0]["target"]["text"] = ""
    g["actions"][0]["target"]["hint"] = "nonexistent_hint"
    (run / "processed" / "action_graph.json").write_text(json.dumps(g), "utf-8")
    a0 = next(r for r in Simulator(run).simulate_all() if r.label == "original").steps[0]
    assert a0.status == "fail"
    assert a0.fail_reason == "target_not_found"


def test_simulator_handles_no_diff_gracefully(tmp_path: Path) -> None:
    run = _make_normalized_run(tmp_path)
    (run / "processed" / "state_diff.json").write_text('{"diffs": []}', "utf-8")
    assert len(Simulator(run).simulate_all()) >= 1


def test_renderer_writes_output_file(tmp_path: Path) -> None:
    out = tmp_path / "preview.mp4"
    shots = tmp_path / "shots"
    shots.mkdir()
    fake_sim = {"runs": [{
        "label": "original", "params": {}, "status": "ok",
        "steps": [{
            "action_id": "a0", "action": "click", "target_hint": "buy",
            "target_found": True, "target_confidence": 0.9, "target_bbox": None,
            "expected_effect": "none", "observed_effects": [], "effect_match": True,
            "diff_confidence": 0.5, "match_score": 1.0, "effect_confidence": 0.45,
            "frame_before": None, "frame_after": None, "status": "ok", "fail_reason": "",
        }],
    }]}
    result = VideoRenderer(shots, out, fps=2, frames_per_step=1).render(fake_sim)
    assert out.is_file()
    assert result.path == out


def test_renderer_pick_backend_returns_enum(tmp_path: Path) -> None:
    shots = tmp_path / "shots"
    shots.mkdir()
    backend = VideoRenderer(shots, tmp_path / "preview.mp4")._pick_backend()
    assert isinstance(backend, VideoBackend)


def test_validator_writes_simulation_and_preview(tmp_path: Path) -> None:
    run = _make_normalized_run(tmp_path)
    summary = Validator(run, fps=2, frames_per_step=1).run()
    assert (run / "validation" / "simulation.json").is_file()
    assert (run / "validation" / "preview.mp4").is_file()
    sim = json.loads((run / "validation" / "simulation.json").read_text("utf-8"))
    assert summary["runs"] == len(sim["runs"])


def test_validator_summary_counts(tmp_path: Path) -> None:
    run = _make_normalized_run(tmp_path)
    summary = Validator(run, fps=2, frames_per_step=1).run()
    assert summary["ok"] + summary["fail"] == summary["runs"]


def test_validator_missing_action_graph_raises(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "processed").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        Validator(run).run()
