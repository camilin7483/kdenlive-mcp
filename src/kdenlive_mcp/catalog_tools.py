"""Catalogo dinamico de efectos MLT para proyectos Kdenlive.

Las listas de servicios se obtienen del MLT instalado (`melt -query ...`)
en vez de estar hardcodeadas, con cache en memoria de 1h.

Convencion de almacenamiento (igual que effects_tools.add_effect):
    Cada efecto es un elemento <filter> hijo directo del <entry> del clip
    dentro del <playlist id="track_id"> correspondiente::

        <playlist id="v1">
          <entry producer="clip1" in="0" out="149">
            <filter id="filter_0" mlt_service="avfilter.boxblur" in="0" out="149">
              <property name="mlt_service">avfilter.boxblur</property>
              <property name="av.luma_radius">5</property>
            </filter>
          </entry>
        </playlist>

    Los IDs tienen formato ``filter_N`` y son unicos en todo el documento.
    Las transiciones son hijas del <tractor> con propiedades
    mlt_service/a_track/b_track.
"""

from __future__ import annotations

import re
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

try:  # mismo fallback que effects_tools si security aun no existe
    from kdenlive_mcp.security import validate_path
except ImportError:  # pragma: no cover
    try:
        from .security import validate_path  # type: ignore[no-redef]
    except ImportError:

        def validate_path(p: str | Path) -> Path:  # type: ignore[no-redef]
            return Path(p)  # type: ignore[arg-type]


MELT_TIMEOUT = 30
_CACHE_TTL = 3600  # 1h

# Listas curadas: fallback cuando melt no está disponible o devuelve vacío.
# Deben cubrir los servicios que usan effects/subtitle/timeline_tools.
CURATED_FILTERS: list[str] = [
    "affine",
    "brightness",
    "volume",
    "subtitle",
    "composite",
    "mix",
    "avfilter.eq",
    "avfilter.boxblur",
    "avfilter.unsharp",
    "avfilter.volume",
    "boxblur",
    "frei0r.brightness",
    "frei0r.cairoblend",
    "movit.opacity",
    "movit.saturation",
]

CURATED_TRANSITIONS: list[str] = [
    "mix",
    "composite",
    "frei0r.cairoblend",
    "movit.mix",
    "movit.overlay",
]

CURATED_PRODUCERS: list[str] = [
    "color",
    "colour",
    "noise",
    "frei0r.test_pat_B",
    "frei0r.ising0r",
]

CURATED_PARAMS: dict[str, list[dict]] = {
    "avfilter.eq": [
        {"name": "av.contrast", "default": "1.0"},
        {"name": "av.brightness", "default": "0.0"},
        {"name": "av.saturation", "default": "1.0"},
    ],
}

# Cache global: clave -> (timestamp_monotonico, resultado)
_CACHE: dict[str, tuple[float, dict]] = {}

_KIND_PREFIXES = ("frei0r", "avfilter", "ladspa", "movit", "composite")

_FILTER_ID_RE = re.compile(r"^filter_(\d+)$")
_TRANSITION_ID_RE = re.compile(r"^transition(\d+)$")
_LIST_ITEM_RE = re.compile(r"^\s*-\s+(\S.*?)\s*$")
_DESCRIBE_ENTRY_RE = re.compile(r"^(\s*)-\s+identifier:\s*(.+?)\s*$")
_PROP_RE = re.compile(r"^\s*([A-Za-z0-9_.*-]+)\s*:\s*(.*?)\s*$")


# ---------------------------------------------------------------------------
# Internos: melt + cache
# ---------------------------------------------------------------------------

def clear_cache() -> None:
    """Vacia la cache de queries melt (util en tests)."""
    _CACHE.clear()


def _run_melt(args: list[str]) -> str:
    """Ejecuta `melt <args>` y devuelve stdout; fallos -> ValueError."""
    try:
        proc = subprocess.run(
            ["melt", *args],
            capture_output=True,
            text=True,
            timeout=MELT_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise ValueError("melt no disponible en PATH (MLT no instalado)") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            f"melt tardo mas de {MELT_TIMEOUT}s: melt {' '.join(args)}"
        ) from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-500:].strip()
        raise ValueError(f"melt fallo (rc={proc.returncode}): {tail}")
    return proc.stdout or ""


def _cached(key: str, loader: Callable[[], dict]) -> dict:
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit is not None and now - hit[0] < _CACHE_TTL:
        return hit[1]
    result = loader()
    _CACHE[key] = (now, result)
    return result


