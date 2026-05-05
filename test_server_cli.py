"""Validator orchestrator — Phase 6.

Runs the simulator on a normalized run, persists the report and renders
the preview video.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from dare.validator.simulator import Simulator
from dare.validator.video_renderer import VideoRenderer
from dare.utils.logging import get_logger


class Validator:
    def __init__(
        self,
        run_dir: Path,
        fps: int = 4,
        frames_per_step: int = 4,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.processed = self.run_dir / "processed"
        self.validation_dir = self.run_dir / "validation"
        self.validation_dir.mkdir(parents=True, exist_ok=True)
        self.shots_dir = self.run_dir / "assets" / "screenshots"
        self.simulation_path = self.validation_dir / "simulation.json"
        self.preview_path = self.validation_dir / "preview.mp4"
        self.fps = fps
        self.frames_per_step = frames_per_step
        self.log = logger or get_logger("dare.validator", run_dir=self.run_dir)

    def run(self) -> dict:
        if not (self.processed / "action_graph.json").is_file():
            raise FileNotFoundError(
                "action_graph.json missing — run normalize first"
            )
        sim = Simulator(self.run_dir)
        sim_runs = sim.simulate_all()
        report = {"runs": [r.to_dict() for r in sim_runs]}
        self.simulation_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        renderer = VideoRenderer(
            screenshots_dir=self.shots_dir,
            out_path=self.preview_path,
            fps=self.fps,
            frames_per_step=self.frames_per_step,
        )
        result = renderer.render(report)

        ok_runs = sum(1 for r in sim_runs if r.status == "ok")
        fail_runs = sum(1 for r in sim_runs if r.status == "fail")
        summary = {
            "runs": len(sim_runs),
            "ok": ok_runs,
            "fail": fail_runs,
            "preview": result.to_dict(),
        }
        self.log.info("Validator complete: %s", summary)
        return summary
