"""Phase 5 -- Unobserved Components Model: decomposing tractor sales into factor uplift.

For each district we fit

    log y_t = mu_t + gamma_t + psi_t + sum_j beta_j x_j,t + eps_t

with mu_t a local linear trend, gamma_t a stochastic 12-period seasonal, psi_t a
damped stochastic cycle, and x_j the standardised agri drivers. Because y is logged
and x standardised, beta_j reads directly as "a 1-sd move in this driver shifts
sales by beta_j x 100 percent" -- which is exactly the uplift number the dashboard
reports, and exactly the quantity the Excel asserts a direction for but never sizes.

Three things this module refuses to do quietly:
  * use a beta whose sign contradicts the Excel's stated impact direction
  * use a beta from a model that cannot beat seasonal-naive out of sample
  * present a decomposition that does not add back to the observed series
"""
from __future__ import annotations

import warnings
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

from pipeline.common import CURATED, MARTS, Config, read_table, write_table, log

warnings.filterwarnings("ignore")
LOG = log("ucm")


# ------------------------------------------------------------------ single fit

@dataclass
class FitResult:
    district_id: str
    ok: bool
    reason: str = ""
    decomposition: pd.DataFrame | None = None
    betas: pd.DataFrame | None = None
    diagnostics: dict | None = None


