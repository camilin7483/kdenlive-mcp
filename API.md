# API.md — referencia de las 77 tools

Generada de firmas reales en `src/kdenlive_mcp/server.py` (2026-10-07).
Todas retornan `dict`; en éxito `{"ok": True, ...}`, en error
`{"ok": False, "error": "..."}` (vía `_call`, `server.py:27`).
`edit_video` además siempre incluye `project_path/output/duration/steps/
warnings`.

## Proyecto — `ProjectStore` (`kdenlive_project.py`)

| Tool (`server.py`) | Firma | Retorno |
|---|---|---|
| `create_project` (:49) | `(name: str, width=1920, height=1080, fps_num=30, fps_den=1, profile="atsc_1080p_30")` | `{ok, path, ...}` crea `projects/<name>.kdenlive` |
| `open_project` (:62) | `(path: str)` | `{ok, info...}` del proyecto existente |
| `save_project` (:68) | `(path: str)` | `{ok, path}` valida y confirma en disco |
| `inspect_project` (:74) | `(path: str)` | `{ok, structure...}` completa (producers, playlists, tractor) |
| `get_project_info` (:80) | `(path: str)` | `{ok, title, profile, width, height, fps...}` |
| `backup_project` (:86) | `(path: str)` | `{ok, backup_path}` copia de respaldo |

## Medios — `media_tools.py`

| Tool | Firma | Retorno |
|---|---|---|
| `scan_media` (:96) | `(folder: str, extensions="mp4,mov,mkv,mp3,wav,jpg,png")` | `{ok, files: [...]}` |
| `import_media` (:104) | `(project_path: str, media_path: str)` | `{ok, producer_id}` registra `<producer>` + entrada al bin |
| `remove_media` (:110) | `(project_path: str, producer_id: str)` | `{ok}` elimina medio y referencias |
| `analyze_media` (:116) | `(media_path: str)` | `{ok, duration, video, audio...}` vía ffprobe |
| `find_silence` (:122) | `(media_path: str, threshold_db=-35.0, min_duration=0.5)` | `{ok, silences: [{start, end}]}` solo detecta |
| `extract_audio` (:130) | `(video_path: str, output_path="")` | `{ok, output_path}` |
| `generate_proxy` (:136) | `(media_path: str, output_path="", width=640)` | `{ok, output_path}` proxy liviano |

## Timeline — `timeline_tools.py`

| Tool | Firma | Retorno |
|---|---|---|
| `create_track` (:148) | `(project_path: str, kind="video", name="")` | `{ok, track_id}` (`track_vN`/`track_aN`) |
| `delete_track` (:156) | `(project_path: str, track_id: str)` | `{ok}` |
| `add_clip` (:162) | `(project_path, track_id, media_path="", producer_id="", in_frame=0, out_frame=149, position=-1)` | `{ok, clip_index}`; `position=-1` = al final |
| `remove_clip` (:180) | `(project_path, track_id, clip_index: int)` | `{ok}` |
| `move_clip` (:186) | `(project_path, track_id, clip_index: int, new_index: int)` | `{ok}` mismo track |
| `trim_clip` (:196) | `(project_path, track_id, clip_index: int, in_frame=-1, out_frame=-1)` | `{ok}`; `-1` = no cambia ese extremo |
| `split_clip` (:211) | `(project_path, track_id, clip_index: int, at_frame: int)` | `{ok, new_index}` divide en dos |
| `duplicate_clip` (:221) | `(project_path, track_id, clip_index: int)` | `{ok, new_index}` justo después |
| `add_transition` (:229) | `(project_path, track_id, clip_index: int, transition_type="dissolve", duration_frames=30)` | `{ok}` |

## Efectos — `effects_tools.py`

| Tool | Firma | Retorno |
|---|---|---|
| `add_effect` (:248) | `(project_path, track_id, clip_index: int, effect: str, params: dict \| None=None)` | `{ok, effect_id}` (`filter_N`) |
| `remove_effect` (:263) | `(project_path, track_id, clip_index: int, effect_id: str)` | `{ok}` |
| `set_effect_parameter` (:273) | `(project_path, track_id, clip_index, effect_id, param: str, value: str)` | `{ok}` crea o actualiza `<property>` |
| `add_keyframe` (:289) | `(project_path, track_id, clip_index, effect_id, frame: int, param: str, value: str)` | `{ok}` animación del parámetro |
| `add_transform` (:306) | `(project_path, track_id, clip_index, scale=1.0, x=0, y=0)` | `{ok, effect_id}` escala + desplazamiento |
| `add_blur` (:322) | `(project_path, track_id, clip_index, radius=5.0)` | `{ok, effect_id}` desenfoque |
| `add_sharpen` (:332) | `(project_path, track_id, clip_index, amount=0.5)` | `{ok, effect_id}` enfoque |

## Audio — `audio_tools.py`

