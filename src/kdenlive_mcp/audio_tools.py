"""Herramientas de audio: filtros MLT en proyecto y procesado con ffmpeg.

Convencion de proyecto: igual que effects_tools, cada filtro de audio es
un <filter> hijo directo del <entry> del clip, con in/out copiados del entry
e IDs unicos ``filter_N``.

Volumen: el filtro ``mlt_service="volume"`` usa la propiedad ``gain`` en dB.
Los fades se expresan como keyframes de ``gain`` relativos al inicio del
clip (frame 0 = primer frame del clip): fade_in ``"0=-60;D=0"`` y fade_out
``"S=0;E=-60"`` donde D es la duracion y [S,E] el tramo final del clip.

duck_music: aplica atenuacion base (gain = duck_db) a todos los clips del
track de musica y marca cada filtro con propiedades ``ducked_by`` (track de
voz) y ``duck_db``. Es sidechain manual: no genera keyframes automaticos
siguiendo la voz; el usuario ajusta o anima ``gain`` despues si lo necesita.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

try:  # security.py lo crea otro modulo; fallback si aun no existe
    from kdenlive_mcp.security import validate_output_path, validate_path
except ImportError:  # pragma: no cover
    try:
        from .security import validate_output_path, validate_path  # type: ignore[no-redef]
    except ImportError:

        def validate_path(p: str | Path) -> Path:  # type: ignore[no-redef]
            return Path(p)

        def validate_output_path(p: str | Path) -> Path:  # type: ignore[no-redef]
            out = Path(p)
            out.parent.mkdir(parents=True, exist_ok=True)
            return out


VOLUME_SERVICE = "volume"
SILENCE_DB_RANGE = (-90.0, 0.0)
VOLUME_DB_RANGE = (-60.0, 12.0)
DUCK_DB_RANGE = (-60.0, 0.0)
TARGET_I_RANGE = (-70.0, 0.0)
FFMPEG_TIMEOUT = 300

_FILTER_ID_RE = re.compile(r"^filter_(\d+)$")


# ---------------------------------------------------------------------------
# Helpers XML privados (autocontenidos; no dependen de effects_tools)
# ---------------------------------------------------------------------------

def _load_root(project_path: str) -> tuple[ET.ElementTree, ET.Element, Path]:
    resolved = validate_path(project_path)
    if not resolved.is_file():
        raise FileNotFoundError(f"Proyecto no encontrado: {project_path}")
    try:
        tree = ET.parse(resolved)
    except ET.ParseError as exc:
        raise ValueError(f"XML invalido en proyecto: {project_path}: {exc}") from exc
    return tree, tree.getroot(), resolved


def _save(tree: ET.ElementTree, path: Path) -> None:
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _find_playlist(root: ET.Element, track_id: str) -> ET.Element:
    for playlist in root.iter("playlist"):
        if playlist.get("id") == track_id:
            return playlist
    raise ValueError(f"Track no encontrado: {track_id!r}")


def _entries(playlist: ET.Element) -> list[ET.Element]:
    return [child for child in playlist if child.tag == "entry"]


def _get_entry(playlist: ET.Element, clip_index: int) -> ET.Element:
    if clip_index < 0:
        raise IndexError(f"clip_index fuera de rango: {clip_index}")
    entries = _entries(playlist)
    if clip_index >= len(entries):
        raise IndexError(
            f"clip_index {clip_index} fuera de rango "
            f"(track tiene {len(entries)} clips)"
        )
    return entries[clip_index]


def _next_filter_id(root: ET.Element) -> str:
    max_n = -1
    for filt in root.iter("filter"):
        match = _FILTER_ID_RE.match(filt.get("id", ""))
        if match:
            max_n = max(max_n, int(match.group(1)))
    return f"filter_{max_n + 1}"


def _set_property(filt: ET.Element, name: str, value: str) -> None:
    for prop in filt.findall("property"):
        if prop.get("name") == name:
            prop.text = value
            return
    prop = ET.SubElement(filt, "property", {"name": name})
    prop.text = value


def _find_volume_filter(entry: ET.Element) -> ET.Element | None:
    for filt in entry.findall("filter"):
        if filt.get("mlt_service") == VOLUME_SERVICE:
            return filt
    return None


def _ensure_volume_filter(root: ET.Element, entry: ET.Element) -> ET.Element:
    """Devuelve el filtro volume del clip, creandolo si no existe."""
    existing = _find_volume_filter(entry)
    if existing is not None:
        return existing
    attrs: dict[str, str] = {"id": _next_filter_id(root), "mlt_service": VOLUME_SERVICE}
    if entry.get("in") is not None:
        attrs["in"] = entry.get("in", "")
    if entry.get("out") is not None:
        attrs["out"] = entry.get("out", "")
    filt = ET.SubElement(entry, "filter", attrs)
    _set_property(filt, "mlt_service", VOLUME_SERVICE)
    return filt


def _clip_length(entry: ET.Element) -> int | None:
    """Longitud del clip en frames segun in/out, o None si no se sabe."""
    try:
        begin = int(entry.get("in", ""))
        end = int(entry.get("out", ""))
    except (TypeError, ValueError):
        return None
    if end < begin:
        return None
    return end - begin + 1


# ---------------------------------------------------------------------------
# Helpers ffmpeg privados
# ---------------------------------------------------------------------------

def _require_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg no esta disponible en el PATH")
    return exe


def _default_output(media: Path, suffix: str) -> Path:
    return media.with_name(f"{media.stem}{suffix}{media.suffix}")


def _audio_codec_for(suffix: str) -> list[str]:
    if suffix.lower() == ".mp3":
        return ["-c:a", "libmp3lame"]
    if suffix.lower() == ".wav":
        return ["-c:a", "pcm_s16le"]
    return ["-c:a", "aac"]


def _run_ffmpeg(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Ejecuta ffmpeg con lista de args (sin shell)."""
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout)


