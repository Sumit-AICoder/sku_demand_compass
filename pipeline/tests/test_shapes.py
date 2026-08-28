"""Tests for the drill-down map geometry."""
from __future__ import annotations

import json

import numpy as np
import pytest

from pipeline.common import MARTS, CURATED, read_table

SHAPES = MARTS / "shapes"
pytestmark = pytest.mark.filterwarnings("ignore")


def _load(name):
    return json.loads((SHAPES / name).read_text())


def test_india_covers_the_country():
    d = _load("india.json")
    pts = np.array([p for f in d["features"] for r in f["rings"] for p in r])
    assert 67 < pts[:, 0].min() < 70 and 95 < pts[:, 0].max() < 98
    assert 6 < pts[:, 1].min() < 10
    assert pts[:, 1].max() > 36.5, "northern extent is short of the official boundary"
    assert len(d["features"]) >= 30


def test_jammu_kashmir_and_ladakh_are_complete():
    """India's official boundary, which is what a map published in India must show.

    The original GADM source depicted only the Indian-administered portion, stopping at
    about 35.5N / 73.8E and omitting Gilgit-Baltistan and Aksai Chin. This test pins the
    correct depiction so a future change of source cannot quietly revert it.
    """
    d = _load("india.json")
    by = {f["name"]: np.array([p for r in f["rings"] for p in r]) for f in d["features"]}
    assert "Ladakh" in by, "Ladakh missing (it is a separate UT since 2019)"
    assert "Jammu and Kashmir" in by

    north = np.vstack([by["Ladakh"], by["Jammu and Kashmir"]])
    assert north[:, 1].max() > 36.5, "Gilgit-Baltistan missing from the north"
    assert north[:, 0].min() < 73.0, "western extent short of the official boundary"
    assert north[:, 0].max() > 79.5, "Aksai Chin missing from the east"


def test_the_north_is_contiguous_with_the_rest_of_india():
    """Simplification must not open a gap between J&K and its neighbours."""
    d = _load("india.json")
    by = {f["name"]: np.array([p for r in f["rings"] for p in r]) for f in d["features"]}
    north = np.vstack([by["Ladakh"], by["Jammu and Kashmir"]])
    hp = by["Himachal Pradesh"]
    gap = np.min(np.hypot(north[:, None, 0] - hp[None, :, 0],
                          north[:, None, 1] - hp[None, :, 1]))
    assert gap < 0.05, f"{gap * 111:.0f} km gap between J&K and Himachal Pradesh"


def test_pilot_states_are_flagged():
    d = _load("india.json")
    pilots = {f["name"] for f in d["features"] if f["pilot"]}
    assert pilots == {"Punjab", "Madhya Pradesh", "Maharashtra"}


def test_states_are_where_they_should_be():
    """A projection bug is invisible in a bounds check but obvious here."""
    d = _load("india.json")
    c = {f["name"]: np.array([p for r in f["rings"] for p in r]).mean(0)
         for f in d["features"]}
    assert c["Tamil Nadu"][1] < c["Punjab"][1]          # south of Punjab
    assert c["Gujarat"][0] < c["West Bengal"][0]        # west of Bengal
    assert c["Maharashtra"][1] < c["Punjab"][1]
    assert 73 < c["Punjab"][0] < 77 and 29 < c["Punjab"][1] < 33
    assert c["Ladakh"][1] > c["Punjab"][1]             # Ladakh is the northernmost


@pytest.mark.parametrize("state,n", [("punjab", 23), ("madhya_pradesh", 55),
                                     ("maharashtra", 36)])
def test_every_district_has_geometry(state, n):
    d = _load(f"state_{state}.json")
    assert len(d["features"]) == n
    ids = read_table(CURATED / "geo_districts.parquet")
    ids = set(ids[ids.state == d["state"]]["district_id"])
    assert {f["id"] for f in d["features"]} == ids


