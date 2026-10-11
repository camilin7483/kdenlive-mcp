"""Render real de proyectos Kdenlive con melt en background thread.

Comando base: melt proyecto.mlt -consumer avformat:OUT <args MLT>.
El progreso se parsea de "Current Frame:" en stderr. Estado global
thread-safe en _JOBS.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger(__name__)

try:
    from kdenlive_mcp.kdenlive_project import ensure_composite_transitions
except ImportError:  # pragma: no cover
    try:
        from .kdenlive_project import ensure_composite_transitions  # type: ignore
    except ImportError:
        def ensure_composite_transitions(project_path: str) -> dict:
            return {"added": 0, "tracks": 0}

try:  # security.py lo crea otro módulo; fallback local si aún no existe
    from kdenlive_mcp.security import validate_output_path, validate_path
except ImportError:  # pragma: no cover - solo hasta que exista security.py
    try:
        from .security import validate_output_path, validate_path  # type: ignore
    except ImportError:
        def validate_path(p: str) -> Path:
            path = Path(p).resolve()
            if not path.exists():
                raise FileNotFoundError(f"No existe: {p}")
            return path

        def validate_output_path(p: str) -> Path:
            path = Path(p).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return path

RENDER_PROFILES: dict[str, str] = {
    "MP4-H264/AAC": (
        "f=mp4 movflags=+faststart vcodec=libx264 crf=23 g=15 "
        "acodec=aac ab=160k"
    ),
    "MP4-H265": "f=mp4 vcodec=libx265 crf=28 acodec=aac ab=160k",
    "WebM-VP9": "f=webm vcodec=libvpx-vp9 crf=25 acodec=libopus",
    "Preview": (
        "f=mp4 vcodec=libx264 crf=28 preset=veryfast acodec=aac ab=128k"
    ),
    "MP4-1080p30": (
        "f=mp4 movflags=+faststart s=1920x1080 vcodec=libx264 crf=23 "
        "preset=medium acodec=aac ab=160k"
    ),
    "MP4-4K30": (
        "f=mp4 movflags=+faststart s=3840x2160 vcodec=libx264 crf=26 "
        "preset=medium acodec=aac ab=192k"
    ),
    "MP4-Vertical-Shorts": (
        "f=mp4 movflags=+faststart s=1080x1920 vcodec=libx264 crf=23 "
        "preset=medium acodec=aac ab=160k"
    ),
}

_PROFILE_EXTENSIONS: dict[str, str] = {
    "MP4-H264/AAC": "mp4",
    "MP4-H265": "mp4",
    "WebM-VP9": "webm",
    "Preview": "mp4",
    "MP4-1080p30": "mp4",
    "MP4-4K30": "mp4",
    "MP4-Vertical-Shorts": "mp4",
}

DEFAULT_PROFILE = "MP4-H264/AAC"
_FRAME_RE = re.compile(r"Current Frame:\s*(\d+)", re.IGNORECASE)
_LOG_KEEP = 200
_LOG_TAIL = 20

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
MAX_CONCURRENT_RENDERS = 2
try:
    from kdenlive_mcp.security import MAX_JOBS
except ImportError:
    MAX_JOBS = 20


def _evict_jobs() -> None:
    """Evita crecimiento ilimitado de _JOBS: conserva los más recientes."""
    done = [jid for jid, j in _JOBS.items() if j["status"] != "running"]
    overflow = len(_JOBS) - MAX_JOBS
    for jid in done[: max(0, overflow)]:
        _JOBS.pop(jid, None)


def _project_profile(project: Path) -> str:
    """Perfil guardado en el tractor o el default."""
    try:
        root = ET.parse(project).getroot()
    except ET.ParseError:
        return DEFAULT_PROFILE
    for tractor in root.findall("tractor"):
        name = tractor.findtext("property[@name='render_profile']")
        if name in RENDER_PROFILES:
            return name
    return DEFAULT_PROFILE


def _render_duration(project: Path) -> int:
    """Duración real (helper compartido: suma secuencial por playlist)."""
    try:
        from kdenlive_mcp.kdenlive_project import timeline_length_frames
    except ImportError:
        from .kdenlive_project import timeline_length_frames  # type: ignore
    try:
        root = ET.parse(project).getroot()
    except ET.ParseError:
        return 149
    return timeline_length_frames(root) or 149


def list_render_profiles() -> dict:
    """Lista los perfiles de render disponibles."""
    return {
        "profiles": [
            {"name": name, "args": args,
             "extension": _PROFILE_EXTENSIONS.get(name, "mp4")}
            for name, args in RENDER_PROFILES.items()
        ]
    }


def set_render_profile(project_path: str, profile_name: str) -> dict:
    """Guarda el perfil de render como propiedad del tractor."""
    if profile_name not in RENDER_PROFILES:
        raise ValueError(
            f"Perfil desconocido: {profile_name!r}. "
            f"Disponibles: {sorted(RENDER_PROFILES)}"
        )
    project = validate_path(project_path)
    try:
        tree = ET.parse(project)
    except ET.ParseError as exc:
        raise ValueError(f"XML inválido en {project_path}: {exc}") from exc
    tractor = tree.getroot().find("tractor")
    if tractor is None:
        raise ValueError("El proyecto no tiene tractor")
    prop = tractor.find("property[@name='render_profile']")
    if prop is None:
        prop = ET.SubElement(tractor, "property", {"name": "render_profile"})
    prop.text = profile_name
    tree.write(project, encoding="utf-8", xml_declaration=True)
    return {
        "project": str(project),
        "profile": profile_name,
        "args": RENDER_PROFILES[profile_name],
    }


def _run_melt(job_id: str, cmd: list[str], total: int | None) -> None:
    """Worker en thread: ejecuta melt y actualiza _JOBS."""
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
    except OSError as exc:
        with _LOCK:
            _JOBS[job_id].update(
                {"status": "error", "returncode": -1,
                 "log": [f"no se pudo lanzar melt: {exc}"]}
            )
        return
    with _LOCK:
        _JOBS[job_id]["pid"] = proc.pid
        _JOBS[job_id]["_proc"] = proc
    log: list[str] = []
    current = 0
    assert proc.stderr is not None
    for line in proc.stderr:
        line = line.rstrip()
        if line:
            log.append(line)
            if len(log) > _LOG_KEEP:
                del log[: len(log) - _LOG_KEEP]
        m = _FRAME_RE.search(line)
        if m:
            current = int(m.group(1))
        with _LOCK:
            job = _JOBS[job_id]
            job["log"] = list(log)
            if total:
                job["progress"] = min(99, int(current * 100 / max(total, 1)))
    try:
        proc.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        proc.kill()
        with _LOCK:
            _JOBS[job_id]["status"] = "error"
            _JOBS[job_id]["returncode"] = -9
    with _LOCK:
        job = _JOBS[job_id]
        job.pop("_proc", None)
        if job["status"] == "running":  # cancel_render pudo marcarlo antes
            job["status"] = "done" if proc.returncode == 0 else "error"
        job["returncode"] = proc.returncode
        if job["status"] == "done":
            job["progress"] = 100
        job["log"] = list(log)


def render_project(
    project_path: str, output_path: str = "", profile_name: str = ""
) -> dict:
    """Lanza melt en background; devuelve {job_id, output, status}."""
    project = validate_path(project_path)
    try:
        ensure_composite_transitions(str(project))
    except Exception as exc:
        log.warning("composites no garantizados en %s: %s", project, exc)
    profile = (
        profile_name
        or _project_profile(project)
        or DEFAULT_PROFILE
    )
    if profile not in RENDER_PROFILES:
        raise ValueError(f"Perfil desconocido: {profile!r}")
    ext = _PROFILE_EXTENSIONS.get(profile, "mp4")
    out = (
        validate_output_path(output_path)
        if output_path
        else project.parent / "output" / f"{project.stem}_render.{ext}"
    )
    if not output_path:
        out.parent.mkdir(parents=True, exist_ok=True)
        out = out.resolve()
    dur = _render_duration(project)
    cmd = (
        ["melt", str(project), "in=0", f"out={max(0, dur - 1)}",
         "-consumer", f"avformat:{out}"]
        + RENDER_PROFILES[profile].split()
    )
    total = dur
    # out conocido para progreso aunque el proyecto no declare duración
    job_id = uuid.uuid4().hex[:8]
    with _LOCK:
        running = sum(1 for j in _JOBS.values() if j["status"] == "running")
        if running >= MAX_CONCURRENT_RENDERS:
            raise RuntimeError(
                "máximo 2 renders concurrentes; espera o cancela uno"
            )
        _evict_jobs()
        _JOBS[job_id] = {
            "status": "running", "pid": None, "output": str(out),
            "project": str(project), "profile": profile,
            "progress": 0, "returncode": None, "log": [],
        }
    thread = threading.Thread(
        target=_run_melt, args=(job_id, cmd, total),
        name=f"render-{job_id}", daemon=True,
    )
    thread.start()
    return {"job_id": job_id, "output": str(out), "status": "running"}


def get_render_status(job_id: str) -> dict:
    """Estado actual de un job de render."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise KeyError(f"Job desconocido: {job_id!r}")
        log: list[str] = list(job.get("log", []))
        return {
            "job_id": job_id,
            "status": job["status"],
            "progress": job["progress"],
            "output": job["output"],
            "returncode": job["returncode"],
            "log_tail": "\n".join(log[-_LOG_TAIL:]),
        }


def cancel_render(job_id: str) -> dict:
    """Termina el proceso melt si sigue corriendo."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise KeyError(f"Job desconocido: {job_id!r}")
        proc = job.get("_proc")
        if job["status"] == "running" and proc is not None:
            job["status"] = "cancelled"
        else:
            return {"job_id": job_id, "status": job["status"]}
    assert proc is not None
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    with _LOCK:
        job = _JOBS[job_id]
        job.pop("_proc", None)
        job["returncode"] = proc.returncode
        return {"job_id": job_id, "status": job["status"]}
