"""Tests de render pro HW (VAAPI/QSV), fallback CPU, cola y rangos.

Todo vive bajo tmp_path: ningún test escribe en output/ ni projects/
del repo. Antes del primer render completo se usa generate_preview
(produce el preview rápido y valida que melt funciona).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from conftest import synth_video
from kdenlive_mcp.kdenlive_project import ProjectStore
from kdenlive_mcp.media_tools import import_media
from kdenlive_mcp.qa_tools import generate_preview
from kdenlive_mcp.render_pro import (
    HW_PROFILES,
    _fallback_for,
    _is_hw_failure,
    cancel_render,
    detect_hwaccel,
    get_queue_status,
    get_render_status,
    list_hw_profiles,
    render_hw,
    render_queue,
    render_range,
)
import kdenlive_mcp.render_pro as render_pro
from kdenlive_mcp.render_tools import RENDER_PROFILES
from kdenlive_mcp.timeline_tools import add_clip, create_track


def _build_project(tmp_path: Path, name: str,
                   duration: float = 1.0) -> str:
    """Proyecto con 1 clip sintético de `duration` s a 30fps."""
    store = ProjectStore(base_dir=str(tmp_path / "projects"))
    project = store.create_project(name=name)["project_path"]
    clip = synth_video(tmp_path / "media" / f"{name}.mp4",
                       duration=duration)
    producer = import_media(project, clip)["producer_id"]
    track = create_track(project, kind="video", name="V1")["track_id"]
    n_frames = max(1, int(round(duration * 30)))
    add_clip(project, track, producer_id=producer,
             in_frame=0, out_frame=n_frames - 1)
    return project


def _wait_done(job_id: str, timeout: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout
    final = None
    while time.monotonic() < deadline:
        final = get_render_status(job_id)
        if final["status"] in ("done", "error", "cancelled"):
            return final
        time.sleep(1)
    raise TimeoutError(f"job {job_id} sin terminar en {timeout}s: {final}")


def _assert_file(path: str) -> None:
    assert Path(path).is_file() and Path(path).stat().st_size > 0, path


def test_detect_hwaccel_shape():
    info = detect_hwaccel()
    assert set(info) == {"vaapi", "qsv", "render_node", "ffmpeg_ok"}
    assert info["ffmpeg_ok"] is True


def test_software_profiles_kept():
    for name in ("MP4-H264/AAC", "MP4-H265", "WebM-VP9", "Preview"):
        assert name in RENDER_PROFILES
    assert "s=1920x1080" in RENDER_PROFILES["MP4-1080p30"]
    assert "s=3840x2160" in RENDER_PROFILES["MP4-4K30"]
    assert "s=1080x1920" in RENDER_PROFILES["MP4-Vertical-Shorts"]


def test_hw_profiles_declared():
    assert "MP4-H264-VAAPI" in HW_PROFILES
    assert "MP4-H264-QSV" in HW_PROFILES
    assert "MP4-H265-VAAPI" in HW_PROFILES
    assert "h264_vaapi" in HW_PROFILES["MP4-H264-VAAPI"]
    assert "vb=12M" in HW_PROFILES["MP4-H264-VAAPI"]
    assert "s=1920x1080" in HW_PROFILES["MP4-1080p30-VAAPI"]
    assert "s=3840x2160" in HW_PROFILES["MP4-4K30-VAAPI"]
    assert "s=1080x1920" in HW_PROFILES["MP4-Vertical-VAAPI"]
    listed = list_hw_profiles()
    assert {p["name"] for p in listed["profiles"]} >= set(HW_PROFILES)
    assert all(p["extension"] == "mp4" for p in listed["profiles"])


def test_hw_failure_detection():
    assert _is_hw_failure(["[h264_vaapi] Failed to create VAAPI device"])
    assert _is_hw_failure(["MFX_ERR_DEVICE_FAILED -17"])
    assert _is_hw_failure(["Unknown encoder 'h264_vaapi'"])
    assert not _is_hw_failure(["[consumer] file not found: /x/y.mlt"])
    assert not _is_hw_failure([])


def test_fallback_mapping_keeps_resolution():
    for hw in HW_PROFILES:
        cpu = _fallback_for(hw)
        assert cpu in RENDER_PROFILES, (hw, cpu)
    assert _fallback_for("MP4-1080p30-VAAPI") == "MP4-1080p30"
    assert _fallback_for("MP4-4K30-VAAPI") == "MP4-4K30"
    assert _fallback_for("MP4-Vertical-VAAPI") == "MP4-Vertical-Shorts"
    assert _fallback_for("MP4-H264-QSV") == "MP4-H264/AAC"


def test_qsv_rejects_with_actionable_message(tmp_path, monkeypatch):
    project = _build_project(tmp_path, "qsvrej", duration=1.0)
    monkeypatch.setattr(render_pro, "_QSV_OK", None)
    monkeypatch.setattr(render_pro, "_qsv_preflight", lambda ff: False)
    with pytest.raises(RuntimeError, match="VAAPI"):
        render_hw(project, output_path=str(tmp_path / "out.mp4"),
                  profile_name="MP4-H264-QSV")


def test_preview_first_then_range(tmp_path):
    """Preview rápido primero; solo después el render del rango."""
    project = _build_project(tmp_path, "prevfirst", duration=2.0)
    preview = generate_preview(
        project, output_path=str(tmp_path / "out" / "prev.mp4"),
        width=320, duration_sec=1)
    _assert_file(preview["preview_path"])
    job = render_range(project, output_path=str(tmp_path / "out" / "rng.mp4"),
                       profile_name="Preview",
                       start_sec=0.0, duration_sec=1.0)
    assert job["in"] == 0
    assert job["out"] == 29  # 1s a 30fps
    final = _wait_done(job["job_id"])
    assert final["status"] == "done", final
    _assert_file(job["output"])


def test_render_hw_vaapi_auto_fallback(tmp_path):
    """HW directo o fallback CPU: el job termina done sin reintento manual."""
    project = _build_project(tmp_path, "hw1s", duration=1.0)
    out = str(tmp_path / "out" / "hw1s.mp4")
    job = render_hw(project, output_path=out, profile_name="MP4-H264-VAAPI")
    final = _wait_done(job["job_id"])
    assert final["status"] == "done", final["log_tail"][-500:]
    assert final["profile"] in ("MP4-H264-VAAPI", "MP4-H264/AAC")
    _assert_file(out)


def test_render_hw_forced_fallback_to_cpu(tmp_path, monkeypatch):
    """Fallo HW simulado: el job reintenta en CPU y expone fallback_from."""
    import subprocess as _sp

    project = _build_project(tmp_path, "hwfb", duration=1.0)
    out = str(tmp_path / "out" / "hwfb.mp4")
    real_popen = _sp.Popen
    calls = {"n": 0}

    class _FailProc:
        pid = 999999
        returncode = 1
        stderr = ["[h264_vaapi @ 0x1] Failed to create a VAAPI device"]

        def wait(self, timeout: float | None = None) -> int:
            return 1

    def _popen_once(cmd: list[str], **kw: object) -> object:
        if calls["n"] == 0 and "h264_vaapi" in " ".join(cmd):
            calls["n"] += 1
            return _FailProc()  # type: ignore[return-value]
        return real_popen(cmd, **kw)

    monkeypatch.setattr(render_pro.subprocess, "Popen", _popen_once)
    job = render_hw(project, output_path=out, profile_name="MP4-H264-VAAPI")
    final = _wait_done(job["job_id"])
    assert final["status"] == "done", final["log_tail"][-500:]
    assert final["profile"] == "MP4-H264/AAC"
    assert final["fallback_from"] == "MP4-H264-VAAPI"
    _assert_file(out)


def test_render_hw_unknown_profile_raises(tmp_path):
    project = _build_project(tmp_path, "hwbogus", duration=1.0)
    with pytest.raises(ValueError):
        render_hw(project, output_path=str(tmp_path / "out.mp4"),
                  profile_name="NO-EXISTE")


def test_render_queue_two_items(tmp_path):
    p1 = _build_project(tmp_path, "q1", duration=1.0)
    p2 = _build_project(tmp_path, "q2", duration=1.0)
    o1 = str(tmp_path / "out" / "q1.mp4")
    o2 = str(tmp_path / "out" / "q2.mp4")
    queued = render_queue([
        {"project_path": p1, "output_path": o1,
         "profile_name": "MP4-H264-VAAPI"},
        {"project_path": p2, "output_path": o2,
         "profile_name": "MP4-H264-VAAPI"},
    ])
    assert "queue_id" in queued
    assert len(queued["jobs"]) == 2
    for entry in queued["jobs"]:
        final = _wait_done(entry["job_id"], timeout=240.0)
        assert final["status"] == "done", final["log_tail"][-500:]
    _assert_file(o1)
    _assert_file(o2)
    qs = get_queue_status(queued["queue_id"])
    assert qs["status"] == "done"


def test_render_queue_bad_item_continues(tmp_path):
    good = _build_project(tmp_path, "qgood", duration=1.0)
    out_good = str(tmp_path / "out" / "qgood.mp4")
    queued = render_queue([
        {"project_path": str(tmp_path / "no-existe.kdenlive"),
         "output_path": str(tmp_path / "out" / "bad.mp4"),
         "profile_name": "MP4-H264-VAAPI"},
        {"project_path": good, "output_path": out_good,
         "profile_name": "Preview"},
    ])
    assert len(queued["jobs"]) == 2
    bad_id = queued["jobs"][0]["job_id"]
    assert get_render_status(bad_id)["status"] == "error"
    good_id = queued["jobs"][1]["job_id"]
    assert _wait_done(good_id)["status"] == "done"
    _assert_file(out_good)


def test_render_range_validates(tmp_path):
    project = _build_project(tmp_path, "rngbad", duration=1.0)
    with pytest.raises(ValueError):
        render_range(project, start_sec=-1.0)
    with pytest.raises(ValueError):
        render_range(project, duration_sec=0.0)


def test_cancel_before_proc_marks_cancelled(tmp_path, monkeypatch):
    """Cancelar sin proceso lanzado marca cancelled (no queda colgado)."""
    project = _build_project(tmp_path, "cancelado", duration=1.0)
    monkeypatch.setattr(render_pro, "_spawn", lambda *a: None)
    job = render_hw(project, output_path=str(tmp_path / "out.mp4"),
                    profile_name="Preview")
    assert cancel_render(job["job_id"])["status"] == "cancelled"
    assert get_render_status(job["job_id"])["status"] == "cancelled"
    assert cancel_render(job["job_id"])["status"] == "cancelled"


def test_cancel_unknown_raises():
    with pytest.raises(KeyError):
        cancel_render("noexiste")
