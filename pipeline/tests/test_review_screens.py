"""The Review stage: what the funnel is allowed to claim, and what the panel may call real.

Review is where a modelled number is likeliest to be mistaken for a measured one. The BD
funnel, the coverage indices and the village demographics are all simulated or allocated,
and they sit on the same panel as real ICAR soil and real dealer counts. Four things are
worth guarding:

  1. the funnel's arithmetic -- `deliveries_yr` and `sonalika_sales_units` are one column
     under two names, so a screen that draws both invents a stage converting at 100%;
  2. what each bucket claims about itself, because "sales coverage is real · dealer locator"
     was on screen while the number behind it was a decay off a *simulated* dealer network;
  3. that one competitor answer is given everywhere -- two tables can answer it and they
     disagree on 90 of 114 districts;
  4. that a Configure re-cluster reaches these screens at all. Three Review endpoints read
     the raw mart and served pre-edit labels beside post-edit rollups.
"""
from __future__ import annotations

import copy

import pandas as pd
import pytest

from pipeline.common import MARTS, read_table

WON = 0.10          # the share at which a micro-market counts as won, per operations.py


@pytest.fixture(scope="module")
def ops() -> pd.DataFrame:
    # Implements only: these marts carry both product lines now, and every figure
    # asserted below is an implements one. A tractor row summed into them would make
    # the assertion wrong in a way that still looks like a plausible number.
    d = read_table(MARTS / "micromarket_ops.parquet")
    return d[d["product_line"] == "implements"]


@pytest.fixture(scope="module")
def arch() -> pd.DataFrame:
    # Implements only: these marts carry both product lines now, and every figure
    # asserted below is an implements one. A tractor row summed into them would make
    # the assertion wrong in a way that still looks like a plausible number.
    d = read_table(MARTS / "archetype_ops.parquet")
    return d[d["product_line"] == "implements"]


@pytest.fixture(scope="module")
def api():
    from api import main
    return main


# ---------------------------------------------------------------- the funnel

def test_deliveries_and_sales_are_the_same_column(ops):
    """One number under two names (`operations.py:116` assigns one from the other).

    A funnel drawn as Activities -> Enquiries -> Deliveries -> Sales therefore shows a final
    stage converting at exactly 100%, which is not a finding, it is the same column twice.
    This is why the Review panel has three bars and calls the last one the sale.
    """
    assert (ops["deliveries_yr"] == ops["sonalika_sales_units"]).all()


def test_the_archetype_rollup_disagrees_with_itself_by_a_known_amount(arch):
    """The same two columns are NOT equal one grain up, and the gap is 13%, not rounding.

    `rollup()` sums the per-micro-market integer for `deliveries_yr` but re-derives
    `sonalika_sales_units` from the unrounded `share x demand`. Mean sales per micro-market
    is 0.75, so most of them round to 0 or 1 and summing the integers loses an eighth of the
    total. The archetype table shows the second number as "Sales" and the panel beside it
    shows the first as "Deliveries".

    This is upstream of Review and moving it would move Plan and Act too, so it is bounded
    and documented here rather than silently corrected. If someone fixes `rollup()` to use
    one value for both, this test is the one that should be deleted.
    """
    gap = (arch["sonalika_sales_units"] - arch["deliveries_yr"]).abs().sum()
    pct = gap / arch["deliveries_yr"].sum()
    assert 0.10 < pct < 0.20, f"the known gap moved to {pct:.1%} -- was it fixed, or did it grow?"


def test_the_funnel_narrows_at_every_grain(ops, arch, api):
    """A stage that is bigger than the one before it means the rollup divided a numerator by
    the wrong denominator -- the failure that would make a conversion rate read over 100%."""
    for df in (ops, arch):
        assert (df["activities_yr"] >= df["enquiries_yr"]).all()
        assert (df["enquiries_yr"] >= df["deliveries_yr"]).all()
    d = api.review_profile(level="district", id="03002")["sales"]
    assert d["activities"] >= d["enquiries"] >= d["deliveries"]
    assert 0 < d["enquiry_rate"] <= 1 and 0 < d["conversion_rate"] <= 1


def test_market_share_is_sales_over_demand(api):
    """The panel prints sales, demand and share side by side. If share were a TIV-weighted
    mean of the stored per-micro-market shares instead, the three numbers a reader can see
    would not divide into each other -- which is the kind of thing a client checks."""
    for level, ident in (("district", "27011"), ("micromarket", "03002M0006")):
        s = api.review_profile(level=level, id=ident)["sales"]
        # To the precision the panel prints: sales and demand are shown rounded, so the
        # check is that a reader dividing what they can see lands on the share shown.
        assert abs(s["share"] - s["sales_units"] / s["demand"]) < 5e-4


