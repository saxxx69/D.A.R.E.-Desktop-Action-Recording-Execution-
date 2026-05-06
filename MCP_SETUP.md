# D.A.R.E. — Troubleshooting

A practical, failure-mode-first guide. If something doesn't behave as
expected, find your symptom in the table of contents and follow the
corresponding section.

## Table of contents

- [Install fails](#install-fails)
- [`pytest` fails](#pytest-fails)
- [`dare doctor` warnings](#dare-doctor-warnings)
- [Recorder records 0 events](#recorder-records-0-events)
- [Recorder is slow](#recorder-is-slow)
- [Normalizer finds 0 elements](#normalizer-finds-0-elements)
- [DSL has no `value` for type actions](#dsl-has-no-value-for-type-actions)
- [`preview.mp4` is a text file](#previewmp4-is-a-text-file)
- [Executor finds wrong target](#executor-finds-wrong-target)
- [MCP server won't start](#mcp-server-wont-start)
- [Permission errors on macOS](#permission-errors-on-macos)
- [Wayland](#wayland)
- [Headless VPS](#headless-vps)

---

## Install fails

Symptoms: `pip install -e ".[all,dev]"` errors out, often on
`opencv-python`, `mss` or `pyautogui`.

**Recovery path** (in order; stop at the first that works):

1. Make sure you're on Python ≥ 3.10:
   ```bash
   python3 --version
   ```
2. Upgrade build tooling:
   ```bash
   .venv/bin/pip install --upgrade pip setuptools wheel
   ```
3. Install only the dev extra and add others incrementally:
   ```bash
   .venv/bin/pip install -e ".[dev]"
   .venv/bin/pip install -e ".[recorder]"
   .venv/bin/pip install -e ".[vision]"
   .venv/bin/pip install -e ".[ocr]"
   .venv/bin/pip install -e ".[exec]"
   .venv/bin/pip install -e ".[hitl]"
   .venv/bin/pip install -e ".[dsl]"
   .venv/bin/pip install -e ".[video]"
   .venv/bin/pip install -e ".[mcp]"
   ```
   The first failing extra tells you which system tool is missing.
4. Linux only: many Python wheels need a system toolchain. Install:
   ```bash
   sudo apt install build-essential libssl-dev libffi-dev python3-dev \
                    libgl1 libglib2.0-0 tesseract-ocr ffmpeg xdotool
   ```
5. macOS only: install command-line tools:
   ```bash
   xcode-select --install
   brew install tesseract ffmpeg
   ```

---

## `pytest` fails

The full suite is **133 passed in ~5 seconds** on a clean install. If yours
fails:

| Failing test                          | Likely cause                               | Fix |
|---------------------------------------|--------------------------------------------|-----|
| `test_screenshot::*`                  | `Pillow` missing                           | `pip install -e .[vision]` |
| `test_normalizer::*`                  | `Pillow` or OpenCV missing                 | `pip install -e .[vision,ocr]` |
| `test_recorder::test_idle_timeout_*`  | Slow VM — flaky on heavily loaded systems  | Re-run; consider `pytest --reruns 2` |
| `test_executor_mcp::*`                | Optional executor extras missing           | `pip install -e .[exec,mcp]` |
| `test_validator::test_renderer_*`     | OpenCV missing                             | `pip install -e .[video]` (and ffmpeg) |

Run the suite with `-v` to see which specific tests fail, and
`--no-header --tb=short` for a tighter view.

---

## `dare doctor` warnings

`dare doctor` checks the runtime environment and prints non-fatal warnings.
Each line is independent — fix only what you need:

| Warning                              | What it means                                | Fix |
|--------------------------------------|----------------------------------------------|-----|
| Wayland session detected             | Recorder cannot capture global input         | Switch to X11 (see [Wayland](#wayland)) |
| Headless: no DISPLAY/XDG\_SESSION    | No graphical session                         | Use Xvfb (see [Headless VPS](#headless-vps)) |
| Tesseract not found                  | Normalizer falls back to vision-only         | `apt install tesseract-ocr` (or brew/win) |
| FFmpeg not found                     | Validator may use OpenCV (lower quality)     | `apt install ffmpeg` (or brew/win) |
| xdotool not found                    | Linux active-window detection degraded       | `apt install xdotool` |

`dare doctor` always exits 0 — its warnings are informational.

---

## Recorder records 0 events

Almost always one of:

1. **Wayland** — see [Wayland](#wayland). `dare doctor` will say so.
2. **macOS permissions** — see [Permission errors on macOS](#permission-errors-on-macos).
3. **`pynput` not installed** — install with `pip install -e .[recorder]`.
4. **You actually were idle for 30 s before pressing anything** — by design.
   Pass `--idle 60` for more headroom while testing.

Diagnosis:
```bash
dare record --idle 5            # short timeout for quick test
# Move mouse / press keys actively for 5 s
cat runs/<run_id>/raw/raw_events.jsonl | head
```

If the file is empty, the listener never fired — check the OS layer.

---

## Recorder is slow

If screenshots are slow you'll feel it as input latency. Causes:

1. `mss` not installed → fallback to `PIL.ImageGrab` is much slower.
   Fix: `pip install -e .[recorder]`.
2. Multi-monitor with `--monitor 0` (default) on a 4K + secondary setup —
   each frame is huge. Pin to a single monitor: `--monitor 1`.
3. `--capture-moves` enabled — this records every mouse movement.
   Disable it (the default is off).

---

## Normalizer finds 0 elements

The pipeline runs but `processed/ui_state.json` shows empty `elements`:

1. `pytesseract` not installed → no OCR pass. Vision-only.
   Fix: `pip install -e .[ocr]` and ensure `tesseract` is in PATH.
2. `opencv-python` not installed → no vision pass. OCR-only.
   Fix: `pip install -e .[vision]`.
3. Both installed but still empty: the screenshots are degenerate (solid
   colour, blank). Sanity-check by opening one in an image viewer.
4. Tesseract installed but on a non-English UI: pass `--ocr-lang ita`
   (or whatever language code) to `dare normalize`.

---

## DSL has no `value` for type actions

The `keyboard_input` events in `raw_events.jsonl` were recorded with
`text=""`. Causes:

1. The user pressed only modifier keys (Shift, Ctrl, …) — these don't
   produce printable characters and are recorded in `keys` only.
2. The recorder was started **after** the user began typing — the burst
   started before the recorder's listener was attached.

Mitigation: start the recorder, wait 1 s, then begin typing.

---

## `preview.mp4` is a text file

The validator's renderer has three tiers (FFmpeg → OpenCV → placeholder)
and falls back silently. If you got a placeholder, both higher tiers were
unavailable.

```bash
dare doctor                              # confirms ffmpeg presence
.venv/bin/pip list | grep -E 'opencv|ffmpeg-python'
```

Fix:
```bash
.venv/bin/pip install -e .[video]        # ffmpeg-python + scikit-image
sudo apt install ffmpeg                  # the actual encoder
```

Re-run `dare validate --run <id>`. The renderer overwrites the previous
output.

---

## Executor finds wrong target

The executor reports `target_not_found` even though the element is on
screen. Resolution order is:

1. `element_id` (exact match against the live UI state).
2. `text` (fuzzy, threshold 0.6).
3. Vision template (`vision: templates/<aid>_target.png`).
4. `relative_position` (last resort).

Diagnosis:
```bash
dare execute --run <id> 2>&1 | grep -E 'target|fail'
```

Common causes:
- Resolution between recording and execution differs significantly →
  rely on `text` or vision; pin a single monitor.
- The UI changed between recording and execution → rebuild the run from a
  fresh recording.

---

## MCP server won't start

```bash
dare --mcp
```

Failures and fixes:

- `error: ... fastmcp ...` — install with `pip install -e .[mcp]`.
- Server prints `serving on stdio` then exits — your client closed stdin.
  This is normal: MCP clients (Claude Code, Cursor, …) hold the stdio
  pipe. Manual testing requires a piped client; see `docs/MCP_SETUP.md`.
- `error: ... Permission denied ...` on Linux — make sure the script is
  executable: `chmod +x .venv/bin/dare`.

---

## Permission errors on macOS

First-run prompts:

1. **Accessibility** (for `pynput` to capture global input):
   System Settings → Privacy & Security → Accessibility → enable both
   your terminal app **and** Python.
2. **Screen Recording** (for `mss` / Pillow to capture the screen):
   same panel → Screen Recording.

After granting, **toggle each permission off and on once** — macOS
sometimes needs the cycle to take effect.

---

## Wayland

Wayland intentionally restricts global input capture. `pynput` cannot
listen for global events on Wayland; the recorder will refuse to start
under Wayland with an explicit message.

Switch to X11:
```bash
echo $XDG_SESSION_TYPE                # if "wayland", you must change it
```

Log out and select "GNOME on Xorg" / "KDE Plasma on X11" at the login
screen. `dare doctor` will then report `linux_session: x11` and the
recorder will work.

`--force` is available but produces zero events under Wayland.

---

## Headless VPS

A VPS with no graphical session cannot run the recorder directly:

```bash
sudo apt install xvfb
xvfb-run -a -s "-screen 0 1920x1080x24" \
    .venv/bin/dare record --idle 5
```

This works for synthetic / scripted runs but cannot capture real user
input (there isn't any).

For real input on a remote machine, run a real X11 desktop inside a
VNC / NoMachine / RDP session and start `dare record` inside that session.

The remaining stages (`normalize`, `generate-dsl`, `build-intents`,
`clarify --auto-clarify`, `validate`, `execute`) all run on a headless
VPS without further gymnastics.
