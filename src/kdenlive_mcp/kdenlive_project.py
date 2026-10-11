"""Store de proyectos .kdenlive (XML MLT) — métodos puros, sin MCP."""
from __future__ import annotations

import logging
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from .models import ProjectMeta
from .security import safe_name, validate_output_path, validate_path

log = logging.getLogger(__name__)

MLT_VERSION = "7.40.0"
DEFAULT_BASE_DIR = "/home/cam/Projects/kdenlive-mcp/projects"

AUTO_COMPOSITE_ID = "auto_cairoblend"


def timeline_length_frames(root: ET.Element) -> int:
    """Duración del timeline en frames.

    Los clips de un playlist son secuenciales: su longitud es la SUMA
    de (out-in+1). Retorna el max entre playlists del multitrack y el
    out declarado del tractor. 0 si no hay contenido.
    """
    best = 0
    tids: set[str] = set()
    tractor = root.find("tractor")
    if tractor is not None:
        mt = tractor.find("multitrack")
        if mt is not None:
            tids = {t.get("producer", "") for t in mt.findall("track")}
        for attr in (tractor.get("out"),
                     tractor.findtext("property[@name='out']")):
            if attr is not None and attr.lstrip("-").isdigit():
                best = max(best, int(attr))
    for pl in root.findall("playlist"):
        pid = pl.get("id")
        if pid == "main_bin" or (tids and pid not in tids):
            continue
        total = 0
        for entry in pl.findall("entry"):
            try:
                ein = int(entry.get("in", "0"))
                eout = int(entry.get("out", "0"))
            except (TypeError, ValueError):
                continue
            if eout >= ein:
                total += eout - ein + 1
        best = max(best, total)
    return best


def ensure_composite_transitions(project_path: str) -> dict:
    """Garantiza composite frei0r.cairoblend para pistas de video superiores.

    Sin <transition> en el tractor, MLT muestra solo la pista superior
    (p.ej. subtítulos) y el render sale negro. Kdenlive genera estos
    composites; aquí se regeneran de forma idempotente (marcados con
    property kdenlive_id=auto_cairoblend). Retorna {added, tracks}.
    """
    path = validate_path(project_path)
    tree = ET.parse(str(path))
    root = tree.getroot()
    tractor = root.find("tractor")
    if tractor is None:
        raise ValueError(f"sin tractor: {project_path}")
    multitrack = tractor.find("multitrack")
    if multitrack is None:
        raise ValueError(f"sin multitrack: {project_path}")
    tracks = list(multitrack.findall("track"))
    for tr in list(tractor.findall("transition")):
        kid = tr.find("./property[@name='kdenlive_id']")
        if kid is not None and kid.text == AUTO_COMPOSITE_ID:
            tractor.remove(tr)
    existing = [t.get("id") for t in tractor.findall("transition")]
    n = 0
    for t in existing:
        try:
            num = int(str(t).replace("transition", ""))
            n = max(n, num + 1)
        except (ValueError, TypeError):
            continue
    added = 0
    for i, track in enumerate(tracks):
        if i == 0 or track.get("hide") == "video":
            continue
        tr = ET.SubElement(tractor, "transition", {"id": f"transition{n}"})
        n += 1
        for name, val in (
            ("mlt_service", "frei0r.cairoblend"),
            ("a_track", "0"),
            ("b_track", str(i)),
            ("version", "0.1"),
            ("kdenlive_id", AUTO_COMPOSITE_ID),
        ):
            p = ET.SubElement(tr, "property", {"name": name})
            p.text = val
        added += 1
    ET.indent(tree, space="  ")
    tree.write(str(path), encoding="utf-8", xml_declaration=True)
    return {"added": added, "tracks": len(tracks)}


def finalize_tractor(project_path: str) -> dict:
    """Fija in/out del tractor a la duracion real del timeline.

    timeline_tools no actualiza el tractor (queda out=0 de la plantilla),
    lo que deja el proyecto sin duracion declarada para Kdenlive GUI y QA.
    Idempotente; retorna {in, out, frames}.
    """
    path = validate_path(project_path)
    tree = ET.parse(str(path))
    root = tree.getroot()
    tractor = root.find("tractor")
    if tractor is None:
        raise ValueError(f"sin tractor: {project_path}")
    frames = timeline_length_frames(root)
    tractor.set("in", "0")
    tractor.set("out", str(max(0, frames - 1)))
    ET.indent(tree, space="  ")
    tree.write(str(path), encoding="utf-8", xml_declaration=True)
    return {"in": 0, "out": max(0, frames - 1), "frames": frames}


