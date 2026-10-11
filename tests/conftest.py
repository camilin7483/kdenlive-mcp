"""Fixtures y helpers compartidos de la suite (solo tests, sin src/).

Todo vive bajo tmp_path (permitido por security.py: /tmp) para no
contaminar projects/ ni output/ del repo.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_SYS_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SYS_SRC) not in sys.path:
    sys.path.insert(0, str(_SYS_SRC))

import pytest

from kdenlive_mcp.kdenlive_project import ProjectStore


@pytest.fixture
def store(tmp_path: Path) -> ProjectStore:
    """ProjectStore aislado: sus .kdenlive viven en tmp_path/projects."""
    return ProjectStore(base_dir=str(tmp_path / "projects"))


@pytest.fixture
def project_path(store: ProjectStore) -> str:
    """Un proyecto recién creado en el store aislado."""
    return store.create_project(name="test_proj")["project_path"]


def synth_video(
    dest: Path | str,
    duration: float = 1.0,
    width: int = 320,
    height: int = 240,
    fps: int = 30,
) -> str:
    """Genera un mp4 sintético (testsrc) o skipea si ffmpeg falla."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return str(dest)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg no disponible")
    proc = subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"testsrc=size={width}x{height}:rate={fps}:duration={duration}",
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast",
            str(dest),
        ],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        pytest.skip(f"ffmpeg no pudo generar video: {(proc.stderr or '')[-500:]}")
    return str(dest)


def synth_wav(dest: Path | str, duration: float = 1.0) -> str:
    """Genera un wav sintético (sine 440Hz) o skipea si ffmpeg falla."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return str(dest)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg no disponible")
    proc = subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            str(dest),
        ],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        pytest.skip(f"ffmpeg no pudo generar wav: {(proc.stderr or '')[-500:]}")
    return str(dest)
