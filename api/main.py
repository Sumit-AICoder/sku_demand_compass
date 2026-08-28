"""FastAPI service over the DuckDB/Parquet marts.

Thin by design: every endpoint is one DuckDB query against a pre-aggregated mart, so
drill-down stays sub-second on 3.9M scored rows without a server database. The only
endpoint that computes anything is /scenario, which re-scores under user weights and
propagates shocks through the UCM elasticities.
"""
from __future__ import annotations

import threading
from functools import lru_cache

import duckdb
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pipeline.common import CURATED, MARTS, CONFIG as ROOT_CFG, Config, Manifest
from api import chat as chat_mod
from api import llm, narrative

app = FastAPI(title="Sonalika Village-Level SKU Propensity API", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FACTOR_IDS = [f"F{i}" for i in range(1, 11)]


@lru_cache(maxsize=1)
def _root_con() -> duckdb.DuckDBPyConnection:
    """The one connection that owns the database and defines the views."""
    c = duckdb.connect(":memory:")
    views = {
        "village_totals": MARTS / "village_totals.parquet",
        "block_totals": MARTS / "block_totals.parquet",
        "district_totals": MARTS / "district_totals.parquet",
        "district_sku": MARTS / "district_sku_scores.parquet",
        "block_sku": MARTS / "block_sku_scores.parquet",
        "state_sku": MARTS / "state_sku_scores.parquet",
        "village_sku": MARTS / "village_sku_scores.parquet",
        "village_factors": MARTS / "village_factors.parquet",
        "village_features": MARTS / "village_features.parquet",
        "clusters": MARTS / "village_clusters.parquet",
        "village_insights": MARTS / "village_insights.parquet",
        "micro_segments": MARTS / "micro_segments.parquet",
        "landscape": MARTS / "competitive_landscape.parquet",
        "player_shares": MARTS / "player_shares.parquet",
        "cannibal_ext": MARTS / "cannibalisation_external.parquet",
        "cannibal_int": MARTS / "cannibalisation_internal.parquet",
        "cannibal_int_sku": MARTS / "cannibalisation_internal_by_sku.parquet",
        "sku_overlap": MARTS / "sku_overlap.parquet",
        "cluster_profiles": MARTS / "cluster_profiles.parquet",
        "ucm_decomposition": MARTS / "ucm_decomposition.parquet",
        "ucm_betas": MARTS / "ucm_betas.parquet",
        "ucm_diagnostics": MARTS / "ucm_diagnostics.parquet",
        "ucm_vif": MARTS / "ucm_vif.parquet",
        "sku_ref": MARTS / "sku_reference.parquet",
        "sku_weights": MARTS / "sku_weights.parquet",
        "weight_origin": MARTS / "sku_weight_origin.parquet",
        "seasonality": MARTS / "sku_seasonality.parquet",
        "factor_defs": MARTS / "factor_definitions.parquet",
        "feature_dict": MARTS / "feature_dictionary.parquet",
        "geo_districts": CURATED / "geo_districts.parquet",
        "geo_blocks": CURATED / "geo_blocks.parquet",
        "geo_villages": CURATED / "geo_villages.parquet",
        "dealers": CURATED / "dealers.parquet",
        "dealer_network": MARTS / "dealer_network.parquet",
        "dealer_by_oem": MARTS / "dealer_by_oem.parquet",
        "agroclimate": MARTS / "agroclimate.parquet",
        "subsidy": MARTS / "subsidy.parquet",
        "micromarkets": MARTS / "micromarkets.parquet",
        "archetypes_mart": MARTS / "micromarket_archetypes.parquet",
        "village_micromarket": MARTS / "village_micromarket.parquet",
        "micromarket_ops": MARTS / "micromarket_ops.parquet",
        "archetype_ops": MARTS / "archetype_ops.parquet",
        "ucm_arch_decomposition": MARTS / "ucm_archetype_decomposition.parquet",
        "ucm_arch_betas": MARTS / "ucm_archetype_betas.parquet",
        "ucm_arch_diagnostics": MARTS / "ucm_archetype_diagnostics.parquet",
        "competition": CURATED / "competition_shares.parquet",
        "district_series": CURATED / "district_series.parquet",
    }
    for name, path in views.items():
        if path.exists():
            c.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{path}')")
    return c


_local = threading.local()


def con() -> duckdb.DuckDBPyConnection:
    """A per-thread cursor over the shared database.

    FastAPI runs sync endpoints in a threadpool, and a DuckDB connection is not safe for
    concurrent execute: two threads sharing one connection interleave and `fetchdf()`
    can come back None, surfacing as a random 500 on whichever endpoint lost the race.
    `.cursor()` hands each thread its own connection over the same in-memory database,
    so the views are defined once and every thread reads them independently.
    """
    c = getattr(_local, "con", None)
    if c is None:
        c = _root_con().cursor()
        _local.con = c
    return c


def q(sql: str, params: list | None = None) -> list[dict]:
    df = con().execute(sql, params or []).fetchdf()
    if df is None:
        raise RuntimeError("query returned no result set")
    return clean(df.to_dict("records"))


def clean(obj):
    """Strip NaN/inf before serialisation -- JSON has no representation for them."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    if isinstance(obj, (np.floating, np.integer)):
        v = obj.item()
        return None if isinstance(v, float) and not np.isfinite(v) else v
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


# ---------------------------------------------------------------- meta

@app.get("/api/meta")
def meta():
    """Provenance and model-quality summary -- what the UI badges are built from."""
    diag = con().execute("SELECT * FROM ucm_diagnostics").fetchdf()
    origin = con().execute("SELECT origin, count(*) n FROM weight_origin GROUP BY 1").fetchdf()
    prof = con().execute("SELECT * FROM cluster_profiles LIMIT 1").fetchdf()
    return {
        "pilot_states": [s["name"] for s in Config.pilot_states()],
        "counts": {
            "districts": con().execute("SELECT count(*) FROM district_totals").fetchone()[0],
            "blocks": con().execute("SELECT count(*) FROM block_totals").fetchone()[0],
            "villages": con().execute("SELECT count(*) FROM village_totals").fetchone()[0],
            "skus": con().execute("SELECT count(*) FROM sku_ref").fetchone()[0],
        },
        "ucm": {
            "districts_fitted": int(len(diag)),
            "beats_seasonal_naive": int(diag["beats_snaive"].sum()),
            "median_backtest_mape": round(float(diag["backtest_mape"].median()), 1),
            "median_snaive_mape": round(float(diag["snaive_mape"].median()), 1),
            "median_r2": round(float(diag["r2_like"].median()), 3),
            "residual_autocorr_ok": int(diag["resid_autocorr_ok"].sum()),
        },
        "weights": {r["origin"]: int(r["n"]) for _, r in origin.iterrows()},
        "clustering": {
            "k": int(con().execute("SELECT count(*) FROM cluster_profiles").fetchone()[0]),
            "bootstrap_ari": float(prof["bootstrap_ari"].iloc[0]),
            "spatial_coherence": float(prof["spatial_coherence"].iloc[0]),
        },
        "sources": clean(Manifest.summary().to_dict("records")),
        "ai": llm.status(),
    }


@app.get("/api/skus")
def skus():
    return q("SELECT * FROM sku_ref ORDER BY category, name")


@app.get("/api/factors")
def factors():
    return q("SELECT * FROM factor_defs ORDER BY factor")


# ---------------------------------------------------------------- geography drill

@app.get("/api/geo/{level}")
def geo(level: str,
        parent: str | None = None,
        sku: str | None = None,
        category: str | None = None,
        month: int | None = Query(None, ge=1, le=12)):
    """Children of a node with their aggregate scores -- the map + drill payload."""
    if level not in ("state", "district", "block", "village"):
        raise HTTPException(400, "level must be state|district|block|village")

    season = _season_factor(sku, category, month)

    if level == "state":
        rows = q("""
            SELECT state AS "id", state AS "name", NULL::VARCHAR AS "parent",
                   avg(lon) AS lon, avg(lat) AS lat,
                   sum(potential_units_yr) AS "units", sum(potential_value_inr) AS "value",
                   sum(headroom) AS headroom, sum(addressable) AS addressable
            FROM district_totals GROUP BY 1,2 ORDER BY "units" DESC""")
    elif level == "district":
        sql = """SELECT district_id AS "id", district AS "name", state AS "parent",
                        lon, lat, zone, crop_system, mech_tier, top_sku, top_category,
                        potential_units_yr AS "units", potential_value_inr AS "value",
                        headroom, addressable, attach_gap
                 FROM district_totals"""
        rows = q(sql + (" WHERE state = ?" if parent else "") + ' ORDER BY "units" DESC',
                 [parent] if parent else [])
    elif level == "block":
        sql = """SELECT b.block_id AS "id", b.block AS "name", b.district_id AS "parent",
                        b.lon, b.lat, b.top_sku, b.top_category,
                        b.potential_units_yr AS "units", b.potential_value_inr AS "value",
                        b.headroom, b.addressable, b.attach_gap
                 FROM block_totals b"""
        rows = q(sql + (" WHERE b.district_id = ?" if parent else "") + ' ORDER BY "units" DESC',
                 [parent] if parent else [])
    else:
        if not parent:
            raise HTTPException(400, "village level requires a parent block_id")
        rows = q("""SELECT v.village_id AS "id", v.village AS "name", v.block_id AS "parent",
                           v.lon, v.lat, v.top_sku, v.top_category,
                           v.potential_units_yr AS "units", v.potential_value_inr AS "value",
                           v.headroom, v.addressable, v.attach_gap, c.archetype
                    FROM village_totals v LEFT JOIN clusters c USING (village_id)
                    WHERE v.block_id = ? ORDER BY "units" DESC LIMIT 3000""", [parent])

    if sku or category:
        rows = _rescope(rows, level, parent, sku, category)
    for r in rows:
        if r.get("units") is not None:
            r["units"] = r["units"] * season
            r["value"] = (r.get("value") or 0) * season
    return {"level": level, "parent": parent, "season_factor": round(season, 3), "items": rows}


def _rescope(rows, level, parent, sku, category):
    """Replace the all-SKU totals with the selected SKU or category."""
    key = {"district": ("district_sku", "district_id"),
           "block": ("block_sku", "block_id"),
           "state": ("state_sku", "state"),
           "village": ("village_sku", "village_id")}.get(level)
    if key is None:
        return rows
    tbl, col = key
    where, params = [], []
    if sku:
        where.append("sku_id = ?"); params.append(sku)
    elif category:
        where.append("category = ?"); params.append(category)
    if parent and level == "village":
        where.append(f"{col} IN (SELECT village_id FROM geo_villages WHERE block_id = ?)")
        params.append(parent)
    sql = (f'SELECT {col} AS "id", sum(potential_units_yr) AS "units", '
           f'sum(potential_value_inr) AS "value", sum(headroom) AS headroom, '
           f'sum(addressable) AS addressable '
           f'FROM {tbl} WHERE ' + " AND ".join(where) + ' GROUP BY 1')
    m = {r["id"]: r for r in q(sql, params)}
    out = []
    for r in rows:
        s = m.get(r["id"])
        if s is None:
            continue
        r.update({k: s[k] for k in ("units", "value", "headroom", "addressable")})
        out.append(r)
    return sorted(out, key=lambda x: -(x["units"] or 0))


def _season_factor(sku: str | None, category: str | None, month: int | None) -> float:
    if month is None:
        return 1.0
    if sku:
        r = q("SELECT season_index FROM seasonality WHERE sku_id=? AND month_of_year=?",
              [sku, month])
    elif category:
        r = q("""SELECT avg(s.season_index) season_index FROM seasonality s
                 JOIN sku_ref r USING (sku_id) WHERE r.category=? AND s.month_of_year=?""",
              [category, month])
    else:
        r = q("SELECT avg(season_index) season_index FROM seasonality WHERE month_of_year=?",
              [month])
    return float(r[0]["season_index"]) if r and r[0]["season_index"] else 1.0


# ---------------------------------------------------------------- scores & drivers

@app.get("/api/scores")
def scores(level: str = "district", id: str | None = None,
           category: str | None = None, limit: int = 40):
    """SKU ranking within a geography node."""
    tbl, col = {"state": ("state_sku", "state"),
                "district": ("district_sku", "district_id"),
                "block": ("block_sku", "block_id")}.get(level, ("district_sku", "district_id"))
    where, params = [], []
    if id:
        where.append(f"{col} = ?"); params.append(id)
    if category:
        where.append("category = ?"); params.append(category)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    return q(f"""SELECT s.sku_id, r.name, s.category, r.category_label, r.price_inr,
                        r.hp_min, r.hp_max, r.maturity,
                        sum(s.potential_units_yr) AS "units",
                        sum(s.potential_value_inr) AS "value",
                        sum(s.new_units_yr) AS new_units,
                        sum(s.replacement_units_yr) AS replacement_units,
                        sum(s.headroom) AS headroom, sum(s.addressable) AS addressable,
                        avg(s.propensity) AS propensity
                 FROM {tbl} s JOIN sku_ref r USING (sku_id){w}
                 GROUP BY 1,2,3,4,5,6,7,8 ORDER BY "units" DESC LIMIT {int(limit)}""", params)


@app.get("/api/drivers")
def drivers(village_id: str, sku_id: str):
    """Waterfall decomposition of one village x SKU score, with weight origins."""
    f = q("SELECT * FROM village_factors WHERE village_id = ?", [village_id])
    if not f:
        raise HTTPException(404, "village not found")
    f = f[0]
    w = q("SELECT * FROM sku_weights WHERE sku_id = ?", [sku_id])
    if not w:
        raise HTTPException(404, "sku not found")
    w = w[0]
    origin = {r["factor"]: r["origin"]
              for r in q("SELECT factor, origin FROM weight_origin WHERE sku_id = ?", [sku_id])}
    defs = {r["factor"]: r for r in q("SELECT * FROM factor_defs")}
    row = q("""SELECT * FROM village_sku WHERE village_id = ? AND sku_id = ?""",
            [village_id, sku_id])

    contrib = []
    for fid in FACTOR_IDS:
        contrib.append({
            "factor": fid,
            "label": defs[fid]["label"],
            "index": round(float(f[fid]), 1),
            "index_state_scope": round(float(f.get(f"{fid}_state") or 0), 1),
            "weight": round(float(w[fid]), 4),
            "contribution": round(float(f[fid]) / 100.0 * float(w[fid]), 4),
            "origin": origin.get(fid, "prior"),
            "evidence": defs[fid]["village_evidence"],
            "excel_impact": defs[fid]["excel_impact"],
        })
    contrib.sort(key=lambda x: -abs(x["contribution"]))
    return {"village_id": village_id, "sku_id": sku_id,
            "contributions": contrib, "score": row[0] if row else None}


@app.get("/api/village/{village_id}")
def village(village_id: str):
    v = q("""SELECT v.*, c.archetype, c.cluster_spatial AS "cluster"
             FROM village_totals v LEFT JOIN clusters c USING (village_id)
             WHERE v.village_id = ?""", [village_id])
    if not v:
        raise HTTPException(404, "village not found")
    return {
        "village": v[0],
        "factors": (q("SELECT * FROM village_factors WHERE village_id = ?", [village_id]) or [None])[0],
        "features": (q("SELECT * FROM village_features WHERE village_id = ?", [village_id]) or [None])[0],
        "top_skus": q("""SELECT s.sku_id, r.name, s.category, s.potential_units_yr AS "units",
                                s.potential_value_inr AS "value", s.propensity, s.penetration,
                                s.headroom, s.addressable
                         FROM village_sku s JOIN sku_ref r USING (sku_id)
                         WHERE s.village_id = ? ORDER BY "units" DESC LIMIT 12""", [village_id]),
    }


# ---------------------------------------------------------------- UCM

@app.get("/api/ucm/decomposition")
def ucm_decomposition(district_id: str):
    rows = q("""SELECT * FROM ucm_decomposition WHERE district_id = ? ORDER BY month""",
             [district_id])
    if not rows:
        raise HTTPException(404, "district not fitted")
    diag = q("SELECT * FROM ucm_diagnostics WHERE district_id = ?", [district_id])
    return {"district_id": district_id, "series": rows,
            "diagnostics": diag[0] if diag else None}


@app.get("/api/ucm/uplift")
def ucm_uplift(district_id: str, months: int = 12):
    """Year-on-year uplift attribution: which factors produced this year's growth."""
    d = con().execute(
        "SELECT * FROM ucm_decomposition WHERE district_id = ? ORDER BY month", [district_id]
    ).fetchdf()
    if d.empty:
        raise HTTPException(404, "district not fitted")
    if len(d) < months * 2:
        raise HTTPException(400, "series too short for a year-on-year comparison")

    cur, prev = d.iloc[-months:], d.iloc[-2 * months:-months]
    comps = ["trend", "seasonal", "cycle", "irregular"] + \
            [c for c in d.columns if c.startswith("contrib_")]
    total = float(cur["observed_log"].mean() - prev["observed_log"].mean())
    out = []
    for c in comps:
        delta = float(cur[c].mean() - prev[c].mean())
        if abs(delta) < 1e-6:
            continue
        out.append({
            "component": c.replace("contrib_", ""),
            "kind": "structural" if c in ("trend", "seasonal", "cycle", "irregular") else "factor",
            "delta_log": delta,
            "pp_of_growth": round(delta * 100, 2),
        })
    out.sort(key=lambda x: -abs(x["delta_log"]))
    return {"district_id": district_id,
            "total_growth_pct": round((np.exp(total) - 1) * 100, 2),
            "current_units": round(float(cur["observed"].sum()), 1),
            "prior_units": round(float(prev["observed"].sum()), 1),
            "components": out}


@app.get("/api/ucm/elasticities")
def ucm_elasticities(district_id: str | None = None):
    if district_id:
        return q("SELECT * FROM ucm_betas WHERE district_id = ? ORDER BY abs(beta) DESC",
                 [district_id])
    return q("""SELECT regressor, factor, expected_sign,
                       avg(beta) beta, avg(ci_low) ci_low, avg(ci_high) ci_high,
                       avg(CASE WHEN significant THEN 1 ELSE 0 END) sig_share,
                       avg(CASE WHEN sign_ok THEN 1 ELSE 0 END) sign_ok_share,
                       avg(CASE WHEN usable THEN 1 ELSE 0 END) usable_share,
                       count(*) n_districts
                FROM ucm_betas GROUP BY 1,2,3 ORDER BY abs(avg(beta)) DESC""")


@app.get("/api/ucm/diagnostics")
def ucm_diag():
    return {"districts": q("SELECT * FROM ucm_diagnostics ORDER BY backtest_mape"),
            "vif": q("SELECT * FROM ucm_vif ORDER BY vif DESC")}


# ---------------------------------------------------------------- clusters

@app.get("/api/clusters")
def clusters():
    return q("SELECT * FROM cluster_profiles ORDER BY n_villages DESC")


@app.get("/api/clusters/{cluster_id}/skus")
def cluster_skus(cluster_id: int, limit: int = 12):
    """The SKU basket an archetype over-indexes on, versus the national mix."""
    return q("""
        WITH c AS (SELECT sku_id, sum(potential_units_yr) u FROM village_sku
                   WHERE cluster = ? GROUP BY 1),
             n AS (SELECT sku_id, sum(potential_units_yr) u FROM village_sku GROUP BY 1),
             ct AS (SELECT sum(u) t FROM c), nt AS (SELECT sum(u) t FROM n)
        SELECT c.sku_id, r.name, r.category, c.u AS "units",
               (c.u / (SELECT t FROM ct)) / NULLIF(n.u / (SELECT t FROM nt), 0) AS index_vs_national
        FROM c JOIN n USING (sku_id) JOIN sku_ref r USING (sku_id)
        ORDER BY index_vs_national DESC LIMIT ?""", [cluster_id, limit])


@app.get("/api/whitespace")
def whitespace(cluster_id: int | None = None, state: str | None = None, limit: int = 100):
    """Villages under-penetrated relative to their own archetype -- targeting priorities."""
    where, params = ["1=1"], []
    if cluster_id is not None:
        where.append("c.cluster_spatial = ?"); params.append(cluster_id)
    if state:
        where.append("v.state = ?"); params.append(state)
    return q(f"""
        WITH x AS (
          SELECT v.village_id, v.village, v.state, d.district, c.archetype,
                 c.cluster_spatial AS "cluster", f.attach_rate, f.peer_attach_rate,
                 f.adoption_gap_vs_peers, v.potential_units_yr AS "units", v.headroom
          FROM village_totals v
          JOIN clusters c USING (village_id)
          JOIN village_features f USING (village_id)
          JOIN geo_districts d ON d.district_id = v.district_id
          WHERE {' AND '.join(where)})
        SELECT *, (peer_attach_rate - attach_rate) AS gap_to_peers
        FROM x WHERE attach_rate < peer_attach_rate
        ORDER BY (peer_attach_rate - attach_rate) * "units" DESC LIMIT {int(limit)}""", params)


@app.get("/api/lookalike")
def lookalike(village_id: str, n: int = 25):
    """Nearest villages in feature space -- 'find every village like this one'."""
    feats = ["avg_holding_ha", "irrigation_reliability", "cropping_intensity",
             "tractor_density", "attach_rate", "farm_power_kw_ha", "income_per_ha",
             "high_value_share", "residue_burden_per_ha", "dealer_accessibility"]
    df = con().execute(
        f"SELECT village_id, state, {','.join(feats)} FROM village_features").fetchdf()
    if village_id not in set(df["village_id"]):
        raise HTTPException(404, "village not found")
    X = df[feats].to_numpy(float)
    mu, sd = X.mean(0), X.std(0)
    sd[sd < 1e-9] = 1.0
    Z = (X - mu) / sd
    i = int(np.where(df["village_id"].to_numpy() == village_id)[0][0])
    d = np.sqrt(((Z - Z[i]) ** 2).sum(1))
    order = np.argsort(d)[1:n + 1]
    out = df.iloc[order][["village_id", "state"] + feats].copy()
    out["distance"] = d[order]
    tot = con().execute("SELECT village_id, potential_units_yr FROM village_totals").fetchdf()
    out = out.merge(tot, on="village_id", how="left")
    return clean(out.to_dict("records"))


# ---------------------------------------------------------------- scenario

class Scenario(BaseModel):
    weights: dict[str, float] | None = Field(
        None, description="Factor weight overrides, e.g. {'F6': 0.30}")
    shocks: dict[str, float] | None = Field(
        None, description="Regressor shocks in standard deviations, e.g. {'rainfall_departure': -1.5}")
    sku_id: str | None = None
    category: str | None = None
    state: str | None = None
    level: str = "district"


@app.post("/api/scenario")
def scenario(s: Scenario):
    """Re-score under user weights, and propagate shocks through the UCM elasticities.

    The shock path is what makes this quantitative rather than directional: a shock of
    -1.5 sd on rainfall moves demand by beta_rainfall x -1.5 in log space, and the
    confidence band comes from the estimated standard errors, not from a guess.
    """
    where, params = [], []
    if s.sku_id:
        where.append("s.sku_id = ?"); params.append(s.sku_id)
    elif s.category:
        where.append("s.category = ?"); params.append(s.category)
    if s.state:
        where.append("v.state = ?"); params.append(s.state)
    w = (" WHERE " + " AND ".join(where)) if where else ""

    base = con().execute(f"""
        SELECT v.state, v.district_id, s.village_id, s.sku_id,
               s.potential_units_yr AS "units", s.potential_value_inr AS "value", s.propensity
        FROM village_sku s JOIN geo_villages v USING (village_id){w}""", params).fetchdf()
    if base.empty:
        raise HTTPException(404, "no rows match the scenario filters")

    # ---- weight override ----------------------------------------------------
    weight_mult = pd.Series(1.0, index=base.index)
    if s.weights:
        bad = set(s.weights) - set(FACTOR_IDS)
        if bad:
            raise HTTPException(400, f"unknown factors {bad}")
        F = con().execute(
            f"SELECT village_id, {','.join(FACTOR_IDS)} FROM village_factors").fetchdf().set_index("village_id")
        W = con().execute("SELECT * FROM sku_weights").fetchdf().set_index("sku_id")
        newW = W.copy()
        for f_, v_ in s.weights.items():
            newW[f_] = v_
        pos = newW[FACTOR_IDS].clip(lower=0).sum(axis=1).replace(0, 1.0)
        newW[FACTOR_IDS] = newW[FACTOR_IDS].div(pos, axis=0)

        fac = F.reindex(base["village_id"])[FACTOR_IDS].to_numpy() / 100.0
        w_old = W.reindex(base["sku_id"])[FACTOR_IDS].to_numpy()
        w_new = newW.reindex(base["sku_id"])[FACTOR_IDS].to_numpy()
        old = (fac * w_old).sum(1)
        new = (fac * w_new).sum(1)
        weight_mult = pd.Series(np.divide(new, old, out=np.ones_like(new), where=old > 1e-9),
                                index=base.index)

    # ---- shock via UCM elasticities ----------------------------------------
    # Applied with each DISTRICT's own beta, not the pooled mean. That difference is
    # the whole point: a monsoon shock should hit a rainfed Vidarbha district far
    # harder than an assured-irrigation Punjab district, and pooled betas would flatten
    # exactly the variation a planner needs to see.
    shock_mult = pd.Series(1.0, index=base.index)
    band_series = pd.Series(0.0, index=base.index)
    applied = []
    if s.shocks:
        B = con().execute("""SELECT district_id, regressor, beta, se, usable
                             FROM ucm_betas""").fetchdf()
        known = set(B["regressor"].unique())
        bad = set(s.shocks) - known
        if bad:
            raise HTTPException(400, f"unknown regressors {bad}; known: {sorted(known)}")

        log_delta = pd.Series(0.0, index=base.index)
        var = pd.Series(0.0, index=base.index)
        for r_, sd_ in s.shocks.items():
            br = B[B["regressor"] == r_].set_index("district_id")
            beta_v = base["district_id"].map(br["beta"]).fillna(br["beta"].mean())
            se_v = base["district_id"].map(br["se"]).fillna(br["se"].mean())
            log_delta += beta_v * sd_
            var += (se_v * sd_) ** 2
            applied.append({
                "regressor": r_, "shock_sd": sd_,
                "beta_pooled": round(float(br["beta"].mean()), 4),
                "beta_min": round(float(br["beta"].min()), 4),
                "beta_max": round(float(br["beta"].max()), 4),
                "effect_pct_pooled": round((float(np.exp(br["beta"].mean() * sd_)) - 1) * 100, 2),
                "usable_share": round(float(br["usable"].mean()), 2),
            })
        shock_mult = np.exp(log_delta)
        band_series = 1.645 * np.sqrt(var)          # 90% band in log space

    base["units_scenario"] = base["units"] * weight_mult * shock_mult
    base["value_scenario"] = base["value"] * weight_mult * shock_mult
    base["units_lo"] = base["units_scenario"] * np.exp(-band_series)
    base["units_hi"] = base["units_scenario"] * np.exp(band_series)

    key = {"state": "state", "district": "district_id"}.get(s.level, "district_id")
    g = base.groupby(key).agg(units_base=("units", "sum"),
                              units_scenario=("units_scenario", "sum"),
                              units_lo=("units_lo", "sum"),
                              units_hi=("units_hi", "sum"),
                              value_base=("value", "sum"),
                              value_scenario=("value_scenario", "sum")).reset_index()
    g["delta_units"] = g["units_scenario"] - g["units_base"]
    g["delta_pct"] = np.where(g["units_base"] > 0,
                              g["delta_units"] / g["units_base"] * 100, 0)
    g["delta_pct_lo"] = np.where(g["units_base"] > 0,
                                 (g["units_lo"] / g["units_base"] - 1) * 100, 0)
    g["delta_pct_hi"] = np.where(g["units_base"] > 0,
                                 (g["units_hi"] / g["units_base"] - 1) * 100, 0)
    g = g.sort_values("delta_units")

    tb, ts = float(base["units"].sum()), float(base["units_scenario"].sum())
    lo, hi = float(base["units_lo"].sum()), float(base["units_hi"].sum())
    return {
        "total": {
            "units_base": round(tb, 1), "units_scenario": round(ts, 1),
            "delta_pct": round((ts / tb - 1) * 100, 2) if tb else 0.0,
            "ci_low_pct": round((lo / tb - 1) * 100, 2) if tb else 0.0,
            "ci_high_pct": round((hi / tb - 1) * 100, 2) if tb else 0.0,
        },
        "shocks_applied": applied,
        "by_level": clean(g.to_dict("records")),
    }


@app.get("/api/compete")
def compete(district_id: str | None = None, category: str | None = None):
    where, params = [], []
    if district_id:
        where.append("district_id = ?"); params.append(district_id)
    if category:
        where.append("category = ?"); params.append(category)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    return q(f"""SELECT player, category, avg(share) AS "share"
                 FROM competition{w} GROUP BY 1,2 ORDER BY "share" DESC""", params)


# ---------------------------------------------------------------- define: micro-markets & archetypes

_MM_METRICS = {"tiv", "potential_units_yr", "sonalika_share", "n_villages"}
_CUSTOM_PATH = MARTS / "custom_archetypes.json"
_CROP_SHARES = ["crop_wheat_share", "crop_rice_share", "crop_cotton_share",
                "crop_soybean_share", "crop_sugarcane_share", "crop_maize_share"]


def _load_rules() -> list[dict]:
    import json as _j
    if _CUSTOM_PATH.exists():
        try:
            return _j.loads(_CUSTOM_PATH.read_text())
        except Exception:                                      # noqa: BLE001
            return []
    return []


def _save_rules(rules: list[dict]) -> None:
    import json as _j
    _CUSTOM_PATH.write_text(_j.dumps(rules))


def _apply_rules(df: pd.DataFrame, rules: list[dict]) -> pd.DataFrame:
    """Reassign micro-markets that match each active custom rule to that new archetype.

    Applied in order; later rules win. Thresholds are computed against the base
    population so a rule means the same thing regardless of order.
    """
    for rule in rules:
        m = pd.Series(True, index=df.index)
        if rule.get("tiv") == "high":
            m &= df["tiv"] >= df["tiv"].quantile(0.66)
        elif rule.get("tiv") == "low":
            m &= df["tiv"] <= df["tiv"].quantile(0.33)
        if rule.get("hp_belt"):
            m &= df["hp_belt"] == rule["hp_belt"]
        crop = rule.get("crop")
        if crop and f"crop_{crop}_share" in df.columns:
            m &= df[f"crop_{crop}_share"] >= 0.15
        if rule.get("irrigation") == "irrigated":
            m &= df["irrigation_reliability"] >= df["irrigation_reliability"].median()
        elif rule.get("irrigation") == "rainfed":
            m &= df["irrigation_reliability"] < df["irrigation_reliability"].median()
        if rule.get("subzone_id"):
            m &= df["subzone_id"] == rule["subzone_id"]
        if rule.get("zone"):
            m &= df["zone"] == rule["zone"]
        df.loc[m, "archetype"] = rule["name"]
        df.loc[m, "base_name"] = rule["name"]
        df.loc[m, "hp_belt"] = "(custom)"
        df.loc[m, "tiv_tier"] = "(custom)"
        df.loc[m, "subzone"] = "(custom)"
        df.loc[m, "archetype_id"] = "custom-" + rule["name"]
    return df


def _current_mm() -> pd.DataFrame:
    """The micro-market table with any active custom archetypes applied -- the single
    source every Define view reads, so a reconfigure shows up on all tabs at once."""
    df = con().execute("SELECT * FROM micromarkets").fetchdf()
    return _apply_rules(df, _load_rules())


def _summarise_mm(df: pd.DataFrame) -> list[dict]:
    rows = []
    # group by the unique archetype_id (sub-zone | TIV | HP), NOT the display name -- names
    # repeat across sub-zones by design (crop+TIV), so the sub-zone still divides them.
    for aid, g in df.groupby("archetype_id"):
        tiv = float(g["tiv"].sum())
        rows.append({
            "archetype_id": str(aid), "archetype": g["archetype"].iloc[0],
            "base_name": g["base_name"].iloc[0], "hp_belt": g["hp_belt"].iloc[0],
            "zone": g["zone"].iloc[0], "zone_name": g["zone_name"].iloc[0],
            "subzone_id": g["subzone_id"].iloc[0], "subzone": g["subzone"].iloc[0],
            "lgp": g["lgp"].iloc[0], "tiv_tier": g["tiv_tier"].iloc[0],
            "n_micromarkets": int(len(g)), "n_villages": int(g["n_villages"].sum()),
            "tiv": round(tiv),
            "avg_sonalika_share": round(float((g["sonalika_share"] * g["tiv"]).sum() / max(tiv, 1)), 4),
            "potential_units_yr": round(float(g["potential_units_yr"].sum())),
            "mean_hp": round(float((g["mean_hp"] * g["tiv"]).sum() / max(tiv, 1)), 1),
            "states": ", ".join(g["state"].value_counts().head(3).index),
            "is_custom": bool(str(g["archetype_id"].iloc[0]).startswith("custom-")),
        })
    rows.sort(key=lambda r: -r["potential_units_yr"])
    return rows


@app.get("/api/define/districts")
def define_districts():
    """District profile for Define: real agro-climate (incl. crop-mix) + modelled TIV/share."""
    crop_sel = ", ".join(f"a.{c}" for c in _CROP_SHARES)
    rows = q(f"""
        SELECT a.district_id, a.district, a.state,
               a.mean_temp, a.temp_seasonality, a.rain_normal_mm, a.rain_departure_pct,
               a.total_crop_area_lha, a.top_crops, a.temp_is_allocated, {crop_sel},
               m.tiv, m.sonalika_share, m.n_micromarkets, m.n_villages,
               m.subzone_id, m.subzone, m.zone_name, m.lgp,
               t.potential_units_yr AS demand_units
        FROM agroclimate a
        LEFT JOIN (SELECT district_id, sum(tiv) AS tiv,
                          count(*) AS n_micromarkets, sum(n_villages) AS n_villages,
                          sum(tiv * sonalika_share) / nullif(sum(tiv), 0) AS sonalika_share,
                          max(subzone_id) AS subzone_id, max(subzone) AS subzone,
                          max(zone_name) AS zone_name, max(lgp) AS lgp
                   FROM micromarkets GROUP BY district_id) m USING (district_id)
        LEFT JOIN district_totals t USING (district_id)
        ORDER BY a.state, a.district
    """)
    return {"provenance": "mixed", "districts": rows}


@app.get("/api/archetypes")
def archetypes():
    """Base-segment x HP-belt archetypes (with any active custom archetypes applied)."""
    df = _current_mm()
    rows = _summarise_mm(df)
    tot_tiv = sum(r["tiv"] for r in rows) or 1
    totals = {
        "n_archetypes": len(rows),
        "n_micromarkets": int(len(df)),
        "n_villages": int(df["n_villages"].sum()),
        "tiv": sum(r["tiv"] for r in rows),
        "potential_units_yr": sum(r["potential_units_yr"] for r in rows),
        "avg_sonalika_share": sum(r["avg_sonalika_share"] * r["tiv"] for r in rows) / tot_tiv,
    }
    belts = []
    for belt, g in df.groupby("hp_belt"):
        belts.append({"hp_belt": belt, "archetypes": int(g["archetype_id"].nunique()),
                      "micromarkets": int(len(g)), "tiv": round(float(g["tiv"].sum()))})
    belts.sort(key=lambda b: -b["tiv"])
    # NARP sub-zones present (the agro-climatic axis), for display + the Configure dropdown
    zones = []
    for (zid, zn, sid, sub, lgp), g in df[df["subzone_id"] != ""].groupby(
            ["zone", "zone_name", "subzone_id", "subzone", "lgp"]):
        zones.append({"zone": zid, "zone_name": zn, "subzone_id": sid, "subzone": sub,
                      "lgp": lgp, "micromarkets": int(len(g)), "tiv": round(float(g["tiv"].sum())),
                      "states": ", ".join(g["state"].value_counts().head(3).index)})
    zones.sort(key=lambda z: (z["zone"], z["subzone_id"]))
    n_custom = sum(1 for r in rows if r["is_custom"])
    return {"provenance": "allocated", "archetypes": rows, "totals": clean(totals),
            "hp_belts": belts, "subzones": zones, "custom_count": n_custom}


@app.get("/api/micromarkets")
def micromarkets(district: str | None = None, archetype: str | None = None,
                 hp_belt: str | None = None, metric: str = "tiv", limit: int = 600):
    metric = metric if metric in _MM_METRICS else "tiv"
    df = _current_mm()
    if district:
        df = df[df["district_id"] == district]
    if archetype:
        df = df[df["archetype"] == archetype]
    if hp_belt:
        df = df[df["hp_belt"] == hp_belt]
    df = df.sort_values(metric, ascending=False).head(limit)
    keep = ["micro_market_id", "district_id", "district", "state", "lon", "lat",
            "n_villages", "tiv", "mean_hp", "hp_belt", "sonalika_share",
            "potential_units_yr", "archetype", "base_name", "zone", "zone_name",
            "subzone_id", "subzone", "lgp", "tiv_tier", "mean_temp",
            "rain_normal_mm", "top_crops", "dominant_crop", "dealer_accessibility"]
    return {"metric": metric, "micromarkets": clean(df[keep].to_dict("records"))}


@app.get("/api/micromarket/{mm_id}")
def micromarket_detail(mm_id: str):
    df = _current_mm()
    row = df[df["micro_market_id"] == mm_id]
    mm = clean(row.to_dict("records"))[0] if len(row) else None
    villages = q("""SELECT g.village_id, g.village, g.lon, g.lat,
                           t.potential_units_yr, t.addressable
                    FROM village_micromarket v
                    JOIN geo_villages g USING (village_id)
                    LEFT JOIN village_totals t USING (village_id)
                    WHERE v.micro_market_id = ? ORDER BY t.potential_units_yr DESC""", [mm_id])
    return {"micromarket": mm, "villages": villages}


class ConfigureRule(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    tiv: str | None = None          # 'high' | 'low' | None
    hp_belt: str | None = None      # e.g. '41-50 HP'
    crop: str | None = None         # 'wheat' | 'rice' | 'cotton' | 'soybean' | 'sugarcane'
    irrigation: str | None = None   # 'irrigated' | 'rainfed' | None
    subzone_id: str | None = None   # NARP sub-zone id, e.g. '6.4'
    zone: str | None = None         # NARP zone id, e.g. '6'


@app.post("/api/archetypes/configure")
def configure(rule: ConfigureRule):
    """Define a NEW archetype from a rule and PERSIST it, so every Define tab updates.

    The rule (thresholds over TIV, HP belt, crop share, irrigation) is saved; matching
    micro-markets are reassigned to the new archetype on every read. Re-running with an
    existing name replaces that rule. Deterministic and transparent.
    """
    rules = [r for r in _load_rules() if r.get("name") != rule.name]
    rules.append(rule.model_dump(exclude_none=True))
    _save_rules(rules)

    df = _current_mm()
    matched = df[df["archetype"] == rule.name]
    summary = _summarise_mm(df)
    return {"new_archetype": rule.name, "moved_micromarkets": int(len(matched)),
            "n_archetypes": len(summary), "custom_count": len(rules),
            "archetypes": summary}


@app.post("/api/archetypes/reset")
def configure_reset():
    """Clear all custom archetypes -- revert Define to the base base-segment x HP model."""
    _save_rules([])
    df = _current_mm()
    return {"n_archetypes": df["archetype"].nunique(), "custom_count": 0}


# ---------------------------------------------------------------- review: what drives sales (archetype UCM)

@app.get("/api/archetype-ucm/decomposition")
def archetype_ucm_decomposition(archetype_id: str):
    """Daily causal decomposition for one archetype: actual, baseline (trend+seasonal,
    stripped of weather/holiday/promo/price/competition), predicted, and each factor's
    uplift. Sales history is SIMULATED (no real daily/weekly Sonalika feed exists)."""
    rows = q("SELECT * FROM ucm_arch_decomposition WHERE archetype_id = ? ORDER BY date",
             [archetype_id])
    if not rows:
        raise HTTPException(404, "archetype not fitted")
    diag = q("SELECT * FROM ucm_arch_diagnostics WHERE archetype_id = ?", [archetype_id])
    return {"archetype_id": archetype_id, "provenance": "simulated", "series": rows,
            "diagnostics": diag[0] if diag else None}


@app.get("/api/archetype-ucm/elasticities")
def archetype_ucm_elasticities(archetype_id: str | None = None):
    if archetype_id:
        return q("SELECT * FROM ucm_arch_betas WHERE archetype_id = ? ORDER BY abs(beta) DESC",
                 [archetype_id])
    return q("""SELECT regressor, label, expected_sign,
                       avg(beta) beta, avg(true_beta) true_beta,
                       avg(ci_low) ci_low, avg(ci_high) ci_high,
                       avg(CASE WHEN significant THEN 1 ELSE 0 END) sig_share,
                       avg(CASE WHEN sign_ok THEN 1 ELSE 0 END) sign_ok_share,
                       count(*) n_archetypes
                FROM ucm_arch_betas GROUP BY 1,2,3 ORDER BY abs(avg(beta)) DESC""")


@app.get("/api/archetype-ucm/diagnostics")
def archetype_ucm_diagnostics():
    return {"archetypes": q("""SELECT d.*, a.base_name, a.hp_belt, a.diagnosis
                               FROM ucm_arch_diagnostics d
                               LEFT JOIN archetype_ops a USING (archetype_id)
                               ORDER BY backtest_wape""")}


@app.get("/api/archetype-ucm/uplift")
def archetype_ucm_uplift(archetype_id: str, days: int = 180):
    """Trailing-vs-prior-period uplift attribution, in sales units (this model is a
    levels model, not log, so the delta is additive units, not log-percent)."""
    d = con().execute(
        "SELECT * FROM ucm_arch_decomposition WHERE archetype_id = ? ORDER BY date",
        [archetype_id]).fetchdf()
    if d.empty:
        raise HTTPException(404, "archetype not fitted")
    if len(d) < days * 2:
        raise HTTPException(400, "series too short for this comparison window")

    cur, prev = d.iloc[-days:], d.iloc[-2 * days:-days]
    uplift_cols = [c for c in d.columns if c.startswith("uplift_")]
    comps = ["baseline"] + uplift_cols + ["residual"]
    prev_total = float(prev["actual_sales"].sum())
    cur_total = float(cur["actual_sales"].sum())
    out = []
    for c in comps:
        delta_units = float(cur[c].sum() - prev[c].sum())
        if abs(delta_units) < 1e-6:
            continue
        out.append({
            "component": c.replace("uplift_", "").replace("baseline", "Baseline (trend+seasonal)")
                          .replace("residual", "Unexplained"),
            "kind": "structural" if c in ("baseline", "residual") else "factor",
            "delta_units": round(delta_units, 1),
            "pp_of_growth": round(delta_units / max(prev_total, 1e-6) * 100, 2),
        })
    out.sort(key=lambda x: -abs(x["delta_units"]))
    return {"archetype_id": archetype_id, "days": days,
            "total_growth_pct": round((cur_total - prev_total) / max(prev_total, 1e-6) * 100, 2),
            "current_units": round(cur_total, 1), "prior_units": round(prev_total, 1),
            "components": out}


# ---------------------------------------------------------------- plan: subsidy + sizing (REAL)

@app.get("/api/subsidy")
def subsidy(state: str | None = None):
    """Real equipment-subsidy rates by state (Punjab/Maharashtra real; MP = SMAM proxy)."""
    where = "WHERE s.state = ?" if state else ""
    rows = q(f"""
        SELECT s.state, s.sku_id, r.name, s.category, r.category_label,
               s.subsidy_pct, s.provenance
        FROM subsidy s LEFT JOIN sku_ref r USING (sku_id)
        {where} ORDER BY s.subsidy_pct DESC, s.category
    """, [state] if state else [])
    return {"rows": rows}


@app.get("/api/plan/priorities")
def plan_priorities(state: str = "Punjab", product: str = "implements"):
    """Focus-product ranking: demand potential met with the real subsidy lever per state.

    High demand + high subsidy is the fastest-moving push; the subsidy column is real for
    Punjab/Maharashtra and a national-SMAM proxy for MP.
    """
    rows = q("""
        SELECT ss.sku_id, r.name, ss.category, r.category_label,
               ss.potential_units_yr AS units, ss.new_units_yr AS new_units,
               ss.replacement_units_yr AS replace_units,
               ss.potential_value_inr AS value,
               sub.subsidy_pct, sub.provenance AS subsidy_provenance
        FROM state_sku ss
        LEFT JOIN sku_ref r USING (sku_id)
        LEFT JOIN subsidy sub ON sub.state = ss.state AND sub.sku_id = ss.sku_id
        WHERE ss.state = ?
        ORDER BY ss.potential_units_yr DESC
    """, [state])
    return {"state": state, "product_line": product, "skus": rows}


@app.get("/api/plan/districts")
def plan_districts():
    """District priorities anchored to REAL cropland: demand vs DES cropped area, and the
    demand intensity per '000 ha -- an under-penetrated district has high cropland but low
    demand-per-hectare captured today."""
    rows = q("""
        SELECT t.district_id, t.district, t.state,
               t.potential_units_yr AS units,
               a.total_crop_area_lha AS crop_area_lha,
               CASE WHEN a.total_crop_area_lha > 0
                    THEN t.potential_units_yr / (a.total_crop_area_lha * 100.0) END AS units_per_kha
        FROM district_totals t
        LEFT JOIN agroclimate a USING (district_id)
        ORDER BY t.potential_units_yr DESC
    """)
    return {"provenance": "real", "districts": rows}


# ---------------------------------------------------------------- agro-climate (REAL)

@app.get("/api/agroclimate")
def agroclimate():
    """Real district agro-climatic profile: temperature, IMD rainfall, DES crop-mix.

    This is the real half of a micro-market's definition (the other half, tractor TIV /
    HP mix, is still ITL-pending). Joined to demand so the Define stage can show what
    kind of place each district is.
    """
    rows = q("""
        SELECT a.district_id, a.district, a.state,
               a.mean_temp, a.temp_seasonality, a.rain_normal_mm, a.rain_departure_pct,
               a.total_crop_area_lha, a.top_crops,
               a.crop_wheat_share, a.crop_rice_share, a.crop_cotton_share,
               a.crop_soybean_share, a.crop_sugarcane_share, a.crop_maize_share,
               a.temp_is_allocated, a.provenance,
               t.potential_units_yr AS demand_units
        FROM agroclimate a
        LEFT JOIN district_totals t USING (district_id)
        ORDER BY a.state, a.district
    """)
    return {"provenance": "real", "temp_note": "station data covers 31 districts; "
            "the rest are filled from the nearest station (allocated)", "districts": rows}


# ---------------------------------------------------------------- review: operational detail

_OPS_METRICS = {"sonalika_sales_units", "tiv", "sonalika_share", "potential_units_yr",
                "activities_yr", "enquiries_yr", "deliveries_yr", "product_fit"}


@app.get("/api/review/micromarkets")
def review_micromarkets(district: str | None = None, archetype_id: str | None = None,
                        metric: str = "sonalika_sales_units", limit: int = 700):
    metric = metric if metric in _OPS_METRICS else "sonalika_sales_units"
    df = con().execute("SELECT * FROM micromarket_ops").fetchdf()
    if district:
        df = df[df["district_id"] == district]
    if archetype_id:
        df = df[df["archetype_id"] == archetype_id]
    df = df.sort_values(metric, ascending=False).head(limit)
    return {"metric": metric, "micromarkets": clean(df.to_dict("records"))}


@app.get("/api/review/micromarket/{mm_id}")
def review_micromarket(mm_id: str):
    df = con().execute("SELECT * FROM micromarket_ops WHERE micro_market_id = ?",
                       [mm_id]).fetchdf()
    return {"micromarket": clean(df.to_dict("records"))[0] if len(df) else None}


@app.get("/api/review/coverage")
def review_coverage(product: str = "implements", type: str = "sales"):
    """Network coverage per archetype: Sonalika vs rival OEMs, sales (real dealers) and
    service (dummy). pct_covered = share of an archetype's micro-markets whose district has
    at least one Sonalika dealer (real for implements)."""
    net = con().execute(
        "SELECT district_id, own_dealers FROM dealer_network WHERE product_line = ?",
        [product]).fetchdf()
    covered = set(net.loc[net["own_dealers"] > 0, "district_id"])
    mmd = con().execute("SELECT archetype_id, district_id FROM micromarket_ops").fetchdf()
    mmd["cov"] = mmd["district_id"].isin(covered)
    pct = mmd.groupby("archetype_id")["cov"].mean().to_dict()

    arch = con().execute(
        """SELECT archetype_id, base_name, hp_belt, subzone_id, subzone, n_micromarkets,
                  diagnosis, sales_coverage, service_coverage, avg_sonalika_share
           FROM archetype_ops""").fetchdf()
    arch["coverage"] = arch["service_coverage" if type == "service" else "sales_coverage"]
    arch["pct_covered"] = arch["archetype_id"].map(pct).fillna(0.0)
    arch = arch.sort_values("coverage")           # worst-covered first = the gap

    def _sum(like_not: bool):
        op = "NOT LIKE" if like_not else "LIKE"
        r = q(f"SELECT sum(dealers) d FROM dealer_by_oem WHERE product_line = ? "
              f"AND lower(oem) {op} '%sonalika%'", [product])
        return int(r[0]["d"] or 0)

    oems = q("""SELECT oem, sum(dealers) AS dealers, count(DISTINCT district_id) AS districts
                FROM dealer_by_oem WHERE product_line = ? AND lower(oem) NOT LIKE '%sonalika%'
                GROUP BY oem ORDER BY dealers DESC LIMIT 6""", [product])
    return {"product_line": product, "type": type,
            "provenance": "real" if type == "sales" else "simulated",
            "own_dealers": _sum(False), "competitor_dealers": _sum(True),
            "archetypes": clean(arch.to_dict("records")), "oems": oems}


@app.get("/api/review/archetypes")
def review_archetypes():
    rows = q("SELECT * FROM archetype_ops ORDER BY potential_units_yr DESC")
    diag = q("""SELECT diagnosis, count(*) AS archetypes, sum(n_micromarkets) AS micromarkets,
                       sum(potential_units_yr) AS demand, sum(sonalika_sales_units) AS sales
                FROM archetype_ops GROUP BY diagnosis ORDER BY demand DESC""")
    tot = q("""SELECT sum(sonalika_sales_units) AS sales, sum(activities_yr) AS activities,
                      sum(enquiries_yr) AS enquiries, sum(deliveries_yr) AS deliveries,
                      sum(potential_units_yr) AS demand FROM archetype_ops""")[0]
    return {"provenance": "simulated", "archetypes": rows,
            "diagnosis": diag, "totals": tot}


# ---------------------------------------------------------------- dealer network (REAL)

@app.get("/api/network")
def network(product: str = "implements"):
    """Real dealer-network coverage per district: own (Sonalika) vs competitor.

    Every district is returned (left-joined onto demand) so white-space -- real demand
    with no Sonalika dealer -- is visible, not just districts that already have one.
    Demand is the implement-demand mart; it is only meaningful for the implements line,
    so it is nulled for tractors (whose demand awaits ITL TIV data).
    """
    rows = q("""
        SELECT d.district_id, d.district, d.state, d.zone,
               COALESCE(n.own_dealers, 0)        AS own_dealers,
               COALESCE(n.competitor_dealers, 0) AS competitor_dealers,
               COALESCE(n.total_dealers, 0)      AS total_dealers,
               COALESCE(n.n_oems, 0)             AS n_oems,
               t.potential_units_yr             AS demand_units
        FROM district_totals t
        JOIN geo_districts d USING (district_id)
        LEFT JOIN dealer_network n
               ON n.district_id = d.district_id AND n.product_line = ?
        ORDER BY t.potential_units_yr DESC
    """, [product])
    is_impl = product == "implements"
    # Which states are actually present in the dealer dataset for this line? The
    # implements DB has no Punjab rows at all, so Punjab is "no data", not white-space --
    # calling an absent state white-space would imply a gap we cannot actually see.
    covered_states = {r["state"] for r in rows if r["total_dealers"] > 0}
    for r in rows:
        if not is_impl:
            r["demand_units"] = None
        r["has_dealer_data"] = r["state"] in covered_states
        if r["own_dealers"] > 0:
            r["status"] = "covered"
        elif not r["has_dealer_data"]:
            r["status"] = "no_data"
        elif is_impl and (r["demand_units"] or 0) > 0:
            r["status"] = "whitespace"
        else:
            r["status"] = "no_own"
        r["whitespace"] = r["status"] == "whitespace"
    return {"product_line": product, "provenance": "real",
            "covered_states": sorted(covered_states), "districts": rows}


@app.get("/api/network/summary")
def network_summary(product: str = "implements"):
    res = network(product)
    d = res["districts"]
    own = sum(r["own_dealers"] for r in d)
    comp = sum(r["competitor_dealers"] for r in d)
    with_data = [r for r in d if r["has_dealer_data"]]
    covered = sum(1 for r in d if r["own_dealers"] > 0)
    ws = [r for r in d if r["whitespace"]]
    return {
        "product_line": product, "provenance": "real",
        "covered_states": res["covered_states"],
        "own_dealers": own, "competitor_dealers": comp,
        "districts_total": len(d), "districts_with_data": len(with_data),
        "districts_covered": covered,
        "districts_no_data": len(d) - len(with_data),
        "whitespace_districts": len(ws),
        "whitespace_demand": sum(r["demand_units"] or 0 for r in ws),
        "top_competitors": q("""
            SELECT oem, sum(dealers) AS dealers, count(DISTINCT district_id) AS districts
            FROM dealer_by_oem WHERE product_line = ? AND lower(oem) NOT LIKE '%sonalika%'
            GROUP BY oem ORDER BY dealers DESC LIMIT 6
        """, [product]),
    }


# ---------------------------------------------------------------- map shapes

SHAPES = MARTS / "shapes"


def _slug(x: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in x)


@app.get("/api/shapes/{level}")
def shapes(level: str, parent: str | None = None, sku: str | None = None,
           category: str | None = None, month: int | None = None):
    """Map geometry plus the value to colour it by, in one call.

    Geometry and values are joined server-side so the map cannot render a shape whose
    number came from a different filter -- the commonest way a choropleth ends up
    quietly lying.
    """
    if level == "india":
        path = SHAPES / "india.json"
    elif level == "state":
        if not parent:
            raise HTTPException(400, "state shapes need ?parent=<state name>")
        path = SHAPES / f"state_{_slug(parent)}.json"
    elif level == "district":
        if not parent:
            raise HTTPException(400, "district shapes need ?parent=<district_id>")
        path = SHAPES / f"district_{parent}.json"
    else:
        raise HTTPException(400, "level must be india|state|district")

    if not path.exists():
        raise HTTPException(404, f"no geometry for {level} {parent or ''}".strip())

    import json as _json
    shape = _json.loads(path.read_text())

    values = _shape_values(level, parent, sku, category, month)
    for f in shape.get("features", []):
        v = values.get(f["id"], {})
        f.update({"units": v.get("units"), "headroom": v.get("headroom"),
                  "top_sku": v.get("top_sku")})
    shape["season_factor"] = _season_factor(sku, category, month)
    shape["scope"] = {"sku": sku, "category": category, "month": month}
    return clean(shape)


def _shape_values(level, parent, sku, category, month) -> dict:
    """Metric per shape id, honouring the active product filter."""
    season = _season_factor(sku, category, month)
    where, params = [], []
    if sku:
        where.append("s.sku_id = ?"); params.append(sku)
    elif category:
        where.append("s.category = ?"); params.append(category)

    if level == "india":
        key, join = "v.state", "JOIN geo_villages v USING (village_id)"
    elif level == "state":
        key, join = "v.district_id", "JOIN geo_villages v USING (village_id)"
        where.append("v.state = ?"); params.append(parent)
    else:
        key, join = "v.block_id", "JOIN geo_villages v USING (village_id)"
        where.append("v.district_id = ?"); params.append(parent)

    w = (" WHERE " + " AND ".join(where)) if where else ""
    rows = q(f"""SELECT {key} AS "id", sum(s.potential_units_yr) AS "units",
                        sum(s.headroom) AS headroom,
                        arg_max(s.sku_id, s.potential_units_yr) AS top_sku
                 FROM village_sku s {join}{w} GROUP BY 1""", params)
    return {r["id"]: {"units": (r["units"] or 0) * season,
                      "headroom": r["headroom"], "top_sku": r["top_sku"]} for r in rows}


@app.get("/api/shapes/villages/{block_id}")
def shape_villages(block_id: str, sku: str | None = None,
                   category: str | None = None, month: int | None = None):
    """Villages of a block as points -- the last drill level."""
    season = _season_factor(sku, category, month)
    where, params = ["i.block_id = ?"], [block_id]
    sel = "i.potential_units_yr"
    join = ""
    if sku:
        join = "JOIN village_sku s USING (village_id)"
        where.append("s.sku_id = ?"); params.append(sku)
        sel = "s.potential_units_yr"
    elif category:
        join = "JOIN village_sku s USING (village_id)"
        where.append("s.category = ?"); params.append(category)
        sel = "sum(s.potential_units_yr)"

    grp = " GROUP BY 1,2,3,4,5,6,7,8" if category and not sku else ""
    rows = q(f"""SELECT i.village_id AS "id", i.village AS "name", i.lon, i.lat,
                        i.action_segment, i.archetype, i.top_sku,
                        round(i.opportunity_score) AS opportunity,
                        {sel} AS "units"
                 FROM village_insights i {join}
                 WHERE {' AND '.join(where)}{grp}
                 ORDER BY "units" DESC LIMIT 4000""", params)
    for r in rows:
        r["units"] = (r["units"] or 0) * season
    return clean({"level": "village", "block_id": block_id, "features": rows,
                  "season_factor": season})


# ---------------------------------------------------------------- competition

@app.get("/api/compete/summary")
def compete_summary(state: str | None = None, category: str | None = None):
    """Where Sonalika stands, what is winnable, and what is exposed."""
    where, p = ["1=1"], []
    if state:
        where.append("v.state = ?"); p.append(state)
    if category:
        where.append("c.category = ?"); p.append(category)
    w = " AND ".join(where)
    tot = q(f"""SELECT sum(c.market_units) AS market, sum(c.sonalika_units) AS sonalika,
                       sum(c.competitor_units) AS competitor,
                       sum(c.winnable_units) AS winnable,
                       sum(c.at_risk_units) AS at_risk,
                       avg(c.hhi) AS hhi
                FROM cannibal_ext c JOIN geo_villages v USING (village_id)
                WHERE {w}""", p)[0]
    by_status = q(f"""SELECT c.status, count(*) AS rows_n,
                             sum(c.market_units) AS market,
                             sum(c.sonalika_units) AS sonalika,
                             sum(c.winnable_units) AS winnable
                      FROM cannibal_ext c JOIN geo_villages v USING (village_id)
                      WHERE {w} GROUP BY 1 ORDER BY market DESC""", p)
    rivals = q(f"""SELECT c.closest_rival AS rival, count(*) AS contests,
                          sum(c.competitor_units) AS their_units,
                          sum(c.winnable_units) AS winnable_from_them,
                          sum(c.at_risk_units) AS we_could_lose
                   FROM cannibal_ext c JOIN geo_villages v USING (village_id)
                   WHERE {w} GROUP BY 1 ORDER BY their_units DESC""", p)
    share = (tot["sonalika"] / tot["market"] * 100) if tot["market"] else 0
    return clean({"totals": {**tot, "sonalika_share_pct": round(share, 2)},
                  "by_status": by_status, "rivals": rivals})


@app.get("/api/compete/players")
def compete_players(district_id: str | None = None, category: str | None = None):
    where, p = ["1=1"], []
    if district_id:
        where.append("district_id = ?"); p.append(district_id)
    if category:
        where.append("category = ?"); p.append(category)
    return q(f"""SELECT player, avg("share") AS "share", avg(price_index) AS price_index,
                        avg(reach_km) AS reach_km
                 FROM player_shares WHERE {' AND '.join(where)}
                 GROUP BY 1 ORDER BY "share" DESC""", p)


@app.get("/api/compete/headtohead")
def compete_headtohead(rival: str, state: str | None = None):
    """Where one rival costs us most, and where they are beatable."""
    where, p = ["c.closest_rival = ?"], [rival]
    if state:
        where.append("v.state = ?"); p.append(state)
    w = " AND ".join(where)
    return clean({
        "rival": rival,
        "by_category": q(f"""SELECT c.category, sum(c.market_units) AS market,
                                    sum(c.sonalika_units) AS sonalika,
                                    sum(c.competitor_units) AS theirs,
                                    sum(c.winnable_units) AS winnable,
                                    avg(c.closeness) AS closeness
                             FROM cannibal_ext c JOIN geo_villages v USING (village_id)
                             WHERE {w} GROUP BY 1 ORDER BY theirs DESC""", p),
        "top_districts": q(f"""SELECT d.district, d.state,
                                      sum(c.competitor_units) AS theirs,
                                      sum(c.winnable_units) AS winnable
                               FROM cannibal_ext c JOIN geo_villages v USING (village_id)
                               JOIN geo_districts d ON d.district_id = c.district_id
                               WHERE {w} GROUP BY 1,2 ORDER BY winnable DESC LIMIT 12""", p),
    })


@app.get("/api/compete/cannibalisation")
def compete_cannibalisation():
    """Internal overlap: Sonalika SKUs competing with each other."""
    return clean({
        "pairs": q("""SELECT * FROM cannibal_int ORDER BY displaced_units DESC"""),
        "by_sku": q("""SELECT c.*, r.name FROM cannibal_int_sku c
                       JOIN sku_ref r USING (sku_id)
                       ORDER BY c.displaced_units DESC"""),
        "all_overlaps": q("""SELECT * FROM sku_overlap ORDER BY overlap DESC"""),
    })


class CompeteScenario(BaseModel):
    rival: str
    dealer_change_pct: float = 0.0   # rival expands (+) or retreats (-) its network
    price_change_pct: float = 0.0    # rival cuts (-) or raises (+) price
    state: str | None = None
    category: str | None = None


@app.post("/api/compete/scenario")
def compete_scenario(b: CompeteScenario):
    """What a competitor move costs or gains Sonalika.

    Shares come from a choice model, so moving one brand's price or reach necessarily
    moves everyone's share -- that is what makes this answerable at all. A static
    share table could only be asserted at, not computed from.
    """
    cfg = Config.sim()["competition"]
    can = Config.sim()["cannibalisation"]
    if b.rival not in cfg["players"] or b.rival == "Sonalika":
        raise HTTPException(400, f"rival must be one of "
                                 f"{[p for p in cfg['players'] if p != 'Sonalika']}")

    where, p = ["1=1"], []
    if b.state:
        where.append("v.state = ?"); p.append(b.state)
    if b.category:
        where.append("c.category = ?"); p.append(b.category)
    w = " AND ".join(where)
    rows = con().execute(f"""
        SELECT c.category, v.state, c.market_units, c.sonalika_share, c.rival_share,
               c.closest_rival, c.sonalika_units
        FROM cannibal_ext c JOIN geo_villages v USING (village_id)
        WHERE {w} AND c.closest_rival = ?""", p + [b.rival]).fetchdf()
    if rows.empty:
        raise HTTPException(404, f"no contests against {b.rival} under those filters")

    # Utility shift for the rival, then re-normalise the two-way contest.
    du = (can["price_sensitivity"] * (b.price_change_pct / 100.0)
          + can["distance_sensitivity"] * (-cfg["reach_km"][b.rival]
                                           * b.dealer_change_pct / 100.0))
    son, riv = rows["sonalika_share"].to_numpy(), rows["rival_share"].to_numpy()
    riv_new = riv * np.exp(du)
    denom_old, denom_new = son + riv, son + riv_new
    son_new = son * denom_old / np.maximum(denom_new, 1e-12)
    delta = (son_new - son) / np.maximum(son, 1e-12)
    units_before = rows["sonalika_units"].to_numpy()
    units_after = units_before * (1 + delta)

    rows["units_before"], rows["units_after"] = units_before, units_after
    by_cat = rows.groupby("category").agg(
        before=("units_before", "sum"), after=("units_after", "sum")).reset_index()
    by_cat["delta"] = by_cat["after"] - by_cat["before"]
    by_cat["delta_pct"] = by_cat["delta"] / by_cat["before"].clip(lower=1e-9) * 100

    tb, ta = float(units_before.sum()), float(units_after.sum())
    return clean({
        "rival": b.rival,
        "move": {"dealer_change_pct": b.dealer_change_pct,
                 "price_change_pct": b.price_change_pct},
        "total": {"units_before": round(tb, 1), "units_after": round(ta, 1),
                  "delta_units": round(ta - tb, 1),
                  "delta_pct": round((ta / tb - 1) * 100, 2) if tb else 0.0},
        "by_category": by_cat.to_dict("records"),
        "contests_affected": int(len(rows)),
    })


@app.get("/api/sku/images")
def sku_images():
    """SKU photography with licence and author, which most licences require us to show."""
    import json as _json
    path = ROOT_CFG / "sku_images.json"
    return _json.loads(path.read_text()) if path.exists() else {}


@app.get("/api/health")
def health():
    return JSONResponse({"status": "ok",
                         "villages": con().execute(
                             "SELECT count(*) FROM village_totals").fetchone()[0]})


# ---------------------------------------------------------------- narratives

@app.get("/api/narrative/{view}")
def narrative_view(view: str,
                   sku: str | None = None, category: str | None = None,
                   month: int | None = None, level: str | None = None,
                   id: str | None = None, name: str | None = None,
                   village_id: str | None = None, district_id: str | None = None):
    """Plain-English briefing for a view, grounded in a computed fact pack."""
    try:
        if view == "executive":
            facts, tmpl = narrative.facts_executive(q)
        elif view == "overview":
            facts, tmpl = narrative.facts_overview(q, sku, category, month)
        elif view == "geography":
            if not (level and id):
                raise HTTPException(400, "geography narrative needs level and id")
            facts, tmpl = narrative.facts_geography(q, level, id, name or id)
        elif view == "village":
            facts, tmpl = narrative.facts_village(q, village_id)
        elif view == "ucm":
            nm = q("SELECT district FROM geo_districts WHERE district_id = ?", [district_id])
            facts, tmpl = narrative.facts_ucm(q, district_id,
                                              nm[0]["district"] if nm else district_id)
        elif view == "clusters":
            facts, tmpl = narrative.facts_clusters(q)
        elif view == "sku":
            facts, tmpl = narrative.facts_sku(q, category)
        else:
            raise HTTPException(404, f"no narrative for view '{view}'")
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    return clean(narrative.narrate(view, facts, tmpl))


@app.post("/api/narrative/scenario")
def narrative_scenario(body: dict):
    facts, tmpl = narrative.facts_scenario(q, body["result"], body.get("shocks", {}))
    return clean(narrative.narrate("scenario", facts, tmpl))


# ---------------------------------------------------------------- chat

class ChatIn(BaseModel):
    question: str
    session_id: str | None = None
    context: dict | None = None


@app.post("/api/chat")
def chat(body: ChatIn):
    if not body.question.strip():
        raise HTTPException(400, "empty question")
    return clean(chat_mod.answer(q, body.question.strip(),
                                 session_id=body.session_id, context=body.context))


@app.get("/api/chat/suggestions")
def chat_suggestions():
    return {"suggestions": chat_mod.SUGGESTIONS, "ai_enabled": llm.available(),
            "provider": llm.provider_name()}


@app.get("/api/chat/session")
def chat_session(session_id: str | None = None):
    """Transcript and remembered facts. Called on open so a reload resumes mid-thread."""
    from api.memory import store
    return clean(store.snapshot(session_id))


@app.post("/api/chat/session/new")
def chat_session_new(session_id: str | None = None):
    """Start a fresh conversation but keep what we know about the user.

    Clearing the thread and forgetting the person are different actions, so they get
    different buttons -- wiping someone's territory because they wanted a clean thread
    would be a poor surprise.
    """
    from api.memory import store
    if session_id:
        store.clear_turns(session_id)
        return clean(store.snapshot(session_id))
    return clean(store.snapshot(None))


@app.delete("/api/chat/memory")
def chat_forget(session_id: str, index: int | None = None):
    """Forget one remembered fact, or all of them."""
    from api.memory import store
    store.forget(session_id, index)
    return clean(store.snapshot(session_id))


@app.delete("/api/chat/session")
def chat_drop(session_id: str):
    """Delete the session entirely -- transcript and facts."""
    from api.memory import store
    store.drop(session_id)
    return {"status": "deleted", "session_id": session_id}


# ---------------------------------------------------------------- village level

@app.get("/api/villages")
def villages(state: str | None = None, district: str | None = None,
             block_id: str | None = None, action: str | None = None,
             archetype: str | None = None, micro_id: str | None = None,
             sku: str | None = None, min_headroom: float | None = None,
             max_dealer_km: float | None = None, sort: str = "opportunity_score",
             limit: int = 200):
    """Village finder -- the operational list a field team actually works from."""
    allowed_sort = {"opportunity_score", "potential_units_yr", "headroom",
                    "attach_gap_micro", "dealer_distance_km", "tractors"}
    if sort not in allowed_sort:
        raise HTTPException(400, f"sort must be one of {sorted(allowed_sort)}")
    where, p = ["1=1"], []
    for col, val in [("state", state), ("district", district), ("block_id", block_id),
                     ("action_segment", action), ("archetype", archetype),
                     ("micro_id", micro_id)]:
        if val:
            where.append(f"i.{col} = ?"); p.append(val)
    if min_headroom is not None:
        where.append("i.headroom >= ?"); p.append(min_headroom)
    if max_dealer_km is not None:
        where.append("i.dealer_distance_km <= ?"); p.append(max_dealer_km)

    if sku:
        return q(f"""SELECT i.*, s.potential_units_yr AS sku_units,
                            s.penetration AS sku_penetration, s.headroom AS sku_headroom
                     FROM village_insights i
                     JOIN village_sku s USING (village_id)
                     WHERE {' AND '.join(where)} AND s.sku_id = ?
                     ORDER BY s.potential_units_yr DESC LIMIT {int(min(limit, 1000))}""",
                 p + [sku])
    return q(f"""SELECT i.* FROM village_insights i WHERE {' AND '.join(where)}
                 ORDER BY i.{sort} DESC LIMIT {int(min(limit, 1000))}""", p)


@app.get("/api/villages/summary")
def villages_summary(state: str | None = None, district: str | None = None):
    where, p = ["1=1"], []
    if state:
        where.append("state = ?"); p.append(state)
    if district:
        where.append("district = ?"); p.append(district)
    w = " AND ".join(where)
    return {
        "actions": q(f"""SELECT action_segment, any_value(action_rationale) rationale,
                                count(*) villages, sum(potential_units_yr) "units",
                                sum(headroom) headroom,
                                avg(dealer_distance_km) avg_km,
                                avg(attach_rate) "attach"
                         FROM village_insights WHERE {w}
                         GROUP BY 1 ORDER BY units DESC""", p),
        "archetypes": q(f"""SELECT archetype, count(*) villages,
                                   sum(potential_units_yr) "units", sum(headroom) headroom,
                                   avg(attach_rate) "attach", avg(opportunity_score) opp
                            FROM village_insights WHERE {w}
                            GROUP BY 1 ORDER BY units DESC""", p),
        "micro": q(f"""SELECT micro_id, archetype, count(*) villages,
                              sum(potential_units_yr) "units", sum(headroom) headroom,
                              avg(opportunity_score) opp, avg(dealer_distance_km) avg_km,
                              avg(attach_rate) "attach", mode(action_segment) main_action,
                              mode(top_sku) top_sku
                       FROM village_insights WHERE {w}
                       GROUP BY 1,2 ORDER BY opp DESC""", p),
    }


@app.get("/api/village/{village_id}/insight")
def village_insight(village_id: str):
    r = q("SELECT * FROM village_insights WHERE village_id = ?", [village_id])
    if not r:
        raise HTTPException(404, "village not found")
    v = r[0]
    return {
        "insight": v,
        "peers": q("""SELECT village, district, round(opportunity_score) opportunity,
                             round(attach_rate,2) attach_rate, round(headroom,1) headroom,
                             round(dealer_distance_km,1) km
                      FROM village_insights WHERE micro_id = ? AND village_id != ?
                      ORDER BY abs(opportunity_score - ?) LIMIT 8""",
                   [v["micro_id"], village_id, v["opportunity_score"]]),
        "top_skus": q("""SELECT r.name, s.sku_id, s.potential_units_yr units,
                                s.penetration, s.headroom, s.propensity
                         FROM village_sku s JOIN sku_ref r USING (sku_id)
                         WHERE s.village_id = ? ORDER BY units DESC LIMIT 8""", [village_id]),
    }


@app.get("/api/kpis")
def kpis(state: str | None = None):
    """The executive KPI set. One call, everything the summary tiles need."""
    where, p = ([], [])
    if state:
        where.append("i.state = ?"); p.append(state)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    core = q(f"""SELECT count(*) villages, sum(potential_units_yr) demand_units,
                        sum(potential_value_inr) demand_value,
                        sum(headroom) unserved_units, sum(addressable) addressable,
                        sum(owned) "owned", avg(attach_rate) attach_rate,
                        avg(dealer_distance_km) avg_dealer_km,
                        sum(tractors) tractors
                 FROM village_insights i{w}""", p)[0]
    acts = q(f"""SELECT action_segment, count(*) villages, sum(potential_units_yr) "units",
                        sum(headroom) headroom
                 FROM village_insights i{w} GROUP BY 1""", p)
    repl = q(f"""SELECT sum(s.new_units_yr) new_units, sum(s.replacement_units_yr) repl_units
                 FROM village_sku s JOIN village_insights i USING (village_id){w}""", p)[0]
    conv = next((a for a in acts if a["action_segment"] == "Convert now"), {})
    acc = next((a for a in acts if a["action_segment"] == "Build access"), {})
    pen = core["owned"] / core["addressable"] if core["addressable"] else 0
    return clean({
        "coverage": {"villages": core["villages"], "tractors": round(core["tractors"])},
        "demand": {"units_per_year": round(core["demand_units"]),
                   "value_crore": round(core["demand_value"] / 1e7, 1),
                   "new_units": round(repl["new_units"]),
                   "replacement_units": round(repl["repl_units"]),
                   "replacement_share_pct": round(
                       repl["repl_units"] / (repl["new_units"] + repl["repl_units"]) * 100, 1)
                   if (repl["new_units"] + repl["repl_units"]) else 0},
        "penetration": {"pct": round(pen * 100, 1),
                        "unserved_units": round(core["unserved_units"]),
                        "implements_per_tractor": round(core["attach_rate"], 2)},
        "coverage_quality": {"avg_km_to_dealer": round(core["avg_dealer_km"], 1)},
        "priority": {
            "convert_now_villages": conv.get("villages", 0),
            "convert_now_units": round(conv.get("units", 0) or 0),
            "build_access_villages": acc.get("villages", 0),
            "build_access_units": round(acc.get("units", 0) or 0),
        },
        "actions": acts,
    })
