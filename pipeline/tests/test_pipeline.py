"""Verification suite for the plan's checks 1-11.

The one that matters most is test_ucm_parameter_recovery: it fits the UCM to a series
whose true coefficients are known by construction and asserts the estimator gets them
back. If that fails, no coefficient this pipeline reports means anything -- on
simulated OR real data -- so it gates everything downstream.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.common import CURATED, MARTS, Config, read_table

pytestmark = pytest.mark.filterwarnings("ignore")


# ---------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def villages():
    return read_table(CURATED / "geo_villages.parquet")


@pytest.fixture(scope="module")
def districts():
    return read_table(CURATED / "geo_districts.parquet")


@pytest.fixture(scope="module")
def scores():
    return read_table(MARTS / "village_sku_scores.parquet")


@pytest.fixture(scope="module")
def betas():
    return read_table(MARTS / "ucm_betas.parquet")


@pytest.fixture(scope="module")
def diagnostics():
    return read_table(MARTS / "ucm_diagnostics.parquet")


@pytest.fixture(scope="module")
def decomposition():
    return read_table(MARTS / "ucm_decomposition.parquet")


# ---------------------------------------------------------------- 1. geo spine

def test_district_counts(districts):
    """Published district counts: Punjab 23, Madhya Pradesh 55, Maharashtra 36."""
    n = districts.groupby("state").size().to_dict()
    assert n == {"Punjab": 23, "Madhya Pradesh": 55, "Maharashtra": 36}


def test_village_counts_match_census_anchors(villages):
    """Village totals within 1% of the Census 2011 anchors."""
    from pipeline.transform.geo_spine import STATE_ANCHORS
    got = villages.groupby("state").size()
    for st, a in STATE_ANCHORS.items():
        assert abs(got[st] - a["villages"]) / a["villages"] < 0.01, st


def test_referential_integrity(villages, districts):
    blocks = read_table(CURATED / "geo_blocks.parquet")
    assert villages["village_id"].is_unique
    assert districts["district_id"].is_unique
    assert villages["district_id"].isin(districts["district_id"]).all()
    assert villages["block_id"].isin(blocks["block_id"]).all()


def test_villages_fall_inside_their_state_bbox(villages):
    from pipeline.transform.geo_spine import STATE_BBOX
    for st, (lo0, la0, lo1, la1) in STATE_BBOX.items():
        g = villages[villages.state == st]
        pad = 0.6
        assert g["lon"].between(lo0 - pad, lo1 + pad).mean() > 0.98, st
        assert g["lat"].between(la0 - pad, la1 + pad).mean() > 0.98, st


def test_net_sown_area_preserved_by_downscaling(villages, districts):
    """District totals must survive allocation to villages exactly."""
    got = villages.groupby("district_id")["net_sown_ha"].sum()
    want = districts.set_index("district_id")["net_sown_ha"]
    assert np.allclose(got.reindex(want.index), want, rtol=1e-6)


# ---------------------------------------------------------------- 2/3. config + normalisation

def test_factor_indices_bounded():
    f = read_table(MARTS / "village_factors.parquet")
    for fid in [f"F{i}" for i in range(1, 11)]:
        assert f[fid].between(0, 100).all(), fid


def test_subfactor_weights_sum_to_one():
    for fid, spec in Config.factors().items():
        assert abs(sum(s["weight"] for s in spec["subfactors"]) - 1.0) < 1e-9, fid


def test_every_table_declares_provenance():
    from pipeline.common import PROVENANCE
    for p in list(MARTS.glob("*.parquet")) + list(CURATED.glob("*.parquet")):
        df = pd.read_parquet(p, columns=None)
        assert "provenance" in df.columns, p.name
        assert set(df["provenance"].dropna().unique()) <= set(PROVENANCE), p.name


# ---------------------------------------------------------------- 4. UCM recovery

def test_ucm_parameter_recovery(betas):
    """The estimator must recover the coefficients injected into the simulated series.

    Two assertions, deliberately different in strength:
      * the POOLED estimate of each beta lands within 0.05 of the truth (tight -- the
        pooled mean averages out district-level estimation noise);
      * the truth lies inside the district's own 90% confidence interval for the large
        majority of districts (looser -- individual intervals should cover, but a
        nominal 90% interval is allowed to miss sometimes, which is the point of it).
    """
    true = Config.sim()["sales_dgp"]["true_betas"]
    pooled = betas.groupby("regressor")["beta"].mean()

    for name, tb in true.items():
        assert abs(pooled[name] - tb) < 0.05, (
            f"{name}: pooled beta {pooled[name]:.3f} vs true {tb:.3f}")

    # Coverage is checked against each district's OWN true beta, because the DGP makes
    # elasticities district-specific (see test_ucm_recovers_district_heterogeneity).
    # Checking against the global mean would fail for reasons that have nothing to do
    # with the estimator.
    series = read_table(CURATED / "district_series.parquet")
    true_cols = [c for c in series.columns if c.startswith("_true_beta_")]
    per_district = (series.groupby("district_id")[true_cols].first()
                    .rename(columns=lambda c: c.replace("_true_beta_", ""))
                    .stack().rename("true").reset_index()
                    .rename(columns={"level_1": "regressor"}))
    cover = betas.merge(per_district, on=["district_id", "regressor"], how="inner")
    assert len(cover) == len(betas), "true betas missing for some district/regressor pairs"
    inside = (cover["true"] >= cover["ci_low"]) & (cover["true"] <= cover["ci_high"])
    assert inside.mean() > 0.70, f"only {inside.mean():.0%} of 90% CIs cover the truth"


def test_ucm_recovers_district_heterogeneity(betas):
    """The DGP gives rainfed districts a much larger rainfall elasticity than irrigated
    ones. The estimator must recover that VARIATION, not just the average -- otherwise
    the scenario view would report the same drought impact everywhere, which is the
    single most misleading thing this dashboard could do."""
    series = read_table(CURATED / "district_series.parquet")
    truth = (series.groupby("district_id")["_true_beta_rainfall_departure"]
             .first().rename("true_beta"))
    est = betas[betas["regressor"] == "rainfall_departure"].set_index("district_id")["beta"]
    j = pd.concat([truth, est], axis=1).dropna()
    assert j["true_beta"].std() > 0.05, "DGP is not heterogeneous -- test is vacuous"
    assert j.corr().iloc[0, 1] > 0.80, (
        f"estimated betas track the truth at only r={j.corr().iloc[0, 1]:.2f}")

    geo = read_table(CURATED / "geo_districts.parquet").set_index("district_id")
    tier = geo.loc[j.index, "irrigation_tier"]
    by_tier = j.groupby(tier.to_numpy())["beta"].mean()
    assert by_tier["low"] > by_tier["high"] * 2, (
        f"rainfed districts should be far more rainfall-sensitive: {by_tier.to_dict()}")


def test_ucm_recovers_signs(betas):
    """Every beta's sign must match the direction the Excel asserts."""
    bad = betas[~betas["sign_ok"]]
    assert len(bad) / len(betas) < 0.05, (
        f"{len(bad)} sign violations:\n{bad.groupby('regressor').size()}")


