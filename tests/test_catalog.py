"""Tests del catalogo dinamico (catalog_tools) sobre MLT real y proyecto temporal."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from conftest import synth_video
from kdenlive_mcp.catalog_tools import (
    add_filter,
    add_transition_by_service,
    describe_filter,
    list_filters,
    list_producers,
    list_transitions,
)
from kdenlive_mcp.media_tools import import_media
from kdenlive_mcp.timeline_tools import add_clip, create_track


def _project_with_clip(project_path, tmp_path) -> str:
    clip = synth_video(tmp_path / "media" / "clip.mp4", duration=1.0)
    producer_id = import_media(project_path, clip)["producer_id"]
    track_id = create_track(project_path, kind="video", name="V1")["track_id"]
    add_clip(project_path, track_id, producer_id=producer_id,
             in_frame=0, out_frame=29)
    return track_id


def test_list_filters_non_empty():
    result = list_filters()
    assert len(result["filters"]) > 0
    for item in result["filters"][:20]:
        assert item["name"] and item["type"]


def test_list_filters_kind_prefix():
    frei0r = list_filters("frei0r")["filters"]
    assert frei0r
    assert all(f["name"].startswith("frei0r") for f in frei0r)
    avfilter = list_filters("avfilter")["filters"]
    assert avfilter
    assert all(f["name"].startswith("avfilter") for f in avfilter)
    assert len(list_filters()["filters"]) >= len(frei0r) + len(avfilter)


def test_list_transitions_and_producers():
    transitions = list_transitions()["transitions"]
    assert transitions
    assert any(t["name"] == "mix" for t in transitions)
    producers = list_producers()["producers"]
    assert producers
    assert all(p["name"] for p in producers)


def test_describe_avfilter_eq():
    described = describe_filter("avfilter.eq")
    assert described["service"] == "avfilter.eq"
    assert described["params"]
    names = {p["name"] for p in described["params"]}
    assert "av.contrast" in names and "av.brightness" in names


def test_describe_without_metadata_returns_empty():
    # frei0r.cairoblend no publica metadatos: no debe fallar
    described = describe_filter("frei0r.cairoblend")
    assert described == {"service": "frei0r.cairoblend", "params": []}


def test_add_filter_blur(project_path, tmp_path):
    track_id = _project_with_clip(project_path, tmp_path)
    added = add_filter(project_path, track_id, 0, "avfilter.boxblur",
                       {"av.luma_radius": "5"})
    assert added["effect_id"].startswith("filter_")
    assert added["mlt_service"] == "avfilter.boxblur"

    root = ET.parse(project_path).getroot()
    playlist = next(pl for pl in root.iter("playlist")
                    if pl.get("id") == track_id)
    entry = [e for e in playlist if e.tag == "entry"][0]
    filt = next(f for f in entry.findall("filter")
                if f.get("id") == added["effect_id"])
    assert filt.get("mlt_service") == "avfilter.boxblur"
    # in/out heredados del entry del clip
    assert filt.get("in") == entry.get("in") == "0"
    assert filt.get("out") == entry.get("out") == "29"
    props = {p.get("name"): p.text for p in filt.findall("property")}
    assert props["mlt_service"] == "avfilter.boxblur"
    assert props["av.luma_radius"] == "5"


def test_add_filter_invalid_service(project_path, tmp_path):
    track_id = _project_with_clip(project_path, tmp_path)
    with pytest.raises(ValueError):
        add_filter(project_path, track_id, 0, "noexiste_xyz")


def test_add_filter_bad_clip_index(project_path, tmp_path):
    track_id = _project_with_clip(project_path, tmp_path)
    with pytest.raises((IndexError, ValueError)):
        add_filter(project_path, track_id, 99, "brightness")


def test_add_transition_mix(project_path):
    created = add_transition_by_service(project_path)
    assert created["transition_id"].startswith("transition")

    root = ET.parse(project_path).getroot()
    tractor = root.find("tractor")
    assert tractor is not None
    trans = next(t for t in tractor.findall("transition")
                 if t.get("id") == created["transition_id"])
    props = {p.get("name"): p.text for p in trans.findall("property")}
    assert props["mlt_service"] == "mix"
    assert props["a_track"] == "0"
    assert props["b_track"] == "1"


def test_add_transition_invalid_service(project_path):
    with pytest.raises(ValueError):
        add_transition_by_service(project_path, service="noexiste_xyz")


def test_fallback_curado_sin_melt(project_path, tmp_path, monkeypatch):
    import kdenlive_mcp.catalog_tools as catalog

    def _sin_melt(args):
        raise ValueError("melt no disponible en PATH (MLT no instalado)")

    monkeypatch.setattr(catalog, "_run_melt", _sin_melt)
    catalog.clear_cache()

    try:
        filters = list_filters()["filters"]
        assert filters
        names = {f["name"] for f in filters}
        assert "avfilter.eq" in names

        assert any(t["name"] == "mix"
                   for t in list_transitions()["transitions"])
        assert list_producers()["producers"]

        described = describe_filter("avfilter.eq")
        assert {p["name"] for p in described["params"]} >= {
            "av.contrast", "av.brightness", "av.saturation"}

        track_id = _project_with_clip(project_path, tmp_path)
        added = add_filter(project_path, track_id, 0, "avfilter.eq",
                           {"av.contrast": "1.1"})
        assert added["mlt_service"] == "avfilter.eq"

        created = add_transition_by_service(project_path, service="mix")
        assert created["transition_id"].startswith("transition")
    finally:
        catalog.clear_cache()
