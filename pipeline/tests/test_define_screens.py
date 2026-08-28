"""The Define stage: the taxonomy, the merged profile panel, and what Configure can do to them.

Define decides what an archetype *is*, so every other stage inherits whatever these tests
let through. Three things are worth guarding:

  1. the taxonomy produces the archetypes the client was shown, with terciles that are
     really terciles and one name per zone;
  2. editing it -- adding a tier, moving a belt bound, merging or deleting a crop category
     -- moves the archetypes and keeps every downstream stage on the same set, which is the
     bug the old Configure had (it relabelled rows into an id nothing could join to);
  3. demand is off the Define screens and still on the Plan and Act ones.
"""
from __future__ import annotations

import copy

import pandas as pd
import pytest

from pipeline.cluster import taxonomy as tx
from pipeline.common import MARTS, read_table

SHIPPED = tx.load()


@pytest.fixture(scope="module")
def mm() -> pd.DataFrame:
    return read_table(MARTS / "micromarkets.parquet")


@pytest.fixture(scope="module")
def api():
    from api import main
    return main


# ---------------------------------------------------------------- the taxonomy itself

def test_the_shipped_taxonomy_is_valid():
    assert tx.validate(SHIPPED) == []


def test_every_zone_has_exactly_one_name(mm):
    """Six zone ids carried eight names before this, which is what made a zone-keyed
    archetype ambiguous -- zone 4 was both Northern Plain and Central Highlands."""
    assert mm.groupby("zone")["zone_name"].nunique().max() == 1


def test_every_subzone_belongs_to_exactly_one_zone():
    seen: dict[str, str] = {}
    for z in SHIPPED["zones"]:
        for sz in z["subzones"]:
            assert sz not in seen, f"{sz} is in zone {seen[sz]} and zone {z['id']}"
            seen[sz] = str(z["id"])
    assert len(seen) == 15


def test_tiv_tiers_are_terciles(mm):
    """Three tiers, asked for by name -- high, medium, low -- and actually the same size.
    A quantile cut lands between ranks, so exact thirds are not achievable; within a
    percent of each other is."""
    n = mm["tiv_tier"].value_counts()
    assert set(n.index) == {"Low", "Medium", "High"}
    assert (n.max() - n.min()) / len(mm) < 0.01, n.to_dict()


def test_the_archetype_count_matches_what_the_screen_claims(mm):
    """46, not the 7 x 3 x 4 = 84 the axes allow: no district in the pilot has a mean HP
    under 30, so that belt is empty, and a few zone x tier x belt cells have no members."""
    assert mm["archetype_id"].nunique() == 46
    assert mm["archetype_id"].str.count(r"\|").eq(2).all()


def test_the_archetype_name_says_the_crop_that_is_actually_grown(mm):
    """The name and the Most-grown column are one fact, not two. Naming by a z-scored
    'distinctive' crop produced rows called Cotton High-TIV whose most-grown was sugarcane,
    and no reader could tell which of the two was the crop in the ground."""
    for aid, g in mm.groupby("archetype_id"):
        assert g["crop_label"].nunique() == 1
        assert g["crop_label"].iloc[0].lower() == g["dominant_crop"].mode().iloc[0].lower()


def test_hp_belts_partition_mean_hp(mm):
    """Every micro-market lands in exactly one belt, and the belt agrees with its mean HP."""
    bounds = {b["name"]: b["upto"] for b in SHIPPED["hp_belts"]}
    assert mm["hp_belt"].isin(bounds).all()
    for name, upto in bounds.items():
        g = mm[mm["hp_belt"] == name]
        if len(g) and upto is not None:
            assert g["mean_hp"].max() <= upto + 1e-9


# ---------------------------------------------------------------- editing it

def _relabel(tax: dict, mm: pd.DataFrame) -> pd.DataFrame:
    return tx.assign(mm, tax)


def test_merging_two_crops_into_one_category_renames_their_archetypes(mm):
    """The client's 'edit the dominant-crop categories': put wheat, rice and maize under one
    Cereals row and every archetype they named is now a Cereals archetype. The count is
    untouched -- crop names the archetype, it is not one of its axes."""
    tax = copy.deepcopy(SHIPPED)
    tax["crops"] = [c for c in tax["crops"] if c["name"] not in ("Wheat", "Rice", "Maize")]
    tax["crops"].append({"name": "Cereals", "values": ["wheat", "rice", "maize"]})

    out = _relabel(tax, mm)
    assert tx.validate(tax) == []
    assert out["archetype_id"].nunique() == mm["archetype_id"].nunique()
    assert "Cereals" in set(out["crop_label"])
    assert not {"Wheat", "Rice", "Maize"} & set(out["crop_label"])
    moved = mm["dominant_crop"].isin(["wheat", "rice", "maize"])
    assert (out.loc[moved & (out["crop_label"] == "Cereals"), "archetype"]
            .str.startswith("Cereals").all())


def test_deleting_a_crop_category_falls_through_to_the_next_biggest(mm):
    """Deleting a category stops it naming anything. The archetypes it named take their
    next-biggest crop -- they do not disappear, and they are not left blank."""
    tax = copy.deepcopy(SHIPPED)
    before = _relabel(tax, mm)
    named_wheat = set(before.loc[before["crop_label"] == "Wheat", "archetype_id"])
    assert named_wheat, "nothing was named Wheat to begin with"

    tax["crops"] = [c for c in tax["crops"] if c["name"] != "Wheat"]
    out = _relabel(tax, mm)
    assert out["archetype_id"].nunique() == mm["archetype_id"].nunique()
    assert "Wheat" not in set(out["crop_label"])
    still = out[out["archetype_id"].isin(named_wheat)]
    assert still["crop_label"].notna().all() and (still["crop_label"] != "").all()


