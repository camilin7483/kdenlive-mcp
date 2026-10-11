"""Timeline tools for Kdenlive/MLT projects.

Funciones puras que retornan dicts (sin decoradores MCP). Operan sobre el
XML MLT con ElementTree: tracks son ``playlist id="playlistN"`` enlazadas
al ``tractor`` vía ``multitrack/track`` (``hide="audio"`` = track de video,
``hide="video"`` = track de audio). Cada mutación guarda el XML sin romper
la estructura tractor/multitrack.

Frames: enteros >= 0, ``out > in``. Errores ``ValueError``/``FileNotFoundError``.
"""

from __future__ import annotations

import copy
import logging
import re
from pathlib import Path
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

try:
    from kdenlive_mcp.security import validate_path, validate_output_path  # noqa: F401
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

try:
    from kdenlive_mcp.kdenlive_project import ensure_composite_transitions
except ImportError:
    try:
        from .kdenlive_project import ensure_composite_transitions  # type: ignore
    except ImportError:
        def ensure_composite_transitions(project_path: str) -> dict:  # type: ignore
            return {"added": 0, "tracks": 0}

_VALID_KINDS = ("video", "audio")
_VALID_TRANSITIONS = ("dissolve", "wipe")
_HIDE_FOR_KIND = {"video": "audio", "audio": "video"}


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


def _prop(parent: ET.Element, name: str, value: str) -> None:
    for prop in parent.findall("property"):
        if prop.get("name") == name:
            prop.text = value
            return
    prop = ET.SubElement(parent, "property", {"name": name})
    prop.text = value


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


def _next_playlist_id(root: ET.Element) -> str:
    taken = set()
    for pl in root.findall("playlist"):
        pid = pl.get("id", "")
        m = re.fullmatch(r"playlist(\d+)", pid or "")
        if m:
            taken.add(int(m.group(1)))
    n = 1
    while n in taken:
        n += 1
    return f"playlist{n}"


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


def _track_position(multitrack: ET.Element, track_id: str) -> int | None:
    tracks = [t for t in multitrack if t.tag == "track"]
    for i, track in enumerate(tracks):
        if track.get("producer") == track_id:
            return i
    return None


def _insert_at_entry_index(pl: ET.Element, elem: ET.Element, position: int) -> None:
    """Inserta elem en la posición lógica `position` entre las entries."""
    children = list(pl)
    entry_pos = [i for i, e in enumerate(children) if e.tag == "entry"]
    if position >= len(entry_pos):
        pl.append(elem)
    else:
        pl.insert(entry_pos[position], elem)


def _producer_by_resource(root: ET.Element, resource: str) -> str | None:
    for prod in root.findall("producer"):
        for prop in prod.findall("property"):
            if prop.get("name") == "resource" and (prop.text or "") == resource:
                pid = prod.get("id")
                if pid:
                    return pid
    return None


def _create_producer(root: ET.Element, resource: str) -> str:
    producer_id = _next_id(root, "producer", "prod_")
    prod = ET.Element("producer", {"id": producer_id})
    _prop(prod, "mlt_service", "avformat")
    _prop(prod, "resource", resource)
    children = list(root)
    producers = [e for e in children if e.tag == "producer"]
    if producers:
        root.insert(children.index(producers[-1]) + 1, prod)
    else:
        pos = next((i for i, e in enumerate(children) if e.tag in ("playlist", "tractor")), len(children))
        root.insert(pos, prod)
    return producer_id


