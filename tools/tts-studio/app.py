"""TTS-Studio: app Gradio para pre-escuchar el backend TTS (Piper).

Comparte el backend con las tools MCP: importa tts_engine desde src/
(lo que se pre-escucha es lo que sale en el video).

Uso:
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python app.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR.parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import gradio as gr

from kdenlive_mcp.tts_engine import (
    DEFAULT_VOICE,
    list_voices,
    speak,
    srt_to_narration,
)

OUTPUTS_DIR = BASE_DIR / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


def _voice_choices() -> tuple[list[str], str]:
    """Lee las voces del backend; si falla, usa la voz por defecto."""
    try:
        info = list_voices()
        names = [v["name"] for v in info.get("voices", []) if v.get("name")]
        default = info.get("default") or DEFAULT_VOICE
        if not names:
            return [DEFAULT_VOICE], DEFAULT_VOICE
        if default not in names:
            default = names[0]
        return names, default
    except Exception:
        return [DEFAULT_VOICE], DEFAULT_VOICE


VOICE_CHOICES, VOICE_DEFAULT = _voice_choices()


def _fmt_duration(duration) -> str:
    try:
        if duration is None:
            return "duración desconocida"
        return f"{float(duration):.2f} s"
    except (TypeError, ValueError):
        return "duración desconocida"


def tts_from_text(text: str, voice: str, speed: float):
    """Sintetiza texto libre. Retorna (audio, descarga, estado)."""
    try:
        text = (text or "").strip()
        if not text:
            return None, None, "Error: escribe un texto primero."
        result = speak(text, voice=voice or VOICE_DEFAULT, speed=float(speed))
        out = result.get("output", "")
        dur = _fmt_duration(result.get("duration"))
        tag = " (caché)" if result.get("cached") else ""
        return out, out, f"OK{tag} · voz {result.get('voice')} · duración {dur}"
    except Exception as exc:  # visible en la UI
        return None, None, f"Error: {exc}"


def _read_blocks(srt_text: str) -> list[list]:
    """Parsea el SRT para la tabla; si falla, devuelve tabla vacía."""
    try:
        from kdenlive_mcp.subtitle_tools import parse_srt

        rows = []
        for b in parse_srt(srt_text):
            rows.append([b.get("index", ""), b.get("start", ""), b.get("text", "")])
        return rows
    except Exception:
        return []


def tts_from_srt(srt_file, voice: str, speed: float):
    """Genera pista mezclada desde un .srt. Retorna (audio, descarga, estado, tabla)."""
    try:
        if srt_file is None:
            return None, None, "Error: sube un archivo .srt primero.", []
        path = srt_file if isinstance(srt_file, str) else srt_file.name
        src = Path(path)
        if not src.exists():
            return None, None, f"Error: archivo no encontrado: {path}", []
        if src.suffix.lower() != ".srt":
            return None, None, "Error: se esperaba un archivo .srt", []
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dest = OUTPUTS_DIR / f"{src.stem}_{stamp}_narration.wav"
        result = srt_to_narration(
            str(src), voice=voice or VOICE_DEFAULT, speed=float(speed),
            output=str(dest),
        )
        out = result.get("output", "")
        try:
            srt_text = src.read_text(encoding="utf-8")
        except Exception:
            srt_text = ""
        rows = _read_blocks(srt_text)
        dur = _fmt_duration(result.get("duration"))
        return (
            out,
            out,
            f"OK · {result.get('blocks', len(rows))} bloques · "
            f"voz {result.get('voice')} · duración {dur}",
            rows,
        )
    except Exception as exc:  # visible en la UI
        return None, None, f"Error: {exc}", []


with gr.Blocks(title="TTS-Studio") as demo:
    gr.Markdown("# TTS-Studio · pre-escucha Piper (español)")
    with gr.Tab("Texto → Voz"):
        txt = gr.Textbox(label="Texto", lines=6, placeholder="Escribe aquí la narración…")
        with gr.Row():
            voice1 = gr.Dropdown(
                choices=VOICE_CHOICES, value=VOICE_DEFAULT, label="Voz"
            )
            speed1 = gr.Slider(0.5, 2.0, value=1.0, step=0.05, label="Velocidad")
        btn1 = gr.Button("Generar", variant="primary")
        audio1 = gr.Audio(label="Audio", type="filepath")
        file1 = gr.File(label="Descargar WAV")
        status1 = gr.Markdown()
        btn1.click(tts_from_text, [txt, voice1, speed1], [audio1, file1, status1])
    with gr.Tab("SRT → Narración"):
        srt = gr.File(label="Archivo .srt", file_types=[".srt"])
        with gr.Row():
            voice2 = gr.Dropdown(
                choices=VOICE_CHOICES, value=VOICE_DEFAULT, label="Voz"
            )
            speed2 = gr.Slider(0.5, 2.0, value=1.0, step=0.05, label="Velocidad")
        btn2 = gr.Button("Generar pista", variant="primary")
        audio2 = gr.Audio(label="Pista mezclada", type="filepath")
        file2 = gr.File(label="Descargar WAV")
        status2 = gr.Markdown()
        table2 = gr.Dataframe(
            headers=["Bloque", "Inicio", "Texto"],
            label="Bloques del SRT",
            wrap=True,
        )
        btn2.click(tts_from_srt, [srt, voice2, speed2], [audio2, file2, status2, table2])


if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860)
