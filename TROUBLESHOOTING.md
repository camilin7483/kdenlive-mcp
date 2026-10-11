# TROUBLESHOOTING.md — kdenlive-mcp

## Warnings `libsox_ng` / `librtaudio` de melt

```text
... libsox_ng / librtaudio: módulo opcional no disponible ...
```

**Causa:** build de MLT sin esos módulos de audio opcionales.
**Solución:** ignorables. El render `avformat` con `MP4-H264/AAC`
(`render_tools.RENDER_PROFILES`) no los necesita. Solo preocúpate si el
`status` del job es `error` y el `log` de `get_render_status` menciona
codecs (`libx264`, `aac`) en vez de estos módulos.

## Render largo o que no avanza

**Causa:** `render_project` lanza `melt proyecto in=0 out=<dur>
-consumer avformat:<out> ...` (`render_tools.py:223`); `dur` es la
longitud del tractor, así que un tractor largo = render largo.
**Solución:**

1. `get_render_status(job_id)` y mira `progress` (parseado de
   `Current Frame:`) y `log` (últimas 20 líneas).
2. Prueba primero `generate_preview(width=640, duration_sec=10)`.
3. Usa el perfil `Preview` (`f=mp4 ... preset=veryfast`) para iterar.
4. Si te equivocaste de proyecto, `cancel_render(job_id)`.

## Subtítulos: render acotado con `in`/`out`

El melt del render fija `in=0 out=<duración del tractor>`; los
subtítulos fuera de ese rango no se renderizan y un SRT larguísimo no
alarga el vídeo. **Solución:** cuadra `end_frames` de `create_subtitles`
con la duración real (ver `check_timeline`) y confirma con
`check_render(output_path)` la duración del MP4.

## `validate_project` decía "sin tracks" (ya corregido)

**Causa histórica:** el validador no reconocía el `<tractor>` +
`<multitrack>` real y reportaba falsos "sin tracks".
**Estado:** corregido en 0.1.0 (`qa_tools.validate_project` +
`edit_video._filter_qa_false_positives`). Si ves ese mensaje en un
proyecto que `inspect_project` muestra con tracks, actualiza el código y
re-corre `validate_project`; si persiste, adjunta `inspect_project`.

## `ffprobe` / `ffmpeg` / `melt` no encontrados

```text
{"ok": False, "error": "... No such file ... ffprobe/melt ..."}
```

**Causa:** falta el paquete del sistema (`media_tools._require_tool`,
`audio_tools._require_ffmpeg`).
**Solución:** `sudo pacman -S --needed kdenlive mlt ffmpeg python
python-pip` y verifica `melt -version`, `ffmpeg -version`,
`ffprobe -version`.

## Permisos / `validate_path` rechaza una ruta

```text
{"ok": False, "error": "ruta fuera del área permitida ..."}
```

**Causa:** `security.validate_path` / `validate_output_path`
(`security.py:44/69`) restringen a rutas bajo el proyecto para evitar
escrituras arbitrarias.
**Solución:** usa rutas absolutas bajo `/home/cam/Projects/kdenlive-mcp`
o relativas (`projects/`, `output/`, `media/`). No uses `..` para salir
del árbol.

## Cancelar un render colgado

```python
cancel_render(job_id="<id>")   # termina el proceso melt (proc.wait timeout 10s)
get_render_status(job_id="<id>")  # confirma status "cancelled"
```

Si el `status` queda en `running` sin `progress`, revisa el `log`: falta
de medios (`check_missing_media`) suele ser la causa.

## Preview en negro o vacío

1. `check_timeline` — ¿hay clips con `in <= out` dentro del rango?
2. `check_missing_media` — ¿existen los recursos en disco?
3. `analyze_media` del fuente — ¿ffprobe ve stream de vídeo?
4. `generate_preview` con `duration_sec` mayor y `width=640`.