def test_a_crop_in_two_categories_is_rejected():
    """Which category names the archetype would otherwise depend on dict order."""
    tax = copy.deepcopy(SHIPPED)
    tax["crops"].append({"name": "Grains", "values": ["wheat"]})
    assert any("wheat" in e for e in tx.validate(tax))


def test_zones_are_not_client_editable(api):
    """Zones are the published ICAR scheme and the profile panel's soil, climate and
    growing-season figures are measured against those boundaries. The API pins them, so a
    PUT that redraws one is ignored rather than silently accepted."""
    tax = copy.deepcopy(SHIPPED)
    tax["zones"] = [{"id": "ONE", "name": "All of it",
                     "subzones": sorted(sz for z in SHIPPED["zones"] for sz in z["subzones"])}]
    try:
        got = api.taxonomy_put(api.Taxonomy(**tax))
        assert got["taxonomy"]["zones"] == SHIPPED["zones"]
        assert got["n_archetypes"] == 46, "a pinned zone set still changed the archetypes"
    finally:
        api.taxonomy_reset()


def test_adding_a_tier_changes_the_bands_not_the_membership(mm):
    """Four tiers instead of three: every micro-market is still in exactly one, and the
    cuts still cover the whole population."""
    tax = copy.deepcopy(SHIPPED)
    tax["tiv_tiers"] = [{"name": "Low", "code": "L", "upto": 0.25},
                        {"name": "Lower-mid", "code": "LM", "upto": 0.5},
                        {"name": "Upper-mid", "code": "UM", "upto": 0.75},
                        {"name": "High", "code": "H", "upto": 1.0}]
    out = _relabel(tax, mm)
    assert set(out["tiv_tier"]) == {"Low", "Lower-mid", "Upper-mid", "High"}
    assert out["tiv_tier"].notna().all()
    n = out["tiv_tier"].value_counts()
    assert (n.max() - n.min()) / len(mm) < 0.01, n.to_dict()


def test_a_taxonomy_that_cannot_band_everything_is_rejected():
    """Tiers that stop short of 1.0 would leave the top of the fleet in no tier at all, and
    two zones sharing an id would make an archetype key ambiguous."""
    tax = copy.deepcopy(SHIPPED)
    tax["tiv_tiers"] = [{"name": "Only", "upto": 0.5}]          # never reaches 1.0
    assert any("1.0" in e for e in tx.validate(tax))

    dup = copy.deepcopy(SHIPPED)
    dup["zones"].append({"id": "4", "name": "Clash", "subzones": ["6.1"]})
    assert any("id" in e for e in tx.validate(dup))


# ---------------------------------------------------------------- what the screens serve

def test_the_rollup_is_the_same_function_the_pipeline_used():
    """The API re-rolls archetype operations when the taxonomy is edited. If that drifted
    from the pipeline's own rollup, Plan would quietly disagree with the mart."""
    from pipeline.simulate.operations import rollup
    mm = read_table(MARTS / "micromarket_ops.parquet")
    got = rollup(mm[mm["product_line"] == "implements"])
    want = read_table(MARTS / "archetype_ops.parquet")
    want = want[want["product_line"] == "implements"].drop(columns="product_line")
    key = "archetype_id"
    assert got.sort_values(key).reset_index(drop=True).equals(
        want.sort_values(key).reset_index(drop=True))


def test_define_payloads_carry_no_demand(api):
    """Define describes the market; demand is the ranking Plan chooses with, and moving it
    off these tables was the client's call. It stays everywhere else."""
    arch = api.archetypes()
    assert not any("demand" in k or "potential" in k for k in arch["archetypes"][0])
    assert "potential_units_yr" not in arch["totals"]

    districts = api.define_districts()["districts"][0]
    assert not any("demand" in k or "potential" in k for k in districts)


def test_plan_and_act_still_carry_demand(api):
    """The other half of the same decision: nothing above lost the column."""
    plan = api.plan_buckets()
    assert plan["archetypes"][0]["potential_units_yr"] > 0
    act = api.act_summary(archetype_id=plan["archetypes"][0]["archetype_id"])
    assert act["size"]["demand_units"] > 0


def test_every_stage_reports_the_same_archetypes(api):
    """Define, Review and Plan must never disagree about how many archetypes exist -- the
    reason Plan re-rolls from micro-market grain instead of reading the mart."""
    n = api.archetypes()["totals"]["n_archetypes"]
    assert len(api.plan_buckets()["archetypes"]) == n
    assert len(api.review_archetypes()["archetypes"]) == n


@pytest.mark.parametrize("level,ident", [("district", "03002"), ("micromarket", "03002M0006")])
def test_the_profile_panel_is_filled_at_both_grains(api, level, ident):
    """One panel, two grains. Villages and fleet are the panel's own; soil and rainfall are
    district measurements a micro-market inherits, and the payload says which is which."""
    d = api.define_profile(level=level, id=ident)
    assert d["scope"]["villages"] > 0
    assert d["scope"]["tiv"] > 0
    assert d["scope"]["hp_belt"] and d["scope"]["tiv_tier"]
    assert d["soil"]["soil_type"], "the AESR join left a district with no soil class"
    assert d["agro"]["rain_normal_mm"] > 0
    assert d["provenance"]["grain"]
    if level == "micromarket":
        assert d["scope"]["micromarkets"] == 1
        assert d["scope"]["dealer_km"] is not None, "no dealer count exists at this grain"
