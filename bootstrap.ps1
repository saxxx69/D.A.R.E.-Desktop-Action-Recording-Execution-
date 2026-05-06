"""Smoke tests for the CLI surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dare.runs.run_manager import RUNS_ROOT_ENV
from dare.server import build_parser, main


@pytest.fixture(autouse=True)
def _runs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(RUNS_ROOT_ENV, str(tmp_path))
    return tmp_path


def test_parser_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--help"])
    assert exc.value.code == 0


def test_version_flag_exits_zero(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "dare" in out


def test_runs_new_creates_run(capsys: pytest.CaptureFixture) -> None:
    rc = main(["runs", "new"])
    out = capsys.readouterr().out.strip()
    assert rc == 0
    parts = out.split("_")
    assert len(parts) == 3, f"expected 3 parts, got {parts!r}"
    # YYYYMMDD_HHMMSSffffff_<6hex>
    assert len(parts[0]) == 8 and parts[0].isdigit()
    assert len(parts[1]) == 12 and parts[1].isdigit()
    assert len(parts[2]) == 6 and all(c in "0123456789abcdef" for c in parts[2])


def test_runs_list_after_create(capsys: pytest.CaptureFixture) -> None:
    main(["runs", "new"])
    capsys.readouterr()  # discard the new-run output
    rc = main(["runs", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "id" in out and "status" in out


def test_runs_show_latest(capsys: pytest.CaptureFixture) -> None:
    main(["runs", "new"])
    capsys.readouterr()
    rc = main(["runs", "show"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["status"] == "created"


def test_runs_show_missing_id_returns_2(capsys: pytest.CaptureFixture) -> None:
    # Valid format, but no folder on disk → exercises the not-found path.
    rc = main(["runs", "show", "20240101_000000000000_000000"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "not found" in err.lower()


def test_doctor_runs_clean(capsys: pytest.CaptureFixture) -> None:
    rc = main(["doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "platform report" in out.lower()


def test_record_help_lists_flags(capsys: pytest.CaptureFixture) -> None:
    """The record subcommand exposes its Phase 2 flags in --help."""
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["record", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--idle", "--double-esc-window", "--monitor", "--force"):
        assert flag in out


def test_validate_help_lists_flags(capsys: pytest.CaptureFixture) -> None:
    """The validate subcommand exposes its Phase 6 flags."""
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["validate", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--fps", "--frames-per-step"):
        assert flag in out


def test_mcp_flag_attempts_to_start_server() -> None:
    """--mcp now invokes cmd_mcp; without fastmcp installed it returns 2."""
    rc = main(["--mcp"])
    assert rc in (0, 2)  # 0 if fastmcp installed, 2 otherwise


def test_no_command_prints_help(capsys: pytest.CaptureFixture) -> None:
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "usage:" in out.lower()
