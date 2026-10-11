"""Efectos de video/color para proyectos Kdenlive (.kdenlive = XML MLT).

Convencion de almacenamiento (unica):
    Cada efecto es un elemento <filter> hijo directo del <entry> del clip
    dentro del <playlist id="track_id"> correspondiente::

        <playlist id="v1">
          <entry producer="clip1" in="0" out="149">
            <filter id="filter_0" mlt_service="avfilter.boxblur" in="0" out="149">
              <property name="mlt_service">avfilter.boxblur</property>
              <property name="radius">5.0</property>
            </filter>
          </entry>
        </playlist>

    Los atributos in/out del filter se copian del entry del clip.
    Los IDs tienen formato ``filter_N`` y son unicos en todo el documento
    (se calcula max(N)+1 sobre los <filter> existentes).

    Keyframes: una propiedad con varios valores usa el formato
    ``"frame=valor;frame2=valor2"`` ordenado por frame ascendente.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

try:  # security.py lo crea otro modulo; fallback si aun no existe
    from kdenlive_mcp.security import validate_path
except ImportError:  # pragma: no cover
    try:
        from .security import validate_path  # type: ignore[no-redef]
    except ImportError:

        def validate_path(p: str | Path) -> Path:  # type: ignore[no-redef]
            return Path(p)


EFFECT_MAP: dict[str, str] = {
    "blur": "avfilter.boxblur",
    "sharpen": "avfilter.unsharp",
    "transform": "affine",
    "color": "avfilter.eq",
    "brightness": "brightness",
    "volume": "volume",
}

_FILTER_ID_RE = re.compile(r"^filter_(\d+)$")


# ---------------------------------------------------------------------------
# Helpers XML privados
# ---------------------------------------------------------------------------

def _load_root(project_path: str) -> tuple[ET.ElementTree, ET.Element, Path]:
    """Carga el proyecto y devuelve (tree, root, path resuelto)."""
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


def _get_entry(playlist: ET.Element, clip_index: int) -> ET.Element:
    if clip_index < 0:
        raise IndexError(f"clip_index fuera de rango: {clip_index}")
    entries = [child for child in playlist if child.tag == "entry"]
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


def _get_filter(entry: ET.Element, effect_id: str) -> ET.Element:
    for filt in entry.findall("filter"):
        if filt.get("id") == effect_id:
            return filt
    raise ValueError(f"Efecto no encontrado en el clip: {effect_id!r}")


def _set_property(filt: ET.Element, name: str, value: str) -> None:
    for prop in filt.findall("property"):
        if prop.get("name") == name:
            prop.text = value
            return
    prop = ET.SubElement(filt, "property", {"name": name})
    prop.text = value


def _get_property(filt: ET.Element, name: str) -> ET.Element | None:
    for prop in filt.findall("property"):
        if prop.get("name") == name:
            return prop
    return None


def _parse_keyframes(text: str) -> list[tuple[int, str]]:
    """Parsea "f=v;f2=v2" o un valor plano (se trata como frame 0)."""
    text = (text or "").strip()
    if not text:
        return []
    if "=" not in text:
        return [(0, text)]
    pairs: list[tuple[int, str]] = []
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        frame_s, _, val = chunk.partition("=")
        try:
            frame = int(frame_s.strip())
        except ValueError:
            continue
        if frame < 0:
            continue
        pairs.append((frame, val.strip()))
    return pairs


def _format_keyframes(pairs: list[tuple[int, str]]) -> str:
    ordered = sorted(pairs, key=lambda pair: pair[0])
    return ";".join(f"{frame}={value}" for frame, value in ordered)


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

def add_effect(
    project_path: str,
    track_id: str,
    clip_index: int,
    effect: str,
    params: dict | None = None,
) -> dict:
    """Anade un efecto como <filter> hijo del <entry> del clip."""
    if effect not in EFFECT_MAP:
        raise ValueError(
            f"Efecto desconocido: {effect!r}. "
            f"Soportados: {sorted(EFFECT_MAP)}"
        )
    tree, root, resolved = _load_root(project_path)
    entry = _get_entry(_find_playlist(root, track_id), clip_index)
    service = EFFECT_MAP[effect]
    effect_id = _next_filter_id(root)

    attrs: dict[str, str] = {"id": effect_id, "mlt_service": service}
    if entry.get("in") is not None:
        attrs["in"] = entry.get("in", "")
    if entry.get("out") is not None:
        attrs["out"] = entry.get("out", "")
    filt = ET.SubElement(entry, "filter", attrs)
    _set_property(filt, "mlt_service", service)
    applied: dict[str, str] = {}
    for key, val in (params or {}).items():
        str_val = str(val)
        _set_property(filt, str(key), str_val)
        applied[str(key)] = str_val

    _save(tree, resolved)
    return {
        "effect_id": effect_id,
        "mlt_service": service,
        "track_id": track_id,
        "clip_index": clip_index,
        "params": applied,
    }


def remove_effect(
    project_path: str, track_id: str, clip_index: int, effect_id: str
) -> dict:
    """Elimina el <filter> con id dado del clip indicado."""
    tree, root, resolved = _load_root(project_path)
    entry = _get_entry(_find_playlist(root, track_id), clip_index)
    filt = _get_filter(entry, effect_id)
    entry.remove(filt)
    _save(tree, resolved)
    return {"removed": effect_id, "track_id": track_id, "clip_index": clip_index}


def set_effect_parameter(
    project_path: str,
    track_id: str,
    clip_index: int,
    effect_id: str,
    param: str,
    value: str,
) -> dict:
    """Crea o actualiza una propiedad del efecto."""
    if not param:
        raise ValueError("param no puede estar vacio")
    tree, root, resolved = _load_root(project_path)
    entry = _get_entry(_find_playlist(root, track_id), clip_index)
    filt = _get_filter(entry, effect_id)
    _set_property(filt, param, str(value))
    _save(tree, resolved)
    return {
        "effect_id": effect_id,
        "param": param,
        "value": str(value),
        "track_id": track_id,
        "clip_index": clip_index,
    }


def add_keyframe(
    project_path: str,
    track_id: str,
    clip_index: int,
    effect_id: str,
    frame: int,
    param: str,
    value: str,
) -> dict:
    """Anade un keyframe; la propiedad queda como "f=v;f2=v2" ordenada."""
    frame = int(frame)
    if frame < 0:
        raise ValueError(f"frame debe ser >= 0, recibido: {frame}")
    if not param:
        raise ValueError("param no puede estar vacio")
    tree, root, resolved = _load_root(project_path)
    entry = _get_entry(_find_playlist(root, track_id), clip_index)
    filt = _get_filter(entry, effect_id)

    prop = _get_property(filt, param)
    pairs = _parse_keyframes(prop.text or "" if prop is not None else "")
    pairs = [(frm, val) for frm, val in pairs if frm != frame]
    pairs.append((frame, str(value)))
    keyframes = _format_keyframes(pairs)
    if prop is None:
        _set_property(filt, param, keyframes)
    else:
        prop.text = keyframes
    _save(tree, resolved)
    return {
        "effect_id": effect_id,
        "param": param,
        "frame": frame,
        "value": str(value),
        "keyframes": keyframes,
    }


def add_transform(
    project_path: str,
    track_id: str,
    clip_index: int,
    scale: float = 1.0,
    x: int = 0,
    y: int = 0,
) -> dict:
    """Atajo para efecto affine con escala y desplazamiento."""
    scale = float(scale)
    if scale <= 0:
        raise ValueError(f"scale debe ser > 0, recibido: {scale}")
    params = {
        "scale_x": str(scale),
        "scale_y": str(scale),
        "x": str(int(x)),
        "y": str(int(y)),
        "geometry": f"{int(x)},{int(y)},{scale}",
    }
    return add_effect(project_path, track_id, clip_index, "transform", params)


def add_blur(
    project_path: str, track_id: str, clip_index: int, radius: float = 5.0
) -> dict:
    """Atajo para desenfoque avfilter.boxblur."""
    radius = float(radius)
    if radius < 0:
        raise ValueError(f"radius debe ser >= 0, recibido: {radius}")
    return add_effect(
        project_path, track_id, clip_index, "blur", {"radius": str(radius)}
    )


def add_sharpen(
    project_path: str, track_id: str, clip_index: int, amount: float = 0.5
) -> dict:
    """Atajo para enfoque avfilter.unsharp."""
    amount = float(amount)
    if amount < 0:
        raise ValueError(f"amount debe ser >= 0, recibido: {amount}")
    return add_effect(
        project_path, track_id, clip_index, "sharpen", {"amount": str(amount)}
    )


# Presets YouTube: un filtro avfilter.eq (servicio real verificado contra
# catalog_tools, fallback curado) con un parámetro animado (keyframes
# "0=inicio;mitad=final" para fundir el look al entrar el clip).
PRESETS: dict[str, dict] = {
    "limpio": {
        "service": "avfilter.eq",
        "params": {"av.contrast": "1.05", "av.saturation": "1.05"},
        "animated": ("av.brightness", "0.0", "0.02"),
        "aliases": ("limpio", "clean"),
    },
    "cine": {
        "service": "avfilter.eq",
        "params": {"av.contrast": "1.15", "av.brightness": "-0.03"},
        "animated": ("av.saturation", "1.0", "0.85"),
        "aliases": ("cine", "cinematic", "cinema"),
    },
    "vivo": {
        "service": "avfilter.eq",
        "params": {"av.contrast": "1.12", "av.brightness": "0.01"},
        "animated": ("av.saturation", "1.0", "1.35"),
        "aliases": ("vivo", "vivid"),
    },
}


def _resolve_service(candidates: list[str]) -> str:
    """Primer candidato presente en el catálogo real; si no, curado."""
    try:
        from kdenlive_mcp.catalog_tools import list_filters
        known = {item["name"] for item in list_filters()["filters"]}
    except Exception:
        known = set()
    for candidate in candidates:
        if candidate in known:
            return candidate
    return candidates[0]


def apply_preset(
    project_path: str, track_id: str, clip_index: int, preset: str = "limpio"
) -> dict:
    """Aplica un preset YouTube (limpio/cine/vivo) con fundido por keyframes."""
    key = (preset or "").strip().lower()
    spec: dict | None = None
    for name, candidate in PRESETS.items():
        if key == name or key in candidate["aliases"]:
            spec = candidate
            key = name
            break
    if spec is None:
        raise ValueError(
            f"Preset desconocido: {preset!r}. "
            f"Soportados: {sorted(PRESETS)}"
        )
    service = _resolve_service([spec["service"]])
    tree, root, resolved = _load_root(project_path)
    entry = _get_entry(_find_playlist(root, track_id), clip_index)
    effect_id = _next_filter_id(root)
    attrs: dict[str, str] = {"id": effect_id, "mlt_service": service}
    if entry.get("in") is not None:
        attrs["in"] = entry.get("in", "")
    if entry.get("out") is not None:
        attrs["out"] = entry.get("out", "")
    filt = ET.SubElement(entry, "filter", attrs)
    _set_property(filt, "mlt_service", service)
    applied: dict[str, str] = {}
    for name_param, val in spec["params"].items():
        _set_property(filt, name_param, str(val))
        applied[name_param] = str(val)
    param, start_val, end_val = spec["animated"]
    try:
        mid = max(1, (int(entry.get("out", "1")) - int(entry.get("in", "0"))) // 2)
    except (TypeError, ValueError):
        mid = 15
    keyframes = f"0={start_val};{mid}={end_val}"
    _set_property(filt, param, keyframes)
    applied[param] = keyframes
    _save(tree, resolved)
    return {
        "effect_id": effect_id,
        "mlt_service": service,
        "preset": key,
        "track_id": track_id,
        "clip_index": clip_index,
        "params": applied,
    }
