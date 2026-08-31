"""The Act playbook: what has to stay true after the seven-use-case rebuild.

The existing suite (`pipeline/tests/test_act_screens.py`) already pins the arithmetic the
plays inherited. These tests cover what the rebuild newly makes possible to get wrong:
re-homing six plays across seven cards without double-counting, narrowing the scope to a
district or a single micro-market, and modelling a survey that has not been run.

Assert-based and fixture-free on purpose -- the whole thing is one FastAPI process reading
parquet, and a fixture layer would be more machinery than the checks are worth.
"""
from __future__ import annotations

import pytest

from api import playbook as P
from api.main import _current_grain, _plan_buckets


def _req(aid, **kw):
    return P.PlaybookReq(archetype_id=aid, **kw)


@pytest.fixture(scope="module")
def buckets():
    return _plan_buckets()


@pytest.fixture(scope="module")
def one_per_bucket(buckets):
    return {b: buckets[buckets["bucket"] == b].iloc[0]["archetype_id"]
            for b in ("Defend", "Grow", "No product fit")
            if len(buckets[buckets["bucket"] == b])}


@pytest.fixture(scope="module")
def grow(one_per_bucket):
    return P.build(_req(one_per_bucket["Grow"]))


# ------------------------------------------------------------------ the survey

def test_survey_percentiles_are_percentiles(grow):
    """Every modelled score is a 0-100 position against the national village. A score
    outside that range means a mapping is dividing by the wrong thing."""
    s = grow["survey"]
    for d in s["purchase_drivers"]:
        assert 0 <= d["score"] <= 100, d
        assert d["evidence"], "a modelled driver with no evidence is an assertion"
    p = s["perception"]
    assert 0 <= p["satisfied_pct"] <= 100 and 0 <= p["detractor_pct"] <= 100
    assert p["satisfied_pct"] + p["detractor_pct"] + p["neutral_pct"] == 100
    cm = s["channel_mix"]
    assert cm["digital_pct"] + cm["btl_pct"] + cm["dealer_pct"] == 100


def test_survey_is_deterministic(one_per_bucket):
    """Modelled, not simulated: two calls must agree, or nobody can act on it."""
    a = P.build(_req(one_per_bucket["Grow"]))["survey"]
    b = P.build(_req(one_per_bucket["Grow"]))["survey"]
    assert [d["driver"] for d in a["purchase_drivers"]] == \
           [d["driver"] for d in b["purchase_drivers"]]
    assert a["perception"]["top_complaint"] == b["perception"]["top_complaint"]
    assert a["top_barrier"] == b["top_barrier"]


def test_top_barrier_is_derived_but_overridable(one_per_bucket):
    aid = one_per_bucket["Grow"]
    modelled = P.build(_req(aid))
    assert modelled["survey"]["barrier_source"] == "modelled from this scope's villages"
    forced = P.build(_req(aid, assumptions=P.Assumptions(top_barrier="product")))
    assert forced["survey"]["top_barrier"] == "product"
    assert forced["survey"]["barrier_source"] == "your override"
    # The override moves what you read first and nothing else.
    assert {p["play"]: p["units"] for p in forced["plays"]} == \
           {p["play"]: p["units"] for p in modelled["plays"]}


# ------------------------------------------------------------------ the seven cards

def test_every_play_lands_in_exactly_one_card(grow):
    """The re-homing must partition the plays, not copy them. If a play appeared in two
    cards the page would read as if it were worth twice what it is."""
    seen = [p["play"] for c in grow["cards"] for p in c["plays"]]
    assert sorted(seen) == sorted(p["play"] for p in grow["plays"])
    assert len(seen) == len(set(seen))


def test_cards_sum_to_the_total(grow):
    """Card units must add to the growth total -- that is the whole addability claim,
    restated at card grain."""
    assert sum(c["units"] for c in grow["cards"]) == grow["total"]["raw_units"]
    assert grow["total"]["capped_units"] <= grow["total"]["headroom"]


def test_customer_and_inventory_carry_no_addend(grow):
    """Cards 2 and 4 allocate and aim volume the other cards create. Giving either one a
    units figure would double-count the plays it is allocating."""
    for key in ("customer", "inventory"):
        card = next(c for c in grow["cards"] if c["key"] == key)
        assert card["units"] == 0 and not card["plays"]


