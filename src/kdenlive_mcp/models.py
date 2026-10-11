"""Modelos simples del proyecto (solo stdlib)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProjectMeta:
    name: str
    width: int = 1920
    height: int = 1080
    fps_num: int = 30
    fps_den: int = 1
    profile: str = "atsc_1080p_30"
    project_path: str = ""
    render_profile: str = "MP4-H264/AAC"
