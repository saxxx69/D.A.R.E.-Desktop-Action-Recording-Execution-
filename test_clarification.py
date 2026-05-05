"""Validator — Phase 6.

State-aware simulation + multi-tier video preview rendering.
"""

from dare.validator.simulator import Simulator
from dare.validator.validator import Validator
from dare.validator.video_renderer import VideoRenderer, VideoBackend

__all__ = ["Simulator", "Validator", "VideoRenderer", "VideoBackend"]
