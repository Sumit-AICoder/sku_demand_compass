"""Phase 7 -- SKU propensity and demand potential, per village.

    Addressable(v, sku) = fleet in the SKU's HP band
                        x farm-size fit x crop fit x category ceiling x gating
    Headroom(v, sku)    = Addressable - already owned
    Potential           = (Headroom x conversion + Replacement) x Propensity

    Propensity(v, sku)  = sum_i w_i(sku) . F_i(v)/100
                        x soil_fit x season x compete_headwind

The weights w_i are the point of the whole exercise. Where the UCM produced a
significant, correctly-signed coefficient from a model that beat seasonal-naive, the
weight is EMPIRICAL -- derived from that coefficient. Where it did not, the weight
falls back to the judgmental prior in weights.yaml. Every weight carries an origin
badge so the UI can show which is which, and the split is never hidden.

Only four of the ten factor groups have a time-varying driver the UCM can identify
(F1 farm economics, F4 mechanisation, F6 policy, F7 monsoon). The other six -- land
holding, tractor base, cropping pattern, custom hiring, technology, distribution --
are structural: they barely move month to month, so a monthly time-series model has
nothing to estimate from and their weights stay judgmental. That is a real limit of
the method, not an oversight, and the dashboard says so.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.common import CURATED, MARTS, Config, read_table, write_table, log

LOG = log("score.propensity")

FACTOR_IDS = ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10"]


# ---------------------------------------------------------------- weights

def resolve_weights(betas: pd.DataFrame, diagnostics: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Turn UCM betas into per-SKU factor weights, with origin recorded per factor."""
    cfg = Config.ucm()
    wcfg = Config.weights()
    prior_default = wcfg["default"]
    con = wcfg["constraints"]

    # ---- pool and shrink the district betas ---------------------------------
    usable = betas[betas["usable"]]
    if not len(usable):
        LOG.warning("no usable UCM betas -- all weights fall back to priors")
        emp_importance = {}
    else:
        # Empirical-Bayes shrinkage toward the global mean: districts with noisy
        # estimates get pulled harder toward the pool, which is what stops a single
        # badly-identified district from producing a wild weight.
        g = usable.groupby("regressor")["beta"]
        pool_mean, pool_var = g.mean(), g.var().fillna(0.0)
        k = cfg["pooling"]["shrinkage_prior_weight"]
        sh = usable.assign(
            pm=usable["regressor"].map(pool_mean),
            pv=usable["regressor"].map(pool_var))
        wgt = sh["pv"] / (sh["pv"] + k * sh["se"] ** 2 + 1e-12)
        sh["beta_shrunk"] = wgt * sh["beta"] + (1 - wgt) * sh["pm"]

        factor_of = {r["name"]: r["factor"] for r in cfg["regressors"]}
        sh["factor"] = sh["regressor"].map(factor_of)
        emp_importance = (sh.groupby("factor")["beta_shrunk"]
                          .apply(lambda s: float(np.abs(s).sum())).to_dict())
        write_table(sh.assign(provenance="allocated"), MARTS / "ucm_betas_shrunk.parquet")

    covered = sorted(emp_importance)
    LOG.info("UCM-covered factors: %s | structural (prior-only): %s",
             covered, [f for f in FACTOR_IDS if f not in covered])

    # ---- per-SKU weight vectors ---------------------------------------------
    rows, origin_rows = [], []
    for sku in Config.skus():
        w = dict(prior_default)
        w.update(wcfg["by_category"].get(sku["category"], {}))
        w.update(wcfg["by_sku"].get(sku["id"], {}))

        origin = {f: "prior" for f in FACTOR_IDS}
        if covered:
            # Preserve the prior's total mass on the covered factors, and redistribute
            # it in proportion to the estimated elasticities. This keeps the balance
            # between estimated and structural factors intact -- the UCM decides the
            # SPLIT among the factors it can see, not how much they matter overall.
            mass = sum(max(w[f], 0.0) for f in covered)
            tot = sum(emp_importance[f] for f in covered)
            if mass > 0 and tot > 0:
                for f in covered:
                    w[f] = mass * emp_importance[f] / tot
                    origin[f] = "ucm"

        for f in FACTOR_IDS:
            w[f] = float(np.clip(w[f], con["min_weight"], con["max_weight"]))
        if con["renormalise"]:
            pos = sum(v for v in w.values() if v > 0)
            if pos > 0:
                w = {f: (v / pos if v > 0 else v) for f, v in w.items()}

        rows.append({"sku_id": sku["id"], "category": sku["category"], **w})
        for f in FACTOR_IDS:
            origin_rows.append({"sku_id": sku["id"], "factor": f,
                                "weight": w[f], "origin": origin[f],
                                "provenance": "allocated"})

    W = pd.DataFrame(rows)
    O = pd.DataFrame(origin_rows)
    LOG.info("weight origin: %d/%d sku-factor pairs empirical (UCM), %d judgmental prior",
             int((O["origin"] == "ucm").sum()), len(O), int((O["origin"] == "prior").sum()))
    write_table(W.assign(provenance="allocated"), MARTS / "sku_weights.parquet")
    write_table(O, MARTS / "sku_weight_origin.parquet")
    return W, O


