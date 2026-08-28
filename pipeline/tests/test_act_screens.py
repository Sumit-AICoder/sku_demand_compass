"""The two Act screens: the archetype summary and the priced playbook.

The playbook's whole claim is that its numbers are addable — each play moves a different
part of one identity, so nothing is counted twice and the total cannot exceed what the
archetype has left to give. These tests hold it to that claim, and to the rule that Act and
Plan must always agree about which bucket an archetype is in.
"""
from __future__ import annotations

import pytest

from api.main import (Assumptions, PlaybookReq, _plan_buckets, act_playbook, act_summary)


@pytest.fixture(scope="module")
def buckets():
    return _plan_buckets()


@pytest.fixture(scope="module")
def one_per_bucket(buckets):
    """One archetype from each bucket -- enough to cover every code path without paying the
    3.9M-row competitor join 53 times."""
    out = {}
    for b in ("Defend", "Grow", "No product fit"):
        rows = buckets[buckets["bucket"] == b]
        if len(rows):
            out[b] = rows.iloc[0]["archetype_id"]
    return out


# ------------------------------------------------------------------ summary

def test_summary_agrees_with_plan(one_per_bucket, buckets):
    """Act and Plan must never disagree about an archetype -- they share _plan_buckets."""
    for bucket, aid in one_per_bucket.items():
        s = act_summary(aid)
        row = buckets[buckets["archetype_id"] == aid].iloc[0]
        assert s["identity"]["bucket"] == bucket
        assert s["position"]["share"] == pytest.approx(float(row["avg_sonalika_share"]))
        assert s["funnel"]["deliveries"] == int(row["deliveries_yr"])
        assert s["size"]["micromarkets"] == int(row["n_micromarkets"])


def test_summary_has_a_competitive_picture(one_per_bucket):
    for aid in one_per_bucket.values():
        s = act_summary(aid)
        assert s["leaderboard"], "no OEM leaderboard for this archetype"
        assert s["position"]["leader"], "no leader identified"
        # Every rival row must be self-consistent: you cannot win more than they hold.
        for r in s["rivals"]:
            assert (r["winnable"] or 0) <= (r["their_units"] or 0) + 1e-6


def test_fleet_within_reach_is_a_subset_of_the_fleet(one_per_bucket):
    for aid in one_per_bucket.values():
        s = act_summary(aid)
        assert 0 <= s["position"]["tiv_in_reach"] <= s["size"]["tiv"]


# ------------------------------------------------------------------ playbook

def test_every_play_is_labelled(one_per_bucket):
    for aid in one_per_bucket.values():
        d = act_playbook(PlaybookReq(archetype_id=aid))
        assert d["plays"], "an archetype with no plays at all"
        for p in d["plays"]:
            assert p["confidence"] in ("arithmetic", "estimated", "proxy")
            assert p["mode"] in ("grow", "protect", "stop")
            assert p["detail"], "a play with no evidence line"


def test_no_play_exceeds_the_headroom(one_per_bucket):
    for aid in one_per_bucket.values():
        d = act_playbook(PlaybookReq(archetype_id=aid))
        for p in d["plays"]:
            if p["mode"] == "grow":
                assert p["units"] <= d["total"]["headroom"] + 1e-6, p["play"]


def test_total_is_capped_by_unclaimed_demand(one_per_bucket):
    for aid in one_per_bucket.values():
        t = act_playbook(PlaybookReq(archetype_id=aid))["total"]
        assert t["capped_units"] <= t["raw_units"] + 1e-6
        assert t["capped_units"] <= t["headroom"] + 1e-6


def test_protect_volume_is_never_summed_into_growth(one_per_bucket):
    """A Defend play holds volume we already have; adding it to growth would double-count
    the same units as both kept and won."""
    for aid in one_per_bucket.values():
        d = act_playbook(PlaybookReq(archetype_id=aid))
        grow = sum(p["units"] for p in d["plays"] if p["mode"] == "grow")
        assert d["total"]["raw_units"] == pytest.approx(grow, abs=1.0)


def test_no_product_fit_gets_no_selling_play(one_per_bucket):
    aid = one_per_bucket.get("No product fit")
    if aid is None:
        pytest.skip("no No-product-fit archetype in the current segmentation")
    d = act_playbook(PlaybookReq(archetype_id=aid))
    assert [p["mode"] for p in d["plays"]] == ["stop"]
    assert d["total"]["raw_units"] == 0


def test_assumptions_move_the_numbers(one_per_bucket):
    """The panel has to be worth its space: a better approval rate and a wider network must
    show up in the units, not just in the prose."""
    aid = one_per_bucket["Grow"]
    base = act_playbook(PlaybookReq(archetype_id=aid))
    bigger = act_playbook(PlaybookReq(
        archetype_id=aid,
        assumptions=Assumptions(approval_rate=0.90, dealer_density_pct=60,
                                awareness=0.8, activity_uplift_pct=40)))
    assert bigger["total"]["raw_units"] > base["total"]["raw_units"]

    reach_base = next(p["tiv_reached"] for p in base["plays"] if p["owns"] == "reach")
    reach_big = next(p["tiv_reached"] for p in bigger["plays"] if p["owns"] == "reach")
    assert reach_big > reach_base, "a wider network must reach more fleet"


def test_top_barrier_reranks_without_changing_units(one_per_bucket):
    aid = one_per_bucket["Grow"]
    finance = act_playbook(PlaybookReq(archetype_id=aid,
                                       assumptions=Assumptions(top_barrier="finance")))
    service = act_playbook(PlaybookReq(archetype_id=aid,
                                       assumptions=Assumptions(top_barrier="service")))
    units = {p["play"]: p["units"] for p in finance["plays"]}
    for p in service["plays"]:
        assert p["units"] == units[p["play"]], "the barrier must move rank, never units"
    assert finance["plays"][0]["owns"] == "approval"
    assert service["plays"][0]["owns"] == "reach"
