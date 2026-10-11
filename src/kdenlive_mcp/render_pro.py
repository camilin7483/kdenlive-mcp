"""Render pro con aceleración HW (VAAPI/QSV), cola y rangos.

Todo sobre melt (`melt proyecto.kdenlive in=.. out=.. -consumer
avformat:OUT <args>`), con jobs en background thread y estado global
thread-safe en _JOBS (mismo patrón que render_tools, registro propio).

Perfiles HW en HW_PROFILES con args estilo MLT avformat. Si el encoder
HW falla en melt, el job reintenta solo en CPU (fallback automático) y
`get_render_status` expone `fallback_from`; solo errores no-HW quedan
en error con el log de melt tal cual.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger(__name__)

try:
    from kdenlive_mcp.security import safe_name, validate_output_path, validate_path
except ImportError:  # pragma: no cover
    try:
        from .security import (  # type: ignore
            safe_name,
            validate_output_path,
            validate_path,
        )
    except ImportError:
        def validate_path(p: str, must_exist: bool = False, **_: object) -> Path:
            path = Path(p).resolve()
            if must_exist and not path.exists():
                raise FileNotFoundError(f"No existe: {p}")
            return path

        def validate_output_path(p: str) -> Path:
            path = Path(p).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return path

        def safe_name(s: str) -> str:
            out = re.sub(r"[^A-Za-z0-9_-]", "_", str(s).strip()) or "id"
            return re.sub(r"_+", "_", out).strip("_")[:64] or "id"

try:
    from kdenlive_mcp.render_tools import RENDER_PROFILES as _SW_PROFILES
except ImportError:  # pragma: no cover
    try:
        from .render_tools import RENDER_PROFILES as _SW_PROFILES  # type: ignore
    except ImportError:
        _SW_PROFILES = {
            "MP4-H264/AAC": (
                "f=mp4 movflags=+faststart vcodec=libx264 crf=23 g=15 "
                "acodec=aac ab=160k"
            ),
            "MP4-H265": "f=mp4 vcodec=libx265 crf=28 acodec=aac ab=160k",
            "WebM-VP9": "f=webm vcodec=libvpx-vp9 crf=25 acodec=libopus",
            "Preview": (
                "f=mp4 vcodec=libx264 crf=28 preset=veryfast "
                "acodec=aac ab=128k"
            ),
        }

HW_PROFILES: dict[str, str] = {
    "MP4-H264-VAAPI": (
        "f=mp4 movflags=+faststart vcodec=h264_vaapi vb=12M "
        "acodec=aac ab=160k"
    ),
    "MP4-H264-QSV": (
        "f=mp4 movflags=+faststart vcodec=h264_qsv vb=12M "
        "acodec=aac ab=160k"
    ),
    "MP4-H265-VAAPI": (
        "f=mp4 movflags=+faststart vcodec=hevc_vaapi vb=12M "
        "acodec=aac ab=160k"
    ),
    "MP4-1080p30-VAAPI": (
        "f=mp4 movflags=+faststart s=1920x1080 vcodec=h264_vaapi vb=12M "
        "acodec=aac ab=160k"
    ),
    "MP4-4K30-VAAPI": (
        "f=mp4 movflags=+faststart s=3840x2160 vcodec=hevc_vaapi vb=30M "
        "acodec=aac ab=192k"
    ),
    "MP4-Vertical-VAAPI": (
        "f=mp4 movflags=+faststart s=1080x1920 vcodec=h264_vaapi vb=12M "
        "acodec=aac ab=160k"
    ),
}

_HW_PROFILE_EXTENSIONS: dict[str, str] = {
    "MP4-H264-VAAPI": "mp4",
    "MP4-H264-QSV": "mp4",
    "MP4-H265-VAAPI": "mp4",
    "MP4-1080p30-VAAPI": "mp4",
    "MP4-4K30-VAAPI": "mp4",
    "MP4-Vertical-VAAPI": "mp4",
}

DEFAULT_HW_PROFILE = "MP4-H264-VAAPI"
SOFTWARE_FALLBACK_PROFILE = "Preview"
_FALLBACK_DURATION = 149
_DEFAULT_FPS = 30.0
_JOB_TIMEOUT = 1800.0
_POLL_INTERVAL = 1.0
_RENDER_NODE = "/dev/dri/renderD128"
_QSV_OK: bool | None = None

_FRAME_RE = re.compile(r"Current Frame:\s*(\d+)", re.IGNORECASE)
_LOG_KEEP = 200
_LOG_TAIL = 20

_JOBS: dict[str, dict] = {}
_QUEUES: dict[str, dict] = {}
_LOCK = threading.Lock()
MAX_CONCURRENT_HW_RENDERS = 2
MAX_QUEUES = 20
try:
    from kdenlive_mcp.security import MAX_JOBS
except ImportError:
    MAX_JOBS = 20

# Pistas de fallo del encoder HW en el log de melt (para fallback a CPU).
_HW_FAILURE_HINTS: tuple[str, ...] = (
    "vaapi", "h264_vaapi", "hevc_vaapi", "h264_qsv", "hevc_qsv",
    "qsv", "mfx", "hwaccel", "va display", "init failed",
    "device failed", "unknown encoder",
)

# Fallback CPU por perfil HW (misma resolución cuando existe).
_HW_TO_CPU: dict[str, str] = {
    "MP4-H264-VAAPI": "MP4-H264/AAC",
    "MP4-H264-QSV": "MP4-H264/AAC",
    "MP4-H265-VAAPI": "MP4-H265",
    "MP4-1080p30-VAAPI": "MP4-1080p30",
    "MP4-4K30-VAAPI": "MP4-4K30",
    "MP4-Vertical-VAAPI": "MP4-Vertical-Shorts",
}


# -- detección HW ------------------------------------------------------

def _run_capture(cmd: list[str], timeout: float = 15.0) -> str:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "") + (proc.stderr or "")


def detect_hwaccel() -> dict:
    """Detecta aceleración HW disponible.

    Retorna {vaapi, qsv, render_node, ffmpeg_ok}.
    """
    ffmpeg = shutil.which("ffmpeg")
    ffmpeg_ok = ffmpeg is not None
    node = _RENDER_NODE if Path(_RENDER_NODE).exists() else None
    if node is None:  # fallback: cualquier renderD* presente
        try:
            found = sorted(Path("/dev/dri").glob("renderD*"))
            if found:
                node = str(found[0])
        except OSError:
            node = None
    hwaccels = _run_capture(["ffmpeg", "-hide_banner", "-hwaccels"]).lower()
    encoders = _run_capture(["ffmpeg", "-hide_banner", "-encoders"]).lower()
    vaapi = (
        ffmpeg_ok and node is not None
        and "vaapi" in hwaccels and "h264_vaapi" in encoders
    )
    qsv = (
        ffmpeg_ok and node is not None
        and "qsv" in hwaccels and "h264_qsv" in encoders
        and _qsv_preflight(str(ffmpeg) if ffmpeg else "ffmpeg")
    )
    return {
        "vaapi": bool(vaapi),
        "qsv": bool(qsv),
        "render_node": node,
        "ffmpeg_ok": bool(ffmpeg_ok),
    }


def _qsv_preflight(ffmpeg: str) -> bool:
    """Verifica que QSV codifique de verdad (el encoder puede existir
    con el driver roto: MFX_ERR_DEVICE_FAILED -17). Caché en memoria."""
    global _QSV_OK
    if _QSV_OK is not None:
        return _QSV_OK
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc2=s=320x240:r=10:d=1",
             "-c:v", "h264_qsv", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60)
        _QSV_OK = proc.returncode == 0
    except Exception:
        _QSV_OK = False
    return _QSV_OK


def list_hw_profiles() -> dict:
    """Lista los perfiles de render con aceleración HW."""
    return {
        "profiles": [
            {"name": name, "args": args,
             "extension": _HW_PROFILE_EXTENSIONS.get(name, "mp4")}
            for name, args in HW_PROFILES.items()
        ]
    }


# -- internos: proyecto ------------------------------------------------

def _profile_args(profile: str) -> tuple[str, str]:
    """Args MLT + extensión; acepta perfiles HW y software (fallback)."""
    if profile in HW_PROFILES:
        return HW_PROFILES[profile], _HW_PROFILE_EXTENSIONS.get(profile, "mp4")
    if profile in _SW_PROFILES:
        ext = "webm" if profile == "WebM-VP9" else "mp4"
        return _SW_PROFILES[profile], ext
    raise ValueError(
        f"Perfil desconocido: {profile!r}. "
        f"HW: {sorted(HW_PROFILES)}; software: {sorted(_SW_PROFILES)}"
    )


def _render_duration(project: Path) -> int:
    """Duración en frames: max entre suma secuencial por playlist y out."""
    try:
        root = ET.parse(project).getroot()
    except ET.ParseError:
        return _FALLBACK_DURATION
    best = 0
    tractor = root.find("tractor")
    if tractor is not None:
        out = (tractor.get("out") or "").lstrip("-")
        if out.isdigit():
            best = max(best, int(out))
    for pl in root.findall("playlist"):
        if pl.get("id") == "main_bin":
            continue
        total = 0
        for entry in pl.findall("entry"):
            try:
                ein, eout = int(entry.get("in", 0)), int(entry.get("out", 0))
            except (TypeError, ValueError):
                continue
            if eout >= ein:
                total += eout - ein + 1
        best = max(best, total)
    return best or _FALLBACK_DURATION


def _project_fps(project: Path) -> float:
    """fps del <profile> del proyecto; 30.0 si no se puede leer."""
    try:
        root = ET.parse(project).getroot()
    except ET.ParseError:
        return _DEFAULT_FPS
    prof = root.find("profile")
    if prof is not None:
        try:
            num = float(prof.get("frame_rate_num", ""))
            den = float(prof.get("frame_rate_den", ""))
            if num > 0 and den > 0:
                return num / den
        except (TypeError, ValueError):
            pass
    return _DEFAULT_FPS


def _evict_jobs() -> None:
    done = [jid for jid, j in _JOBS.items() if j["status"] != "running"]
    overflow = len(_JOBS) - MAX_JOBS
    for jid in done[: max(0, overflow)]:
        _JOBS.pop(jid, None)


def _evict_queues() -> None:
    done = [qid for qid, q in _QUEUES.items() if q.get("status") == "done"]
    overflow = len(_QUEUES) - MAX_QUEUES
    for qid in done[: max(0, overflow)]:
        _QUEUES.pop(qid, None)


def _is_hw_failure(lines: list[str]) -> bool:
    """True si la cola del log de melt culpa al encoder HW."""
    tail = "\n".join(lines[-_LOG_TAIL:]).lower()
    return any(hint in tail for hint in _HW_FAILURE_HINTS)


def _fallback_for(profile: str) -> str:
    """Perfil CPU equivalente al HW dado (misma resolución si existe)."""
    cpu = _HW_TO_CPU.get(profile, SOFTWARE_FALLBACK_PROFILE)
    if cpu in _SW_PROFILES:
        return cpu
    return SOFTWARE_FALLBACK_PROFILE


# -- internos: worker --------------------------------------------------

def _run_once(job_id: str, cmd: list[str], total: int | None,
               melt_log: list[str]) -> int | None:
    """Una pasada de melt; retorna returncode (None si hubo timeout)."""
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
    except OSError as exc:
        with _LOCK:
            _JOBS[job_id].update(
                {"status": "error", "returncode": -1,
                 "log": list(melt_log) + [f"no se pudo lanzar melt: {exc}"]}
            )
        return -1
    with _LOCK:
        _JOBS[job_id]["pid"] = proc.pid
        _JOBS[job_id]["_proc"] = proc
    current = 0
    assert proc.stderr is not None
    for line in proc.stderr:
        line = line.rstrip()
        if line:
            melt_log.append(line)
            if len(melt_log) > _LOG_KEEP:
                del melt_log[: len(melt_log) - _LOG_KEEP]
        m = _FRAME_RE.search(line)
        if m:
            current = int(m.group(1))
        with _LOCK:
            job = _JOBS[job_id]
            job["log"] = list(melt_log)
            if total:
                job["progress"] = min(99, int(current * 100 / max(total, 1)))
    try:
        proc.wait(timeout=_JOB_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        with _LOCK:
            _JOBS[job_id]["status"] = "error"
            _JOBS[job_id]["returncode"] = -9
            _JOBS[job_id]["log"] = list(melt_log) + [
                f"timeout: melt superó {_JOB_TIMEOUT:.0f}s y fue terminado"]
        return None
    with _LOCK:
        _JOBS[job_id].pop("_proc", None)
    return proc.returncode


def _fallback_cmd(job: dict) -> tuple[str, list[str], int] | None:
    """Comando CPU equivalente al job HW (None si no se puede armar)."""
    cpu = _fallback_for(str(job.get("profile", "")))
    try:
        args, _ = _profile_args(cpu)
    except ValueError:
        return None
    cmd = (
        ["melt", str(job["project"]),
         f"in={job['in_frame']}", f"out={job['out_frame']}",
         "-consumer", f"avformat:{job['output']}"]
        + args.split()
    )
    total = int(job["out_frame"]) - int(job["in_frame"]) + 1
    return cpu, cmd, total


def _run_melt(job_id: str, cmd: list[str], total: int | None) -> None:
    """Worker en thread: melt HW y fallback automático a CPU si el
    encoder HW falla (timeout y cancelación no reintentan)."""
    melt_log: list[str] = []
    rc = _run_once(job_id, cmd, total, melt_log)
    with _LOCK:
        job = _JOBS[job_id]
        status, profile = job["status"], job.get("profile", "")
    if (rc is not None and rc != 0 and status == "running"
            and profile in HW_PROFILES and _is_hw_failure(melt_log)):
        nxt = _fallback_cmd(dict(job))
        if nxt is not None:
            cpu, fcmd, ftotal = nxt
            with _LOCK:
                _JOBS[job_id]["log"] = list(melt_log) + [
                    f"fallback automático: {profile} falló en HW; "
                    f"reintentando en CPU con {cpu}"]
                _JOBS[job_id]["progress"] = 0
            rc = _run_once(job_id, fcmd, ftotal, melt_log)
            with _LOCK:
                _JOBS[job_id]["fallback_from"] = profile
                _JOBS[job_id]["profile"] = cpu
    with _LOCK:
        job = _JOBS[job_id]
        job.pop("_proc", None)
        if job["status"] == "running":  # cancel_render pudo marcarlo antes
            job["status"] = "done" if rc == 0 else "error"
        if rc is not None:
            job["returncode"] = rc
        if job["status"] == "done":
            job["progress"] = 100
        job["log"] = list(melt_log) or list(job.get("log", []))


def _spawn(job_id: str, cmd: list[str], total: int | None) -> None:
    thread = threading.Thread(
        target=_run_melt, args=(job_id, cmd, total),
        name=f"render-pro-{job_id}", daemon=True,
    )
    thread.start()


def _register_job(project: Path, out: Path, profile: str,
                  cmd: list[str], total: int,
                  in_frame: int = 0, out_frame: int = 0) -> str:
    job_id = uuid.uuid4().hex[:8]
    with _LOCK:
        running = sum(1 for j in _JOBS.values() if j["status"] == "running")
        if running >= MAX_CONCURRENT_HW_RENDERS:
            raise RuntimeError(
                "máximo 2 renders HW concurrentes; espera o cancela uno")
        _evict_jobs()
        _JOBS[job_id] = {
            "status": "running", "pid": None, "output": str(out),
            "project": str(project), "profile": profile,
            "progress": 0, "returncode": None, "log": [],
            "in_frame": in_frame, "out_frame": out_frame,
            "fallback_from": "",
        }
    _spawn(job_id, cmd, total)
    return job_id


def _build_cmd(project: Path, out: Path, profile: str,
               in_frame: int, out_frame: int) -> tuple[list[str], int]:
    args, _ = _profile_args(profile)
    cmd = (
        ["melt", str(project), f"in={in_frame}", f"out={out_frame}",
         "-consumer", f"avformat:{out}"]
        + args.split()
    )
    return cmd, out_frame - in_frame + 1


# -- API ---------------------------------------------------------------

def render_hw(project_path: str, output_path: str = "",
              profile_name: str = DEFAULT_HW_PROFILE) -> dict:
    """Render HW en background con fallback automático a CPU.

    Si el encoder HW falla en melt, el job reintenta solo con el perfil
    CPU equivalente (misma resolución cuando existe) y el estado expone
    `fallback_from` con el perfil HW original. QSV se rechaza de entrada
    si el preflight falla (mensaje accionable).
    """
    profile = profile_name or DEFAULT_HW_PROFILE
    if profile == "MP4-H264-QSV":
        import shutil as _sh
        ff = _sh.which("ffmpeg") or "ffmpeg"
        if not _qsv_preflight(ff):
            raise RuntimeError(
                "QSV no funcional en este sistema (MFX device failed); "
                "usa MP4-H264-VAAPI"
            )
    args, ext = _profile_args(profile)  # valida el perfil antes de tocar disco
    _ = args
    project = validate_path(project_path, must_exist=True)
    if output_path:
        out = validate_output_path(output_path)
    else:
        out = validate_output_path(
            str(project.parent / "output"
                / f"{project.stem}_{safe_name(profile)}.{ext}"))
    dur = _render_duration(project)
    out_frame = max(0, dur - 1)
    cmd, total = _build_cmd(project, out, profile, 0, out_frame)
    job_id = _register_job(project, out, profile, cmd, total, 0, out_frame)
    return {"job_id": job_id, "output": str(out), "status": "running"}


def get_render_status(job_id: str) -> dict:
    """Estado actual de un job de render pro."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise KeyError(f"Job desconocido: {job_id!r}")
        job_log: list[str] = list(job.get("log", []))
        return {
            "job_id": job_id,
            "status": job["status"],
            "progress": job["progress"],
            "output": job["output"],
            "profile": job.get("profile", ""),
            "fallback_from": job.get("fallback_from", ""),
            "returncode": job["returncode"],
            "log_tail": "\n".join(job_log[-_LOG_TAIL:]),
        }