# ---------------------------------------------------------------- seasonality

def build_seasonality(decomposition: pd.DataFrame) -> pd.DataFrame:
    """Blend the UCM-estimated seasonal with each SKU's demand window.

    The UCM gamma_t is estimated on TRACTOR sales, so it captures the real buying
    rhythm -- post-kharif and festive peaks, monsoon trough -- but not what makes a
    super seeder differ from a sprayer. The SKU's own window from the Seasonality
    sheet supplies that. Multiplying the two keeps the empirically-estimated shape
    while respecting the implement's agronomic calendar.
    """
    dec = decomposition.copy()
    dec["m"] = pd.PeriodIndex(dec["month"], freq="M").month
    gamma = dec.groupby("m")["seasonal"].mean()
    gamma = np.exp(gamma - gamma.mean())            # log-space -> multiplier

    rows = []
    for sku in Config.skus():
        prior = np.array([1.0 if m in sku["season"] else 0.30 for m in range(1, 13)])
        prior = prior / prior.mean()
        blend = prior * gamma.reindex(range(1, 13)).to_numpy()
        blend = blend / blend.mean()
        for m in range(1, 13):
            rows.append({"sku_id": sku["id"], "month_of_year": m,
                         "season_index": float(blend[m - 1]),
                         "prior_index": float(prior[m - 1]),
                         "ucm_gamma": float(gamma.get(m, 1.0)),
                         "provenance": "allocated"})
    out = pd.DataFrame(rows)
    LOG.info("UCM seasonal multiplier by month: %s",
             np.round(gamma.reindex(range(1, 13)).to_numpy(), 2).tolist())
    write_table(out, MARTS / "sku_seasonality.parquet")
    return out


# ---------------------------------------------------------------- scoring

