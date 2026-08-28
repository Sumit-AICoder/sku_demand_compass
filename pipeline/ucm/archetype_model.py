"""Per-archetype Unobserved Components Model for daily sales causal decomposition.

For each archetype we fit, in LEVELS (not log, so every term is additive and reads
directly in sales units):

    sales_t = level_t + seasonal_weekly_t + seasonal_annual_t
              + beta_temp . temperature_t + beta_hol . is_holiday_t
              + beta_promo . is_promotion_t + beta_price . price_drop_pct_t
              + beta_comp . competitor_t + irregular_t

using `statsmodels.tsa.statespace.structural.UnobservedComponents` with a local linear
trend and two frequency-domain (harmonic) seasonal blocks -- period 7 (weekly) and
365.25 (annual) -- so both seasonalities coexist without a 365-dimensional stochastic
seasonal state, which would be both slow and poorly identified from 3 years of data.

Baseline Sales is defined STRICTLY as level + both seasonal blocks: the underlying
volume with weather, holidays, promotions, price and competition stripped out. Predicted
= Baseline + the five exogenous contributions; Residual = Actual - Predicted. That
equality holds by construction (Predicted is built from the model's own smoothed
components), so what we actually verify in tests is (a) the identity holds to numerical
precision and (b) the estimated betas recover the KNOWN true betas the panel was
simulated with (pipeline/simulate/archetype_sales.py) -- the same discipline the
district-level UCM applies to its own synthetic ground truth.
"""
from __future__ import annotations

import warnings
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

from pipeline.common import CURATED, MARTS, read_table, write_table, log

warnings.filterwarnings("ignore")
LOG = log("archetype_ucm")

REGRESSORS = ["temperature", "is_holiday", "is_promotion", "price_drop_pct", "competitor"]
EXPECTED_SIGN = {"temperature": "negative", "is_holiday": "positive", "is_promotion": "positive",
                 "price_drop_pct": "positive", "competitor": "negative"}
LABEL = {"temperature": "Temperature", "is_holiday": "Holiday", "is_promotion": "Promotion",
         "price_drop_pct": "Price drop %", "competitor": "Competitor pressure"}

MIN_OBS = 300
BACKTEST_DAYS = 90
SNAIVE_LAG = 365


@dataclass
class FitResult:
    archetype_id: str
    ok: bool
    reason: str = ""
    decomposition: pd.DataFrame | None = None
    betas: pd.DataFrame | None = None
    diagnostics: dict | None = None


def _build(y, X):
    from statsmodels.tsa.statespace.structural import UnobservedComponents
    return UnobservedComponents(
        y, level="local linear trend", stochastic_level=False, stochastic_trend=False,
        freq_seasonal=[{"period": 7, "harmonics": 3}, {"period": 365.25, "harmonics": 6}],
        stochastic_freq_seasonal=[False, False],
        exog=X, mle_regression=True)


