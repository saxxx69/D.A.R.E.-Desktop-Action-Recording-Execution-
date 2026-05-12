"""Run Manager — manages execution runs and their state.

Provides:
- RunMetadata: Immutable metadata for a single run
- RunManager: Session manager for the 7-stage pipeline
- STAGES: Canonical list of execution stages
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# ----- Stage Configuration --------------------------------------------------

STAGES = [
    "record",
    "normalize",
    "dsl_generation",
    "classification",
    "validation",
    "execution",
    "mcp_export",
]

# ----- RunMetadata ----------------------------------------------------------


@dataclass(frozen=True)
class RunMetadata:
    """Immutable metadata for a single run."""

    id: str
    created_at: str
    status: str  # "running", "completed", "failed", "paused"
    stages_completed: list[str] = field(default_factory=list)
    current_stage: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "id": self.id,
            "created_at": self.created_at,
            "status": self.status,
            "stages_completed": self.stages_completed,
            "current_stage": self.current_stage,
            "error": self.error,
        }


# ----- RunManager -----------------------------------------------------------


class RunManager:
    """Manages execution runs: tracking state, stages, and results."""

    def __init__(self, run_id: Optional[str] = None, run_dir: Optional[Path] = None):
        """Initialize RunManager.

        Args:
            run_id: Optional explicit run ID. If None, generates from timestamp.
            run_dir: Optional base directory for run files.
                    Defaults to ./runs/{run_id}/
        """
        import uuid

        self.run_id = run_id or str(uuid.uuid4())[:8]
        self.run_dir = (run_dir or Path("./runs")) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self._metadata_file = self.run_dir / "metadata.json"
        self._stages_dir = self.run_dir / "stages"
        self._stages_dir.mkdir(exist_ok=True)

        self._metadata = self._load_or_create_metadata()

    def _load_or_create_metadata(self) -> RunMetadata:
        """Load existing metadata or create new."""
        if self._metadata_file.exists():
            data = json.loads(self._metadata_file.read_text())
            return RunMetadata(
                id=data["id"],
                created_at=data["created_at"],
                status=data["status"],
                stages_completed=data.get("stages_completed", []),
                current_stage=data.get("current_stage"),
                error=data.get("error"),
            )
        return RunMetadata(
            id=self.run_id,
            created_at=datetime.now().isoformat(),
            status="running",
            stages_completed=[],
            current_stage=None,
        )

    def _save_metadata(self) -> None:
        """Persist metadata to disk."""
        self._metadata_file.write_text(json.dumps(self._metadata.to_dict(), indent=2))

    @property
    def metadata(self) -> RunMetadata:
        """Get current metadata."""
        return self._metadata

    def start_stage(self, stage: str) -> None:
        """Mark a stage as current/in-progress."""
        if stage not in STAGES:
            raise ValueError(f"Unknown stage: {stage}")
        self._metadata = RunMetadata(
            id=self._metadata.id,
            created_at=self._metadata.created_at,
            status="running",
            stages_completed=self._metadata.stages_completed,
            current_stage=stage,
            error=None,
        )
        self._save_metadata()

    def complete_stage(self, stage: str, data: Optional[dict] = None) -> None:
        """Mark a stage as completed."""
        if stage not in STAGES:
            raise ValueError(f"Unknown stage: {stage}")

        if stage not in self._metadata.stages_completed:
            new_completed = self._metadata.stages_completed + [stage]
        else:
            new_completed = self._metadata.stages_completed

        self._metadata = RunMetadata(
            id=self._metadata.id,
            created_at=self._metadata.created_at,
            status="running",
            stages_completed=new_completed,
            current_stage=None,
            error=None,
        )
        self._save_metadata()

        # Save stage data if provided
        if data:
            stage_file = self._stages_dir / f"{stage}.json"
            stage_file.write_text(json.dumps(data, indent=2))

    def fail(self, error: str) -> None:
        """Mark run as failed."""
        self._metadata = RunMetadata(
            id=self._metadata.id,
            created_at=self._metadata.created_at,
            status="failed",
            stages_completed=self._metadata.stages_completed,
            current_stage=None,
            error=error,
        )
        self._save_metadata()

    def complete(self) -> None:
        """Mark run as completed."""
        self._metadata = RunMetadata(
            id=self._metadata.id,
            created_at=self._metadata.created_at,
            status="completed",
            stages_completed=self._metadata.stages_completed,
            current_stage=None,
            error=None,
        )
        self._save_metadata()

    def get_stage_data(self, stage: str) -> Optional[dict]:
        """Load data from a completed stage."""
        stage_file = self._stages_dir / f"{stage}.json"
        if stage_file.exists():
            return json.loads(stage_file.read_text())
        return None

    def list_runs(base_dir: Optional[Path] = None) -> list[RunMetadata]:
        """List all runs in the runs directory."""
        base_dir = base_dir or Path("./runs")
        if not base_dir.exists():
            return []

        runs = []
        for run_dir in base_dir.iterdir():
            if not run_dir.is_dir():
                continue
            metadata_file = run_dir / "metadata.json"
            if metadata_file.exists():
                data = json.loads(metadata_file.read_text())
                runs.append(
                    RunMetadata(
                        id=data["id"],
                        created_at=data["created_at"],
                        status=data["status"],
                        stages_completed=data.get("stages_completed", []),
                        current_stage=data.get("current_stage"),
                        error=data.get("error"),
                    )
                )
        return sorted(runs, key=lambda r: r.created_at, reverse=True)
