"""Motor TTS local (Piper) para narración en español.

Backend compartido por las tools MCP y la app tts-studio: lo que se
pre-escucha es lo que sale en el vídeo.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from kdenlive_mcp.security import validate_output_path, validate_path

PIPER_BIN = (
    shutil.which("piper")
    or str(Path.home() / ".local" / "bin" / "piper")
)
VOICES_DIR = Path("/home/cam/Projects/kdenlive-mcp/voices")
CACHE_DIR = VOICES_DIR / "cache"
DEFAULT_VOICE = "es_MX-claude"
TTS_TIMEOUT = 300

VOICES: dict[str, str] = {
    "es_MX-claude": "es_MX-claude-high.onnx",
    "es_ES-sharvard": "es_ES-sharvard-medium.onnx",
}


def _require_piper() -> str:
    if PIPER_BIN and Path(PIPER_BIN).exists():
        return PIPER_BIN
    raise RuntimeError("piper no instalado (pipx install piper-tts)")


def _voice_model(voice: str) -> Path:
    if voice not in VOICES:
        raise ValueError(
            f"voz desconocida: {voice!r}. Disponibles: {sorted(VOICES)}"
        )
    model = VOICES_DIR / VOICES[voice]
    if not model.exists():
        raise FileNotFoundError(f"modelo de voz no encontrado: {model}")
    return model


def list_voices() -> dict:
    """Lista voces TTS instaladas."""
    out = []
    for name, fname in VOICES.items():
        model = VOICES_DIR / fname
        out.append({
            "name": name, "model": str(model),
            "installed": model.exists(), "lang": "es",
        })
    return {"voices": out, "default": DEFAULT_VOICE}


def speak(
    text: str, voice: str = DEFAULT_VOICE, speed: float = 1.0,
    output: str = "",
) -> dict:
    """Sintetiza texto → WAV 48 kHz mono. Con caché por hash."""
    text = (text or "").strip()
    if not text:
        raise ValueError("texto vacío")
    if len(text) > 5000:
        raise ValueError("texto demasiado largo (>5000 caracteres)")
    if not 0.5 <= float(speed) <= 2.0:
        raise ValueError(f"speed fuera de rango [0.5, 2.0]: {speed}")
    model = _voice_model(voice)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(
        f"{voice}|{float(speed):.2f}|{text}".encode("utf-8")
    ).hexdigest()
    cached = CACHE_DIR / f"{key}.wav"
    out = validate_output_path(output) if output else cached
    if out.exists() and out.stat().st_size > 0:
        dur = _probe_duration(out)
        return {"output": str(out), "duration": dur,
                "voice": voice, "cached": True}
    piper = _require_piper()
    raw = out.with_name(out.stem + "_raw.wav") if out == cached else None
    target_raw = raw or Path(str(out) + ".raw.tmp.wav")
    cmd = [piper, "-m", str(model), "--length-scale",
           f"{1.0 / float(speed):.3f}", "-f", str(target_raw)]
    proc = subprocess.run(cmd, input=text, capture_output=True,
                          text=True, timeout=TTS_TIMEOUT)
    if proc.returncode != 0 or not target_raw.exists():
        raise RuntimeError(f"piper falló: {(proc.stderr or '')[-500:]}")
    # Normaliza a 48 kHz mono para el timeline.
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    tmp48 = target_raw.with_name(target_raw.stem + "_48k.wav")
    conv = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(target_raw), "-ar", "48000", "-ac", "1",
         str(tmp48)], capture_output=True, text=True, timeout=120)
    if conv.returncode != 0:
        raise RuntimeError(f"ffmpeg resample falló: {conv.stderr[-500:]}")
    tmp48.replace(out)
    try:
        target_raw.unlink()
    except OSError:
        pass
    return {"output": str(out), "duration": _probe_duration(out),
            "voice": voice, "cached": False}


def _probe_duration(path: Path) -> float | None:
    try:
        from kdenlive_mcp.media_tools import analyze_media
        return analyze_media(str(path)).get("duration")
    except Exception:
        return None


def srt_to_narration(
    srt_path: str, voice: str = DEFAULT_VOICE, speed: float = 1.0,
    output: str = "", project_path: str = "", track_id: str = "",
) -> dict:
    """SRT → pista WAV con cada bloque en su timestamp (adelay+amix).

    Si se pasa project_path+track_id, importa la pista al timeline.
    """
    from kdenlive_mcp.subtitle_tools import parse_srt, timestamp_to_frames

    src = validate_path(srt_path, must_exist=True)
    if src.suffix.lower() != ".srt":
        raise ValueError("se esperaba un .srt")
    blocks = parse_srt(src.read_text(encoding="utf-8"))
    if not blocks:
        raise ValueError("SRT sin bloques")
    wavs: list[tuple[Path, float]] = []
    for b in blocks:
        r = speak(b["text"], voice=voice, speed=speed)
        start_s = timestamp_to_frames(b["start"]) / 30.0
        wavs.append((Path(r["output"]), start_s))
    total_s = max(
        timestamp_to_frames(b["end"]) for b in blocks) / 30.0 + 0.5
    out = (validate_output_path(output) if output
           else src.parent / f"{src.stem}_narration.wav")
    if len(wavs) == 1 and abs(wavs[0][1]) < 0.01:
        Path(wavs[0][0]).replace(out) if Path(wavs[0][0]) != out else None
        mixed = out if out.exists() else Path(wavs[0][0])
        if mixed != out:
            shutil.copy(mixed, out)
    else:
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        cmd: list[str] = [ffmpeg, "-y", "-hide_banner", "-loglevel",
                           "error"]
        for w, _ in wavs:
            cmd += ["-i", str(w)]
        filt = "".join(
            f"[{i}:a]adelay={int(s * 1000)}|{int(s * 1000)}[a{i}];"
            for i, (_, s) in enumerate(wavs))
        filt += "".join(f"[a{i}]" for i in range(len(wavs)))
        filt += f"amix=inputs={len(wavs)}:normalize=0[aout]"
        cmd += ["-filter_complex", filt, "-map", "[aout]",
                "-t", f"{total_s:.2f}", str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=600)
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(
                f"mezcla de narración falló: {proc.stderr[-500:]}")
    result: dict = {"output": str(out), "duration": total_s,
                    "blocks": len(blocks), "voice": voice}
    if project_path and track_id:
        from kdenlive_mcp.media_tools import import_media
        from kdenlive_mcp.timeline_tools import add_clip
        imp = import_media(project_path, str(out))
        pid = imp.get("producer_id", "")
        added = add_clip(project_path, track_id, producer_id=pid,
                         in_frame=0,
                         out_frame=max(0, int(total_s * 30) - 1))
        result["clip"] = added
    return result
