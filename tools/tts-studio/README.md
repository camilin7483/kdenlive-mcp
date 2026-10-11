# TTS-Studio

App Gradio para pre-escuchar el backend TTS local (Piper, español).
Comparte el backend con las tools MCP (`src/kdenlive_mcp/tts_engine.py`):
lo que se pre-escucha es lo que sale en el vídeo.

## Pestañas

- **Texto → Voz**: escribe texto, elige voz y velocidad, genera audio (escucha + descarga WAV).
- **SRT → Narración**: sube un `.srt`, genera la pista mezclada con cada bloque en su timestamp (escucha + descarga + tabla de bloques).

## Arranque

```bash
cd /home/cam/Projects/kdenlive-mcp/tools/tts-studio
python3 -m venv .venv \
  && .venv/bin/pip install -r requirements.txt \
  && .venv/bin/python app.py
```

Abre http://127.0.0.1:7860

## Notas

- Solo depende de `gradio` (ver `requirements.txt`).
- Los WAV generados del SRT se guardan en `outputs/`.
- Los errores del backend se muestran como mensaje en la propia UI.