# ---------------------------------------------------------------- 5. UCM diagnostics

def test_ucm_beats_seasonal_naive(diagnostics):
    """A model that cannot beat seasonal-naive is not fit to weight anything."""
    assert diagnostics["beats_snaive"].mean() > 0.90
    assert diagnostics["backtest_mape"].median() < diagnostics["snaive_mape"].median()


def test_ucm_residuals_are_clean(diagnostics):
    """Ljung-Box: no leftover autocorrelation the model failed to capture."""
    assert diagnostics["resid_autocorr_ok"].mean() > 0.85


def test_ucm_explains_the_series(diagnostics):
    assert diagnostics["r2_like"].median() > 0.80


# ---------------------------------------------------------------- 6. additivity

def test_decomposition_is_additive(decomposition):
    """trend + seasonal + cycle + regression + irregular == observed, exactly."""
    d = decomposition
    recon = d["trend"] + d["seasonal"] + d["cycle"] + d["regression"] + d["irregular"]
    assert np.allclose(recon, d["observed_log"], atol=1e-8)


def test_regression_equals_sum_of_contributions(decomposition):
    cols = [c for c in decomposition.columns if c.startswith("contrib_")]
    assert np.allclose(decomposition[cols].sum(axis=1), decomposition["regression"], atol=1e-8)


# ---------------------------------------------------------------- 7. weight substitution

def test_weights_record_their_origin():
    o = read_table(MARTS / "sku_weight_origin.parquet")
    assert set(o["origin"].unique()) <= {"ucm", "prior"}
    assert (o["origin"] == "ucm").any(), "no weight came from the UCM"
    assert (o["origin"] == "prior").any(), "structural factors should stay judgmental"


def test_only_ucm_covered_factors_are_empirical():
    """Factors with no time-varying driver cannot be estimated and must stay priors."""
    o = read_table(MARTS / "sku_weight_origin.parquet")
    covered = {r["factor"] for r in Config.ucm()["regressors"]}
    emp = set(o[o["origin"] == "ucm"]["factor"].unique())
    assert emp <= covered, f"{emp - covered} claimed empirical without a regressor"


def test_positive_weights_normalised():
    W = read_table(MARTS / "sku_weights.parquet")
    fids = [f"F{i}" for i in range(1, 11)]
    pos = W[fids].clip(lower=0).sum(axis=1)
    assert np.allclose(pos, 1.0, atol=1e-6)