def _entry_bounds(entry: ET.Element) -> tuple[int, int]:
    try:
        in_f = int(entry.get("in", "0"))
        out_f = int(entry.get("out", "0"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Clip con frames no numéricos: in={entry.get('in')} out={entry.get('out')}") from exc
    return in_f, out_f


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def create_track(project_path: str, kind: str = "video", name: str = "") -> dict:
    """Crea un track (playlist + referencia en el multitrack del tractor)."""
    if kind not in _VALID_KINDS:
        raise ValueError(f"kind debe ser uno de {_VALID_KINDS}, recibido: {kind!r}")
    tree, root, proj = _load(project_path)
    track_id = _next_playlist_id(root)
    pl = ET.Element("playlist", {"id": track_id})
    if name:
        pl.set("title", name)
    _prop(pl, "mlt_service", "playlist")
    children = list(root)
    tractor_pos = next((i for i, e in enumerate(children) if e.tag == "tractor"), len(children))
    root.insert(tractor_pos, pl)
    _, multitrack = _ensure_tractor(root)
    multitrack.append(ET.Element("track", {"producer": track_id, "hide": _HIDE_FOR_KIND[kind]}))
    _save(tree, proj)
    return {"track_id": track_id, "project_path": str(proj)}


def delete_track(project_path: str, track_id: str) -> dict:
    """Elimina un track (playlist y sus referencias en multitracks)."""
    tree, root, proj = _load(project_path)
    root.remove(_playlist(root, track_id))
    removed_refs = 0
    for multitrack in root.iter("multitrack"):
        for track in [t for t in list(multitrack) if t.tag == "track" and t.get("producer") == track_id]:
            multitrack.remove(track)
            removed_refs += 1
    _save(tree, proj)
    return {"track_id": track_id, "removed_refs": removed_refs, "project_path": str(proj)}


def add_clip(
    project_path: str,
    track_id: str,
    media_path: str = "",
    producer_id: str = "",
    in_frame: int = 0,
    out_frame: int = 149,
    position: int = -1,
) -> dict:
    """Añade un clip al track; auto-crea el producer si se da media_path nuevo."""
    in_frame = _frame(in_frame, "in_frame")
    out_frame = _frame(out_frame, "out_frame")
    if out_frame <= in_frame:
        raise ValueError(f"out_frame ({out_frame}) debe ser > in_frame ({in_frame})")
    tree, root, proj = _load(project_path)
    pl = _playlist(root, track_id)
    if producer_id:
        if not any(p.get("id") == producer_id for p in root.findall("producer")):
            raise ValueError(f"Producer desconocido: {producer_id}")
    elif media_path:
        media = validate_path(media_path, must_exist=True)
        if not media.is_file():
            raise ValueError(f"Medio no válido (no es archivo): {media_path}")
        producer_id = _producer_by_resource(root, str(media.resolve())) or _create_producer(
            root, str(media.resolve())
        )
    else:
        raise ValueError("Se requiere media_path o producer_id")
    entries = _entries(pl)
    if position == -1:
        position = len(entries)
    if isinstance(position, bool) or not isinstance(position, int) or not 0 <= position <= len(entries):
        raise ValueError(f"position fuera de rango: {position} (clips: {len(entries)})")
    _insert_at_entry_index(
        pl, ET.Element("entry", {"producer": producer_id, "in": str(in_frame), "out": str(out_frame)}), position
    )
    _save(tree, proj)
    return {"clip_index": position, "producer_id": producer_id, "track_id": track_id,
            "project_path": str(proj)}


def remove_clip(project_path: str, track_id: str, clip_index: int) -> dict:
    """Elimina el clip en clip_index del track."""
    tree, root, proj = _load(project_path)
    pl = _playlist(root, track_id)
    entry = _clip(pl, clip_index)
    removed_producer = entry.get("producer", "")
    pl.remove(entry)
    _save(tree, proj)
    return {"track_id": track_id, "clip_index": clip_index, "producer_id": removed_producer,
            "project_path": str(proj)}


def move_clip(project_path: str, track_id: str, clip_index: int, new_index: int) -> dict:
    """Mueve un clip a otra posición dentro del mismo track."""
    tree, root, proj = _load(project_path)
    pl = _playlist(root, track_id)
    entry = _clip(pl, clip_index)
    count = len(_entries(pl))
    if isinstance(new_index, bool) or not isinstance(new_index, int) or not 0 <= new_index < count:
        raise ValueError(f"new_index fuera de rango: {new_index} (clips: {count})")
    if new_index != clip_index:
        pl.remove(entry)
        _insert_at_entry_index(pl, entry, new_index)
        _save(tree, proj)
    return {"track_id": track_id, "old_index": clip_index, "new_index": new_index,
            "project_path": str(proj)}


def trim_clip(project_path: str, track_id: str, clip_index: int,
              in_frame: int = -1, out_frame: int = -1) -> dict:
    """Ajusta in/out del clip; -1 conserva el valor actual."""
    tree, root, proj = _load(project_path)
    pl = _playlist(root, track_id)
    entry = _clip(pl, clip_index)
    cur_in, cur_out = _entry_bounds(entry)
    new_in = cur_in if in_frame == -1 else _frame(in_frame, "in_frame")
    new_out = cur_out if out_frame == -1 else _frame(out_frame, "out_frame")
    if new_out <= new_in:
        raise ValueError(f"out_frame ({new_out}) debe ser > in_frame ({new_in})")
    entry.set("in", str(new_in))
    entry.set("out", str(new_out))
    _save(tree, proj)
    return {"track_id": track_id, "clip_index": clip_index, "in_frame": new_in,
            "out_frame": new_out, "project_path": str(proj)}


def split_clip(project_path: str, track_id: str, clip_index: int, at_frame: int) -> dict:
    """Divide un clip en dos; at_frame es el offset relativo al inicio del clip."""
    if isinstance(at_frame, bool) or not isinstance(at_frame, int):
        raise ValueError(f"at_frame debe ser entero, recibido: {at_frame!r}")
    tree, root, proj = _load(project_path)
    pl = _playlist(root, track_id)
    entry = _clip(pl, clip_index)
    cur_in, cur_out = _entry_bounds(entry)
    length = cur_out - cur_in + 1
    if not 0 < at_frame < length:
        raise ValueError(f"at_frame fuera de rango: {at_frame} (duración del clip: {length})")
    entry.set("out", str(cur_in + at_frame - 1))
    second = ET.Element("entry", {k: v for k, v in entry.attrib.items() if k not in ("in", "out")})
    second.set("in", str(cur_in + at_frame))
    second.set("out", str(cur_out))
    for child in entry:
        clone = copy.deepcopy(child)
        if clone.tag == "filter":
            clone.set("id", "__pending__")
        second.append(clone)
    _insert_at_entry_index(pl, second, clip_index + 1)
    for filt in second.findall("filter"):
        if filt.get("id") == "__pending__":
            filt.set("id", _next_id(root, "filter", "filter_"))
    _save(tree, proj)
    return {"track_id": track_id, "clip_index": clip_index, "new_index": clip_index + 1,
            "project_path": str(proj)}


def duplicate_clip(project_path: str, track_id: str, clip_index: int) -> dict:
    """Duplica un clip insertando la copia justo después del original."""
    tree, root, proj = _load(project_path)
    pl = _playlist(root, track_id)
    clone = copy.deepcopy(_clip(pl, clip_index))
    for child in clone:
        if child.tag == "filter":
            child.set("id", "__pending__")
    _insert_at_entry_index(pl, clone, clip_index + 1)
    for filt in clone.findall("filter"):
        if filt.get("id") == "__pending__":
            filt.set("id", _next_id(root, "filter", "filter_"))
    _save(tree, proj)
    return {"track_id": track_id, "clip_index": clip_index, "new_index": clip_index + 1,
            "project_path": str(proj)}


def add_transition(project_path: str, track_id: str, clip_index: int,
                   transition_type: str = "dissolve", duration_frames: int = 30) -> dict:
    """Añade una transición (hija del tractor): dissolve→mix, wipe→luma."""
    if transition_type not in _VALID_TRANSITIONS:
        raise ValueError(f"transition_type debe ser uno de {_VALID_TRANSITIONS}, recibido: {transition_type!r}")
    if isinstance(duration_frames, bool) or not isinstance(duration_frames, int) or duration_frames <= 0:
        raise ValueError(f"duration_frames debe ser entero > 0, recibido: {duration_frames!r}")
    tree, root, proj = _load(project_path)
    pl = _playlist(root, track_id)
    entry = _clip(pl, clip_index)
    tractor, multitrack = _ensure_tractor(root)
    track_pos = _track_position(multitrack, track_id)
    if track_pos is None:
        raise ValueError(f"Track sin referencia en el multitrack: {track_id}")
    _, clip_out = _entry_bounds(entry)
    start = max(0, clip_out - duration_frames + 1)
    transition_id = _next_id(root, "transition", "trans_")
    trans = ET.Element("transition", {"id": transition_id})
    _prop(trans, "mlt_service", "mix" if transition_type == "dissolve" else "luma")
    if transition_type == "wipe":
        _prop(trans, "resource", "luma")
    _prop(trans, "in", str(start))
    _prop(trans, "out", str(clip_out))
    _prop(trans, "a_track", str(track_pos))
    _prop(trans, "b_track", str(track_pos))
    tractor.append(trans)
    _save(tree, proj)
    return {"transition_id": transition_id, "track_id": track_id, "clip_index": clip_index,
            "project_path": str(proj)}
