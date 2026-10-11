# USAGE.md — kdenlive-mcp

## Arrancar el servidor

```bash
cd /home/cam/Projects/kdenlive-mcp
source .venv/bin/activate

kdenlive-mcp                      # entry point (pyproject: server:main)
# o bien:
PYTHONPATH=src python3 -m kdenlive_mcp.server
```

El servidor habla MCP por stdio (sin puerto ni HTTP). Para usarlo desde
un agente, conéctalo como servidor MCP externo.

## Configuración cliente MCP (ejemplo `claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "kdenlive": {
      "command": "/home/cam/Projects/kdenlive-mcp/.venv/bin/kdenlive-mcp"
    }
  }
}
```

Sin venv instalado, alternativa con `PYTHONPATH`:

```json
{
  "mcpServers": {
    "kdenlive": {
      "command": "python3",
      "args": ["-m", "kdenlive_mcp.server"],
      "cwd": "/home/cam/Projects/kdenlive-mcp",
      "env": { "PYTHONPATH": "src" }
    }
  }
}
```

## Ejemplos por área

Todas las tools retornan `dict` con `"ok": True/False` (y `"error"` si
falla). Rutas relativas se resuelven bajo el proyecto y las valida
`security.validate_path`.

### Proyecto (ProjectStore vía server.py:48-88)

```python
create_project(name="demo", width=1920, height=1080,
               fps_num=30, fps_den=1, profile="atsc_1080p_30")
# -> {"ok": True, "path": "projects/demo.kdenlive", ...}

open_project(path="projects/demo.kdenlive")
get_project_info(path="projects/demo.kdenlive")
inspect_project(path="projects/demo.kdenlive")   # estructura completa
save_project(path="projects/demo.kdenlive")
backup_project(path="projects/demo.kdenlive")    # copia de respaldo
```

### Medios (media_tools vía server.py:95-140)

```python
scan_media(folder="/home/cam/Videos")            # extensions="mp4,mov,mkv,mp3,wav,jpg,png"
import_media(project_path="projects/demo.kdenlive", media_path="/home/cam/Videos/clip.mp4")
analyze_media(media_path="/home/cam/Videos/clip.mp4")   # ffprobe: duración, video, audio
find_silence(media_path="media/demo_1.mp4", threshold_db=-35.0, min_duration=0.5)
extract_audio(video_path="media/demo_1.mp4")     # output_path="" → deriva nombre
generate_proxy(media_path="media/demo_1.mp4", width=640)
remove_media(project_path="projects/demo.kdenlive", producer_id="prod_1")
```

### Timeline (timeline_tools vía server.py:147-240)

```python
create_track(project_path="projects/demo.kdenlive", kind="video", name="")
add_clip(project_path="projects/demo.kdenlive", track_id="track_v1",
         media_path="/home/cam/Videos/clip.mp4",
         in_frame=0, out_frame=149, position=-1)   # position=-1 → al final
trim_clip(project_path=..., track_id="track_v1", clip_index=0, in_frame=0, out_frame=89)
split_clip(project_path=..., track_id="track_v1", clip_index=0, at_frame=45)
duplicate_clip(project_path=..., track_id="track_v1", clip_index=0)
move_clip(project_path=..., track_id="track_v1", clip_index=1, new_index=0)
remove_clip(project_path=..., track_id="track_v1", clip_index=0)
add_transition(project_path=..., track_id="track_v1", clip_index=0,
               transition_type="dissolve", duration_frames=30)
delete_track(project_path="projects/demo.kdenlive", track_id="track_v1")
```

### Efectos (effects_tools vía server.py:247-338)

```python
add_effect(project_path=..., track_id="track_v1", clip_index=0,
           effect="blur", params={"radius": "5"})
set_effect_parameter(project_path=..., track_id="track_v1", clip_index=0,
                     effect_id="filter_1", param="radius", value="8")
add_keyframe(project_path=..., track_id="track_v1", clip_index=0,
             effect_id="filter_1", frame=30, param="radius", value="10")
remove_effect(project_path=..., track_id="track_v1", clip_index=0, effect_id="filter_1")
# Atajos:
add_transform(project_path=..., track_id="track_v1", clip_index=0, scale=1.0, x=0, y=0)
add_blur(project_path=..., track_id="track_v1", clip_index=0, radius=5.0)
add_sharpen(project_path=..., track_id="track_v1", clip_index=0, amount=0.5)
```

### Audio (audio_tools vía server.py:345-408)

```python
set_volume(project_path=..., track_id="track_a1", clip_index=0, volume_db=0.0)
fade_in(project_path=..., track_id="track_a1", clip_index=0, duration_frames=30)
fade_out(project_path=..., track_id="track_a1", clip_index=0, duration_frames=30)
duck_music(project_path=..., voice_track_id="track_a1",
           music_track_id="track_a2", duck_db=-8.0)   # atenuación fija