def cancel_render(job_id: str) -> dict:
    """Cancela el job: termina melt si corre, o lo marca si aún no lanzó."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise KeyError(f"Job desconocido: {job_id!r}")
        proc = job.get("_proc")
        if job["status"] in ("running", "queued"):
            if proc is None:
                job["status"] = "cancelled"
                return {"job_id": job_id, "status": "cancelled"}
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


def _wait_job(job_id: str, timeout: float = _JOB_TIMEOUT) -> dict:
    """Bloquea hasta estado terminal; timeout marca error vía cancel."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = get_render_status(job_id)
        if st["status"] in ("done", "error", "cancelled"):
            return st
        time.sleep(_POLL_INTERVAL)
    try:
        cancel_render(job_id)
    except (KeyError, OSError):
        pass
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None and job["status"] == "running":
            job["status"] = "error"
            job["returncode"] = -9
            job["log"] = list(job.get("log", [])) + [
                f"timeout: item superó {timeout:.0f}s"]
    return get_render_status(job_id)


def _run_queue(queue_id: str, items: list[dict]) -> None:
    """Worker de cola: un item por vez, sigue aunque uno falle."""
    snapshots: list[dict] = []
    for item in items:
        job_id = item["job_id"]
        cmd, total = item["cmd"], item["total"]
        with _LOCK:
            skipped = _JOBS[job_id]["status"] == "cancelled"
            if skipped:
                snapshots.append({
                    "job_id": job_id, "output": item.get("output", ""),
                    "profile": item["profile"], "status": "cancelled",
                    "returncode": _JOBS[job_id].get("returncode"),
                })
            else:
                _JOBS[job_id]["status"] = "running"
        if not skipped:
            _spawn(job_id, cmd, total)
            final = _wait_job(job_id, timeout=_JOB_TIMEOUT)
            snapshots.append({
                "job_id": job_id, "output": final["output"],
                "profile": final.get("profile", item["profile"]),
                "status": final["status"],
                "returncode": final["returncode"],
            })
        with _LOCK:
            queue = _QUEUES.get(queue_id)
            if queue is not None:
                queue["jobs"] = list(snapshots)
                queue["done"] = len(snapshots)
                if len(snapshots) == len(items):
                    queue["status"] = "done"
    with _LOCK:
        queue = _QUEUES.get(queue_id)
        if queue is not None and queue.get("status") == "running":
            queue["status"] = "done"


