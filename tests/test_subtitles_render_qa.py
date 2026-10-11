"""Tests de subtítulos + render real + QA sobre proyecto temporal."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from conftest import synth_video
from kdenlive_mcp.media_tools import import_media
from kdenlive_mcp.qa_tools import (
    check_audio,
    check_missing_media,
    check_render,
    check_timeline,
    generate_preview,
)
from kdenlive_mcp.render_tools import (
    cancel_render,
    get_render_status,
    list_render_profiles,
    render_project,
    set_render_profile,
)
from kdenlive_mcp.subtitle_tools import (
    align_srt_to_audio,
    burn_subtitles,
    create_subtitles,
    export_srt,
    import_srt,
    timestamp_to_frames,
)
from kdenlive_mcp.timeline_tools import add_clip, create_track


def test_subtitle_roundtrip(project_path, tmp_path):
    track_id = create_track(project_path, kind="video", name="V1")["track_id"]
    out_srt = str(tmp_path / "subs" / "subs.srt")
    created = create_subtitles(
        project_path, track_id,
        ["Hola mundo", "Segunda línea"], [0, 30], [29, 59],
        output_srt=out_srt,
    )
    assert created["count"] == 2
    assert Path(created["srt_path"]).is_file()

    exported_path = str(tmp_path / "subs" / "export.srt")
    exported = export_srt(project_path, exported_path)
    assert exported["count"] == 2
    content = Path(exported["srt_path"]).read_text(encoding="utf-8")
    assert "Hola mundo" in content and "Segunda línea" in content

    reimported = import_srt(project_path, track_id, exported["srt_path"])
    assert reimported["count"] == 2


def test_render_profiles(project_path):
    profiles = list_render_profiles()
    assert profiles["profiles"], "sin perfiles de render"
    name = profiles["profiles"][0]["name"]
    applied = set_render_profile(project_path, name)
    assert applied["profile"] == name


def test_render_qa_and_preview(project_path, tmp_path):
    clip = synth_video(tmp_path / "media" / "clip.mp4", duration=1.0)
    producer_id = import_media(project_path, clip)["producer_id"]
    track_id = create_track(project_path, kind="video", name="V1")["track_id"]
    add_clip(project_path, track_id, producer_id=producer_id,
             in_frame=0, out_frame=29)  # 1s a 30fps

    set_render_profile(project_path, "Preview")
    out = str(tmp_path / "out" / "render.mp4")
    job = render_project(project_path, output_path=out, profile_name="Preview")
    assert job["status"] == "running"
    job_id = job["job_id"]

    deadline = time.monotonic() + 120
    final = None
    while time.monotonic() < deadline:
        final = get_render_status(job_id)
        assert final["job_id"] == job_id
        if final["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(1)
    assert final is not None and final["status"] == "done", final
    assert Path(out).is_file()

    missing = check_missing_media(project_path)
    assert missing["ok"] is True, missing["missing"]

    timeline = check_timeline(project_path)
    assert timeline["total_clips"] >= 1

    audio = check_audio(project_path)
    assert "issues" in audio

    checked = check_render(out)
    assert checked["ok"] is True, checked
    assert checked["size"] > 0

    preview = generate_preview(
        project_path, output_path=str(tmp_path / "out" / "preview.mp4"),
        width=320, duration_sec=1,
    )
    assert Path(preview["preview_path"]).is_file()


def test_cancel_unknown_job_raises():
    with pytest.raises(KeyError):
        cancel_render("job-inexistente-xyz")


def _clip_30f(project_path, tmp_path, name):
    clip = synth_video(tmp_path / "media" / f"{name}.mp4", duration=1.0)
    producer_id = import_media(project_path, clip)["producer_id"]
    track_id = create_track(project_path, kind="video", name=name)["track_id"]
    add_clip(project_path, track_id, producer_id=producer_id,
             in_frame=0, out_frame=29)  # 1s a 30fps
    return track_id


def test_subtitles_clamped_to_timeline(project_path, tmp_path):
    track_id = _clip_30f(project_path, tmp_path, "Clamp")
    created = create_subtitles(
        project_path, track_id, ["Uno", "Dos"], [0, 20], [29, 200],
        output_srt=str(tmp_path / "subs" / "clamp.srt"),
    )
    assert created["count"] == 2
    content = Path(created["srt_path"]).read_text(encoding="utf-8")
    assert "00:00:06,666" not in content  # frame 200 fuera del timeline
    last_line = next(
        line for line in reversed(content.strip().splitlines())
        if "-->" in line)
    _, last_end = (part.strip() for part in last_line.split("-->"))
    assert timestamp_to_frames(last_end) == 29


def _synth_av(dest, duration=1.0):
    import shutil
    import subprocess

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    proc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i",
         f"testsrc=size=320x240:rate=30:duration={duration}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", str(dest)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0 or not dest.exists():
        pytest.skip(f"ffmpeg no pudo generar AV: {proc.stderr[-300:]}")
    return str(dest)


def test_burn_subtitles_h264_aac(tmp_path):
    video = _synth_av(tmp_path / "media" / "av.mp4")
    srt = tmp_path / "subs" / "burn.srt"
    srt.parent.mkdir(parents=True, exist_ok=True)
    srt.write_text("1\n00:00:00,000 --> 00:00:00,900\nHola\n\n",
                   encoding="utf-8")
    out = str(tmp_path / "out" / "burned.mp4")
    result = burn_subtitles(video, str(srt), output_path=out)
    assert Path(result["output"]).is_file()
    assert Path(result["output"]).stat().st_size > 0


def test_align_srt_to_audio(tmp_path):
    from conftest import synth_wav

    wav = synth_wav(tmp_path / "audio" / "tone.wav", duration=1.0)
    srt = tmp_path / "subs" / "align.srt"
    srt.parent.mkdir(parents=True, exist_ok=True)
    srt.write_text("1\n00:00:00,100 --> 00:00:00,900\nHola\n\n",
                   encoding="utf-8")
    result = align_srt_to_audio(
        wav, str(srt), output_path=str(tmp_path / "subs" / "aligned.srt"))
    assert result["count"] == 1
    assert result["adjusted"] >= 0
    assert Path(result["srt_path"]).is_file()