normalize_audio(media_path="media/demo_1.mp4", target_i=-16.0)  # loudnorm EBU R128
remove_silence(media_path="media/demo_1.mp4", threshold_db=-35.0, min_duration=0.5)
```

### Subtítulos (subtitle_tools vía server.py:415-440)

```python
create_subtitles(project_path=..., track_id="track_v1",
                 texts=["Hola mundo", "Segundo rótulo"],
                 start_frames=[0, 90], end_frames=[89, 179],
                 output_srt="")                       # "" → deriva junto al proyecto
import_srt(project_path=..., track_id="track_v1", srt_path="subs.srt")
export_srt(project_path=..., output_path="output/subs.srt")
```

### Render (render_tools vía server.py:447-476)

```python
list_render_profiles()   # MP4-H264/AAC (default), MP4-H265, WebM-VP9, Preview
set_render_profile(project_path="projects/demo.kdenlive", profile_name="MP4-H264/AAC")
render_project(project_path="projects/demo.kdenlive",
               output_path="output/demo.mp4", profile_name="MP4-H264/AAC")
# -> {"ok": True, "job_id": "...", "output": ..., "status": "running"}
get_render_status(job_id="<id>")    # status/progress/log
cancel_render(job_id="<id>")        # termina el melt en curso
```

El render usa `melt proyecto in=0 out=<dur> -consumer avformat:<out>
<args-perfil>` en background; consulta el estado con polling de
`get_render_status`.

### QA (qa_tools vía server.py:483-524)

```python
validate_project(project_path="projects/demo.kdenlive")  # estructura MLT multitrack
check_missing_media(project_path="projects/demo.kdenlive")
check_timeline(project_path="projects/demo.kdenlive")
check_audio(project_path="projects/demo.kdenlive")
check_render(output_path="output/demo.mp4")               # inspecciona el MP4
generate_preview(project_path="projects/demo.kdenlive",
                 output_path="", width=640, duration_sec=10)
```

### `edit_video` completo (server.py:536, params reales)

```python
edit_video(
    source_folder="/home/cam/Videos",   # "" → usa/genera media/demo_*.mp4
    output="output/youtube_final.mp4",
    style="youtube",
    target_duration=60.0,
    subtitles=True,
    remove_silence=True,
    music="",               # ruta opcional a pista musical
    transitions=True,
    color=True,
    source_files=None,      # o lista explícita de rutas
    width=1920, height=1080, fps=30,
    project_name="youtube_edit",
)
# -> {"ok", "project_path", "output", "duration", "steps", "warnings"}
```

Flujo interno (`edit_video.py`): resuelve fuentes (`_resolve_sources`,
genera demos si faltan) → planifica duraciones (`_plan_durations`) →
crea proyecto + tracks → importa y monta clips → transiciones/color →
subtítulos (`_subtitle_slots`) → fija longitud del tractor
(`_finalize_tractor_length`) → render melt → QA (filtra falsos
positivos) → recorte a duración (`_trim_to_duration`).

### Narración TTS (ejemplo terremoto)

```python
edit_video(source_folder="/home/cam/Videos/sismo", output="output/terremoto.mp4",
    target_duration=60.0, project_name="terremoto",
    narration="Fuerte sismo sacudió la ciudad. Así se vivió en las calles.",
    voice="es_MX-claude")          # TTS → pista VOZ + duck auto + subs MLT
# -> {"ok": True, "project_path": "projects/terremoto.kdenlive", ...}
# Los subs van como filtro MLT (visibles en melt y en la GUI de Kdenlive).
# Solo para vídeos externos: burn_subtitles(video, srt, output)
check_render(output_path="output/terremoto.mp4")  # QA del entregable
```

### `edit_video` 60 s + preview (YouTube pro)

```python
result = edit_video(
    source_folder="/home/cam/Videos",
    output="output/youtube_pro_60s.mp4",
    target_duration=60.0,
    project_name="youtube_pro_60s",
    subtitles=True,
    remove_silence=True,
    transitions=True,
    color=True,
)
assert result["ok"]
print(result["duration"])   # -> ~60.04, h264+aac
print(result["warnings"])   # p. ej. aviso de subs clamped

# Preview sin render completo + QA del entregable:
generate_preview(project_path=result["project_path"],
                 output_path="output/youtube_pro_60s_preview.mp4",
                 width=640, duration_sec=10)
check_render(output_path=result["output"])
```
