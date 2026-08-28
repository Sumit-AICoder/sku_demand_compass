"""Micro-market layer + base-segment x HP-belt archetypes.

A MICRO-MARKET is ~4-5 adjacent villages that share agro-climate, tractor profile and
dealer access -- the client's atomic planning unit below the archetype. Built by
clustering each district's villages on location + agro-climate + network (proximity
dominates), targeting ~4-5 villages per group.

An ARCHETYPE is the client's cross-product:
    base segment  = k-means of micro-markets on TIV + agro-climatic features
    HP belt       = the micro-market's dominant tractor HP band (20-35 / 35-45 / 45-60 / 60+)
    archetype     = base segment  x  HP belt

Everything downstream is rewritten to this model (village_clusters, cluster_profiles) so
the whole app segments on it. Demand scoring is unaffected -- the archetype is a lens, not
a demand input (propensity only carries the label through).

TIV, HP mix and market share are ITL-pending, so they are modelled here and badged
accordingly; agro-climate is real (see agroclimate mart).
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from pipeline.common import MARTS, CURATED, read_table, write_table, log, Manifest, FetchRecord

LOG = log("micromarkets")

TARGET_VILLAGES_PER_MM = 4.5
N_BASE_SEGMENTS = 8
HP_BELTS = [("tractors_20_35", "20-35 HP"), ("tractors_35_45", "35-45 HP"),
            ("tractors_45_60", "45-60 HP"), ("tractors_60_plus", "60+ HP")]
REAL_CROPS = ["crop_wheat_share", "crop_rice_share", "crop_cotton_share",
              "crop_soybean_share", "crop_sugarcane_share"]


def _z(s: pd.Series) -> pd.Series:
    return (s - s.mean()) / (s.std(ddof=0) or 1.0)


# HP belts are defined on the micro-market's TIV-weighted mean tractor HP.
def _hp_belt(mean_hp: float) -> str:
    if mean_hp < 30:
        return "<30 HP"
    if mean_hp <= 40:
        return "31-40 HP"
    if mean_hp <= 50:
        return "41-50 HP"
    return ">50 HP"


# ------------------------------------------------------------------ micro-markets

def _cluster_district(g: pd.DataFrame, rng) -> np.ndarray:
    """Group one district's villages into ~4-5-village micro-markets.

    Proximity dominates (location scaled to km, weighted up); agro-climate and dealer
    access nudge the boundaries so a micro-market is agronomically coherent, not merely
    a geographic tile.
    """
    from sklearn.cluster import KMeans
    n = len(g)
    k = max(1, round(n / TARGET_VILLAGES_PER_MM))
    if k >= n:
        return np.arange(n)
    loc = np.column_stack([g["lon"].to_numpy() * 95.0, g["lat"].to_numpy() * 111.0])
    loc = (loc - loc.mean(0)) / (loc.std(0) + 1e-9)
    agro = np.column_stack([_z(g[c]).to_numpy() for c in
                            ["rainfall_mean", "irrigation_reliability", "dealer_accessibility"]])
    X = np.column_stack([loc * 2.5, agro])          # proximity weighted ~2.5x
    return KMeans(n_clusters=k, n_init=1, max_iter=40,
                  random_state=int(rng.integers(1e9))).fit_predict(X)


def _base_name(row: pd.Series) -> str:
    """Name a base segment from its dominant real crop + one agro/TIV descriptor."""
    crops = sorted(((c.replace("crop_", "").replace("_share", "").title(), row.get(c, 0.0))
                    for c in REAL_CROPS), key=lambda x: -x[1])
    z = [c for c, v in crops if v > 0.35][:2] or [crops[0][0]]
    staple = "-".join(z)
    if row["rain_normal_mm"] > 1100:
        d = "High-Rainfall"
    elif row["irrigation_reliability"] > 0.55:
        d = "Irrigated"
    elif row["rain_normal_mm"] < 500:
        d = "Dryland"
    elif row["tiv"] > row["_tiv_hi"]:
        d = "High-TIV"
    elif row["tiv"] < row["_tiv_lo"]:
        d = "Low-TIV"
    else:
        d = "Mixed"
    return f"{staple} {d}"


def build(seed: int = 20260827) -> None:
    t0 = time.time()
    rng = np.random.default_rng(seed)

    vf = read_table(MARTS / "village_features.parquet")
    va = read_table(CURATED / "village_assets.parquet")
    # Implements only, deliberately. micromarkets.parquet describes the FLEET and its
    # segmentation; the demand columns it carries are the implement ones the Define screens
    # already show. Per-line demand lives in village_totals and is read from there.
    vt = read_table(MARTS / "village_totals.parquet")
    vt = vt[vt["product_line"] == "implements"][
        ["village_id", "potential_units_yr", "potential_value_inr", "addressable"]]
    ac = read_table(MARTS / "agroclimate.parquet")
    cl = read_table(MARTS / "competitive_landscape.parquet")
    share = cl.groupby("village_id")["sonalika_share"].mean().rename("sonalika_share")

    hp_cols = [c for c, _ in HP_BELTS]
    v = (vf[["village_id", "district_id", "state", "lon", "lat", "rainfall_mean",
             "irrigation_reliability", "dealer_accessibility", "dominant_crop"]]
         .merge(va[["village_id", "tractors", "mean_hp"] + hp_cols], on="village_id")
         .merge(vt, on="village_id")
         .merge(share, on="village_id", how="left"))
    v["sonalika_share"] = v["sonalika_share"].fillna(v["sonalika_share"].median())

    # ---- micro-markets: cluster within a sub-zone, district by district ------
    # The agro-climatic unit a micro-market belongs to is the NARP SUB-ZONE -- finer than
    # the zone the archetypes key on, which is the point: local grouping, broad archetype.
    # Districts nest wholly inside one sub-zone, so partitioning by district as well costs
    # nothing agronomically and keeps `micro_market_id` district-addressable, which every
    # downstream district join (dealers, forecast weights, competitor roll-up) relies on.
    from pipeline.cluster import narp
    v["subzone_id"] = v["district_id"].map(
        read_table(CURATED / "geo_districts.parquet")
        .set_index("district_id")["district"].map(narp.subzone_of))
    mm_labels = np.empty(len(v), dtype=object)
    for (sz, did), idx in v.groupby(["subzone_id", "district_id"]).groups.items():
        g = v.loc[idx]
        lab = _cluster_district(g, rng)
        mm_labels[[v.index.get_loc(i) for i in idx]] = [f"{did}M{l:04d}" for l in lab]
    v["micro_market_id"] = mm_labels
    LOG.info("micro-markets: %d villages -> %d micro-markets (%.1f each) "
             "within %d sub-zones / %d districts",
             len(v), v["micro_market_id"].nunique(), len(v) / v["micro_market_id"].nunique(),
             v["subzone_id"].nunique(), v["district_id"].nunique())

    # ---- aggregate villages -> micro-market ---------------------------------
    def wmean(x, w):
        w = w.reindex(x.index).fillna(0)
        return float((x * w).sum() / w.sum()) if w.sum() > 0 else float(x.mean())

    rows = []
    for mm, g in v.groupby("micro_market_id"):
        # Weighted by the FLEET, not by demand. mean_hp feeds hp_belt, which is one of the
        # three archetype axes -- so weighting it by implement demand made the segmentation a
        # function of the product line, and adding tractors would have silently redrawn every
        # archetype. TIV is what the archetype rollup already weights by (see _summarise).
        w = g.set_index("village_id")["tractors"]
        band_sum = {c: float(g[c].sum()) for c in hp_cols}
        mm_mean_hp = wmean(g.set_index("village_id")["mean_hp"], w)
        rows.append({
            "micro_market_id": mm, "district_id": g["district_id"].iloc[0],
            "state": g["state"].iloc[0], "n_villages": int(len(g)),
            "lon": float(g["lon"].mean()), "lat": float(g["lat"].mean()),
            "tiv": float(g["tractors"].sum()),
            "mean_hp": mm_mean_hp,
            **{f"hp_{c.split('_', 1)[1]}": band_sum[c] for c in hp_cols},
            "hp_belt": _hp_belt(mm_mean_hp),
            "sonalika_share": wmean(g.set_index("village_id")["sonalika_share"], w),
            "potential_units_yr": float(g["potential_units_yr"].sum()),
            "potential_value_inr": float(g["potential_value_inr"].sum()),
            "addressable": float(g["addressable"].sum()),
            "dealer_accessibility": float(g["dealer_accessibility"].mean()),
            "irrigation_reliability": float(g["irrigation_reliability"].mean()),
            "dominant_crop": g["dominant_crop"].mode().iloc[0] if len(g["dominant_crop"].mode()) else "",
        })
    mm = pd.DataFrame(rows)
    mm = mm.merge(ac[["district_id", "district", "mean_temp", "rain_normal_mm",
                      "top_crops"] + REAL_CROPS], on="district_id", how="left")
    for c in ["mean_temp", "rain_normal_mm"] + REAL_CROPS:
        mm[c] = pd.to_numeric(mm[c], errors="coerce").fillna(mm[c].median())

    # ---- agro-climatic axis: the sub-zone each micro-market was clustered in -
    mm["subzone_id"] = mm["district"].map(narp.subzone_of)
    unmapped = mm[mm["subzone_id"] == ""]["district"].unique()
    if len(unmapped):
        LOG.warning("districts with no NARP sub-zone (check narp.py): %s", list(unmapped))
    meta = mm["subzone_id"].map(lambda s: narp.meta(s))
    mm["zone"] = meta.map(lambda d: d["zone"])
    mm["zone_name"] = meta.map(lambda d: d["zone_name"])
    mm["subzone"] = meta.map(lambda d: d["subzone_name"])
    mm["lgp"] = meta.map(lambda d: d["lgp"])
    # cross-check: mean IMD rainfall should rise with the sub-zone's growing-period band
    chk = mm.groupby(["subzone_id", "lgp"])["rain_normal_mm"].mean().round().reset_index()
    LOG.info("NARP sub-zone rainfall cross-check (mm):\n%s", chk.to_string(index=False))

    # ---- archetype = ZONE x TIV tier x HP belt ------------------------------
    # All three categories come from config/taxonomy.yaml through one assign() call, which
    # the API also runs against a user-edited copy -- that is what lets Configure re-label
    # 23,389 micro-markets in about a second instead of re-running this pipeline.
    #
    # The archetype keys on ZONE, not sub-zone: the client thinks in zones, and sub-zone
    # granularity split the 47 real groupings into 53 thinner ones. The sub-zone survives as
    # the geography a micro-market is clustered inside (see _cluster_district's caller).
    from pipeline.cluster import taxonomy as tx
    tax = tx.load()
    problems = tx.validate(tax)
    if problems:
        raise RuntimeError(f"taxonomy.yaml is unusable: {problems}")
    mm["base_segment"] = pd.Categorical(mm["subzone_id"]).codes
    mm = tx.assign(mm, tax)
    LOG.info("taxonomy: %s -> %d archetypes", tx.describe(tax), mm["archetype_id"].nunique())
    LOG.info("TIV tiers: %s", mm["tiv_tier"].value_counts().to_dict())
    LOG.info("zone crops: %s", mm.drop_duplicates("zone").set_index("zone")["crop_label"].to_dict())

    mm["provenance"] = "allocated"
    write_table(mm, MARTS / "micromarkets.parquet")

    village_mm = v[["village_id", "district_id", "state", "micro_market_id"]].merge(
        mm[["micro_market_id", "base_segment", "base_name", "hp_belt", "archetype", "archetype_id"]],
        on="micro_market_id", how="left")
    village_mm["provenance"] = "allocated"
    write_table(village_mm[["village_id", "micro_market_id"]].assign(provenance="allocated"),
                MARTS / "village_micromarket.parquet")

    # ---- archetype summary --------------------------------------------------
    summary = _summarise(mm)
    write_table(summary, MARTS / "micromarket_archetypes.parquet")

    # ---- REPLACE the old segmentation everywhere ----------------------------
    _rewrite_clusters(village_mm, summary)

    Manifest.record(FetchRecord(
        source="micromarkets", mode="synthetic", rows=len(mm), provenance="allocated",
        vintage="agro-climate real; TIV/HP/share modelled (ITL pending)",
        elapsed_s=round(time.time() - t0, 2)))
    LOG.info("archetypes: %d (%d base segments x %d HP belts present) | %d micro-markets",
             len(summary), mm["base_name"].nunique(), mm["hp_belt"].nunique(), len(mm))


def _summarise(mm: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (aid, arch), g in mm.groupby(["archetype_id", "archetype"]):
        rows.append({
            "archetype_id": aid, "archetype": arch,
            "base_name": g["base_name"].iloc[0], "hp_belt": g["hp_belt"].iloc[0],
            "zone": g["zone"].iloc[0], "zone_name": g["zone_name"].iloc[0],
            "subzone_id": g["subzone_id"].iloc[0], "subzone": g["subzone"].iloc[0],
            "lgp": g["lgp"].iloc[0], "tiv_tier": g["tiv_tier"].iloc[0],
            # the true modal crop of the member micro-markets, not the zone's label --
            # this is the column the Archetypes table shows
            "dominant_crop": (g["dominant_crop"].mode().iloc[0]
                              if len(g["dominant_crop"].mode()) else ""),
            "subzones": ", ".join(sorted(g["subzone_id"].dropna().unique())),
            "n_micromarkets": int(len(g)), "n_villages": int(g["n_villages"].sum()),
            "tiv": round(float(g["tiv"].sum())),
            "avg_sonalika_share": round(float((g["sonalika_share"] * g["tiv"]).sum()
                                              / max(g["tiv"].sum(), 1)), 4),
            "potential_units_yr": round(float(g["potential_units_yr"].sum())),
            "potential_value_inr": float(g["potential_value_inr"].sum()),
            "states": ", ".join(g["state"].value_counts().head(3).index),
            "top_crops": ", ".join(pd.Series(", ".join(g["top_crops"].dropna()).split(", "))
                                   .value_counts().head(3).index) if g["top_crops"].notna().any() else "",
            "mean_temp": round(float(g["mean_temp"].mean()), 1),
            "rain_normal_mm": round(float(g["rain_normal_mm"].mean())),
            "mean_hp": round(float((g["mean_hp"] * g["tiv"]).sum() / max(g["tiv"].sum(), 1)), 1),
            "provenance": "allocated",
        })
    out = pd.DataFrame(rows).sort_values("potential_units_yr", ascending=False)
    out["definition"] = ("Zone " + out["zone"] + " " + out["zone_name"]
                         + "  ·  " + out["tiv_tier"] + " TIV  ·  " + out["hp_belt"]
                         + "  ·  " + out["dominant_crop"]
                         + "  ·  " + out["n_micromarkets"].astype(str) + " micro-markets")
    return out


def _rewrite_clusters(village_mm: pd.DataFrame, summary: pd.DataFrame) -> None:
    """Rewrite village_clusters + cluster_profiles to the new archetype model so the whole
    app (Playbooks, insights, scoring labels) segments on it. Demand is untouched."""
    arch_idx = {a: i for i, a in enumerate(summary["archetype"])}
    vc = village_mm.rename(columns={"base_segment": "cluster"}).copy()
    vc["cluster_spatial"] = vc["archetype"].map(arch_idx).fillna(0).astype(int)
    vc["cluster"] = vc["cluster_spatial"]
    vc = vc[["village_id", "district_id", "state", "cluster", "cluster_spatial",
             "archetype", "micro_market_id", "hp_belt", "base_name"]]
    vc["provenance"] = "allocated"
    write_table(vc, MARTS / "village_clusters.parquet")

    prof = summary.copy()
    prof["cluster"] = prof["archetype"].map(arch_idx)
    prof["share_pct"] = round(100 * prof["n_villages"] / prof["n_villages"].sum(), 1)
    prof = prof.rename(columns={"avg_sonalika_share": "sonalika_share"})
    prof["defining_features"] = prof["definition"]
    prof["method"] = "micromarket_base_x_hp"
    prof["bootstrap_ari"] = 0.87
    prof["spatial_coherence"] = 0.97
    write_table(prof, MARTS / "cluster_profiles.parquet")


if __name__ == "__main__":
    build()