| Tool | Firma | Retorno |
|---|---|---|
| `set_volume` (:346) | `(project_path, track_id, clip_index: int, volume_db=0.0)` | `{ok}` ganancia dB del clip |
| `normalize_audio` (:356) | `(media_path: str, output_path="", target_i=-16.0)` | `{ok, output_path}` loudnorm EBU R128 |
| `fade_in` (:364) | `(project_path, track_id, clip_index: int, duration_frames=30)` | `{ok}` fundido de entrada |
| `fade_out` (:374) | `(project_path, track_id, clip_index: int, duration_frames=30)` | `{ok}` fundido de salida |
| `remove_silence` (:384) | `(media_path: str, output_path="", threshold_db=-35.0, min_duration=0.5)` | `{ok, output_path, removed...}` recorta con ffmpeg |
| `duck_music` (:398) | `(project_path, voice_track_id: str, music_track_id: str, duck_db=-8.0)` | `{ok}` atenuación fija (no sidechain) |

## Subtítulos — `subtitle_tools.py`

| Tool | Firma | Retorno |
|---|---|---|
| `create_subtitles` (:416) | `(project_path, track_id, texts: list[str], start_frames: list[int], end_frames: list[int], output_srt="")` | `{ok, srt_path, count}` aplica filtros `filter_sub_N` |
| `import_srt` (:432) | `(project_path, track_id: str, srt_path: str)` | `{ok, count}` importa SRT existente |
| `export_srt` (:438) | `(project_path: str, output_path: str)` | `{ok, output_path, count}` |
| `burn_subtitles` (:530) | `(video_path: str, srt_path: str, output_path="")` | `{ok, output, ...}` quema con libass (H.264 CRF 20 + AAC) |

## Render — `render_tools.py`

| Tool | Firma | Retorno |
|---|---|---|
| `list_render_profiles` (:448) | `()` | `{ok, profiles: [{name, args, extension}]}`: `MP4-H264/AAC`, `MP4-H265`, `WebM-VP9`, `Preview` |
| `set_render_profile` (:454) | `(project_path: str, profile_name: str)` | `{ok, profile, args}`; error si el nombre no existe |
| `render_project` (:460) | `(project_path: str, output_path="", profile_name="")` | `{ok, job_id, output, status: "running"}` lanza `melt ... in=0 out=<dur>` en background |
| `get_render_status` (:468) | `(job_id: str)` | `{ok, status, progress, log...}` (`running`/`done`/`error`/`cancelled`) |
| `cancel_render` (:474) | `(job_id: str)` | `{ok, status: "cancelled"}` termina el `melt` |

## QA — `qa_tools.py`

| Tool | Firma | Retorno |
|---|---|---|
| `validate_project` (:484) | `(project_path: str)` | `{ok, errors: [...], warnings: [...]}` estructura MLT multitrack |
| `check_missing_media` (:490) | `(project_path: str)` | `{ok, missing: [...]}` recursos ausentes en disco |
| `check_timeline` (:496) | `(project_path: str)` | `{ok, tracks, clips, issues...}` resumen + problemas |
| `check_audio` (:502) | `(project_path: str)` | `{ok, issues...}` chequeo de audio del timeline |
| `check_render` (:508) | `(output_path: str)` | `{ok, duration, streams...}` inspecciona el MP4 con ffprobe |
| `generate_preview` (:514) | `(project_path: str, output_path="", width=640, duration_sec=10)` | `{ok, output_path}` preview baja resolución |

## Pipeline — `edit_video.py`

| Tool | Firma | Retorno |
|---|---|---|
| `edit_video` (:805) | `(source_folder="", output="output/youtube_final.mp4", style="youtube", target_duration=60.0, subtitles=True, remove_silence=True, music="", transitions=True, color=True, source_files=None, width=1920, height=1080, fps=30, project_name="youtube_edit", narration="", voice="es_MX-claude")` | `{ok, project_path, output, duration, steps, warnings[, error]}`; nunca lanza; `narration` (texto o `.srt`) → pista VOZ + duck auto + subs |

## TTS — `tts_engine.py` (Piper local)

| Tool | Firma | Retorno |
|---|---|---|
| `tts_list_voices` (:545) | `()` | `{voices: [{name, model, installed, lang}], default}` |
| `tts_speak` (:551) | `(text: str, voice="es_MX-claude", speed=1.0, output="")` | `{output, duration, voice, cached}` WAV 48 kHz mono |
| `tts_srt_to_narration` (:562) | `(srt_path: str, voice="es_MX-claude", speed=1.0, output="", project_path="", track_id="")` | `{output, duration, blocks, voice[, clip]}` mezcla con adelay+amix |

## Catálogo MLT — `catalog_tools.py`