def _check_range(name: str, value: float, low: float, high: float) -> float:
    value = float(value)
    if not low <= value <= high:
        raise ValueError(f"{name} fuera de rango [{low},{high}]: {value}")
    return value


# ---------------------------------------------------------------------------
# API publica: proyecto
# ---------------------------------------------------------------------------

def set_volume(
    project_path: str, track_id: str, clip_index: int, volume_db: float = 0.0
) -> dict:
    """Fija la ganancia (dB) del clip, creando el filtro volume si falta."""
    volume_db = _check_range("volume_db", volume_db, *VOLUME_DB_RANGE)
    tree, root, resolved = _load_root(project_path)
    entry = _get_entry(_find_playlist(root, track_id), clip_index)
    filt = _ensure_volume_filter(root, entry)
    _set_property(filt, "gain", str(volume_db))
    _save(tree, resolved)
    return {
        "effect_id": filt.get("id"),
        "mlt_service": VOLUME_SERVICE,
        "track_id": track_id,
        "clip_index": clip_index,
        "volume_db": volume_db,
    }


def fade_in(
    project_path: str, track_id: str, clip_index: int, duration_frames: int = 30
) -> dict:
    """Fundido de entrada de audio: gain -60dB -> 0dB en duration_frames."""
    duration_frames = int(duration_frames)
    if duration_frames < 1:
        raise ValueError(f"duration_frames debe ser >= 1: {duration_frames}")
    tree, root, resolved = _load_root(project_path)
    entry = _get_entry(_find_playlist(root, track_id), clip_index)
    filt = _ensure_volume_filter(root, entry)
    keyframes = f"0=-60;{duration_frames}=0"
    _set_property(filt, "gain", keyframes)
    _save(tree, resolved)
    return {
        "effect_id": filt.get("id"),
        "mlt_service": VOLUME_SERVICE,
        "track_id": track_id,
        "clip_index": clip_index,
        "duration_frames": duration_frames,
        "keyframes": keyframes,
    }


