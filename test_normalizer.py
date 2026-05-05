"""Per-run isolation: every D.A.R.E. execution writes its artifacts to a
unique folder under ``runs/`` so the repository root stays clean.

Folder layout for a single run::

    runs/<run_id>/
    ├── run.json              # metadata (id, status, timestamps, stages)
    ├── raw/                  # raw recorder output (events, screenshots)
    │   └── raw_events.jsonl
    ├── processed/            # normalizer output
    │   ├── ui_state.json
    │   ├── state_diff.json
    │   ├── action_graph.json
    │   └── action.dsl.yaml
    ├── assets/
    │   ├── screenshots/      # frames captured by the recorder
    │   └── templates/        # vision template crops
    ├── validation/
    │   ├── simulation.json
    │   └── preview.mp4
    ├── docs/
    │   └── intents/          # one .md per action
    └── logs/
        └── run.log

The repository root is *never* written to by the pipeline.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Subfolder layout — kept as a module constant so callers can iterate.
SUBDIRS: tuple[str, ...] = (
    "raw",
    "processed",
    "assets/screenshots",
    "assets/templates",
    "validation",
    "docs/intents",
    "logs",
)

# Pipeline stages, in execution order. Used for status tracking.
STAGES: tuple[str, ...] = (
    "record",
    "normalize",
    "generate_dsl",
    "build_intents",
    "clarify",
    "validate",
    "execute",
)

# Environment variable to override the runs root (useful for tests / VPS).
RUNS_ROOT_ENV = "DARE_RUNS_ROOT"

# Run ID format: YYYYMMDD_HHMMSSffffff_<6 hex chars>
# (date + time-with-microseconds + uuid suffix — sortable, unique, fs-safe).
# Microsecond resolution prevents same-second collisions when runs are created
# in rapid succession (e.g. by tests or batch scripts).
_RUN_ID_RE = re.compile(r"^\d{8}_\d{12}_[0-9a-f]{6}$")


def _default_runs_root() -> Path:
    """Resolve the directory that holds all runs.

    Resolution order:
      1. ``$DARE_RUNS_ROOT`` if set.
      2. ``./runs`` relative to the current working directory.
    """
    env = os.environ.get(RUNS_ROOT_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd() / "runs"


def _make_run_id(now: Optional[datetime] = None) -> str:
    """Generate a fresh sortable run ID: ``YYYYMMDD_HHMMSSffffff_<6hex>``.

    The microsecond precision in the timestamp makes accidental collisions
    between rapid successive calls effectively impossible, while the uuid
    suffix protects against clock skew or simultaneous creates from
    different processes.
    """
    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%d_%H%M%S%f")
    short = uuid.uuid4().hex[:6]
    return f"{ts}_{short}"


@dataclass
class RunMetadata:
    """Serializable metadata for a single run, persisted as ``run.json``."""

    id: str
    created_at: str  # ISO-8601 UTC
    status: str = "created"  # created | running | completed | failed | aborted
    current_stage: Optional[str] = None
    stages_completed: list[str] = field(default_factory=list)
    stages_failed: list[str] = field(default_factory=list)
    notes: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "RunMetadata":
        return cls(
            id=data["id"],
            created_at=data["created_at"],
            status=data.get("status", "created"),
            current_stage=data.get("current_stage"),
            stages_completed=list(data.get("stages_completed", [])),
            stages_failed=list(data.get("stages_failed", [])),
            notes=data.get("notes", ""),
        )


class RunManager:
    """Create, locate, and update isolated run folders.

    Typical usage::

        rm = RunManager()
        run = rm.create()                          # new folder + metadata
        rm.set_stage(run.id, "record")
        rm.complete_stage(run.id, "record")
        rm.fail_stage(run.id, "normalize", "OCR engine missing")

    The class is stateless — all state lives on disk under :attr:`runs_root`.
    """

    def __init__(self, runs_root: Optional[Path] = None) -> None:
        self.runs_root = Path(runs_root) if runs_root else _default_runs_root()

    # ----- creation / lookup --------------------------------------------

    def create(self, *, notes: str = "") -> RunMetadata:
        """Create a new run folder with the full subdirectory layout and
        write its metadata. Returns the freshly written metadata.
        """
        run_id = _make_run_id()
        run_dir = self.runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        for sub in SUBDIRS:
            (run_dir / sub).mkdir(parents=True, exist_ok=True)
        meta = RunMetadata(
            id=run_id,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            notes=notes,
        )
        self._write_metadata(run_dir, meta)
        return meta

    def path(self, run_id: str) -> Path:
        """Return the folder path for a given run ID. Raises if the ID is
        malformed or the folder does not exist.
        """
        if not _RUN_ID_RE.match(run_id):
            raise ValueError(f"Invalid run id format: {run_id!r}")
        p = self.runs_root / run_id
        if not p.is_dir():
            raise FileNotFoundError(
                f"Run not found: {run_id} (looked in {self.runs_root})"
            )
        return p

    def metadata(self, run_id: str) -> RunMetadata:
        """Load metadata for a run."""
        meta_file = self.path(run_id) / "run.json"
        with meta_file.open("r", encoding="utf-8") as f:
            return RunMetadata.from_dict(json.load(f))

    def list_runs(self) -> list[RunMetadata]:
        """List all runs in the runs root, sorted by ID (chronological)."""
        if not self.runs_root.is_dir():
            return []
        out: list[RunMetadata] = []
        for child in sorted(self.runs_root.iterdir()):
            if not (child.is_dir() and _RUN_ID_RE.match(child.name)):
                continue
            meta_file = child / "run.json"
            if not meta_file.is_file():
                continue
            try:
                with meta_file.open("r", encoding="utf-8") as f:
                    out.append(RunMetadata.from_dict(json.load(f)))
            except (json.JSONDecodeError, KeyError):
                # Corrupt metadata — surface as a minimal record so the user
                # can still inspect / clean it up.
                out.append(
                    RunMetadata(
                        id=child.name, created_at="unknown", status="corrupt"
                    )
                )
        return out

    def latest(self) -> Optional[RunMetadata]:
        """Return the most recent run, or None if there are no runs."""
        runs = self.list_runs()
        return runs[-1] if runs else None

    # ----- stage tracking ------------------------------------------------

    def set_stage(self, run_id: str, stage: str) -> RunMetadata:
        """Mark ``stage`` as currently in progress."""
        if stage not in STAGES:
            raise ValueError(f"Unknown stage: {stage!r}. Valid: {STAGES}")
        meta = self.metadata(run_id)
        meta.current_stage = stage
        meta.status = "running"
        self._write_metadata(self.path(run_id), meta)
        return meta

    def complete_stage(self, run_id: str, stage: str) -> RunMetadata:
        """Mark ``stage`` as completed. Resets any prior failure record for
        the same stage. Sets overall status to ``completed`` once every
        pipeline stage has been completed at least once.
        """
        if stage not in STAGES:
            raise ValueError(f"Unknown stage: {stage!r}. Valid: {STAGES}")
        meta = self.metadata(run_id)
        if stage not in meta.stages_completed:
            meta.stages_completed.append(stage)
        if stage in meta.stages_failed:
            meta.stages_failed.remove(stage)
        meta.current_stage = None
        meta.status = (
            "completed"
            if set(meta.stages_completed) >= set(STAGES)
            else "running"
        )
        self._write_metadata(self.path(run_id), meta)
        return meta

    def fail_stage(self, run_id: str, stage: str, reason: str = "") -> RunMetadata:
        """Mark ``stage`` as failed. Pipeline status becomes ``failed``."""
        if stage not in STAGES:
            raise ValueError(f"Unknown stage: {stage!r}. Valid: {STAGES}")
        meta = self.metadata(run_id)
        if stage not in meta.stages_failed:
            meta.stages_failed.append(stage)
        meta.current_stage = None
        meta.status = "failed"
        if reason:
            meta.notes = (meta.notes + f"\n[{stage}] {reason}").strip()
        self._write_metadata(self.path(run_id), meta)
        return meta

    def abort(self, run_id: str, reason: str = "") -> RunMetadata:
        """Mark the run as aborted (user-initiated stop)."""
        meta = self.metadata(run_id)
        meta.status = "aborted"
        meta.current_stage = None
        if reason:
            meta.notes = (meta.notes + f"\n[abort] {reason}").strip()
        self._write_metadata(self.path(run_id), meta)
        return meta

    # ----- helpers -------------------------------------------------------

    def resolve(self, run_id: Optional[str]) -> str:
        """Resolve ``run_id`` or fall back to the latest run.

        Useful for CLI commands that accept ``--run`` optionally.
        """
        if run_id:
            return self.path(run_id).name  # validates existence
        latest = self.latest()
        if latest is None:
            raise FileNotFoundError(
                "No runs found. Create one with: dare runs new"
            )
        return latest.id

    def subpath(self, run_id: str, sub: str) -> Path:
        """Return a subpath inside a run, creating it if missing."""
        p = self.path(run_id) / sub
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ----- internal ------------------------------------------------------

    @staticmethod
    def _write_metadata(run_dir: Path, meta: RunMetadata) -> None:
        (run_dir / "run.json").write_text(meta.to_json() + "\n", encoding="utf-8")
