"""The three Plan screens: Where to play, Forecast, Targets.

Each screen rests on one piece of arithmetic that has to hold, and these are those:
the forecast has to be forward-dated and bracketed by its own interval, the three
buckets have to partition the archetype set exactly once, and the funnel back-solve has
to reproduce the target at the archetype's own rates -- it is an identity, not a model,
so any drift is a bug rather than a fit quality question.
"""
from __future__ import annotations

import pandas as pd
import pytest

from pipeline.common import MARTS, read_table


@pytest.fixture(scope="module")
def forecast():
    return read_table(MARTS / "ucm_forecast.parquet")


@pytest.fixture(scope="module")
def decomposition():
    return read_table(MARTS / "ucm_decomposition.parquet")


# ------------------------------------------------------------------ forecast

def test_every_fitted_district_forecasts(forecast, decomposition):
    assert set(forecast["district_id"]) == set(decomposition["district_id"])


def test_forecast_is_forward_dated(forecast, decomposition):
    """The whole point of the mart: it must sit strictly after the history it extends."""
    assert forecast["month"].min() > decomposition["month"].max()


def test_six_months_per_district(forecast):
    assert forecast.groupby("district_id")["month"].nunique().eq(6).all()


def test_interval_brackets_the_point_estimate(forecast):
    assert (forecast["lo"] <= forecast["forecast"]).all()
    assert (forecast["forecast"] <= forecast["hi"]).all()
    assert (forecast["lo"] > 0).all()          # a log-space model cannot go negative


# ------------------------------------------------------------------ where to play

def _buckets(**kw):
    from api.main import _plan_buckets
    return _plan_buckets(**kw)


def test_buckets_partition_the_archetypes():
    a = _buckets()
    # Implements only: these marts carry both product lines now, and every figure
    # asserted below is an implements one. A tractor row summed into them would make
    # the assertion wrong in a way that still looks like a plausible number.
    ops = read_table(MARTS / "archetype_ops.parquet")
    ops = ops[ops["product_line"] == "implements"]
    assert len(a) == len(ops)
    assert set(a["bucket"]) <= {"Defend", "Grow", "No product fit"}
    assert a["bucket"].notna().all()


def test_no_product_fit_is_exactly_the_fit_floor():
    a = _buckets(fit_min=0.55)
    assert (a.loc[a["bucket"] == "No product fit", "product_fit"] < 0.55).all()
    assert (a.loc[a["bucket"] != "No product fit", "product_fit"] >= 0.55).all()


def test_leader_mode_is_empty_on_todays_shares():
    """Documents why `stronghold` is the default.

    A literal market-leader test puts nothing in Defend: on the modelled shares the
    unbranded "Local" segment leads every archetype and Sonalika sits at 6-9% throughout.
    If real ITL share data ever changes that, this test fails loudly and the default
    should be revisited -- which is the point of asserting it.
    """
    a = _buckets(mode="leader")
    assert (a["bucket"] == "Defend").sum() == 0
    assert (a["bucket"] == "Grow").sum() > 0


def test_stronghold_mode_splits_three_ways():
    a = _buckets(mode="stronghold")
    assert (a["bucket"] == "Defend").sum() > 0
    assert (a["bucket"] == "Grow").sum() > 0


# ------------------------------------------------------------------ targets

def test_back_solve_reproduces_the_target():
    """activities -> enquiries -> deliveries, walked forwards, must land on the target."""
    from api.main import _plan_buckets, plan_targets
    grow = _plan_buckets()
    grow = grow[grow["bucket"] == "Grow"].iloc[0]
    t = plan_targets(archetype_id=grow["archetype_id"])

    c, g = t["current"], t["target"]
    enquiries = g["activities"] * c["enquiry_rate"]
    deliveries = enquiries * c["conversion_rate"]
    assert deliveries == pytest.approx(g["units"], rel=0.01)
    assert g["enquiries"] == pytest.approx(enquiries, rel=0.01)


def test_default_target_walks_towards_the_leader():
    from api.main import _plan_buckets, plan_targets
    grow = _plan_buckets()
    grow = grow[grow["bucket"] == "Grow"].iloc[0]
    t = plan_targets(archetype_id=grow["archetype_id"])
    assert t["target"]["units"] > t["current"]["deliveries"]
    assert t["target"]["share"] <= t["current"]["leader_share"]


def test_levers_are_ranked_by_units_closed():
    from api.main import _plan_buckets, plan_targets
    grow = _plan_buckets()
    grow = grow[grow["bucket"] == "Grow"].iloc[0]
    levers = plan_targets(archetype_id=grow["archetype_id"])["levers"]
    ranked = [l["units"] for l in levers if l["kind"] != "ceiling"]
    assert ranked == sorted(ranked, reverse=True)
    assert levers[-1]["kind"] == "ceiling"      # the cap always reads last
