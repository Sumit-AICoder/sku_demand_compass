"""Village-level micro-segments, action segments and per-village insights.

The archetype layer answers "what KIND of village is this?" — six broad types across
105k villages. Useful for strategy, useless for a field team, because it says nothing
about which of the 9,639 villages in an archetype to visit on Monday.

This module adds the operational layer:

  micro_segment    Sub-clusters WITHIN each archetype on opportunity-relevant dimensions,
                   so "High-Mech Irrigated Wheat-Paddy" resolves into distinguishable
                   pockets rather than one undifferentiated mass.

  action_segment   What to DO about the village, from two axes a sales head actually
                   acts on: how much unserved demand sits there, and whether the
                   distribution to capture it already exists.

                        headroom HIGH        headroom LOW
       dealer near      CONVERT NOW          DEFEND
       dealer far       BUILD ACCESS         MONITOR

  peer_gap         The village's attach rate against its OWN micro-segment peers, not a
                   district average. This is the honest whitespace measure: villages of
                   the same type, same soil, same crop, same holding size — one of them
                   buys less iron than the others, and that difference is addressable.

  headline         A plain-English sentence per village, generated from its own numbers.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.common import CURATED, MARTS, read_table, write_table, log

LOG = log("village_insights")

# Dimensions that separate villages by OPPORTUNITY, not by farming type. Farming type is
# already handled by the archetype; sub-clustering on it again would just re-cut the same
# groups. These are the levers a commercial team can pull on.
MICRO_DIMS = [
    "attach_rate", "adoption_gap_vs_peers", "tractor_density", "hp_mix_skew",
    "dealer_accessibility", "credit_depth", "income_per_ha", "avg_holding_ha",
    "replacement_pressure", "chc_density",
]
MICRO_K = 4          # sub-segments per archetype -> ~24 micro-segments overall

ACTION_RULES = {
    ("high", "near"): ("Convert now",
                       "Unserved demand with a dealer already in reach — the fastest conversion available."),
    ("high", "far"):  ("Build access",
                       "Real demand, but no distribution to capture it. Needs a dealer, sub-dealer or camp."),
    ("low", "near"):  ("Defend",
                       "Well penetrated and well served. Protect the base; sell replacement and attachments."),
    ("low", "far"):   ("Monitor",
                       "Little unserved demand and poor access. Low priority until the base grows."),
}


def build(seed: int = 20260822) -> pd.DataFrame:
    from sklearn.cluster import KMeans

    f = read_table(MARTS / "village_features.parquet")
    c = read_table(MARTS / "village_clusters.parquet")[
        ["village_id", "cluster_spatial", "archetype"]]
    tot = read_table(MARTS / "village_totals.parquet")[
        ["village_id", "potential_units_yr", "potential_value_inr",
         "headroom", "addressable", "owned", "top_sku", "top_category"]]
    geo = read_table(CURATED / "geo_villages.parquet")[
        ["village_id", "village", "block_id", "district_id", "district", "state"]] \
        if "district" in read_table(CURATED / "geo_villages.parquet").columns else None
    if geo is None:
        gv = read_table(CURATED / "geo_villages.parquet")
        gd = read_table(CURATED / "geo_districts.parquet")[["district_id", "district"]]
        geo = gv[["village_id", "village", "block_id", "district_id", "state"]].merge(gd, on="district_id")

    # `f` already carries block_id/district_id; taking them from geo too would produce
    # _x/_y suffixes and silently lose the plain name downstream.
    geo_cols = [c_ for c_ in ("village_id", "village", "district") if c_ in geo.columns]
    d = (f.merge(c, on="village_id").merge(tot, on="village_id")
          .merge(geo[geo_cols], on="village_id"))
    # lon/lat come through the feature table; the village map needs them on this mart too.

    # ---------------- micro-segments within each archetype --------------------
    rng = np.random.default_rng(seed)
    d["micro_segment"] = -1
    for arch, g in d.groupby("archetype"):
        X = g[MICRO_DIMS].to_numpy(float)
        med = np.nanmedian(X, 0)
        iqr = np.nanpercentile(X, 75, 0) - np.nanpercentile(X, 25, 0)
        iqr[iqr < 1e-9] = 1.0
        Z = np.clip((X - med) / iqr, -5, 5)
        k = min(MICRO_K, max(2, len(g) // 500))
        lab = KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(Z)
        d.loc[g.index, "micro_segment"] = lab

    d["micro_id"] = d["archetype"] + " · " + (d["micro_segment"] + 1).astype(str)

    # ---------------- peer gap against the micro-segment ----------------------
    grp = d.groupby("micro_id")
    d["peer_attach_micro"] = grp["attach_rate"].transform("median")
    d["peer_income_micro"] = grp["income_per_ha"].transform("median")
    d["attach_gap_micro"] = d["peer_attach_micro"] - d["attach_rate"]

    # ---------------- opportunity score ---------------------------------------
    # Deliberately simple and explainable: unserved demand, scaled by how far the
    # village lags its own peers, and by whether it can actually be served.
    pr = lambda s: s.rank(pct=True)
    d["opportunity_score"] = (
        0.40 * pr(d["headroom"])
        + 0.25 * pr(d["attach_gap_micro"].clip(lower=0))
        + 0.20 * pr(d["potential_units_yr"])
        + 0.15 * pr(d["dealer_accessibility"])
    ) * 100

    # ---------------- action segment ------------------------------------------
    head_hi = d["headroom"] >= d["headroom"].median()
    near = d["dealer_distance_km"] <= d["dealer_distance_km"].median()
    key = pd.Series(list(zip(np.where(head_hi, "high", "low"),
                             np.where(near, "near", "far"))), index=d.index)
    d["action_segment"] = key.map(lambda k: ACTION_RULES[k][0])
    d["action_rationale"] = key.map(lambda k: ACTION_RULES[k][1])

    # ---------------- ranks ----------------------------------------------------
    d["rank_in_district"] = d.groupby("district_id")["opportunity_score"].rank(ascending=False).astype(int)
    d["rank_in_state"] = d.groupby("state")["opportunity_score"].rank(ascending=False).astype(int)
    d["rank_in_micro"] = d.groupby("micro_id")["opportunity_score"].rank(ascending=False).astype(int)
    d["villages_in_district"] = d.groupby("district_id")["village_id"].transform("size")
    d["pct_in_state"] = d.groupby("state")["opportunity_score"].rank(pct=True) * 100

    # ---------------- what makes this village distinctive ---------------------
    d = _distinguishing(d)
    d["headline"] = _headlines(d)

    keep = [
        "village_id", "village", "district", "district_id", "block_id", "state",
        "lon", "lat",
        "archetype", "cluster_spatial", "micro_segment", "micro_id",
        "action_segment", "action_rationale", "opportunity_score",
        "rank_in_district", "villages_in_district", "rank_in_state", "rank_in_micro",
        "pct_in_state", "potential_units_yr", "potential_value_inr",
        "headroom", "addressable", "owned", "top_sku", "top_category",
        "attach_rate", "peer_attach_micro", "attach_gap_micro",
        "tractors", "avg_holding_ha", "net_sown_ha", "irrigation_ratio",
        "dominant_crop", "soil_texture", "workability", "farm_power_kw_ha",
        "dealer_distance_km", "dealer_accessibility", "credit_depth", "chc_density",
        "income_per_ha", "replacement_pressure", "residue_burden_per_ha",
        "distinct_1", "distinct_2", "distinct_3", "headline",
    ]
    missing = [k for k in keep if k not in d.columns]
    if missing:
        # A silent drop here is how block_id went missing once, breaking the block
        # filter and the map without anything failing loudly.
        raise KeyError(f"village_insights is missing expected columns: {missing}")
    out = d[keep].copy()
    out["provenance"] = "allocated"

    LOG.info("micro-segments: %d across %d archetypes", d["micro_id"].nunique(),
             d["archetype"].nunique())
    LOG.info("action segments:\n%s",
             out.groupby("action_segment").agg(
                 villages=("village_id", "size"),
                 units=("potential_units_yr", "sum"),
                 headroom=("headroom", "sum")).round(0).to_string())
    write_table(out, MARTS / "village_insights.parquet")
    _micro_profiles(d)
    return out


DISTINCT_LABELS = {
    "attach_rate": ("implements per tractor", "high", "low"),
    "tractor_density": ("tractor density", "high", "low"),
    "avg_holding_ha": ("farm size", "large", "small"),
    "irrigation_ratio": ("irrigation", "assured", "rainfed"),
    "income_per_ha": ("farm income per ha", "high", "low"),
    "dealer_accessibility": ("dealer access", "good", "poor"),
    "credit_depth": ("credit depth", "strong", "weak"),
    "residue_burden_per_ha": ("crop residue load", "heavy", "light"),
    "hp_mix_skew": ("share of 45 HP+ tractors", "high", "low"),
    "chc_density": ("custom-hiring presence", "dense", "sparse"),
    "workability": ("soil workability", "easy", "hard"),
    "replacement_pressure": ("replacement due", "high", "low"),
    "farm_power_kw_ha": ("farm power", "high", "low"),
}


def _distinguishing(d: pd.DataFrame) -> pd.DataFrame:
    """Top-3 features on which the village most departs from its micro-segment peers."""
    cols = list(DISTINCT_LABELS)
    Z = pd.DataFrame(index=d.index)
    for c in cols:
        g = d.groupby("micro_id")[c]
        Z[c] = (d[c] - g.transform("mean")) / g.transform("std").replace(0, np.nan)
    Z = Z.fillna(0.0)

    arr = Z.to_numpy()
    order = np.argsort(-np.abs(arr), axis=1)[:, :3]
    for slot in range(3):
        idx = order[:, slot]
        vals = arr[np.arange(len(arr)), idx]
        names = np.array(cols)[idx]
        d[f"distinct_{slot + 1}"] = [
            f"{DISTINCT_LABELS[n][0]}: {DISTINCT_LABELS[n][1] if v > 0 else DISTINCT_LABELS[n][2]}"
            f" ({v:+.1f} sd vs peers)" for n, v in zip(names, vals)]
    return d


def _headlines(d: pd.DataFrame) -> pd.Series:
    """One plain-English sentence per village, built from that village's own numbers."""
    def line(r):
        gap = r["attach_gap_micro"]
        rank = f"#{int(r['rank_in_district'])} of {int(r['villages_in_district'])} in {r['district']}"
        if r["action_segment"] == "Convert now":
            return (f"{rank} on opportunity. About {r['headroom']:.0f} implements of unserved "
                    f"demand, a dealer {r['dealer_distance_km']:.0f} km away, and "
                    f"{'an attach rate ' + format(gap, '.2f') + ' below' if gap > 0 else 'an attach rate at or above'} "
                    f"comparable villages. Best next SKU: {r['top_sku']}.")
        if r["action_segment"] == "Build access":
            return (f"{rank} on opportunity, but the nearest dealer is "
                    f"{r['dealer_distance_km']:.0f} km away. Roughly {r['headroom']:.0f} implements "
                    f"of demand sit unserved — distribution, not demand, is the constraint.")
        if r["action_segment"] == "Defend":
            return (f"Well served and well penetrated ({r['attach_rate']:.2f} implements per "
                    f"tractor vs {r['peer_attach_micro']:.2f} for peers). Protect the base: "
                    f"{r['replacement_pressure']:.2f} replacement cycles per tractor per year.")
        return (f"Limited near-term opportunity — {r['headroom']:.0f} implements of headroom and "
                f"a dealer {r['dealer_distance_km']:.0f} km away. Revisit as the tractor base grows "
                f"({r['tractors']:.0f} tractors today).")
    return d.apply(line, axis=1)


