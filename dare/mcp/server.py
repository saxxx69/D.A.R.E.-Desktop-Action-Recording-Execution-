"""MCP server for D.A.R.E.

Exposes the entire pipeline as MCP tools so any MCP-compatible client
(Claude Code, Claude Desktop, Cursor, Continue, Cline, …) can drive it.

Tools
-----
* ``record``             — start a recorder session.
* ``normalize_run``      — run normalizer on a recorded run.
* ``generate_dsl``       — emit ``action.dsl.yaml``.
* ``build_intents``      — classify actions, write registry + markdowns.
* ``clarify``            — HITL clarification (auto mode for MCP).
* ``validate_run``       — simulate + render preview.
* ``execute_run``        — run the DSL (dry-run by default).
* ``run_full_pipeline``  — record + normalize + DSL + intents + validate.

Transport: stdio (the only widely-supported MCP transport for desktop
clients today). Started via ``dare --mcp`` or ``python -m dare.mcp_server``.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from dare.runs.run_manager import RunManager
from dare.utils.logging import get_logger

_LOG = get_logger("dare.mcp")


# ---------------------------------------------------------------------------
# Tool implementations (importable for tests; do not start the MCP transport)
# ---------------------------------------------------------------------------


def tool_record(
    idle_seconds: float = 30.0,
    capture_moves: bool = False,
    monitor: int = 0,
    force: bool = False,
    notes: str = "mcp recorded session",
) -> dict:
    """Run the recorder synchronously. Returns the run_id and stats."""
    from dare.recorder import Recorder, RecorderConfig

    rm = RunManager()
    run_id = rm.create(notes=notes).id
    run_dir = rm.path(run_id)
    cfg = RecorderConfig(
        inactivity_seconds=idle_seconds,
        capture_mouse_move=capture_moves,
        monitor_index=monitor,
    )
    rec = Recorder(run_dir=run_dir, config=cfg)
    problems = rec.preflight()
    if problems and not force:
        rm.fail_stage(run_id, "record", "; ".join(problems))
        return {"ok": False, "run_id": run_id, "errors": problems}
    rm.set_stage(run_id, "record")
    rec.start()
    rec.start_listeners()
    rec.wait_until_stopped()
    rec.close()
    rm.complete_stage(run_id, "record")
    return {"ok": True, "run_id": run_id, "stats": rec.stats}


def tool_normalize_run(run_id: Optional[str] = None) -> dict:
    from dare.normalizer import Normalizer

    rm = RunManager()
    rid = rm.resolve(run_id)
    rm.set_stage(rid, "normalize")
    try:
        summary = Normalizer(rm.path(rid)).run()
    except Exception as e:
        rm.fail_stage(rid, "normalize", str(e))
        return {"ok": False, "run_id": rid, "error": str(e)}
    rm.complete_stage(rid, "normalize")
    return {"ok": True, "run_id": rid, "summary": summary}


def tool_generate_dsl(run_id: Optional[str] = None) -> dict:
    from dare.dsl import DSLGenerator

    rm = RunManager()
    rid = rm.resolve(run_id)
    rm.set_stage(rid, "generate_dsl")
    try:
        summary = DSLGenerator(rm.path(rid)).run()
    except Exception as e:
        rm.fail_stage(rid, "generate_dsl", str(e))
        return {"ok": False, "run_id": rid, "error": str(e)}
    rm.complete_stage(rid, "generate_dsl")
    return {"ok": True, "run_id": rid, "summary": summary}


def tool_build_intents(run_id: Optional[str] = None) -> dict:
    from dare.intent import IntentBuilder

    rm = RunManager()
    rid = rm.resolve(run_id)
    rm.set_stage(rid, "build_intents")
    try:
        summary = IntentBuilder(rm.path(rid)).run()
    except Exception as e:
        rm.fail_stage(rid, "build_intents", str(e))
        return {"ok": False, "run_id": rid, "error": str(e)}
    rm.complete_stage(rid, "build_intents")
    return {"ok": True, "run_id": rid, "summary": summary}


def tool_clarify(run_id: Optional[str] = None, auto: bool = True) -> dict:
    """In MCP context default to auto-clarify (no interactive TTY)."""
    from dare.clarification import ClarificationLayer

    rm = RunManager()
    rid = rm.resolve(run_id)
    rm.set_stage(rid, "clarify")
    try:
        layer = ClarificationLayer(
            rm.path(rid), auto_clarify=auto, open_screenshots=False
        )
        summary = layer.run()
    except Exception as e:
        rm.fail_stage(rid, "clarify", str(e))
        return {"ok": False, "run_id": rid, "error": str(e)}
    rm.complete_stage(rid, "clarify")
    return {"ok": True, "run_id": rid, "summary": summary}


def tool_validate_run(
    run_id: Optional[str] = None,
    fps: int = 4,
    frames_per_step: int = 4,
) -> dict:
    from dare.validator import Validator

    rm = RunManager()
    rid = rm.resolve(run_id)
    rm.set_stage(rid, "validate")
    try:
        summary = Validator(
            rm.path(rid), fps=fps, frames_per_step=frames_per_step
        ).run()
    except Exception as e:
        rm.fail_stage(rid, "validate", str(e))
        return {"ok": False, "run_id": rid, "error": str(e)}
    rm.complete_stage(rid, "validate")
    return {"ok": True, "run_id": rid, "summary": summary}


def tool_execute_run(
    run_id: Optional[str] = None,
    live: bool = False,
    params: Optional[dict] = None,
) -> dict:
    from dare.executor import Executor

    rm = RunManager()
    rid = rm.resolve(run_id)
    rm.set_stage(rid, "execute")
    try:
        summary = Executor(
            rm.path(rid), live=live, params=params or {}
        ).run()
    except Exception as e:
        rm.fail_stage(rid, "execute", str(e))
        return {"ok": False, "run_id": rid, "error": str(e)}
    rm.complete_stage(rid, "execute")
    return {"ok": True, "run_id": rid, "summary": summary}


def tool_run_full_pipeline(
    idle_seconds: float = 30.0,
    capture_moves: bool = False,
    force: bool = False,
    skip_record: bool = False,
    run_id: Optional[str] = None,
) -> dict:
    """Run the full pipeline: record (optional) → normalize → DSL → intents
    → clarify (auto) → validate. Stops after validate; ``execute_run`` is a
    separate intentional step.
    """
    out: dict = {}
    if not skip_record:
        rec = tool_record(idle_seconds=idle_seconds,
                          capture_moves=capture_moves, force=force)
        out["record"] = rec
        if not rec.get("ok"):
            return out
        run_id = rec["run_id"]
    if run_id is None:
        rm = RunManager()
        run_id = rm.resolve(None)
    out["run_id"] = run_id
    out["normalize"] = tool_normalize_run(run_id)
    if not out["normalize"].get("ok"):
        return out
    out["generate_dsl"] = tool_generate_dsl(run_id)
    if not out["generate_dsl"].get("ok"):
        return out
    out["build_intents"] = tool_build_intents(run_id)
    if not out["build_intents"].get("ok"):
        return out
    out["clarify"] = tool_clarify(run_id, auto=True)
    out["validate"] = tool_validate_run(run_id)
    return out


# ---------------------------------------------------------------------------
# fastmcp transport
# ---------------------------------------------------------------------------


def serve(transport: str = "stdio") -> int:
    """Start the MCP server. Returns the process exit code."""
    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
    except ImportError:
        try:
            from fastmcp import FastMCP  # type: ignore[import-not-found]
        except ImportError:
            print(
                "error: MCP server requires 'mcp' or 'fastmcp'. "
                "Install with: pip install -e .[mcp]",
                file=sys.stderr,
            )
            return 2

    app = FastMCP("dare")

    # Register every tool. We use thin wrappers so fastmcp can introspect
    # Python type hints for schema generation.

    @app.tool()
    def record(
        idle_seconds: float = 30.0,
        capture_moves: bool = False,
        monitor: int = 0,
        force: bool = False,
        notes: str = "mcp recorded session",
    ) -> dict:
        """Capture a desktop session (mouse / keyboard / screenshots).

        Stops automatically after ``idle_seconds`` of inactivity or a
        double-ESC hard-stop. Returns the run_id and event counts.
        """
        return tool_record(
            idle_seconds=idle_seconds, capture_moves=capture_moves,
            monitor=monitor, force=force, notes=notes,
        )

    @app.tool()
    def normalize_run(run_id: Optional[str] = None) -> dict:
        """Convert raw events into ui_state / state_diff / action_graph."""
        return tool_normalize_run(run_id)

    @app.tool()
    def generate_dsl(run_id: Optional[str] = None) -> dict:
        """Emit ``action.dsl.yaml`` from action_graph."""
        return tool_generate_dsl(run_id)

    @app.tool()
    def build_intents(run_id: Optional[str] = None) -> dict:
        """Classify actions STATIC/DYNAMIC; write intent_registry + markdowns."""
        return tool_build_intents(run_id)

    @app.tool()
    def clarify(run_id: Optional[str] = None, auto: bool = True) -> dict:
        """HITL clarification (auto mode for MCP context)."""
        return tool_clarify(run_id, auto=auto)

    @app.tool()
    def validate_run(
        run_id: Optional[str] = None, fps: int = 4, frames_per_step: int = 4
    ) -> dict:
        """State-aware simulation + preview.mp4 rendering."""
        return tool_validate_run(run_id, fps=fps, frames_per_step=frames_per_step)

    @app.tool()
    def execute_run(
        run_id: Optional[str] = None,
        live: bool = False,
        params: Optional[dict] = None,
    ) -> dict:
        """Execute action.dsl.yaml. Defaults to dry-run (no real input)."""
        return tool_execute_run(run_id, live=live, params=params)

    @app.tool()
    def run_full_pipeline(
        idle_seconds: float = 30.0,
        capture_moves: bool = False,
        force: bool = False,
        skip_record: bool = False,
        run_id: Optional[str] = None,
    ) -> dict:
        """One-shot orchestration: record → normalize → DSL → intents →
        clarify (auto) → validate. Does NOT execute; that is a separate
        intentional step via ``execute_run``."""
        return tool_run_full_pipeline(
            idle_seconds=idle_seconds, capture_moves=capture_moves,
            force=force, skip_record=skip_record, run_id=run_id,
        )

    _LOG.info("Starting D.A.R.E. MCP server on %s.", transport)
    try:
        # FastMCP's run() blocks until the client disconnects.
        app.run(transport=transport)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as e:  # pragma: no cover
        _LOG.exception("MCP server crashed: %s", e)
        return 1


# Allow `python -m dare.mcp_server`
def main() -> int:
    return serve()


if __name__ == "__main__":
    sys.exit(main())