def test_districts_sit_inside_their_state():
    india = {f["name"]: np.array([p for r in f["rings"] for p in r])
             for f in _load("india.json")["features"]}
    for slug, name in [("punjab", "Punjab"), ("maharashtra", "Maharashtra")]:
        s = india[name]
        lo0, lo1 = s[:, 0].min() - 0.6, s[:, 0].max() + 0.6
        la0, la1 = s[:, 1].min() - 0.6, s[:, 1].max() + 0.6
        for f in _load(f"state_{slug}.json")["features"]:
            p = np.array([q for r in f["rings"] for q in r])
            assert lo0 <= p[:, 0].min() and p[:, 0].max() <= lo1, f["name"]
            assert la0 <= p[:, 1].min() and p[:, 1].max() <= la1, f["name"]


def test_shared_outlines_are_flagged_not_hidden():
    """Several present-day districts share one historical polygon. The map must say so
    rather than implying a boundary we do not have."""
    d = _load("state_punjab.json")
    shared = [f["name"] for f in d["features"] if f["shared"]]
    assert shared, "no shared outlines flagged — the flag has stopped working"


def test_block_cells_hug_their_district():
    """Unbounded Voronoi cells once sprawled ~40x the district, so the map zoomed out
    and left the district a speck. Cells are clipped to its bounding box now."""
    blocks = read_table(CURATED / "geo_blocks.parquet")
    for did in blocks["district_id"].drop_duplicates().sample(12, random_state=0):
        d = _load(f"district_{did}.json")
        cells = [f["cell"] for f in d["features"] if f.get("cell")]
        if not cells or not d["outline"]:
            continue
        c = np.vstack([np.array(x) for x in cells])
        o = np.vstack([np.array(r) for r in d["outline"]])
        ow = max(o[:, 0].max() - o[:, 0].min(), 1e-9)
        assert (c[:, 0].max() - c[:, 0].min()) / ow < 1.3, did


def test_every_district_has_block_geometry():
    districts = read_table(CURATED / "geo_districts.parquet")
    for did in districts["district_id"]:
        assert (SHAPES / f"district_{did}.json").exists(), did


def test_simplification_keeps_files_small():
    """A browser must not be asked to parse the raw 33 MB source."""
    total = sum(p.stat().st_size for p in SHAPES.glob("*.json"))
    assert total < 4_000_000, f"{total/1e6:.1f} MB of shapes"
    assert (SHAPES / "india.json").stat().st_size < 400_000


def test_rdp_preserves_shape():
    from pipeline.export.shapes import rdp
    t = np.linspace(0, 2 * np.pi, 400)
    circle = np.column_stack([np.cos(t), np.sin(t)])
    s = rdp(circle, 0.01)
    assert len(s) < len(circle)
    # every simplified vertex still lies on the unit circle
    assert np.allclose(np.hypot(s[:, 0], s[:, 1]), 1.0, atol=1e-9)


def test_clip_to_box_bounds_the_polygon():
    from pipeline.export.shapes import _clip_to_box
    poly = np.array([[-10., -10.], [10., -10.], [10., 10.], [-10., 10.]])
    out = _clip_to_box(poly, (-1, -1, 1, 1))
    assert len(out) >= 3
    assert out[:, 0].min() >= -1.001 and out[:, 0].max() <= 1.001
    assert out[:, 1].min() >= -1.001 and out[:, 1].max() <= 1.001


# ---------------------------------------------------------------- api

@pytest.fixture(scope="module")
def q():
    from api.main import q as _q
    return _q


def test_shape_values_join_to_the_active_filter(q):
    """Geometry and value come from one request, so a shape can never display a number
    computed under a different filter."""
    from api.main import _shape_values
    allv = _shape_values("india", None, None, None, None)
    ss = _shape_values("india", None, "SUPER_SEEDER", None, None)
    assert allv["Punjab"]["units"] > ss["Punjab"]["units"] > 0
    # super seeder is a Punjab product; it must not lead in Maharashtra
    assert ss["Punjab"]["units"] > ss["Maharashtra"]["units"]


def test_month_filter_scales_the_map(q):
    from api.main import _shape_values
    base = _shape_values("india", None, "SUPER_SEEDER", None, None)["Punjab"]["units"]
    nov = _shape_values("india", None, "SUPER_SEEDER", None, 11)["Punjab"]["units"]
    assert nov > base, "November should be peak for super seeders"
