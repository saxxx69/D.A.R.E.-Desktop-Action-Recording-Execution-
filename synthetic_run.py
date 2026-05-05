# D.A.R.E. — Architecture

D.A.R.E. is a 7-stage pipeline. Each stage is a self-contained module that
reads its input from the previous stage's on-disk output, so any stage can
be re-run in isolation. The stages communicate via structured files inside
a per-run folder; nothing crosses module boundaries except files.

```
┌─────────┐  raw_events.jsonl  ┌────────────┐  ui_state +    ┌─────────────┐
│Recorder ├───────────────────►│ Normalizer ├───────────────►│ DSL         │
└─────────┘  screenshots/       │ + UI state │  state_diff +  │ Generator   │
                                │ + diff     │  action_graph  │             │
                                └────────────┘                └──────┬──────┘
                                                                     │
                                                       action.dsl.yaml
                                                                     ▼
┌──────────┐                  ┌──────────────────┐         ┌─────────────────┐
│ Executor │ ◄────────────────┤ Validator        │ ◄───────┤ Intent Layer    │
│ (live or │  validated DSL   │ (state-aware     │ intents │ + HITL Clarif.  │
│  dryrun) │                  │  + preview.mp4)  │         │                 │
└──────────┘                  └──────────────────┘         └─────────────────┘
```

## Modules

### 1. Recorder
Captures raw user input and visual state.

* Mouse: each click triggers an immediate screenshot and an event with
  `(x, y, button, timestamp, screenshot)`.
* Keyboard: events are buffered and flushed after 1.5 s of input quiescence,
  yielding a `(text, before_screenshot, after_screenshot)` triple.
* Screenshots embed metadata: resolution, cursor position, active window
  title, and SHA-256 content hash for deduplication.
* Auto-stop after 30 s of total inactivity. Hard-stop via double-`ESC`.

### 2. Normalizer (with UI State + State Diff submodules)
Converts raw events into a structured semantic representation.

1. **UI State Extractor** — runs vision (OpenCV contour detection, optional
   YOLOv8) plus OCR (Tesseract or PaddleOCR) on each screenshot, fuses
   results with confidence scoring, and assigns each element a stable ID
   `hash(text, bbox_relative, type)`.
2. **State Diff Engine** — matches elements between consecutive frames by
   ID first, then IoU > 0.7, then fuzzy text match (RapidFuzz). Emits
   `text_change`, `new_element`, `removed_element`, `moved_element`.
3. **Action Graph** — joins each event to the elements it acted on, with
   semantic targets (hint + vision template + relative position + expected
   diff) instead of raw coordinates.

### 3. DSL Generator
Produces a portable, executable YAML — `action.dsl.yaml` — with `WAIT_FOR`,
`CLICK`, `TYPE`, `VERIFY` actions. Each action declares its `expected_effect`
so any executor can verify success without context.

### 4. Intent Layer
Classifies each action as **STATIC** (always the same effect: click on a
named button) or **DYNAMIC** (a parameter varies: lot size, ticker, …).
Emits an `intent_registry.json` and a per-action markdown file under
`docs/intents/`.

### 5. Clarification Layer (Human-in-the-loop)
Walks the action list interactively, showing each screenshot and asking the
user to confirm: **goal**, **STATIC vs DYNAMIC**, and — if DYNAMIC — the
**variable component**, **fixed component**, and **example values**. Updates
intents on disk. A `--auto-clarify` flag falls back to heuristics for
non-interactive runs.

### 6. Validator
Simulates the DSL against the recorded UI state with composite confidence:

```
effect_confidence = target_confidence × diff_confidence × match_score
```

For each DYNAMIC action it generates one run per example value. For each run
it compares `expected_effect` against `observed_effects` and records a
`state_delta`. The validator also renders a real `preview.mp4` with overlay
(action label, target bbox, expected/observed match, confidence) using a
multi-tier fallback: FFmpeg ▶ OpenCV `VideoWriter` ▶ placeholder file.

### 7. Executor
Replays the DSL on a live desktop. Defaults to **dry-run** (prints intended
actions). With `--live-executor` it routes through `pydirectinput` on
Windows (which bypasses the input filtering used by trading platforms) and
falls back to `pyautogui` elsewhere. Pre/post conditions are verified per
action with retry strategies (vision → OCR → relative position).

## Per-run isolation

Each pipeline invocation writes to a unique folder under `runs/`. The
repository root is never written to. Run IDs are sortable and unique:
`YYYYMMDD_HHMMSS_<8 hex chars>`.

```
runs/<run_id>/
├── run.json
├── raw/
├── processed/
├── assets/{screenshots,templates}/
├── validation/
├── docs/intents/
└── logs/
```

The runs root can be relocated via `DARE_RUNS_ROOT`.

## Cross-mode operation

Every pipeline stage is exposed both as a CLI subcommand and as an MCP tool
(Phase 7). The MCP server uses `fastmcp` over stdio for compatibility with
any MCP client — Claude Code, Claude Desktop, Cursor, Continue, Cline, …

## Pipeline robustness

* Each stage is independently re-runnable. State lives on disk.
* Stage failures are recorded in `run.json` and never crash downstream
  stages — the pipeline aborts cleanly at the failure point.
* Optional system dependencies (Tesseract, FFmpeg, xdotool) degrade
  gracefully with explicit warnings, never silently.
