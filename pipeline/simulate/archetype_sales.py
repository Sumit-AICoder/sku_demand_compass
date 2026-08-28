"""Daily sales history per archetype -- input panel for the "What drives sales" UCM.

Sonalika has no real daily/weekly sales feed (only annual estimates modelled in
operations.py), so this is entirely SIMULATED illustrative history: a data-generating
process built so an Unobserved Components Model can cleanly recover it -- deterministic
local linear trend, weekly + annual seasonality, and five exogenous effects with KNOWN
true coefficients (stored alongside, so parameter recovery is testable). It exists to
demonstrate the causal-decomposition method the client asked for, not to report real
historical sales -- badged 'simulated' throughout the UI.

Two real inputs anchor it to the rest of the app rather than inventing numbers from
nothing:
  * temperature -- the archetype's real IMD mean_temp (agroclimate mart), with a
                    realistic annual heat cycle layered on top.
  * competitor  -- the average price of the archetype's top-3 highest-selling rival
                    OEMs in its own districts (player_shares mart), turned into a
                    competitive-pressure index: cheaper/more aggressive rivals raise the
                    index, and its true coefficient is NEGATIVE by construction.
  * the archetype's annual sales LEVEL ties back to operations.py's deliveries_yr, so
    this daily story integrates to the same annual number shown on the other Review tabs.
"""
from __future__ import annotations

import hashlib
import time

import numpy as np
import pandas as pd

from pipeline.common import CURATED, MARTS, read_table, write_table, log, Manifest, FetchRecord

LOG = log("archetype_sales")

START, END = "2023-01-01", "2025-12-31"

# Fixed illustrative calendar of agri/tractor-buying occasions (approximate dates --
# several of these are lunar and shift year to year in reality; kept fixed here since
# this whole panel is dummy/illustrative).
HOLIDAYS_MMDD = [(1, 14), (3, 8), (4, 14), (4, 22), (8, 15),
                 (10, 2), (10, 12), (11, 1), (11, 15), (12, 25)]
# (month, day, length_days) campaign windows, timed around sowing/harvest + festivals.
PROMO_WINDOWS = [(2, 20, 12), (4, 25, 14), (6, 10, 10), (9, 20, 12), (10, 20, 16), (12, 5, 10)]

# Archetype-diagnosis-linked annual growth rate -- ties the daily trend back to the
# Review-tab-2 diagnosis instead of being an unrelated random number.
TREND_PCT_PER_YEAR = {"Product issue": -0.03, "Sales issue": 0.06, "Defend": 0.02, "Monitor": 0.0}

PEAK_SALES_DOY = 288    # mid-October -- matches this app's documented UCM finding elsewhere
PEAK_TEMP_DOY = 135     # mid-May -- pre-monsoon heat peak


def _seed(archetype_id: str) -> np.random.Generator:
    h = int(hashlib.sha256(archetype_id.encode()).hexdigest()[:8], 16)
    return np.random.default_rng(h)


def _competitor_price_base(mm: pd.DataFrame, ps: pd.DataFrame) -> dict[str, float]:
    """Per archetype: TIV-weighted average price_index of the top-3 highest-selling
    rival OEMs across the districts that archetype's micro-markets sit in."""
    ps = ps[ps["player"].str.lower() != "sonalika"]
    agg = ps.groupby(["district_id", "player"], as_index=False).agg(
        share=("share", "mean"), price_index=("price_index", "mean"))
    top3 = (agg.sort_values(["district_id", "share"], ascending=[True, False])
            .groupby("district_id").head(3))
    dist_price = top3.groupby("district_id")["price_index"].mean()

    tiv_by_ad = mm.groupby(["archetype_id", "district_id"], as_index=False)["tiv"].sum()
    tiv_by_ad["price"] = tiv_by_ad["district_id"].map(dist_price)
    tiv_by_ad = tiv_by_ad.dropna(subset=["price"])
    out: dict[str, float] = {}
    for aid, g in tiv_by_ad.groupby("archetype_id"):
        w = g["tiv"]
        out[str(aid)] = float((g["price"] * w).sum() / max(w.sum(), 1e-9))
    return out


def _mean_temp_by_archetype(mm: pd.DataFrame) -> dict[str, float]:
    out: dict[str, float] = {}
    for aid, g in mm.groupby("archetype_id"):
        w = g["tiv"]
        out[str(aid)] = float((g["mean_temp"] * w).sum() / max(w.sum(), 1e-9))
    return out


