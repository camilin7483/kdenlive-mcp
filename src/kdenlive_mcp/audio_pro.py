"""Audio profesional: loudness, compresor, stems, ducking auto y mezcla.

Convenciones del proyecto (.kdenlive = XML MLT, igual que audio_tools):
cada filtro de audio es un <filter mlt_service="volume" gain> hijo del
<entry> del clip, con in/out copiados del entry e IDs unicos filter_N.
Los tracks de audio son playlists con hide="video" en el multitrack.

Funciones puras que retornan dicts (sin decoradores MCP).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
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
TARGET_I_RANGE = (-70.0, 0.0)
DUCK_DB_RANGE = (-60.0, 0.0)
THRESHOLD_DB_RANGE = (-60.0, 0.0)
RATIO_RANGE = (1.0, 20.0)
SILENCE_DB_RANGE = (-90.0, 0.0)
MEASURE_TIMEOUT = 300
NORMALIZE_TIMEOUT = 600
COMPRESSOR_TIMEOUT = 300
MIX_TIMEOUT = 600
SILENCE_TIMEOUT = 300

_STEM_ROLES = ("voice", "music", "sfx")
_FILTER_ID_RE = re.compile(r"^filter_(\d+)$")
_JSON_BLOCK_RE = re.compile(r"\{.*?\}", re.DOTALL)


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

def _require_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg no esta disponible en el PATH")
    return exe


def _check_range(name: str, value: float, low: float, high: float) -> float:
    value = float(value)
    if not low <= value <= high:
        raise ValueError(f"{name} fuera de rango [{low},{high}]: {value}")
    return value


def _default_output(media: Path, suffix: str) -> Path:
    return media.with_name(f"{media.stem}{suffix}{media.suffix}")


def _audio_codec_for(suffix: str) -> list[str]:
    lowered = suffix.lower()
    if lowered == ".mp3":
        return ["-c:a", "libmp3lame"]
    if lowered == ".wav":
        return ["-c:a", "pcm_s16le"]
    return ["-c:a", "aac"]


def _measure_full(media: Path) -> dict:
    """Primera pasada loudnorm: retorna el JSON completo de la medida."""
    ffmpeg = _require_ffmpeg()
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(media),
         "-af", "loudnorm=print_format=json",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=MEASURE_TIMEOUT,
    )
    stderr = proc.stderr or ""
    for match in reversed(list(_JSON_BLOCK_RE.finditer(stderr))):
        try:
            data = json.loads(match.group(0))
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and (
            "input_i" in data or "measured_I" in data
        ):
            return data
    raise RuntimeError(
        f"loudnorm no devolvio medida JSON: {stderr[-1000:]}"
    )


def _num(data: dict, *keys: str) -> float:
    for key in keys:
        if key in data:
            try:
                return float(data[key])
            except (TypeError, ValueError):
                continue
    raise ValueError(f"medida sin claves {keys}: {sorted(data)}")


# --- XML ---

def _load_root(project_path: str) -> tuple[ET.ElementTree, ET.Element, Path]:
    resolved = validate_path(project_path)
    if not resolved.is_file():
        raise FileNotFoundError(f"Proyecto no encontrado: {project_path}")
    try:
        tree = ET.parse(resolved)
    except ET.ParseError as exc:
        raise ValueError(
            f"XML invalido en proyecto: {project_path}: {exc}") from exc
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


def _next_filter_id(root: ET.Element) -> str:
    max_n = -1
    for filt in root.iter("filter"):
        match = _FILTER_ID_RE.match(filt.get("id", ""))
        if match:
            max_n = max(max_n, int(match.group(1)))
    return f"filter_{max_n + 1}"


def _set_property(elem: ET.Element, name: str, value: str) -> None:
    for prop in elem.findall("property"):
        if prop.get("name") == name:
            prop.text = value
            return
    prop = ET.SubElement(elem, "property", {"name": name})
    prop.text = value


def _find_volume_filter(entry: ET.Element) -> ET.Element | None:
    for filt in entry.findall("filter"):
        if filt.get("mlt_service") == VOLUME_SERVICE:
            return filt
    return None


def _ensure_volume_filter(root: ET.Element, entry: ET.Element) -> ET.Element:
    existing = _find_volume_filter(entry)
    if existing is not None:
        return existing
    attrs: dict[str, str] = {
        "id": _next_filter_id(root), "mlt_service": VOLUME_SERVICE}
    if entry.get("in") is not None:
        attrs["in"] = entry.get("in", "")
    if entry.get("out") is not None:
        attrs["out"] = entry.get("out", "")
    filt = ET.SubElement(entry, "filter", attrs)
    _set_property(filt, "mlt_service", VOLUME_SERVICE)
    return filt


def _entry_length(entry: ET.Element) -> int:
    try:
        begin = int(entry.get("in", "0"))
        end = int(entry.get("out", "0"))
    except (TypeError, ValueError):
        return 0
    return max(0, end - begin + 1)


def _fmt_db(value: float) -> str:
    return str(float(value))


# ---------------------------------------------------------------------------
# API publica: ffmpeg (loudness / compresor)
# ---------------------------------------------------------------------------

def loudness_measure(media_path: str) -> dict:
    """Mide loudness EBU R128 con loudnorm (pasada de medida)."""
    media = validate_path(media_path)
    if not media.is_file():
        raise FileNotFoundError(f"Medio no encontrado: {media_path}")
    data = _measure_full(media)
    return {
        "integrated_lufs": _num(data, "measured_I", "input_i"),
        "true_peak": _num(data, "measured_TP", "input_tp"),
        "lra": _num(data, "measured_LRA", "input_lra"),
    }


def loudness_normalize(
    media_path: str, output_path: str = "", target_i: float = -16.0
) -> dict:
    """Normaliza a target_i LUFS estereo (YouTube: -16; shorts: -14).

    Doble pasada lineal con los valores medidos; si la medida falla,
    una sola pasada directa.
    """
    target_i = _check_range("target_i", target_i, *TARGET_I_RANGE)
    media = validate_path(media_path)
    if not media.is_file():
        raise FileNotFoundError(f"Medio no encontrado: {media_path}")
    output = Path(output_path) if output_path else _default_output(
        media, "_norm")
    output = validate_output_path(str(output))
    ffmpeg = _require_ffmpeg()
    try:
        measured = _measure_full(media)
        filt = (
            f"loudnorm=I={target_i}:TP=-1.5:LRA=11"
            f":measured_I={_num(measured, 'measured_I', 'input_i')}"
            f":measured_TP={_num(measured, 'measured_TP', 'input_tp')}"
            f":measured_LRA={_num(measured, 'measured_LRA', 'input_lra')}"
            f":measured_thresh={_num(measured, 'measured_thresh', 'input_thresh')}"
            f":offset={_num(measured, 'offset', 'target_offset')}"
            ":linear=true"
        )
        linear = True
    except (RuntimeError, ValueError):
        filt = f"loudnorm=I={target_i}:TP=-1.5:LRA=11"
        linear = False
    proc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-i", str(media),
         "-af", filt, "-ac", "2",
         *_audio_codec_for(output.suffix), str(output)],
        capture_output=True, text=True, timeout=NORMALIZE_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg loudnorm fallo: {(proc.stderr or '')[-2000:]}")
    return {"output": str(output), "target_i": target_i, "linear": linear}


def apply_compressor(
    media_path: str,
    output_path: str = "",
    threshold_db: float = -20.0,
    ratio: float = 3.0,
) -> dict:
    """Aplica compresion dinamica (acompressor) al audio."""
    threshold_db = _check_range(
        "threshold_db", threshold_db, *THRESHOLD_DB_RANGE)
    ratio = _check_range("ratio", ratio, *RATIO_RANGE)
    media = validate_path(media_path)
    if not media.is_file():
        raise FileNotFoundError(f"Medio no encontrado: {media_path}")
    output = Path(output_path) if output_path else _default_output(
        media, "_comp")
    output = validate_output_path(str(output))
    ffmpeg = _require_ffmpeg()
    filt = (f"acompressor=threshold={threshold_db}dB:ratio={ratio}"
            ":attack=20:release=200:makeup=2")
    proc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-i", str(media),
         "-af", filt, *_audio_codec_for(output.suffix), str(output)],
        capture_output=True, text=True, timeout=COMPRESSOR_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg acompressor fallo: {(proc.stderr or '')[-2000:]}")
    return {
        "output": str(output),
        "threshold_db": threshold_db,
        "ratio": ratio,
    }


def duck_sidechain(
    voice_path: str,
    music_path: str,
    output_path: str = "",
    duck_db: float = -10.0,
    threshold: float = 0.02,
    ratio: float = 8.0,
) -> dict:
    """Duck real: sidechaincompressor con la voz como sidechain.

    Si sidechaincompressor falla, fallback a atenuacion fija (volume).
    """
    duck_db = _check_range("duck_db", duck_db, *DUCK_DB_RANGE)
    threshold = float(threshold)
    if not 0.015 <= threshold <= 1.0:
        raise ValueError(f"threshold fuera de rango [0.015,1.0]: {threshold}")
    ratio = _check_range("ratio", ratio, *RATIO_RANGE)
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
    proc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-i", str(voice), "-i", str(music),
         "-filter_complex", filt, "-map", "[ducked]",
         "-ac", "2", *_audio_codec_for(output.suffix), str(output)],
        capture_output=True, text=True, timeout=COMPRESSOR_TIMEOUT,
    )
    if proc.returncode == 0 and output.is_file() and output.stat().st_size > 0:
        return {"output": str(output), "duck_db": duck_db,
                "method": "sidechain", "fallback": False}
    proc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-i", str(music),
         "-af", f"volume={duck_db}dB", "-ac", "2",
         *_audio_codec_for(output.suffix), str(output)],
        capture_output=True, text=True, timeout=COMPRESSOR_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg sidechain+fallback fallo: {(proc.stderr or '')[-2000:]}")
    return {"output": str(output), "duck_db": duck_db,
            "method": "fixed", "fallback": True}


def cut_silence(
    media_path: str,
    output_path: str = "",
    threshold_db: float = -35.0,
    min_duration: float = 0.5,
) -> dict:
    """Corte real de silencios (silenceremove, fallback aselect)."""
    threshold_db = _check_range(
        "threshold_db", threshold_db, *SILENCE_DB_RANGE)
    min_duration = float(min_duration)
    if min_duration <= 0:
        raise ValueError(f"min_duration debe ser > 0: {min_duration}")
    media = validate_path(media_path)
    if not media.is_file():
        raise FileNotFoundError(f"Medio no encontrado: {media_path}")
    output = Path(output_path) if output_path else _default_output(
        media, "_cut")
    output = validate_output_path(str(output))
    ffmpeg = _require_ffmpeg()
    detect = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(media),
         "-af", f"silencedetect=noise={threshold_db}dB:d={min_duration}",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=SILENCE_TIMEOUT,
    )
    segments: list[dict] = []
    pending: float | None = None
    for line in (detect.stderr or "").splitlines():
        if "silence_start:" in line:
            try:
                pending = float(line.split("silence_start:")[1].split()[0])
            except (ValueError, IndexError):
                pending = None
        elif "silence_end:" in line and pending is not None:
            try:
                end = float(line.split("silence_end:")[1].split()[0])
            except (ValueError, IndexError):
                pending = None
                continue
            segments.append({"start": pending, "end": end})
            pending = None
    filt = (
        f"silenceremove=start_periods=1:start_duration={min_duration}"
        f":start_threshold={threshold_db}dB"
        f":stop_periods=-1:stop_duration={min_duration}"
        f":stop_threshold={threshold_db}dB"
    )
    proc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-i", str(media),
         "-af", filt, "-ac", "2",
         *_audio_codec_for(output.suffix), str(output)],
        capture_output=True, text=True, timeout=SILENCE_TIMEOUT,
    )
    if proc.returncode != 0:
        fallback = (
            f"aselect='not(silencedetect=n={threshold_db}dB:d={min_duration})'"
            ",asetpts=N/SR/TB"
        )
        proc = subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-i", str(media),
             "-af", fallback, "-ac", "2",
             *_audio_codec_for(output.suffix), str(output)],
            capture_output=True, text=True, timeout=SILENCE_TIMEOUT,
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg silenceremove fallo: {(proc.stderr or '')[-2000:]}")
    return {"output": str(output), "removed_segments": segments}


# ---------------------------------------------------------------------------
# API publica: proyecto (stems / ducking / mezcla)
# ---------------------------------------------------------------------------

def split_stems(
    project_path: str,
    voice_track_id: str,
    music_track_id: str,
    sfx_track_id: str = "",
) -> dict:
    """Etiqueta cada track con su rol (voice/music/sfx). Determinista."""
    tree, root, resolved = _load_root(project_path)
    pairs = [(voice_track_id, "voice"), (music_track_id, "music")]
    if sfx_track_id:
        pairs.append((sfx_track_id, "sfx"))
    roles: dict[str, str] = {}
    for track_id, role in pairs:
        if role not in _STEM_ROLES:  # pragma: no cover - defensa
            raise ValueError(f"Rol desconocido: {role!r}")
        playlist = _find_playlist(root, track_id)  # valida existencia
        _set_property(playlist, "role", role)
        roles[track_id] = role
    _save(tree, resolved)
    return {"roles": roles}


def tts_duck_auto(
    project_path: str,
    narration_track_id: str,
    music_track_id: str,
    duck_db: float = -10.0,
    fade_frames: int = 15,
    voice_media: str = "",
    music_media: str = "",
    output_path: str = "",
    use_sidechain: bool = False,
) -> dict:
    """Ducking automatico: atenua la musica bajo cada clip de narracion.

    Cada clip de narracion ocupa [start, end] donde start es la longitud
    acumulada de los clips previos. En cada entry de musica se escribe un
    filtro volume con keyframes de gain: 0dB fuera y duck_db durante
    [start-fade, end+fade] (rampa de fade_frames en cada borde).

    Si use_sidechain es True y se dan voice_media+music_media, renderiza
    ademas un duck real con sidechaincompressor (fallback a atenuacion
    fija si falla) sin romper el duck por keyframes.
    """
    duck_db = _check_range("duck_db", duck_db, *DUCK_DB_RANGE)
    fade_frames = int(fade_frames)
    if fade_frames < 1:
        raise ValueError(f"fade_frames debe ser >= 1: {fade_frames}")
    tree, root, resolved = _load_root(project_path)
    narration = _find_playlist(root, narration_track_id)
    music = _find_playlist(root, music_track_id)

    narr_entries = _entries(narration)
    if not narr_entries:
        raise ValueError(
            f"Track de narracion sin clips: {narration_track_id!r}")
    music_entries = _entries(music)
    if not music_entries:
        raise ValueError(
            f"Track de musica sin clips: {music_track_id!r}")

    # Segmentos de narracion en coordenadas de timeline.
    segments: list[tuple[int, int]] = []
    cursor = 0
    for entry in narr_entries:
        length = _entry_length(entry)
        if length > 0:
            segments.append((cursor, cursor + length - 1))
            cursor += length

    ducked = 0
    effect_ids: list[str] = []
    cursor = 0
    for entry in music_entries:
        length = _entry_length(entry)
        if length <= 0:
            continue
        span_start, span_end = cursor, cursor + length - 1
        cursor += length
        points: dict[int, float] = {0: 0.0}
        for start, end in segments:
            pre = max(0, start - fade_frames)
            post = end + fade_frames
            if post < span_start or pre > span_end:
                continue
            for frame, value in (
                (pre, 0.0), (start, duck_db),
                (end, duck_db), (post, 0.0),
            ):
                if span_start <= frame <= span_end:
                    rel = frame - span_start
                    if rel in points and points[rel] == 0.0 and value != 0.0:
                        points[rel] = value  # el duck manda en colisiones
                    elif rel not in points:
                        points[rel] = value
        keyframes = ";".join(
            f"{frame}={_fmt_db(points[frame])}" for frame in sorted(points)
        )
        filt = _ensure_volume_filter(root, entry)
        _set_property(filt, "gain", keyframes)
        _set_property(filt, "ducked_by", narration_track_id)
        _set_property(filt, "duck_db", _fmt_db(duck_db))
        effect_ids.append(filt.get("id", ""))
        ducked += 1

    _save(tree, resolved)
    result: dict = {
        "narration_track_id": narration_track_id,
        "music_track_id": music_track_id,
        "ducked_clips": ducked,
        "narration_clips": len(segments),
        "duck_db": duck_db,
        "fade_frames": fade_frames,
        "effect_ids": effect_ids,
    }
    if use_sidechain and voice_media and music_media:
        try:
            rendered = duck_sidechain(
                voice_media, music_media,
                output_path=output_path, duck_db=duck_db)
            result["sidechain"] = {
                "attempted": True, **rendered}
        except (OSError, RuntimeError, ValueError) as exc:
            result["sidechain"] = {"attempted": True, "fallback": True,
                                   "error": str(exc)}
    else:
        result["sidechain"] = {"attempted": bool(use_sidechain)}
    return result


def _probe_duration(path: Path) -> float | None:
    """Duracion en segundos via ffprobe (None si no se puede)."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return float((proc.stdout or "").strip())
    except (TypeError, ValueError):
        return None


