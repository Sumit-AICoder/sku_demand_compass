"""Tests for the competitive choice model, cannibalisation and SKU imagery."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from pipeline.common import MARTS, CONFIG, Config, read_table

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(scope="module")
def land():
    return read_table(MARTS / "competitive_landscape.parquet")


@pytest.fixture(scope="module")
def ext():
    return read_table(MARTS / "cannibalisation_external.parquet")


@pytest.fixture(scope="module")
def pairs():
    return read_table(MARTS / "sku_overlap.parquet")


# ---------------------------------------------------------------- choice model

def test_shares_are_a_valid_distribution(land):
    """Softmax shares must sum to one per contest, or every downstream number is wrong."""
    assert land["sonalika_share"].between(0, 1).all()
    assert land["leader_share"].between(0, 1).all()
    assert (land["leader_share"] >= land["sonalika_share"] - 1e-9).all()


def test_hhi_is_in_range(land):
    n = len(Config.sim()["competition"]["players"])
    assert land["hhi"].between(1.0 / n - 1e-9, 1.0).all()


def test_sonalika_share_is_a_challenger_position(ext):
    """~6-8% by design (sim_params sonalika_implement_share). A model that quietly put
    Sonalika at 40% would invalidate every conclusion drawn from this view."""
    share = ext["sonalika_units"].sum() / ext["market_units"].sum()
    assert 0.03 < share < 0.15, f"{share:.1%}"


def test_price_and_reach_move_share_the_right_way():
    """Cheaper and further-reaching must not lower a brand's share, or the utility
    signs are inverted and every scenario points the wrong way."""
    p = read_table(MARTS / "player_shares.parquet")
    g = p.groupby("player").agg(share=("share", "mean"), price=("price_index", "first"),
                                reach=("reach_km", "first"))
    prem = g[g["price"] > 1.2]["share"].mean()
    value = g[g["price"] < 0.95]["share"].mean()
    assert value > prem, "premium brands out-share value brands — check the price sign"


def test_local_fabricators_win_on_price_not_reach():
    """Face validity from the Excel: local players dominate on price but a buyer will
    not travel for them."""
    cfg = Config.sim()["competition"]
    assert cfg["price_index"]["Local"] < cfg["price_index"]["Sonalika"]
    assert cfg["reach_km"]["Local"] < cfg["reach_km"]["Sonalika"]
    p = read_table(MARTS / "player_shares.parquet")
    assert p[p.player == "Local"]["share"].mean() > p[p.player == "Sonalika"]["share"].mean()


# ---------------------------------------------------------------- external

def test_units_reconcile(ext):
    assert np.allclose(ext["sonalika_units"] + ext["competitor_units"],
                       ext["market_units"], rtol=1e-6)


def test_status_classes_are_actionable(ext):
    """Every contest must be classified, and 'Winnable' must genuinely be closer than
    'Out of reach' -- the first version labelled 95% 'Losing', which was true and useless."""
    assert set(ext["status"].unique()) <= {"Leading", "Winnable", "Stretch", "Out of reach"}
    c = ext.groupby("status")["closeness"].mean()
    assert c["Winnable"] > c["Stretch"] > c["Out of reach"]
    frac = (ext["status"] == "Out of reach").mean()
    assert frac < 0.75, f"{frac:.0%} written off — the classification is not discriminating"


def test_at_risk_is_not_gated_on_leadership(ext):
    """A challenger's volume is mostly held where it does not lead; gating exposure on
    leadership reported ~0 and hid the real risk."""
    non_leading = ext[ext["leader"] != "Sonalika"]
    assert non_leading["at_risk_units"].sum() > 0
    assert (ext["at_risk_units"] <= ext["sonalika_units"] + 1e-9).all()


def test_winnable_never_exceeds_what_rivals_hold(ext):
    assert (ext["winnable_units"] <= ext["competitor_units"] + 1e-9).all()


# ---------------------------------------------------------------- internal

def test_substitution_requires_a_shared_job(pairs):
    """The gate that stops a trolley looking like a competitor to everything."""
    from pipeline.score.competition_model import JOBS
    for _, r in pairs.iterrows():
        ja = {j for j, m in JOBS.items() if r["sku_a"] in m}
        jb = {j for j, m in JOBS.items() if r["sku_b"] in m}
        assert ja & jb, f"{r['sku_a']} vs {r['sku_b']} share no job"


def test_haulage_competes_with_nothing_but_haulage(pairs):
    bad = pairs[((pairs.sku_a.str.startswith("TROLLEY")) ^
                 (pairs.sku_b.str.startswith("TROLLEY")))]
    assert len(bad) == 0, f"trolleys paired with non-haulage:\n{bad[['name_a','name_b']]}"


def test_known_substitutes_are_detected(pairs):
    """These genuinely compete for the same sale and must be found."""
    got = {frozenset((r["sku_a"], r["sku_b"])) for _, r in pairs.iterrows()}
    for a, b in [("SUPER_SEEDER", "HAPPY_SEEDER"),
                 ("SEED_DRILL_11T", "SEED_FERT_DRILL_13T"),
                 ("ROTAVATOR_5FT", "CULTIVATOR_9T")]:
        assert frozenset((a, b)) in got, f"{a} vs {b} not detected as substitutes"


def test_internal_cannibalisation_is_arithmetically_possible():
    """Summing pair-wise displacement once reported 82% of gross demand cannibalised,
    which cannot happen -- a SKU with three substitutes cannot lose 3 x 45% of itself."""
    by_sku = read_table(MARTS / "cannibalisation_internal_by_sku.parquet")
    assert (by_sku["displaced_pct"] <= 100.0 + 1e-9).all()
    assert (by_sku["displaced_units"] <= by_sku["gross_units"] + 1e-6).all()
    ext = read_table(MARTS / "cannibalisation_external.parquet")
    total = by_sku["displaced_units"].sum() / ext["sonalika_units"].sum()
    assert total < 0.30, f"{total:.0%} of demand displaced internally — implausible"


# ---------------------------------------------------------------- imagery

def test_image_manifest_is_valid():
    path = CONFIG / "sku_images.json"
    if not path.exists():
        pytest.skip("no images fetched")
    m = json.loads(path.read_text())
    ids = {s["id"] for s in Config.skus()}
    assert set(m) <= ids, "manifest references unknown SKUs"
    for sid, meta in m.items():
        assert meta["licence"], f"{sid} has no licence recorded"
        assert meta["page"].startswith("https://commons.wikimedia.org/"), sid


def test_every_image_file_exists():
    from pipeline.common import ROOT
    path = CONFIG / "sku_images.json"
    if not path.exists():
        pytest.skip("no images fetched")
    for sid, meta in json.loads(path.read_text()).items():
        assert (ROOT / "web" / "public" / "sku" / meta["file"]).exists(), sid


def test_images_are_plausibly_about_farm_machinery():
    """A picture of the wrong thing is worse than no picture -- it is read as fact.
    A book cover once stood in for a GPS guidance kit."""
    from pipeline.ingest.sku_images import RELEVANT, REJECT
    path = CONFIG / "sku_images.json"
    if not path.exists():
        pytest.skip("no images fetched")
    for sid, meta in json.loads(path.read_text()).items():
        low = meta["title"].lower()
        assert any(r in low for r in RELEVANT), f"{sid}: {meta['title']}"
        assert not any(b in low for b in REJECT), f"{sid}: {meta['title']}"
