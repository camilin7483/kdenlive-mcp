# ARCHITECTURE.md — kdenlive-mcp

## Diagrama (texto)

```text
Cliente MCP (Claude Desktop, agente, tests)
        │  stdio / JSON-RPC (protocolo MCP)
        ▼
src/kdenlive_mcp/server.py ............ CAPA 1: FastMCP
  mcp = FastMCP("kdenlive-mcp")            50× @mcp.tool()
  _call(fn, ...)                           normaliza error → dict
  get_mcp() / main()                       entry: kdenlive_mcp.server:main
        │  llamadas directas a funciones puras
        ▼
CAPA 2: lógica de dominio (funciones puras → dict)
  kdenlive_project.py  ProjectStore (create/open/save/inspect/info/backup)
  media_tools.py       scan/import/remove/analyze/find_silence/extract/proxy
  timeline_tools.py    create/delete track, add/remove/move/trim/split/
                       duplicate clip, add_transition
  effects_tools.py     add/remove/param/keyframe, transform/blur/sharpen
  audio_tools.py       volumen, normalize, fades, remove_silence, duck
  subtitle_tools.py    create/import/export SRT
  edit_video.py        edit_video() — orquesta todo lo anterior
        │                + render_tools + qa_tools
        ▼
CAPA 3: persistencia y sistema
  projects/*.kdenlive  XML MLT vía xml.etree.ElementTree (ET)
  media_tools/audio    subprocess list-args → ffprobe / ffmpeg (sin shell)
  render_tools.py      subprocess → melt en thread (_JOBS, _LOCK)
  qa_tools.py          ET (proyecto) + ffprobe (MP4 renderizado)
  security.py          validate_path / validate_output_path / safe_name
```

Regla: `server.py` **no contiene lógica**; solo wrappers. Toda la lógica
vive en funciones puras testeables del módulo correspondiente.

## Formato `.kdenlive` = XML MLT

Un proyecto es un documento `<mlt>` con `<profile>`, `<producer>`s
(medios y clips), `<playlist>`s (tracks y bin) y un `<tractor>` con
`<multitrack>` que ordena las pistas. `ProjectStore._new_mlt_tree`
(`src/kdenlive_mcp/kdenlive_project.py:26`) genera este esqueleto;
`timeline_tools._ensure_tractor` (`timeline_tools.py:126`) garantiza el
tractor. Ejemplo mínimo real (cabecera de `projects/ev_test.kdenlive`):

```xml
<?xml version='1.0' encoding='utf-8'?>
<mlt profile="atsc_1080p_30" version="7.40.0" LC_NUMERIC="C" title="ev_test">
  <profile description="atsc_1080p_30" width="1280" height="720"
      progressive="1" display_aspect_num="16" display_aspect_den="9"
      frame_rate_num="30" frame_rate_den="1" colorspace="709"
      sample_aspect_num="1" sample_aspect_den="1" />
  <producer id="prod_1">
    <property name="mlt_service">avformat</property>
    <property name="resource">/ruta/al/medio.mp4</property>
  </producer>
  <playlist id="main_bin">
    <property name="xml_retain">1</property>
    <!-- entradas al bin: <entry producer="prod_1"/> -->
  </playlist>
  <!-- una <playlist id="track_v1"> por track, con <entry> por clip -->
  <!-- <tractor><multitrack><track producer="track_v1"/>... -->
</mlt>
```

Clips = `<entry producer="..." in="0" out="149"/>` dentro de la playlist
del track; efectos = `<filter>` hijos del entry (`effects_tools.py`);
volumen/fades = filtros `volume`/`panner` (`audio_tools.py`);
subtítulos = filters `subtitle` sobre clips + SRT externo (`subtitle_tools.py`).

## Decisiones

| Decisión | Por qué |
|---|---|
| **FastMCP** (`mcp.server.fastmcp.FastMCP`) | Decorador `@mcp.tool()` genera schema MCP desde la firma tipada; validación de tipos gratis. Sin servidor HTTP propio. |
| **melt para render, no Kdenlive headless** | Kdenlive no tiene modo de render headless utilizable; MLT sí (`melt proyecto.mlt -consumer avformat:OUT args`). Comando base en `render_tools.py:1-5`. Fiel al XML que Kdenlive abre después. |
| **xml.etree.ElementTree (stdlib)** | Sin dependencias de edición; el MLT es XML plano y ET basta para CRUD de producers/playlists/tractor. Sin lxml. |
| **subprocess con lista de args, sin shell** | `media_tools._run`, `audio_tools`, `render_tools._run_melt` pasan `list[str]` a `subprocess`; nunca `shell=True`. Rutas saneadas con `security.validate_path`. |
| **Jobs de render en thread** (`_JOBS`, `_LOCK`) | `render_project` retorna inmediato `{job_id, output, status}`; `_run_melt` corre `melt` en `threading.Thread` y actualiza `_JOBS[job_id]` (`status`, `progress`, `log` últimas 200 líneas). `cancel_render` termina el `proc`. |
| **Errores como dict, no excepciones** | `server._call` captura `Exception` → `{"ok": False, "error": str}`; `edit_video._fail` igual. El cliente MCP siempre recibe JSON válido. |
| **QA filtra falsos positivos** | `edit_video._filter_qa_false_positives` + `qa_tools.validate_project` entienden multitrack real (antes reportaba "sin tracks" sobre proyectos válidos; corregido en 0.1.0). |

## Qué API se usó (verificable)

- **MLT `melt` / consumer `avformat`** (`render_tools.py:200-225`):
  `melt <proyecto> in=0 out=<dur> -consumer avformat:<out> <args-perfil>`.
  `dur` sale de `_render_duration` (longitud del tractor). Perfiles en
  `RENDER_PROFILES` (`render_tools.py:34`): `MP4-H264/AAC` (default),
  `MP4-H265`, `WebM-VP9`, `Preview`. Progreso parseado con
  `_FRAME_RE = re.compile(r"Current Frame:\s*(\d+)")`.
- **ffprobe** (`media_tools.analyze_media`, `qa_tools.check_render`):
  `ffprobe -v error -show_format -show_streams -of json` → duración,
  streams video/audio. `_parse_fps` normaliza `r_frame_rate`.
- **ffmpeg** (`media_tools.extract_audio/generate_proxy/find_silence`,
  `audio_tools.normalize_audio/remove_silence`, `edit_video.
  _generate_demo_material/_trim_to_duration`): filtros `silencedetect`,
  `loudnorm` (EBU R128), `testsrc2`+`sine` para demos.
- **Perfiles MLT del proyecto**: atributo `profile="atsc_1080p_30"` en
  `<mlt>` + elemento `<profile>` con `width/height/frame_rate_num/den`
  (fijados por `ProjectStore.create_project`, `server.py:49`).
