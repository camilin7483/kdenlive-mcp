"""Tests de effects_tools + audio_tools sobre proyecto y medios temporales."""

from __future__ import annotations

from pathlib import Path

from conftest import synth_video, synth_wav
from kdenlive_mcp.audio_tools import (
    duck_music,
    fade_in,
    fade_out,
    normalize_audio,
    remove_silence,
    set_volume,
)
from kdenlive_mcp.effects_tools import (
    add_blur,
    add_effect,
    add_sharpen,
    add_transform,
    apply_preset,
    remove_effect,
    set_effect_parameter,
)
from kdenlive_mcp.effects_tools import add_keyframe
from kdenlive_mcp.media_tools import import_media
from kdenlive_mcp.timeline_tools import add_clip, create_track


def _project_with_clip(project_path, tmp_path) -> tuple[str, int]:
    clip = synth_video(tmp_path / "media" / "clip.mp4", duration=1.0)
    producer_id = import_media(project_path, clip)["producer_id"]
    track_id = create_track(project_path, kind="video", name="V1")["track_id"]
    add_clip(project_path, track_id, producer_id=producer_id,
             in_frame=0, out_frame=29)
    return track_id, 0


def test_effect_lifecycle(project_path, tmp_path):
    track_id, clip_index = _project_with_clip(project_path, tmp_path)

    added = add_effect(project_path, track_id, clip_index, "blur",
                       {"radius": "5.0"})
    effect_id = added["effect_id"]
    assert added["mlt_service"] == "avfilter.boxblur"

    updated = set_effect_parameter(project_path, track_id, clip_index,
                                   effect_id, "radius", "7.0")
    assert updated["value"] == "7.0"

    # keyframes desordenados deben quedar ordenados por frame
    add_keyframe(project_path, track_id, clip_index, effect_id,
                 10, "radius", "9.0")
    kf = add_keyframe(project_path, track_id, clip_index, effect_id,
                      0, "radius", "1.0")
    first, second = kf["keyframes"].split(";")
    assert first.startswith("0=") and second.startswith("10=")

    assert add_transform(project_path, track_id, clip_index, 1.1)["effect_id"]
    assert add_blur(project_path, track_id, clip_index, 3.0)["effect_id"]
    assert add_sharpen(project_path, track_id, clip_index, 0.4)["effect_id"]

    removed = remove_effect(project_path, track_id, clip_index, effect_id)
    assert removed["removed"] == effect_id


def test_audio_fades_and_duck(project_path, tmp_path):
    track_id, clip_index = _project_with_clip(project_path, tmp_path)

    vol = set_volume(project_path, track_id, clip_index, -3.0)
    assert vol["volume_db"] == -3.0

    assert fade_in(project_path, track_id, clip_index, 10)["duration_frames"] == 10
    assert fade_out(project_path, track_id, clip_index, 10)["duration_frames"] == 10

    # duck: voz en V1, música en A1 con un clip
    music_track = create_track(project_path, kind="audio", name="A1")["track_id"]
    clip = synth_video(tmp_path / "media" / "clip.mp4", duration=1.0)
    producer_id = import_media(project_path, clip)["producer_id"]
    add_clip(project_path, music_track, producer_id=producer_id,
             in_frame=0, out_frame=29)
    ducked = duck_music(project_path, track_id, music_track, -8.0)
    assert ducked["clips_ducked"] == 1
    assert ducked["duck_db"] == -8.0


def test_normalize_and_remove_silence(tmp_path):
    wav = synth_wav(tmp_path / "audio" / "tone.wav", duration=1.0)

    normalized = normalize_audio(wav, str(tmp_path / "audio" / "norm.wav"))
    assert Path(normalized["output"]).is_file()

    # no debe fallar aunque el tono no tenga silencios
    cleaned = remove_silence(wav, str(tmp_path / "audio" / "nosil.wav"))
    assert Path(cleaned["output"]).is_file()


def test_apply_preset_youtube(project_path, tmp_path):
    import xml.etree.ElementTree as ET

    import pytest

    from kdenlive_mcp.catalog_tools import list_filters

    known = {f["name"] for f in list_filters()["filters"]}
    for preset in ("limpio", "cine", "vivo"):
        track_id, clip_index = _project_with_clip(project_path, tmp_path)
        applied = apply_preset(project_path, track_id, clip_index, preset)
        assert applied["preset"] == preset
        assert applied["mlt_service"] in known  # servicio real
        animated = [v for v in applied["params"].values() if ";" in v]
        assert animated, "el preset debe traer keyframes"
        first, second = animated[0].split(";")
        assert first.startswith("0=") and "=" in second

        root = ET.parse(project_path).getroot()
        playlist = next(pl for pl in root.iter("playlist")
                        if pl.get("id") == track_id)
        entry = [e for e in playlist if e.tag == "entry"][clip_index]
        filt = next(f for f in entry.findall("filter")
                    if f.get("id") == applied["effect_id"])
        assert filt.get("mlt_service") == applied["mlt_service"]

    with pytest.raises(ValueError):
        apply_preset(project_path, track_id, clip_index, "noexiste")
