"""D.A.R.E. command-line entrypoint.

Phase 1 exposes:

    dare --version
    dare doctor                     # platform capability report
    dare runs new                   # create a new run folder
    dare runs list                  # list existing runs
    dare runs show <run_id>         # print metadata for a run

Future phases will add: ``record``, ``normalize``, ``generate-dsl``,
``build-intents``, ``clarify``, ``validate``, ``execute``, and the global
``--mcp`` flag.

Exit codes
----------
0   success
2   usage error / not found
64  feature not implemented in current phase (EX_USAGE convention)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from dare import __version__
from dare.runs.run_manager import STAGES, RunManager, RunMetadata
from dare.utils import platform as plat
from dare.utils.logging import get_logger


# ----- helpers --------------------------------------------------------------


def _print_table(rows: list[dict], headers: list[str]) -> None:
    if not rows:
        print("(none)")
        return
    widths = [
        max(len(str(r.get(h, ""))) for r in rows + [{h: h for h in headers}])
        for h in headers
    ]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(
            "  ".join(
                str(r.get(h, "")).ljust(widths[i]) for i, h in enumerate(headers)
            )
        )


def _meta_summary(m: RunMetadata) -> dict:
    return {
        "id": m.id,
        "created_at": m.created_at,
        "status": m.status,
        "completed": f"{len(m.stages_completed)}/{len(STAGES)}",
        "current": m.current_stage or "-",
    }


# ----- subcommand handlers --------------------------------------------------


def cmd_doctor(_args: argparse.Namespace) -> int:
    rep = plat.report()
    print("D.A.R.E. platform report")
    print("-" * 40)
    for k, v in rep.to_dict().items():
        print(f"  {k:<16} {v}")
    warns = plat.warnings_for_current_platform()
    if warns:
        print("\nWarnings:")
        for w in warns:
            print(f"  ! {w}")
    else:
        print("\nNo warnings — platform looks good.")
    return 0


def cmd_runs_new(args: argparse.Namespace) -> int:
    rm = RunManager()
    meta = rm.create(notes=args.notes or "")
    print(meta.id)
    if args.verbose:
        print(f"  path: {rm.path(meta.id)}", file=sys.stderr)
    return 0


def cmd_runs_list(_args: argparse.Namespace) -> int:
    rm = RunManager()
    runs = rm.list_runs()
    rows = [_meta_summary(m) for m in runs]
    _print_table(
        rows, headers=["id", "created_at", "status", "completed", "current"]
    )
    return 0


def cmd_runs_show(args: argparse.Namespace) -> int:
    rm = RunManager()
    try:
        run_id = rm.resolve(args.run_id)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    meta = rm.metadata(run_id)
    print(meta.to_json())
    return 0


def cmd_not_yet(args: argparse.Namespace) -> int:
    name = getattr(args, "_cmd_name", "this command")
    phase = getattr(args, "_cmd_phase", "a future phase")
    print(
        f"error: '{name}' is not implemented in Phase 1 (lands in {phase}).",
        file=sys.stderr,
    )
    return 64


def cmd_mcp(_args: argparse.Namespace) -> int:
    """Start the D.A.R.E. MCP server on stdio."""
    try:
        from dare.mcp_server import serve
    except ImportError as e:
        print(f"error: {e}. Install with: pip install -e .[mcp]", file=sys.stderr)
        return 2
    return serve(transport="stdio")


def cmd_execute(args: argparse.Namespace) -> int:
    """Run action.dsl.yaml against the real desktop (or dry-run)."""
    try:
        from dare.executor import Executor
    except ImportError as e:
        print(f"error: executor deps missing ({e})", file=sys.stderr)
        return 2
    rm = RunManager()
    try:
        run_id = rm.resolve(args.run_id)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    params: dict = {}
    if args.param:
        for kv in args.param:
            if "=" not in kv:
                print(f"error: --param must be KEY=VALUE, got {kv!r}", file=sys.stderr)
                return 2
            k, v = kv.split("=", 1)
            params[k] = v

    rm.set_stage(run_id, "execute")
    try:
        summary = Executor(
            rm.path(run_id),
            live=args.live,
            params=params,
            action_delay_ms=args.action_delay_ms,
        ).run()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        rm.fail_stage(run_id, "execute", str(e))
        return 2
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        rm.fail_stage(run_id, "execute", str(e))
        return 1
    print(json.dumps(summary, indent=2))
    rm.complete_stage(run_id, "execute")
    return 0


def cmd_generate_dsl(args: argparse.Namespace) -> int:
    """Generate action.dsl.yaml from action_graph.json."""
    try:
        from dare.dsl import DSLGenerator
    except ImportError as e:
        print(f"error: dsl deps missing ({e}). Try: pip install -e .[dsl]",
              file=sys.stderr)
        return 2
    rm = RunManager()
    try:
        run_id = rm.resolve(args.run_id)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    rm.set_stage(run_id, "generate_dsl")
    try:
        summary = DSLGenerator(rm.path(run_id), wait_timeout_ms=args.wait_timeout).run()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        rm.fail_stage(run_id, "generate_dsl", str(e))
        return 2
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        rm.fail_stage(run_id, "generate_dsl", str(e))
        return 1
    print(json.dumps(summary, indent=2))
    rm.complete_stage(run_id, "generate_dsl")
    return 0


def cmd_build_intents(args: argparse.Namespace) -> int:
    """Classify actions STATIC/DYNAMIC, write intent registry + markdown."""
    try:
        from dare.intent import IntentBuilder
    except ImportError as e:
        print(f"error: intent deps missing ({e})", file=sys.stderr)
        return 2
    rm = RunManager()
    try:
        run_id = rm.resolve(args.run_id)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    rm.set_stage(run_id, "build_intents")
    try:
        summary = IntentBuilder(rm.path(run_id)).run()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        rm.fail_stage(run_id, "build_intents", str(e))
        return 2
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        rm.fail_stage(run_id, "build_intents", str(e))
        return 1
    print(json.dumps(summary, indent=2))
    rm.complete_stage(run_id, "build_intents")
    return 0


def cmd_normalize(args: argparse.Namespace) -> int:
    """Run the normalizer on a recorded run."""
    try:
        from dare.normalizer import Normalizer, UIStateExtractor
    except ImportError as e:
        print(
            f"error: normalizer dependencies missing ({e}). "
            "Install with: pip install -e .[vision,ocr]",
            file=sys.stderr,
        )
        return 2

    rm = RunManager()
    try:
        run_id = rm.resolve(args.run_id)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    run_dir = rm.path(run_id)

    rm.set_stage(run_id, "normalize")
    try:
        extractor = UIStateExtractor(
            min_area=args.min_area,
            ocr_lang=args.ocr_lang,
            ocr_min_confidence=args.ocr_min_confidence,
        )
        norm = Normalizer(run_dir=run_dir, extractor=extractor)
        summary = norm.run()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        rm.fail_stage(run_id, "normalize", str(e))
        return 2
    except Exception as e:
        print(f"error: normalization failed: {e}", file=sys.stderr)
        rm.fail_stage(run_id, "normalize", str(e))
        return 1

    print(json.dumps(summary, indent=2))
    rm.complete_stage(run_id, "normalize")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Run state-aware simulator + render preview video."""
    try:
        from dare.validator import Validator
    except ImportError as e:
        print(f"error: validator deps missing ({e})", file=sys.stderr)
        return 2
    rm = RunManager()
    try:
        run_id = rm.resolve(args.run_id)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    rm.set_stage(run_id, "validate")
    try:
        summary = Validator(
            rm.path(run_id),
            fps=args.fps,
            frames_per_step=args.frames_per_step,
        ).run()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        rm.fail_stage(run_id, "validate", str(e))
        return 2
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        rm.fail_stage(run_id, "validate", str(e))
        return 1
    print(json.dumps(summary, indent=2))
    rm.complete_stage(run_id, "validate")
    return 0


