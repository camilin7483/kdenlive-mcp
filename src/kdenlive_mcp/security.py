"""Seguridad de rutas para MCP Kdenlive.

Solo permite rutas dentro de ALLOWED_PREFIXES.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_SYSTMP = os.path.normpath(tempfile.gettempdir())

ALLOWED_PREFIXES = (
    "/home/cam/Projects/kdenlive-mcp",
    "/home/cam/Videos",
    "/home/cam/Music",
    "/tmp/opencode",
    _SYSTMP,
)

MAX_XML_BYTES = 50 * 1024 * 1024
MAX_SRT_BYTES = 2 * 1024 * 1024
MAX_SRT_ENTRIES = 2000
MAX_SCAN_FILES = 500
MAX_JOBS = 20

_BASE_DIR = Path("/home/cam/Projects/kdenlive-mcp")


def _resolve_normalized(p: str) -> Path:
    """Absoluta normalizada + symlinks resueltos (non-strict) y re-chequeo."""
    candidate = Path(p)
    if not candidate.is_absolute():
        candidate = _BASE_DIR / candidate
    normalized = Path(os.path.normpath(str(candidate)))
    if not normalized.is_absolute():
        raise ValueError(f"ruta no absoluta tras normalizar: {p!r}")
    try:
        resolved = normalized.resolve()
    except (OSError, RuntimeError):
        resolved = normalized
    return resolved


def _check_allowed(resolved: Path) -> None:
    sp = str(resolved)
    for prefix in ALLOWED_PREFIXES:
        nprefix = os.path.normpath(prefix)
        if sp == nprefix or sp.startswith(nprefix.rstrip(os.sep) + os.sep):
            return
    raise PermissionError(f"ruta fuera de zonas permitidas: {sp}")


def validate_path(p: str, must_exist: bool = False, allow_new: bool = True) -> Path:
    """Valida y resuelve una ruta.

    - Rechaza bytes nulos y cadenas vacías.
    - Las relativas se resuelven contra /home/cam/Projects/kdenlive-mcp.
    - Rechaza rutas fuera de ALLOWED_PREFIXES.
    - Si must_exist y no existe lanza FileNotFoundError.
    - Si not allow_new y existe lanza FileExistsError.
    """
    if not isinstance(p, str):
        raise TypeError(f"path debe ser str, no {type(p).__name__}")
    if not p or not p.strip():
        raise ValueError("ruta vacía no permitida")
    if "\x00" in p:
        raise ValueError("ruta con byte nulo no permitida")
    resolved = _resolve_normalized(p.strip())
    _check_allowed(resolved)
    exists = resolved.exists()
    if must_exist and not exists:
        raise FileNotFoundError(f"no existe: {resolved}")
    if not allow_new and exists:
        raise FileExistsError(f"ya existe: {resolved}")
    if exists and resolved.is_file():
        _check_file_size(resolved)
    return resolved


def validate_output_path(p: str) -> Path:
    """Como validate_path pero crea los directorios padre."""
    resolved = validate_path(p, must_exist=False, allow_new=True)
    parent = resolved.parent
    _check_allowed(parent)
    parent.mkdir(parents=True, exist_ok=True)
    return resolved


_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def safe_name(s: str) -> str:
    """Sanitiza una cadena para usarla como id MLT."""
    if not isinstance(s, str):
        s = str(s)
    s = s.strip()
    if not s:
        return "id"
    out = _SAFE_RE.sub("_", s)
    out = re.sub(r"_+", "_", out).strip("_")
    if not out:
        return "id"
    if out[0].isdigit():
        out = "_" + out
    return out[:64]


def _check_file_size(resolved: Path) -> None:
    """Topes anti-DoS según extensión (XML bombs / SRT gigantes)."""
    suf = resolved.suffix.lower()
    try:
        size = resolved.stat().st_size
    except OSError:
        return
    if suf in (".kdenlive", ".mlt", ".xml") and size > MAX_XML_BYTES:
        raise ValueError(f"proyecto demasiado grande (>50MB): {resolved}")
    if suf == ".srt" and size > MAX_SRT_BYTES:
        raise ValueError(f"SRT demasiado grande (>2MB): {resolved}")


def parse_xml_secure(path: str | Path) -> "ET.Element":
    """Parsea XML con tope de tamaño (mitiga XML bombs)."""
    import xml.etree.ElementTree as ET

    p = Path(path)
    try:
        if p.is_file() and p.stat().st_size > MAX_XML_BYTES:
            raise ValueError(f"XML demasiado grande: {p} (>50MB)")
    except OSError:
        pass
    try:
        tree = ET.parse(str(p))
    except ET.ParseError as e:
        raise ValueError(f"XML mal formado en {p}: {e}") from e
    return tree.getroot()
