"""Validación y QA de proyectos Kdenlive (XML MLT) y salidas renderizadas."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

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

# Producers cuyo resource NO es un archivo en disco
_NON_FILE_SERVICES = {"color", "pango", "qtext"}
_FPS_DEFAULT = 30


def _parse(project_path: str) -> tuple[ET.Element, Path]:
    project = validate_path(project_path)
    try:
        root = ET.parse(project).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"XML inválido en {project_path}: {exc}") from exc
    return root, project


def _known_ids(root: ET.Element) -> set[str]:
    ids: set[str] = set()
    for tag in ("producer", "playlist", "tractor", "chain", "link"):
        for el in root.findall(tag):
            el_id = el.get("id")
            if el_id:
                ids.add(el_id)
    return ids


def _int_or_none(value: str | None) -> int | None:
    if value is None or not value.lstrip("-").isdigit():
        return None
    return int(value)


def validate_project(project_path: str) -> dict:
    """Valida estructura MLT: profile, tractor, producers, entries."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        root, project = _parse(project_path)
    except (FileNotFoundError, ValueError) as exc:
        return {"valid": False, "errors": [str(exc)], "warnings": []}

    if root.find("profile") is None:
        errors.append("Falta el elemento <profile>")

    tractor = root.find("tractor")
    if tractor is None:
        errors.append("Falta el elemento <tractor>")
        tracks: list[ET.Element] = []
    else:
        tracks = tractor.findall("track")
        # Compat: ProjectStore/timeline_tools generan
        # <tractor><multitrack><track producer=.../></multitrack></tractor>
        for mt in tractor.findall("multitrack"):
            tracks.extend(mt.findall("track"))
        if not tracks:
            errors.append("El tractor no tiene tracks")

    known = _known_ids(root)
    for producer in root.findall("producer"):
        pid = producer.get("id", "?")
        service = producer.findtext("property[@name='mlt_service']")
        resource = producer.findtext("property[@name='resource']")
        if service not in _NON_FILE_SERVICES and not resource:
            errors.append(f"Producer {pid!r} sin resource")
        for prop in ("in", "out"):
            val = producer.findtext(f"property[@name='{prop}']")
            if val is not None and not val.lstrip("-").isdigit():
                errors.append(f"Producer {pid!r}: {prop} no numérico ({val!r})")
        pin = _int_or_none(producer.findtext("property[@name='in']"))
        pout = _int_or_none(producer.findtext("property[@name='out']"))
        if pin is not None and pout is not None and pin > pout:
            errors.append(f"Producer {pid!r}: in ({pin}) > out ({pout})")

    for playlist in root.findall("playlist"):
        for entry in playlist.findall("entry"):
            ref = entry.get("producer")
            if ref and ref not in known:
                errors.append(
                    f"Entry en {playlist.get('id')!r} referencia "
                    f"producer inexistente {ref!r}"
                )
            ein, eout = _int_or_none(entry.get("in")), _int_or_none(entry.get("out"))
            if ein is not None and eout is not None and ein > eout:
                errors.append(
                    f"Entry {ref!r} en {playlist.get('id')!r}: "
                    f"in ({ein}) > out ({eout})"
                )

    for playlist in root.findall("playlist"):
        if not playlist.findall("entry") and not playlist.findall("blank"):
            warnings.append(f"Track vacío: {playlist.get('id')!r}")

    # Bloqueante: medios referenciados que faltan en disco (bloquea render).
    for producer in root.findall("producer"):
        service = producer.findtext("property[@name='mlt_service']")
        resource = producer.findtext("property[@name='resource']")
        if not resource or service in _NON_FILE_SERVICES:
            continue
        candidate = Path(resource)
        if not candidate.is_absolute():
            candidate = project.parent / resource
        if not candidate.exists():
            errors.append(
                f"Medio faltante: producer {producer.get('id')!r} -> {resource!r}"
            )

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def check_missing_media(project_path: str) -> dict:
    """Detecta producers cuyo resource apunta a archivos inexistentes."""
    root, project = _parse(project_path)
    missing: list[dict] = []
    for producer in root.findall("producer"):
        service = producer.findtext("property[@name='mlt_service']")
        resource = producer.findtext("property[@name='resource']")
        if not resource or service in _NON_FILE_SERVICES:
            continue
        candidate = Path(resource)
        if not candidate.is_absolute():
            candidate = project.parent / resource
        if not candidate.exists():
            missing.append(
                {"producer_id": producer.get("id"), "resource": resource}
            )
    return {"missing": missing, "ok": not missing}


def check_timeline(project_path: str) -> dict:
    """Resume tracks/clips/duración y detecta problemas de timeline."""
    root, _ = _parse(project_path)
    tractor = root.find("tractor")
    track_ids: list = []
    if tractor is not None:
        _tracks = list(tractor.findall("track"))
        # Compat: <tractor><multitrack><track .../></multitrack></tractor>
        for _mt in tractor.findall("multitrack"):
            _tracks.extend(_mt.findall("track"))
        track_ids = [t.get("producer") for t in _tracks]
    playlists = {pl.get("id"): pl for pl in root.findall("playlist")}
    total_clips = 0
    duration: int | None = None
    issues: list[str] = []

    def _bump(out: int | None) -> None:
        nonlocal duration
        if out is not None:
            duration = out if duration is None else max(duration, out)

    if tractor is not None:
        _bump(_int_or_none(tractor.findtext("property[@name='out']")))
        _bump(_int_or_none(tractor.get("out")))
    for tid in track_ids:
        pl = playlists.get(tid) if tid else None
        if pl is None:
            issues.append(f"Track referencia playlist inexistente: {tid!r}")
            continue
        entries = pl.findall("entry")
        if not entries and not pl.findall("blank"):
            issues.append(f"Track vacío: {tid!r}")
        track_len = 0
        for entry in entries:
            total_clips += 1
            ein, eout = _int_or_none(entry.get("in")), _int_or_none(entry.get("out"))
            if ein is not None and eout is not None:
                if ein > eout:
                    issues.append(
                        f"Clip {entry.get('producer')!r} en {tid!r}: "
                        f"in ({ein}) > out ({eout})"
                    )
                else:
                    track_len += eout - ein + 1
        _bump(track_len or None)
    try:
        from kdenlive_mcp.kdenlive_project import timeline_length_frames
    except ImportError:
        timeline_length_frames = None  # type: ignore
    if timeline_length_frames is not None:
        try:
            duration = max(duration or 0, timeline_length_frames(root))
        except Exception:
            pass
    return {
        "tracks": len(track_ids),
        "total_clips": total_clips,
        "duration_frames": duration if duration is not None else 0,
        "issues": issues,
    }


