# D.A.R.E. — Python API Reference

Every CLI subcommand is also a Python class you can drive directly. This
is the recommended path when embedding D.A.R.E. in a larger application
or extending the pipeline.

All classes follow the same shape: take a `run_dir: Path`, optional
configuration, and expose a single `run() -> dict` method that returns a
JSON-serializable summary.

## `dare.runs.run_manager`

```python
from dare.runs.run_manager import RunManager

rm = RunManager()                           # uses $DARE_RUNS_ROOT or ./runs/
meta = rm.create(notes="my recording")
run_id = meta.id                            # e.g. "20260105_120000123456_a1b2c3"
run_dir = rm.path(run_id)                   # Path to runs/<id>/

# Lifecycle
rm.set_stage(run_id, "record")              # mark current stage
rm.complete_stage(run_id, "record")         # mark stage complete
rm.fail_stage(run_id, "record", "reason")   # mark failed + record reason

# Listing / resolving
rm.list()                                   # list[RunMetadata], newest first
rm.resolve(None)                            # latest run id
rm.resolve("20260105_120000123456_a1b2c3")  # validates and returns id
```

Stages: `STAGES = ["record", "normalize", "generate_dsl", "build_intents",
"clarify", "validate", "execute"]` — a run is `completed` when all are
done.

## `dare.recorder` (Phase 2)

```python
from dare.recorder import Recorder, RecorderConfig
from pathlib import Path

config = RecorderConfig(
    inactivity_seconds=30.0,
    double_esc_window=0.5,
    keyboard_flush_idle=1.5,
    capture_mouse_move=False,
    monitor_index=0,                        # 0 = all monitors
)
rec = Recorder(run_dir=Path("runs/<id>"), config=config)

problems = rec.preflight()                  # list[str], empty if OK
rec.start()                                 # opens the JSONL file
rec.start_listeners()                       # attach pynput (live mode)
rec.wait_until_stopped(timeout=None)        # blocks
rec.close()                                 # flushes + writes stats.json

# Test-friendly: bypass listeners and feed events directly
rec.feed_mouse_click(x=100, y=200, button="Button.left")
rec.feed_keyboard(key_repr="a", char="a")
```

Output: `<run>/raw/raw_events.jsonl` + `<run>/assets/screenshots/*.png` +
`<run>/raw/stats.json`.

## `dare.normalizer` (Phase 3)

```python
from dare.normalizer import Normalizer, UIStateExtractor, StateDiffEngine

extractor = UIStateExtractor(
    min_area=200,
    ocr_lang="eng",
    ocr_min_confidence=30,
)
norm = Normalizer(run_dir=run_dir, extractor=extractor)
summary = norm.run()
# {'frames': 2, 'elements_total': 4, 'diffs': 1, 'actions': 2}
```

Lower-level primitives:

```python
from dare.normalizer.element_matcher import (
    bbox_iou, bbox_relative, fuzzy_text_similarity, stable_element_id,
)

iou = bbox_iou((10, 10, 50, 50), (20, 20, 50, 50))   # 0.0 .. 1.0
sid = stable_element_id("BUY", (100, 100, 80, 30), "button", (1920, 1080))
# "abc123def456"  — survives 1px shifts and resolution changes
```

Outputs: `processed/{ui_state, state_diff, action_graph}.json` plus
`assets/templates/<aid>_target.png` per interactive action.

## `dare.dsl` (Phase 4a)

```python
from dare.dsl import DSLGenerator

gen = DSLGenerator(run_dir=run_dir, wait_timeout_ms=3000)
gen.run()    # writes processed/action.dsl.yaml
```

YAML emission uses `pyyaml` if installed, else a hand-rolled stdlib
emitter that handles the project's schema.

## `dare.intent` (Phase 4b)

```python
from dare.intent import IntentBuilder, IntentClassifier, classify_action

builder = IntentBuilder(run_dir=run_dir)
summary = builder.run()
# {'static': 1, 'dynamic': 1, 'registry': '...', 'intents_dir': '...'}

# Per-action use:
clas = classify_action(action_dict)
# IntentClassification(type="DYNAMIC", parameters=[IntentParameter(...)])
```

