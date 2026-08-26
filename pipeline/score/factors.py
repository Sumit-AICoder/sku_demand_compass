"""Phase 4 -- the ten factor indices from the Excel "Factors Listings" sheet.

Each factor group becomes a 0-100 index built from named sub-factors, computed at TWO
scopes because the two questions a planner asks need different answers:

  Fx        national percentile -- "is this village a better bet than that one,
            anywhere in the pilot geography?" This is what the propensity score uses,
            and it is what lets Punjab genuinely outrank MP on tractor base rather
            than being normalised into a tie.
  Fx_state  within-state percentile -- "which villages in MY state should I work
            first?" Punjab's mechanisation baseline sits so far above MP's that a
            national rank alone would compress most MP villages into the bottom
            decile and make within-MP targeting useless.

Both are written. The UI switches scope; the model always uses the national one.

Sub-factor -> feature wiring lives in FEATURE_MAP. Anything the feature layer cannot
supply is dropped with a warning and the remaining sub-factor weights renormalise, so a
missing input degrades the index rather than silently zeroing it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.common import CURATED, MARTS, Config, pct_rank, read_table, write_table, log

LOG = log("score.factors")

# sub-factor key -> (feature column, invert?)
# invert=True means a HIGH feature value implies a LOW index contribution.
FEATURE_MAP = {
    # F1 farm economics
    "crop_realisation":   ("income_per_ha", False),
    "mandi_price_index":  ("income_per_ha", False),
    "msp_exposure":       ("_msp_exposure", False),
    "credit_depth":       ("credit_depth", False),
    "input_intensity":    ("cropping_intensity", False),
    # F2 land holding
    "avg_holding_ha":     ("avg_holding_ha", False),
    "fragmentation":      ("fragmentation", True),
    "large_holding_share": ("large_holding_share", False),
    # F3 tractor base
    "tractor_density":    ("tractor_density", False),
    "new_sales_trend":    ("_registration_cagr", False),
    "hp_mix_skew":        ("hp_mix_skew", False),
    # F4 mechanisation
    "farm_power_kw_ha":   ("farm_power_kw_ha", False),
    "rural_wage_index":   ("_rural_wage", False),
    "labour_scarcity":    ("_labour_scarcity", False),
    "outmigration":       ("_outmigration", False),
    # F5 cropping pattern
    "cropping_intensity": ("cropping_intensity", False),
    "high_value_share":   ("high_value_share", False),
    "crop_diversity":     ("crop_entropy", False),
    # F6 policy & subsidy
    "smam_intensity":     ("_subsidy_intensity", False),
    "chc_programme":      ("chc_density", False),
    "fpo_density":        ("_fpo_density", False),
    # F7 monsoon & water
    "rainfall_departure": ("_rainfall_departure", False),
    "irrigation_ratio":   ("irrigation_reliability", False),
    "reservoir_status":   ("_reservoir_status", False),
    "ndvi_anomaly":       ("_ndvi_anomaly", False),
    # F8 custom hiring
    "chc_density":        ("chc_density", False),
    "rental_ecosystem":   ("rental_ecosystem", False),
    "agri_service_prov":  ("rental_ecosystem", False),
    # F9 technology
    "precision_adoption": ("_precision_adoption", False),
    "progressive_farmer": ("_progressive_farmer", False),
    "digital_access":     ("_digital_access", False),
    # F10 distribution
    "dealer_accessibility": ("dealer_accessibility", False),
    "service_density":      ("service_density", False),
    "spares_index":         ("spares_index", False),
    "demo_activity":        ("demo_activity", False),
}


def _derive(f: pd.DataFrame, series: pd.DataFrame, assets: pd.DataFrame) -> pd.DataFrame:
    """Attach the district-level drivers and the composite proxies the factors need."""
    # Latest 12 months of each district driver, allocated to its villages.
    last = series[series["month"] >= sorted(series["month"].unique())[-12]]
    dmean = last.groupby("district_id")[
        ["rainfall_departure", "reservoir_status", "ndvi_anomaly", "mandi_price_index",
         "msp_change", "subsidy_intensity", "rural_wage_index"]].mean()

    for c, alias in [("rainfall_departure", "_rainfall_departure"),
                     ("reservoir_status", "_reservoir_status"),
                     ("ndvi_anomaly", "_ndvi_anomaly"),
                     ("msp_change", "_msp_exposure"),
                     ("subsidy_intensity", "_subsidy_intensity"),
                     ("rural_wage_index", "_rural_wage")]:
        f[alias] = f["district_id"].map(dmean[c])

    f["_registration_cagr"] = f["village_id"].map(
        assets.set_index("village_id")["registration_cagr"])

    # Labour scarcity: high wages plus low small-holder labour supply. The Excel's
    # mechanisation logic is that scarcity, not wealth, forces adoption.
    f["_labour_scarcity"] = (pct_rank(f["_rural_wage"]) / 100.0) * 0.6 \
        + (1 - f["small_marginal_share"]) * 0.4
    f["_outmigration"] = np.clip(0.35 + 0.45 * (1 - f["irrigation_reliability"])
                                 - 0.20 * f["income_per_ha"] / f["income_per_ha"].median(), 0, 1)
    f["_fpo_density"] = np.clip(0.25 + 0.5 * f["market_access"] + 0.3 * f["high_value_share"], 0, 1.2)
    f["_progressive_farmer"] = np.clip(
        0.5 * np.clip(f["avg_holding_ha"] / 5.0, 0, 1) + 0.5 * np.clip(f["income_per_ha"]
        / f["income_per_ha"].quantile(0.9), 0, 1), 0, 1)
    f["_digital_access"] = np.clip(0.30 + 0.45 * f["market_access"]
                                   + 0.25 * f["_progressive_farmer"], 0, 1)
    f["_precision_adoption"] = np.clip(
        0.45 * f["_progressive_farmer"] + 0.35 * f["high_value_share"]
        + 0.20 * f["dealer_accessibility"], 0, 1)
    return f


def build() -> pd.DataFrame:
    f = read_table(MARTS / "village_features.parquet")
    series = read_table(CURATED / "district_series.parquet")
    assets = read_table(CURATED / "village_assets.parquet")
    f = _derive(f, series, assets)

    factors = Config.factors()
    out = f[["village_id", "district_id", "state"]].copy()
    detail_rows = []

    for fid, spec in factors.items():
        parts, weights, used = [], [], []
        parts_state = []
        for sf in spec["subfactors"]:
            col, invert = FEATURE_MAP.get(sf["key"], (None, False))
            if col is None or col not in f.columns:
                LOG.warning("%s/%s: no feature for '%s' -- dropped", fid, spec["key"], sf["key"])
                continue
            flip = invert or sf["direction"] == "negative"
            r_nat = pct_rank(f[col], invert=flip)
            r_st = f.groupby("state")[col].transform(lambda s: pct_rank(s))
            if flip:
                r_st = 100.0 - r_st
            parts.append(r_nat.to_numpy())
            parts_state.append(r_st.to_numpy())
            weights.append(sf["weight"])
            used.append(sf["key"])
            out[f"{fid}_{sf['key']}"] = r_nat.to_numpy()

        if not parts:
            raise RuntimeError(f"{fid} has no usable sub-factors")
        W = np.array(weights) / np.sum(weights)          # renormalise after any drops
        out[fid] = np.column_stack(parts) @ W
        out[f"{fid}_state"] = np.column_stack(parts_state) @ W
        detail_rows.append({"factor": fid, "key": spec["key"], "label": spec["label"],
                            "direction": spec["direction"],
                            "subfactors_used": ", ".join(used),
                            "n_subfactors": len(used),
                            "village_evidence": spec["village_evidence"],
                            "excel_impact": spec["excel_impact"],
                            "provenance": "allocated"})

    out["provenance"] = "allocated"
    LOG.info("national factor indices, mean by state "
             "(F3 tractor base and F4 mechanisation should separate Punjab):\n%s",
             out.groupby("state")[list(factors)].mean().round(1).to_string())
    write_table(out, MARTS / "village_factors.parquet")
    write_table(pd.DataFrame(detail_rows), MARTS / "factor_definitions.parquet")
    return out


if __name__ == "__main__":
    build()
