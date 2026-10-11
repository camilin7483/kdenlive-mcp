"""Tests TTS (piper local) + narración + burn de subtítulos (tmp_path).

Skips si faltan piper/ffmpeg/melt o el modelo de voz (no hay rojos en
entornos sin dependencias del sistema).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from conftest import synth_video
from kdenlive_mcp import tts_engine
from kdenlive_mcp.media_tools import analyze_media
from kdenlive_mcp.subtitle_tools import burn_subtitles

VOICE = "es_MX-claude"


def _need_model():
    model = tts_engine.VOICES_DIR / tts_engine.VOICES[VOICE]
    if not model.exists():
        pytest.skip(f"modelo de voz ausente: {model}")


def _need_piper():
    _need_model()
    if not tts_engine.PIPER_BIN or not Path(tts_engine.PIPER_BIN).exists():
        pytest.skip("piper no instalado (pipx install piper-tts)")


def _need_ffmpeg():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg no disponible")


def _duration(path: str) -> float | None:
    return analyze_media(path).get("duration")


def test_list_voices_has_default_installed():
    voices = tts_engine.list_voices()
    assert voices["default"] == VOICE
    entry = next(
        (v for v in voices["voices"] if v["name"] == VOICE), None
    )
    assert entry is not None, voices["voices"]
    assert entry["installed"] is True, entry


def test_speak_short_and_cache(tmp_path):
    _need_piper()
    _need_ffmpeg()
    out = str(tmp_path / "tts" / "hola.wav")
    first = tts_engine.speak("Hola mundo", voice=VOICE, output=out)
    assert Path(first["output"]).is_file()
    assert (first["duration"] or 0) > 0, first
    assert first["cached"] is False, first
    second = tts_engine.speak("Hola mundo", voice=VOICE, output=out)
    assert second["cached"] is True, second
    assert Path(second["output"]).is_file()


def test_speak_empty_raises():
    with pytest.raises(ValueError):
        tts_engine.speak("   ", voice=VOICE)


def test_speak_unknown_voice_raises(tmp_path):
    with pytest.raises(ValueError):
        tts_engine.speak(
            "Hola mundo", voice="xx_YY-inexistente",
            output=str(tmp_path / "tts" / "x.wav"),
        )


def test_srt_to_narration_two_blocks(tmp_path):
    _need_piper()
    _need_ffmpeg()
    srt = tmp_path / "subs" / "narr.srt"
    srt.parent.mkdir(parents=True, exist_ok=True)
    srt.write_text(
        "1\n00:00:00,500 --> 00:00:01,500\nHola mundo\n\n"
        "2\n00:00:02,000 --> 00:00:03,200\nEsto es una prueba\n",
        encoding="utf-8",
    )
    out = str(tmp_path / "subs" / "narr.wav")
    result = tts_engine.srt_to_narration(str(srt), voice=VOICE, output=out)
    assert result["blocks"] == 2, result
    assert Path(result["output"]).is_file()
    # Fin del último bloque: 3.2 s (+0.5 s de cola) → ≈3.7 s ±2 s.
    probed = _duration(result["output"])
    assert probed is not None
    assert abs(probed - 3.7) < 2.0, (probed, result)


def test_burn_subtitles_on_synth_clip(tmp_path):
    _need_ffmpeg()
    clip = synth_video(tmp_path / "media" / "clip.mp4", duration=2.0)
    srt = tmp_path / "subs" / "subs.srt"
    srt.parent.mkdir(parents=True, exist_ok=True)
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nHola mundo\n",
        encoding="utf-8",
    )
    out = str(tmp_path / "out" / "subbed.mp4")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    try:
        result = burn_subtitles(clip, str(srt), out)
    except RuntimeError as exc:
        pytest.skip(f"burn con libass no disponible: {exc}")
    assert Path(result.get("output", out)).is_file()
    probed = _duration(result.get("output", out))
    assert probed is not None
    assert abs(probed - 2.0) < 1.5, probed


def test_edit_video_narration(tmp_path, monkeypatch):
    """edit_video con narración: pista VOZ + SRT generado (TTS real)."""
    _need_piper()
    _need_ffmpeg()
    if shutil.which("melt") is None:
        pytest.skip("melt no disponible")
    clip = synth_video(tmp_path / "src" / "clip.mp4", duration=3.0)

    import kdenlive_mcp.edit_video as edit_module
    from kdenlive_mcp.kdenlive_project import ProjectStore
    from kdenlive_mcp.qa_tools import check_timeline

    class _TmpStore(ProjectStore):
        def __init__(self, *args, **kwargs):
            super().__init__(base_dir=str(tmp_path / "projects"))

    monkeypatch.setattr(edit_module, "ProjectStore", _TmpStore)

    output = str(tmp_path / "out" / "narr.mp4")
    result = edit_module.edit_video(
        source_files=[clip],
        output=output,
        target_duration=4.0,
        project_name="test_narr",
        width=320,
        height=240,
        fps=30,
        narration="Hola. Esto es una prueba.",
        voice=VOICE,
    )
    assert result["ok"] is True, result.get("warnings")
    project = result["project_path"]
    assert Path(project).is_file()

    timeline = check_timeline(project)
    xml = Path(project).read_text(encoding="utf-8")
    assert timeline["tracks"] >= 4 or 'title="VOZ"' in xml, timeline

    narr_srt = Path(project).parent / f"{Path(project).stem}_narr.srt"
    assert narr_srt.is_file(), result.get("warnings")
    assert "Hola" in narr_srt.read_text(encoding="utf-8")