def fade_out(
    project_path: str, track_id: str, clip_index: int, duration_frames: int = 30
) -> dict:
    """Fundido de salida: gain 0dB -> -60dB en el tramo final del clip."""
    duration_frames = int(duration_frames)
    if duration_frames < 1:
        raise ValueError(f"duration_frames debe ser >= 1: {duration_frames}")
    tree, root, resolved = _load_root(project_path)
    entry = _get_entry(_find_playlist(root, track_id), clip_index)
    length = _clip_length(entry)
    if length is None:
        start, end = 0, duration_frames
    else:
        duration_frames = min(duration_frames, length)
        start = length - duration_frames
        end = length - 1
    filt = _ensure_volume_filter(root, entry)
    keyframes = f"{start}=0;{end}=-60"
    _set_property(filt, "gain", keyframes)
    _save(tree, resolved)
    return {
        "effect_id": filt.get("id"),
        "mlt_service": VOLUME_SERVICE,
        "track_id": track_id,
        "clip_index": clip_index,
        "duration_frames": duration_frames,
        "keyframes": keyframes,
    }


def duck_music(
    project_path: str,
    voice_track_id: str,
    music_track_id: str,
    duck_db: float = -10.0,
) -> dict:
    """Atenua todos los clips del track de musica para dejar paso a la voz."""
    duck_db = _check_range("duck_db", duck_db, *DUCK_DB_RANGE)
    tree, root, resolved = _load_root(project_path)
    _find_playlist(root, voice_track_id)  # valida que la voz existe
    music = _find_playlist(root, music_track_id)
    clips = _entries(music)
    if not clips:
        raise ValueError(f"Track de musica sin clips: {music_track_id!r}")
    effect_ids: list[str] = []
    for entry in clips:
        filt = _ensure_volume_filter(root, entry)
        _set_property(filt, "gain", str(duck_db))
        _set_property(filt, "ducked_by", voice_track_id)
        _set_property(filt, "duck_db", str(duck_db))
        effect_ids.append(filt.get("id", ""))
    _save(tree, resolved)
    return {
        "voice_track_id": voice_track_id,
        "music_track_id": music_track_id,
        "duck_db": duck_db,
        "clips_ducked": len(clips),
        "effect_ids": effect_ids,
    }


# ---------------------------------------------------------------------------
# API publica: ffmpeg
# ---------------------------------------------------------------------------