def test_all_seven_cards_are_present_and_populated(grow):
    keys = [c["key"] for c in grow["cards"]]
    assert sorted(keys) == sorted(["network", "customer", "product", "inventory",
                                   "activity", "sales", "incentives"])
    for c in grow["cards"]:
        assert c["sections"], f"{c['key']} has no sections"
        for s in c["sections"]:
            assert s["kind"] in ("table", "facts", "list")
            assert s["title"], f"{c['key']} has an unnamed section"
            # A section may legitimately have no rows -- no coverage gaps, no dealer file for
            # the state -- but it must then say why. A blank table is the one thing that
            # cannot be allowed, because "no data" and "no problem" look identical as one.
            assert (s.get("rows") or s.get("items") or s.get("empty")), \
                f"{c['key']} / {s['title']} is empty and does not say why"


def test_every_play_says_how_and_what_to_watch(grow):
    for p in grow["plays"]:
        ex = p["execution"]
        assert ex["objective"] and ex["why"] and ex["how"]
        assert all(step["what"] and step["when"] for step in ex["how"])
        assert ex["kpi"]["metric"] and ex["kpi"]["by_when"]
        assert ex["owner"]


# ------------------------------------------------------------------ scope

def test_narrowing_the_scope_narrows_everything(one_per_bucket):
    """A district is smaller than its archetype and a micro-market smaller than its
    district -- in demand, in plays and in the stock the inventory card asks you to hold.
    This is the check that catches an archetype-grain number leaking into a narrow scope,
    which is exactly how the price play and the SKU basket were wrong before scaling."""
    aid = one_per_bucket["Grow"]
    g = _current_grain()[lambda d: d.archetype_id == aid]
    district = g.groupby("district_id")["potential_units_yr"].sum().idxmax()
    mm = g.sort_values("potential_units_yr", ascending=False).micro_market_id.iloc[0]

    whole = P.build(_req(aid))
    dist = P.build(_req(aid, district_id=district))
    one = P.build(_req(aid, micro_market_id=mm))

    assert whole["scope"]["level"] == "archetype"
    assert dist["scope"]["level"] == "district"
    assert one["scope"]["level"] == "micro-market"
    assert one["scope"]["micromarkets"] == 1

    for a, b in ((whole, dist), (dist, one)):
        assert b["situation"]["demand"] < a["situation"]["demand"]
        assert b["scope"]["micromarkets"] < a["scope"]["micromarkets"]
        assert b["total"]["raw_units"] <= a["total"]["raw_units"]
        assert b["targets"]["activities"]["target"] <= a["targets"]["activities"]["target"]

    def held(r):
        card = next(c for c in r["cards"] if c["key"] == "inventory")
        return sum(x["hold"] for x in card["sections"][0]["rows"])
    assert held(one) < held(dist) < held(whole)


def test_named_places_belong_to_the_scope(one_per_bucket):
    """Every micro-market a play names has to be inside the selection -- a beat plan that
    sends an ASM to another district is worse than no beat plan."""
    aid = one_per_bucket["Grow"]
    g = _current_grain()[lambda d: d.archetype_id == aid]
    district = g.groupby("district_id")["potential_units_yr"].sum().idxmax()
    inside = set(g[g.district_id == district].micro_market_id.astype(str))

    r = P.build(_req(aid, district_id=district))
    named = {w["micro_market"] for p in r["plays"] for w in p["execution"]["where"]}
    assert named, "no play named a place"
    assert named <= inside


def test_a_stale_scope_is_refused(one_per_bucket):
    """A district id left over from a previous archetype must 404, not quietly return the
    playbook for somewhere else."""
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        P.build(_req(one_per_bucket["Grow"], district_id="not-a-district"))


# ------------------------------------------------------------------ the edge modes

def test_no_product_fit_has_one_stop_play_and_leads_with_product(one_per_bucket):
    if "No product fit" not in one_per_bucket:
        pytest.skip("no No-product-fit archetype in this dataset")
    r = P.build(_req(one_per_bucket["No product fit"]))
    assert len(r["plays"]) == 1
    assert r["plays"][0]["mode"] == "stop" and r["plays"][0]["units"] == 0
    assert r["total"]["raw_units"] == 0
    assert r["cards"][0]["key"] == "product", "the product decision must lead this scope"


def test_defend_protects_without_adding_to_growth(one_per_bucket):
    if "Defend" not in one_per_bucket:
        pytest.skip("no Defend archetype in this dataset")
    r = P.build(_req(one_per_bucket["Defend"]))
    protect = [p for p in r["plays"] if p["mode"] == "protect"]
    assert protect, "a Defend scope with nothing to hold"
    assert r["total"]["raw_units"] == sum(p["units"] for p in r["plays"]
                                          if p["mode"] == "grow")


# ------------------------------------------------------------------ product line

