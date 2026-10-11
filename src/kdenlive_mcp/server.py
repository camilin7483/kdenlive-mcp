"""Servidor MCP Kdenlive — wrappers FastMCP sobre la lógica pura.

Cada herramienta llama a su función pura correspondiente dentro de
try/except y retorna dict. En error retorna {"ok": False, "error": ...}
en vez de lanzar (la validación de tipos la hace FastMCP con el schema).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import audio_tools, effects_tools, media_tools, qa_tools, render_tools
from . import subtitle_tools, timeline_tools, tts_engine
from . import audio_pro, catalog_tools, render_pro, timeline_pro
from .edit_video import edit_video as _edit_video_impl
from .kdenlive_project import ProjectStore

log = logging.getLogger(__name__)

mcp = FastMCP("kdenlive-mcp")

_store = ProjectStore()


def _call(fn: Callable[..., dict], *args: Any, **kwargs: Any) -> dict:
    """Ejecuta la función pura y normaliza errores a dict."""
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — el error viaja en el dict
        log.warning("tool %s falló: %s", getattr(fn, "__name__", fn), exc)
        return {"ok": False, "error": str(exc)}
    if isinstance(result, dict):
        result.setdefault("ok", True)
        return result
    return {"ok": True, "result": result}


def get_mcp() -> FastMCP:
    """Retorna la instancia FastMCP (para tests)."""
    return mcp


# ---------------------------------------------------------------------------
# Proyecto
# ---------------------------------------------------------------------------

@mcp.tool()
def create_project(
    name: str,
    width: int = 1920,
    height: int = 1080,
    fps_num: int = 30,
    fps_den: int = 1,
    profile: str = "atsc_1080p_30",
) -> dict:
    """Crea un proyecto .kdenlive nuevo."""
    return _call(_store.create_project, name, width, height, fps_num, fps_den, profile)


@mcp.tool()
def open_project(path: str) -> dict:
    """Abre un proyecto existente y retorna su info."""
    return _call(_store.open_project, path)


@mcp.tool()
def save_project(path: str) -> dict:
    """Valida y confirma el proyecto en disco."""
    return _call(_store.save_project, path)


@mcp.tool()
def inspect_project(path: str) -> dict:
    """Inspecciona la estructura completa del proyecto."""
    return _call(_store.inspect_project, path)


@mcp.tool()
def get_project_info(path: str) -> dict:
    """Retorna metadatos básicos del proyecto."""
    return _call(_store.get_project_info, path)


@mcp.tool()
def backup_project(path: str) -> dict:
    """Crea una copia de respaldo del proyecto."""
    return _call(_store.backup_project, path)


# ---------------------------------------------------------------------------
# Medios
# ---------------------------------------------------------------------------

@mcp.tool()
def scan_media(
    folder: str, extensions: str = "mp4,mov,mkv,mp3,wav,jpg,png"
) -> dict:
    """Lista archivos multimedia de una carpeta."""
    return _call(media_tools.scan_media, folder, extensions)


@mcp.tool()
def import_media(project_path: str, media_path: str) -> dict:
    """Registra un medio en el proyecto."""
    return _call(media_tools.import_media, project_path, media_path)


@mcp.tool()
def remove_media(project_path: str, producer_id: str) -> dict:
    """Elimina un medio del proyecto y sus referencias."""
    return _call(media_tools.remove_media, project_path, producer_id)


@mcp.tool()
def analyze_media(media_path: str) -> dict:
    """Sondea un medio con ffprobe (duración, video, audio)."""
    return _call(media_tools.analyze_media, media_path)


@mcp.tool()
def find_silence(
    media_path: str, threshold_db: float = -35.0, min_duration: float = 0.5
) -> dict:
    """Detecta tramos de silencio en un medio."""
    return _call(media_tools.find_silence, media_path, threshold_db, min_duration)


@mcp.tool()
def extract_audio(video_path: str, output_path: str = "") -> dict:
    """Extrae el audio de un video."""
    return _call(media_tools.extract_audio, video_path, output_path)


@mcp.tool()
def generate_proxy(
    media_path: str, output_path: str = "", width: int = 640
) -> dict:
    """Genera un proxy liviano del medio."""
    return _call(media_tools.generate_proxy, media_path, output_path, width)


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

@mcp.tool()
def create_track(
    project_path: str, kind: str = "video", name: str = ""
) -> dict:
    """Crea un track de video o audio."""
    return _call(timeline_tools.create_track, project_path, kind, name)


@mcp.tool()
def delete_track(project_path: str, track_id: str) -> dict:
    """Elimina un track del proyecto."""
    return _call(timeline_tools.delete_track, project_path, track_id)


@mcp.tool()
def add_clip(
    project_path: str,
    track_id: str,
    media_path: str = "",
    producer_id: str = "",
    in_frame: int = 0,
    out_frame: int = 149,
    position: int = -1,
) -> dict:
    """Añade un clip al track."""
    return _call(
        timeline_tools.add_clip,
        project_path, track_id, media_path, producer_id,
        in_frame, out_frame, position,
    )


@mcp.tool()
def remove_clip(project_path: str, track_id: str, clip_index: int) -> dict:
    """Elimina un clip del track."""
    return _call(timeline_tools.remove_clip, project_path, track_id, clip_index)


@mcp.tool()
def move_clip(
    project_path: str, track_id: str, clip_index: int, new_index: int
) -> dict:
    """Mueve un clip a otra posición del mismo track."""
    return _call(
        timeline_tools.move_clip, project_path, track_id, clip_index, new_index
    )


@mcp.tool()
def trim_clip(
    project_path: str,
    track_id: str,
    clip_index: int,
    in_frame: int = -1,
    out_frame: int = -1,
) -> dict:
    """Ajusta los puntos in/out de un clip."""
    return _call(
        timeline_tools.trim_clip,
        project_path, track_id, clip_index, in_frame, out_frame,
    )


@mcp.tool()
def split_clip(
    project_path: str, track_id: str, clip_index: int, at_frame: int
) -> dict:
    """Divide un clip en dos en el frame indicado."""
    return _call(
        timeline_tools.split_clip, project_path, track_id, clip_index, at_frame
    )


@mcp.tool()
def duplicate_clip(project_path: str, track_id: str, clip_index: int) -> dict:
    """Duplica un clip justo después del original."""
    return _call(
        timeline_tools.duplicate_clip, project_path, track_id, clip_index
    )


@mcp.tool()
def add_transition(
    project_path: str,
    track_id: str,
    clip_index: int,
    transition_type: str = "dissolve",
    duration_frames: int = 30,
) -> dict:
    """Añade una transición al clip."""
    return _call(
        timeline_tools.add_transition,
        project_path, track_id, clip_index, transition_type, duration_frames,
    )


# ---------------------------------------------------------------------------
# Efectos
# ---------------------------------------------------------------------------

@mcp.tool()
def add_effect(
    project_path: str,
    track_id: str,
    clip_index: int,
    effect: str,
    params: dict | None = None,
) -> dict:
    """Añade un efecto al clip."""
    return _call(
        effects_tools.add_effect,
        project_path, track_id, clip_index, effect, params,
    )


@mcp.tool()
def remove_effect(
    project_path: str, track_id: str, clip_index: int, effect_id: str
) -> dict:
    """Elimina un efecto del clip."""
    return _call(
        effects_tools.remove_effect, project_path, track_id, clip_index, effect_id
    )


@mcp.tool()
def set_effect_parameter(
    project_path: str,
    track_id: str,
    clip_index: int,
    effect_id: str,
    param: str,
    value: str,
) -> dict:
    """Crea o actualiza un parámetro de un efecto."""
    return _call(
        effects_tools.set_effect_parameter,
        project_path, track_id, clip_index, effect_id, param, value,
    )


@mcp.tool()
def add_keyframe(
    project_path: str,
    track_id: str,
    clip_index: int,
    effect_id: str,
    frame: int,
    param: str,
    value: str,
) -> dict:
    """Añade un keyframe a un parámetro de un efecto."""
    return _call(
        effects_tools.add_keyframe,
        project_path, track_id, clip_index, effect_id, frame, param, value,
    )


@mcp.tool()
def add_transform(
    project_path: str,
    track_id: str,
    clip_index: int,
    scale: float = 1.0,
    x: int = 0,
    y: int = 0,
) -> dict:
    """Añade un efecto de transformación (escala + desplazamiento)."""
    return _call(
        effects_tools.add_transform,
        project_path, track_id, clip_index, scale, x, y,
    )


@mcp.tool()
def add_blur(
    project_path: str, track_id: str, clip_index: int, radius: float = 5.0
) -> dict:
    """Añade desenfoque al clip."""
    return _call(
        effects_tools.add_blur, project_path, track_id, clip_index, radius
    )


@mcp.tool()
def add_sharpen(
    project_path: str, track_id: str, clip_index: int, amount: float = 0.5
) -> dict:
    """Añade enfoque al clip."""
    return _call(
        effects_tools.add_sharpen, project_path, track_id, clip_index, amount
    )


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

@mcp.tool()
def set_volume(
    project_path: str, track_id: str, clip_index: int, volume_db: float = 0.0
) -> dict:
    """Fija la ganancia (dB) de un clip."""
    return _call(
        audio_tools.set_volume, project_path, track_id, clip_index, volume_db
    )


@mcp.tool()
def normalize_audio(
    media_path: str, output_path: str = "", target_i: float = -16.0
) -> dict:
    """Normaliza el audio con loudnorm (EBU R128)."""
    return _call(audio_tools.normalize_audio, media_path, output_path, target_i)


@mcp.tool()
def fade_in(
    project_path: str, track_id: str, clip_index: int, duration_frames: int = 30
) -> dict:
    """Aplica fundido de entrada al clip."""
    return _call(
        audio_tools.fade_in, project_path, track_id, clip_index, duration_frames
    )


@mcp.tool()
def fade_out(
    project_path: str, track_id: str, clip_index: int, duration_frames: int = 30
) -> dict:
    """Aplica fundido de salida al clip."""
    return _call(
        audio_tools.fade_out, project_path, track_id, clip_index, duration_frames
    )


@mcp.tool()
def remove_silence(
    media_path: str,
    output_path: str = "",
    threshold_db: float = -35.0,
    min_duration: float = 0.5,
) -> dict:
    """Quita los silencios de un medio."""
    return _call(
        audio_tools.remove_silence,
        media_path, output_path, threshold_db, min_duration,
    )


@mcp.tool()
def duck_music(
    project_path: str,
    voice_track_id: str,
    music_track_id: str,
    duck_db: float = -10.0,
) -> dict:
    """Atenúa la música para dejar paso a la voz."""
    return _call(
        audio_tools.duck_music,
        project_path, voice_track_id, music_track_id, duck_db,
    )


# ---------------------------------------------------------------------------
# Subtítulos
# ---------------------------------------------------------------------------

@mcp.tool()
def create_subtitles(
    project_path: str,
    track_id: str,
    texts: list[str],
    start_frames: list[int],
    end_frames: list[int],
    output_srt: str = "",
) -> dict:
    """Crea subtítulos y los registra en el proyecto."""
    return _call(
        subtitle_tools.create_subtitles,
        project_path, track_id, texts, start_frames, end_frames, output_srt,
    )


@mcp.tool()
def import_srt(project_path: str, track_id: str, srt_path: str) -> dict:
    """Importa un SRT existente al proyecto."""
    return _call(subtitle_tools.import_srt, project_path, track_id, srt_path)


@mcp.tool()
def export_srt(project_path: str, output_path: str) -> dict:
    """Exporta los subtítulos del proyecto a un SRT."""
    return _call(subtitle_tools.export_srt, project_path, output_path)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

@mcp.tool()
def list_render_profiles() -> dict:
    """Lista los perfiles de render disponibles."""
    return _call(render_tools.list_render_profiles)


@mcp.tool()
def set_render_profile(project_path: str, profile_name: str) -> dict:
    """Guarda el perfil de render en el proyecto."""
    return _call(render_tools.set_render_profile, project_path, profile_name)


@mcp.tool()
def render_project(
    project_path: str, output_path: str = "", profile_name: str = ""
) -> dict:
    """Lanza el render del proyecto en background."""
    return _call(render_tools.render_project, project_path, output_path, profile_name)


@mcp.tool()
def get_render_status(job_id: str) -> dict:
    """Consulta el estado de un job de render."""
    return _call(render_tools.get_render_status, job_id)


@mcp.tool()
def cancel_render(job_id: str) -> dict:
    """Cancela un job de render en curso."""
    return _call(render_tools.cancel_render, job_id)


# ---------------------------------------------------------------------------
# QA
# ---------------------------------------------------------------------------

@mcp.tool()
def validate_project(project_path: str) -> dict:
    """Valida la estructura MLT del proyecto."""
    return _call(qa_tools.validate_project, project_path)


@mcp.tool()
def check_missing_media(project_path: str) -> dict:
    """Detecta medios referenciados que faltan en disco."""
    return _call(qa_tools.check_missing_media, project_path)


@mcp.tool()
def check_timeline(project_path: str) -> dict:
    """Resume tracks/clips y detecta problemas de timeline."""
    return _call(qa_tools.check_timeline, project_path)


@mcp.tool()
def check_audio(project_path: str) -> dict:
    """Chequea el audio del timeline."""
    return _call(qa_tools.check_audio, project_path)


@mcp.tool()
def check_render(output_path: str) -> dict:
    """Inspecciona un archivo renderizado."""
    return _call(qa_tools.check_render, output_path)


@mcp.tool()
def generate_preview(
    project_path: str,
    output_path: str = "",
    width: int = 640,
    duration_sec: int = 10,
) -> dict:
    """Renderiza un preview rápido a baja resolución."""
    return _call(
        qa_tools.generate_preview,
        project_path, output_path, width, duration_sec,
    )


@mcp.tool()
def burn_subtitles(
    video_path: str, srt_path: str, output_path: str = "",
) -> dict:
    """Quema SRT en el vídeo (libass, estilo YouTube)."""
    return _call(
        subtitle_tools.burn_subtitles, video_path, srt_path, output_path,
    )


def main() -> None:
    """Punto de entrada: ejecuta el servidor MCP."""
    mcp.run()


@mcp.tool()
def tts_list_voices() -> dict:
    """Lista voces TTS instaladas (español)."""
    return _call(tts_engine.list_voices)


@mcp.tool()
def tts_speak(
    text: str,
    voice: str = "es_MX-claude",
    speed: float = 1.0,
    output: str = "",
) -> dict:
    """Texto → voz español (WAV 48 kHz)."""
    return _call(tts_engine.speak, text, voice, speed, output)


@mcp.tool()
def tts_srt_to_narration(
    srt_path: str,
    voice: str = "es_MX-claude",
    speed: float = 1.0,
    output: str = "",
    project_path: str = "",
    track_id: str = "",
) -> dict:
    """SRT → pista de narración temporizada (opcional: al timeline)."""
    return _call(
        tts_engine.srt_to_narration, srt_path, voice, speed,
        output, project_path, track_id,
    )


@mcp.tool()
def list_filters(kind: str = "") -> dict:
    """Catálogo real de filtros MLT (frei0r/avfilter/ladspa...)."""
    return _call(catalog_tools.list_filters, kind)


@mcp.tool()
def list_transitions() -> dict:
    """Catálogo real de transiciones MLT."""
    return _call(catalog_tools.list_transitions)


@mcp.tool()
def list_producers() -> dict:
    """Catálogo real de productores MLT."""
    return _call(catalog_tools.list_producers)


@mcp.tool()
def describe_filter(service: str) -> dict:
    """Parámetros de un filtro MLT."""
    return _call(catalog_tools.describe_filter, service)


@mcp.tool()
def add_filter(
    project_path: str,
    track_id: str,
    clip_index: int,
    service: str,
    params: dict | None = None,
    in_frame: int = -1,
    out_frame: int = -1,
) -> dict:
    """Añade cualquier filtro MLT validado contra el catálogo."""
    return _call(
        catalog_tools.add_filter, project_path, track_id,
        clip_index, service, params, in_frame, out_frame,
    )


@mcp.tool()
def add_transition_by_service(
    project_path: str,
    service: str = "mix",
    a_track: int = 0,
    b_track: int = 1,
    always_active: bool = True,
) -> dict:
    """Transición MLT arbitraria en el tractor."""
    return _call(
        catalog_tools.add_transition_by_service, project_path,
        service, a_track, b_track, always_active,
    )


@mcp.tool()
def detect_hwaccel() -> dict:
    """Detecta aceleración HW (VAAPI/QSV)."""
    return _call(render_pro.detect_hwaccel)


@mcp.tool()
def render_hw(
    project_path: str,
    output_path: str = "",
    profile_name: str = "MP4-H264-VAAPI",
) -> dict:
    """Render con aceleración hardware."""
    return _call(
        render_pro.render_hw, project_path, output_path, profile_name,
    )


@mcp.tool()
def render_queue(items: list[dict]) -> dict:
    """Cola de renders secuenciales."""
    return _call(render_pro.render_queue, items)


@mcp.tool()
def render_range(
    project_path: str,
    output_path: str = "",
    profile_name: str = "Preview",
    start_sec: float = 0.0,
    duration_sec: float = 10.0,
) -> dict:
    """Render solo de un rango temporal."""
    return _call(
        render_pro.render_range, project_path, output_path,
        profile_name, start_sec, duration_sec,
    )


@mcp.tool()
def loudness_measure(media_path: str) -> dict:
    """Mide LUFS/true peak/LRA."""
    return _call(audio_pro.loudness_measure, media_path)


@mcp.tool()
def loudness_normalize(
    media_path: str,
    output_path: str = "",
    target_i: float = -16.0,
) -> dict:
    """Normaliza a LUFS objetivo (YouTube: -16)."""
    return _call(
        audio_pro.loudness_normalize, media_path, output_path, target_i,
    )


@mcp.tool()
def apply_compressor(
    media_path: str,
    output_path: str = "",
    threshold_db: float = -20.0,
    ratio: float = 3.0,
) -> dict:
    """Compresor de rango dinámico."""
    return _call(
        audio_pro.apply_compressor, media_path, output_path,
        threshold_db, ratio,
    )


@mcp.tool()
def split_stems(
    project_path: str,
    voice_track_id: str,
    music_track_id: str,
    sfx_track_id: str = "",
) -> dict:
    """Marca roles voz/música/efectos en tracks."""
    return _call(
        audio_pro.split_stems, project_path, voice_track_id,
        music_track_id, sfx_track_id,
    )


@mcp.tool()
def tts_duck_auto(
    project_path: str,
    narration_track_id: str,
    music_track_id: str,
    duck_db: float = -10.0,
    fade_frames: int = 15,
    use_sidechain: bool = False,
    voice_media: str = "",
    music_media: str = "",
    output_path: str = "",
) -> dict:
    """Ducking automático de música bajo narración (sidechain opt-in)."""
    return _call(
        audio_pro.tts_duck_auto, project_path, narration_track_id,
        music_track_id, duck_db, fade_frames,
        voice_media, music_media, output_path, use_sidechain,
    )


@mcp.tool()
def mix_stems(project_path: str, output_stem_mix: str = "") -> dict:
    """Mezcla solo-audio del proyecto a WAV 48 kHz."""
    return _call(audio_pro.mix_stems, project_path, output_stem_mix)


@mcp.tool()
def add_marker(
    project_path: str, frame: int, text: str, color: str = "red",
) -> dict:
    """Añade marcador al tractor."""
    return _call(
        timeline_pro.add_marker, project_path, frame, text, color,
    )


@mcp.tool()
def list_markers(project_path: str) -> dict:
    """Lista marcadores."""
    return _call(timeline_pro.list_markers, project_path)


@mcp.tool()
def remove_marker(project_path: str, marker_id: int) -> dict:
    """Elimina un marcador."""
    return _call(timeline_pro.remove_marker, project_path, marker_id)


@mcp.tool()
def ripple_delete(
    project_path: str, track_id: str, clip_index: int,
) -> dict:
    """Elimina clip y cierra el hueco."""
    return _call(
        timeline_pro.ripple_delete, project_path, track_id, clip_index,
    )


@mcp.tool()
def slip_clip(
    project_path: str, track_id: str, clip_index: int,
    offset_frames: int,
) -> dict:
    """Desplaza la ventana in/out dentro del medio."""
    return _call(
        timeline_pro.slip_clip, project_path, track_id,
        clip_index, offset_frames,
    )


@mcp.tool()
def multicam_sync(
    project_paths: list[str], output_project: str, method: str = "peak",
) -> dict:
    """Sincroniza clips por audio en proyecto multicámara (peak|xcorr)."""
    return _call(
        timeline_pro.multicam_sync, project_paths, output_project, method,
    )


@mcp.tool()
def nest_sequence(
    parent_project: str, child_project: str, track_id: str,
    position: int = -1,
) -> dict:
    """Anida una secuencia .mlt como clip."""
    return _call(
        timeline_pro.nest_sequence, parent_project, child_project,
        track_id, position,
    )


# ---------------------------------------------------------------------------
# Edición automática (pipeline completo)
# ---------------------------------------------------------------------------

@mcp.tool()
def edit_video(
    source_folder: str = "",
    output: str = "output/youtube_final.mp4",
    style: str = "youtube",
    target_duration: float = 60.0,
    subtitles: bool = True,
    remove_silence: bool = True,
    music: str = "",
    transitions: bool = True,
    color: bool = True,
    source_files: list[str] | None = None,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    project_name: str = "youtube_edit",
    narration: str = "",
    voice: str = "es_MX-claude",
) -> dict:
    """Pipeline completo de edición automática de video."""
    try:
        return _edit_video_impl(
            source_folder=source_folder,
            source_files=source_files,
            output=output,
            style=style,
            target_duration=target_duration,
            subtitles=subtitles,
            remove_silence=remove_silence,
            music=music,
            transitions=transitions,
            color=color,
            width=width,
            height=height,
            fps=fps,
            project_name=project_name,
            narration=narration,
            voice=voice,
        )
    except Exception as exc:  # noqa: BLE001 — el error viaja en el dict
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    main()
