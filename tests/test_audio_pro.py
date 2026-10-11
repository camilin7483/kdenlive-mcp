"""Tests de audio_pro: loudness, compresor, stems, ducking y mezcla."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from conftest import synth_wav
from kdenlive_mcp.audio_pro import (
    apply_compressor,
    cut_silence,
    duck_sidechain,
    loudness_measure,
    loudness_normalize,
    mix_stems,
    split_stems,
    tts_duck_auto,
)
from kdenlive_mcp.kdenlive_project import ProjectStore
from kdenlive_mcp.media_tools import import_media
from kdenlive_mcp.timeline_tools import add_clip, create_track

FPS = 30


def _wav(tmp_path: Path, name: str, duration: float = 2.0) -> str:
    return synth_wav(tmp_path / "audio" / name, duration=duration)


def _project_2audio(tmp_path: Path, name: str) -> tuple[str, str, str]:
    """Proyecto con 2 tracks de audio, 1 clip de 2s (60f) cada uno."""
    store = ProjectStore(base_dir=str(tmp_path / "projects"))
    project = store.create_project(name=name)["project_path"]
    voice_wav = _wav(tmp_path, f"{name}_voice.wav")
    music_wav = _wav(tmp_path, f"{name}_music.wav")
    voice_prod = import_media(project, voice_wav)["producer_id"]
    music_prod = import_media(project, music_wav)["producer_id"]
    voice = create_track(project, kind="audio", name="Voice")["track_id"]
    music = create_track(project, kind="audio", name="Music")["track_id"]
    n = int(round(2.0 * FPS))
    add_clip(project, voice, producer_id=voice_prod,
             in_frame=0, out_frame=n - 1)
    add_clip(project, music, producer_id=music_prod,
             in_frame=0, out_frame=n - 1)
    return project, voice, music


def test_loudness_measure_numeric(tmp_path):
    wav = _wav(tmp_path, "tone.wav")
    measured = loudness_measure(wav)
    assert isinstance(measured["integrated_lufs"], float)
    assert isinstance(measured["true_peak"], float)
    assert isinstance(measured["lra"], float)


def test_loudness_normalize_to_minus14(tmp_path):
    wav = _wav(tmp_path, "tone.wav")
    out = str(tmp_path / "audio" / "tone_norm.wav")
    result = loudness_normalize(wav, out, target_i=-14.0)
    assert Path(result["output"]).is_file()
    assert Path(result["output"]).stat().st_size > 0
    back = loudness_measure(result["output"])
    assert abs(back["integrated_lufs"] - (-14.0)) <= 2.0


def test_apply_compressor_generates_file(tmp_path):
    wav = _wav(tmp_path, "tone.wav")
    out = str(tmp_path / "audio" / "tone_comp.wav")
    result = apply_compressor(wav, out, threshold_db=-20.0, ratio=3.0)
    assert Path(result["output"]).is_file()
    assert Path(result["output"]).stat().st_size > 0


def test_split_stems_marks_roles(tmp_path):
    store = ProjectStore(base_dir=str(tmp_path / "projects"))
    project = store.create_project(name="stems")["project_path"]
    voice = create_track(project, kind="audio", name="V")["track_id"]
    music = create_track(project, kind="audio", name="M")["track_id"]
    sfx = create_track(project, kind="audio", name="S")["track_id"]
    result = split_stems(project, voice, music, sfx)
    assert result["roles"] == {voice: "voice", music: "music", sfx: "sfx"}
    root = ET.parse(project).getroot()
    seen = {}
    for playlist in root.iter("playlist"):
        for prop in playlist.findall("property"):
            if prop.get("name") == "role":
                seen[playlist.get("id")] = prop.text
    assert seen == result["roles"]


def test_tts_duck_auto_creates_keyframes(tmp_path):
    project, voice, music = _project_2audio(tmp_path, "duck")
    duck_db = -10.0
    result = tts_duck_auto(project, voice, music,
                           duck_db=duck_db, fade_frames=15)
    assert result["ducked_clips"] == 1
    assert result["duck_db"] == duck_db
    root = ET.parse(project).getroot()
    gains = []
    for playlist in root.iter("playlist"):
        if playlist.get("id") != music:
            continue
        for entry in playlist.findall("entry"):
            for filt in entry.findall("filter"):
                if filt.get("mlt_service") == "volume":
                    for prop in filt.findall("property"):
                        if prop.get("name") == "gain":
                            gains.append(prop.text or "")
    assert gains, "sin filtros volume en la pista de musica"
    assert any(str(duck_db) in gain for gain in gains)
    for gain in gains:
        frames = [int(part.split("=")[0]) for part in gain.split(";")]
        assert frames == sorted(frames), f"keyframes desordenados: {gain}"


def test_mix_stems_generates_wav(tmp_path):
    project, _, _ = _project_2audio(tmp_path, "mix")
    out = str(tmp_path / "audio" / "stem_mix.wav")
    result = mix_stems(project, out)
    assert Path(result["output"]).is_file()
    assert Path(result["output"]).stat().st_size > 0
    assert result["duration"] is not None and result["duration"] > 0


def _channels(path: str) -> int | None:
    import shutil
    import subprocess
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=channels",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return int((proc.stdout or "").strip())
    except (TypeError, ValueError):
        return None


def _wav_with_silence(dest, duration_tone: float = 1.0,
                      duration_sil: float = 1.0) -> str:
    """Tono-silencio-tono concatenados (corte real debe acortar)."""
    import shutil
    import subprocess
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tone = str(dest.parent / "tone_part.wav")
    sil = str(dest.parent / "sil_part.wav")
    synth_wav(tone, duration=duration_tone)
    ffmpeg = shutil.which("ffmpeg")
    proc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={duration_sil}",
         str(sil)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    lst = dest.parent / "concat.txt"
    lst.write_text(f"file '{tone}'\nfile '{sil}'\nfile '{tone}'\n",
                   encoding="utf-8")
    proc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c:a", "pcm_s16le", str(dest)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    return str(dest)


def test_loudness_normalize_default_minus16_stereo(tmp_path):
    wav = _wav(tmp_path, "tone16.wav")
    out = str(tmp_path / "audio" / "tone16_norm.wav")
    result = loudness_normalize(wav, out)
    assert result["target_i"] == -16.0
    assert Path(result["output"]).is_file()
    back = loudness_measure(result["output"])
    assert abs(back["integrated_lufs"] - (-16.0)) <= 2.0
    channels = _channels(result["output"])
    if channels is not None:
        assert channels == 2


def test_duck_sidechain_real(tmp_path):
    voice = _wav(tmp_path, "duck_voice.wav")
    music = _wav(tmp_path, "duck_music.wav")
    out = str(tmp_path / "audio" / "ducked.wav")
    result = duck_sidechain(voice, music, out, duck_db=-10.0)
    assert Path(result["output"]).is_file()
    assert Path(result["output"]).stat().st_size > 0
    assert result["method"] in ("sidechain", "fixed")


def test_tts_duck_auto_with_sidechain(tmp_path):
    project, narr, mus = _project_2audio(tmp_path, "ducksc")
    voice = _wav(tmp_path, "sc_voice.wav")
    music = _wav(tmp_path, "sc_music.wav")
    out = str(tmp_path / "audio" / "sc_ducked.wav")
    result = tts_duck_auto(project, narr, mus,
                           duck_db=-10.0, fade_frames=15,
                           voice_media="", music_media="",
                           output_path="", use_sidechain=False)
    assert result["ducked_clips"] == 1
    assert result["sidechain"] == {"attempted": False}
    # Ruta real con audios: no debe romper los keyframes.
    project2, narr2, mus2 = _project_2audio(tmp_path, "ducksc2")
    result2 = tts_duck_auto(project2, narr2, mus2, duck_db=-10.0,
                             fade_frames=15, voice_media=voice,
                             music_media=music, output_path=out,
                             use_sidechain=True)
    assert result2["ducked_clips"] == 1
    assert result2["sidechain"]["attempted"] is True
    assert Path(out).is_file() and Path(out).stat().st_size > 0


def test_cut_silence_real_cut(tmp_path):
    src = _wav_with_silence(tmp_path / "audio" / "with_sil.wav")
    out = str(tmp_path / "audio" / "cut.wav")
    result = cut_silence(src, out, threshold_db=-35.0, min_duration=0.5)
    assert Path(result["output"]).is_file()
    assert Path(result["output"]).stat().st_size > 0
    assert isinstance(result["removed_segments"], list)
    assert len(result["removed_segments"]) >= 1
    import shutil
    import subprocess
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        def _dur(p: str) -> float | None:
            proc = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries",
                 "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", p],
                capture_output=True, text=True, timeout=60)
            try:
                return float((proc.stdout or "").strip())
            except (TypeError, ValueError):
                return None
        d_src, d_out = _dur(src), _dur(out)
        if d_src and d_out:
            assert d_out < d_src
