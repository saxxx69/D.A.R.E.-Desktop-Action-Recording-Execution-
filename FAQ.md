# D.A.R.E. — Cross-platform notes

D.A.R.E. targets **Windows, macOS, and Linux (X11)**. Wayland and headless
Linux have well-defined limitations documented below.

Run `dare doctor` on the target machine for a tailored report; it surfaces
exactly the issues that apply to your setup.

---

## Windows

**Works out of the box.** Recommended setup:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -e .[recorder,vision]
```

### Gotchas

| Issue | Mitigation |
|-------|-----------|
| **DPI scaling** distorts coordinates from `pyautogui` | The recorder reads pixel data through `mss`, which honours the real desktop resolution. Coordinates are stored exactly as the OS reports them via `pynput`. The Phase 7 executor (when it lands) will use `pydirectinput`, which also respects DPI. |
| **Trading platforms (MT5, FTMO terminals, …) block standard input synthesis** | Phase 7's executor automatically routes through `pydirectinput` on Windows, which uses `SendInput` at a lower level than `pyautogui` and is not filtered by these apps. |
| **Multi-monitor coordinate origin** | `mss` enumerates monitors with `monitors[0]` = the union of all monitors. Use `--monitor 0` (default) for full coverage; `--monitor 1` for primary only, etc. |
| **Active window detection** | Uses `pygetwindow.getActiveWindow()` (works without admin). |

### Optional system tools

- **Tesseract OCR**: download the [UB-Mannheim build](https://github.com/UB-Mannheim/tesseract/wiki) and add the install dir to `PATH`.
- **FFmpeg**: download from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and add to `PATH`.

---

## macOS

**Works**, but the OS will prompt for two permissions on first run. Both
are required.

```bash
brew install ffmpeg tesseract                # optional
python -m venv .venv && source .venv/bin/activate
pip install -e .[recorder,vision]
```

### First-run permissions

The OS shows two prompts:

1. **Accessibility** — required for `pynput` to capture global input.
   System Settings → Privacy & Security → Accessibility → enable your
   terminal app (Terminal / iTerm2 / VS Code) **and** Python itself.
2. **Screen Recording** — required for `mss` / Pillow to capture the
   screen. Same settings panel → Screen Recording.

If the recorder runs but reports zeros, the most likely cause is a missing
permission. Toggle the permission off and on once after granting it (macOS
sometimes needs the cycle to take effect).

### Gotchas

| Issue | Mitigation |
|-------|-----------|
| **Retina scaling**: pixel buffers are 2× the logical coordinates | `mss` reports the true pixel resolution; `pynput` reports logical coordinates. The recorder records both faithfully — downstream stages use the resolution from each screenshot's metadata to relate them. |
| **Active window** | `pygetwindow` works on macOS for most apps but can return the bundle identifier instead of a human title for some background processes. We accept whatever it returns. |

---

## Linux — X11 (recommended)

**Works**. This is the recommended Linux configuration for D.A.R.E.

```bash
sudo apt install xdotool xvfb tesseract-ocr ffmpeg   # optional but useful
python -m venv .venv && source .venv/bin/activate
pip install -e .[recorder,vision]
```

To check you are on X11 not Wayland:

```bash
echo $XDG_SESSION_TYPE   # should print: x11
```

If your distro defaults to Wayland (Fedora, Ubuntu 22.04+, …), select
"GNOME on Xorg" or "KDE Plasma on X11" at the login screen.

### Gotchas

| Issue | Mitigation |
|-------|-----------|
| **`xdotool` missing** | Active-window detection falls back to `"unknown"`. Install via your package manager. |
| **Screensaver / lock screen** during recording | Disable for the recording session. The recorder will keep working but screenshots will capture the locked screen. |

---

## Linux — Wayland (NOT supported for live recording)

Wayland intentionally restricts global input capture and synthesis for
security reasons. `pynput` cannot reliably listen to global mouse / keyboard
events on Wayland; `pyautogui` cannot synthesise them either.

**`dare record` will refuse to start under Wayland** with a clear message.
You can override with `--force` but expect zero events to be captured.

The **only** workable path on a Wayland-only host is to log out and start
an X11 session for D.A.R.E. workflows. We deliberately do *not* recommend
the various dbus / portal-based hacks that exist for individual
compositors — they are fragile and inconsistent.

---

## Linux — headless / VPS

A VPS without a graphical session **cannot** run the recorder directly:
there is no display, no mouse, no keyboard. To exercise the recorder for
synthetic / scripted runs, wrap it in `Xvfb`:

```bash
sudo apt install xvfb
xvfb-run -a -s "-screen 0 1920x1080x24" python -m dare.server record --idle 5
```

This spawns a virtual framebuffer, lets the recorder attach, and exits
when the recorder exits. It is suitable for:

* CI smoke tests.
* Pipeline rehearsals (you can drive synthetic events through the
  recorder's `feed_*` API from a Python script).

It is **not** suitable for capturing real user input because there is no
real input. For that, attach a real display (VNC, NoMachine, RDP) on the
VPS, log in to it, and run D.A.R.E. inside that session.

---

## Quick decision matrix

| Goal | Use |
|------|-----|
| Record real user actions on a workstation | Native Win / macOS / Linux X11 |
| Develop and test the pipeline on a VPS | Xvfb + synthetic event feeds |
| Capture real input on a remote VPS | VNC / NoMachine session inside an X11 desktop |
| Run anything live on Wayland | **Switch to X11 first.** |