# ---------------------------------------------------------------- 8. clustering

def test_cluster_stability_and_coherence():
    p = read_table(MARTS / "cluster_profiles.parquet")
    assert p["bootstrap_ari"].iloc[0] >= 0.70, "segmentation is not stable under resampling"
    assert p["spatial_coherence"].iloc[0] > 0.60, "archetypes are not spatially contiguous"
    # archetypes are now a NARP sub-zone x TIV tier x HP belt cross-product (~15 sub-zones
    # x 2 tiers x HP belts present), so the count is larger; allow a generous range.
    assert 20 <= len(p) <= 90, "archetype count outside the sub-zone x TIV x HP range"


def test_punjab_residue_belt_lands_in_one_archetype():
    c = read_table(MARTS / "village_clusters.parquet")
    pb = c[c.state == "Punjab"]["archetype"].value_counts(normalize=True)
    assert pb.iloc[0] > 0.45, f"Punjab villages scattered across archetypes:\n{pb}"


# ---------------------------------------------------------------- 9. face validity

@pytest.mark.parametrize("sku,expected_state", [
    ("SUPER_SEEDER", "Punjab"),          # residue-burning policy belt
    ("HAPPY_SEEDER", "Punjab"),
    ("ROUND_BALER", "Punjab"),
    ("STRAW_REAPER", "Punjab"),
    ("ORCHARD_SPRAYER", "Maharashtra"),  # horticulture -- grapes, orange, banana
    ("LASER_LEVELER", "Punjab"),         # irrigated north
])
def test_face_validity_regional_leaders(scores, villages, sku, expected_state):
    """The model must reproduce what the Excel's Mechanisation sheet already knows."""
    m = scores[scores.sku_id == sku].merge(villages[["village_id", "state"]], on="village_id")
    lead = m.groupby("state")["potential_units_yr"].sum().idxmax()
    assert lead == expected_state, f"{sku} led by {lead}, expected {expected_state}"


def test_trolley_is_the_highest_volume_sku(scores):
    """Haulage is near-universal: 'often bundled with tractor ownership' (Excel)."""
    top = scores.groupby("sku_id")["potential_units_yr"].sum().idxmax()
    assert top.startswith("TROLLEY")


def test_seasonality_peaks_in_the_right_months():
    """Post-kharif and festive months must dominate; monsoon must trough."""
    s = read_table(MARTS / "sku_seasonality.parquet")
    g = s.groupby("month_of_year")["ucm_gamma"].mean()
    assert g.idxmax() in (9, 10, 11), f"peak in month {g.idxmax()}"
    assert g.idxmin() in (6, 7, 12, 1), f"trough in month {g.idxmin()}"


def test_score_monotonicity_for_tractor_driven_sku():
    """Raising the tractor-base factor must never lower a trolley's propensity."""
    W = read_table(MARTS / "sku_weights.parquet").set_index("sku_id")
    assert W.loc["TROLLEY_2W_5T", "F3"] > 0
    assert W.loc["TROLLEY_2W_5T", "F3"] == W.loc[:, "F3"].max()


# ---------------------------------------------------------------- 10. reconciliation

def test_levels_reconcile(scores):
    """A parent's total must equal the sum of its children's -- at every level."""
    vt = read_table(MARTS / "village_totals.parquet")
    bt = read_table(MARTS / "block_totals.parquet")
    dt = read_table(MARTS / "district_totals.parquet")

    assert np.isclose(vt["potential_units_yr"].sum(), scores["potential_units_yr"].sum(), rtol=1e-6)
    assert np.isclose(bt["potential_units_yr"].sum(), vt["potential_units_yr"].sum(), rtol=1e-6)
    assert np.isclose(dt["potential_units_yr"].sum(), bt["potential_units_yr"].sum(), rtol=1e-6)

    per_block = vt.groupby("block_id")["potential_units_yr"].sum()
    got = bt.set_index("block_id")["potential_units_yr"]
    assert np.allclose(got, per_block.reindex(got.index), rtol=1e-6)


def test_addressable_never_below_owned(scores):
    assert (scores["headroom"] >= -1e-9).all()
    assert (scores["penetration"] <= 1.0 + 1e-9).all()


def test_hp_band_gating_is_strict():
    """A high-HP implement must have no addressable market in a low-HP fleet."""
    from pipeline.simulate.sku_history import hp_band_overlap
    assert hp_band_overlap(Config.sku("ROUND_BALER"))[0] == 0.0      # 20-35 HP band
    assert hp_band_overlap(Config.sku("CHAFF_CUTTER"))[3] == 0.0     # 60+ HP band
    assert hp_band_overlap(Config.sku("AGRI_DRONE")).min() == 1.0    # self-propelled
