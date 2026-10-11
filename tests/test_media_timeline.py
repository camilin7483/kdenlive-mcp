"""Tests de media_tools + timeline_tools sobre carpeta y proyecto temporales."""

from __future__ import annotations

from pathlib import Path

from conftest import synth_video
from kdenlive_mcp.media_tools import analyze_media, import_media, scan_media
from kdenlive_mcp.timeline_tools import (
    add_clip,
    add_transition,
    create_track,
    delete_track,
    duplicate_clip,
    move_clip,
    remove_clip,
    split_clip,
    trim_clip,
)


def test_scan_analyze_import(store, project_path, tmp_path):
    media_dir = tmp_path / "media"
    clip = synth_video(media_dir / "clip.mp4", duration=1.0)
    scanned = scan_media(str(media_dir))
    assert scanned["count"] >= 1
    names = [f["name"] for f in scanned["files"]]
    assert "clip.mp4" in names

    probed = analyze_media(clip)
    assert probed["duration"] is not None and probed["duration"] > 0

    imported = import_media(project_path, clip)
    assert imported["producer_id"]
    assert imported["project_path"] == project_path


def test_timeline_clip_lifecycle(store, project_path, tmp_path):
    clip = synth_video(tmp_path / "media" / "clip.mp4", duration=1.0)
    producer_id = import_media(project_path, clip)["producer_id"]
    track_id = create_track(project_path, kind="video", name="V1")["track_id"]
    assert track_id

    # dos clips para poder mover/dividir/duplicar
    add_clip(project_path, track_id, producer_id=producer_id,
             in_frame=0, out_frame=29, position=0)
    added = add_clip(project_path, track_id, producer_id=producer_id,
                     in_frame=0, out_frame=29, position=1)
    assert added["clip_index"] == 1

    # mover el segundo al frente
    moved = move_clip(project_path, track_id, 1, 0)
    assert (moved["old_index"], moved["new_index"]) == (1, 0)

    # recortar el primero
    trimmed = trim_clip(project_path, track_id, 0, in_frame=0, out_frame=19)
    assert (trimmed["in_frame"], trimmed["out_frame"]) == (0, 19)

    # dividir el primero (20 frames -> corte en 10)
    split = split_clip(project_path, track_id, 0, at_frame=10)
    assert split["new_index"] == 1

    # duplicar el primero
    dup = duplicate_clip(project_path, track_id, 0)
    assert dup["new_index"] == 1

    # transición sobre un clip válido
    trans = add_transition(project_path, track_id, 1,
                           transition_type="dissolve", duration_frames=10)
    assert trans["track_id"] == track_id

    # eliminar un clip (quedan varios)
    removed = remove_clip(project_path, track_id, 0)
    assert removed["track_id"] == track_id
    assert removed["producer_id"] == producer_id


def test_create_delete_track(store, project_path):
    track_id = create_track(project_path, kind="audio", name="A1")["track_id"]
    deleted = delete_track(project_path, track_id)
    assert deleted["track_id"] == track_id
    assert deleted["removed_refs"] >= 1