def render_queue(items: list[dict]) -> dict:
    """Encola renders HW y los ejecuta en orden en un solo thread.

    item = {project_path, output_path (opcional),
            profile_name (opcional, default HW)}.
    Retorna {queue_id, jobs:[...]} con un job por item (estado inicial
    "queued"; el worker los pasa a running/done/error en orden). Un
    item que falla se marca en error y la cola continúa.
    """
    if not isinstance(items, list) or not items:
        raise ValueError("items debe ser una lista no vacía de dicts")
    queue_id = uuid.uuid4().hex[:8]
    prepared: list[dict] = []
    for pos, item in enumerate(items):
        if not isinstance(item, dict) or "project_path" not in item:
            raise ValueError(
                f"item {pos} inválido: se requiere dict con project_path")
        profile = item.get("profile_name") or DEFAULT_HW_PROFILE
        job_id = uuid.uuid4().hex[:8]
        try:
            _, ext = _profile_args(profile)
            project = validate_path(item["project_path"], must_exist=True)
            out_raw = item.get("output_path") or ""
            out = (validate_output_path(out_raw) if out_raw
                   else validate_output_path(
                       str(project.parent / "output" / (
                           f"{project.stem}_{safe_name(profile)}"
                           f"_q{pos}.{ext}"))))
            dur = _render_duration(project)
            out_frame = max(0, dur - 1)
            cmd, total = _build_cmd(project, out, profile, 0, out_frame)
        except (ValueError, FileNotFoundError, PermissionError, OSError) as exc:
            with _LOCK:
                _JOBS[job_id] = {
                    "status": "error", "pid": None,
                    "output": str(item.get("output_path") or ""),
                    "project": str(item.get("project_path") or ""),
                    "profile": profile, "progress": 0,
                    "returncode": -1, "log": [f"item {pos} inválido: {exc}"],
                    "in_frame": 0, "out_frame": 0, "fallback_from": "",
                }
            prepared.append({"job_id": job_id, "cmd": [], "total": 0,
                             "profile": profile, "output": "",
                             "failed": str(exc)})
            continue
        with _LOCK:
            _evict_jobs()
            _JOBS[job_id] = {
                "status": "queued", "pid": None, "output": str(out),
                "project": str(project), "profile": profile,
                "progress": 0, "returncode": None, "log": [],
                "in_frame": 0, "out_frame": out_frame, "fallback_from": "",
            }
        prepared.append({"job_id": job_id, "cmd": cmd, "total": total,
                         "profile": profile, "output": str(out)})
    runnable = [p for p in prepared if p["cmd"]]
    with _LOCK:
        _evict_queues()
        _QUEUES[queue_id] = {
            "status": "running" if runnable else "done",
            "jobs": [{"job_id": p["job_id"], "output": p["output"],
                      "profile": p["profile"], "status": _JOBS[p["job_id"]][
                          "status"]}
                     for p in prepared],
            "done": sum(1 for p in prepared if not p["cmd"]),
        }
    if runnable:
        thread = threading.Thread(
            target=_run_queue, args=(queue_id, runnable),
            name=f"render-queue-{queue_id}", daemon=True,
        )
        thread.start()
    with _LOCK:
        jobs_snapshot = list(_QUEUES[queue_id]["jobs"])
    return {"queue_id": queue_id, "jobs": jobs_snapshot}


