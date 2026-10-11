# CHANGELOG.md — kdenlive-mcp

## 0.2.2 (2026-10-11)

- Fix off-by-one tractor/render: `out = dur - 1` en
  `render_tools.py:245` y `render_pro.py:482` (antes `out=dur` pedía
  un frame inexistente).
- `duck_db=-10.0` unificado (`duck_music`, `sidechain_duck`,
  `tts_duck_auto`).
- Subtítulos con clamp al timeline + aviso (`_clamp_to_timeline`,
  `"subtítulos recortados al timeline"`, flag `clamped`); `burn`
  H.264 + AAC; `align_srt_to_audio` recoloca a fronteras de voz.
- Wrappers de servidor completos: `add_clip` con
  `in_frame/out_frame/position`, `add_filter` con
  `in_frame/out_frame`, `add_transition_by_service` con
  `always_active=True`, `nest_sequence` con `position=-1`.
- Render: perfiles `MP4-1080p30` / `MP4-4K30` / `MP4-Vertical-Shorts`
  (+ variantes `*-VAAPI`) con fallback automático VAAPI → CPU
  (`fallback_from` en el status).
- Presets YouTube `limpio` / `cine` / `vivo` (`apply_preset`);
  catálogo MLT con fallback a listas curadas (`CURATED_*`).
- `edit_video` desacoplado (helpers `_resolve_sources`,
  `_plan_durations`, etc.) + `auto_chapters` + `get_thumbnail`.
- QA: `check_missing_media` bloqueante (bloquea render sin medios).
- Helpers importables (no tools MCP): `apply_preset`,
  `align_srt_to_audio`, `auto_chapters`, `get_thumbnail`,
  `cut_silence`, `sidechain_duck` (77 tools, verificado con
  `grep -c "@mcp.tool" src/kdenlive_mcp/server.py`).
- Tests: 69 passed (`pytest tests -q`).
- Entorno verificado: melt 7.40, ffmpeg n9.0.1, Python 3.14.7.

## 0.2.1 (2026-10-07)

Arreglo de las 4 limitaciones conocidas de 0.2.0:

- Subtítulos MLT visibles en melt: el filtro `subtitle` necesitaba
  `family/size/fgcolour/olcolour/outline/geometry` explícitos (sin
  ellos no dibuja texto). `edit_video` vuelve a confiar en MLT
  (una sola codificación, subs visibles en GUI); `burn_subtitles`
  queda para vídeos externos/estilos ASS.
- QSV: preflight real de codificación en `detect_hwaccel` (el encoder
  existe pero el driver falla con MFX -17); `qsv: False` honesto y
  `render_hw` QSV rechaza con mensaje accionable (usar VAAPI).
- Multicámara `method="xcorr"`: alineación por correlación FFT
  (scipy) a precisión de muestra, pico más cercano a cero ≥99% del
  máximo (robusto a señales periódicas), anclaje relativo al mínimo.
  Verificado: desfase 1.5 s → offset exacto de 45 frames en voz real.
- Segunda voz ES: `es_ES-sharvard` junto a `es_MX-claude` (XTTS sigue
  descartado en CPU).

## 0.2.0 (2026-10-07)

Servidor ampliado de 50 a 77 tools (`grep -c "@mcp.tool"`
`src/kdenlive_mcp/server.py`).

- TTS local (Piper, `tts_engine.py`): `tts_list_voices`, `tts_speak`
  (WAV 48 kHz con caché) y `tts_srt_to_narration` (SRT → pista
  temporizada con adelay+amix); voz por defecto `es_MX-claude`
  (`voices/es_MX-claude-high.onnx`, `pipx install piper-tts`).
- Narración en `edit_video` (`narration` texto o `.srt`, `voice`):
  guion → SRT temporizado con duraciones reales de TTS
  (`_script_to_srt`) → pista VOZ + duck automático (`tts_duck_auto`)
  + subtítulos desde la narración.
- Catálogo MLT real (`catalog_tools.py`): `list_filters`,
  `list_transitions`, `list_producers`, `describe_filter`,
  `add_filter`, `add_transition_by_service`.
- Render pro (`render_pro.py`): `detect_hwaccel`, `render_hw`
  (`HW_PROFILES`: `MP4-H264-VAAPI` default, `MP4-H264-QSV`,
  `MP4-H265-VAAPI`), `render_queue`, `render_range`.
- Audio pro (`audio_pro.py`): `loudness_measure`,
  `loudness_normalize`, `apply_compressor`, `split_stems`,
  `tts_duck_auto`, `mix_stems`.
- Timeline pro (`timeline_pro.py`): `add_marker`, `list_markers`,
  `remove_marker`, `ripple_delete`, `slip_clip`, `multicam_sync`,
  `nest_sequence`.
- `burn_subtitles` (`subtitle_tools.py`, estilo `YOUTUBE_SUB_STYLE`):
  quema el SRT con libass (H.264 CRF 20 + AAC).
- tts-studio (`tools/tts-studio/app.py`): app Gradio de pre-escucha
  (mismo backend `tts_engine`) en http://127.0.0.1:7860.
