"""Herramientas de subtítulos para proyectos Kdenlive (.kdenlive = XML MLT).

Funciones puras sobre el XML con ElementTree: generan/importan/exportan
SRT y los aplican como <filter mlt_service="subtitle"> sobre los clips
del track (filtro = la forma MLT correcta; un producer subtitle en pista
propia compone frames negros y el render sale negro).
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger(__name__)

try:
    from kdenlive_mcp.kdenlive_project import ensure_composite_transitions
except ImportError:
    try:
        from .kdenlive_project import ensure_composite_transitions  # type: ignore
    except ImportError:
        def ensure_composite_transitions(project_path: str) -> dict:  # type: ignore
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

FPS = 30

_TIMESTAMP_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def frames_to_timestamp(frames: int, fps: int = FPS) -> str:
    """Convierte frames a timestamp SRT HH:MM:SS,mmm."""
    if frames < 0:
        raise ValueError("frames debe ser >= 0")
    total_ms = round(frames * 1000 / fps)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def timestamp_to_frames(ts: str, fps: int = FPS) -> int:
    """Convierte timestamp SRT a frames (redondeo al más cercano)."""
    m = re.fullmatch(
        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", ts.strip()
    )
    if not m:
        raise ValueError(f"Timestamp SRT inválido: {ts!r}")
    h, mi, s, ms = map(int, m.groups())
    total_ms = ((h * 60 + mi) * 60 + s) * 1000 + ms
    return round(total_ms * fps / 1000)


def _format_srt(
    texts: list[str], starts: list[int], ends: list[int], fps: int = FPS
) -> str:
    lines: list[str] = []
    for i, (text, start, end) in enumerate(zip(texts, starts, ends), start=1):
        lines.append(str(i))
        lines.append(
            f"{frames_to_timestamp(start, fps)} --> "
            f"{frames_to_timestamp(end, fps)}"
        )
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def parse_srt(content: str) -> list[dict]:
    """Parsea contenido SRT a [{index, start, end, text}]; valida formato."""
    from kdenlive_mcp.security import MAX_SRT_BYTES, MAX_SRT_ENTRIES
    if len(content.encode("utf-8", "ignore")) > MAX_SRT_BYTES:
        raise ValueError("SRT demasiado grande (>2MB)")
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", content.strip())
    entries: list[dict] = []
    for block in blocks:
        if not block.strip():
            continue
        parts = block.strip().splitlines()
        if len(parts) < 3:
            raise ValueError(f"Bloque SRT inválido: {block!r}")
        try:
            index = int(parts[0].strip())
        except ValueError:
            raise ValueError(f"Índice SRT inválido: {parts[0]!r}")
        m = _TIMESTAMP_RE.fullmatch(parts[1].strip())
        if not m:
            raise ValueError(f"Línea de tiempo SRT inválida: {parts[1]!r}")
        start = f"{m.group(1)}:{m.group(2)}:{m.group(3)},{m.group(4)}"
        end = f"{m.group(5)}:{m.group(6)}:{m.group(7)},{m.group(8)}"
        entries.append(
            {"index": index, "start": start, "end": end,
             "text": "\n".join(parts[2:])}
        )
    if not entries:
        raise ValueError("SRT vacío o sin entradas válidas")
    if len(entries) > MAX_SRT_ENTRIES:
        raise ValueError(f"SRT con demasiadas entradas (>{MAX_SRT_ENTRIES})")
    return entries


def _load_tree(project_path: str) -> tuple[ET.ElementTree, Path]:
    project = validate_path(project_path)
    try:
        tree = ET.parse(project)
    except ET.ParseError as exc:
        raise ValueError(f"XML inválido en {project_path}: {exc}") from exc
    return tree, project


def _timeline_frames(root: ET.Element) -> int:
    """Duración del tractor en frames (0 si el proyecto está vacío)."""
    try:
        from kdenlive_mcp.kdenlive_project import timeline_length_frames
    except ImportError:
        try:
            from .kdenlive_project import timeline_length_frames  # type: ignore
        except ImportError:
            return 0
    try:
        return int(timeline_length_frames(root))
    except Exception:
        return 0


def _clamp_to_timeline(
    root: ET.Element, starts: list[int], ends: list[int]
) -> tuple[list[int], list[int], bool]:
    """Recorta frames a [0, duración-1] del tractor; sin contenido no toca nada."""
    duration = _timeline_frames(root)
    if duration <= 0:
        return starts, ends, False
    last = duration - 1
    clamped_s = [min(max(0, s), last) for s in starts]
    clamped_e = [min(max(0, e), last) for e in ends]
    for s, e in zip(clamped_s, clamped_e):
        if s > e:
            raise ValueError(
                f"Subtítulo fuera del timeline: inicio {s} > fin {e} "
                f"(duración {duration} frames)"
            )
    return clamped_s, clamped_e, (clamped_s != starts or clamped_e != ends)


def _find_playlist(root: ET.Element, track_id: str) -> ET.Element | None:
    for pl in root.findall("playlist"):
        if pl.get("id") == track_id:
            return pl
    # track_id numérico: índice de <track> dentro del tractor principal
    if track_id.isdigit():
        tractor = root.find("tractor")
        if tractor is not None:
            tracks = tractor.findall("track")
            idx = int(track_id)
            if 0 <= idx < len(tracks):
                producer = tracks[idx].get("producer")
                for pl in root.findall("playlist"):
                    if pl.get("id") == producer:
                        return pl
    return None


def _next_filter_id(root: ET.Element, prefix: str = "filter_sub_") -> str:
    taken = {el.get("id") for el in root.iter() if el.get("id")}
    i = 0
    while f"{prefix}{i}" in taken:
        i += 1
    return f"{prefix}{i}"


def _register_subtitle(
    tree: ET.ElementTree,
    project: Path,
    track_id: str,
    srt_path: Path,
    in_frame: int,
    out_frame: int,
) -> str:
    """Aplica el SRT como filter subtitle a los clips del track; guarda XML."""
    root = tree.getroot()
    playlist = _find_playlist(root, track_id)
    if playlist is None:
        raise ValueError(f"Track no encontrado: {track_id!r}")
    first_id = ""
    for entry in playlist.findall("entry"):
        ein = entry.get("in", str(in_frame))
        eout = entry.get("out", str(out_frame))
        fid = _next_filter_id(root)
        filt = ET.SubElement(
            entry, "filter",
            {"id": fid, "mlt_service": "subtitle",
             "in": ein, "out": eout},
        )
        # Nota: family/size/colores explícitos son obligatorios; sin
        # ellos melt no resuelve la fuente y no dibuja texto.
        for name, val in (
            ("mlt_service", "subtitle"),
            ("resource", str(srt_path)),
            ("family", "DejaVu Sans"),
            ("size", "72"),
            ("fgcolour", "white"),
            ("olcolour", "black"),
            ("outline", "2"),
            ("geometry", "0%/80%:100%x20%:100%"),
            ("kdenlive_id", "subtitles"),
        ):
            p = ET.SubElement(filt, "property", {"name": name})
            p.text = val
        if not first_id:
            first_id = fid
    if not first_id:
        # Track sin clips: filtro a nivel tractor (quema subs en la mezcla).
        tractor = root.find("tractor")
        if tractor is None:
            raise ValueError(f"Track vacío y sin tractor: {track_id!r}")
        fid = _next_filter_id(root)
        filt = ET.SubElement(
            tractor, "filter",
            {"id": fid, "mlt_service": "subtitle",
             "in": str(in_frame), "out": str(out_frame)},
        )
        for name, val in (
            ("mlt_service", "subtitle"),
            ("resource", str(srt_path)),
            ("family", "DejaVu Sans"),
            ("size", "72"),
            ("fgcolour", "white"),
            ("olcolour", "black"),
            ("outline", "2"),
            ("geometry", "0%/80%:100%x20%:100%"),
            ("kdenlive_id", "subtitles"),
        ):
            p = ET.SubElement(filt, "property", {"name": name})
            p.text = val
        first_id = fid
    tree.write(project, encoding="utf-8", xml_declaration=True)
    try:
        ensure_composite_transitions(str(project))
    except Exception as exc:
        log.warning("composites no garantizados en %s: %s", project, exc)
    return first_id


def create_subtitles(
    project_path: str,
    track_id: str,
    texts: list[str],
    start_frames: list[int],
    end_frames: list[int],
    output_srt: str = "",
) -> dict:
    """Genera un SRT válido y lo aplica como filtro subtitle a los clips."""
    if not (len(texts) == len(start_frames) == len(end_frames)):
        raise ValueError("texts, start_frames y end_frames deben tener igual longitud")
    if not texts:
        raise ValueError("Listas vacías: nada que subtitular")
    from kdenlive_mcp.security import MAX_SRT_ENTRIES as _MAXE
    if len(texts) > _MAXE:
        raise ValueError(f"Demasiados subtítulos (>{_MAXE})")
    for s, e in zip(start_frames, end_frames):
        if s < 0 or e < 0:
            raise ValueError("Los frames deben ser >= 0")
        if s > e:
            raise ValueError(f"start_frame {s} > end_frame {e}")
    tree, project = _load_tree(project_path)
    start_frames, end_frames, clamped = _clamp_to_timeline(
        tree.getroot(), list(start_frames), list(end_frames)
    )
    content = _format_srt(texts, start_frames, end_frames)
    srt_path = (
        validate_output_path(output_srt)
        if output_srt
        else project.parent / f"{project.stem}.srt"
    )
    srt_path.write_text(content, encoding="utf-8")
    producer_id = _register_subtitle(
        tree, project, track_id, srt_path.resolve(),
        min(start_frames), max(end_frames),
    )
    warnings: list[str] = []
    if clamped:
        warnings.append("subtítulos recortados al timeline")
    return {
        "srt_path": str(srt_path.resolve()),
        "count": len(texts),
        "producer_id": producer_id,
        "clamped": clamped,
        "warnings": warnings,
    }


def import_srt(project_path: str, track_id: str, srt_path: str) -> dict:
    """Valida un SRT existente y lo aplica como filtro subtitle."""
    src = validate_path(srt_path)
    entries = parse_srt(src.read_text(encoding="utf-8"))
    tree, project = _load_tree(project_path)
    starts = [timestamp_to_frames(e["start"]) for e in entries]
    ends = [timestamp_to_frames(e["end"]) for e in entries]
    starts, ends, clamped = _clamp_to_timeline(tree.getroot(), starts, ends)
    start, end = starts[0], ends[-1]
    producer_id = _register_subtitle(
        tree, project, track_id, src.resolve(), start, end
    )
    warnings = ["subtítulos recortados al timeline"] if clamped else []
    return {
        "srt_path": str(src.resolve()),
        "count": len(entries),
        "producer_id": producer_id,
        "clamped": clamped,
        "warnings": warnings,
    }


def export_srt(project_path: str, output_path: str) -> dict:
    """Concatena los SRT de los filtros subtitle al output (reindexado)."""
    tree, _ = _load_tree(project_path)
    resources: list[Path] = []
    seen: set[str] = set()
    for filt in tree.getroot().iter("filter"):
        service = filt.findtext("property[@name='mlt_service']")
        resource = filt.findtext("property[@name='resource']")
        if service == "subtitle" and resource and resource not in seen:
            seen.add(resource)
            resources.append(Path(resource))
    if not resources:
        raise ValueError("El proyecto no tiene subtítulos") 
    merged: list[dict] = []
    for resource in resources:
        res = validate_path(str(resource), must_exist=True)
        merged.extend(parse_srt(res.read_text(encoding="utf-8")))
    lines: list[str] = []
    for i, entry in enumerate(merged, start=1):
        lines += [str(i), f"{entry['start']} --> {entry['end']}",
                  entry["text"], ""]
    out = validate_output_path(output_path)
    out.write_text("\n".join(lines), encoding="utf-8")
    return {"srt_path": str(out.resolve()), "count": len(merged)}


YOUTUBE_SUB_STYLE = (
    "FontName=DejaVu Sans,FontSize=22,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H80000000,BorderStyle=1,Outline=2,Shadow=0,"
    "MarginV=40,Alignment=2"
)


def burn_subtitles(
    video_path: str, srt_path: str, output_path: str = "",
    style: str = YOUTUBE_SUB_STYLE,
) -> dict:
    """Quema SRT en el vídeo con libass (estilo YouTube configurable).

    Para proyectos del MCP el filtro MLT subtitle (con family/size
    explícitos) ya dibuja texto en melt; esta función sirve para
    vídeos externos o estilos ASS avanzados. Re-encode H.264 CRF 20 + AAC.
    """
    import shutil
    import subprocess

    src = validate_path(video_path, must_exist=True)
    srt = validate_path(srt_path, must_exist=True)
    if srt.suffix.lower() != ".srt":
        raise ValueError("se esperaba un .srt")
    out = (validate_output_path(output_path) if output_path
           else src.parent / f"{src.stem}_subbed{src.suffix}")
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    esc = str(srt).replace("\\", "/").replace(":", "\\:")
    vf = f"subtitles='{esc}':force_style='{style}'"
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(src), "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "aac", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=900)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"burn_subtitles falló: {proc.stderr[-500:]}")
    try:
        from kdenlive_mcp.media_tools import analyze_media
        dur = analyze_media(str(out)).get("duration")
    except Exception:
        dur = None
    return {"output": str(out), "duration": dur}


_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)")


def _speech_boundaries(audio: str) -> tuple[list[float], list[float]]:
    """Fronteras voz/silencio con ffmpeg silencedetect (vacío si falla).

    Retorna (inicios_de_voz, fines_de_voz) en segundos. Sin ffmpeg,
    sin audio o sin silencios detectados retorna ([], []).
    """
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return [], []
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-i", audio,
           "-af", "silencedetect=noise=-30dB:d=0.3",
           "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return [], []
    text = proc.stderr or ""
    silences: list[tuple[float, float | None]] = []
    for line in text.splitlines():
        m = _SILENCE_START_RE.search(line)
        if m:
            silences.append((float(m.group(1)), None))
            continue
        m = _SILENCE_END_RE.search(line)
        if m and silences and silences[-1][1] is None:
            silences[-1] = (silences[-1][0], float(m.group(1)))
    if not silences:
        return [], []
    starts = [0.0] + [e for _, e in silences if e is not None]
    ends = [s for s, _ in silences]
    return starts, ends


def _snap(value: float, anchors: list[float], max_shift: float) -> tuple[float, bool]:
    """Acerca value al ancla más cercana si está dentro de max_shift."""
    best: float | None = None
    for anchor in anchors:
        if abs(anchor - value) <= max_shift and (
            best is None or abs(anchor - value) < abs(best - value)
        ):
            best = anchor
    if best is None:
        return value, False
    return best, best != value


def align_srt_to_audio(
    audio_path: str,
    srt_path: str,
    output_path: str = "",
    max_shift_sec: float = 0.5,
    fps: int = FPS,
) -> dict:
    """Recoloca inicios/fines del SRT a fronteras de voz por energía.

    Detecta silencios con ffmpeg (silencedetect -30dB) y mueve cada
    inicio al comienzo de voz más cercano y cada fin al final de voz
    más cercano, solo si el desplazamiento es <= max_shift_sec.
    Sin ffmpeg o sin silencios, copia el SRT sin cambios.
    """
    if max_shift_sec < 0:
        raise ValueError("max_shift_sec debe ser >= 0")
    if fps <= 0:
        raise ValueError("fps debe ser > 0")
    src = validate_path(audio_path, must_exist=True)
    srt = validate_path(srt_path, must_exist=True)
    if srt.suffix.lower() != ".srt":
        raise ValueError("se esperaba un .srt")
    entries = parse_srt(srt.read_text(encoding="utf-8"))
    starts, ends = _speech_boundaries(str(src))
    adjusted = 0
    out_entries: list[dict] = []
    for entry in entries:
        start_f = timestamp_to_frames(entry["start"], fps)
        end_f = timestamp_to_frames(entry["end"], fps)
        if starts or ends:
            start_s, end_s = start_f / fps, end_f / fps
            new_start_s, moved_s = _snap(start_s, starts, max_shift_sec)
            new_end_s, moved_e = _snap(end_s, ends, max_shift_sec)
            start_f = round(new_start_s * fps)
            end_f = round(new_end_s * fps)
            if start_f > end_f:
                start_f, end_f = timestamp_to_frames(entry["start"], fps), \
                    timestamp_to_frames(entry["end"], fps)
            else:
                adjusted += int(moved_s) + int(moved_e)
        out_entries.append({
            "start": frames_to_timestamp(start_f, fps),
            "end": frames_to_timestamp(end_f, fps),
            "text": entry["text"],
        })
    lines: list[str] = []
    for i, entry in enumerate(out_entries, start=1):
        lines += [str(i), f"{entry['start']} --> {entry['end']}",
                  entry["text"], ""]
    out = (validate_output_path(output_path) if output_path
           else srt.parent / f"{srt.stem}_aligned.srt")
    out.write_text("\n".join(lines), encoding="utf-8")
    return {"srt_path": str(out.resolve()), "count": len(out_entries),
            "adjusted": adjusted}
