"""Competitive landscape and cannibalisation.

Static market shares say who is winning. They cannot say what happens if somebody
changes something, because a share is an outcome, not a mechanism. This module replaces
the static split with a **choice model**, so share becomes a consequence of things a
business can actually move: price, dealer proximity, product fit and brand equity.

    utility(player) = brand + fit_weight*fit
                    + price_sensitivity*(price - 1)
                    + distance_sensitivity*max(0, distance - reach)
    share(player)   = softmax(utility)

Because shares are a softmax they necessarily sum to one, so a gain for one brand is a
loss for the others -- which is what makes switching and cannibalisation calculable
rather than asserted.

Two kinds of cannibalisation come out of it:

  external  Demand Sonalika could win that a competitor takes instead. Split into
            "contested" (we are close on utility -- winnable) and "lost" (they are far
            ahead -- not worth the call), because those need different responses.

  internal  Sonalika SKUs that compete with each other. A 7 ft rotavator sold into a
            village that would have bought the 5 ft is not incremental revenue, and a
            demand plan that adds both at full value double-counts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.common import CURATED, MARTS, Config, read_table, write_table, log
from pipeline.simulate.competition import AFFINITY
from pipeline.ingest.village_layers import CROPS, HOLDING_CLASSES
from pipeline.simulate.sku_history import hp_band_overlap
from pipeline.simulate.assets import HP_BANDS

LOG = log("competition_model")


# ------------------------------------------------------------------ landscape

def build_landscape() -> pd.DataFrame:
    """Village x category x player share, from the choice model."""
    cfg = Config.sim()["competition"]
    can = Config.sim()["cannibalisation"]
    rng = np.random.default_rng(Config.sim()["seed"] + 61)

    players = cfg["players"]
    price = np.array([cfg["price_index"][p] for p in players])
    reach = np.array([cfg["reach_km"][p] for p in players])

    v = read_table(MARTS / "village_features.parquet")
    d = read_table(CURATED / "geo_districts.parquet").set_index("district_id")
    # Implement categories only: the 13 players below and their affinity weights are
    # implement brands. Tractor competition comes from the real 6-OEM dealer footprint,
    # not from this hand-set choice model.
    cats = list(Config.sku_categories("implements"))

    state = v["district_id"].map(d["state"]).to_numpy()
    dealer_km = v["dealer_distance_km"].to_numpy()
    # Every brand's distance is anchored on the observed Sonalika dealer distance, then
    # scattered by its own network density -- a village far from one brand is usually
    # far from most, but not identically so.
    dist = dealer_km[:, None] * rng.lognormal(0, 0.30, (len(v), len(players)))

    rows = []
    for cat in cats:
        fit = np.array([AFFINITY[p][cat] for p in players])
        fit = fit / fit.max()
        brand = np.array([cfg["state_bias"].get("Punjab", {}).get(p, 1.0) for p in players])

        # state bias is per-state, so build utility per state block
        share = np.zeros((len(v), len(players)))
        for st in np.unique(state):
            m = state == st
            bias = np.array([cfg["state_bias"].get(st, {}).get(p, 1.0) for p in players])
            u = (can["brand_weight"] * (bias - 1.0)
                 + can["fit_weight"] * fit
                 + can["price_sensitivity"] * (price - 1.0)
                 + can["distance_sensitivity"] * np.maximum(0.0, dist[m] - reach))
            e = np.exp(u - u.max(axis=1, keepdims=True))
            share[m] = e / e.sum(axis=1, keepdims=True)

        son = players.index("Sonalika")
        # Runner-up utility gap tells us whether a village is contested or already lost.
        order = np.argsort(-share, axis=1)
        leader = order[:, 0]
        best_other = np.where(leader == son, order[:, 1], leader)
        gap = share[np.arange(len(v)), best_other] - share[:, son]

        rows.append(pd.DataFrame({
            "village_id": v["village_id"].to_numpy(),
            "district_id": v["district_id"].to_numpy(),
            "state": state,
            "category": cat,
            "sonalika_share": share[:, son],
            "leader": [players[i] for i in leader],
            "leader_share": share[np.arange(len(v)), leader],
            "closest_rival": [players[i] for i in best_other],
            "rival_share": share[np.arange(len(v)), best_other],
            "share_gap": gap,
            "hhi": (share ** 2).sum(1),
        }))

    out = pd.concat(rows, ignore_index=True)
    out["provenance"] = "simulated"
    LOG.info("landscape: %d village x category rows | Sonalika mean share %.1f%% | "
             "leads in %.1f%% of them",
             len(out), out["sonalika_share"].mean() * 100,
             (out["leader"] == "Sonalika").mean() * 100)
    write_table(out, MARTS / "competitive_landscape.parquet")

    # long form for the head-to-head view
    return out


def build_player_shares() -> pd.DataFrame:
    """District x category x player, for the competitor table."""
    cfg = Config.sim()["competition"]
    can = Config.sim()["cannibalisation"]
    players = cfg["players"]
    price = np.array([cfg["price_index"][p] for p in players])
    reach = np.array([cfg["reach_km"][p] for p in players])

    v = read_table(MARTS / "village_features.parquet")
    d = read_table(CURATED / "geo_districts.parquet").set_index("district_id")
    rng = np.random.default_rng(Config.sim()["seed"] + 62)
    state = v["district_id"].map(d["state"]).to_numpy()
    dist = v["dealer_distance_km"].to_numpy()[:, None] * rng.lognormal(0, .30, (len(v), len(players)))

    rows = []
    for cat in Config.sku_categories("implements"):
        fit = np.array([AFFINITY[p][cat] for p in players]); fit = fit / fit.max()
        share = np.zeros((len(v), len(players)))
        for st in np.unique(state):
            m = state == st
            bias = np.array([cfg["state_bias"].get(st, {}).get(p, 1.0) for p in players])
            u = (can["brand_weight"] * (bias - 1.0) + can["fit_weight"] * fit
                 + can["price_sensitivity"] * (price - 1.0)
                 + can["distance_sensitivity"] * np.maximum(0.0, dist[m] - reach))
            e = np.exp(u - u.max(1, keepdims=True)); share[m] = e / e.sum(1, keepdims=True)
        df = pd.DataFrame(share, columns=players)
        df["district_id"] = v["district_id"].to_numpy()
        g = df.groupby("district_id").mean()
        for p in players:
            rows.append(pd.DataFrame({"district_id": g.index, "category": cat,
                                      "player": p, "share": g[p].to_numpy()}))
    out = pd.concat(rows, ignore_index=True)
    out["price_index"] = out["player"].map(cfg["price_index"])
    out["reach_km"] = out["player"].map(cfg["reach_km"])
    out["provenance"] = "simulated"
    write_table(out, MARTS / "player_shares.parquet")
    return out


# ------------------------------------------------------------------ external

def build_external_cannibalisation() -> pd.DataFrame:
    """Per village x SKU: demand at stake, who takes it, and whether it is winnable."""
    land = read_table(MARTS / "competitive_landscape.parquet")
    # PHASE 2 BOUNDARY. village_sku_scores now carries both product lines. Everything
    # below this point still rolls up to a single un-keyed "demand" number, so summing
    # the two here would add a 7-lakh tractor to a 42k cultivator and call it units.
    # Scoped to implements until each rollup gains product_line as a group key --
    # which keeps every number on screen today exactly what it was.
    scores = read_table(MARTS / "village_sku_scores.parquet")
    scores = scores[scores["product_line"] == "implements"]

    L = land.set_index(["village_id", "category"])
    idx = pd.MultiIndex.from_arrays([scores["village_id"], scores["category"]])
    for c in ("sonalika_share", "leader", "leader_share", "closest_rival",
              "rival_share", "share_gap", "hhi"):
        scores[c] = L[c].reindex(idx).to_numpy()

    # Total category demand this village represents; Sonalika's slice is its share of it.
    scores["market_units"] = scores["potential_units_yr"]
    scores["sonalika_units"] = scores["market_units"] * scores["sonalika_share"]
    scores["competitor_units"] = scores["market_units"] - scores["sonalika_units"]

    # Sonalika is a ~7% challenger in implements, so classifying on the raw gap to the
    # category leader would label almost everything "losing" -- true, and useless.
    # The actionable question for a challenger is how this village compares with its OWN
    # normal performance, and how close the nearest rival is to being displaced.
    benchmark = scores.groupby("category")["sonalika_share"].transform("mean")
    scores["share_index"] = scores["sonalika_share"] / benchmark.clip(lower=1e-9) * 100

    # Closeness of the contest, 0..1: how near Sonalika's share is to the nearest rival's.
    closeness = (scores["sonalika_share"]
                 / (scores["sonalika_share"] + scores["rival_share"]).clip(lower=1e-9))
    scores["closeness"] = closeness

    scores["status"] = np.select(
        [scores["leader"] == "Sonalika", closeness >= 0.42, closeness >= 0.28],
        ["Leading", "Winnable", "Stretch"], default="Out of reach")

    # Winnable: the rival volume a realistic push could flip, scaled by how close the
    # contest is -- not a flat fraction of everything a competitor holds.
    scores["winnable_units"] = (scores["competitor_units"]
                                * np.clip((closeness - 0.25) / 0.35, 0, 1) ** 1.5 * 0.30)
    # At risk: Sonalika volume the nearest rival could take, regardless of who leads.
    # A challenger's business is mostly won in places it does not lead, so gating this
    # on leadership (as the first version did) reported ~0 and hid the real exposure.
    scores["at_risk_units"] = (scores["sonalika_units"]
                               * np.clip(1 - closeness, 0, 1) ** 1.2 * 0.40)
    scores["at_risk_units"] = np.minimum(scores["at_risk_units"], scores["sonalika_units"])

    keep = ["village_id", "district_id", "sku_id", "category", "market_units",
            "sonalika_share", "share_index", "sonalika_units", "competitor_units",
            "leader", "leader_share", "closest_rival", "rival_share", "share_gap",
            "closeness", "hhi", "status", "winnable_units", "at_risk_units",
            "potential_value_inr"]
    out = scores[keep].copy()
    out["provenance"] = "simulated"

    LOG.info("external: Sonalika %s units of %s market (%.1f%%)",
             f"{out['sonalika_units'].sum():,.0f}", f"{out['market_units'].sum():,.0f}",
             out["sonalika_units"].sum() / out["market_units"].sum() * 100)
    LOG.info("  winnable from rivals %s | own volume at risk %s",
             f"{out['winnable_units'].sum():,.0f}", f"{out['at_risk_units'].sum():,.0f}")
    LOG.info("  status split:\n%s", out.groupby("status")["market_units"].agg(
        ["size", "sum"]).round(0).to_string())
    write_table(out, MARTS / "cannibalisation_external.parquet")
    return out


# ------------------------------------------------------------------ internal

# The job each machine actually does. Two products substitute only if they can do the
# same job -- without this gate, similarity on crop fit alone makes a trolley (which
# suits every crop) look like a competitor to everything, which is nonsense: a farmer
# choosing a trolley is not thereby choosing not to buy a cultivator.
# A SKU can do more than one job; a disc harrow serves both tillage passes.
JOBS = {
    "primary_tillage":  ["MB_PLOUGH_2F", "REV_PLOUGH_2F", "SUBSOILER", "DISC_HARROW_16"],
    "secondary_tillage": ["ROTAVATOR_5FT", "ROTAVATOR_7FT", "CULTIVATOR_9T",
                          "POWER_HARROW", "DISC_HARROW_16"],
    "levelling":        ["LASER_LEVELER"],
    "wheat_sowing":     ["SEED_DRILL_11T", "SEED_FERT_DRILL_13T", "SUPER_SEEDER",
                         "HAPPY_SEEDER"],
    "row_planting":     ["PNEUMATIC_PLANTER", "MULTICROP_PLANTER", "RAISED_BED_PLANTER",
                         "SEED_FERT_DRILL_13T"],
    "paddy_planting":   ["RICE_TRANSPLANTER"],
    "spraying":         ["BOOM_SPRAYER", "HTP_SPRAYER", "ORCHARD_SPRAYER", "AGRI_DRONE"],
    "fertilising":      ["FERT_BROADCASTER", "AGRI_DRONE"],
    "irrigation":       ["PTO_PUMP", "WATER_TANKER_3000L"],
    "grain_harvest":    ["REAPER_BINDER", "TRACTOR_REAPER"],
    "root_harvest":     ["POTATO_HARVESTER"],
    "residue":          ["STRAW_REAPER", "MULCHER", "ROUND_BALER", "HAY_RAKE"],
    "threshing":        ["MULTICROP_THRESHER", "MAIZE_SHELLER"],
    "fodder":           ["CHAFF_CUTTER"],
    "haulage":          ["TROLLEY_2W_5T", "TROLLEY_4W_8T"],
    "guidance":         ["GPS_GUIDANCE_KIT"],
}


def _job_matrix(ids: list[str]) -> np.ndarray:
    """1 where two SKUs can do at least one job in common, else 0."""
    idx = {s: i for i, s in enumerate(ids)}
    M = np.zeros((len(ids), len(ids)))
    for members in JOBS.values():
        ms = [idx[m] for m in members if m in idx]
        for a in ms:
            for b in ms:
                M[a, b] = 1.0
    return M


def sku_overlap() -> pd.DataFrame:
    """How much two Sonalika SKUs compete for the same sale.

    Gated on doing the same job, then scaled by how far they agree on the tractor they
    need, the farm size they suit, the crops they serve and when in the year they sell.
    Two products in different categories can still compete hard -- a super seeder and a
    seed drill both put wheat in the ground -- which is why the gate is the job, not the
    catalogue category.
    """
    skus = Config.skus()
    ids = [s["id"] for s in skus]
    n = len(skus)
    bands = np.array([hp_band_overlap(s) for s in skus])
    sizes = np.array([[s["farm_size_fit"][c] for c in HOLDING_CLASSES] for s in skus])
    crops = np.array([[s["crop_fit"][c] for c in CROPS] for s in skus])
    season = np.array([[1.0 if m in s["season"] else 0.0 for m in range(1, 13)]
                       for s in skus])
    price = np.array([s["price_inr"] for s in skus], dtype=float)

    def cos(A):
        Nn = A / (np.linalg.norm(A, axis=1, keepdims=True) + 1e-12)
        return Nn @ Nn.T

    ov = _job_matrix(ids) * cos(bands) * cos(sizes) * cos(crops)
    ov *= 0.55 + 0.45 * cos(season)
    # A machine four times the price is a different purchase decision, not a substitute.
    ratio = price[:, None] / price[None, :]
    ov *= np.exp(-np.abs(np.log(np.clip(ratio, 1e-6, None))) / 1.4)
    np.fill_diagonal(ov, 0.0)

    jobs_of = {i: [j for j, m in JOBS.items() if i in m] for i in ids}
    rows = []
    for i in range(n):
        for j in range(n):
            if i >= j or ov[i, j] < 0.35:
                continue
            shared = sorted(set(jobs_of[ids[i]]) & set(jobs_of[ids[j]]))
            rows.append({"sku_a": skus[i]["id"], "name_a": skus[i]["name"],
                         "sku_b": skus[j]["id"], "name_b": skus[j]["name"],
                         "category_a": skus[i]["category"], "category_b": skus[j]["category"],
                         "shared_job": ", ".join(shared).replace("_", " "),
                         "overlap": float(ov[i, j]),
                         "price_a": skus[i]["price_inr"], "price_b": skus[j]["price_inr"],
                         "same_category": skus[i]["category"] == skus[j]["category"]})
    out = pd.DataFrame(rows).sort_values("overlap", ascending=False)
    out["provenance"] = "allocated"
    return out


def build_internal_cannibalisation() -> pd.DataFrame:
    """Village-level demand that two Sonalika SKUs are both counting."""
    cfg = Config.sim()["cannibalisation"]
    pairs = sku_overlap()
    write_table(pairs, MARTS / "sku_overlap.parquet")

    ext = read_table(MARTS / "cannibalisation_external.parquet")
    son = ext.groupby(["village_id", "sku_id"])["sonalika_units"].sum().unstack(fill_value=0.0)

    hot = pairs[pairs["overlap"] >= cfg["internal_overlap_threshold"]]

    # Displacement is accumulated per SKU per village as a survival product rather than
    # summed across pairs. A SKU facing three in-house substitutes cannot lose 3 x 45%
    # of its demand; summing pair-wise displacement is how the first version reported
    # 82% of gross demand cannibalised, which is arithmetically impossible.
    retain = pd.DataFrame(1.0, index=son.index, columns=son.columns)
    rows = []
    for _, r in hot.iterrows():
        a, b = r["sku_a"], r["sku_b"]
        if a not in son.columns or b not in son.columns:
            continue
        shared = np.minimum(son[a], son[b])
        frac = float(r["overlap"] * cfg["internal_capture_rate"])
        # the smaller line concedes to the larger one in each village
        smaller_is_a = son[a] <= son[b]
        for sku, mask in ((a, smaller_is_a), (b, ~smaller_is_a)):
            hit = pd.Series(0.0, index=son.index)
            hit[mask] = np.where(son.loc[mask, sku] > 0,
                                 shared[mask] / son.loc[mask, sku].clip(lower=1e-9) * frac,
                                 0.0)
            retain[sku] *= (1.0 - hit.clip(0, 1))
        est = float((shared * frac).sum())
        if est < 1:
            continue
        rows.append({"sku_a": a, "name_a": r["name_a"], "sku_b": b, "name_b": r["name_b"],
                     "shared_job": r["shared_job"], "overlap": r["overlap"],
                     "same_category": r["same_category"],
                     "villages_affected": int((shared > 0.01).sum()),
                     "displaced_units": est,
                     "displaced_value_inr": est * min(r["price_a"], r["price_b"])})

    displaced_by_sku = (son * (1.0 - retain)).sum()
    out = pd.DataFrame(rows).sort_values("displaced_units", ascending=False)
    out["provenance"] = "allocated"

    per_sku = displaced_by_sku[displaced_by_sku > 0].sort_values(ascending=False)
    net = pd.DataFrame({"sku_id": per_sku.index, "displaced_units": per_sku.to_numpy()})
    net["gross_units"] = net["sku_id"].map(son.sum())
    net["displaced_pct"] = net["displaced_units"] / net["gross_units"].clip(lower=1e-9) * 100
    net["provenance"] = "allocated"
    write_table(net, MARTS / "cannibalisation_internal_by_sku.parquet")

    total = float(displaced_by_sku.sum())
    gross = son.to_numpy().sum()
    LOG.info("internal: %d overlapping pairs | net displaced %.0f units "
             "(%.1f%% of gross Sonalika demand, capped per SKU)", len(out), total,
             total / max(gross, 1) * 100)
    if len(out):
        LOG.info("  worst pairs:\n%s", out.head(5)[
            ["name_a", "name_b", "overlap", "displaced_units"]].round(2).to_string(index=False))
    write_table(out, MARTS / "cannibalisation_internal.parquet")
    return out


def build() -> None:
    build_landscape()
    build_player_shares()
    build_external_cannibalisation()
    build_internal_cannibalisation()


if __name__ == "__main__":
    build()
