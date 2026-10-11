# kdenlive-mcp

Pipeline de edición automática de vídeo sobre proyectos Kdenlive/MLT
(`.kdenlive` = XML MLT) expuesto como servidor MCP. Solo stdlib +
`ffmpeg`/`ffprobe`/`melt` del sistema.

## Qué es

Servidor MCP (**FastMCP**, objeto `mcp` en `src/kdenlive_mcp/server.py`,
77 tools) que manipula proyectos `.kdenlive` como XML MLT con
`xml.etree.ElementTree`, analiza medios con `ffprobe`, renderiza con
`melt -consumer avformat:` y valida con chequeos QA. Incluye el pipeline
`edit_video` de una llamada: fuentes → proyecto → efectos → subtítulos →
render.

## Arquitectura (3 líneas)

1. **MCP Server** (`server.py`): 77 wrappers `@mcp.tool()` que delegan a
   funciones puras y normalizan errores a `{"ok": False, "error": ...}`.
2. **Lógica pura** (`kdenlive_project.py`, `media/timeline/effects/audio/
   subtitle_tools.py`, `edit_video.py`): lee/escribe el XML MLT con ET,
   invoca `ffmpeg`/`ffprobe` por `subprocess` sin shell.
3. **Render + QA** (`render_tools.py`, `qa_tools.py`): `melt` en thread
   background (`_JOBS`); validación multitrack del XML y del MP4 final.

Detalle: ver [ARCHITECTURE.md](ARCHITECTURE.md).

## Requisitos (versiones verificadas 2026-10-07, Arch Linux)

| Componente | Versión verificada |
|---|---|
| Kdenlive | 26.08.0 (`kdenlive --version`) |
| MLT / melt | 7.40.0 (`melt -version`) |
| ffmpeg | n9.0.1 (`ffmpeg -version`) |
| Python | 3.14.7 (`python3 --version`) |
| mcp (SDK) | 1.30.0 (`pip show mcp`) |
| Paquete | 0.1.0 (`pyproject.toml`) |

Sistema: `sudo pacman -S --needed kdenlive mlt ffmpeg python python-pip`
(requiere `ffmpeg`, que trae `ffprobe`). Python >= 3.10 según
`pyproject.toml`. Detalle: ver [INSTALL.md](INSTALL.md).

## Instalación

```bash
cd /home/cam/Projects/kdenlive-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

Esto instala el entry point `kdenlive-mcp = kdenlive_mcp.server:main`
(ver `pyproject.toml`). Reproducible paso a paso: ver
[INSTALL.md](INSTALL.md).

## Arranque

```bash
# Opción A: entry point (tras pip install -e .)
kdenlive-mcp   # equivale a: python -m kdenlive_mcp.server

# Opción B: directa con PYTHONPATH (sin instalar)
PYTHONPATH=src python3 -m kdenlive_mcp.server
```

El servidor expone las 77 tools vía stdio (protocolo MCP); conéctalo
desde tu cliente MCP (ejemplo JSON en [USAGE.md](USAGE.md)).

## Ejemplo mínimo

```python
from kdenlive_mcp.kdenlive_project import ProjectStore

store = ProjectStore()
print(store.create_project("demo"))          # -> {"ok": True, ...}
print(store.get_project_info("projects/demo.kdenlive"))
```

## Ejemplo `edit_video` (una llamada, YouTube)

```python
from kdenlive_mcp.edit_video import edit_video

result = edit_video(
    source_folder="/home/cam/Videos",
    output="output/youtube_final.mp4",
    target_duration=60.0,
)
print(result["ok"], result["output"], result["duration"])
```

Firma real (`src/kdenlive_mcp/edit_video.py:326`):

```python
def edit_video(source_folder="", source_files=None, output="output/youtube_final.mp4",
               style="youtube", target_duration=60.0, subtitles=True,
               remove_silence=True, music="", transitions=True, color=True,
               width=1920, height=1080, fps=30, project_name="youtube_edit",
               narration="", voice="es_MX-claude") -> dict
```

Retorna siempre `{"ok", "project_path", "output", "duration", "steps",
"warnings"}` (+ `"error"` si `ok` es False); nunca lanza.
`narration` acepta texto libre o ruta a `.srt`: genera la pista VOZ
con TTS, ducking automático y subtítulos desde la narración.

Retorna siempre `{"ok", "project_path", "output", "duration", "steps",
"warnings"}` (+ `"error"` si `ok` es False); nunca lanza.

Sin fuentes no falla: genera material demo (`media/demo_*.mp4` con
`testsrc2` + `sine`) y edita sobre él.

## TTS y narración (Piper local, español)

Voces ES: `es_MX-claude` (default) y `es_ES-sharvard` (`voices/`).
Instala el binario con `pipx install piper-tts` (el motor lo busca en
`PATH` o `~/.local/bin/piper`); sin piper las tools TTS fallan con
mensaje.

```python
from kdenlive_mcp.tts_engine import list_voices, speak