def mix_stems(project_path: str, output_stem_mix: str = "") -> dict:
    """Mezcla solo el audio del proyecto a WAV 48kHz estereo (melt)."""
    project = validate_path(project_path)
    if not project.is_file():
        raise FileNotFoundError(f"Proyecto no encontrado: {project_path}")
    melt = shutil.which("melt")
    if not melt:
        raise RuntimeError("melt no esta disponible en el PATH")
    if output_stem_mix:
        out = validate_output_path(output_stem_mix)
    else:
        out = validate_output_path(
            str(project.parent / f"{project.stem}_mix.wav"))

    try:
        root = ET.parse(project).getroot()
        try:
            from kdenlive_mcp.kdenlive_project import timeline_length_frames
        except ImportError:
            from .kdenlive_project import timeline_length_frames  # type: ignore
        total = timeline_length_frames(root) or 0
    except ET.ParseError as exc:
        raise ValueError(
            f"XML invalido en proyecto: {project_path}: {exc}") from exc

    cmd = [melt, str(project)]
    if total > 0:
        cmd += ["in=0", f"out={total - 1}"]
    cmd += ["-consumer", f"avformat:{out}",
            "acodec=pcm_s16le", "ac=2", "ar=48000", "video_off=1"]

    state: dict = {}

    def _run() -> None:
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True,
            )
            state["proc"] = proc
            _, stderr = proc.communicate()
            state["returncode"] = proc.returncode
            state["stderr"] = stderr or ""
        except OSError as exc:
            state["error"] = str(exc)

    worker = threading.Thread(target=_run, name="mix-stems", daemon=True)
    worker.start()
    worker.join(MIX_TIMEOUT)
    proc = state.get("proc")
    if worker.is_alive():
        try:
            if proc is not None:
                proc.kill()
        finally:
            raise TimeoutError(
                f"melt no termino en {MIX_TIMEOUT}s: {project_path}")
    if "error" in state:
        raise RuntimeError(f"no se pudo lanzar melt: {state['error']}")
    if state.get("returncode") != 0:
        raise RuntimeError(
            f"melt fallo (rc={state.get('returncode')}): "
            f"{str(state.get('stderr', ''))[-2000:]}")
    if not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError(f"melt no genero mezcla: {out}")
    duration = _probe_duration(out)
    if duration is None and total > 0:
        duration = total / 30.0
    return {"output": str(out), "duration": duration}
