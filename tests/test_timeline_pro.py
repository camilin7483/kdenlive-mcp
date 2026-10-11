"""Tests de timeline_pro (markers, ripple, slip, multicam, nest)."""

from __future__ import annotations

import math
import struct
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from kdenlive_mcp.timeline_pro import (
    add_marker,
    list_markers,
    multicam_sync,
    nest_sequence,
    remove_marker,
    ripple_delete,
    slip_clip,
)
from kdenlive_mcp.timeline_tools import add_clip, create_track


def _wav(path: Path, silence_sec: float, tone_sec: float,
         sr: int = 44100, freq: float = 440.0) -> str:
    """Wav mono 16-bit: silencio digital + tono (determinista, sin ffmpeg)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n_sil = int(silence_sec * sr)
    n_tone = int(tone_sec * sr)
    frames = [0] * n_sil + [
        int(0.4 * 32767 * math.sin(2 * math.pi * freq * i / sr))
        for i in range(n_tone)
    ]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(struct.pack(f"<{len(frames)}h", *frames) if frames else b"")
    return str(path)


@pytest.fixture
def track3(store, project_path, tmp_path):
    """Proyecto con un track de 3 clips de 30 frames (total 90)."""
    media = _wav(tmp_path / "media.wav", 0.0, 3.0)
    track_id = create_track(project_path, kind="video", name="V1")["track_id"]
    for pos in range(3):
        add_clip(project_path, track_id, media_path=media,
                 in_frame=0, out_frame=29, position=pos)
    return project_path, track_id, media


def test_markers_add_list_remove(store, project_path):
    m0 = add_marker(project_path, 30, "intro")
    assert m0["marker_id"] == 0 and m0["frame"] == 30 and m0["text"] == "intro"
    m1 = add_marker(project_path, 90, "out:ro;fin", color="blue")
    assert m1["marker_id"] == 1
    listed = list_markers(project_path)
    assert listed["count"] == 2
    # escapes reversibles
    assert listed["markers"][1]["text"] == "out:ro;fin"
    assert listed["markers"][1]["color"] == "blue"
    removed = remove_marker(project_path, 0)
    assert removed["removed"]["text"] == "intro"
    rest = list_markers(project_path)
    assert rest["count"] == 1 and rest["markers"][0]["text"] == "out:ro;fin"
    assert rest["markers"][0]["marker_id"] == 0  # reindexa
    with pytest.raises(ValueError):
        remove_marker(project_path, 5)
    with pytest.raises(ValueError):
        add_marker(project_path, -1, "x")


def test_ripple_delete_closes_gap(track3):
    project_path, track_id, _ = track3
    res = ripple_delete(project_path, track_id, 1)
    assert res["removed"] is True
    assert res["new_duration"] == 60  # 2 clips x 30 frames
    root = ET.parse(project_path).getroot()
    pl = next(p for p in root.findall("playlist") if p.get("id") == track_id)
    assert len([e for e in pl if e.tag == "entry"]) == 2
    tractor = root.find("tractor")
    assert tractor is not None and tractor.get("out") == "59"
    with pytest.raises(ValueError):
        ripple_delete(project_path, track_id, 7)


def test_slip_clip_offsets(store, project_path, tmp_path):
    media = _wav(tmp_path / "slip.wav", 0.0, 3.0)  # ~90 frames @30fps
    track_id = create_track(project_path, kind="video")["track_id"]
    add_clip(project_path, track_id, media_path=media,
             in_frame=0, out_frame=29, position=0)
    fwd = slip_clip(project_path, track_id, 0, 10)
    assert (fwd["in"], fwd["out"]) == (10, 39)
    assert isinstance(fwd["warnings"], list)
    back = slip_clip(project_path, track_id, 0, -5)
    assert (back["in"], back["out"]) == (5, 34)
    far = slip_clip(project_path, track_id, 0, 100)  # out=134 > ~90: advierte
    assert far["warnings"], "slip más allá del medio debe advertir"
    with pytest.raises(ValueError):
        slip_clip(project_path, track_id, 0, -500)  # in negativo


def test_multicam_sync_two_wavs(tmp_path):
    # Tono desde t=1s vs t=2s: onsets absolutos ~30/~60f; offsets
    # relativos (dif. vs mínimo) ~0/~30f, tolerancia ±15.
    a = _wav(tmp_path / "camA.wav", 1.0, 2.0)
    b = _wav(tmp_path / "camB.wav", 2.0, 2.0)
    out = str(tmp_path / "multi.kdenlive")
    res = multicam_sync([a, b], out, method="peak")
    assert res["project_path"] == out
    offs = res["offsets"]
    assert set(offs) == {a, b}
    assert abs(offs[a] - 0) <= 15
    assert abs(offs[b] - 30) <= 15
    root = ET.parse(out).getroot()
    playlists = [p for p in root.findall("playlist") if p.get("id") != "main_bin"]
    assert len(playlists) == 2
    for pl, key in zip(playlists, [a, b]):
        entry = pl.find("entry")
        assert entry is not None and int(entry.get("in", "-1")) == offs[key]
    with pytest.raises(ValueError):
        multicam_sync([a], str(tmp_path / "solo.kdenlive"))
    silent = _wav(tmp_path / "silencio.wav", 2.0, 0.0)
    with pytest.raises(ValueError):
        multicam_sync([a, silent], str(tmp_path / "mala.kdenlive"))
    with pytest.raises(ValueError):
        multicam_sync([a, b], str(tmp_path / "x.kdenlive"), method="rms")


def test_nest_sequence(store, tmp_path):
    child = store.create_project(name="hijo")["project_path"]
    media = _wav(tmp_path / "nest.wav", 0.0, 1.0)
    child_track = create_track(child, kind="video")["track_id"]
    add_clip(child, child_track, media_path=media, in_frame=0, out_frame=29, position=0)
    parent = store.create_project(name="padre")["project_path"]
    ptrack = create_track(parent, kind="video")["track_id"]
    res = nest_sequence(parent, child, ptrack)
    assert res["producer_id"]
    root = ET.parse(parent).getroot()
    prod = next(p for p in root.findall("producer") if p.get("id") == res["producer_id"])
    props = {(p.get("name") or ""): (p.text or "") for p in prod.findall("property")}
    assert props.get("mlt_service") == "xml"
    assert props.get("resource", "").endswith(".kdenlive")
    pl = next(p for p in root.findall("playlist") if p.get("id") == ptrack)
    entries = [e for e in pl if e.tag == "entry"]
    assert len(entries) == 1 and entries[0].get("producer") == res["producer_id"]
    with pytest.raises(FileNotFoundError):
        nest_sequence(parent, str(tmp_path / "noexiste.kdenlive"), ptrack)
    roto = tmp_path / "roto.kdenlive"
    roto.write_text("esto no es xml <", encoding="utf-8")
    with pytest.raises(ValueError):
        nest_sequence(parent, str(roto), ptrack)