def _fit_one(args) -> FitResult:
    """Fit one district. Runs in a worker process, so it takes/returns plain data."""
    district_id, y, X, months, cfg = args
    import warnings as _w
    _w.filterwarnings("ignore")          # worker process needs its own filter
    from statsmodels.tsa.statespace.structural import UnobservedComponents
    from statsmodels.stats.diagnostic import acorr_ljungbox
    from scipy import stats as sps

    spec, est, diag = cfg["specification"], cfg["estimation"], cfg["diagnostics"]
    reg_names = list(X.columns)

    if len(y) < est["min_observations"]:
        return FitResult(district_id, False, f"only {len(y)} obs")

    ly = np.log(np.maximum(y.to_numpy(float), 1e-6))

    def _build(endog, exog):
        return UnobservedComponents(
            endog,
            level=spec["level"],
            stochastic_level=spec["stochastic_level"],
            stochastic_trend=spec["stochastic_trend"],
            seasonal=spec["seasonal"],
            stochastic_seasonal=spec["stochastic_seasonal"],
            cycle=spec["cycle"],
            damped_cycle=spec["damped_cycle"],
            stochastic_cycle=spec["stochastic_cycle"],
            exog=exog,
            mle_regression=spec["mle_regression"],
        )

    try:
        res = _build(ly, X.to_numpy(float)).fit(
            disp=False, maxiter=est["maxiter"], method=est["method"])
    except Exception as exc:                                        # noqa: BLE001
        return FitResult(district_id, False, f"fit failed: {type(exc).__name__}")

    # ---- components ---------------------------------------------------------
    ss = np.asarray(res.states.smoothed, dtype=float)
    state_names = [str(s_).lower() for s_ in res.model.state_names]

    def comp(*names):
        """Pull a smoothed state by name; statsmodels returns a bare ndarray here."""
        for n in names:
            for i, sn in enumerate(state_names):
                if sn == n:
                    return ss[:, i]
        return np.zeros(len(ly))

    level = comp("level")
    seasonal = comp("seasonal")
    cycle = comp("cycle")

    # exog coefficients: params named "beta.x1".. in model order
    pnames = list(res.param_names)
    bidx = [i for i, p in enumerate(pnames) if p.startswith("beta.")]
    beta = np.array([res.params[i] for i in bidx], dtype=float)
    bse = np.array([res.bse[i] for i in bidx], dtype=float)
    if len(beta) != len(reg_names):
        return FitResult(district_id, False, "exog coefficient count mismatch")

    Xv = X.to_numpy(float)
    contrib = Xv * beta[None, :]
    regression = contrib.sum(1)
    fitted = level + seasonal + cycle + regression
    irregular = ly - fitted

    dec = pd.DataFrame({
        "district_id": district_id,
        "month": months,
        "observed_log": ly,
        "trend": level,
        "seasonal": seasonal,
        "cycle": cycle,
        "regression": regression,
        "irregular": irregular,
        "fitted_log": fitted,
        "observed": np.exp(ly),
        "fitted": np.exp(fitted),
    })
    for j, nm in enumerate(reg_names):
        dec[f"contrib_{nm}"] = contrib[:, j]

    # ---- diagnostics --------------------------------------------------------
    # Drop the diffuse initialisation burn-in: during it the one-step-ahead errors are
    # dominated by the undefined initial state, and including them makes Ljung-Box and
    # Jarque-Bera reject for every series regardless of model quality.
    burn = int(getattr(res, "nobs_diffuse", 0) or getattr(res, "loglikelihood_burn", 0) or 0)
    burn = max(burn, spec["seasonal"] + 2)
    resid = np.asarray(res.resid, dtype=float)[burn:]
    resid = resid[np.isfinite(resid)]
    try:
        lb = acorr_ljungbox(resid, lags=[diag["ljung_box_lags"]], return_df=True)
        lb_p = float(lb["lb_pvalue"].iloc[0])
    except Exception:                                               # noqa: BLE001
        lb_p = np.nan
    try:
        jb_p = float(sps.jarque_bera(resid).pvalue)
    except Exception:                                               # noqa: BLE001
        jb_p = np.nan

    # out-of-sample backtest vs seasonal-naive
    h = diag["backtest_horizon"]
    mape = snaive_mape = np.nan
    if len(ly) > h + spec["seasonal"] * 2:
        try:
            tr, te = ly[:-h], ly[-h:]
            r2 = _build(tr, Xv[:-h]).fit(disp=False, maxiter=est["maxiter"], method=est["method"])
            fc = np.asarray(r2.forecast(steps=h, exog=Xv[-h:]), dtype=float)
            mape = float(np.mean(np.abs(np.exp(te) - np.exp(fc)) / np.exp(te)) * 100)
            sn = ly[-h - 12:-12]
            snaive_mape = float(np.mean(np.abs(np.exp(te) - np.exp(sn)) / np.exp(te)) * 100)
        except Exception:                                           # noqa: BLE001
            pass

    beats = bool(np.isfinite(mape) and np.isfinite(snaive_mape) and mape < snaive_mape)
    diagnostics = {
        "district_id": district_id,
        "aic": float(res.aic), "bic": float(res.bic),
        "ljung_box_p": lb_p, "jarque_bera_p": jb_p,
        "resid_autocorr_ok": bool(np.isfinite(lb_p) and lb_p > diag["alpha"]),
        "resid_normal_ok": bool(np.isfinite(jb_p) and jb_p > diag["alpha"]),
        "backtest_mape": mape, "snaive_mape": snaive_mape,
        "beats_snaive": beats,
        "r2_like": float(1 - np.var(irregular) / np.var(ly)),
        "n_obs": int(len(ly)),
        # A model is usable for WEIGHTING only if it forecasts better than the naive
        # benchmark. Everything else it produces (the decomposition) is still shown.
        "usable_for_weights": beats or not diag["require_beat_seasonal_naive"],
    }

    # ---- betas with intervals and sign audit --------------------------------
    z = sps.norm.ppf(1 - cfg["significance"]["alpha"] / 2)
    expected = {r["name"]: r["expected_sign"] for r in cfg["regressors"]}
    factor_of = {r["name"]: r["factor"] for r in cfg["regressors"]}
    tstat = np.divide(beta, bse, out=np.full_like(beta, np.nan), where=bse > 0)
    pval = 2 * (1 - sps.norm.cdf(np.abs(tstat)))
    sig = pval < cfg["significance"]["alpha"]
    sign_ok = np.array([
        (beta[j] > 0) == (expected[nm] == "positive") for j, nm in enumerate(reg_names)])

    betas = pd.DataFrame({
        "district_id": district_id,
        "regressor": reg_names,
        "factor": [factor_of[n] for n in reg_names],
        "beta": beta, "se": bse, "tstat": tstat, "pvalue": pval,
        "ci_low": beta - z * bse, "ci_high": beta + z * bse,
        "significant": sig,
        "expected_sign": [expected[n] for n in reg_names],
        "sign_ok": sign_ok,
        "usable": sig & sign_ok & diagnostics["usable_for_weights"],
    })
    return FitResult(district_id, True, "", dec, betas, diagnostics)


# ------------------------------------------------------------------ driver

