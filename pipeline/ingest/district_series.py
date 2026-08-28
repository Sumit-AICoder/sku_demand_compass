"""District x month panel: the UCM regressors and the UCM target.

The target is monthly tractor registrations. Vahan publishes this for real but only
through a JSF dashboard with no scriptable REST surface, so this module synthesises
the panel from an explicit structural DGP:

    log(y_t) = level_t + trend + seasonal_t + cycle_t + sum_j beta_j x_j,t
               + interventions_t + irregular_t

The beta_j are read from sim_params.yaml `sales_dgp.true_betas`. That is the whole
point: Phase 5 fits an UnobservedComponents model to this series, and the recovery
test asserts the estimator gets those betas back. If the estimator cannot recover
known coefficients from a series with known structure, its coefficients on real
Vahan data would be worthless too.

Regressors are generated as persistent AR(1) processes with a shared monsoon factor,
so rainfall / reservoir / NDVI are genuinely correlated the way they are in nature.
That correlation is real and the VIF screen in Phase 5 has to cope with it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.common import CURATED, Config, write_table, log
from pipeline.ingest.base import Connector

LOG = log("district_series")

N_MONTHS = 120
END_PERIOD = "2026-07"

# Indian tractor-sales seasonality: post-kharif + festive peak (Sep-Nov), a secondary
# rabi-harvest peak (Mar-Apr), and a monsoon-onset trough (Jun-Jul).
SEASONAL_SHAPE = np.array([
    -0.16,  # Jan
    -0.10,  # Feb
     0.09,  # Mar
     0.12,  # Apr
     0.02,  # May
    -0.14,  # Jun
    -0.20,  # Jul
    -0.05,  # Aug
     0.14,  # Sep
     0.26,  # Oct
     0.15,  # Nov
    -0.13,  # Dec
])

REGRESSORS = [
    "rainfall_departure", "reservoir_status", "ndvi_anomaly", "mandi_price_index",
    "msp_change", "credit_depth", "subsidy_intensity", "rural_wage_index",
    "diesel_price", "fertilizer_offtake", "pmfby_claims",
]


def _ar1(rng, n, rho, sigma, size):
    """Persistent AR(1) paths -- agri drivers have memory, white noise would not do."""
    x = np.zeros((size, n))
    x[:, 0] = rng.normal(0, sigma / np.sqrt(1 - rho ** 2), size)
    for t in range(1, n):
        x[:, t] = rho * x[:, t - 1] + rng.normal(0, sigma, size)
    return x


class DistrictSeriesConnector(Connector):
    """Builds the regressor panel and the tractor-registration target together."""

    source_key = "vahan_tractor_registrations"

    def synthesize(self) -> pd.DataFrame:
        cfg = Config.sim()
        dgp = cfg["sales_dgp"]
        rng = np.random.default_rng(self.seed + 21)

        d = self.spine["districts"]
        nd, n = len(d), N_MONTHS
        idx = pd.period_range(end=END_PERIOD, periods=n, freq="M")
        month_of_year = np.array([p.month for p in idx])

        # ---- shared monsoon factor: one national/regional signal per year --------
        years = np.array([p.year for p in idx])
        uy = np.unique(years)
        monsoon_year = dict(zip(uy, rng.normal(0, 1.0, len(uy))))
        monsoon = np.array([monsoon_year[y] for y in years])

        X = {}
        # Rainfall departure: shared monsoon + district idiosyncrasy, and it only
        # really varies in the monsoon months.
        season_gate = np.isin(month_of_year, [6, 7, 8, 9]).astype(float) * 0.7 + 0.3
        X["rainfall_departure"] = (0.62 * monsoon[None, :]
                                   + _ar1(rng, n, 0.25, 0.8, nd)) * season_gate[None, :]
        # Reservoir follows rainfall with a lag and much more persistence.
        X["reservoir_status"] = (0.55 * np.roll(X["rainfall_departure"], 2, axis=1)
                                 + _ar1(rng, n, 0.80, 0.45, nd))
        # NDVI responds to rainfall one month on.
        X["ndvi_anomaly"] = (0.48 * np.roll(X["rainfall_departure"], 1, axis=1)
                             + _ar1(rng, n, 0.55, 0.6, nd))
        # Price / policy / cost drivers -- largely independent of the monsoon.
        X["mandi_price_index"] = _ar1(rng, n, 0.86, 0.42, nd)
        X["msp_change"] = np.tile(rng.normal(0, 0.9, (1, n)), (nd, 1)) * 0.6 + _ar1(rng, n, 0.5, 0.5, nd)
        X["credit_depth"] = _ar1(rng, n, 0.90, 0.35, nd) + np.linspace(0, 0.6, n)[None, :]
        X["subsidy_intensity"] = _ar1(rng, n, 0.45, 0.75, nd)
        X["rural_wage_index"] = _ar1(rng, n, 0.92, 0.30, nd) + np.linspace(0, 0.8, n)[None, :]
        X["diesel_price"] = np.tile(_ar1(rng, n, 0.93, 0.30, 1), (nd, 1))   # national
        X["fertilizer_offtake"] = _ar1(rng, n, 0.6, 0.7, nd)
        X["pmfby_claims"] = (-0.4 * X["rainfall_departure"] + _ar1(rng, n, 0.4, 0.7, nd))

        for k in X:
            X[k] = (X[k] - X[k].mean()) / X[k].std()     # standardise: betas read as elasticities

        # ---- structural components ---------------------------------------------
        tier_cagr = d["mech_tier"].map(cfg["tractor_base"]["registration_cagr"]).to_numpy()
        drift = (np.log1p(tier_cagr) / 12.0)[:, None]
        trend = np.cumsum(np.tile(drift, (1, n)) + rng.normal(0, dgp["trend_noise"] / 12, (nd, n)), axis=1)

        # Stochastic seasonal: the base shape drifts slowly over the decade.
        seas = SEASONAL_SHAPE[month_of_year - 1][None, :] * np.ones((nd, 1))
        seas = seas * (1 + 0.15 * np.linspace(-1, 1, n))[None, :]
        seas += rng.normal(0, 0.012, (nd, n))

        cyc_p, cyc_a = dgp["cycle_period_months"], dgp["cycle_amplitude"]
        phase = rng.uniform(0, 2 * np.pi, nd)[:, None]
        cycle = cyc_a * np.sin(2 * np.pi * np.arange(n)[None, :] / cyc_p + phase)

        # ---- district-heterogeneous elasticities --------------------------------
        # A uniform beta would be a lie with consequences: it would make a monsoon
        # shock look equally damaging in assured-irrigation Punjab and rainfed
        # Vidarbha, and the scenario view would have nothing to say. Water-driven
        # elasticities scale UP where irrigation is thin; credit and subsidy
        # elasticities scale UP where mechanisation is already established and there
        # is something to finance. Multipliers are centred on 1.0 so the POOLED beta
        # still equals the configured true value and parameter recovery stays testable.
        irr = d["irrigation_tier"].map({"high": 0.80, "medium": 0.45, "low": 0.18}).to_numpy()
        mech = d["mech_tier"].map({"high": 1.0, "medium": 0.6, "low": 0.3}).to_numpy()

        def _centred(x, strength):
            m = 1.0 + strength * (x - x.mean()) / (x.std() + 1e-9)
            return np.clip(m / m.mean(), 0.25, 2.2)

        water_mult = _centred(-irr, 0.42)      # rainfed districts react harder to water
        credit_mult = _centred(mech, 0.33)     # mechanised districts react harder to credit
        flat = np.ones(nd)

        BETA_MULT = {
            "rainfall_departure": water_mult, "reservoir_status": water_mult,
            "ndvi_anomaly": water_mult, "pmfby_claims": water_mult,
            "credit_depth": credit_mult, "subsidy_intensity": credit_mult,
            "mandi_price_index": credit_mult,
        }
        beta_d = {k: dgp["true_betas"][k] * BETA_MULT.get(k, flat)[:, None]
                  for k in REGRESSORS}
        beta_contrib = {k: beta_d[k] * X[k] for k in REGRESSORS}
        beta_sum = sum(beta_contrib.values())

        # Festive window and one-off interventions.
        festive = np.isin(month_of_year, dgp["festive_months"]).astype(float) * dgp["festive_effect"]
        interv = np.zeros(n)
        for name, spec in dgp["interventions"].items():
            m0 = pd.Period(spec["month"], freq="M")
            if m0 not in idx:
                continue
            pos = list(idx).index(m0)
            decay = np.exp(-np.arange(n - pos) / max(1, spec["decay_months"]))
            interv[pos:] += spec["effect"] * decay

        irregular = rng.normal(0, dgp["irregular_sigma"], (nd, n))

        # District scale: registrations scale with net sown area and mechanisation.
        dens = d["mech_tier"].map(cfg["tractor_base"]["density_per_1000ha"]).to_numpy()
        annual_units = d["net_sown_ha"].to_numpy() / 1000.0 * dens * 0.085   # ~8.5% of base/yr
        level = np.log(np.maximum(annual_units / 12.0, 3.0))[:, None]

        log_y = (level + trend + seas + cycle + beta_sum
                 + festive[None, :] + interv[None, :] + irregular)
        y = np.exp(log_y)

        # ---- long form ----------------------------------------------------------
        rows = {
            "district_id": np.repeat(d["district_id"].to_numpy(), n),
            "state": np.repeat(d["state"].to_numpy(), n),
            "month": np.tile(idx.astype(str), nd),
            "tractor_registrations": np.round(y.ravel(), 1),
        }
        for k in REGRESSORS:
            rows[k] = X[k].ravel()
            # per-district true beta, so the recovery test can check heterogeneity too
            rows[f"_true_beta_{k}"] = np.repeat(beta_d[k][:, 0], n)
        # Persist the true component split so the recovery test can compare directly.
        rows["_true_trend"] = (level + trend).ravel()
        rows["_true_seasonal"] = seas.ravel()
        rows["_true_cycle"] = cycle.ravel()
        rows["_true_regression"] = beta_sum.ravel()
        rows["_true_intervention"] = np.tile(festive + interv, nd)
        rows["_true_irregular"] = irregular.ravel()

        out = pd.DataFrame(rows)
        LOG.info("panel: %d districts x %d months = %d rows, %s..%s",
                 nd, n, len(out), idx[0], idx[-1])
        LOG.info("annual pilot-state registrations (latest 12m): %s",
                 f"{out[out.month >= str(idx[-12])].tractor_registrations.sum():,.0f}")
        return out


def build(spine: dict[str, pd.DataFrame], seed: int = 20260822) -> pd.DataFrame:
    df = DistrictSeriesConnector(spine, seed).run()
    write_table(df, CURATED / "district_series.parquet")
    return df


if __name__ == "__main__":
    from pipeline.common import read_table
    sp = {k: read_table(CURATED / f"geo_{k}.parquet") for k in ("districts", "blocks", "villages")}
    build(sp)
