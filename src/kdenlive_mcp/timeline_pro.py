"""Funciones pro de timeline para proyectos .kdenlive (XML MLT).

Funciones puras que retornan dicts, siguiendo las convenciones de
``timeline_tools`` (``_load``/``_save``, ``_playlist``/``_clip``/
``_entry_bounds``, playlists ``playlistN``, ``tractor``/``multitrack``/
``track``). Sin dependencias nuevas: solo stdlib + ffmpeg (subproceso)
para el análisis de audio.

Convención de markers (documentada aquí porque MLT no define markers
para Kdenlive de forma estándar legible por ElementTree sin namespaces):
se guardan como lista serializada en una única propiedad del ``tractor``::

    <property name="kdenlive:markers">frame:texto:color;frame:texto:color;</property>

donde ``frame`` es entero >= 0 y ``texto``/``color`` escapan ``\\``, ``:``
y ``;`` con backslash (``\\:``, ``\\;``, ``\\\\``). El ``marker_id`` es el
índice en esa lista. Formato MLT-compatible (una property de texto más,
que Kdenlive/MLT ignoran si no la conocen) y reversible.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

try:
    from kdenlive_mcp.security import validate_output_path, validate_path
except ImportError:  # Implementación paralela: contrato mínimo local.
    def validate_path(p, must_exist=False, **kwargs):  # type: ignore[no-redef]
        path = Path(p).expanduser()
        if must_exist and not path.exists():
            raise FileNotFoundError(f"Ruta no encontrada: {p}")
        return path

    def validate_output_path(p):  # type: ignore[no-redef]
        path = Path(p).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

try:
    from kdenlive_mcp.kdenlive_project import ensure_composite_transitions
except ImportError:
    try:
        from .kdenlive_project import ensure_composite_transitions  # type: ignore
    except ImportError:
        def ensure_composite_transitions(project_path: str) -> dict:  # type: ignore
            return {"added": 0, "tracks": 0}

_MARKERS_PROP = "kdenlive:markers"
_FPS_DEFAULT = 30.0
_MULTICAM_FPS = 30.0
_SILENCE_NOISE = "-30dB"
_SILENCE_MIN_DUR = 0.2
_AUDIBLE_MIN_DB = -60.0  # Bajo esto se considera silencio (ceros dan ~-91dB/LSB).
_VALID_METHODS = ("peak", "xcorr")


# ---------------------------------------------------------------------------
# Internos (misma convención que timeline_tools)
# ---------------------------------------------------------------------------

def _load(project_path: str) -> tuple[ET.ElementTree, ET.Element, Path]:
    proj = validate_path(project_path, must_exist=True)
    try:
        tree = ET.parse(str(proj))
    except ET.ParseError as exc:
        raise ValueError(f"Proyecto XML inválido: {project_path}: {exc}") from exc
    root = tree.getroot()
    if root.tag != "mlt":
        raise ValueError(f"No es un proyecto MLT/Kdenlive válido: {project_path}")
    return tree, root, proj


def _save(tree: ET.ElementTree, proj: Path) -> None:
    ET.indent(tree, space="  ")
    tmp = proj.with_name(proj.name + ".tmp")
    tree.write(str(tmp), encoding="utf-8", xml_declaration=True)
    tmp.replace(proj)
    try:
        ensure_composite_transitions(str(proj))
    except Exception as exc:
        log.warning("composites no garantizados en %s: %s", proj, exc)


def _frame(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} debe ser entero >= 0, recibido: {value!r}")
    if value < 0:
        raise ValueError(f"{name} debe ser >= 0, recibido: {value}")
    return value


def _playlist(root: ET.Element, track_id: str) -> ET.Element:
    if not track_id:
        raise ValueError("track_id vacío")
    for pl in root.findall("playlist"):
        if pl.get("id") == track_id:
            return pl
    raise ValueError(f"Track desconocido: {track_id}")


def _entries(pl: ET.Element) -> list[ET.Element]:
    return [e for e in pl if e.tag == "entry"]


def _clip(pl: ET.Element, clip_index: int) -> ET.Element:
    entries = _entries(pl)
    if isinstance(clip_index, bool) or not isinstance(clip_index, int):
        raise ValueError(f"clip_index debe ser entero, recibido: {clip_index!r}")
    if not 0 <= clip_index < len(entries):
        raise ValueError(f"clip_index fuera de rango: {clip_index} (clips: {len(entries)})")
    return entries[clip_index]


def _entry_bounds(entry: ET.Element) -> tuple[int, int]:
    try:
        in_f = int(entry.get("in", "0"))
        out_f = int(entry.get("out", "0"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Clip con frames no numéricos: in={entry.get('in')} out={entry.get('out')}"
        ) from exc
    return in_f, out_f


def _next_id(root: ET.Element, tag: str, prefix: str) -> str:
    taken = set()
    for elem in root.iter(tag):
        eid = elem.get("id", "")
        if eid.startswith(prefix) and eid[len(prefix):].isdigit():
            taken.add(int(eid[len(prefix):]))
    n = 1
    while n in taken:
        n += 1
    return f"{prefix}{n}"


def _ensure_tractor(root: ET.Element) -> tuple[ET.Element, ET.Element]:
    for tractor in root.findall("tractor"):
        multitrack = tractor.find("multitrack")
        if multitrack is not None:
            return tractor, multitrack
    tractor = root.find("tractor")
    if tractor is None:
        tractor = ET.SubElement(root, "tractor", {"id": "tractor"})
    multitrack = tractor.find("multitrack")
    if multitrack is None:
        multitrack = ET.SubElement(tractor, "multitrack")
    return tractor, multitrack


def _insert_at_entry_index(pl: ET.Element, elem: ET.Element, position: int) -> None:
    children = list(pl)
    entry_pos = [i for i, e in enumerate(children) if e.tag == "entry"]
    if position >= len(entry_pos):
        pl.append(elem)
    else:
        pl.insert(entry_pos[position], elem)


def _fps(root: ET.Element) -> float:
    prof = root.find("profile")
    if prof is not None:
        try:
            num = float(prof.get("frame_rate_num", ""))
            den = float(prof.get("frame_rate_den", ""))
            if num > 0 and den > 0:
                return num / den
        except (TypeError, ValueError):
            pass
    return _FPS_DEFAULT


def _track_total(pl: ET.Element) -> int:
    total = 0
    for entry in pl.findall("entry"):
        try:
            ein = int(entry.get("in", "0"))
            eout = int(entry.get("out", "0"))
        except (TypeError, ValueError):
            continue
        if eout >= ein:
            total += eout - ein + 1
    return total


def _timeline_total(root: ET.Element) -> int:
    """Suma de clips del track más largo (ignora main_bin y el out previo)."""
    tids: set[str] = set()
    tractor = root.find("tractor")
    if tractor is not None:
        mt = tractor.find("multitrack")
        if mt is not None:
            tids = {t.get("producer", "") for t in mt.findall("track")}
    best = 0
    for pl in root.findall("playlist"):
        pid = pl.get("id", "")
        if pid == "main_bin" or (tids and pid not in tids):
            continue
        best = max(best, _track_total(pl))
    return best


# -- markers: escape / parse -------------------------------------------------

def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace(";", "\\;")


def _parse_markers(raw: str) -> list[tuple[int, str, str]]:
    """Parsea "frame:texto:color;" respetando escapes; salta entradas rotas."""
    items: list[tuple[int, str, str]] = []
    fields: list[str] = []
    buf: list[str] = []
    escaped = False
    for ch in raw or "":
        if escaped:
            buf.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":" and len(fields) < 2:
            fields.append("".join(buf))
            buf = []
        elif ch == ";":
            fields.append("".join(buf))
            buf = []
            if len(fields) == 3 and (fields[0] or "").lstrip("-").isdigit():
                try:
                    items.append((int(fields[0]), fields[1], fields[2]))
                except ValueError:
                    pass
            fields = []
        else:
            buf.append(ch)
    return items


def _format_markers(items: list[tuple[int, str, str]]) -> str:
    return "".join(f"{f}:{_escape(t)}:{_escape(c)};" for f, t, c in items)


def _markers_prop(tractor: ET.Element) -> ET.Element | None:
    for prop in tractor.findall("property"):
        if prop.get("name") == _MARKERS_PROP:
            return prop
    return None


# -- audio (ffmpeg best-effort) ----------------------------------------------

def _ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise RuntimeError("ffmpeg no disponible en el PATH")
    return exe


def _has_audio_stream(media: Path) -> bool:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is not None:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(media)],
            capture_output=True, text=True, timeout=60,
        )
        return bool(proc.stdout.strip())
    proc = subprocess.run(
        [_ffmpeg_bin(), "-hide_banner", "-i", str(media)],
        capture_output=True, text=True, timeout=60,
    )
    return "Audio:" in (proc.stderr or "")


def _probe_duration_sec(media: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(media)],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        dur = float((proc.stdout or "").strip())
    except ValueError:
        return None
    return dur if dur > 0 else None


def _max_volume_db(media: Path) -> float | None:
    """Volumen máximo en dB vía volumedetect; None si silencio digital."""
    proc = subprocess.run(
        [_ffmpeg_bin(), "-hide_banner", "-i", str(media),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )
    m = re.search(r"max_volume:\s*(.+?)\s*dB", proc.stderr or "")
    if m is None:
        return None
    val = m.group(1).strip()
    if "inf" in val.lower() or "n/a" in val.lower():
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _audio_onset_seconds(media: Path) -> float:
    """Primer fin-de-silencio (inicio de audio) en segundos vía silencedetect."""
    if not _has_audio_stream(media):
        raise ValueError(f"sin pista de audio: {media}")
    max_db = _max_volume_db(media)
    if max_db is None or max_db <= _AUDIBLE_MIN_DB:
        raise ValueError(f"no se detectó audio audible (silencio total): {media}")
    proc = subprocess.run(
        [_ffmpeg_bin(), "-hide_banner", "-i", str(media),
         "-af", f"silencedetect=noise={_SILENCE_NOISE}:d={_SILENCE_MIN_DUR}",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )
    events = re.findall(
        r"silence_(start|end):\s*([0-9]+(?:\.[0-9]+)?)", proc.stderr or "")
    if not events:
        return 0.0  # Nunca hubo silencio: suena desde el inicio.
    kind0, t0 = events[0][0], float(events[0][1])
    if kind0 == "start" and t0 <= 0.5:
        # Silencio inicial: el onset es su fin; sin fin, era silencio total
        # (el gate de volumedetect ya lo habría descartado, esto es red).
        for kind, t in events[1:]:
            if kind == "end":
                return max(0.0, float(t))
        raise ValueError(f"no se detectó audio audible (silencio total): {media}")
    return 0.0  # Audio desde el inicio (el silencio, si hay, es posterior).


def _extract_mono_8k(media: Path, out: Path) -> None:
    """Audio mono 8 kHz s16 para correlación (rápido y suficiente)."""
    proc = subprocess.run(
        [_ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(media), "-ar", "8000", "-ac", "1", "-sample_fmt", "s16",
         str(out)], capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 or not out.exists():
        raise ValueError(f"no se pudo extraer audio de {media}")


def _read_mono_s16(path: Path) -> "object":
    import wave
    with wave.open(str(path), "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
    import numpy as np
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64)


def _audio_xcorr_seconds(media: list[Path]) -> list[float]:
    """Onsets por correlación cruzada FFT contra el primer medio.

    Precisión de muestra (1/8000 s). Requiere numpy+scipy; sin ellos,
    ValueError (usar method="peak").
    """
    try:
        import numpy as np
        from scipy import signal
    except ImportError as exc:
        raise ValueError(
            "method='xcorr' requiere numpy+scipy instalados") from exc
    import tempfile
    for m in media:
        if not _has_audio_stream(m):
            raise ValueError(f"sin pista de audio: {m}")
        max_db = _max_volume_db(m)
        if max_db is None or max_db <= _AUDIBLE_MIN_DB:
            raise ValueError(
                f"no se detectó audio audible (silencio total): {m}")
    wavs: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="xcorr-") as tmp:
        for i, m in enumerate(media):
            w = Path(tmp) / f"ref{i}.wav"
            _extract_mono_8k(m, w)
            wavs.append(w)
        ref = _read_mono_s16(wavs[0])
        ref -= ref.mean()
        lags = [0]
        for w in wavs[1:]:
            cur = _read_mono_s16(w)
            cur -= cur.mean()
            n = min(len(ref), len(cur))
            if n < 800:
                raise ValueError(f"audio demasiado corto: {w}")
            corr = signal.correlate(cur[:n], ref[:n], mode="full",
                                    method="fft")
            peak = corr.max()
            if peak <= 0:
                raise ValueError(f"sin correlación de audio: {w}")
            # Pico más cercano a cero dentro del 99% del máximo: evita
            # picos espurios equidistantes con señales periódicas.
            cands = np.where(corr >= 0.99 * peak)[0] - (n - 1)
            lag = int(cands[np.argmin(np.abs(cands))])
            lags.append(lag)
        base = min([0] + lags)
        onsets = [max(0.0, (lag - base) / 8000.0) for lag in lags]
    return onsets


def _producer_resource(root: ET.Element, producer_id: str) -> str:
    for prod in root.findall("producer"):
        if prod.get("id") == producer_id:
            for prop in prod.findall("property"):
                if prop.get("name") == "resource":
                    return (prop.text or "").strip()
    return ""


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def add_marker(project_path: str, frame: int, text: str, color: str = "red") -> dict:
    """Añade un marker al tractor (lista en property ``kdenlive:markers``)."""
    frame = _frame(frame, "frame")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text debe ser str no vacío")
    if not isinstance(color, str) or not color.strip():
        raise ValueError("color debe ser str no vacío")
    tree, root, proj = _load(project_path)
    tractor, _ = _ensure_tractor(root)
    prop = _markers_prop(tractor)
    items = _parse_markers(prop.text or "" if prop is not None else "")
    marker_id = len(items)
    items.append((frame, text, color))
    if prop is None:
        prop = ET.SubElement(tractor, "property", {"name": _MARKERS_PROP})
    prop.text = _format_markers(items)
    _save(tree, proj)
    return {"marker_id": marker_id, "frame": frame, "text": text,
            "color": color, "project_path": str(proj)}


def list_markers(project_path: str) -> dict:
    """Lista los markers del tractor en orden de inserción."""
    _, root, proj = _load(project_path)
    tractor = root.find("tractor")
    raw = ""
    if tractor is not None:
        prop = _markers_prop(tractor)
        raw = (prop.text or "") if prop is not None else ""
    markers = [
        {"marker_id": i, "frame": f, "text": t, "color": c}
        for i, (f, t, c) in enumerate(_parse_markers(raw))
    ]
    return {"markers": markers, "count": len(markers), "project_path": str(proj)}


def remove_marker(project_path: str, marker_id: int) -> dict:
    """Elimina el marker en ``marker_id`` (índice) y reindexa la lista."""
    if isinstance(marker_id, bool) or not isinstance(marker_id, int):
        raise ValueError(f"marker_id debe ser entero, recibido: {marker_id!r}")
    tree, root, proj = _load(project_path)
    tractor = root.find("tractor")
    if tractor is None:
        raise ValueError("proyecto sin tractor: no hay markers")
    prop = _markers_prop(tractor)
    items = _parse_markers(prop.text or "" if prop is not None else "")
    if not 0 <= marker_id < len(items):
        raise ValueError(f"marker_id fuera de rango: {marker_id} (markers: {len(items)})")
    removed = items.pop(marker_id)
    if prop is not None:
        if items:
            prop.text = _format_markers(items)
        else:
            tractor.remove(prop)
    _save(tree, proj)
    return {"marker_id": marker_id,
            "removed": {"frame": removed[0], "text": removed[1], "color": removed[2]},
            "remaining_count": len(items), "project_path": str(proj)}


def ripple_delete(project_path: str, track_id: str, clip_index: int) -> dict:
    """Elimina el clip y cierra el gap (los playlists MLT son secuenciales).

    Tras el ``remove`` recalcula la duración total (suma de clips del track
    más largo) y la escribe en el ``out`` del tractor. Retorna el total en
    frames (misma unidad que compara ``timeline_length_frames``).
    """
    tree, root, proj = _load(project_path)
    pl = _playlist(root, track_id)
    entry = _clip(pl, clip_index)
    removed_producer = entry.get("producer", "")
    pl.remove(entry)
    new_duration = _timeline_total(root)
    tractor = root.find("tractor")
    if tractor is not None:
        tractor.set("out", str(max(0, new_duration - 1)))
    _save(tree, proj)
    return {"removed": True, "removed_producer": removed_producer,
            "track_id": track_id, "clip_index": clip_index,
            "new_duration": new_duration, "project_path": str(proj)}


def slip_clip(project_path: str, track_id: str, clip_index: int,
              offset_frames: int) -> dict:
    """Desplaza la ventana in/out dentro del medio sin mover el clip.

    ``in``/``out`` se mueven juntos (misma duración en el timeline).
    ``in`` resultante < 0 lanza ``ValueError``. Si el ``out`` supera la
    duración estimada del medio (ffprobe best-effort sobre el ``resource``)
    se advierte en ``warnings``; si no se puede verificar, warning genérico.
    """
    if isinstance(offset_frames, bool) or not isinstance(offset_frames, int):
        raise ValueError(f"offset_frames debe ser entero, recibido: {offset_frames!r}")
    tree, root, proj = _load(project_path)
    pl = _playlist(root, track_id)
    entry = _clip(pl, clip_index)
    cur_in, cur_out = _entry_bounds(entry)
    new_in = cur_in + offset_frames
    new_out = cur_out + offset_frames
    if new_in < 0:
        raise ValueError(f"slip deja in negativo ({new_in}); offset inválido: {offset_frames}")
    if new_out <= new_in:
        raise ValueError(f"slip deja out ({new_out}) <= in ({new_in})")
    entry.set("in", str(new_in))
    entry.set("out", str(new_out))
    warnings: list[str] = []
    resource = _producer_resource(root, entry.get("producer", ""))
    media_frames: int | None = None
    if resource and Path(resource).is_file():
        fps = _fps(root)
        dur = _probe_duration_sec(Path(resource))
        if dur is not None:
            media_frames = int(round(dur * fps))
    if media_frames is not None:
        if new_out >= media_frames:
            warnings.append(
                f"out ({new_out}) excede la duración estimada del medio "
                f"({media_frames} frames @ {fps:g}fps): {resource}"
            )
    else:
        warnings.append(
            "no se pudo verificar la duración del medio "
            f"(recurso no local o sin ffprobe): {resource or 'desconocido'}"
        )
    _save(tree, proj)
    return {"track_id": track_id, "clip_index": clip_index,
            "in": new_in, "out": new_out, "warnings": warnings,
            "project_path": str(proj)}


def multicam_sync(project_paths: list[str], output_project: str,
                  method: str = "peak") -> dict:
    """Sincroniza 2+ medios por audio y crea un proyecto multicámara.

    Con ``method="peak"`` (rápido) mide con ffmpeg
    ``silencedetect`` el primer fin-de-silencio de cada archivo (= inicio
    de audio). Con ``method="xcorr"`` (preciso) extrae audio mono 8 kHz
    y alinea por correlación cruzada FFT (scipy) contra el primer medio,
    con precisión de muestra. ``offset[medio] = round((onset - min_onset)
    * FPS)`` con ``FPS=30``; cada clip entra con ``in=offset`` para que
    todos los inicios de audio queden alineados en el timeline.
    Determinista para los mismos archivos. Sin pista de audio (o silencio
    total): ``ValueError``.
    """
    if method not in _VALID_METHODS:
        raise ValueError(f"method debe ser uno de {_VALID_METHODS}, recibido: {method!r}")
    if not isinstance(project_paths, list) or len(project_paths) < 2:
        raise ValueError("multicam_sync requiere una lista con 2+ rutas de medios")
    fps = _MULTICAM_FPS
    media: list[Path] = []
    for p in project_paths:
        if not isinstance(p, str) or not p.strip():
            raise ValueError(f"ruta de medio inválida: {p!r}")
        m = validate_path(p, must_exist=True)
        if not m.is_file():
            raise ValueError(f"medio no válido (no es archivo): {p}")
        media.append(m)
    if method == "peak":
        onsets = [_audio_onset_seconds(m) for m in media]
    else:
        onsets = _audio_xcorr_seconds(media)
    base = min(onsets)
    offsets = {str(project_paths[i]): int(round((on - base) * fps)) for i, on in enumerate(onsets)}
    out = validate_output_path(output_project)
    mlt = ET.Element("mlt", {"profile": "atsc_1080p_30", "version": "7.40.0",
                             "LC_NUMERIC": "C", "title": out.stem})
    ET.SubElement(mlt, "profile",
                  {"description": "atsc_1080p_30", "width": "1920", "height": "1080",
                   "progressive": "1", "display_aspect_num": "16", "display_aspect_den": "9",
                   "frame_rate_num": "30", "frame_rate_den": "1", "colorspace": "709",
                   "sample_aspect_num": "1", "sample_aspect_den": "1"})
    main_bin = ET.SubElement(mlt, "playlist", {"id": "main_bin"})
    ET.SubElement(main_bin, "property", {"name": "xml_retain"}).text = "1"
    for i, m in enumerate(media):
        pid = f"prod_{i + 1}"
        prod = ET.SubElement(mlt, "producer", {"id": pid})
        ET.SubElement(prod, "property", {"name": "mlt_service"}).text = "avformat"
        ET.SubElement(prod, "property", {"name": "resource"}).text = str(m.resolve())
        in_f = offsets[str(project_paths[i])]
        dur = _probe_duration_sec(m)
        total = int(round(dur * fps)) if dur is not None else in_f + 900
        out_f = max(total - 1, in_f + 1)
        pl = ET.SubElement(mlt, "playlist", {"id": f"playlist{i}"})
        ET.SubElement(pl, "property", {"name": "mlt_service"}).text = "playlist"
        ET.SubElement(pl, "entry",
                      {"producer": pid, "in": str(in_f), "out": str(out_f)})
    tractor = ET.SubElement(mlt, "tractor",
                            {"id": "tractor", "title": out.stem,
                             "in": "0", "out": "0"})
    ET.SubElement(tractor, "property", {"name": "mlt_service"}).text = "tractor"
    multitrack = ET.SubElement(tractor, "multitrack")
    for i in range(len(media)):
        ET.SubElement(multitrack, "track",
                      {"producer": f"playlist{i}", "hide": "audio"})
    tree = ET.ElementTree(mlt)
    ET.indent(tree, space="  ")
    tree.write(str(out), encoding="utf-8", xml_declaration=True)
    try:
        ensure_composite_transitions(str(out))
    except Exception as exc:
        log.warning("composites no garantizados en %s: %s", out, exc)
    return {"project_path": str(out), "offsets": offsets,
            "fps": fps, "method": method}


def nest_sequence(parent_project: str, child_project: str, track_id: str,
                  position: int = -1) -> dict:
    """Anida ``child_project`` como clip del track (producer ``xml`` + entry).

    Crea ``<producer mlt_service="xml" resource=child_abs>`` y una entry
    ``in=0 out=len(child)-1`` (o ventana 150 frames si el hijo está vacío).
    Valida que el hijo exista y sea XML MLT válido.
    """
    if isinstance(position, bool) or not isinstance(position, int):
        raise ValueError(f"position debe ser entero, recibido: {position!r}")
    tree, root, proj = _load(parent_project)
    pl = _playlist(root, track_id)
    child = validate_path(child_project, must_exist=True)
    if not child.is_file():
        raise ValueError(f"hijo no válido (no es archivo): {child_project}")
    try:
        child_tree = ET.parse(str(child))
    except ET.ParseError as exc:
        raise ValueError(f"hijo XML inválido: {child_project}: {exc}") from exc
    child_root = child_tree.getroot()
    if child_root.tag != "mlt":
        raise ValueError(f"hijo no es proyecto MLT/Kdenlive válido: {child_project}")
    child_frames = _timeline_total(child_root)
    if child_frames <= 0:
        tout = (child_root.find("tractor") or ET.Element("x")).get("out", "")
        if str(tout).lstrip("-").isdigit() and int(str(tout)) > 0:
            child_frames = int(str(tout)) + 1
    if child_frames <= 0:
        child_frames = 150
    resource = str(child.resolve())
    producer_id = ""
    for prod in root.findall("producer"):
        props = {(p.get("name") or ""): (p.text or "") for p in prod.findall("property")}
        if props.get("mlt_service") == "xml" and props.get("resource", "").strip() == resource:
            producer_id = prod.get("id", "")
            break
    if not producer_id:
        producer_id = _next_id(root, "producer", "prod_")
        prod = ET.Element("producer", {"id": producer_id})
        ET.SubElement(prod, "property", {"name": "mlt_service"}).text = "xml"
        ET.SubElement(prod, "property", {"name": "resource"}).text = resource
        children = list(root)
        producers = [e for e in children if e.tag == "producer"]
        if producers:
            root.insert(children.index(producers[-1]) + 1, prod)
        else:
            pos = next((i for i, e in enumerate(children)
                        if e.tag in ("playlist", "tractor")), len(children))
            root.insert(pos, prod)
    entries = _entries(pl)
    if position == -1:
        position = len(entries)
    if not 0 <= position <= len(entries):
        raise ValueError(f"position fuera de rango: {position} (clips: {len(entries)})")
    _insert_at_entry_index(
        pl, ET.Element("entry", {"producer": producer_id, "in": "0",
                                 "out": str(child_frames - 1)}), position)
    _save(tree, proj)
    return {"producer_id": producer_id, "clip_index": position,
            "track_id": track_id, "child_project": str(child),
            "project_path": str(proj)}


def auto_chapters(
    project_path: str,
    interval_sec: float = 60.0,
    prefix: str = "Cap",
) -> dict:
    """Genera capitulos: usa markers existentes o crea uno cada interval_sec.

    Si el proyecto ya tiene markers, los devuelve como capitulos sin
    modificar nada. Si no, crea markers equiespaciados sobre la duracion
    real del timeline y los devuelve. Retorna {chapters, count, created}.
    """
    if isinstance(interval_sec, bool) or not isinstance(
        interval_sec, (int, float)
    ) or float(interval_sec) <= 0:
        raise ValueError(f"interval_sec debe ser > 0, recibido: {interval_sec!r}")
    if not isinstance(prefix, str) or not prefix.strip():
        raise ValueError("prefix debe ser str no vacío")
    tree, root, proj = _load(project_path)
    fps = _fps(root) or _FPS_DEFAULT
    tractor = root.find("tractor")
    raw = ""
    if tractor is not None:
        prop = _markers_prop(tractor)
        raw = (prop.text or "") if prop is not None else ""
    existing = _parse_markers(raw)
    if existing:
        chapters = [
            {"index": i, "frame": f, "time_sec": round(f / fps, 2),
             "text": t, "color": c}
            for i, (f, t, c) in enumerate(existing)
        ]
        return {"chapters": chapters, "count": len(chapters),
                "created": False, "project_path": str(proj)}
    total = _timeline_total(root)
    if total <= 0:
        return {"chapters": [], "count": 0, "created": False,
                "project_path": str(proj)}
    step = max(1, int(round(float(interval_sec) * fps)))
    frames = list(range(0, total, step)) or [0]
    items = [(f, f"{prefix.strip()} {i + 1}", "blue")
             for i, f in enumerate(frames)]
    if tractor is None:
        tractor, _ = _ensure_tractor(root)
    prop = _markers_prop(tractor)
    if prop is None:
        prop = ET.SubElement(tractor, "property", {"name": _MARKERS_PROP})
    prop.text = _format_markers(items)
    _save(tree, proj)
    chapters = [
        {"index": i, "frame": f, "time_sec": round(f / fps, 2),
         "text": t, "color": c}
        for i, (f, t, c) in enumerate(items)
    ]
    return {"chapters": chapters, "count": len(chapters),
            "created": True, "project_path": str(proj)}


def get_thumbnail(
    media_path: str,
    output_path: str = "",
    at_sec: float = 1.0,
    width: int = 320,
) -> dict:
    """Extrae un frame JPG del medio con ffmpeg (best-effort puro).

    Retorna {thumbnail, at_sec, width}. Lanza ValueError/RuntimeError si
    el medio no existe o ffmpeg falla.
    """
    if not isinstance(at_sec, (int, float)) or float(at_sec) < 0:
        raise ValueError(f"at_sec debe ser >= 0, recibido: {at_sec!r}")
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise ValueError(f"width debe ser entero > 0, recibido: {width!r}")
    media = validate_path(str(media_path), must_exist=True)
    if not media.is_file():
        raise ValueError(f"medio no válido (no es archivo): {media_path}")
    out = (validate_output_path(output_path) if output_path
           else validate_output_path(str(media.with_suffix("")) + "_thumb.jpg"))
    proc = subprocess.run(
        [_ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
         "-ss", str(float(at_sec)), "-i", str(media),
         "-frames:v", "1", "-vf", f"scale={width}:-2", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(
            f"thumbnail falló: {(proc.stderr or '')[-500:]}")
    return {"thumbnail": str(out), "at_sec": float(at_sec),
            "width": width}
