# INSTALL.md — kdenlive-mcp (Arch Linux)

Instalación reproducible. No instala nada fuera de lo listado.

## 1. Dependencias del sistema (oficial Arch)

```bash
sudo pacman -S --needed kdenlive mlt ffmpeg python python-pip
```

Qué trae cada una (verificado 2026-10-07):

| Paquete | Aporta | Versión verificada |
|---|---|---|
| `kdenlive` | GUI para abrir los `.kdenlive` generados | 26.08.0 |
| `mlt` | `melt` (render headless) | 7.40.0 |
| `ffmpeg` | `ffmpeg` + `ffprobe` (sondeo, proxies, audio) | n9.0.1 |
| `python` | intérprete | 3.14.7 |
| `python-pip` | instalador (`venv` viene con `python`) | — |

## 2. Entorno virtual + paquete

```bash
cd /home/cam/Projects/kdenlive-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

Esto instala `kdenlive-mcp 0.1.0` (ver `pyproject.toml`: `requires-python
>= 3.10`, `dependencies = ["mcp>=1.0"]`) y registra el entry point
`kdenlive-mcp = kdenlive_mcp.server:main`.

## 3. Verificación paso a paso

```bash
source .venv/bin/activate   # si no está activo

# 3a. Imports (falla si falta mcp>=1.0)
python -c "from kdenlive_mcp.server import get_mcp; print(len(get_mcp()._tools), 'tools')"
# esperado: 50 tools

# 3b. Binarios del sistema
melt -version | head -1      # esperado: melt 7.40.0
ffmpeg -version | head -1    # esperado: ffmpeg version n9.0.1
ffprobe -version | head -1  # viene con el paquete ffmpeg

# 3c. Pipeline sin GUI (usa media/demo_*.mp4 si no hay fuentes)
PYTHONPATH=src python -c "
from kdenlive_mcp.edit_video import edit_video
r = edit_video(output='output/install_check.mp4', target_duration=5.0)
print(r['ok'], r['output'], r['duration'])"

# 3d. Tests
pytest -q
```

Si `3a` falla con `ModuleNotFoundError: mcp`, repite `pip install -e .`
con el venv activo. Si `melt`/`ffprobe` faltan, reinstala el paquete
Arch correspondiente (paso 1). Más casos: ver TROUBLESHOOTING.md.