def check_audio(project_path: str) -> dict:
    """Chequeo simple y determinista de audio del timeline."""
    root, _ = _parse(project_path)
    has_volume = any(
        (f.findtext("property[@name='mlt_service']") == "volume")
        for f in root.iter("filter")
    )
    producers = root.findall("producer")
    av_producers = [
        p for p in producers
        if (p.findtext("property[@name='mlt_service']") or "") in
        {"avformat", "avformat-novalidate"}
    ]
    issues: list[dict] = []
    if av_producers and not has_volume:
        issues.append({
            "level": "info",
            "message": f"{len(av_producers)} clip(s) de audio/video sin "
                       "filtro 'volume'",
        })
    for p in av_producers:
        resource = p.findtext("property[@name='resource']") or ""
        if "music" in resource.lower() and not has_volume:
            issues.append({
                "level": "warning",
                "message": f"Clip de música {p.get('id')!r} sin ducking/"
                           "control de volumen",
            })
    return {"issues": issues, "tracks_with_audio": len(av_producers)}


def check_render(output_path: str) -> dict:
    """Inspecciona un archivo renderizado con ffprobe."""
    try:
        path = validate_path(output_path, must_exist=True)
    except (FileNotFoundError, ValueError, PermissionError):
        return {"exists": False, "size": 0, "duration": 0.0,
                "video_codec": "", "audio_codec": "", "ok": False}
    size = path.stat().st_size
    ffprobe = shutil.which("ffprobe")
    duration, vcodec, acodec = 0.0, "", ""
    if ffprobe:
        try:
            proc = subprocess.run(
                [ffprobe, "-v", "quiet", "-print_format", "json",
                 "-show_format", "-show_streams", str(path)],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            proc = None
        if proc is not None and proc.returncode == 0:
            try:
                info = json.loads(proc.stdout or "{}")
            except json.JSONDecodeError:
                info = {}
            for stream in info.get("streams", []):
                if stream.get("codec_type") == "video" and not vcodec:
                    vcodec = stream.get("codec_name", "")
                elif stream.get("codec_type") == "audio" and not acodec:
                    acodec = stream.get("codec_name", "")
            fmt = info.get("format", {})
            try:
                duration = float(fmt.get("duration", 0.0))
            except (TypeError, ValueError):
                duration = 0.0
    return {
        "exists": True,
        "size": size,
        "duration": duration,
        "video_codec": vcodec,
        "audio_codec": acodec,
        "ok": size > 0 and bool(vcodec),
    }


def generate_preview(
    project_path: str,
    output_path: str = "",
    width: int = 640,
    duration_sec: int = 10,
) -> dict:
    """Renderiza un preview rápido a baja resolución con melt.

    Copia el proyecto a tmp, ajusta el profile a `width` (alto
    proporcional) y renderiza los primeros duration_sec*30 frames con el
    perfil Preview de forma síncrona.
    """
    if width <= 0:
        raise ValueError("width debe ser > 0")
    if duration_sec <= 0:
        raise ValueError("duration_sec debe ser > 0")
    project = validate_path(project_path)
    try:
        tree = ET.parse(project)
    except ET.ParseError as exc:
        raise ValueError(f"XML inválido en {project_path}: {exc}") from exc
    root = tree.getroot()
    profile_el = root.find("profile")
    if profile_el is not None:
        try:
            orig_w = int(profile_el.get("width", width))
            orig_h = int(profile_el.get("height", width))
        except ValueError:
            orig_w, orig_h = width, width
        height = max(2, round(orig_h * width / max(orig_w, 1)) // 2 * 2)
        profile_el.set("width", str(width))
        profile_el.set("height", str(height))
    frames = duration_sec * _FPS_DEFAULT
    out = (
        validate_output_path(output_path)
        if output_path
        else project.parent / "output" / f"{project.stem}_preview.mp4"
    )
    if not output_path:
        out.parent.mkdir(parents=True, exist_ok=True)
        out = out.resolve()
    melt = shutil.which("melt")
    if melt is None:
        raise RuntimeError("melt no encontrado en PATH")
    with tempfile.NamedTemporaryFile(
        suffix=".mlt", delete=False
    ) as tmp:
        tmp_path = tmp.name
    try:
        tree.write(tmp_path, encoding="utf-8", xml_declaration=True)
        # Import tardío para evitar dependencia circular entre módulos
        from kdenlive_mcp.render_tools import RENDER_PROFILES
        cmd = (
            [melt, tmp_path, "in=0", f"out={frames - 1}",
             "-consumer", f"avformat:{out}"]
            + RENDER_PROFILES["Preview"].split()
        )
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=max(120, duration_sec * 30),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Preview excedió el timeout") from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"melt falló ({proc.returncode}): "
                f"{(proc.stderr or '').strip()[-500:]}"
            )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return {"preview_path": str(out)}
