"""Tests for ``RunManager`` — per-run folder isolation and metadata."""

from __future__ import annotations

from pathlib import Path

import pytest

from dare.runs.run_manager import (
    RUNS_ROOT_ENV,
    STAGES,
    SUBDIRS,
    RunManager,
)


@pytest.fixture
def runs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(RUNS_ROOT_ENV, str(tmp_path))
    return tmp_path


def test_create_makes_full_layout(runs_root: Path) -> None:
    rm = RunManager()
    meta = rm.create()
    run_dir = rm.path(meta.id)
    assert run_dir.is_dir()
    for sub in SUBDIRS:
        assert (run_dir / sub).is_dir(), f"missing subdir: {sub}"
    assert (run_dir / "run.json").is_file()


def test_metadata_roundtrip(runs_root: Path) -> None:
    rm = RunManager()
    meta = rm.create(notes="hello")
    loaded = rm.metadata(meta.id)
    assert loaded.id == meta.id
    assert loaded.notes == "hello"
    assert loaded.status == "created"
    assert loaded.stages_completed == []
    assert loaded.stages_failed == []


def test_list_runs_sorted(runs_root: Path) -> None:
    rm = RunManager()
    a = rm.create()
    b = rm.create()
    c = rm.create()
    listed = [m.id for m in rm.list_runs()]
    assert listed == sorted([a.id, b.id, c.id])


def test_latest_returns_most_recent(runs_root: Path) -> None:
    rm = RunManager()
    assert rm.latest() is None
    rm.create()
    second = rm.create()
    latest = rm.latest()
    assert latest is not None
    assert latest.id == second.id


def test_stage_lifecycle(runs_root: Path) -> None:
    rm = RunManager()
    meta = rm.create()
    rm.set_stage(meta.id, "record")
    assert rm.metadata(meta.id).status == "running"
    assert rm.metadata(meta.id).current_stage == "record"

    rm.complete_stage(meta.id, "record")
    after = rm.metadata(meta.id)
    assert "record" in after.stages_completed
    assert after.current_stage is None

    rm.set_stage(meta.id, "normalize")
    rm.fail_stage(meta.id, "normalize", "OCR missing")
    final = rm.metadata(meta.id)
    assert "normalize" in final.stages_failed
    assert final.status == "failed"
    assert "OCR missing" in final.notes


def test_completing_all_stages_marks_completed(runs_root: Path) -> None:
    rm = RunManager()
    meta = rm.create()
    for stage in STAGES:
        rm.set_stage(meta.id, stage)
        rm.complete_stage(meta.id, stage)
    assert rm.metadata(meta.id).status == "completed"


def test_complete_clears_prior_failure(runs_root: Path) -> None:
    rm = RunManager()
    meta = rm.create()
    rm.fail_stage(meta.id, "record", "transient")
    assert "record" in rm.metadata(meta.id).stages_failed
    rm.complete_stage(meta.id, "record")
    final = rm.metadata(meta.id)
    assert "record" in final.stages_completed
    assert "record" not in final.stages_failed


def test_set_stage_rejects_unknown(runs_root: Path) -> None:
    rm = RunManager()
    meta = rm.create()
    with pytest.raises(ValueError):
        rm.set_stage(meta.id, "not_a_real_stage")


def test_resolve_falls_back_to_latest(runs_root: Path) -> None:
    rm = RunManager()
    rm.create()
    second = rm.create()
    assert rm.resolve(None) == second.id


def test_resolve_with_no_runs_raises(runs_root: Path) -> None:
    rm = RunManager()
    with pytest.raises(FileNotFoundError):
        rm.resolve(None)


def test_resolve_validates_id_format(runs_root: Path) -> None:
    rm = RunManager()
    with pytest.raises(ValueError):
        rm.resolve("not-a-valid-id")


def test_path_for_missing_run_raises(runs_root: Path) -> None:
    rm = RunManager()
    # Valid format, but no folder on disk → must raise FileNotFoundError
    # (not ValueError, which is reserved for malformed IDs).
    with pytest.raises(FileNotFoundError):
        rm.path("20240101_000000000000_000000")


def test_run_id_is_unique(runs_root: Path) -> None:
    rm = RunManager()
    ids = {rm.create().id for _ in range(5)}
    assert len(ids) == 5


def test_rapid_succession_creates_have_strict_ordering(runs_root: Path) -> None:
    """Regression test: two runs created back-to-back must remain in
    creation order under string sort. Fixed by switching the run-id
    timestamp to microsecond precision."""
    rm = RunManager()
    ids = [rm.create().id for _ in range(20)]
    assert ids == sorted(ids), (
        "creation order should match lexicographic sort of run IDs"
    )


def test_subpath_creates_when_missing(runs_root: Path) -> None:
    rm = RunManager()
    meta = rm.create()
    p = rm.subpath(meta.id, "raw")
    assert p.is_dir()
    # Idempotent
    p2 = rm.subpath(meta.id, "raw")
    assert p == p2


def test_abort_marks_status(runs_root: Path) -> None:
    rm = RunManager()
    meta = rm.create()
    rm.set_stage(meta.id, "record")
    rm.abort(meta.id, reason="user pressed double-ESC")
    final = rm.metadata(meta.id)
    assert final.status == "aborted"
    assert final.current_stage is None
    assert "double-ESC" in final.notes
