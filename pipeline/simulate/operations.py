"""Simulated operational metrics per micro-market + the product-vs-sales-issue diagnosis.

Sonalika's actual sales, marketing activities, enquiries and deliveries are ITL data
(pending), so they are modelled here from the market structure with a realistic funnel
(activities -> enquiries -> deliveries) and badged. The point is the diagnosis the client
asked for on the Review stage:

  PRODUCT issue  -- poor product fit, share stays low across the WHOLE archetype (cannot
                    crack it anywhere); a new/adapted product is needed, not more selling.
  SALES issue    -- product is proven (good fit, some pockets cracked) but share is low in
                    others -- an execution / coverage / effort gap that selling can close.
  DEFEND         -- already winning; protect the share.
  MONITOR        -- too little demand to prioritise.

Writes micromarket_ops.parquet (per micro-market) and archetype_ops.parquet (rolled up).
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from pipeline.common import MARTS, read_table, write_table, log, Manifest, FetchRecord

LOG = log("operations")

# Sonalika product fit by HP belt (stronger in mid-HP, weaker in the >50 premium band and
# the low band where rivals lead) and by dominant crop (strong in wheat/rice plains, weaker
# in the cotton black-soil belt where local/Shaktiman lead). Modelled, ITL-pending.
BELT_FIT = {"<30 HP": 0.46, "31-40 HP": 0.63, "41-50 HP": 0.71, ">50 HP": 0.52, "(custom)": 0.6}
# For tractors the HP belt is not "which implement fits" but "which of our own models sells
# here", so it needs its own curve: Sonalika's range is strongest in the 41-50 band, present
# but under-weight above 50, and thin in the compact end.
BELT_FIT_TRACTOR = {"<30 HP": 0.40, "31-40 HP": 0.68, "41-50 HP": 0.74, ">50 HP": 0.58,
                    "(custom)": 0.6}
CROP_FIT = {"Wheat": 0.68, "Rice": 0.66, "Wheat-Rice": 0.71, "Rice-Wheat": 0.71,
            "Cotton": 0.47, "Soybean": 0.57, "Sugarcane": 0.60, "Mixed": 0.55}


def _diagnose(product_fit: float, share: float, cracked: float, potential: float,
              lo_pot: float) -> str:
    if potential < lo_pot:
        return "Monitor"
    if product_fit < 0.48:
        return "Product issue"
    if share >= 0.10:
        return "Defend"
    return "Sales issue"      # good fit but under-penetrated -> execution/coverage gap


def rollup(mm: pd.DataFrame, lo_pot: float | None = None) -> pd.DataFrame:
    """Archetype-grain operations, rolled up from micro-market grain.

    Additive columns sum; rates are TIV-weighted; coverage is a plain mean. Split out of
    `build()` so the API can re-roll against a customised taxonomy -- otherwise combining two
    zones on Configure changes the archetypes on Define and leaves Plan and Act showing the
    shipped ones.
    """
    if lo_pot is None:
        lo_pot = float(mm["potential_units_yr"].quantile(0.20))
    rows = []
    for aid, g in mm.groupby("archetype_id"):
        tiv = float(g["tiv"].sum())
        share = float((g["sonalika_share"] * g["tiv"]).sum() / max(tiv, 1))
        fit = float((g["product_fit"] * g["tiv"]).sum() / max(tiv, 1))
        cracked_pct = float((g["sonalika_share"] >= 0.10).mean())
        pot = float(g["potential_units_yr"].sum())
        diag = _diagnose(fit, share, cracked_pct, pot, lo_pot * len(g))
        # sub-structure: a good-fit archetype with some pockets cracked but many not is the
        # clearest SALES issue; refine the label with the spread.
        if diag == "Sales issue" and cracked_pct < 0.15 and fit < 0.55:
            diag = "Product issue"
        rows.append({
            "archetype_id": aid, "archetype": g["archetype"].iloc[0],
            "base_name": g["base_name"].iloc[0], "hp_belt": g["hp_belt"].iloc[0],
            "subzone_id": g["subzone_id"].iloc[0], "subzone": g["subzone"].iloc[0],
            "n_micromarkets": int(len(g)), "n_villages": int(g["n_villages"].sum()),
            "tiv": round(tiv), "avg_sonalika_share": round(share, 4),
            "potential_units_yr": round(pot),
            # unrounded so tiny archetypes don't collapse to zero sales
            "sonalika_sales_units": int(round((g["sonalika_share"] * g["potential_units_yr"]).sum())),
            "activities_yr": int(g["activities_yr"].sum()),
            "enquiries_yr": int(g["enquiries_yr"].sum()),
            "deliveries_yr": int(g["deliveries_yr"].sum()),
            "conversion_rate": round(float((g["conversion_rate"] * g["tiv"]).sum() / max(tiv, 1)), 3),
            "product_fit": round(fit, 3),
            "sales_effort": round(float((g["sales_effort"] * g["tiv"]).sum() / max(tiv, 1)), 3),
            "cracked_pct": round(cracked_pct, 3),
            "sales_coverage": round(float(g["dealer_accessibility"].mean()), 3),
            "service_coverage": round(float(g["service_index"].mean()), 3),
            "states": ", ".join(g["state"].value_counts().head(3).index),
            "diagnosis": diag, "provenance": "simulated",
        })
    return pd.DataFrame(rows).sort_values("potential_units_yr", ascending=False)


def build(seed: int = 20260828) -> None:
    """Operational metrics per micro-market, for every product line.

    The micro-market and its archetype are shared -- they describe the fleet -- but the
    demand, the funnel and the diagnosis hanging off them are per line. So this runs once per
    line over the same 23,389 micro-markets, joining that line's demand in.
    """
    t0 = time.time()
    vm = read_table(MARTS / "village_micromarket.parquet")
    vt = read_table(MARTS / "village_totals.parquet")
    dem = (vt.merge(vm, on="village_id")
             .groupby(["micro_market_id", "product_line"], as_index=False)
             .agg(potential_units_yr=("potential_units_yr", "sum"),
                  potential_value_inr=("potential_value_inr", "sum"),
                  addressable=("addressable", "sum")))

    grain, arch = [], []
    for line, d in dem.groupby("product_line"):
        g, a = _one_line(line, d, seed)
        grain.append(g); arch.append(a)
    mm_out = pd.concat(grain, ignore_index=True)
    ar_out = pd.concat(arch, ignore_index=True)
    write_table(mm_out, MARTS / "micromarket_ops.parquet")
    write_table(ar_out, MARTS / "archetype_ops.parquet")
    LOG.info("operations: %d micro-market rows, %d archetype rows across %d lines in %.1fs",
             len(mm_out), len(ar_out), dem["product_line"].nunique(), time.time() - t0)
    Manifest.record(FetchRecord(
        source="operations", mode="synthetic", rows=len(mm_out), provenance="simulated",
        vintage="modelled sales funnel + product/sales diagnosis (ITL pending)",
        elapsed_s=round(time.time() - t0, 2)))


def _one_line(line: str, demand: pd.DataFrame, seed: int):
    rng = np.random.default_rng(seed + (0 if line == "implements" else 7))
    mm = read_table(MARTS / "micromarkets.parquet").copy()
    # The fleet columns stay; the demand columns are replaced by this line's.
    mm = mm.drop(columns=["potential_units_yr", "potential_value_inr", "addressable"],
                 errors="ignore").merge(
        demand.drop(columns="product_line"), on="micro_market_id", how="left")
    for c in ("potential_units_yr", "potential_value_inr", "addressable"):
        mm[c] = mm[c].fillna(0.0)
    mm["product_line"] = line

    price_per_unit = (mm["potential_value_inr"] / mm["potential_units_yr"].clip(lower=1)).clip(1e4, 1e7)

    # ---- product fit + sales effort (latent, modelled) ----------------------
    fit_map = BELT_FIT_TRACTOR if line == "tractors" else BELT_FIT
    belt = mm["hp_belt"].map(fit_map).fillna(0.55)
    crop = mm["crop_label"].map(CROP_FIT).fillna(0.55)
    mm["product_fit"] = np.clip(0.5 * belt + 0.5 * crop
                                + rng.normal(0, 0.06, len(mm)), 0.2, 0.95)
    eff = ((np.log1p(mm["tiv"]) - np.log1p(mm["tiv"]).mean()) / np.log1p(mm["tiv"]).std()
           + rng.normal(0, 0.8, len(mm)))
    mm["sales_effort"] = np.clip(1 / (1 + np.exp(-eff)), 0.08, 0.95)

    # ---- funnel: deliveries -> enquiries -> activities (annual) --------------
    mm["deliveries_yr"] = np.round(mm["sonalika_share"] * mm["potential_units_yr"]).astype(int)
    conv = np.clip(0.12 + 0.22 * mm["product_fit"] + rng.normal(0, 0.03, len(mm)), 0.05, 0.5)
    mm["conversion_rate"] = conv
    mm["enquiries_yr"] = np.ceil(mm["deliveries_yr"] / conv).astype(int)
    enq_rate = np.clip(0.10 + 0.16 * mm["sales_effort"] + rng.normal(0, 0.03, len(mm)), 0.05, 0.4)
    mm["activities_yr"] = np.ceil(mm["enquiries_yr"] / enq_rate).astype(int)
    mm["sonalika_sales_units"] = mm["deliveries_yr"]
    mm["sonalika_sales_value_inr"] = (mm["deliveries_yr"] * price_per_unit).round()

    # ---- coverage: sales (real-ish accessibility) + service (dummy, sparser) ----------
    # dealer_accessibility (0-1) is the modelled SALES coverage proxy per micro-market.
    # SERVICE networks are sparser and lag sales, so service coverage is a discounted,
    # noisier version -- pure dummy until ITL shares the service master.
    mm["service_index"] = np.clip(mm["dealer_accessibility"] * 0.70
                                  + rng.normal(0, 0.07, len(mm)), 0.02, 0.98)
    mm["service_distance_km"] = np.round((1 - mm["service_index"]) * 35 + 4, 1)

    lo_pot = mm["potential_units_yr"].quantile(0.20)
    cracked = (mm["sonalika_share"] >= 0.10).astype(float)
    mm["diagnosis"] = [
        _diagnose(pf, sh, cr, pot, lo_pot)
        for pf, sh, cr, pot in zip(mm["product_fit"], mm["sonalika_share"],
                                   cracked, mm["potential_units_yr"])]
    mm["provenance"] = "simulated"

    ops_cols = ["micro_market_id", "district_id", "district", "state", "archetype_id",
                "archetype", "base_name", "subzone_id", "subzone", "hp_belt", "tiv_tier",
                "n_villages", "lon", "lat", "tiv", "mean_hp", "sonalika_share",
                "potential_units_yr", "sonalika_sales_units", "sonalika_sales_value_inr",
                "activities_yr", "enquiries_yr", "deliveries_yr", "conversion_rate",
                "product_fit", "sales_effort", "dealer_accessibility", "service_index",
                "service_distance_km", "diagnosis", "provenance"]
    # ---- archetype rollup + diagnosis ---------------------------------------
    arch = rollup(mm, lo_pot).assign(product_line=line)
    LOG.info("operations [%s]: %d micro-markets | diagnosis: %s",
             line, len(mm), arch["diagnosis"].value_counts().to_dict())
    return mm[ops_cols + ["product_line"]], arch


if __name__ == "__main__":
    build()
