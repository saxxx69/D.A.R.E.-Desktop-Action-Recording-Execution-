"""DSL Executor.

Parses ``<run>/processed/action.dsl.yaml`` and runs each action either:

* **dry-run** (default) — print intended actions, never touch the desktop.
  Suitable for CI and pre-flight inspection.
* **live** — actually drive mouse/keyboard via ``pydirectinput`` on
  Windows (compatible with trading platforms that block ``pyautogui``)
  and ``pyautogui`` elsewhere.

Per-action retry chain (vision → OCR → relative position) is implemented
in :mod:`dare.executor.retry`.

Each step's outcome is appended to ``<run>/processed/execution_log.json``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from dare.executor.retry import RetryStrategy
from dare.utils import platform as plat
from dare.utils.logging import get_logger


@dataclass
class ExecutionResult:
    """Per-action result."""

    step_index: int
    action: str
    target_hint: str
    status: str                     # ok | fail | skipped | dry_run
    resolved_xy: Optional[tuple[int, int]] = None
    confidence: float = 0.0
    strategy: str = ""
    duration_ms: int = 0
    fail_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "step_index": self.step_index,
            "action": self.action,
            "target_hint": self.target_hint,
            "status": self.status,
            "resolved_xy": list(self.resolved_xy) if self.resolved_xy else None,
            "confidence": round(self.confidence, 4),
            "strategy": self.strategy,
            "duration_ms": self.duration_ms,
            "fail_reason": self.fail_reason,
        }


# ---------------------------------------------------------------------------


class Executor:
    """DSL executor with dry-run + live modes."""

    def __init__(
        self,
        run_dir: Path,
        live: bool = False,
        params: Optional[dict] = None,
        action_delay_ms: int = 200,
        per_action_timeout_s: float = 5.0,
        retry: Optional[RetryStrategy] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.dsl_path = self.run_dir / "processed" / "action.dsl.yaml"
        self.log_path = self.run_dir / "processed" / "execution_log.json"
        self.live = live
        self.params = dict(params or {})
        self.action_delay_ms = max(0, action_delay_ms)
        self.per_action_timeout_s = max(0.5, per_action_timeout_s)
        self.retry = retry or RetryStrategy()
        self.log = logger or get_logger("dare.executor", run_dir=self.run_dir)
        self._results: list[ExecutionResult] = []

    # ----- public ------------------------------------------------------

    def run(self) -> dict:
        if not self.dsl_path.is_file():
            raise FileNotFoundError(
                f"action.dsl.yaml not found at {self.dsl_path}. "
                "Run `dare generate-dsl` first."
            )
        doc = self._load_yaml(self.dsl_path)
        actions = doc.get("actions", [])
        for i, action in enumerate(actions):
            res = self._run_action(i, action)
            self._results.append(res)
            if res.status == "fail" and not self.live:
                # In dry-run, keep going so the user sees full plan.
                pass

        # Persist log
        self.log_path.write_text(
            json.dumps([r.to_dict() for r in self._results], indent=2,
                       ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        ok = sum(1 for r in self._results if r.status in ("ok", "dry_run"))
        fail = sum(1 for r in self._results if r.status == "fail")
        summary = {
            "mode": "live" if self.live else "dry_run",
            "total": len(self._results),
            "ok": ok,
            "fail": fail,
            "log": str(self.log_path),
        }
        self.log.info("Execution complete: %s", summary)
        return summary

    # ----- per-action --------------------------------------------------

    def _run_action(self, idx: int, action: dict) -> ExecutionResult:
        atype = (action.get("action") or "").upper()
        target = action.get("target") or {}
        hint = target.get("hint") or "?"
        t0 = time.time()

        if atype == "WAIT_FOR":
            return self._wait_for(idx, action, t0)
        if atype == "VERIFY":
            return self._verify(idx, action, t0)
        if atype == "CLICK":
            return self._click(idx, action, t0)
        if atype == "TYPE":
            return self._type(idx, action, t0)

        return ExecutionResult(
            step_index=idx, action=atype, target_hint=hint,
            status="skipped",
            duration_ms=int((time.time() - t0) * 1000),
            fail_reason=f"unknown action {atype!r}",
        )

    def _wait_for(self, idx: int, action: dict, t0: float) -> ExecutionResult:
        target = action.get("target") or {}
        hint = target.get("hint") or "?"
        if not self.live:
            return ExecutionResult(
                step_index=idx, action="WAIT_FOR", target_hint=hint,
                status="dry_run",
                duration_ms=int((time.time() - t0) * 1000),
            )
        timeout = float(action.get("timeout", self.per_action_timeout_s * 1000)) / 1000.0
        deadline = time.time() + timeout
        while time.time() < deadline:
            screen = self._screen()
            hit = self.retry.resolve(target, self.run_dir, screen)
            if hit is not None:
                return ExecutionResult(
                    step_index=idx, action="WAIT_FOR", target_hint=hint,
                    status="ok",
                    resolved_xy=(hit[0], hit[1]), confidence=hit[2],
                    duration_ms=int((time.time() - t0) * 1000),
                )
            time.sleep(0.2)
        return ExecutionResult(
            step_index=idx, action="WAIT_FOR", target_hint=hint,
            status="fail",
            duration_ms=int((time.time() - t0) * 1000),
            fail_reason=f"target {hint!r} not seen within {timeout}s",
        )

    def _click(self, idx: int, action: dict, t0: float) -> ExecutionResult:
        target = action.get("target") or {}
        hint = target.get("hint") or "?"
        if not self.live:
            return ExecutionResult(
                step_index=idx, action="CLICK", target_hint=hint,
                status="dry_run",
                duration_ms=int((time.time() - t0) * 1000),
            )
        screen = self._screen()
        hit = self.retry.resolve(target, self.run_dir, screen)
        if hit is None:
            return ExecutionResult(
                step_index=idx, action="CLICK", target_hint=hint,
                status="fail", fail_reason="could not resolve target",
                duration_ms=int((time.time() - t0) * 1000),
            )
        x, y, conf = hit
        try:
            self._do_click(x, y)
        except Exception as e:
            return ExecutionResult(
                step_index=idx, action="CLICK", target_hint=hint,
                status="fail", fail_reason=f"click failed: {e}",
                resolved_xy=(x, y), confidence=conf,
                duration_ms=int((time.time() - t0) * 1000),
            )
        time.sleep(self.action_delay_ms / 1000.0)
        return ExecutionResult(
            step_index=idx, action="CLICK", target_hint=hint,
            status="ok", resolved_xy=(x, y), confidence=conf,
            duration_ms=int((time.time() - t0) * 1000),
        )

    def _type(self, idx: int, action: dict, t0: float) -> ExecutionResult:
        target = action.get("target") or {}
        hint = target.get("hint") or "?"
        # Substitute parameter values when provided
        action_id = action.get("id")
        value = self.params.get(action_id, action.get("value", ""))
        value = str(value)
        if not self.live:
            return ExecutionResult(
                step_index=idx, action=f"TYPE[{value}]", target_hint=hint,
                status="dry_run",
                duration_ms=int((time.time() - t0) * 1000),
            )
        # Click the input first if we can find it
        screen = self._screen()
        hit = self.retry.resolve(target, self.run_dir, screen)
        if hit is not None:
            x, y, conf = hit
            try:
                self._do_click(x, y)
            except Exception as e:
                return ExecutionResult(
                    step_index=idx, action="TYPE", target_hint=hint,
                    status="fail", fail_reason=f"focus click failed: {e}",
                    duration_ms=int((time.time() - t0) * 1000),
                )
            time.sleep(0.1)
        else:
            x = y = 0
            conf = 0.0
        try:
            self._do_type(value)
        except Exception as e:
            return ExecutionResult(
                step_index=idx, action="TYPE", target_hint=hint,
                status="fail", fail_reason=f"type failed: {e}",
                resolved_xy=(x, y) if hit else None, confidence=conf,
                duration_ms=int((time.time() - t0) * 1000),
            )
        time.sleep(self.action_delay_ms / 1000.0)
        return ExecutionResult(
            step_index=idx, action=f"TYPE[{value}]", target_hint=hint,
            status="ok", resolved_xy=(x, y) if hit else None, confidence=conf,
            duration_ms=int((time.time() - t0) * 1000),
        )

    def _verify(self, idx: int, action: dict, t0: float) -> ExecutionResult:
        cond = action.get("condition") or ""
        if not self.live:
            return ExecutionResult(
                step_index=idx, action=f"VERIFY[{cond}]", target_hint="-",
                status="dry_run",
                duration_ms=int((time.time() - t0) * 1000),
            )
        # Live verification: only "text_present:<text>" supported for now
        if cond.startswith("text_present:"):
            needle = cond.split(":", 1)[1].strip()
            screen = self._screen()
            if self._screen_has_text(screen, needle):
                return ExecutionResult(
                    step_index=idx, action=f"VERIFY[{cond}]", target_hint="-",
                    status="ok",
                    duration_ms=int((time.time() - t0) * 1000),
                )
            return ExecutionResult(
                step_index=idx, action=f"VERIFY[{cond}]", target_hint="-",
                status="fail",
                duration_ms=int((time.time() - t0) * 1000),
                fail_reason=f"text {needle!r} not visible",
            )
        # Unknown condition: pass-through
        return ExecutionResult(
            step_index=idx, action=f"VERIFY[{cond}]", target_hint="-",
            status="ok",
            duration_ms=int((time.time() - t0) * 1000),
        )

    # ----- input drivers ----------------------------------------------

    def _do_click(self, x: int, y: int) -> None:
        if plat.get_os() == "windows":
            try:
                import pydirectinput  # type: ignore[import-not-found]
                pydirectinput.moveTo(x, y)
                pydirectinput.click()
                return
            except ImportError:
                pass
        import pyautogui  # type: ignore[import-not-found]
        pyautogui.click(x, y)

    def _do_type(self, text: str) -> None:
        if plat.get_os() == "windows":
            try:
                import pydirectinput  # type: ignore[import-not-found]
                pydirectinput.typewrite(text, interval=0.02)
                return
            except ImportError:
                pass
        import pyautogui  # type: ignore[import-not-found]
        pyautogui.typewrite(text, interval=0.02)

    def _screen(self):
        try:
            import mss  # type: ignore[import-not-found]
            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[0])
                from PIL import Image  # type: ignore[import-not-found]
                return Image.frombytes("RGB", (shot.width, shot.height),
                                       shot.rgb)
        except ImportError:
            try:
                from PIL import ImageGrab  # type: ignore[import-not-found]
                return ImageGrab.grab().convert("RGB")
            except ImportError:
                return None

    def _screen_has_text(self, screen, needle: str) -> bool:
        if screen is None:
            return False
        try:
            import pytesseract  # type: ignore[import-not-found]
        except ImportError:
            return False
        try:
            text = pytesseract.image_to_string(screen)
        except Exception:
            return False
        return needle.lower() in text.lower()

    # ----- DSL loading -------------------------------------------------

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        try:
            import yaml  # type: ignore[import-not-found]
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except ImportError:
            # Minimal fallback for our hand-rolled emitter
            return _parse_minimal_yaml(path.read_text(encoding="utf-8"))


def _parse_minimal_yaml(text: str) -> dict:
    """Fallback YAML loader for our schema only.

    Handles the subset that ``dsl/generator.py`` emits when pyyaml is
    missing: top-level mapping, nested mappings, lists of mappings via
    ``-\\n  k: v`` blocks, and scalar values.
    """
    import re

    lines = text.splitlines()
    pos = [0]

    def peek():
        while pos[0] < len(lines) and not lines[pos[0]].strip():
            pos[0] += 1
        if pos[0] >= len(lines):
            return None
        return lines[pos[0]]

    def indent(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def parse_value(s: str):
        s = s.strip()
        if s in ("null", "~", ""):
            return None
        if s == "true":
            return True
        if s == "false":
            return False
        if s.startswith('"') and s.endswith('"') and len(s) >= 2:
            return s[1:-1].replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")
        if s.startswith("'") and s.endswith("'") and len(s) >= 2:
            return s[1:-1].replace("''", "'")
        if re.match(r"^-?\d+$", s):
            try:
                return int(s)
            except ValueError:
                return s
        if re.match(r"^-?\d+\.\d+$", s):
            try:
                return float(s)
            except ValueError:
                return s
        return s

    def parse_block(min_indent: int):
        # Returns a dict or list at exactly indent==min_indent
        line = peek()
        if line is None or indent(line) < min_indent:
            return {}
        stripped = line.lstrip()
        if stripped == "-" or stripped.startswith("- "):
            return parse_list(min_indent)
        return parse_map(min_indent)

    def parse_map(cur_indent: int):
        out: dict = {}
        while True:
            line = peek()
            if line is None or indent(line) < cur_indent:
                return out
            if indent(line) > cur_indent:
                # shouldn't happen — bail
                return out
            stripped = line.strip()
            if stripped == "-" or stripped.startswith("- "):
                return out
            m = re.match(r'^([^:]+):\s*(.*)$', stripped)
            if not m:
                pos[0] += 1
                continue
            key = parse_value(m.group(1).strip())
            rest = m.group(2)
            pos[0] += 1
            if rest.strip() == "":
                # value is on subsequent indented lines
                child_line = peek()
                if child_line is not None and indent(child_line) > cur_indent:
                    out[key] = parse_block(indent(child_line))
                else:
                    out[key] = None
            else:
                out[key] = parse_value(rest)
        return out

    def parse_list(cur_indent: int):
        out: list = []
        while True:
            line = peek()
            if line is None or indent(line) < cur_indent:
                return out
            stripped = line.strip()
            # Bare "-" → block-style item with body on subsequent lines.
            if stripped == "-":
                pos[0] += 1
                child = peek()
                if child is not None and indent(child) > cur_indent:
                    out.append(parse_block(indent(child)))
                else:
                    out.append(None)
                continue
            if not stripped.startswith("- "):
                return out
            inner = stripped[2:].strip()
            pos[0] += 1
            if inner == "":
                child = peek()
                if child is not None and indent(child) > cur_indent:
                    out.append(parse_block(indent(child)))
                else:
                    out.append(None)
            elif ":" in inner:
                m = re.match(r'^([^:]+):\s*(.*)$', inner)
                if m:
                    key = parse_value(m.group(1).strip())
                    rest = m.group(2)
                    item: dict = {}
                    if rest.strip() == "":
                        child = peek()
                        if child is not None and indent(child) > cur_indent + 2:
                            item[key] = parse_block(indent(child))
                        else:
                            item[key] = None
                    else:
                        item[key] = parse_value(rest)
                    child = peek()
                    if child is not None and indent(child) > cur_indent:
                        rest_map = parse_map(indent(child))
                        item.update(rest_map)
                    out.append(item)
                else:
                    out.append(parse_value(inner))
            else:
                out.append(parse_value(inner))
        return out

    return parse_block(0) or {}
