"""Media tools for Kdenlive/MLT projects.

Funciones puras que retornan dicts (sin decoradores MCP). Validan rutas
con ``kdenlive_mcp.security`` y leen/escriben el XML MLT con ElementTree.

Contrato asumido del proyecto paralelo:
- ``validate_path(p, must_exist=False) -> Path``
- ``validate_output_path(p) -> Path``
- ``ProjectStore`` en ``kdenlive_mcp.kdenlive_project`` (import de
  cohesión; la persistencia aquí usa ET directo, formato MLT con
  ``playlist id="playlistN"``, producers avformat con ``resource`` absoluta
  y ``tractor``/``multitrack``).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from shutil import which
from xml.etree import ElementTree as ET

try:
    from kdenlive_mcp.security import validate_path, validate_output_path
except ImportError:  # Implementación paralela: contrato mínimo local.
    def validate_path(p, must_exist=False):  # type: ignore[no-redef]
        path = Path(p).expanduser()
        if must_exist and not path.exists():
            raise FileNotFoundError(f"Ruta no encontrada: {p}")
        return path

    def validate_output_path(p):  # type: ignore[no-redef]
        path = Path(p).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

try:
    from kdenlive_mcp.kdenlive_project import ProjectStore
except ImportError:  # Implementación paralela: aún no existe.
    ProjectStore = None  # type: ignore[no-redef,assignment]

FFPROBE_TIMEOUT = 30
FFMPEG_TIMEOUT = 300
SILENCE_TIMEOUT = 120
_SILENCE_RE = r"silence_{}:\s*(-?\d+(?:\.\d+)?)"


def _require_tool(name: str) -> str:
    exe = which(name)
    if exe is None:
        raise FileNotFoundError(f"Herramienta requerida no disponible: {name}")
    return exe


def _prop(parent: ET.Element, name: str, value: str) -> None:
    for prop in parent.findall("property"):
        if prop.get("name") == name:
            prop.text = value
            return
    prop = ET.SubElement(parent, "property", {"name": name})
    prop.text = value


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Ejecuta un subproceso sin shell y con timeout (nunca shell=True)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Timeout ({timeout}s) ejecutando: {cmd[0]}") from exc


def _load_root(project_path: str) -> tuple[ET.ElementTree, Path]:
    proj = validate_path(project_path, must_exist=True)
    try:
        tree = ET.parse(str(proj))
    except ET.ParseError as exc:
        raise ValueError(f"Proyecto XML inválido: {project_path}: {exc}") from exc
    if tree.getroot().tag != "mlt":
        raise ValueError(f"No es un proyecto MLT/Kdenlive válido: {project_path}")
    return tree, proj


def _save_tree(tree: ET.ElementTree, proj: Path) -> None:
    ET.indent(tree, space="  ")
    tmp = proj.with_name(proj.name + ".tmp")
    tree.write(str(tmp), encoding="utf-8", xml_declaration=True)
    tmp.replace(proj)


def _producer_by_resource(root: ET.Element, resource: str) -> str | None:
    for prod in root.findall("producer"):
        for prop in prod.findall("property"):
            if prop.get("name") == "resource" and (prop.text or "") == resource:
                pid = prod.get("id")
                if pid:
                    return pid
    return None


def _next_producer_id(root: ET.Element) -> str:
    taken = set()
    prefix = "prod_"
    for prod in root.findall("producer"):
        pid = prod.get("id", "")
        if pid.startswith(prefix) and pid[len(prefix):].isdigit():
            taken.add(int(pid[len(prefix):]))
    n = 1
    while n in taken:
        n += 1
    return f"prod_{n}"


def _insert_producer(root: ET.Element, prod: ET.Element) -> None:
    children = list(root)
    producers = [e for e in children if e.tag == "producer"]
    if producers:
        root.insert(children.index(producers[-1]) + 1, prod)
        return
    pos = next((i for i, e in enumerate(children) if e.tag in ("playlist", "tractor")), len(children))
    root.insert(pos, prod)


def _find_main_bin(root: ET.Element) -> ET.Element | None:
    for pl in root.findall("playlist"):
        if pl.get("id") in ("main_bin", "main bin"):
            return pl
    return None


def _ensure_main_bin(root: ET.Element) -> ET.Element:
    bin_pl = _find_main_bin(root)
    if bin_pl is not None:
        return bin_pl
    bin_pl = ET.Element("playlist", {"id": "main_bin"})
    _prop(bin_pl, "xml_retain", "1")
    root.insert(0, bin_pl)
    return bin_pl


def _bin_has_entry(bin_pl: ET.Element, producer_id: str) -> bool:
    return any(e.tag == "entry" and e.get("producer") == producer_id for e in bin_pl)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def scan_media(folder: str, extensions: str = "mp4,mov,mkv,mp3,wav,jpg,png") -> dict:
    """Lista archivos multimedia de una carpeta con tamaño y duración (si se puede sondear)."""
    base = validate_path(folder, must_exist=True)
    if not base.is_dir():
        raise ValueError(f"No es una carpeta: {folder}")
    exts = {e.strip().lower().lstrip(".") for e in extensions.split(",") if e.strip()}
    if not exts:
        raise ValueError("Lista de extensiones vacía")
    files: list[dict] = []
    for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if len(files) >= 500:
            break
        if not child.is_file() or child.suffix.lower().lstrip(".") not in exts:
            continue
        info: dict = {
            "path": str(child.resolve()),
            "name": child.name,
            "size": child.stat().st_size,
            "extension": child.suffix.lower().lstrip("."),
        }
        probe = _quick_probe(child)
        if probe.get("duration") is not None:
            info["duration"] = probe["duration"]
        if probe.get("width") is not None:
            info["width"] = probe["width"]
            info["height"] = probe["height"]
        files.append(info)
    return {"files": files, "count": len(files)}


def _quick_probe(path: Path) -> dict:
    """Sondeo best-effort: un archivo ilegible no debe romper el escaneo."""
    try:
        return analyze_media(str(path))
    except Exception:
        return {}


def import_media(project_path: str, media_path: str) -> dict:
    """Registra un medio como producer avformat y lo añade al main_bin."""
    media = validate_path(media_path, must_exist=True)
    if not media.is_file():
        raise ValueError(f"Medio no válido (no es archivo): {media_path}")
    tree, proj = _load_root(project_path)
    root = tree.getroot()
    resource = str(media.resolve())
    producer_id = _producer_by_resource(root, resource)
    if producer_id is None:
        producer_id = _next_producer_id(root)
        prod = ET.Element("producer", {"id": producer_id})
        _prop(prod, "mlt_service", "avformat")
        _prop(prod, "resource", resource)
        _insert_producer(root, prod)
    bin_pl = _ensure_main_bin(root)
    if not _bin_has_entry(bin_pl, producer_id):
        bin_pl.append(ET.Element("entry", {"producer": producer_id, "in": "0"}))
    _save_tree(tree, proj)
    return {"producer_id": producer_id, "project_path": str(proj)}


def remove_media(project_path: str, producer_id: str) -> dict:
    """Elimina un producer y todas las entries que lo referencien."""
    if not producer_id:
        raise ValueError("producer_id vacío")
    tree, proj = _load_root(project_path)
    root = tree.getroot()
    target = next((p for p in root.findall("producer") if p.get("id") == producer_id), None)
    if target is None:
        raise ValueError(f"Producer desconocido: {producer_id}")
    root.remove(target)
    removed_entries = 0
    for pl in root.iter("playlist"):
        for entry in [e for e in list(pl) if e.tag == "entry" and e.get("producer") == producer_id]:
            pl.remove(entry)
            removed_entries += 1
    _save_tree(tree, proj)
    return {"producer_id": producer_id, "removed_entries": removed_entries, "project_path": str(proj)}


def analyze_media(media_path: str) -> dict:
    """Sondea un medio con ffprobe y retorna duración, video y audio."""
    media = validate_path(media_path, must_exist=True)
    if not media.is_file():
        raise ValueError(f"Medio no válido (no es archivo): {media_path}")
    ffprobe = _require_tool("ffprobe")
    proc = _run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(media)],
        FFPROBE_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe falló para {media_path}: {(proc.stderr or '').strip()[-500:]}")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe retornó JSON inválido para {media_path}") from exc
    result: dict = {
        "path": str(media.resolve()),
        "size": media.stat().st_size,
        "duration": None,
        "width": None,
        "height": None,
        "fps": None,
        "codec": None,
        "audio": None,
    }
    fmt = data.get("format") or {}
    try:
        result["duration"] = float(fmt["duration"])
    except (KeyError, TypeError, ValueError):
        pass
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video" and result["codec"] is None:
            result["codec"] = stream.get("codec_name")
            result["width"] = stream.get("width")
            result["height"] = stream.get("height")
            result["fps"] = _parse_fps(stream.get("avg_frame_rate"))
        elif stream.get("codec_type") == "audio" and result["audio"] is None:
            result["audio"] = {
                "codec": stream.get("codec_name"),
                "channels": stream.get("channels"),
                "sample_rate": stream.get("sample_rate"),
            }
    return result


def _parse_fps(raw: object) -> float | None:
    if not isinstance(raw, str) or "/" not in raw:
        return None
    try:
        num, den = raw.split("/", 1)
        fps = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        return None
    return fps if fps > 0 else None


def find_silence(media_path: str, threshold_db: float = -35.0, min_duration: float = 0.5) -> dict:
    """Detecta silencios con ffmpeg silencedetect; retorna [{start, end}]."""
    media = validate_path(media_path, must_exist=True)
    if not media.is_file():
        raise ValueError(f"Medio no válido (no es archivo): {media_path}")
    if min_duration <= 0:
        raise ValueError(f"min_duration debe ser > 0: {min_duration}")
    ffmpeg = _require_tool("ffmpeg")
    proc = _run(
        [
            ffmpeg, "-hide_banner", "-i", str(media),
            "-af", f"silencedetect=noise={threshold_db}dB:d={min_duration}",
            "-vn", "-sn", "-dn", "-f", "null", "-",
        ],
        SILENCE_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg falló en {media_path}: {(proc.stderr or '').strip()[-500:]}")
    silences: list[dict] = []
    pending: float | None = None
    for line in (proc.stderr or "").splitlines():
        start = re.search(_SILENCE_RE.format("start"), line)
        if start:
            pending = float(start.group(1))
            continue
        end = re.search(_SILENCE_RE.format("end"), line)
        if end:
            end_t = float(end.group(1))
            silences.append({"start": pending if pending is not None else 0.0, "end": end_t})
            pending = None
    if pending is not None:  # Silencio que llega hasta el final sin marca de cierre.
        try:
            total = analyze_media(str(media)).get("duration")
        except Exception:
            total = None
        if total is not None:
            silences.append({"start": pending, "end": float(total)})
    return {"silences": silences, "threshold_db": threshold_db, "min_duration": min_duration}


def extract_audio(video_path: str, output_path: str = "") -> dict:
    """Extrae el audio: WAV→PCM, resto de extensiones soportadas→AAC."""
    src = validate_path(video_path, must_exist=True)
    if not src.is_file():
        raise ValueError(f"Video no válido (no es archivo): {video_path}")
    if not output_path:
        output_path = str(src.parent / f"{src.stem}_audio.m4a")
    out = validate_output_path(output_path)
    ext = out.suffix.lower()
    if ext == ".wav":
        audio_codec = "pcm_s16le"
    elif ext in (".m4a", ".aac", ".mp4", ".mov", ".mkv"):
        audio_codec = "aac"
    else:
        raise ValueError(f"Extensión de salida no soportada: {ext} (usa .wav o .m4a)")
    ffmpeg = _require_tool("ffmpeg")
    proc = _run(
        [ffmpeg, "-hide_banner", "-y", "-i", str(src), "-vn", "-c:a", audio_codec, str(out)],
        FFMPEG_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg no pudo extraer audio: {(proc.stderr or '').strip()[-500:]}")
    return {"output": str(out.resolve())}


def generate_proxy(media_path: str, output_path: str = "", width: int = 640) -> dict:
    """Genera un proxy liviano escalado (H.264, CRF 28, preset veryfast)."""
    src = validate_path(media_path, must_exist=True)
    if not src.is_file():
        raise ValueError(f"Medio no válido (no es archivo): {media_path}")
    if width <= 0:
        raise ValueError(f"width debe ser > 0: {width}")
    if output_path:
        out = validate_output_path(output_path)
    else:
        out = src.with_name(f"{src.stem}_proxy.mp4")
    ffmpeg = _require_tool("ffmpeg")
    proc = _run(
        [
            ffmpeg, "-hide_banner", "-y", "-i", str(src),
            "-vf", f"scale={width}:-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-c:a", "aac", str(out),
        ],
        FFMPEG_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg no pudo generar proxy: {(proc.stderr or '').strip()[-500:]}")
    return {"proxy_path": str(out.resolve())}
