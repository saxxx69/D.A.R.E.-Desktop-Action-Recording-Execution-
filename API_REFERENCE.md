# D.A.R.E. — FAQ

## Why not just use AutoHotkey / xdotool / pyautogui?

Those tools record and replay coordinates. D.A.R.E. records **semantic
actions** (button labels, input fields, expected effects) and replays
them through a vision/OCR-aware executor with three retry strategies.
The result tolerates UI shifts, resolution changes and language
variations that defeat coordinate-based replay.

## Why a DSL instead of just running Python?

The `action.dsl.yaml` file is the **portable, inspectable, diffable**
contract between recording and execution. It is human-readable, can be
hand-edited, version-controlled, and consumed by alternative executors
(including the MCP server, CI tooling, or future agent frameworks).

## Can I edit `action.dsl.yaml` by hand?

Yes. The schema is in `AGENTS.md §4`. The executor will not be confused
by manual edits; just keep the `target` block intact (the executor needs
something to look for — `text`, `element_id`, or `vision`).

## What's the difference between STATIC and DYNAMIC actions?

- **STATIC**: same on every run. Click "BUY", press OK, etc.
- **DYNAMIC**: parameterised. Type `0.25` (which next time might be
  `1.50`), enter a ticker symbol, paste a path. Each DYNAMIC action
  carries a parameter description and example values.

The executor accepts a `params` mapping that overrides DYNAMIC values
per run. The MCP server's `execute_run` tool exposes the same.

## Can I run D.A.R.E. on a CI server?

Yes, but only the **non-recording** stages: `normalize → generate-dsl →
build-intents → clarify --auto-clarify → validate → execute (dry-run)`.
You need an existing recording (or a synthetic one — see
`examples/synthetic_run.py`) to start from.

For recording in CI, use `xvfb-run` with synthetic event injection
through the recorder's `feed_*` API.

## Does it work over SSH / VNC / RDP?

Recording: yes if the remote session is a real X11 desktop (VNC,
NoMachine, Xrdp). No if it's a "session-less" remote shell.

Execution: same — it must run inside a graphical session.

## How do I preserve recordings across runs of the pipeline?

Each `dare` invocation acts on a single run folder under `runs/<id>/`.
Move that folder anywhere (rename, archive, ship) and re-run subsequent
stages on it. Set `DARE_RUNS_ROOT` to point at archived locations.

## Can the recorder run in the background while I work?

Yes — that's the intended workflow. Start `dare record --idle 30`, do
your task naturally, leave the terminal idle for 30 seconds when done
(or press ESC twice within 0.5 s). The recorder produces complete
artifacts on stop.

## How do I add a non-English UI?

Pass `--ocr-lang <code>` to `dare normalize`. Tesseract language packs
must be installed separately — for Italian:
```bash
sudo apt install tesseract-ocr-ita    # Linux
brew install tesseract-lang           # macOS (all languages)
```
Use the ISO codes Tesseract expects (e.g. `ita`, `deu`, `fra`, `spa`).

## What is the `confidence` field used for?

Three different confidences travel through the pipeline:
- `target_confidence`: how sure are we that the right element was
  located on the before-frame.
- `diff_confidence`: how sure are we about the recorded UI changes.
- `match_score`: did the observed effect match the expected one (1 or 0).

The validator emits a composite `effect_confidence = target × diff ×
match`. The executor uses these to decide between vision/OCR/relative
fallbacks.

## How big are recordings?

Typical 30-second recording: 30–80 MB of PNG screenshots, < 100 KB of
JSONL. The screenshots dominate. They compress well in tar.gz / zip
(typically 3–5×).

## How do I delete old runs?

```bash
make clean-runs                  # nukes everything in runs/
rm -rf runs/<specific_id>        # one run
```

Runs are independent — no shared global state.