def test_the_district_rollup_conserves_the_micro_market_totals(ops, api):
    """There is no district-grain funnel mart; this groupby is the mart. If it drops a
    micro-market the screen quietly under-reports and nothing else notices."""
    grain = api._current_grain()
    assert grain.groupby("district_id").ngroups == 114
    for col in ("activities_yr", "enquiries_yr", "deliveries_yr", "potential_units_yr", "tiv"):
        assert abs(grain[col].sum() - ops[col].sum()) < 1e-6


# ---------------------------------------------------------------- the profile panel

@pytest.mark.parametrize("level,ident", [("district", "03002"), ("micromarket", "03002M0006")])
def test_the_three_buckets_are_filled_at_both_grains(api, level, ident):
    """One panel, three sources, three grains -- the screen is only worth having if all
    three arrive. Demographics are the fragile one: they exist only at village grain and
    have to be rolled up through two different join paths to get here."""
    d = api.review_profile(level=level, id=ident)
    assert d["sales"]["demand"] > 0 and d["sales"]["tiv"] > 0
    assert d["demographics"]["population"] > 0
    assert d["demographics"]["households"] > 0
    assert d["demographics"]["avg_holding_ha"] > 0
    assert d["soil"]["soil_type"], "the AESR join left a place with no soil class"
    assert d["agro"]["rain_normal_mm"] > 0
    assert d["competitor"]["rival"] not in (None, "Local", "Sonalika")
    assert d["provenance"]["grain"]


def test_demographics_cover_every_micro_market(api):
    """The rollup joins village_features through village_micromarket. A village that belongs
    to no micro-market, or a micro-market whose villages are missing, would show a panel of
    zeros rather than an error -- so the coverage is asserted, not assumed."""
    demo = api._demographics()
    grain = api._current_grain()
    missing = set(grain["micro_market_id"]) - set(demo.index)
    assert not missing, f"{len(missing)} micro-markets have no demographics"
    assert (demo["population"] > 0).all()


def test_income_per_ha_never_reaches_the_panel(api):
    """`features/build.py` divides a per-HOLDING farm income by the whole village's sown
    area, so the stored income_per_ha is out by a factor of n_holdings -- about 176x, which
    is a grain mismatch rather than a rounding one. Until that is fixed upstream it must not
    be on a client screen, and farm income per holding is shown instead."""
    d = api.review_profile(level="district", id="03002")
    assert "income_per_ha" not in d["demographics"]
    assert d["demographics"]["farm_income_inr"] > 10_000


def test_demand_is_still_on_the_review_payloads(api):
    """The mirror of Define's `test_define_payloads_carry_no_demand`. Demand came off Define
    deliberately; it stays here because share is sales divided by it and the funnel is sized
    off it. The same grep that must find nothing there must find something here."""
    assert api.review_profile(level="district", id="03002")["sales"]["demand"] > 0
    assert api.review_archetypes()["totals"]["demand"] > 0


# ---------------------------------------------------------------- the two crop systems

def test_the_des_columns_are_still_foodgrain_only():
    """Pins the fact the panel's wording rests on: the DES extract behind agroclimate covers
    foodgrains, so cotton, soybean, sugarcane and groundnut are zero on all 114 districts
    while the modelled dominant_crop says cotton. If a fuller extract ever lands, this fails
    and the 'foodgrains only' caveat comes off the screen -- rather than quietly lying in the
    other direction."""
    a = read_table(MARTS / "agroclimate.parquet")
    for c in ("cotton", "soybean", "sugarcane", "groundnut"):
        assert a[f"crop_{c}_share"].max() == 0


def test_a_zero_crop_share_never_reaches_the_panel(api):
    """A real 0% cotton printed three lines above 'Most-grown: cotton' reads as a broken
    screen, not as two sources. The panel selects only crops the source reports."""
    for level, ident in (("micromarket", "03002M0006"), ("district", "27011")):
        d = api.review_profile(level=level, id=ident)
        zeros = [k for k, v in d["agro"].items() if k.startswith("crop_") and (v or 0) == 0]
        assert not zeros, f"{ident} would draw an empty bar for {zeros}"
        assert d["dominant_crop"], "the modelled crop line is what covers non-foodgrains"


# ---------------------------------------------------------------- diagnosis

def test_the_diagnosis_split_is_what_the_screen_explains(arch):
    """The screen carries a paragraph saying why there is no Defend card. When real ITL share
    data pushes an archetype past 10% the rule fires, this fails, and the paragraph gets
    deleted rather than left standing beside a Defend card."""
    n = arch["diagnosis"].value_counts().to_dict()
    assert n == {"Sales issue": 31, "Product issue": 10, "Monitor": 5}
    assert arch["avg_sonalika_share"].max() < WON


def test_monitor_is_the_demand_rule_and_nothing_else(ops, arch):
    """The client asked what Monitor means. This pins the answer the tooltip gives: too
    little demand for its size -- not a product problem and not a selling problem."""
    lo = ops["potential_units_yr"].quantile(0.20)
    mon = arch[arch["diagnosis"] == "Monitor"]
    assert len(mon)
    assert (mon["potential_units_yr"] < lo * mon["n_micromarkets"]).all()
    assert (mon["product_fit"] >= 0.48).all(), "then it would be a product issue, not Monitor"