def _parse_name_list(output: str) -> list[str]:
    """Extrae los nombres de `melt -query <tipo>` (formato `- nombre`)."""
    names: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.endswith(":"):
            continue
        match = _LIST_ITEM_RE.match(line)
        if match:
            name = match.group(1).strip().strip("\"'")
            if name:
                names.append(name)
    return names


def _query_names(kind: str) -> list[str]:
    curated = {
        "filters": CURATED_FILTERS,
        "transitions": CURATED_TRANSITIONS,
        "producers": CURATED_PRODUCERS,
    }.get(kind, [])

    def _load() -> dict:
        try:
            names = _parse_name_list(_run_melt(["-query", kind]))
        except ValueError:
            return {"names": list(curated)}
        return {"names": names or list(curated)}

    return _cached(f"query:{kind}", _load)["names"]


def _service_type(name: str) -> str:
    """Familia del servicio: prefijo antes del primer punto, o 'mlt'."""
    return name.split(".", 1)[0] if "." in name else "mlt"


def _coerce_scalar(raw: str) -> object:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1]
    for conv in (int, float):
        try:
            return conv(text)  # type: ignore[call-arg]
        except ValueError:
            continue
    return text


def _parse_describe_params(output: str) -> list[dict]:
    """Parsea la seccion `parameters:` de `melt -query filter=<svc>`."""
    params: list[dict] = []
    current: dict | None = None
    dash_indent = 0
    in_params = False
    for line in output.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if not in_params:
            if _PROP_RE.match(line) and line.strip() == "parameters:":
                in_params = True
            continue
        entry = _DESCRIBE_ENTRY_RE.match(line)
        if entry:
            dash_indent = len(entry.group(1))
            current = {"name": entry.group(2).strip().strip("\"'")}
            params.append(current)
            continue
        prop = _PROP_RE.match(line)
        if prop is None or line.strip().startswith("- "):
            continue  # sublista (values:) u otra estructura anidada
        indent = len(line) - len(line.lstrip(" "))
        if indent <= dash_indent:
            break  # fin de la seccion parameters
        if current is not None and prop.group(2) != "":
            current[prop.group(1)] = _coerce_scalar(prop.group(2))
    return params


# ---------------------------------------------------------------------------
# Internos: XML
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


def _get_entry(playlist: ET.Element, clip_index: int) -> ET.Element:
    if isinstance(clip_index, bool) or not isinstance(clip_index, int):
        raise ValueError(f"clip_index debe ser entero, recibido: {clip_index!r}")
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


def _next_transition_id(root: ET.Element) -> str:
    max_n = -1
    for trans in root.iter("transition"):
        match = _TRANSITION_ID_RE.match(trans.get("id", ""))
        if match:
            max_n = max(max_n, int(match.group(1)))
    return f"transition{max_n + 1}"


def _set_property(parent: ET.Element, name: str, value: str) -> None:
    for prop in parent.findall("property"):
        if prop.get("name") == name:
            prop.text = value
            return
    prop = ET.SubElement(parent, "property", {"name": name})
    prop.text = value


def _frame_or_inherit(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} debe ser entero >= 0 o -1, recibido: {value!r}")
    if value < -1:
        raise ValueError(f"{name} debe ser >= 0 o -1, recibido: {value}")
    return value


# ---------------------------------------------------------------------------
# API publica: catalogo
# ---------------------------------------------------------------------------

def list_filters(kind: str = "") -> dict:
    """Lista filtros MLT reales (`melt -query filters`).

    kind filtra por prefijo (p.ej. frei0r/avfilter/ladspa/movit/composite);
    "" devuelve todos. Sin melt o salida vacía usa la lista curada.
    """
    if not isinstance(kind, str):
        raise TypeError(f"kind debe ser str, no {type(kind).__name__}")
    prefix = kind.strip()
    names = _query_names("filters")
    if not names:
        raise ValueError("melt no devolvio filtros (salida vacia)")
    if prefix:
        names = [n for n in names if n.startswith(prefix)]
    return {
        "filters": [{"name": n, "type": _service_type(n)} for n in names],
    }


def list_transitions() -> dict:
    """Lista transiciones MLT reales (`melt -query transitions`)."""
    names = _query_names("transitions")
    if not names:
        raise ValueError("melt no devolvio transiciones (salida vacia)")
    return {"transitions": [{"name": n} for n in names]}


def list_producers() -> dict:
    """Lista productores MLT reales (`melt -query producers`)."""
    names = _query_names("producers")
    if not names:
        raise ValueError("melt no devolvio productores (salida vacia)")
    return {"producers": [{"name": n} for n in names]}