print(list_voices()["default"])          # -> "es_MX-claude"
print(speak("Hola mundo")["output"])     # -> WAV 48 kHz (con caché)
```

Narración directa en el pipeline (texto o `.srt` → pista VOZ +
duck auto + subs):

```python
from kdenlive_mcp.edit_video import edit_video

result = edit_video(
    source_folder="/home/cam/Videos",
    output="output/youtube_narrado.mp4",
    target_duration=60.0,
    narration="Hola. Esto es una prueba.",
    voice="es_MX-claude",
)
```

### tts-studio (pre-escucha en navegador)

App Gradio en `tools/tts-studio/app.py`: lo que se pre-escucha es lo
que sale en el vídeo (mismo `tts_engine`).

```bash
cd /home/cam/Projects/kdenlive-mcp/tools/tts-studio
python3 -m venv .venv \
  && .venv/bin/pip install -r requirements.txt \
  && .venv/bin/python app.py
```

Abre http://127.0.0.1:7860 (pestañas Texto → Voz y SRT → Narración).

## Dónde sale el vídeo

- Proyecto: `projects/<nombre>.kdenlive`
- Render: la ruta `output` pedida (relativa = bajo el proyecto).
- El dict retornado trae `project_path`, `output`, `duration`, `steps` y `warnings`.

## Estructura

```text
src/kdenlive_mcp/
  server.py            # 77 tools @mcp.tool() + main() + get_mcp()
  kdenlive_project.py  # ProjectStore: create/open/save/inspect/info/backup
  media_tools.py       # scan/import/remove/analyze/find_silence/extract/proxy
  timeline_tools.py    # tracks, clips, transiciones
  effects_tools.py     # add/remove/param/keyframe + transform/blur/sharpen
  audio_tools.py       # volumen, normalize, fades, remove_silence, duck
  audio_pro.py         # loudness, compresor, stems, duck auto, mix
  catalog_tools.py     # catálogo real MLT: filtros/transiciones/productores
  render_pro.py        # HW_PROFILES (VAAPI/QSV), cola y rangos de render
  timeline_pro.py      # markers, ripple_delete, slip, multicam, nest
  tts_engine.py        # TTS local Piper (voz es_MX-claude) para narración
  subtitle_tools.py    # create/import/export SRT + burn con libass
  render_tools.py      # RENDER_PROFILES, render/cancel/status en background
  qa_tools.py          # validate + checks + generate_preview
  edit_video.py        # pipeline completo edit_video()
  security.py          # validate_path, validate_output_path, safe_name
  models.py            # tipos del dominio
projects/  output/  media/  # datos de trabajo (verificables en repo)
```

## Documentación

- [ARCHITECTURE.md](ARCHITECTURE.md) — capas, formato MLT, decisiones.
- [INSTALL.md](INSTALL.md) — instalación reproducible en Arch.
- [USAGE.md](USAGE.md) — arranque, config cliente, ejemplos por área.
- [API.md](API.md) — referencia de las 77 tools con firma y retorno.
- [TOOLS.md](TOOLS.md) — tabla 1-línea + cuándo usar cada tool.
- [DEVELOPMENT.md](DEVELOPMENT.md) — añadir tools, tests, convenciones.
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — fallos conocidos y solución.
- [CHANGELOG.md](CHANGELOG.md) — historial (0.2.2 actual).

## Limitaciones honestas

- Sin GUI de Kdenlive ni modo headless: el render se hace vía `melt`
  con perfiles `MP4-1080p30` / `MP4-4K30` / `MP4-Vertical-Shorts`
  (y variantes HW `*-VAAPI`); si `melt` falta, el render falla con mensaje.
- `duck_music` es atenuación fija del track de música (default
  `duck_db=-10.0`); sidechain real con compresor solo vía helper
  importable `sidechain_duck` (`audio_tools.py:385`, opt-in, no tool MCP).
- `remove_silence` (tool) recorta con ffmpeg; el corte fino real vive en
  el helper `cut_silence` (`audio_pro.py:348`, `silenceremove`).
- Loudness por defecto `-16 LUFS` (`normalize_audio` y
  `loudness_normalize`, `target_i=-16.0`).
- Render HW con fallback automático VAAPI → CPU (`render_pro.py`,
  `fallback_from` en el status); QSV se rechaza de entrada (preflight).
  `out = dur - 1` en tractor y render (fix off-by-one).
- Subtítulos recortados al timeline (`clamped`, con aviso
  `"subtítulos recortados al timeline"`); `burn_subtitles` re-encode
  H.264 + AAC; `align_srt_to_audio` recoloca a fronteras de voz.
- QA bloquea render con medios ausentes (`check_missing_media`
  bloqueante); `validate_project` ya reconoce tractor/multitrack.
- Efectos limitados a los soportados por `effects_tools` + presets
  `limpio` / `cine` / `vivo` (`apply_preset`, helper importable).
- Catálogo MLT real con fallback a listas curadas si `melt` no está
  (`CURATED_*` en `catalog_tools.py`).
- Warnings `libsox_ng` / `librtaudio` de melt son opcionales e ignorables.