def get_queue_status(queue_id: str) -> dict:
    """Estado actual de una cola de renders."""
    with _LOCK:
        queue = _QUEUES.get(queue_id)
        if queue is None:
            raise KeyError(f"Cola desconocida: {queue_id!r}")
        jobs = []
        for entry in queue["jobs"]:
            job = _JOBS.get(entry["job_id"], {})
            jobs.append({
                "job_id": entry["job_id"],
                "output": entry.get("output", ""),
                "profile": entry.get("profile", ""),
                "status": job.get("status", entry.get("status", "queued")),
                "progress": job.get("progress", 0),
            })
        if jobs and all(
                j["status"] in ("done", "error", "cancelled") for j in jobs):
            queue["status"] = "done"
        return {"queue_id": queue_id, "status": queue["status"], "jobs": jobs}


def render_range(project_path: str, output_path: str = "",
                 profile_name: str = SOFTWARE_FALLBACK_PROFILE,
                 start_sec: float = 0.0,
                 duration_sec: float = 10.0) -> dict:
    """Render solo del rango [start, start+duration) en background.

    Frames calculados al fps del proyecto (default 30fps).
    """
    if start_sec < 0:
        raise ValueError(f"start_sec ({start_sec}) debe ser >= 0")
    if duration_sec <= 0:
        raise ValueError(f"duration_sec ({duration_sec}) debe ser > 0")
    profile = profile_name or SOFTWARE_FALLBACK_PROFILE
    args, ext = _profile_args(profile)
    _ = args
    project = validate_path(project_path, must_exist=True)
    fps = _project_fps(project)
    in_frame = int(start_sec * fps)
    n_frames = max(1, int(round(duration_sec * fps)))
    out_frame = in_frame + n_frames - 1
    if output_path:
        out = validate_output_path(output_path)
    else:
        out = validate_output_path(
            str(project.parent / "output" / (
                f"{project.stem}_range{in_frame}-{out_frame}.{ext}")))
    cmd, total = _build_cmd(project, out, profile, in_frame, out_frame)
    job_id = _register_job(project, out, profile, cmd, total,
                            in_frame, out_frame)
    return {"job_id": job_id, "output": str(out), "status": "running",
            "in": in_frame, "out": out_frame, "fps": fps}
