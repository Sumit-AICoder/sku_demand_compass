"""Village x SKU current state, and the district x SKU monthly sales history.

Two outputs:

  village_sku_state    addressable fleet, units already owned, penetration, and the
                       annual replacement echo -- the inputs to Phase 7's
                       Addressable() term and to the attach-rate whitespace measure.

  district_sku_monthly 60 months of implement sales by district and SKU. No public
                       series exists for this (see the plan's open items), so it is
                       generated from the same latent demand process and used for the
                       seasonality view and replacement modelling, not for estimating
                       elasticities -- those come from the real-shaped tractor series.

Addressability is deliberately strict: an implement is sellable only into the slice
of the village fleet inside its HP band, scaled by how well the village's holding
sizes and crop mix suit it. A 7 ft rotavator in a village of 0.6 ha marginal holdings
with 25 HP tractors has almost no addressable market, and the model should say so.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.common import CURATED, Config, read_table, write_table, log
from pipeline.simulate.assets import HP_BANDS, HP_BAND_RANGE
from pipeline.ingest.village_layers import CROPS, HOLDING_CLASSES

LOG = log("simulate.sku_history")


def hp_band_overlap(sku: dict) -> np.ndarray:
    """Fraction of each HP band that falls inside the SKU's usable HP window."""
    if sku.get("self_propelled"):
        return np.ones(len(HP_BANDS))          # not gated on the tractor fleet
    lo, hi = sku["hp_min"], sku["hp_max"]
    out = []
    for b in HP_BANDS:
        b0, b1 = HP_BAND_RANGE[b]
        out.append(max(0.0, min(hi, b1) - max(lo, b0)) / (b1 - b0))
    return np.asarray(out)


def build(spine, layers, assets, seed=20260822):
    rng = np.random.default_rng(seed + 41)
    v = spine["villages"].reset_index(drop=True)
    d = spine["districts"].set_index("district_id")
    L = layers.set_index("village_id").loc[v["village_id"]]
    A = assets.set_index("village_id").loc[v["village_id"]]

    crop_mat = L[[f"crop_{c}" for c in CROPS]].to_numpy()
    hold_mat = L[[f"hold_{c}" for c in HOLDING_CLASSES]].to_numpy()
    fleet = A[[f"tractors_{b}" for b in HP_BANDS]].to_numpy()
    tier = v["district_id"].map(d["mech_tier"]).to_numpy()

    # Adoption index: how far this village is along its mechanisation curve. Drives
    # how much of the addressable market is ALREADY served -- and therefore where
    # the headroom is.
    adopt = np.clip(
        0.28 * pd.Series(tier).map({"high": 1.0, "medium": 0.6, "low": 0.28}).to_numpy()
        + 0.30 * A["dealer_accessibility"].to_numpy()
        + 0.22 * np.clip(A["credit_depth"].to_numpy(), 0, 1)
        + 0.20 * np.clip(L["avg_holding_ha"].to_numpy() / 5.0, 0, 1)
        + rng.normal(0, 0.06, len(v)), 0.02, 0.98)

    irr = L["irrigation_ratio"].to_numpy()
    residue = L["residue_burden_t"].to_numpy()
    residue_idx = np.clip(residue / max(np.percentile(residue, 90), 1e-6), 0, 1.5)

    rows = []
    for sku in Config.skus():
        band = hp_band_overlap(sku)
        base_fleet = fleet @ band                              # tractors in band

        size_fit = hold_mat @ np.array([sku["farm_size_fit"][c] for c in HOLDING_CLASSES])
        crop_fit = crop_mat @ np.array([sku["crop_fit"][c] for c in CROPS])

        gate = np.ones(len(v))
        if sku.get("requires_irrigation"):
            gate *= np.clip(irr / 0.55, 0, 1.2)
        if sku.get("requires_residue"):
            gate *= np.clip(residue_idx, 0, 1.3)

        addressable = base_fleet * size_fit * crop_fit * sku["attach_rate_ceiling"] * gate

        # Owned units: addressable served so far, discounted by how mature the
        # category is and how much of it custom hiring absorbs.
        maturity = {"mature": 0.80, "growth": 0.52, "policy_led": 0.34,
                    "emerging": 0.20, "premium": 0.26, "rental_led": 0.30}[sku["maturity"]]
        chc_absorb = 1.0 - sku["rental_substitutable"] * A["chc_density"].to_numpy() / 0.12 * 0.35
        owned = addressable * adopt * maturity * np.clip(chc_absorb, 0.45, 1.0) \
            * rng.lognormal(0, 0.18, len(v))
        owned = np.minimum(owned, addressable * 0.95)

        rows.append(pd.DataFrame({
            "village_id": v["village_id"].to_numpy(),
            "sku_id": sku["id"],
            "category": sku["category"],
            "fleet_in_band": base_fleet,
            "size_fit": size_fit,
            "crop_fit": crop_fit,
            "gate": gate,
            "addressable": addressable,
            "owned": owned,
            "penetration": np.divide(owned, addressable, out=np.zeros(len(v)),
                                     where=addressable > 1e-9),
            "headroom": np.maximum(addressable - owned, 0.0),
            # Replacement echo: the installed base retiring each year.
            "replacement_units_yr": owned / float(sku["life_years"]),
        }))

    state = pd.concat(rows, ignore_index=True)
    state["provenance"] = "simulated"

    LOG.info("village x SKU state: %d rows | mean penetration %.2f | total owned %.0f",
             len(state), state["penetration"].mean(), state["owned"].sum())
    write_table(state, CURATED / "village_sku_state.parquet")

    # ---- district x SKU monthly history ------------------------------------
    hist = _monthly_history(state, v, spine["districts"], seed)
    write_table(hist, CURATED / "district_sku_monthly.parquet")
    return state, hist


