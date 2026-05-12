#!/usr/bin/env python3
"""End-to-end synthetic demo of the D.A.R.E. pipeline.

Produces a complete run from scratch — fake screenshots + fake events —
then walks every stage (normalize → DSL → intents → clarify → validate →
execute dry-run) and prints a structured summary.

This script needs **no real desktop**: it generates two PNG frames with
Pillow and synthesizes the JSONL events that the recorder would have
emitted. It is the reference fixture for verifying that a fresh install
of D.A.R.E. is fully functional.

Usage:
    python examples/synthetic_run.py
    python examples/synthetic_run.py --runs-root /tmp/dare_demo
    make demo
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


# --- pretty printing -------------------------------------------------------

def _color(s: str, code: str) -> str:
    return f"\033[{code}m{s}\033[0m" if sys.stdout.isatty() else s

def header(s: str) -> None:
    print(_color(f"\n=== {s} ===", "1;36"))

def ok(s: str) -> None:
    print(_color(f"  ✓ {s}", "32"))

def fail(s: str) -> None:
    print(_color(f"  ✘ {s}", "1;31"))


# --- synthetic fixture builder --------------------------------------------

def build_synthetic_run(runs_root: Path) -> str:
    """Create a fake run with 2 screenshots and 3 events.

    Mirrors what the live recorder would produce after the user clicks
    a "BUY" button and types ``0.25`` into a lot-size input.
    """
    try:
        from PIL import Image, ImageDraw  # type: ignore[import-not-found]
    except ImportError:
        print(
            "error: this demo needs Pillow. Install with:\n"
            "  pip install -e .[vision]   (or .[all])",
            file=sys.stderr,
        )
        sys.exit(2)

    # Use the runs CLI just like a user would, so the metadata is real.
    env = os.environ.copy()
    env["DARE_RUNS_ROOT"] = str(runs_root)

    out = subprocess.run(
        [sys.executable, "-m", "dare.server", "runs", "new",
         "--notes", "synthetic demo"],
        capture_output=True, text=True, env=env, check=True,
    )
    # The CLI prints the run id on stdout
    run_id = out.stdout.strip().splitlines()[-1]
    run_dir = runs_root / run_id
    raw_dir = run_dir / "raw"
    shots_dir = run_dir / "assets" / "screenshots"
    raw_dir.mkdir(parents=True, exist_ok=True)
    shots_dir.mkdir(parents=True, exist_ok=True)

    # Frame 1: BUY button + empty input
    img1 = Image.new("RGB", (800, 600), "white")
    d = ImageDraw.Draw(img1)
    d.rectangle([100, 100, 200, 140], outline="black", width=3)
    d.text((110, 110), "BUY", fill="black")
    d.rectangle([300, 200, 480, 240], outline="black", width=2)
    img1.save(shots_dir / "s_000000.png")

    # Frame 2: input now contains "0.25"
    img2 = Image.new("RGB", (800, 600), "white")
    d = ImageDraw.Draw(img2)
    d.rectangle([100, 100, 200, 140], outline="black", width=3)
    d.text((110, 110), "BUY", fill="black")
    d.rectangle([300, 200, 480, 240], outline="black", width=2)
    d.text((310, 210), "0.25", fill="black")
    img2.save(shots_dir / "s_000001.png")

    events = [
        {"type": "screenshot", "filename": "s_000000.png"},
        {
            "type": "mouse_click", "x": 150, "y": 120,
            "button": "Button.left", "timestamp": 1.0,
            "screenshot": "s_000000.png",
        },
        {
            "type": "keyboard_input", "text": "0.25",
            "keys": ["0", ".", "2", "5"],
            "start_ts": 2.0, "end_ts": 2.5,
            "before_screenshot": "s_000000.png",
            "after_screenshot": "s_000001.png",
        },
    ]
    with (raw_dir / "raw_events.jsonl").open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    return run_id


# --- pipeline runner -------------------------------------------------------

def run_stage(name: str, args: list[str], runs_root: Path) -> dict:
    """Invoke a CLI subcommand and parse its JSON output (last block)."""
    env = os.environ.copy()
    env["DARE_RUNS_ROOT"] = str(runs_root)
    result = subprocess.run(
        [sys.executable, "-m", "dare.server", *args],
        capture_output=True, text=True, env=env,
    )
    if result.returncode != 0:
        fail(f"{name} failed (exit {result.returncode})")
        sys.stderr.write(result.stderr)
        sys.exit(result.returncode)
    # Find the last JSON object on stdout — every cmd_* prints one.
    text = result.stdout.strip()
    if not text:
        return {}
    try:
        # The CLI prints exactly one JSON document per stage, after any logs.
        # Find the last "{" at column 0 and parse from there.
        lines = text.splitlines()
        start = next((i for i in range(len(lines) - 1, -1, -1)
                     if lines[i].startswith("{")), 0)
        return json.loads("\n".join(lines[start:]))
    except json.JSONDecodeError:
        return {"raw": text}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs-root", default=None,
        help="Override DARE_RUNS_ROOT for this demo (default: ./runs/).",
    )
    parser.add_argument(
        "--keep", action="store_true",
        help="Do not delete the demo run on completion.",
    )
    args = parser.parse_args()

    runs_root = Path(args.runs_root or "runs").resolve()
    runs_root.mkdir(parents=True, exist_ok=True)

    header("Building synthetic recording")
    run_id = build_synthetic_run(runs_root)
    ok(f"run_id = {run_id}")
    ok(f"run_dir = {runs_root / run_id}")

    stages = [
        ("normalize",     ["normalize", "--run", run_id]),
        ("generate-dsl",  ["generate-dsl", "--run", run_id]),
        ("build-intents", ["build-intents", "--run", run_id]),
        ("clarify",       ["clarify", "--run", run_id, "--auto-clarify",
                           "--no-open"]),
        ("validate",      ["validate", "--run", run_id, "--fps", "2",
                           "--frames-per-step", "2"]),
        ("execute",       ["execute", "--run", run_id]),  # dry-run
    ]
    for name, argv in stages:
        header(name)
        summary = run_stage(name, argv, runs_root)
        for k, v in summary.items():
            print(f"    {k}: {v}")
        ok(f"{name} done")

    header("runs show")
    show = run_stage("show", ["runs", "show", run_id], runs_root)
    print(json.dumps(show, indent=2))

    header("Artifacts")
    rd = runs_root / run_id
    expected = [
        rd / "raw" / "raw_events.jsonl",
        rd / "processed" / "ui_state.json",
        rd / "processed" / "state_diff.json",
        rd / "processed" / "action_graph.json",
        rd / "processed" / "action.dsl.yaml",
        rd / "processed" / "intent_registry.json",
        rd / "processed" / "clarification_log.json",
        rd / "processed" / "execution_log.json",
        rd / "validation" / "simulation.json",
        rd / "validation" / "preview.mp4",
    ]
    missing = [p for p in expected if not p.is_file()]
    if missing:
        for p in missing:
            fail(f"missing: {p.relative_to(rd)}")
        return 1
    for p in expected:
        size = p.stat().st_size
        ok(f"{p.relative_to(rd)} ({size:,} bytes)")

    header("✅  Synthetic pipeline complete")
    print(f"   Inspect:  {rd}")
    print(f"   Preview:  {rd / 'validation' / 'preview.mp4'}")
    print(f"   DSL:      {rd / 'processed' / 'action.dsl.yaml'}")

    if not args.keep:
        shutil.rmtree(rd, ignore_errors=True)
        ok("cleaned up demo run (pass --keep to retain)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