def _fit_one(args) -> FitResult:
    """Fit one archetype. Runs in a worker process, so it takes/returns plain data."""
    aid, y, X, dates, true_betas = args
    import warnings as _w
    _w.filterwarnings("ignore")
    from statsmodels.stats.diagnostic import acorr_ljungbox
    from scipy import stats as sps

    if len(y) < MIN_OBS:
        return FitResult(aid, False, f"only {len(y)} obs")

    yv, Xv = y.to_numpy(float), X.to_numpy(float)
    try:
        res = _build(yv, Xv).fit(disp=False, maxiter=150, method="lbfgs")
    except Exception as exc:                                      # noqa: BLE001
        return FitResult(aid, False, f"fit failed: {type(exc).__name__}")

    level = np.asarray(res.level["smoothed"], dtype=float)
    seasonal_weekly = np.asarray(res.freq_seasonal[0]["smoothed"], dtype=float)
    seasonal_annual = np.asarray(res.freq_seasonal[1]["smoothed"], dtype=float)
    baseline = level + seasonal_weekly + seasonal_annual

    pnames = list(res.param_names)
    bidx = [i for i, p in enumerate(pnames) if p.startswith("beta.")]
    beta = np.array([res.params[i] for i in bidx], dtype=float)
    bse = np.array([res.bse[i] for i in bidx], dtype=float)
    if len(beta) != len(REGRESSORS):
        return FitResult(aid, False, "exog coefficient count mismatch")

    contrib = Xv * beta[None, :]
    regression = contrib.sum(1)
    predicted = baseline + regression
    residual = yv - predicted

    dec = pd.DataFrame({
        "archetype_id": aid, "date": dates,
        "actual_sales": yv, "baseline": baseline, "trend": level,
        "seasonal_weekly": seasonal_weekly, "seasonal_annual": seasonal_annual,
        "predicted": predicted, "residual": residual,
    })
    for j, nm in enumerate(REGRESSORS):
        dec[f"uplift_{nm}"] = contrib[:, j]

    # additive identity, by construction -- verified, not assumed
    max_err = float(np.max(np.abs((baseline + regression + residual) - yv)))

    burn = 14
    resid_tail = residual[burn:]
    try:
        lb_p = float(acorr_ljungbox(resid_tail, lags=[10], return_df=True)["lb_pvalue"].iloc[0])
    except Exception:                                              # noqa: BLE001
        lb_p = np.nan
    try:
        jb_p = float(sps.jarque_bera(resid_tail).pvalue)
    except Exception:                                              # noqa: BLE001
        jb_p = np.nan

    # backtest: hold out the last BACKTEST_DAYS, refit on the rest, forecast using the
    # known exog for the holdout window (explanatory, not blind, forecast -- same
    # convention the district-level UCM uses). Seasonal-naive benchmark = value from
    # SNAIVE_LAG days earlier. WAPE (sum |error| / sum actual), not pointwise MAPE --
    # several archetypes have daily actuals near zero, where a pointwise percentage
    # error blows up on the denominator; WAPE sums first, so it stays well-behaved.
    wape = snaive_wape = np.nan
    h = BACKTEST_DAYS
    if len(yv) > h + SNAIVE_LAG + 30:
        try:
            tr_y, te_y = yv[:-h], yv[-h:]
            tr_X, te_X = Xv[:-h], Xv[-h:]
            r2 = _build(tr_y, tr_X).fit(disp=False, maxiter=150, method="lbfgs")
            fc = np.asarray(r2.forecast(steps=h, exog=te_X), dtype=float)
            denom = max(float(np.sum(te_y)), 1e-6)
            wape = float(np.sum(np.abs(te_y - fc)) / denom * 100)
            sn = yv[-h - SNAIVE_LAG:-SNAIVE_LAG]
            snaive_wape = float(np.sum(np.abs(te_y - sn)) / denom * 100)
        except Exception:                                          # noqa: BLE001
            pass
    beats = bool(np.isfinite(wape) and np.isfinite(snaive_wape) and wape < snaive_wape)

    diagnostics = {
        "archetype_id": aid, "aic": float(res.aic), "bic": float(res.bic),
        "ljung_box_p": lb_p, "jarque_bera_p": jb_p,
        "resid_autocorr_ok": bool(np.isfinite(lb_p) and lb_p > 0.05),
        "backtest_wape": wape, "snaive_wape": snaive_wape, "beats_snaive": beats,
        "r2_like": float(1 - np.var(residual) / max(np.var(yv), 1e-9)),
        "n_obs": int(len(yv)), "identity_max_abs_error": max_err,
        "identity_ok": bool(max_err < 1e-6),
    }

    z = sps.norm.ppf(0.95)
    tstat = np.divide(beta, bse, out=np.full_like(beta, np.nan), where=bse > 0)
    pval = 2 * (1 - sps.norm.cdf(np.abs(tstat)))
    sig = pval < 0.10
    sign_ok = np.array([(beta[j] > 0) == (EXPECTED_SIGN[nm] == "positive")
                        for j, nm in enumerate(REGRESSORS)])
    tb = true_betas
    true_vals = np.array([tb.get(f"true_beta_{nm}", np.nan) for nm in REGRESSORS])

    betas_df = pd.DataFrame({
        "archetype_id": aid, "regressor": REGRESSORS,
        "label": [LABEL[n] for n in REGRESSORS],
        "beta": beta, "true_beta": true_vals, "se": bse, "tstat": tstat, "pvalue": pval,
        "ci_low": beta - z * bse, "ci_high": beta + z * bse, "significant": sig,
        "expected_sign": [EXPECTED_SIGN[n] for n in REGRESSORS], "sign_ok": sign_ok,
    })
    return FitResult(aid, True, "", dec, betas_df, diagnostics)


def fit_all(n_jobs: int | None = None) -> dict[str, pd.DataFrame]:
    panel = read_table(CURATED / "archetype_sales_daily.parquet")
    true_betas = read_table(CURATED / "archetype_sales_true_betas.parquet").set_index("archetype_id")

    jobs = []
    for aid, g in panel.sort_values("date").groupby("archetype_id"):
        g = g.reset_index(drop=True)
        tb = true_betas.loc[aid].to_dict() if aid in true_betas.index else {}
        jobs.append((aid, g["actual_sales"], g[REGRESSORS], g["date"].to_numpy(), tb))

    LOG.info("fitting %d archetype UCMs (%d daily obs each, %d regressors)...",
             len(jobs), len(jobs[0][1]), len(REGRESSORS))

    results: list[FitResult] = []
    with ProcessPoolExecutor(max_workers=n_jobs) as ex:
        for i, r in enumerate(ex.map(_fit_one, jobs, chunksize=1), 1):
            results.append(r)
            if i % 10 == 0:
                LOG.info("  %d/%d fitted", i, len(jobs))

    ok = [r for r in results if r.ok]
    LOG.info("converged: %d/%d", len(ok), len(results))
    for r in results:
        if not r.ok:
            LOG.warning("  %s: %s", r.archetype_id, r.reason)
    if not ok:
        raise RuntimeError("no archetype UCM converged")

    dec = pd.concat([r.decomposition for r in ok], ignore_index=True)
    betas = pd.concat([r.betas for r in ok], ignore_index=True)
    diags = pd.DataFrame([r.diagnostics for r in ok])

    dec["provenance"] = "simulated"
    betas["provenance"] = "simulated"
    diags["provenance"] = "simulated"

    LOG.info("median R2-like %.2f | beats seasonal-naive %d/%d | identity clean %d/%d "
             "| median backtest WAPE %.1f%% vs snaive %.1f%%",
             diags["r2_like"].median(), int(diags["beats_snaive"].sum()), len(diags),
             int(diags["identity_ok"].sum()), len(diags),
             diags["backtest_wape"].median(), diags["snaive_wape"].median())
    recovery = (betas.assign(err=lambda d: (d.beta - d.true_beta).abs())
               .groupby("regressor")["err"].mean())
    LOG.info("mean |beta - true_beta| by regressor:\n%s", recovery.round(4).to_string())

    write_table(dec, MARTS / "ucm_archetype_decomposition.parquet")
    write_table(betas, MARTS / "ucm_archetype_betas.parquet")
    write_table(diags, MARTS / "ucm_archetype_diagnostics.parquet")
    return {"decomposition": dec, "betas": betas, "diagnostics": diags}


if __name__ == "__main__":
    fit_all()