def build() -> pd.DataFrame:
    t0 = time.time()
    arch = read_table(MARTS / "archetype_ops.parquet")
    mm = read_table(MARTS / "micromarkets.parquet")
    ps = read_table(MARTS / "player_shares.parquet")

    temp_by_arch = _mean_temp_by_archetype(mm)
    comp_base_by_arch = _competitor_price_base(mm, ps)

    dates = pd.date_range(START, END, freq="D")
    n = len(dates)
    t = np.arange(n, dtype=float)
    doy = dates.dayofyear.to_numpy()
    dow = dates.dayofweek.to_numpy()

    is_holiday = np.zeros(n, dtype=int)
    for y in dates.year.unique():
        for mo, da in HOLIDAYS_MMDD:
            try:
                d = pd.Timestamp(year=int(y), month=mo, day=da)
            except ValueError:
                continue
            is_holiday[dates == d] = 1

    promo_base = np.zeros(n, dtype=bool)
    for y in dates.year.unique():
        for mo, da, length in PROMO_WINDOWS:
            try:
                d0 = pd.Timestamp(year=int(y), month=mo, day=da)
            except ValueError:
                continue
            promo_base |= np.asarray((dates >= d0) & (dates < d0 + pd.Timedelta(days=length)))

    frames, true_betas = [], []
    for _, row in arch.iterrows():
        aid = str(row["archetype_id"])
        rng = _seed(aid)
        # floor keeps every archetype's daily level comfortably above the noise sd below,
        # so even a "Monitor" archetype with near-zero modelled deliveries gets a fittable
        # (if small) signal rather than pure noise
        annual_target = max(float(row["deliveries_yr"]), 150.0)
        L0 = annual_target / 365.25

        trend_pct = TREND_PCT_PER_YEAR.get(row["diagnosis"], 0.0) + rng.normal(0, 0.01)
        slope = L0 * trend_pct / 365.25
        level = np.maximum(L0 + slope * t, 0.15 * L0)

        seasonal_annual = 0.35 * L0 * np.cos(2 * np.pi * (doy - PEAK_SALES_DOY) / 365.25)
        week_phase = rng.uniform(0, 2 * np.pi)
        seasonal_weekly = 0.12 * L0 * np.cos(2 * np.pi * dow / 7 + week_phase)
        baseline_true = level + seasonal_annual + seasonal_weekly

        # ---- exogenous drivers -----------------------------------------------
        mean_temp = temp_by_arch.get(aid, 26.0)
        temperature = (mean_temp + 9.0 * np.cos(2 * np.pi * (doy - PEAK_TEMP_DOY) / 365.25)
                       + rng.normal(0, 1.4, n))

        shift = int(rng.integers(-3, 4))
        is_promotion = np.roll(promo_base, shift).astype(int)

        price_drop_pct = np.zeros(n)
        idx = np.where(is_promotion == 1)[0]
        if len(idx):
            for block in np.split(idx, np.where(np.diff(idx) != 1)[0] + 1):
                depth = rng.uniform(5, 15)
                price_drop_pct[block] = np.clip(depth + rng.normal(0, 0.8, len(block)), 0, None)

        comp_price = comp_base_by_arch.get(aid, 1.0)
        pressure_base = (1.0 - comp_price) * 100          # cheaper rivals -> higher pressure
        walk = np.zeros(n)
        for i in range(1, n):
            walk[i] = 0.97 * walk[i - 1] + rng.normal(0, 0.6)
        spikes = np.zeros(n)
        for _ in range(4):
            s0 = int(rng.integers(0, n - 15))
            spikes[s0:s0 + int(rng.integers(8, 15))] += rng.uniform(6, 14)
        competitor = pressure_base + walk + spikes

        beta_temp = -0.03 * (L0 / 40.0)
        beta_holiday = 0.55 * L0 + rng.normal(0, 0.03 * L0)
        beta_promo = 0.30 * L0 + rng.normal(0, 0.02 * L0)
        beta_pricedrop = 0.035 * L0 + rng.normal(0, 0.002 * L0)
        beta_competitor = -0.010 * L0 + rng.normal(0, 0.0005 * L0)

        uplift = (beta_temp * (temperature - mean_temp) + beta_holiday * is_holiday
                 + beta_promo * is_promotion + beta_pricedrop * price_drop_pct
                 + beta_competitor * competitor)
        # scale with L0 rather than a fixed absolute floor -- a fixed floor would swamp
        # the smallest archetypes' entire signal (caught via a bad R2/backtest on them)
        noise = rng.normal(0, max(0.08 * L0, 0.05), n)
        actual = np.clip(baseline_true + uplift + noise, 0, None)

        frames.append(pd.DataFrame({
            "archetype_id": aid, "date": dates, "actual_sales": actual,
            "temperature": temperature, "is_holiday": is_holiday,
            "is_promotion": is_promotion, "price_drop_pct": price_drop_pct,
            "competitor": competitor,
        }))
        true_betas.append({
            "archetype_id": aid, "L0_daily_mean": L0, "true_beta_temperature": beta_temp,
            "true_beta_is_holiday": beta_holiday, "true_beta_is_promotion": beta_promo,
            "true_beta_price_drop_pct": beta_pricedrop, "true_beta_competitor": beta_competitor,
            "reference_temp": mean_temp,
        })

    panel = pd.concat(frames, ignore_index=True)
    panel["provenance"] = "simulated"
    write_table(panel, CURATED / "archetype_sales_daily.parquet")
    write_table(pd.DataFrame(true_betas).assign(provenance="simulated"),
                CURATED / "archetype_sales_true_betas.parquet")

    Manifest.record(FetchRecord(
        source="archetype_sales_daily", mode="synthetic", rows=len(panel),
        provenance="simulated",
        vintage=f"illustrative daily history {START}..{END}; no real daily/weekly Sonalika "
                "sales feed exists (only annual estimates)",
        elapsed_s=round(time.time() - t0, 2)))
    LOG.info("archetype daily sales panel: %d archetypes x %d days = %d rows",
             arch["archetype_id"].nunique(), n, len(panel))
    return panel


if __name__ == "__main__":
    build()