- Tests: `tests/test_tts.py` (voces, speak + caché, errores,
  `srt_to_narration`, `burn_subtitles`, `edit_video` con narración).

Nota (0.2.0, corregido en 0.2.1): el filtro MLT `subtitle` no mostraba
texto por falta de parámetros de fuente; desde 0.2.1 se escriben
`family/size/fgcolour/outline/geometry` explícitos y melt sí dibuja.
`burn_subtitles` queda para vídeos externos/estilos ASS.

## 0.1.1 (2026-10-07)

Correcciones de integración tras QA visual (el render con subtítulos en
pista salía negro: MLT compone la pista superior sin transitions y el
producer `subtitle` genera frames negros; la forma correcta es filtro).

- Subtítulos como `<filter mlt_service="subtitle">` sobre los clips
  (`subtitle_tools._register_subtitle`); `export_srt` lee filtros.
- `ensure_composite_transitions` (`kdenlive_project`): composites
  `frei0r.cairoblend` idempotentes; auto en `create_track` (`_save`),
  subtítulos y `render_project`.
- Duración = suma secuencial por playlist (`timeline_length_frames`
  compartido por `render_tools` y `qa_tools`); antes `max(out)` truncaba.
- `edit_video`: segmentos de máx. 8 s para forzar cortes/ritmo.
- Seguridad: allowlist sin `/home/cam` ni `/tmp` genérico (+tempdir del
  sistema), `resolve()` anti-symlink, topes XML/SRT/scan, `check_render`
  y `export_srt` validan rutas, jobs acotados (2 concurrentes, evicción,
  timeout 1800 s).
- Robustez: IDs únicos en split/duplicate, `extract_audio` con salida
  por defecto, `ok:true` en wrappers MCP, encoding utf-8 unificado,
  SRT con BOM/CRLF, `log.warning` en degradaciones.
- Vídeos finales regenerados y validados (sin negros, h264+aac,
  niveles coherentes): `output/youtube_final.mp4` (60 s, 15 MB,
  material real) y `output/youtube_demo_final.mp4` (20 s, sintético).

## 0.1.0 (2026-10-07)

Versión actual (`pyproject.toml`). Verificada con Kdenlive 26.08.0,
MLT/melt 7.40.0, ffmpeg n9.0.1, Python 3.14.7, mcp SDK 1.30.0.

### Implementado

- Servidor MCP FastMCP (`src/kdenlive_mcp/server.py`): 50 tools con
  wrappers `_call` → dict, `get_mcp()`, `main()` y entry point
  `kdenlive-mcp`.
- `ProjectStore` (`kdenlive_project.py`): create/open/save/inspect/info/
  backup de `.kdenlive` (XML MLT vía ET).
- Medios (`media_tools.py`): scan/import/remove/analyze/find_silence/
  extract_audio/generate_proxy (ffprobe + ffmpeg).
- Timeline (`timeline_tools.py`): tracks, add/remove/move/trim/split/
  duplicate clips, transiciones; tractor multitrack.
- Efectos (`effects_tools.py`): add/remove/param/keyframe +
  transform/blur/sharpen.
- Audio (`audio_tools.py`): volumen, fades, duck, normalize loudnorm,
  remove_silence.
- Subtítulos (`subtitle_tools.py`): create/import/export SRT.
- Render (`render_tools.py`): 4 perfiles (`MP4-H264/AAC` default,
  `MP4-H265`, `WebM-VP9`, `Preview`), jobs en background (`_JOBS`),
  status/cancel.
- QA (`qa_tools.py`): validate + missing_media + timeline + audio +
  check_render + generate_preview.
- Pipeline `edit_video` (`edit_video.py` + tool en server): fuentes →
  proyecto → efectos → subs → render → QA, con demos `testsrc2`+`sine`
  si no hay fuentes; retorna `{ok, project_path, output, duration,
  steps, warnings}` sin lanzar.
- Seguridad (`security.py`): `validate_path`, `validate_output_path`,
  `safe_name`; subprocess sin shell.

### Fixes

- QA multitrack: `validate_project` ya reconoce tractor/multitrack
  reales (antes falso "sin tracks"); `edit_video` filtra falsos
  positivos (`_filter_qa_false_positives`).
- Render in/out: `melt` acotado con `in=0 out=<duración del tractor>`
  (`_render_duration`); fija longitud del tractor
  (`_finalize_tractor_length`) y recorte a duración pedida
  (`_trim_to_duration`).
- `edit_video` expuesto como tool MCP en `server.py:536` (antes solo
  función importable).

### Limitaciones conocidas

- Render solo vía `melt` (sin Kdenlive headless); sin `melt` falla con
  mensaje.
- `duck_music` = atenuación fija, no sidechain.
- `find_silence` detecta, no corta; `remove_silence=True` informa.
- Efectos: `blur`, `sharpen`, `transform`, `color`, `brightness`,
  `volume`.
- Warnings `libsox_ng`/`librtaudio` de melt: opcionales, ignorables.