def test_percent_of_mm_won_is_the_ten_percent_bar(ops, arch):
    """The archetype map colours a micro-market green at 10% share and the column beside it
    counts the same thing, so the green fraction of the map IS the column. If they used
    different bars the map would silently contradict the number next to it."""
    got = ops.assign(won=ops["sonalika_share"] >= WON).groupby("archetype_id")["won"].mean()
    want = arch.set_index("archetype_id")["cracked_pct"]
    assert (got.round(3) - want).abs().max() < 1e-9


# ---------------------------------------------------------------- coverage

def test_coverage_provenance_does_not_call_the_index_real(api):
    """"Sales coverage is real · dealer locator" was on screen, and the number behind it is
    exp(-distance to a *simulated* dealer / decay). Only the counts are real."""
    d = api.review_coverage(type="sales")
    assert "real" not in d["provenance"]["coverage"]
    assert "real" in d["provenance"]["dealers"]
    assert "pending" in api.review_coverage(type="service")["provenance"]["coverage"]


def test_service_distance_carries_no_information(ops):
    """service_distance_km is a deterministic inverse of service_index. Showing both is one
    fact twice, which is why the panel shows one."""
    expected = ((1 - ops["service_index"]) * 35 + 4).round(1)
    assert (ops["service_distance_km"] - expected).abs().max() < 0.051


def test_every_archetype_names_a_branded_competitor(api):
    """Local leads every archetype, so a leader column would read identically on all 46 rows.
    The column earns its space only because it excludes the unbranded segment."""
    rows = api.review_coverage()["archetypes"]
    assert len(rows) == 46
    assert all(r["rival"] not in (None, "Local", "Sonalika") for r in rows)


def test_review_and_define_name_the_same_competitor(api):
    """Two tables can answer this and they agree on only 24 of 114 districts. One source, or
    the client sees Landforce on one tab and Shaktiman on another for the same place."""
    define = {r["archetype_id"]: r["rival"] for r in api.archetypes()["archetypes"]}
    for r in api.review_coverage()["archetypes"]:
        assert r["rival"] == define[r["archetype_id"]]


def test_a_district_with_no_dealer_rows_is_not_a_district_with_no_dealers(api):
    """The implements dealer file has no Punjab rows at all. Colouring that as 0% coverage
    would claim an absence we cannot see, so the map greys it and the flag is what drives
    that -- every Punjab district must carry it."""
    rows = {r["district_id"]: r for r in api.review_coverage()["districts"]}
    punjab = [r for r in rows.values() if r["state"] == "Punjab"]
    assert len(punjab) == 23
    assert all(r["has_dealer_data"] is False for r in punjab)
    assert all(r["coverage"] is not None for r in punjab), "coverage is modelled, so it exists"


def test_every_mapped_district_has_a_coordinate(api):
    """The coverage map plots district centroids; a null drops a district off the map with no
    error anywhere."""
    rows = api.review_coverage()["districts"]
    assert len(rows) == 114
    assert all(r["lon"] and r["lat"] for r in rows)


# ---------------------------------------------------------------- the re-cluster path

def test_a_configure_edit_reaches_the_review_screens(api):
    """Three Review endpoints read micromarket_ops raw, so an archetype the client created on
    Configure appeared in Review's table with no micro-markets on its map -- the old Configure
    bug, one stage downstream. Edit the taxonomy, then ask Review for something only the edit
    produced.
    """
    from pipeline.cluster import taxonomy as tx
    tax = copy.deepcopy(tx.load())
    tax["crops"] = [c for c in tax["crops"] if c["name"] not in ("Wheat", "Rice")]
    tax["crops"].append({"name": "Cereals", "values": ["wheat", "rice"]})
    try:
        api.taxonomy_put(api.Taxonomy(**tax))
        aid = next(r["archetype_id"] for r in api.review_archetypes()["archetypes"]
                   if r["base_name"].startswith("Cereals"))

        mms = api.review_micromarkets(archetype_id=aid)["micromarkets"]
        assert mms, "Review's map has no points for an archetype Review's own table lists"
        one = api.review_micromarket(mms[0]["micro_market_id"])["micromarket"]
        assert one["archetype_id"] == aid, "the detail panel is still on the shipped taxonomy"

        cov = {r["archetype_id"]: r for r in api.review_coverage()["archetypes"]}
        assert aid in cov and cov[aid]["pct_covered"] is not None
        assert cov[aid]["rival"], "the rival cache outlived the re-cluster"

        prof = api.review_profile(level="micromarket", id=mms[0]["micro_market_id"])
        assert prof["sales"]["demand"] > 0 and prof["demographics"]["population"] > 0
    finally:
        api.taxonomy_reset()