def fit_all(series: pd.DataFrame | None = None, n_jobs: int | None = None) -> dict[str, pd.DataFrame]:
    cfg = Config.ucm()
    if series is None:
        series = read_table(CURATED / "district_series.parquet")
    reg_names = [r["name"] for r in cfg["regressors"]]

    _vif_report(series, reg_names, cfg)

    jobs = []
    for did, g in series.sort_values("month").groupby("district_id"):
        jobs.append((did, g[cfg["target"]["y"]], g[reg_names].reset_index(drop=True),
                     g["month"].to_numpy(), cfg))

    n_jobs = n_jobs if n_jobs is not None else (cfg["estimation"]["n_jobs"] or None)
    LOG.info("fitting %d district UCMs (%d obs each, %d regressors)...",
             len(jobs), len(jobs[0][1]), len(reg_names))

    results: list[FitResult] = []
    with ProcessPoolExecutor(max_workers=n_jobs) as ex:
        for i, r in enumerate(ex.map(_fit_one, jobs, chunksize=2), 1):
            results.append(r)
            if i % 25 == 0:
                LOG.info("  %d/%d fitted", i, len(jobs))

    ok = [r for r in results if r.ok]
    LOG.info("converged: %d/%d", len(ok), len(results))
    for r in results:
        if not r.ok:
            LOG.warning("  %s: %s", r.district_id, r.reason)
    if not ok:
        raise RuntimeError("no district UCM converged")

    dec = pd.concat([r.decomposition for r in ok], ignore_index=True)
    betas = pd.concat([r.betas for r in ok], ignore_index=True)
    diags = pd.DataFrame([r.diagnostics for r in ok])

    dec["provenance"] = "allocated"
    betas["provenance"] = "allocated"
    diags["provenance"] = "allocated"

    _log_quality(diags, betas)
    write_table(dec, MARTS / "ucm_decomposition.parquet")
    write_table(betas, MARTS / "ucm_betas.parquet")
    write_table(diags, MARTS / "ucm_diagnostics.parquet")
    return {"decomposition": dec, "betas": betas, "diagnostics": diags}


def _vif_report(series: pd.DataFrame, reg_names: list[str], cfg: dict) -> None:
    """Screen the regressor block for multicollinearity.

    Agri drivers are genuinely correlated (rainfall drives reservoir and NDVI), so we
    report rather than drop -- dropping them would destroy the very attribution the
    dashboard exists to show. High-VIF regressors are flagged so their wide intervals
    are read as collinearity, not as absence of effect.
    """
    from statsmodels.stats.outliers_influence import variance_inflation_factor
    X = series[reg_names].to_numpy(float)
    X = np.column_stack([np.ones(len(X)), X])
    vifs = [variance_inflation_factor(X, i + 1) for i in range(len(reg_names))]
    thr = cfg["multicollinearity"]["vif_threshold"]
    hi = [(n, round(v, 1)) for n, v in zip(reg_names, vifs) if v > thr]
    LOG.info("VIF max %.1f (threshold %.1f)%s", max(vifs), thr,
             f" -- above threshold: {hi}" if hi else " -- all clear")
    pd.DataFrame({"regressor": reg_names, "vif": vifs,
                  "above_threshold": [v > thr for v in vifs],
                  "provenance": "allocated"}).pipe(write_table, MARTS / "ucm_vif.parquet")


def _log_quality(diags: pd.DataFrame, betas: pd.DataFrame) -> None:
    LOG.info("diagnostics: median R2-like %.2f | beats seasonal-naive %d/%d "
             "| residual autocorr clean %d/%d | normal residuals %d/%d",
             diags["r2_like"].median(),
             int(diags["beats_snaive"].sum()), len(diags),
             int(diags["resid_autocorr_ok"].sum()), len(diags),
             int(diags["resid_normal_ok"].sum()), len(diags))
    LOG.info("median backtest MAPE %.1f%% vs seasonal-naive %.1f%%",
             diags["backtest_mape"].median(), diags["snaive_mape"].median())
    s = (betas.groupby("regressor")
         .agg(mean_beta=("beta", "mean"), sig_pct=("significant", "mean"),
              sign_ok_pct=("sign_ok", "mean"), usable_pct=("usable", "mean"))
         .sort_values("mean_beta", ascending=False))
    LOG.info("beta summary across districts:\n%s",
             s.assign(**{c: (s[c] * 100).round(0) for c in ("sig_pct", "sign_ok_pct", "usable_pct")})
              .round(3).to_string())


if __name__ == "__main__":
    fit_all()