def test_demo_deployment_is_tractors_only(one_per_bucket):
    """`demo_activity` is a marketing-effort index, not a fleet roster. It earns a
    deployment section on the tractor line, where the OEM actually owns demo vehicles, and
    nothing on the implements line."""
    aid = one_per_bucket["Defend"]
    def sections(line):
        r = P.build(_req(aid), line)
        card = next(c for c in r["cards"] if c["key"] == "inventory")
        return [s["bullet"] for s in card["sections"]]
    assert any("demo vehicle" in s.lower() for s in sections("tractors"))
    assert not any("demo vehicle" in s.lower() for s in sections("implements"))


# ------------------------------------------------------------------ tracking and the list

def test_tracking_covers_every_card(grow):
    keys = {t["key"] for t in grow["tracking"]}
    assert keys == {c["key"] for c in grow["cards"]}
    for t in grow["tracking"]:
        assert t["metric"] and t["review_cadence"]
        assert t["actual"] is None, "actuals are ITL-pending and must not be invented"


def test_the_action_list_is_ten_real_things(grow):
    al = grow["action_list"]
    assert 1 <= len(al) <= 10
    keys = {c["key"] for c in grow["cards"]}
    assert [x["n"] for x in al] == list(range(1, len(al) + 1))
    for x in al:
        assert x["key"] in keys and x["action"] and x["when"] and x["owner"]


def test_missing_dealer_data_is_not_reported_as_zero_dealers():
    """The implements dealer file has no Punjab rows at all. A Punjab scope must say we
    cannot see the network, never that we have none -- those are opposite claims, and the
    second one sends someone to open dealers we may already have."""
    g = _current_grain()
    pj = g[(g.state == "Punjab")]
    if pj.empty:
        pytest.skip("no Punjab micro-markets in this dataset")
    aid = pj.archetype_id.iloc[0]
    district = pj[pj.archetype_id == aid].district_id.iloc[0]
    r = P.build(_req(aid, district_id=district))
    net = next(c for c in r["cards"] if c["key"] == "network")
    assert "no dealer list" in net["summary"].lower()
    assert "0 dealers" not in net["summary"]
    for s in net["sections"]:
        if not (s.get("rows") or s.get("items")):
            assert "punjab" in s["empty"].lower() or "dealer list" in s["empty"].lower(), \
                f"{s['title']} goes blank without saying the data is missing"


def test_no_product_fit_action_list_is_a_product_decision():
    """A scope where the machine does not suit the land must not be handed a selling plan,
    and must never print an action whose number is zero."""
    b = _plan_buckets()
    rows = b[b["bucket"] == "No product fit"]
    if rows.empty:
        pytest.skip("no No-product-fit archetype in this dataset")
    r = P.build(_req(rows.iloc[0]["archetype_id"]))
    keys = {x["key"] for x in r["action_list"]}
    assert "product" in keys
    assert not (keys & {"activity", "incentives"}), \
        "a no-product-fit scope was handed a selling plan"
    for x in r["action_list"]:
        assert " 0 " not in f" {x['action']} ", f"zero-valued action printed: {x['action']}"


def test_authored_content_is_not_badged_as_modelled(grow):
    """`modelled` means "computed from real inputs". The activity-type split, the training
    list and the incentive structure are none of that -- there is no activity-type column
    anywhere in the marts, only a total count -- so they must carry `judgement` and say so.
    Badging a rule of thumb as modelled is how a tool loses the right to be trusted on the
    numbers that ARE modelled."""
    authored = {"What kind of activities, and how many", "Building the brand for the long run",
                "What to train them on", "Incentives that pay for effort", "People needed"}
    found = set()
    for c in grow["cards"]:
        for s in c["sections"]:
            if s["title"] in authored:
                found.add(s["title"])
                assert s["provenance"] == P.JUDGEMENT, \
                    f"{s['title']} is authored but badged {s['provenance']}"
                assert s["note"], f"{s['title']} does not say it is a rule of thumb"
    assert found == authored, f"missing sections: {authored - found}"


def test_the_activity_mix_responds_to_the_barrier():
    """The base split is a constant, so at minimum it has to lean towards whatever the
    scope's own customers complain about -- otherwise every archetype gets the same plan."""
    b = _plan_buckets()
    aid = b[b["bucket"] == "Grow"].iloc[0]["archetype_id"]

    def mix(barrier):
        r = P.build(_req(aid, assumptions=P.Assumptions(top_barrier=barrier)))
        card = next(c for c in r["cards"] if c["key"] == "activity")
        sec = next(s for s in card["sections"] if "kind of activities" in s["title"])
        return {x["format"]: x["share_pct"] for x in sec["rows"]}

    service, finance = mix("service"), mix("finance")
    assert service["Service & spares camp"] > finance["Service & spares camp"]
    assert finance["Finance / subsidy desk"] > service["Finance / subsidy desk"]
    for m in (service, finance):
        assert 97 <= sum(m.values()) <= 103, "the split stopped adding to 100"
