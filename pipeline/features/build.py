"""Phase 6a -- engineered village features.

These go beyond the raw factor indices to the things that actually separate one
village's implement demand from another's: how much iron the village already has per
tractor (attach rate -- the direct whitespace measure), how much residue it has to
clear, how hard its soil is to work, how much of its fleet is due for replacement,
and what its neighbours are doing.

The spatial lag matters and is easy to under-rate: mechanisation diffuses
geographically. A village surrounded by high-adoption neighbours converts better than
an identical village in a low-adoption neighbourhood, and no non-spatial feature
captures that.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.common import CURATED, MARTS, Config, read_table, write_table, log
from pipeline.ingest.village_layers import CROPS, HOLDING_CLASSES

LOG = log("features")

K_NEIGHBOURS = 12


def _spatial_lag(v: pd.DataFrame, values: np.ndarray, k: int = K_NEIGHBOURS) -> np.ndarray:
    """Mean of a variable over each village's k nearest neighbours, within state.

    Computed in coordinate chunks so the 52k-village states never build a full
    pairwise matrix.
    """
    out = np.full(len(v), np.nan)
    lon = v["lon"].to_numpy(); lat = v["lat"].to_numpy()
    for st in v["state"].unique():
        idx = np.where(v["state"].to_numpy() == st)[0]
        # order by longitude so a sliding window contains the true neighbours
        order = idx[np.argsort(lon[idx])]
        P = np.column_stack([lon[order] * 95.0, lat[order] * 111.0])
        val = values[order]
        win = 400
        for s0 in range(0, len(order), 500):
            sl = slice(s0, min(s0 + 500, len(order)))
            lo, hi = max(0, s0 - win), min(len(order), s0 + 500 + win)
            D = ((P[sl, None, :] - P[None, lo:hi, :]) ** 2).sum(-1)
            kk = min(k + 1, D.shape[1])
            nn = np.argpartition(D, kk - 1, axis=1)[:, :kk]
            # drop self (distance 0) then average
            take = np.take(val[lo:hi], nn)
            selfpos = np.take(D, nn) == 0
            take = np.where(selfpos, np.nan, take)
            out[order[sl]] = np.nanmean(take, axis=1)
    return np.nan_to_num(out, nan=float(np.nanmean(values)))


def build(seed: int = 20260822) -> pd.DataFrame:
    v = read_table(CURATED / "geo_villages.parquet").reset_index(drop=True)
    d = read_table(CURATED / "geo_districts.parquet").set_index("district_id")
    L = read_table(CURATED / "village_layers.parquet").set_index("village_id").loc[v["village_id"]]
    A = read_table(CURATED / "village_assets.parquet").set_index("village_id").loc[v["village_id"]]
    # PHASE 2 BOUNDARY. village_sku_scores now carries both product lines. Everything
    # below this point still rolls up to a single un-keyed "demand" number, so summing
    # the two here would add a 7-lakh tractor to a 42k cultivator and call it units.
    # Scoped to implements until each rollup gains product_line as a group key --
    # which keeps every number on screen today exactly what it was.
    S = read_table(CURATED / "village_sku_state.parquet")
    S = S[S["product_line"] == "implements"]
    series = read_table(CURATED / "district_series.parquet")

    f = pd.DataFrame({"village_id": v["village_id"].to_numpy(),
                      "district_id": v["district_id"].to_numpy(),
                      "block_id": v["block_id"].to_numpy(),
                      "state": v["state"].to_numpy(),
                      "lon": v["lon"].to_numpy(), "lat": v["lat"].to_numpy()})

    # ---- structure ----------------------------------------------------------
    f["net_sown_ha"] = v["net_sown_ha"].to_numpy()
    f["rural_population"] = v["rural_population"].to_numpy()
    f["households"] = v["households"].to_numpy()
    for c in CROPS:
        f[f"crop_{c}"] = L[f"crop_{c}"].to_numpy()
    for c in HOLDING_CLASSES:
        f[f"hold_{c}"] = L[f"hold_{c}"].to_numpy()

    f["crop_entropy"] = L["crop_entropy"].to_numpy()
    f["dominant_crop"] = L["dominant_crop"].to_numpy()
    f["cropping_intensity"] = L["cropping_intensity"].to_numpy()
    f["high_value_share"] = L["high_value_share"].to_numpy()
    f["gross_cropped_ha"] = L["gross_cropped_ha"].to_numpy()
    f["avg_holding_ha"] = L["avg_holding_ha"].to_numpy()
    f["holding_gini"] = L["holding_gini"].to_numpy()
    f["fragmentation"] = L["fragmentation"].to_numpy()
    f["large_holding_share"] = L["large_holding_share"].to_numpy()
    f["small_marginal_share"] = (L["hold_marginal"] + L["hold_small"]).to_numpy()
    f["n_holdings"] = L["n_holdings"].to_numpy()

    # ---- water --------------------------------------------------------------
    f["irrigation_ratio"] = L["irrigation_ratio"].to_numpy()
    irr_src_stability = np.clip(0.45 + 0.55 * f["irrigation_ratio"], 0, 1)
    f["irrigation_reliability"] = f["irrigation_ratio"] * irr_src_stability

    # Rainfall volatility from the district monthly panel: risk appetite proxy.
    rv = (series.groupby("district_id")["rainfall_departure"]
          .agg(rainfall_volatility="std", rainfall_mean="mean"))
    drought_freq = (series.assign(dry=series["rainfall_departure"] < -0.8)
                    .groupby("district_id")["dry"].mean().rename("drought_frequency"))
    f = f.merge(rv, left_on="district_id", right_index=True, how="left")
    f = f.merge(drought_freq, left_on="district_id", right_index=True, how="left")

    # ---- soil ---------------------------------------------------------------
    for c in ["texture", "hardness", "ph", "oc_pct", "workability", "draft_requirement",
              "depth_cm", "slope_pct"]:
        f[f"soil_{c}" if c not in ("workability", "draft_requirement") else c] = L[c].to_numpy()
    f["residue_burden_t"] = L["residue_burden_t"].to_numpy()
    f["residue_burden_per_ha"] = f["residue_burden_t"] / np.maximum(f["gross_cropped_ha"], 1e-6)

    # ---- mechanisation ------------------------------------------------------
    f["tractors"] = A["tractors"].to_numpy()
    f["tractor_density"] = A["tractor_density"].to_numpy()
    f["hp_mix_skew"] = A["hp_mix_skew"].to_numpy()
    f["mean_hp"] = A["mean_hp"].to_numpy()
    f["fleet_mean_age"] = A["fleet_mean_age"].to_numpy()
    # Farm power kW/ha, on the same basis as the PwC benchmark the Excel quotes
    # (Punjab ~6.01, Haryana ~5.49). That figure is TOTAL farm power, so counting
    # tractors alone would understate it by roughly half and make the benchmark
    # meaningless. Irrigation pumps, self-propelled machines and draught/human power
    # are added on the same per-hectare basis.
    f["tractor_power_kw_ha"] = (f["tractors"] * f["mean_hp"] * 0.746) / np.maximum(f["net_sown_ha"], 1e-6)
    pump_kw_ha = f["irrigation_ratio"] * 2.05          # electric/diesel lift irrigation
    selfprop_kw_ha = f["tractor_power_kw_ha"] * 0.14   # tillers, reapers, threshers
    draught_kw_ha = np.clip(0.42 - 0.16 * f["tractor_power_kw_ha"], 0.04, 0.42)
    human_kw_ha = np.clip(0.10 + 0.05 * f["small_marginal_share"], 0.05, 0.18)
    f["farm_power_kw_ha"] = (f["tractor_power_kw_ha"] + pump_kw_ha
                             + selfprop_kw_ha + draught_kw_ha + human_kw_ha)

    # ---- distribution & services -------------------------------------------
    for c in ["dealer_distance_km", "dealer_accessibility", "service_density", "spares_index",
              "demo_activity", "chc_density", "rental_ecosystem", "kcc_penetration",
              "farm_income_inr", "credit_depth", "approval_rate", "affordable_ticket_inr"]:
        f[c] = A[c].to_numpy()
    f["income_per_ha"] = f["farm_income_inr"] / np.maximum(f["net_sown_ha"], 1e-6)

    # Market access: distance to the nearest market town, proxied by block centroid
    # spacing, weighted by the block's economic mass.
    blk = read_table(CURATED / "geo_blocks.parquet").set_index("block_id")
    f["market_access"] = np.clip(
        1.0 / (1.0 + np.hypot((f["lon"] - f["block_id"].map(blk["lon"])) * 95.0,
                              (f["lat"] - f["block_id"].map(blk["lat"])) * 111.0) / 12.0), 0, 1)

    # ---- attach rate and replacement (the whitespace measures) --------------
    agg = S.groupby("village_id", observed=True).agg(
        implements_owned=("owned", "sum"),
        implements_addressable=("addressable", "sum"),
        implements_headroom=("headroom", "sum"),
        replacement_units_yr=("replacement_units_yr", "sum"))
    f = f.merge(agg, left_on="village_id", right_index=True, how="left")
    f["attach_rate"] = f["implements_owned"] / np.maximum(f["tractors"], 1e-6)
    f["penetration_overall"] = f["implements_owned"] / np.maximum(f["implements_addressable"], 1e-6)
    f["replacement_pressure"] = f["replacement_units_yr"] / np.maximum(f["tractors"], 1e-6)

    # ---- spatial spillover --------------------------------------------------
    # Neighbours' adoption, not the village's own -- this is the diffusion term.
    f["peer_attach_rate"] = _spatial_lag(f, f["attach_rate"].to_numpy())
    f["peer_tractor_density"] = _spatial_lag(f, f["tractor_density"].to_numpy())
    f["peer_income_per_ha"] = _spatial_lag(f, f["income_per_ha"].to_numpy())
    f["adoption_gap_vs_peers"] = f["attach_rate"] - f["peer_attach_rate"]

    f["provenance"] = "simulated"
    LOG.info("features: %d villages x %d columns", len(f), f.shape[1])
    LOG.info("attach rate  p10 %.2f  median %.2f  p90 %.2f",
             *f["attach_rate"].quantile([.1, .5, .9]))
    LOG.info("farm power kW/ha by state (PwC: Punjab ~6.0):\n%s",
             f.groupby("state")["farm_power_kw_ha"].median().round(2).to_string())

    write_table(f, MARTS / "village_features.parquet")
    _write_dictionary(f)
    return f


def _write_dictionary(f: pd.DataFrame) -> None:
    """Machine-readable feature dictionary, surfaced in the UI feature explorer."""
    groups = {
        "crop_": "cropping pattern", "hold_": "land holding", "soil_": "soil",
        "peer_": "spatial spillover", "residue": "residue", "dealer": "distribution",
    }
    rows = []
    for c in f.columns:
        if c in ("village_id", "district_id", "block_id", "state", "provenance"):
            continue
        grp = next((v for k, v in groups.items() if c.startswith(k) or k in c), "derived")
        rows.append({"feature": c, "group": grp,
                     "dtype": str(f[c].dtype),
                     "nunique": int(f[c].nunique()) if f[c].dtype == object else None,
                     "provenance": "simulated"})
    write_table(pd.DataFrame(rows), MARTS / "feature_dictionary.parquet")


if __name__ == "__main__":
    build()