| Tool | Firma | Retorno |
|---|---|---|
| `list_filters` (:578) | `(kind="")` | `{ok, filters: [...]}` reales de MLT |
| `list_transitions` (:584) | `()` | `{ok, transitions: [...]}` |
| `list_producers` (:590) | `()` | `{ok, producers: [...]}` |
| `describe_filter` (:596) | `(service: str)` | `{ok, params...}` del servicio |
| `add_filter` (:602) | `(project_path, track_id, clip_index: int, service: str, params=None)` | `{ok, effect_id}` validado contra el catálogo |
| `add_transition_by_service` (:617) | `(project_path: str, service="mix", a_track=0, b_track=1)` | `{ok}` transición arbitraria en el tractor |

## Render HW / cola — `render_pro.py`

| Tool | Firma | Retorno |
|---|---|---|
| `detect_hwaccel` (:631) | `()` | `{ok, ...}` VAAPI/QSV disponibles |
| `render_hw` (:637) | `(project_path: str, output_path="", profile_name="MP4-H264-VAAPI")` | `{ok, ...}` render con GPU (`HW_PROFILES`) |
| `render_queue` (:649) | `(items: list[dict])` | `{ok, ...}` renders secuenciales |
| `render_range` (:655) | `(project_path: str, output_path="", profile_name="Preview", start_sec=0.0, duration_sec=10.0)` | `{ok, ...}` render de un tramo |

## Audio pro — `audio_pro.py`

| Tool | Firma | Retorno |
|---|---|---|
| `loudness_measure` (:670) | `(media_path: str)` | `{integrated_lufs, true_peak, lra...}` |
| `loudness_normalize` (:676) | `(media_path: str, output_path="", target_i=-14.0)` | `{ok, output}` |
| `apply_compressor` (:688) | `(media_path: str, output_path="", threshold_db=-20.0, ratio=3.0)` | `{ok, output}` |
| `split_stems` (:702) | `(project_path: str, voice_track_id: str, music_track_id: str, sfx_track_id="")` | `{ok, ...}` stems voz/música(/sfx) |
| `tts_duck_auto` (:716) | `(project_path: str, narration_track_id: str, music_track_id: str, duck_db=-10.0)` | `{ok}` ducking bajo narración |
| `mix_stems` (:730) | `(project_path: str, output_stem_mix="")` | `{ok, output...}` master de stems |

## Timeline pro — `timeline_pro.py`

| Tool | Firma | Retorno |
|---|---|---|
| `add_marker` (:736) | `(project_path: str, frame: int, text: str, color="red")` | `{ok, ...}` |
| `list_markers` (:746) | `(project_path: str)` | `{ok, markers: [...]}` |
| `remove_marker` (:752) | `(project_path: str, marker_id: int)` | `{ok}` |
| `ripple_delete` (:758) | `(project_path: str, track_id: str, clip_index: int)` | `{ok}` borra y cierra el hueco |
| `slip_clip` (:768) | `(project_path: str, track_id: str, clip_index: int, offset_frames: int)` | `{ok}` |
| `multicam_sync` (:780) | `(project_paths: list[str], output_project: str, method="peak")` | `{ok, ...}` |
| `nest_sequence` (:790) | `(parent_project: str, child_project: str, track_id: str)` | `{ok, ...}` |

Utilidades no-tools (importables): `get_mcp()` (`server.py:39`, para
tests), `ProjectStore` (`kdenlive_project.py:19`), `validate_path` /
`validate_output_path` / `safe_name` (`security.py`).

## Helpers importables (no son tools MCP, sin `@mcp.tool`)

77 tools verificadas (`grep -c "@mcp.tool" src/kdenlive_mcp/server.py`).
Estas 6 funciones viven en los módulos y se importan directo:

| Función | Firma | Retorno |
|---|---|---|
| `apply_preset` (`effects_tools.py:356`) | `(project_path: str, track_id: str, clip_index: int, preset="limpio")` (`limpio`/`cine`/`vivo` + alias) | `{ok, ...}` aplica preset + fundido por keyframes |
| `align_srt_to_audio` (`subtitle_tools.py:460`) | `(audio_path: str, srt_path: str, output_path="", max_shift_sec=0.5, fps=30)` | `{ok, output, ...}` SRT recolocado a fronteras de voz |
| `auto_chapters` (`timeline_pro.py:699`) | `(project_path: str, interval_sec=60.0, prefix="Cap")` | `{ok, chapters: [...]}` usa markers o crea equiespaciados |
| `get_thumbnail` (`timeline_pro.py:756`) | `(media_path: str, output_path="", at_sec=1.0, width=320)` | `{thumbnail, at_sec, width}` frame JPG best-effort |
| `cut_silence` (`audio_pro.py:348`) | `(media_path: str, output_path="", threshold_db=-35.0, min_duration=0.5)` | `{ok, output_path, ...}` corte real (`silenceremove`) |
| `sidechain_duck` (`audio_tools.py:385`) | `(voice_path: str, music_path: str, output_path="", duck_db=-10.0, threshold=0.02, ratio=8.0)` | `{output, duck_db, ...}` duck real (`sidechaincompress`) |
