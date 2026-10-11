# DEVELOPMENT.md — kdenlive-mcp

## Estructura

```text
src/kdenlive_mcp/
  server.py            # wrappers @mcp.tool() + _call + get_mcp + main
  kdenlive_project.py  # ProjectStore (clase, línea 19)
  media_tools.py       # _run/_require_tool + 7 funciones públicas
  timeline_tools.py    # _load/_save/_ensure_tractor + 9 ops
  effects_tools.py     # filtros <filter> + keyframes
  audio_tools.py       # filtros volume + ffmpeg loudnorm/silence
  subtitle_tools.py    # SRT <-> filters subtitle sobre clips
  render_tools.py      # RENDER_PROFILES, _JOBS/_LOCK, _run_melt
  qa_tools.py          # validación ET + ffprobe
  edit_video.py        # pipeline edit_video() + helpers _*
  security.py          # validate_path / validate_output_path / safe_name
  models.py            # tipos del dominio
```

`projects/`, `media/`, `output/` son datos de trabajo (hay ejemplos
reales: `projects/ev_test.kdenlive`, `media/demo_*.mp4`).

## Añadir una nueva tool (4 pasos)

1. **Función pura en el módulo responsable.** Ejemplo: una op de
   timeline va en `timeline_tools.py`, siguiendo el patrón existente:
   `_load(project_path)` → mutar ET → `_save(tree, proj)` →
   retornar `{"ok": True, ...}`. Sin I/O fuera del módulo; `subprocess`
   solo con lista de args.
2. **Wrapper `@mcp.tool()` en `server.py`.** Importar el módulo (ya
   están `audio/effects/media/qa/render/subtitle/timeline_tools`) y
   añadir, en su sección:
   ```python
   @mcp.tool()
   def mi_tool(project_path: str, param: int = 0) -> dict:
       """Una línea para el schema MCP."""
       return _call(mi_modulo.mi_funcion, project_path, param)
   ```
   El docstring alimenta la descripción MCP: que sea precisa.
3. **Test.** Añadir caso en `tests/` (ver layout con `ls tests`;
   `pyproject.toml` usa `testpaths = ["tests"]`) y correr `pytest -q`.
   Patrón: crear proyecto en tmp, llamar a la función pura (o a la tool
   vía `get_mcp()`), assert sobre el dict y el XML resultante.
4. **Docs.** Añadir fila en `API.md` y `TOOLS.md`, ejemplo en
   `USAGE.md` si es uso común, y entrada en `CHANGELOG.md`.

## Tests y renders

```bash
source .venv/bin/activate
pytest -q                       # suite completa
pytest -q tests/test_x.py -k nombre   # focalizado
PYTHONPATH=src python -c "from kdenlive_mcp.server import get_mcp; print('tools:', len(get_mcp()._tools))"
```

Renders de prueba: usa `generate_preview` (`qa_tools.generate_preview`)
antes que `render_project` completo; los MP4 de prueba van a `output/`.

## Convenciones

- **Tipado total** en firmas públicas (`str`, `int`, `float`,
  `list[str]`, `dict | None`); FastMCP deriva el schema de ahí.
- **Errores como dict**: `{"ok": False, "error": str(exc)}` vía
  `server._call`; las funciones puras pueden lanzar y el wrapper
  normaliza. Nunca dejes escapar una excepción al protocolo MCP.
- **Seguridad de rutas**: toda ruta que entre por una tool pasa por
  `security.validate_path` (lectura) o `validate_output_path`
  (escritura); nombres con `safe_name`. Sin `shell=True` en ningún
  `subprocess`; sin `os.system`.
- **IDs MLT**: `prod_N`, `track_vN`/`track_aN`, `filter_N`, `sub_N`
  con helpers `_next_*` por módulo; no hardcodear ids.
- **Español** en docstrings/mensajes (convención del repo), código en
  inglés. Funciones pequeñas, early-return.
