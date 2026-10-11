"""Test del pipeline edit_video con 2 clips sintéticos (proyecto en tmp)."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import synth_video


def test_edit_video_two_clips(tmp_path, monkeypatch):
    clip1 = synth_video(tmp_path / "src" / "a.mp4", duration=2.0)
    clip2 = synth_video(tmp_path / "src" / "b.mp4", duration=2.0)
    assert Path(clip1).is_file() and Path(clip2).is_file()

    import kdenlive_mcp.edit_video as edit_module
    from kdenlive_mcp.kdenlive_project import ProjectStore

    # edit_video crea ProjectStore() con base por defecto (projects/ del repo):
    # redirigirlo a tmp_path para no contaminar el repo (solo parcheo de test).
    class _TmpStore(ProjectStore):
        def __init__(self, *args, **kwargs):
            super().__init__(base_dir=str(tmp_path / "projects"))

    monkeypatch.setattr(edit_module, "ProjectStore", _TmpStore)

    output = str(tmp_path / "out" / "final.mp4")
    result = edit_module.edit_video(
        source_files=[clip1, clip2],
        output=output,
        target_duration=4.0,
        project_name="test_edit",
        width=320,
        height=240,
        fps=30,
    )
    assert result["ok"] is True, result.get("warnings")
    assert Path(output).is_file()

    from kdenlive_mcp.media_tools import analyze_media

    probed = analyze_media(output)
    assert probed["duration"] is not None
    assert abs(probed["duration"] - 4.0) < 1.5, probed["duration"]