def _micro_profiles(d: pd.DataFrame) -> None:
    """Profile card per micro-segment, so the finer cut is explainable too."""
    rows = []
    for mid, g in d.groupby("micro_id"):
        z = ((g[MICRO_DIMS].mean() - d[MICRO_DIMS].mean()) / d[MICRO_DIMS].std()).sort_values(
            key=lambda s: -s.abs())
        rows.append({
            "micro_id": mid,
            "archetype": g["archetype"].iloc[0],
            "micro_segment": int(g["micro_segment"].iloc[0]),
            "n_villages": len(g),
            "states": ", ".join(g["state"].value_counts().head(2).index),
            "districts": ", ".join(g["district"].value_counts().head(3).index),
            "avg_opportunity": round(float(g["opportunity_score"].mean()), 1),
            "total_units": round(float(g["potential_units_yr"].sum()), 0),
            "total_headroom": round(float(g["headroom"].sum()), 0),
            "attach_rate": round(float(g["attach_rate"].mean()), 2),
            "dealer_km": round(float(g["dealer_distance_km"].mean()), 1),
            "avg_holding_ha": round(float(g["avg_holding_ha"].mean()), 2),
            "dominant_action": g["action_segment"].value_counts().index[0],
            "top_skus": ", ".join(g["top_sku"].value_counts().head(3).index),
            "defining": "; ".join(f"{k} {v:+.1f}sd" for k, v in z.head(4).items()),
            "provenance": "allocated",
        })
    out = pd.DataFrame(rows).sort_values("avg_opportunity", ascending=False)
    write_table(out, MARTS / "micro_segments.parquet")
    LOG.info("micro-segment profiles: %d", len(out))


if __name__ == "__main__":
    build()
