"""Tests de ProjectStore: creación, carga/validación, inspección, backup y errores."""

from __future__ import annotations

from pathlib import Path

import pytest

from kdenlive_mcp.media_tools import import_media
from kdenlive_mcp.qa_tools import validate_project
from kdenlive_mcp.timeline_tools import add_clip, create_track


def test_create_project(store, tmp_path):
    created = store.create_project(name="mi_proyecto")
    assert created["name"] == "mi_proyecto"
    assert created["project_path"].endswith(".kdenlive")
    assert Path(created["project_path"]).is_file()
    # vive bajo tmp_path, no contamina projects/ del repo
    assert str(tmp_path) in created["project_path"]


def test_create_then_open_and_validate(store, project_path, tmp_path):
    # medio dummy (vacío basta: validate solo mira estructura, no ffprobe)
    dummy = tmp_path / "dummy.mp4"
    dummy.write_bytes(b"\x00")
    track_id = create_track(project_path, kind="video", name="V1")["track_id"]
    imported = import_media(project_path, str(dummy))
    add_clip(project_path, track_id, producer_id=imported["producer_id"],
             in_frame=0, out_frame=29)
    opened = store.open_project(project_path)
    assert opened["project_path"] == project_path
    result = validate_project(project_path)
    assert result["valid"] is True, result["errors"]


def test_inspect_and_info(store, project_path):
    info = store.get_project_info(project_path)
    assert info["project_path"] == project_path
    assert info["num_playlists"] >= 1
    inspected = store.inspect_project(project_path)
    assert inspected["project_path"] == project_path
    assert "playlists" in inspected and "clips" in inspected
    assert "effects" in inspected and "tractor" in inspected
    saved = store.save_project(project_path)
    assert saved["saved"] is True


def test_backup_project(store, project_path):
    result = store.backup_project(project_path)
    assert Path(result["backup_path"]).is_file()
    assert result["project_path"] == project_path


def test_open_missing_path_raises(store, tmp_path):
    with pytest.raises(FileNotFoundError):
        store.open_project(str(tmp_path / "no_existe.kdenlive"))


def test_create_empty_name_raises(store):
    with pytest.raises(ValueError):
        store.create_project(name="")
    with pytest.raises(ValueError):
        store.create_project(name="   ")