class ProjectStore:
    """Crea/lee/inspecciona proyectos .kdenlive (formato MLT)."""

    def __init__(self, base_dir: str = DEFAULT_BASE_DIR) -> None:
        self.base_dir: Path = validate_output_path(str(Path(base_dir) / ".keep")).parent

    # -- internos ------------------------------------------------------
    def _new_mlt_tree(self, meta: ProjectMeta) -> ET.Element:
        mlt = ET.Element(
            "mlt",
            {
                "profile": meta.profile,
                "version": MLT_VERSION,
                "LC_NUMERIC": "C",
                "title": meta.name,
            },
        )
        ET.SubElement(
            mlt,
            "profile",
            {
                "description": meta.profile,
                "width": str(meta.width),
                "height": str(meta.height),
                "progressive": "1",
                "display_aspect_num": "16",
                "display_aspect_den": "9",
                "frame_rate_num": str(meta.fps_num),
                "frame_rate_den": str(meta.fps_den),
                "colorspace": "709",
                "sample_aspect_num": "1",
                "sample_aspect_den": "1",
            },
        )
        main_bin = ET.SubElement(mlt, "playlist", {"id": "main_bin"})
        prop = ET.SubElement(main_bin, "property", {"name": "xml_retain"})
        prop.text = "1"

        ET.SubElement(mlt, "playlist", {"id": "playlist0"})

        tractor = ET.SubElement(
            mlt,
            "tractor",
            {
                "id": "tractor",
                "title": meta.name,
                "name": meta.name,
                "in": "0",
                "out": "0",
            },
        )
        prop = ET.SubElement(tractor, "property", {"name": "mlt_service"})
        prop.text = "tractor"
        multitrack = ET.SubElement(tractor, "multitrack")
        ET.SubElement(
            multitrack, "track", {"producer": "playlist0", "hide": "audio"}
        )
        return mlt

    def _write(self, path: Path, root: ET.Element) -> None:
        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ")
        tree.write(str(path), encoding="utf-8", xml_declaration=True)

    def _parse(self, path: Path) -> ET.Element:
        try:
            tree = ET.parse(str(path))
        except ET.ParseError as e:
            raise ValueError(f"XML mal formado en {path}: {e}") from e
        return tree.getroot()

    def _resolve_project(self, path: str, must_exist: bool = True) -> Path:
        return validate_path(path, must_exist=must_exist, allow_new=True)

    def _next_ids(self, root: ET.Element) -> tuple[str, str]:
        """Calcula próximos ids monotónicos desde el XML (persistidos en XML)."""
        max_prod = 0
        max_pl = 0
        for prod in root.findall("producer"):
            pid = prod.get("id", "")
            if pid.startswith("prod_"):
                try:
                    max_prod = max(max_prod, int(pid.split("_", 1)[1]))
                except ValueError:
                    pass
        for pl in root.findall("playlist"):
            pid = pl.get("id", "")
            if pid.startswith("playlist"):
                suffix = pid[len("playlist"):]
                if suffix.isdigit():
                    max_pl = max(max_pl, int(suffix))
        return f"prod_{max_prod + 1}", f"playlist{max_pl + 1}"

    # -- API -----------------------------------------------------------
    def create_project(
        self,
        name: str,
        width: int = 1920,
        height: int = 1080,
        fps_num: int = 30,
        fps_den: int = 1,
        profile: str = "atsc_1080p_30",
    ) -> dict:
        if not name or not name.strip():
            raise ValueError("name no vacío / name must not be empty")
        if width <= 0 or height <= 0:
            raise ValueError("width/height deben ser positivos")
        if fps_num <= 0 or fps_den <= 0:
            raise ValueError("fps_num/fps_den deben ser positivos")
        if not profile or not profile.strip():
            raise ValueError("profile no vacío")
        clean = name.strip()
        fname = safe_name(clean) + ".kdenlive"
        dest = validate_output_path(str(self.base_dir / fname))
        if dest.exists():
            # evitar colisión: sufijo monotónico
            stem = dest.stem
            i = 2
            while dest.exists():
                dest = validate_output_path(
                    str(self.base_dir / f"{stem}_{i}.kdenlive")
                )
                i += 1
        meta = ProjectMeta(
            name=clean,
            width=width,
            height=height,
            fps_num=fps_num,
            fps_den=fps_den,
            profile=profile.strip(),
            project_path=str(dest),
        )
        root = self._new_mlt_tree(meta)
        self._write(dest, root)
        log.info("proyecto creado: %s", dest)
        return {
            "project_path": str(dest),
            "name": meta.name,
            "width": meta.width,
            "height": meta.height,
            "fps_num": meta.fps_num,
            "fps_den": meta.fps_den,
            "profile": meta.profile,
        }

    def open_project(self, path: str) -> dict:
        rp = self._resolve_project(path, must_exist=True)
        root = self._parse(rp)
        if root.tag != "mlt":
            raise ValueError(f"no es proyecto MLT válido: raíz <{root.tag}>")
        info = self.get_project_info(str(rp))
        info["project_path"] = str(rp)
        return info

    def save_project(self, path: str) -> dict:
        rp = self._resolve_project(path, must_exist=True)
        root = self._parse(rp)
        if root.tag != "mlt":
            raise ValueError(f"no es proyecto MLT válido: raíz <{root.tag}>")
        self._write(rp, root)
        return {"project_path": str(rp), "saved": True}

    def inspect_project(self, path: str) -> dict:
        rp = self._resolve_project(path, must_exist=True)
        root = self._parse(rp)
        if root.tag != "mlt":
            raise ValueError(f"no es proyecto MLT válido: raíz <{root.tag}>")

        producers: list[dict] = []
        for prod in root.findall("producer"):
            props = {
                (p.get("name") or ""): (p.text or "")
                for p in prod.findall("property")
            }
            producers.append(
                {
                    "id": prod.get("id", ""),
                    "in": prod.get("in", ""),
                    "out": prod.get("out", ""),
                    "service": props.get("mlt_service", ""),
                    "resource": props.get("resource", ""),
                    "properties": props,
                }
            )

        playlists: list[dict] = []
        clips: list[dict] = []
        for pl in root.findall("playlist"):
            entries: list[dict] = []
            for e in pl.findall("entry"):
                entries.append(
                    {
                        "producer": e.get("producer", ""),
                        "in": e.get("in", ""),
                        "out": e.get("out", ""),
                    }
                )
                clips.append(
                    {
                        "playlist": pl.get("id", ""),
                        "producer": e.get("producer", ""),
                        "in": e.get("in", ""),
                        "out": e.get("out", ""),
                    }
                )
            playlists.append({"id": pl.get("id", ""), "entries": entries})

        effects: list[dict] = []
        for parent in list(root.iter()):
            for f in parent.findall("filter"):
                props = {
                    (p.get("name") or ""): (p.text or "")
                    for p in f.findall("property")
                }
                effects.append(
                    {
                        "parent": parent.tag,
                        "parent_id": parent.get("id", ""),
                        "service": props.get("mlt_service", ""),
                        "properties": props,
                    }
                )

        tractor_el = root.find("tractor")
        tractor = (
            {
                "id": tractor_el.get("id", ""),
                "title": tractor_el.get("title", ""),
                "in": tractor_el.get("in", ""),
                "out": tractor_el.get("out", ""),
            }
            if tractor_el is not None
            else {}
        )

        prof_el = root.find("profile")
        profile = dict(prof_el.attrib) if prof_el is not None else {}
        if not profile:
            profile = {"description": root.get("profile", "")}

        return {
            "project_path": str(rp),
            "name": tractor.get("title", root.get("title", "")),
            "profile": profile,
            "producers": producers,
            "playlists": playlists,
            "clips": clips,
            "effects": effects,
            "tractor": tractor,
        }

    def get_project_info(self, path: str) -> dict:
        rp = self._resolve_project(path, must_exist=True)
        root = self._parse(rp)
        tractor_el = root.find("tractor")
        prof_el = root.find("profile")
        n_producers = len(root.findall("producer"))
        n_playlists = len(root.findall("playlist"))
        n_entries = sum(len(pl.findall("entry")) for pl in root.findall("playlist"))
        return {
            "project_path": str(rp),
            "name": (
                tractor_el.get("title", "")
                if tractor_el is not None
                else root.get("title", "")
            ),
            "profile": (
                dict(prof_el.attrib)
                if prof_el is not None
                else {"description": root.get("profile", "")}
            ),
            "mlt_profile": root.get("profile", ""),
            "version": root.get("version", ""),
            "tractor_in": tractor_el.get("in", "") if tractor_el is not None else "",
            "tractor_out": tractor_el.get("out", "") if tractor_el is not None else "",
            "num_producers": n_producers,
            "num_playlists": n_playlists,
            "num_clips": n_entries,
        }

    def backup_project(self, path: str) -> dict:
        rp = self._resolve_project(path, must_exist=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_name = f"{rp.name}.bak-{ts}.kdenlive"
        dest = validate_output_path(str(rp.parent / backup_name))
        shutil.copy2(str(rp), str(dest))
        return {"backup_path": str(dest), "project_path": str(rp)}