def build(seed: int = 20260822) -> pd.DataFrame:
    F = read_table(MARTS / "village_factors.parquet")
    S = read_table(CURATED / "village_sku_state.parquet")
    A = read_table(CURATED / "village_assets.parquet").set_index("village_id")
    feats = read_table(MARTS / "village_features.parquet").set_index("village_id")
    clusters = read_table(MARTS / "village_clusters.parquet").set_index("village_id")
    head = read_table(CURATED / "competition_headwind.parquet")
    betas = read_table(MARTS / "ucm_betas.parquet")
    diags = read_table(MARTS / "ucm_diagnostics.parquet")
    dec = read_table(MARTS / "ucm_decomposition.parquet")

    W, O = resolve_weights(betas, diags)
    build_seasonality(dec)

    Fi = F.set_index("village_id")
    fac = Fi[FACTOR_IDS].to_numpy() / 100.0
    vid = Fi.index.to_numpy()
    vpos = pd.Series(np.arange(len(vid)), index=vid)

    hard = feats.loc[vid, "soil_hardness"].to_numpy()
    draft = feats.loc[vid, "draft_requirement"].to_numpy()
    conv = (A.loc[vid, "approval_rate"].to_numpy()
            * (0.55 + 0.45 * A.loc[vid, "dealer_accessibility"].to_numpy()))
    hw = head.set_index(["district_id", "category"])["headwind"]
    did = Fi["district_id"].to_numpy()

    Wi = W.set_index("sku_id")
    out = []
    for sku in Config.skus():
        sub = S[S["sku_id"] == sku["id"]]
        pos = vpos.reindex(sub["village_id"]).to_numpy()
        w = Wi.loc[sku["id"], FACTOR_IDS].to_numpy(float)

        # weighted factor score; negative weights (custom hiring) act as penalties
        prop = fac[pos] @ w

        # soil fit: only SKUs that declare an affinity are gated on soil
        if "soil_affinity" in sku:
            aff = pd.Series(hard[pos]).map(sku["soil_affinity"]).fillna(0.6).to_numpy()
            soil_fit = 0.5 + 0.5 * aff
        else:
            soil_fit = np.clip(0.85 + 0.15 * (2.0 - draft[pos]), 0.7, 1.15)

        head_v = hw.reindex(
            pd.MultiIndex.from_arrays([did[pos], np.repeat(sku["category"], len(pos))])
        ).fillna(0.75).to_numpy()

        propensity = np.clip(prop * soil_fit * head_v, 0.0, None)

        conv_yr = 0.09 if sku["product_line"] == "implements" else 0.05
        new_units = sub["headroom"].to_numpy() * conv_yr * conv[pos]
        repl = sub["replacement_units_yr"].to_numpy()
        potential = (new_units + repl) * propensity / max(propensity.mean(), 1e-9)

        out.append(pd.DataFrame({
            "village_id": sub["village_id"].to_numpy(),
            "district_id": did[pos],
            "sku_id": sku["id"],
            "product_line": sku["product_line"],
            "category": sku["category"],
            "propensity": propensity,
            "soil_fit": soil_fit,
            "compete_headwind": head_v,
            "conversion": conv[pos],
            "addressable": sub["addressable"].to_numpy(),
            "owned": sub["owned"].to_numpy(),
            "headroom": sub["headroom"].to_numpy(),
            "penetration": sub["penetration"].to_numpy(),
            "new_units_yr": new_units,
            "replacement_units_yr": repl,
            "potential_units_yr": potential,
            "potential_value_inr": potential * sku["price_inr"],
        }))

    sc = pd.concat(out, ignore_index=True)
    sc["propensity_score"] = (sc.groupby("sku_id")["propensity"]
                              .transform(lambda s: s.rank(pct=True) * 100))
    sc["cluster"] = sc["village_id"].map(clusters["cluster_spatial"])
    sc["archetype"] = sc["village_id"].map(clusters["archetype"])
    sc["provenance"] = "allocated"

    LOG.info("scores: %d village x SKU rows | total potential %.0f units/yr, INR %.0f cr",
             len(sc), sc["potential_units_yr"].sum(), sc["potential_value_inr"].sum() / 1e7)
    write_table(sc, MARTS / "village_sku_scores.parquet")
    _report(sc)
    return sc


def _report(sc: pd.DataFrame) -> None:
    top = (sc.groupby(["sku_id"])["potential_units_yr"].sum()
           .sort_values(ascending=False).head(8))
    LOG.info("top SKUs by national potential:\n%s", top.round(0).to_string())
    st = read_table(MARTS / "village_factors.parquet")[["village_id", "state"]]
    m = sc.merge(st, on="village_id")
    for sku in ["SUPER_SEEDER", "ORCHARD_SPRAYER", "SEED_DRILL_11T", "ROUND_BALER"]:
        s = (m[m.sku_id == sku].groupby("state")["potential_units_yr"].sum()
             .sort_values(ascending=False))
        LOG.info("%-16s potential by state: %s", sku,
                 " | ".join(f"{k} {v:,.0f}" for k, v in s.items()))


if __name__ == "__main__":
    build()
