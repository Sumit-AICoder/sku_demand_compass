"""Per-archetype UCM ("What drives sales") -- same discipline as the district-level UCM:
the decomposition must add back to the observed series, and the estimator must recover
the coefficients the simulated panel was built with.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.common import CURATED, MARTS, read_table


@pytest.fixture(scope="module")
def decomposition():
    return read_table(MARTS / "ucm_archetype_decomposition.parquet")


@pytest.fixture(scope="module")
def betas():
    return read_table(MARTS / "ucm_archetype_betas.parquet")


@pytest.fixture(scope="module")
def diagnostics():
    return read_table(MARTS / "ucm_archetype_diagnostics.parquet")


def test_all_archetypes_fitted(diagnostics):
    arch = read_table(MARTS / "archetype_ops.parquet")
    assert set(diagnostics["archetype_id"]) == set(arch["archetype_id"]), \
        "every archetype must have a fitted UCM"


def test_additive_identity_holds(decomposition):
    """Baseline + sum(exogenous uplifts) + residual must equal actual, exactly -- this
    is definitional (Predicted is built from the model's own smoothed states), so a
    failure here means a bug in the decomposition, not sampling noise."""
    uplift_cols = [c for c in decomposition.columns if c.startswith("uplift_")]
    reconstructed = (decomposition["baseline"] + decomposition[uplift_cols].sum(axis=1)
                     + decomposition["residual"])
    max_err = (decomposition["actual_sales"] - reconstructed).abs().max()
    assert max_err < 1e-6, f"additive identity violated: max error {max_err}"


def test_predicted_equals_baseline_plus_uplifts(decomposition):
    uplift_cols = [c for c in decomposition.columns if c.startswith("uplift_")]
    predicted_check = decomposition["baseline"] + decomposition[uplift_cols].sum(axis=1)
    max_err = (decomposition["predicted"] - predicted_check).abs().max()
    assert max_err < 1e-6


def test_beats_seasonal_naive_almost_everywhere(diagnostics):
    """The whole point of fitting a structural model instead of reading off last year:
    it should out-predict a same-day-last-year benchmark for nearly every archetype."""
    share = diagnostics["beats_snaive"].mean()
    assert share > 0.85, f"only {share:.0%} of archetypes beat seasonal-naive"


def test_model_fit_is_reasonable(diagnostics):
    assert diagnostics["r2_like"].median() > 0.7
    assert diagnostics["identity_ok"].all()


def test_competitor_coefficient_is_negative(betas):
    """The client's explicit requirement: competitor pressure must carry a negative
    coefficient -- more aggressive/cheaper rivals should reduce Sonalika's sales."""
    comp = betas[betas["regressor"] == "competitor"]
    assert (comp["beta"] < 0).all(), "competitor coefficient must be negative everywhere"
    assert (comp["true_beta"] < 0).all()


def test_expected_signs_recovered(betas):
    """Every regressor's estimated sign should match its expected sign for the large
    majority of archetypes. Temperature is the deliberate exception: its true effect is
    small relative to noise by design (weather is a genuinely weak driver of capital
    equipment purchases), so its estimated sign is often not reliably distinguishable
    from zero -- a lower, still-informative bar applies to it alone."""
    by_reg = betas.groupby("regressor")["sign_ok"].mean()
    min_share = {"temperature": 0.5}
    for reg, share in by_reg.items():
        thr = min_share.get(reg, 0.85)
        assert share > thr, f"{reg}: only {share:.0%} of archetypes have the expected sign"


def test_pooled_betas_recover_known_truth(betas):
    """Pooled (mean) estimate across archetypes should land close to the mean true beta
    -- individual archetypes have estimation noise, but it should average out."""
    pooled = betas.groupby("regressor").agg(beta=("beta", "mean"), true=("true_beta", "mean"))
    for reg, row in pooled.iterrows():
        scale = max(abs(row["true"]), 0.05)
        rel_err = abs(row["beta"] - row["true"]) / scale
        assert rel_err < 0.35, (
            f"{reg}: pooled beta {row['beta']:.4f} vs true {row['true']:.4f} "
            f"(relative error {rel_err:.2f})")


def test_annual_totals_tie_back_to_operations():
    """The daily panel's annual sum should be in the same ballpark as the archetype's
    modelled annual deliveries from operations.py -- the daily story should integrate to
    roughly the same number already shown on the other Review tabs, not a disconnected one."""
    panel = read_table(CURATED / "archetype_sales_daily.parquet")
    # The daily panel is fit on the implements line, so the target it ties back to is that
    # line's -- archetype_ops now holds a row per line and .loc would return both.
    arch = read_table(MARTS / "archetype_ops.parquet")
    arch = arch[arch["product_line"] == "implements"].set_index("archetype_id")
    panel["year"] = pd.to_datetime(panel["date"]).dt.year
    annual = panel[panel["year"] == 2024].groupby("archetype_id")["actual_sales"].sum()
    for aid, total in annual.items():
        target = max(float(arch.loc[aid, "deliveries_yr"]), 150.0)
        assert total > 0.3 * target, f"{aid}: daily sum {total:.0f} far below target {target:.0f}"