def _monthly_history(state, villages, districts, seed):
    """60 months of district x SKU sales, shaped by the SKU's seasonal window."""
    rng = np.random.default_rng(seed + 42)
    cfg = Config.sim()
    months = pd.period_range(end="2026-07", periods=cfg["market"]["months_history"], freq="M")
    moy = np.array([m.month for m in months])

    vd = villages.set_index("village_id")["district_id"]
    st = state.assign(district_id=state["village_id"].map(vd))
    agg = st.groupby(["district_id", "sku_id"], observed=True).agg(
        addressable=("addressable", "sum"), owned=("owned", "sum"),
        replacement=("replacement_units_yr", "sum")).reset_index()

    out = []
    for sku in Config.skus():
        sub = agg[agg.sku_id == sku["id"]]
        if not len(sub):
            continue
        # Seasonal weights: the SKU's peak months from the Seasonality sheet, softened
        # so off-peak months are damped rather than zeroed.
        w = np.where(np.isin(moy, sku["season"]), 1.0, 0.28)
        w = w / w.mean()
        growth = (1 + cfg["sales_dgp"]["trend_drift_annual"]) ** (np.arange(len(months)) / 12.0)

        # Annual new+replacement demand actually transacted, ~9% of headroom a year.
        annual = (sub["addressable"] - sub["owned"]).clip(lower=0) * 0.09 + sub["replacement"]
        base = (annual / 12.0).to_numpy()[:, None]
        units = base * w[None, :] * growth[None, :] * rng.lognormal(0, 0.22, (len(sub), len(months)))

        out.append(pd.DataFrame({
            "district_id": np.repeat(sub["district_id"].to_numpy(), len(months)),
            "sku_id": sku["id"],
            "category": sku["category"],
            "month": np.tile(months.astype(str), len(sub)),
            "units": np.round(units.ravel(), 2),
        }))

    hist = pd.concat(out, ignore_index=True)
    hist["provenance"] = "simulated"
    LOG.info("district x SKU monthly history: %d rows, %s..%s",
             len(hist), months[0], months[-1])
    return hist


if __name__ == "__main__":
    sp = {k: read_table(CURATED / f"geo_{k}.parquet") for k in ("districts", "blocks", "villages")}
    build(sp, read_table(CURATED / "village_layers.parquet"),
          read_table(CURATED / "village_assets.parquet"))