def describe_filter(service: str) -> dict:
    """Describe un filtro (`melt -query filter=<service>`).

    Sin metadatos para el servicio retorna {service, params:[]} sin fallar.
    """
    if not isinstance(service, str) or not service.strip():
        raise ValueError("service no puede estar vacio")
    service = service.strip()

    def _load() -> dict:
        try:
            out = _run_melt(["-query", f"filter={service}"])
        except ValueError:
            return {"service": service,
                    "params": list(CURATED_PARAMS.get(service, []))}
        if out.lstrip().startswith("# No metadata"):
            return {"service": service, "params": []}
        params = _parse_describe_params(out)
        if not params and service in CURATED_PARAMS:
            return {"service": service,
                    "params": list(CURATED_PARAMS[service])}
        return {"service": service, "params": params}

    return _cached(f"describe:{service}", _load)


# ---------------------------------------------------------------------------
# API publica: aplicacion generica
# ---------------------------------------------------------------------------

def add_filter(
    project_path: str,
    track_id: str,
    clip_index: int,
    service: str,
    params: dict | None = None,
    in_frame: int = -1,
    out_frame: int = -1,
) -> dict:
    """Anade un filtro generico como <filter> hijo del <entry> del clip.

    service se valida contra list_filters (cache 1h); desconocido -> ValueError.
    in_frame/out_frame = -1 heredan el in/out del entry del clip.
    """
    if not isinstance(service, str) or not service.strip():
        raise ValueError("service no puede estar vacio")
    service = service.strip()
    known = {f["name"] for f in list_filters()["filters"]}
    if service not in known:
        raise ValueError(
            f"Servicio desconocido: {service!r} "
            f"(no esta en `melt -query filters`, {len(known)} servicios)"
        )
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise TypeError(f"params debe ser dict o None, no {type(params).__name__}")
    in_frame = _frame_or_inherit(in_frame, "in_frame")
    out_frame = _frame_or_inherit(out_frame, "out_frame")
    if in_frame != -1 and out_frame != -1 and out_frame <= in_frame:
        raise ValueError(
            f"out_frame ({out_frame}) debe ser > in_frame ({in_frame})"
        )

    tree, root, resolved = _load_root(project_path)
    entry = _get_entry(_find_playlist(root, track_id), clip_index)
    effect_id = _next_filter_id(root)

    attrs: dict[str, str] = {"id": effect_id, "mlt_service": service}
    attrs["in"] = str(in_frame) if in_frame != -1 else entry.get("in", "0")
    attrs["out"] = str(out_frame) if out_frame != -1 else entry.get("out", "0")
    filt = ET.SubElement(entry, "filter", attrs)
    _set_property(filt, "mlt_service", service)
    applied: dict[str, str] = {}
    for key, val in params.items():
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


def add_transition_by_service(
    project_path: str,
    service: str = "mix",
    a_track: int = 0,
    b_track: int = 1,
    always_active: bool = True,
) -> dict:
    """Crea una transicion hija del tractor con el servicio MLT dado.

    service se valida contra list_transitions (cache 1h).
    always_active=True omite in/out (activa en todo el timeline);
    False fija in=0/out=0 como rango minimo editable.
    """
    if not isinstance(service, str) or not service.strip():
        raise ValueError("service no puede estar vacio")
    service = service.strip()
    known = {t["name"] for t in list_transitions()["transitions"]}
    if service not in known:
        raise ValueError(
            f"Transicion desconocida: {service!r} "
            f"(no esta en `melt -query transitions`, {len(known)} servicios)"
        )
    for label, track in (("a_track", a_track), ("b_track", b_track)):
        if isinstance(track, bool) or not isinstance(track, int):
            raise ValueError(f"{label} debe ser entero >= 0, recibido: {track!r}")
        if track < 0:
            raise ValueError(f"{label} debe ser >= 0, recibido: {track}")

    tree, root, resolved = _load_root(project_path)
    tractor = root.find("tractor")
    if tractor is None:
        raise ValueError(f"Proyecto sin tractor: {project_path}")
    transition_id = _next_transition_id(root)
    trans = ET.SubElement(tractor, "transition", {"id": transition_id})
    _set_property(trans, "mlt_service", service)
    _set_property(trans, "a_track", str(a_track))
    _set_property(trans, "b_track", str(b_track))
    if not always_active:
        _set_property(trans, "in", "0")
        _set_property(trans, "out", "0")

    _save(tree, resolved)
    return {
        "transition_id": transition_id,
        "mlt_service": service,
        "a_track": a_track,
        "b_track": b_track,
        "always_active": bool(always_active),
    }
