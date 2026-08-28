"""Village tractor base, dealer/service network, custom hiring and finance.

These are the layers with no public source at all (see `simulated_by_design` in
sources.yaml), so they are generated here from sim_params.yaml and stamped
provenance="simulated" throughout.

The tractor base matters most: it is the addressable-market gate in Phase 7. An
implement is only sellable into the slice of the village's tractor fleet that sits
in its HP band, so the HP mix -- not just the tractor count -- drives SKU demand.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.common import CURATED, Config, read_table, write_table, log

LOG = log("simulate.assets")
HP_BANDS = ["20_35", "35_45", "45_60", "60_plus"]
HP_BAND_RANGE = {"20_35": (20, 35), "35_45": (35, 45), "45_60": (45, 60), "60_plus": (60, 90)}


def build_tractor_base(spine, layers, series, seed=20260822) -> pd.DataFrame:
    """Allocate the district tractor fleet to villages, with an HP mix and an age profile."""
    rng = np.random.default_rng(seed + 31)
    cfg = Config.sim()["tractor_base"]
    v = spine["villages"]
    d = spine["districts"].set_index("district_id")
    tier = v["district_id"].map(d["mech_tier"])

    # Fleet size scales with net sown area, modulated by irrigation and holding size:
    # irrigated, larger-holding villages own disproportionately more tractors.
    L = layers.set_index("village_id")
    irr = L.loc[v["village_id"], "irrigation_ratio"].to_numpy()
    avg_hold = L.loc[v["village_id"], "avg_holding_ha"].to_numpy()

    dens = tier.map(cfg["density_per_1000ha"]).astype(float).to_numpy()
    modifier = (0.72 + 0.45 * irr) * (0.80 + 0.30 * np.clip(avg_hold / 3.0, 0, 2.0))
    noise = rng.lognormal(0, cfg["density_cv"], len(v))
    tractors = np.maximum(1.0, v["net_sown_ha"].to_numpy() / 1000.0 * dens * modifier * noise)

    # Rescale so village totals reproduce the district fleet implied by the density.
    dist_target = (d["net_sown_ha"] / 1000.0
                   * d["mech_tier"].map(cfg["density_per_1000ha"]).astype(float))
    got = pd.Series(tractors).groupby(v["district_id"].to_numpy()).sum()
    tractors *= v["district_id"].map(dist_target / got).to_numpy()

    out = pd.DataFrame({"village_id": v["village_id"].to_numpy(),
                        "tractors": tractors})

    # HP mix by tier, perturbed per village, then shifted upward where holdings are large.
    base_mix = np.array([[cfg["hp_mix"][t][b] for b in HP_BANDS] for t in tier])
    shift = np.clip((avg_hold - 2.0) / 8.0, -0.15, 0.25)[:, None]
    tilt = np.array([-1.0, -0.4, 0.5, 0.9])[None, :]
    mix = np.clip(base_mix + shift * tilt * base_mix, 0.005, None)
    mix = mix / mix.sum(1, keepdims=True)

    for j, b in enumerate(HP_BANDS):
        out[f"tractors_{b}"] = out["tractors"] * mix[:, j]
    out["hp_mix_skew"] = mix[:, 2] + mix[:, 3]        # share of 45 HP+
    out["mean_hp"] = mix @ np.array([27.5, 40.0, 52.5, 72.0])

    out["tractor_density"] = out["tractors"] / (v["net_sown_ha"].to_numpy() / 1000.0)
    out["fleet_mean_age"] = np.clip(
        rng.lognormal(np.log(cfg["mean_age_years"]), cfg["age_sigma"] * 0.4, len(v)), 1.5, 22)
    out["registration_cagr"] = tier.map(cfg["registration_cagr"]).astype(float).to_numpy()
    out["provenance"] = "simulated"
    LOG.info("tractor fleet: %.0f total, density %.1f-%.1f per 1000ha",
             out["tractors"].sum(), out["tractor_density"].quantile(.05),
             out["tractor_density"].quantile(.95))
    return out


def build_network(spine, layers, seed=20260822) -> pd.DataFrame:
    """Place dealers by market-town gravity, then compute village accessibility.

    Dealers land in blocks, weighted by rural population and mechanisation tier, so
    the network concentrates where the market is -- which is what creates the
    genuine distribution whitespace the dashboard is meant to surface.
    """
    rng = np.random.default_rng(seed + 32)
    cfg = Config.sim()["dealer_network"]
    chc_cfg = Config.sim()["custom_hiring"]
    v, b = spine["villages"], spine["blocks"]
    d = spine["districts"].set_index("district_id")

    # ---- dealer placement ---------------------------------------------------
    blk_pop = v.groupby("block_id")["rural_population"].sum()
    b = b.assign(rural_population=b["block_id"].map(blk_pop).fillna(0.0))
    tier = b["district_id"].map(d["mech_tier"])
    rate = tier.map(cfg["dealers_per_100k_rural_pop"]).astype(float)
    lam = (b["rural_population"] / 100_000.0 * rate).to_numpy()
    n_dealers = rng.poisson(np.maximum(lam, 0.02))

    dealers = []
    for i, k in enumerate(n_dealers):
        for j in range(int(k)):
            dealers.append({
                "dealer_id": f"{b['block_id'].iloc[i]}D{j + 1}",
                "district_id": b["district_id"].iloc[i],
                "block_id": b["block_id"].iloc[i],
                "lon": float(b["lon"].iloc[i] + rng.normal(0, 0.02)),
                "lat": float(b["lat"].iloc[i] + rng.normal(0, 0.02)),
            })
    dealers = pd.DataFrame(dealers)
    LOG.info("placed %d dealers across %d blocks", len(dealers), len(b))

    # ---- village accessibility ---------------------------------------------
    # Nearest-dealer distance computed per state to keep the pairwise matrix small.
    dist_km = np.full(len(v), cfg["max_effective_km"], dtype=float)
    vs = v.reset_index(drop=True)
    dstate = dealers["district_id"].map(d["state"])
    for st in vs["state"].unique():
        vi = np.where(vs["state"].to_numpy() == st)[0]
        dl = dealers[dstate.to_numpy() == st]
        if not len(dl):
            continue
        vp = np.column_stack([vs.loc[vi, "lon"], vs.loc[vi, "lat"]])
        dp = np.column_stack([dl["lon"], dl["lat"]])
        # chunk to bound memory on the 50k-village states
        for s0 in range(0, len(vi), 4000):
            sl = slice(s0, s0 + 4000)
            dx = (vp[sl, None, 0] - dp[None, :, 0]) * 95.0        # km per degree lon at ~22N
            dy = (vp[sl, None, 1] - dp[None, :, 1]) * 111.0
            dist_km[vi[sl]] = np.sqrt(dx ** 2 + dy ** 2).min(1)

    dist_km = np.minimum(dist_km, cfg["max_effective_km"])
    tierv = vs["district_id"].map(d["mech_tier"])

    out = pd.DataFrame({
        "village_id": vs["village_id"],
        "dealer_distance_km": dist_km,
        # exponential distance decay: accessibility halves every `distance_decay_km`
        "dealer_accessibility": np.exp(-dist_km / cfg["distance_decay_km"]),
    })
    out["service_density"] = np.clip(
        out["dealer_accessibility"] * cfg["service_point_multiplier"]
        * rng.uniform(0.7, 1.3, len(vs)), 0, 1.5)
    out["spares_index"] = np.clip(
        tierv.map(cfg["spares_index_beta"]).astype(float).to_numpy()
        * (0.55 + 0.45 * out["dealer_accessibility"]) * rng.uniform(0.85, 1.15, len(vs)), 0, 1)
    out["demo_activity"] = np.clip(
        tierv.map(cfg["demo_activity_beta"]).astype(float).to_numpy()
        * out["dealer_accessibility"] * rng.uniform(0.7, 1.3, len(vs)), 0, 1)

    # ---- custom hiring ------------------------------------------------------
    L = layers.set_index("village_id")
    avg_hold = L.loc[vs["village_id"], "avg_holding_ha"].to_numpy()
    chc_rate = tierv.map(chc_cfg["chc_per_100_villages"]).astype(float).to_numpy() / 100.0
    # Small holdings pull rental demand up: the Excel's "smallholders prefer rental".
    small_amp = 1.0 + (chc_cfg["small_holding_amplifier"] - 1.0) * np.clip(
        (2.5 - avg_hold) / 2.5, 0, 1)
    out["chc_density"] = np.clip(chc_rate * small_amp * rng.lognormal(0, 0.4, len(vs)), 0, 0.35)
    out["rental_ecosystem"] = np.clip(
        out["chc_density"] / 0.10 * 0.6 + out["dealer_accessibility"] * 0.4, 0, 1.5)

    out["provenance"] = "simulated"
    return out, dealers


def build_finance(spine, layers, assets, seed=20260822) -> pd.DataFrame:
    """KCC penetration, approval rate and affordability per village."""
    rng = np.random.default_rng(seed + 33)
    cfg = Config.sim()["finance"]
    v = spine["villages"]
    d = spine["districts"].set_index("district_id")
    tier = v["district_id"].map(d["mech_tier"])
    L = layers.set_index("village_id")

    kcc = np.clip(rng.normal(tier.map(cfg["kcc_penetration"]).astype(float), 0.10), 0.05, 0.95)
    hold = L.loc[v["village_id"], "avg_holding_ha"].to_numpy()
    irr = L.loc[v["village_id"], "irrigation_ratio"].to_numpy()
    hv = L.loc[v["village_id"], "high_value_share"].to_numpy()
    ci = L.loc[v["village_id"], "cropping_intensity"].to_numpy()

    # Farm income proxy: land x intensity x crop value, in INR per year.
    income = hold * ci * (52_000 + 78_000 * hv + 26_000 * irr) * rng.lognormal(0, 0.25, len(v))
    credit_depth = np.clip(kcc * (0.55 + 0.45 * np.clip(hold / 4.0, 0, 1.5)), 0, 1.4)

    out = pd.DataFrame({
        "village_id": v["village_id"].to_numpy(),
        "kcc_penetration": kcc,
        "farm_income_inr": income,
        "credit_depth": credit_depth,
        "approval_rate": np.clip(
            cfg["approval_rate_base"] + cfg["approval_rate_credit_beta"] * (credit_depth - 0.5)
            + cfg["subvention_effect"], 0.15, 0.95),
        # Ticket size a household can carry at ~9 months of farm income.
        "affordable_ticket_inr": income / 12.0 * cfg["affordability_months_income"],
        "provenance": "simulated",
    })
    return out


def build(spine, layers, series, seed=20260822):
    tb = build_tractor_base(spine, layers, series, seed)
    net, dealers = build_network(spine, layers, seed)
    fin = build_finance(spine, layers, tb, seed)

    out = tb.merge(net.drop(columns="provenance"), on="village_id") \
            .merge(fin.drop(columns="provenance"), on="village_id")
    write_table(out, CURATED / "village_assets.parquet")
    dealers["provenance"] = "simulated"
    write_table(dealers, CURATED / "dealers.parquet")
    return out


if __name__ == "__main__":
    sp = {k: read_table(CURATED / f"geo_{k}.parquet") for k in ("districts", "blocks", "villages")}
    build(sp, read_table(CURATED / "village_layers.parquet"),
          read_table(CURATED / "district_series.parquet"))