def normalize_audio(
    media_path: str, output_path: str = "", target_i: float = -16.0
) -> dict:
    """Normaliza con loudnorm en 1 pasada (EBU R128, estereo YouTube)."""
    target_i = _check_range("target_i", target_i, *TARGET_I_RANGE)
    media = validate_path(media_path)
    if not media.is_file():
        raise FileNotFoundError(f"Medio no encontrado: {media_path}")
    output = Path(output_path) if output_path else _default_output(media, "_loudnorm")
    output = validate_output_path(str(output))
    ffmpeg = _require_ffmpeg()
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-i", str(media),
        "-af", f"loudnorm=I={target_i}:TP=-1.5:LRA=11",
        "-ac", "2", *_audio_codec_for(output.suffix), str(output),
    ]
    proc = _run_ffmpeg(cmd, FFMPEG_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg loudnorm fallo: {proc.stderr[-2000:]}")
    return {"output": str(output), "target_i": target_i}


def remove_silence(
    media_path: str,
    output_path: str = "",
    threshold_db: float = -35.0,
    min_duration: float = 0.5,
) -> dict:
    """Quita silencios con corte real (silenceremove, fallback aselect)."""
    threshold_db = _check_range("threshold_db", threshold_db, *SILENCE_DB_RANGE)
    min_duration = float(min_duration)
    if min_duration <= 0:
        raise ValueError(f"min_duration debe ser > 0: {min_duration}")
    media = validate_path(media_path)
    if not media.is_file():
        raise FileNotFoundError(f"Medio no encontrado: {media_path}")
    output = Path(output_path) if output_path else _default_output(media, "_nosilence")
    output = validate_output_path(str(output))
    ffmpeg = _require_ffmpeg()

    detect = _run_ffmpeg(
        [ffmpeg, "-hide_banner", "-i", str(media),
         "-af", f"silencedetect=noise={threshold_db}dB:d={min_duration}",
         "-f", "null", "-"],
        FFMPEG_TIMEOUT,
    )
    segments: list[dict] = []
    pending_start: float | None = None
    for line in detect.stderr.splitlines():
        if "silence_start:" in line:
            try:
                pending_start = float(line.split("silence_start:")[1].split()[0])
            except (ValueError, IndexError):
                pending_start = None
        elif "silence_end:" in line and pending_start is not None:
            try:
                end = float(line.split("silence_end:")[1].split()[0])
            except (ValueError, IndexError):
                pending_start = None
                continue
            segments.append({"start": pending_start, "end": end})
            pending_start = None

    filt = (
        f"silenceremove=start_periods=1:start_duration={min_duration}"
        f":start_threshold={threshold_db}dB"
        f":stop_periods=-1:stop_duration={min_duration}"
        f":stop_threshold={threshold_db}dB"
    )
    proc = _run_ffmpeg(
        [ffmpeg, "-y", "-hide_banner", "-i", str(media),
         "-af", filt, "-ac", "2",
         *_audio_codec_for(output.suffix), str(output)],
        FFMPEG_TIMEOUT,
    )
    if proc.returncode != 0:
        # Fallback: recorta con aselect sobre los tramos no silenciosos.
        fallback = (
            f"aselect='not(silencedetect=n={threshold_db}dB:d={min_duration})'"
            ",asetpts=N/SR/TB"
        )
        proc = _run_ffmpeg(
            [ffmpeg, "-y", "-hide_banner", "-i", str(media),
             "-af", fallback, "-ac", "2",
             *_audio_codec_for(output.suffix), str(output)],
            FFMPEG_TIMEOUT,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg silenceremove fallo: {proc.stderr[-2000:]}")
    return {"output": str(output), "removed_segments": segments}


def sidechain_duck(
    voice_path: str,
    music_path: str,
    output_path: str = "",
    duck_db: float = -10.0,
    threshold: float = 0.02,
    ratio: float = 8.0,
) -> dict:
    """Duck real: sidechaincompressor (voz como sidechain de la musica).

    Si sidechaincompressor falla, fallback a atenuacion fija (volume).
    """
    duck_db = _check_range("duck_db", duck_db, *DUCK_DB_RANGE)
    threshold = float(threshold)
    if not 0.015 <= threshold <= 1.0:
        raise ValueError(f"threshold fuera de rango [0.015,1.0]: {threshold}")
    ratio = float(ratio)
    if not 1.0 <= ratio <= 20.0:
        raise ValueError(f"ratio fuera de rango [1.0,20.0]: {ratio}")
    voice = validate_path(voice_path)
    music = validate_path(music_path)
    if not voice.is_file():
        raise FileNotFoundError(f"Voz no encontrada: {voice_path}")
    if not music.is_file():
        raise FileNotFoundError(f"Musica no encontrada: {music_path}")
    output = Path(output_path) if output_path else _default_output(
        music, "_ducked")
    output = validate_output_path(str(output))
    ffmpeg = _require_ffmpeg()
    filt = (
        f"[1:a][0:a]sidechaincompressor=threshold={threshold}"
        f":ratio={ratio}:attack=20:release=400:makeup=1[ducked]"
    )
    proc = _run_ffmpeg(
        [ffmpeg, "-y", "-hide_banner", "-i", str(voice), "-i", str(music),
         "-filter_complex", filt, "-map", "[ducked]",
         "-ac", "2", *_audio_codec_for(output.suffix), str(output)],
        FFMPEG_TIMEOUT,
    )
    if proc.returncode == 0 and output.is_file() and output.stat().st_size > 0:
        return {"output": str(output), "duck_db": duck_db,
                "method": "sidechain", "fallback": False}
    # Fallback: atenuacion fija de la musica.
    proc = _run_ffmpeg(
        [ffmpeg, "-y", "-hide_banner", "-i", str(music),
         "-af", f"volume={duck_db}dB", "-ac", "2",
         *_audio_codec_for(output.suffix), str(output)],
        FFMPEG_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg sidechain+fallback fallo: {proc.stderr[-2000:]}")
    return {"output": str(output), "duck_db": duck_db,
            "method": "fixed", "fallback": True}