Heuristics:
- Click on text matching common labels (BUY/SELL/OK/...) → STATIC
- Click on element with `element_id` → STATIC
- Type numeric → DYNAMIC float/int with auto-derived `range`
- Type URL/path → DYNAMIC url/path
- Type alphanumeric → DYNAMIC string
- Type fixed phrase → STATIC

## `dare.clarification` (Phase 5)

```python
from dare.clarification import ClarificationLayer

layer = ClarificationLayer(
    run_dir=run_dir,
    auto_clarify=False,                # True for batch mode
    open_screenshots=True,             # auto-open in OS viewer
    input_fn=input,                    # injectable for tests
    output_fn=print,
)
summary = layer.run()
# {'confirmed': 2, 'total': 2, 'auto': False}
```

Persists to: `processed/intent_registry.json` (updated in place),
`processed/clarification_log.json` (audit), `docs/intents/<aid>.md`
(rewritten).

## `dare.validator` (Phase 6)

```python
from dare.validator import Validator, Simulator, VideoRenderer

# All-in-one
val = Validator(run_dir=run_dir, fps=4, frames_per_step=4)
summary = val.run()
# {'runs': 3, 'ok': 3, 'fail': 0, 'preview': {'backend': 'ffmpeg', ...}}

# Lower-level: run only the simulator
sim = Simulator(run_dir=run_dir)
runs = sim.simulate_all()                  # list[SimulationRun]
for r in runs:
    print(r.label, r.status, len(r.steps))

# Render a custom report
renderer = VideoRenderer(
    screenshots_dir=run_dir / "assets" / "screenshots",
    out_path=run_dir / "validation" / "preview.mp4",
    fps=4, frames_per_step=4,
)
result = renderer.render(simulation_dict)  # RenderResult
```

Renderer backend selection: `ffmpeg` (preferred, H.264) → `opencv`
(mp4v) → `placeholder` (text marker).

Effect confidence: `target_confidence × diff_confidence × match_score`.

## `dare.executor` (Phase 7)

```python
from dare.executor import Executor

ex = Executor(
    run_dir=run_dir,
    live=False,                          # True = real input synthesis
    action_delay_ms=200,
    params={"a1": "0.50"},               # override DYNAMIC values
)
summary = ex.run()
# {'mode': 'dry_run', 'total': 4, 'ok': 4, 'fail': 0,
#  'log': '<run>/processed/execution_log.json'}
```

Dry-run is the default. Live mode uses `pyautogui` (cross-platform) or
`pydirectinput` (Windows, for apps that filter SendInput).

Retry strategy (`dare.executor.retry.RetryStrategy`):
1. `vision` — match the saved template via OpenCV `matchTemplate`.
2. `ocr` — re-run OCR on the current screen and match by text.
3. `relative` — fall back to `relative_position * resolution`.

## `dare.mcp_server`

```python
from dare.mcp_server import serve

serve(transport="stdio")    # blocks; serves the 8 MCP tools
```

Tools:
- `record` (start a session — only useful for live desktops)
- `normalize_run`
- `generate_dsl`
- `build_intents`
- `clarify` (auto mode for MCP)
- `validate_run`
- `execute_run` (dry-run by default)
- `run_full_pipeline` (orchestrates 2→7)

All tools take `run_id: Optional[str]` (defaults to latest) and return
a dict.

## `dare.utils`

```python
from dare.utils import platform as plat
from dare.utils.logging import get_logger

plat.get_os()                # "linux" | "macos" | "windows"
plat.is_wayland()            # bool
plat.is_headless()           # bool
plat.has_tesseract()         # bool
plat.has_ffmpeg()            # bool

log = get_logger("my_module", run_dir=run_dir)   # writes to runs/<id>/logs/
```

## Environment variables

| Variable           | Effect |
|--------------------|--------|
| `DARE_RUNS_ROOT`   | Override the runs directory (default `./runs/`) |
| `DARE_LOG_LEVEL`   | One of DEBUG / INFO / WARNING / ERROR (default INFO) |
| `TESSDATA_PREFIX`  | Standard pytesseract override; respected if set |