def cmd_clarify(args: argparse.Namespace) -> int:
    """HITL clarification of intents."""
    try:
        from dare.clarification import ClarificationLayer
    except ImportError as e:
        print(f"error: clarification deps missing ({e})", file=sys.stderr)
        return 2
    rm = RunManager()
    try:
        run_id = rm.resolve(args.run_id)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    rm.set_stage(run_id, "clarify")
    try:
        layer = ClarificationLayer(
            rm.path(run_id),
            auto_clarify=args.auto_clarify,
            open_screenshots=not args.no_open,
        )
        summary = layer.run()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        rm.fail_stage(run_id, "clarify", str(e))
        return 2
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        rm.fail_stage(run_id, "clarify", str(e))
        return 1
    print(json.dumps(summary, indent=2))
    rm.complete_stage(run_id, "clarify")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Run the validator on a normalized run."""
    try:
        from dare.validator import Validator
    except ImportError as e:
        print(f"error: validator deps missing ({e})", file=sys.stderr)
        return 2
    rm = RunManager()
    try:
        run_id = rm.resolve(args.run_id)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    rm.set_stage(run_id, "validate")
    try:
        summary = Validator(
            rm.path(run_id),
            fps=args.fps,
            frames_per_step=args.frames_per_step,
        ).run()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        rm.fail_stage(run_id, "validate", str(e))
        return 2
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        rm.fail_stage(run_id, "validate", str(e))
        return 1
    print(json.dumps(summary, indent=2))
    rm.complete_stage(run_id, "validate")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Run the recorder end-to-end on the current desktop.

    Imports the recorder lazily so users who only run Phase 1 commands
    don't need the heavy ``[recorder]`` extras installed.
    """
    # Lazy imports: keep doctor / runs commands working without recorder deps.
    try:
        from dare.recorder import Recorder, RecorderConfig
    except ImportError as e:
        print(
            f"error: recorder dependencies missing ({e}). "
            "Install with: pip install -e .[recorder,vision]",
            file=sys.stderr,
        )
        return 2

    rm = RunManager()
    if args.run_id:
        run_id = rm.resolve(args.run_id)
    else:
        run_id = rm.create(notes="recorded session").id
        print(f"Created run: {run_id}", file=sys.stderr)
    run_dir = rm.path(run_id)

    config = RecorderConfig(
        inactivity_seconds=args.idle,
        double_esc_window=args.double_esc_window,
        capture_mouse_move=args.capture_moves,
        monitor_index=args.monitor,
    )
    rec = Recorder(run_dir=run_dir, config=config)

    problems = rec.preflight()
    if problems and not args.force:
        print("Recorder preflight failed:", file=sys.stderr)
        for p in problems:
            print(f"  ! {p}", file=sys.stderr)
        print(
            "Pass --force to attempt the recording anyway.", file=sys.stderr
        )
        rm.fail_stage(run_id, "record", "; ".join(problems))
        return 2
    elif problems:
        for p in problems:
            print(f"warning: {p}", file=sys.stderr)

    rm.set_stage(run_id, "record")
    print(
        f"Recording → {run_dir}\n"
        f"  Stop conditions: {args.idle:.0f}s idle OR double-ESC "
        f"(within {args.double_esc_window:.2f}s)",
        file=sys.stderr,
    )
    try:
        rec.start()
        rec.start_listeners()
        rec.wait_until_stopped(timeout=args.timeout if args.timeout > 0 else None)
    except KeyboardInterrupt:
        print("\nrecorder: interrupted by user (Ctrl-C)", file=sys.stderr)
    finally:
        rec.close()

    stats = rec.stats
    if not stats["stop_reason"]:
        stats["stop_reason"] = "explicit_close"
    print(json.dumps(stats, indent=2))
    rm.complete_stage(run_id, "record")
    return 0


# ----- argparse glue --------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dare",
        description="D.A.R.E. — Desktop Action Recording & Execution",
    )
    p.add_argument(
        "--version", action="version", version=f"dare {__version__}"
    )
    p.add_argument(
        "--mcp",
        action="store_true",
        help="Run as an MCP server over stdio (Phase 7).",
    )

    sub = p.add_subparsers(dest="command")

    # doctor
    p_doctor = sub.add_parser(
        "doctor", help="Print platform capability report."
    )
    p_doctor.set_defaults(func=cmd_doctor)

    # runs
    p_runs = sub.add_parser("runs", help="Manage per-run folders.")
    runs_sub = p_runs.add_subparsers(dest="runs_command", required=True)

    p_new = runs_sub.add_parser("new", help="Create a new run folder.")
    p_new.add_argument(
        "--notes", default="", help="Free-form note saved in run metadata."
    )
    p_new.add_argument(
        "-v", "--verbose", action="store_true", help="Also print the run path."
    )
    p_new.set_defaults(func=cmd_runs_new)

    p_list = runs_sub.add_parser("list", help="List all runs.")
    p_list.set_defaults(func=cmd_runs_list)

    p_show = runs_sub.add_parser(
        "show", help="Show metadata for a run (latest if omitted)."
    )
    p_show.add_argument("run_id", nargs="?", default=None)
    p_show.set_defaults(func=cmd_runs_show)

    # record (Phase 2 — real implementation)
    p_rec = sub.add_parser(
        "record",
        help="Capture mouse/keyboard/screenshots until idle or double-ESC.",
    )
    p_rec.add_argument(
        "--run", dest="run_id", default=None,
        help="Append to an existing run (default: create a new one).",
    )
    p_rec.add_argument(
        "--idle", type=float, default=30.0,
        help="Auto-stop after this many seconds of inactivity (default: 30).",
    )
    p_rec.add_argument(
        "--double-esc-window", type=float, default=0.5,
        help="Max seconds between two ESC presses for hard-stop (default: 0.5).",
    )
    p_rec.add_argument(
        "--capture-moves", action="store_true",
        help="Also record mouse-move events (verbose).",
    )
    p_rec.add_argument(
        "--monitor", type=int, default=0,
        help="mss monitor index: 0 = all monitors (default), 1+ = specific.",
    )
    p_rec.add_argument(
        "--timeout", type=float, default=0.0,
        help="Max wall-clock seconds to wait for stop (0 = no limit).",
    )
    p_rec.add_argument(
        "--force", action="store_true",
        help="Bypass preflight failures (Wayland / headless).",
    )
    p_rec.set_defaults(func=cmd_record)

    # normalize (Phase 3)
    p_norm = sub.add_parser(
        "normalize",
        help="Run the normalizer on a recorded run.",
    )
    p_norm.add_argument("--run", dest="run_id", default=None,
                        help="Run id (default: latest).")
    p_norm.add_argument("--min-area", type=int, default=200,
                        help="Min vision bbox area in px² (default: 200).")
    p_norm.add_argument("--ocr-lang", default="eng",
                        help="Tesseract language code (default: eng).")
    p_norm.add_argument("--ocr-min-confidence", type=int, default=30,
                        help="Drop OCR words below this confidence (default: 30).")
    p_norm.set_defaults(func=cmd_normalize)

    # generate-dsl (Phase 4)
    p_dsl = sub.add_parser("generate-dsl", help="Produce action.dsl.yaml from action_graph.")
    p_dsl.add_argument("--run", dest="run_id", default=None)
    p_dsl.add_argument("--wait-timeout", type=int, default=3000,
                       help="WAIT_FOR timeout in ms (default: 3000).")
    p_dsl.set_defaults(func=cmd_generate_dsl)

    # build-intents (Phase 4)
    p_int = sub.add_parser("build-intents", help="Classify actions STATIC/DYNAMIC; write intent registry + markdown.")
    p_int.add_argument("--run", dest="run_id", default=None)
    p_int.set_defaults(func=cmd_build_intents)

    # clarify (Phase 5)
    p_clar = sub.add_parser("clarify", help="Human-in-the-loop intent disambiguation.")
    p_clar.add_argument("--run", dest="run_id", default=None)
    p_clar.add_argument("--auto-clarify", action="store_true",
                        help="Skip interactive prompts; stamp heuristic answers as confirmed.")
    p_clar.add_argument("--no-open", action="store_true",
                        help="Do not auto-open screenshots in the OS viewer.")
    p_clar.set_defaults(func=cmd_clarify)

    # validate (Phase 6)
    p_val = sub.add_parser("validate", help="Run state-aware simulator + render preview video.")
    p_val.add_argument("--run", dest="run_id", default=None)
    p_val.add_argument("--fps", type=int, default=4,
                       help="Preview video FPS (default: 4).")
    p_val.add_argument("--frames-per-step", type=int, default=4,
                       help="Video frames per simulation step (default: 4 = 1s/step at fps=4).")
    p_val.set_defaults(func=cmd_validate)

    # execute (Phase 7)
    p_exec = sub.add_parser("execute", help="Run action.dsl.yaml on the real desktop.")
    p_exec.add_argument("--run", dest="run_id", default=None)
    p_exec.add_argument("--live", action="store_true",
                        help="Actually drive mouse/keyboard. Default is dry-run.")
    p_exec.add_argument("--param", action="append", default=[],
                        help="Override a parameter as KEY=VALUE (repeatable).")
    p_exec.add_argument("--action-delay-ms", type=int, default=200,
                        help="Inter-action delay in ms (default: 200).")
    p_exec.set_defaults(func=cmd_execute)

    # All seven phases now wired — no remaining placeholders.
    placeholders: list[tuple[str, str, str]] = []
    for name, phase, helptext in placeholders:
        sp = sub.add_parser(name, help=f"[{phase}] {helptext}")
        sp.set_defaults(func=cmd_not_yet, _cmd_name=name, _cmd_phase=phase)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # Global --mcp short-circuits subcommand dispatch.
    if getattr(args, "mcp", False):
        return cmd_mcp(args)

    if not getattr(args, "command", None):
        build_parser().print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
