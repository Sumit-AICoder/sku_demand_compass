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
        "ucm_forecast": MARTS / "ucm_forecast.parquet",
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
        "aesr_subzones": MARTS / "aesr_subzones.parquet",
        "district_aesr": MARTS / "district_aesr.parquet",
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
    # Marts that now carry both product lines. Each is registered TWICE: `<name>` filtered to
    # implements, and `<name>_pl` with both. That way the ~30 queries written before the
    # second line existed keep returning exactly what they always did, and a screen opts in
    # to the toggle by moving to the `_pl` view and filtering explicitly. The alternative --
    # letting every existing query see two rows per village -- double-counts silently, which
    # is the one failure mode that would not announce itself.
    SPLIT = {"village_totals", "block_totals", "district_totals", "state_sku",
             "district_sku", "block_sku", "village_insights"}

    for name, path in views.items():
        if not path.exists():
            continue
        src = f"read_parquet('{path}')"
        if name in SPLIT:
            c.execute(f"CREATE VIEW {name}_pl AS SELECT * FROM {src}")
            c.execute(f"CREATE VIEW {name} AS SELECT * FROM {src} "
                      f"WHERE product_line = 'implements'")
        else:
            c.execute(f"CREATE VIEW {name} AS SELECT * FROM {src}")
    return c


# ---------------------------------------------------------------- product line

def _line(product: str | None) -> str:
    """Normalise the product line a request is scoped to.

    A plain default rather than a FastAPI `Depends`: every one of these endpoints is also
    called directly as a function by the test suite, and a Depends default arrives there as
    the sentinel object instead of a string.

    Marts that carry both lines are registered twice (see `_root_con`): the plain view is
    implements, `<name>_pl` holds both. An endpoint becomes line-aware by taking `product`
    and reading the `_pl` view with an explicit filter -- so a screen that has not been
    converted yet cannot silently start summing tractors into implements.
    """
    return product if product in ("implements", "tractors") else "implements"


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


def fmt_units(n: float) -> str:
    """Thousands-separated integer for prose the API writes (play descriptions)."""
    return f"{round(float(n)):,}"


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
_TAXONOMY_PATH = MARTS / "taxonomy.json"


def _load_taxonomy() -> dict:
    """The taxonomy in force: the user's edited copy if there is one, else the shipped
    default from config/taxonomy.yaml."""
    import json as _j
    from pipeline.cluster import taxonomy as tx
    if _TAXONOMY_PATH.exists():
        try:
            return _j.loads(_TAXONOMY_PATH.read_text())
        except Exception:                                      # noqa: BLE001
            LOG_BAD_TAXONOMY.append(True)                      # fall back rather than 500
    return tx.load()


def _save_taxonomy(tax: dict) -> None:
    import json as _j
    _TAXONOMY_PATH.write_text(_j.dumps(tax, indent=1))
    _current_mm.cache_clear()
    _current_grain_cached.cache_clear()
    _current_ops_cached.cache_clear()
    _archetype_players_cached.cache_clear()
    _approval_cached.cache_clear()
    # Keyed on nothing, so it survives a re-cluster unless said so: the Define archetype
    # table would keep naming rivals for archetype ids the edit deleted.
    _top_branded_rival.cache_clear()
    _district_rivals.cache_clear()


LOG_BAD_TAXONOMY: list[bool] = []


@lru_cache(maxsize=4)
def _current_mm_cached(stamp: str) -> pd.DataFrame:
    """Micro-markets labelled by the taxonomy in force.

    Re-labelling is the whole re-cluster path: `assign()` recomputes TIV tier, HP belt,
    crop label, zone and archetype for all 23,389 rows in about a second, so editing a
    category on the Configure screen updates every archetype-grain view without touching
    the pipeline. Unlike the rule mechanism it replaces, the archetype ids it produces are
    real category codes -- so a customised taxonomy still joins in Review, Plan and Act
    instead of vanishing behind a `custom-` prefix.
    """
    from pipeline.cluster import taxonomy as tx
    df = con().execute("SELECT * FROM micromarkets").fetchdf()
    return tx.assign(df, _load_taxonomy())


def _current_mm() -> pd.DataFrame:
    """The micro-market table every Define view reads, so a reconfigure shows on all tabs."""
    return _current_mm_cached(_stamp()).copy()


_current_mm.cache_clear = _current_mm_cached.cache_clear      # type: ignore[attr-defined]


@lru_cache(maxsize=4)
def _current_grain_cached(stamp: str, line: str = "implements") -> pd.DataFrame:
    """micromarket_ops for one product line, with the taxonomy in force applied.

    Keyed on the line as well as the taxonomy: without it the first request's line would be
    cached and served to the other one, which is the kind of wrong answer that looks
    completely plausible on screen.
    """
    grain = con().execute("SELECT * FROM micromarket_ops WHERE product_line = ?",
                          [line]).fetchdf()
    if stamp == "shipped":
        return grain
    mm = _current_mm().set_index("micro_market_id")
    for col in ("archetype_id", "archetype", "base_name", "hp_belt", "tiv_tier", "subzone_id"):
        grain[col] = grain["micro_market_id"].map(mm[col])
    return grain.dropna(subset=["archetype_id"])


@lru_cache(maxsize=4)
def _current_ops_cached(stamp: str, line: str = "implements") -> pd.DataFrame:
    """Archetype-grain operations under the taxonomy in force.

    On the shipped taxonomy this is the mart, read straight through. Once Configure edits it,
    micro-markets move between archetypes, so the rollup is recomputed from micro-market grain
    with the same function the pipeline uses -- otherwise Define would show the client's 43
    archetypes while Plan, Review and Act kept serving the shipped 46.

    What it cannot recompute is anything *fitted* per archetype: the UCM panels and the
    cluster profiles stay keyed on the shipped ids until the pipeline is re-run, so a
    split-out zone shows its numbers with those two fields blank rather than wrong.
    """
    if stamp == "shipped":
        return con().execute("SELECT * FROM archetype_ops WHERE product_line = ?",
                             [line]).fetchdf()
    from pipeline.simulate.operations import rollup
    return rollup(_current_grain_cached(stamp, line)).assign(product_line=line)


def _stamp() -> str:
    return str(_TAXONOMY_PATH.stat().st_mtime) if _TAXONOMY_PATH.exists() else "shipped"


def _current_ops(line: str = "implements") -> pd.DataFrame:
    return _current_ops_cached(_stamp(), line).copy()


def _current_grain(line: str = "implements") -> pd.DataFrame:
    return _current_grain_cached(_stamp(), line).copy()


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
            # the true modal crop of the member micro-markets -- the zone's crop_label names
            # the archetype, but this is the crop actually grown in most of it
            "dominant_crop": (g["dominant_crop"].mode().iloc[0]
                              if "dominant_crop" in g and len(g["dominant_crop"].mode()) else ""),
            "subzones": ", ".join(sorted(g["subzone_id"].dropna().unique())),
        })
    # Ranked by fleet: Define describes the market, so it sorts by the market's own size.
    # Demand ranking belongs to Plan, which is where the choice of where to sell is made.
    rows.sort(key=lambda r: -r["tiv"])
    return rows


@lru_cache(maxsize=1)
def _top_branded_rival() -> dict[str, dict]:
    """The strongest branded competitor in each archetype.

    Not the leader: the unbranded "Local" segment leads all of them, so a leader column
    would read the same on every row. Excluding Local and ourselves leaves the rival a
    territory manager would actually name.
    """
    pl = _archetype_players()
    if pl.empty:
        return {}
    branded = pl[~pl["player"].isin(["Local", "Sonalika"])]
    top = (branded.sort_values("share", ascending=False)
                  .drop_duplicates("archetype_id").set_index("archetype_id"))
    return {k: {"rival": v["player"], "rival_share": float(v["share"])}
            for k, v in top.to_dict("index").items()}


@app.get("/api/define/profile")
def define_profile(level: str, id: str):
    """One panel for either grain: what kind of place this is, and who serves it.

    The two Define tabs used to answer this separately -- a dot map for micro-markets, a
    table for districts -- so the same question had two homes and neither had a map you
    could zoom. This is the single payload behind the merged view.

    Honest about grain: rainfall, temperature and the crop mix are district measurements,
    so a micro-market inherits its district's values and the response says so. Dealer counts
    exist only at district grain (the dealer file is real but district-geocoded), so a
    micro-market gets distance-to-dealer instead of an invented count.
    """
    if level not in ("district", "micromarket"):
        raise HTTPException(400, "level must be district or micromarket")

    mm = _current_mm()
    if level == "micromarket":
        row = mm[mm["micro_market_id"] == id]
        if row.empty:
            raise HTTPException(404, "micro-market not found")
        r = row.iloc[0]
        district_id, name = r["district_id"], f"{r['district']} · {id}"
        scope = {"micromarkets": 1, "villages": int(r["n_villages"]),
                 "sonalika_share": float(r["sonalika_share"]),
                 "tiv": round(float(r["tiv"])), "mean_hp": round(float(r["mean_hp"]), 1),
                 "hp_belt": r["hp_belt"], "tiv_tier": r["tiv_tier"],
                 "dominant_crop": r["dominant_crop"],
                 "hp_mix": {k: float(r[k]) for k in
                            ("hp_20_35", "hp_35_45", "hp_45_60", "hp_60_plus") if k in r},
                 "archetype": r["archetype"], "archetype_id": r["archetype_id"],
                 "irrigation": float(r["irrigation_reliability"]),
                 "dealer_km": None, "dealer_accessibility": float(r["dealer_accessibility"]),
                 "lon": float(r["lon"]), "lat": float(r["lat"])}
        acc = con().execute("""SELECT avg(service_distance_km) km FROM micromarket_ops
                               WHERE micro_market_id = ?""", [id]).fetchdf()
        if len(acc) and pd.notna(acc["km"].iloc[0]):
            scope["dealer_km"] = round(float(acc["km"].iloc[0]), 1)
    else:
        g = mm[mm["district_id"] == id]
        if g.empty:
            raise HTTPException(404, "district not found")
        district_id = id
        name = g["district"].iloc[0]
        tiv = float(g["tiv"].sum())
        scope = {"micromarkets": int(len(g)), "villages": int(g["n_villages"].sum()),
                 "tiv": round(tiv), "mean_hp": round(float((g["mean_hp"] * g["tiv"]).sum()
                                                           / max(tiv, 1)), 1),
                 "hp_belt": g["hp_belt"].mode().iloc[0] if len(g["hp_belt"].mode()) else "",
                 "tiv_tier": g["tiv_tier"].mode().iloc[0] if len(g["tiv_tier"].mode()) else "",
                 "dominant_crop": (g["dominant_crop"].mode().iloc[0]
                                   if len(g["dominant_crop"].mode()) else ""),
                 "hp_mix": {k: float(g[k].sum()) for k in
                            ("hp_20_35", "hp_35_45", "hp_45_60", "hp_60_plus") if k in g},
                 "archetype": None, "archetype_id": None,
                 "irrigation": float(g["irrigation_reliability"].mean()),
                 "dealer_km": None,
                 "dealer_accessibility": float(g["dealer_accessibility"].mean()),
                 "lon": float(g["lon"].mean()), "lat": float(g["lat"].mean())}

    agro = q("""SELECT district, state, mean_temp, temp_is_allocated, rain_normal_mm,
                       rain_departure_pct, total_crop_area_lha, top_crops,
                       crop_wheat_share, crop_rice_share, crop_cotton_share,
                       crop_soybean_share, crop_sugarcane_share, crop_maize_share
                FROM agroclimate WHERE district_id = ?""", [district_id])
    soil = q("""SELECT aesr_code, soil_type, climate, lgp_days, region, sub_region
                FROM district_aesr WHERE district_id = ?""", [district_id])
    dealers = q("""SELECT product_line, own_dealers, competitor_dealers, total_dealers, n_oems
                   FROM dealer_network WHERE district_id = ?""", [district_id])
    oems = q("""SELECT oem, sum(dealers) AS dealers FROM dealer_by_oem
                WHERE district_id = ? GROUP BY 1 ORDER BY 2 DESC LIMIT 6""", [district_id])
    geo = q("""SELECT zone, zone_name, subzone_id, subzone, lgp FROM micromarkets
               WHERE district_id = ? LIMIT 1""", [district_id])

    return clean({
        "level": level, "id": id, "name": name, "district_id": district_id,
        "scope": scope,
        "agro": (agro[0] if agro else {}),
        "soil": (soil[0] if soil else {}),
        "geography": (geo[0] if geo else {}),
        "dealers": {"by_line": dealers, "oems": oems,
                    "note": ("real, district-grain; the implements dealer file has no Punjab "
                             "rows, so a zero there means no data, not white space")},
        "provenance": {"agro": "real · IMD/DES", "soil": "real · NBSS-ICAR AESR",
                       "dealers": "real · dealer locator", "fleet": "modelled · ITL pending",
                       "grain": ("district measurements shown at micro-market grain"
                                 if level == "micromarket" else "district grain")},
    })


@app.get("/api/define/districts")
def define_districts():
    """District profile for Define: real agro-climate (incl. crop-mix) + modelled TIV/share."""
    # The crop columns come from the taxonomy so adding a crop in Configure widens this
    # table too, instead of leaving a hardcoded list to drift out of sync.
    crop_sel = ", ".join(f"a.{c['share_column']}" for c in _load_taxonomy()["crops"]
                         if c.get("share_column"))
    rows = q(f"""
        SELECT a.district_id, a.district, a.state,
               a.mean_temp, a.temp_seasonality, a.rain_normal_mm, a.rain_departure_pct,
               a.total_crop_area_lha, a.top_crops, a.temp_is_allocated, {crop_sel},
               m.tiv, m.sonalika_share, m.n_micromarkets, m.n_villages,
               m.subzone_id, m.subzone, m.zone_name, m.lgp
        FROM agroclimate a
        LEFT JOIN (SELECT district_id, sum(tiv) AS tiv,
                          count(*) AS n_micromarkets, sum(n_villages) AS n_villages,
                          sum(tiv * sonalika_share) / nullif(sum(tiv), 0) AS sonalika_share,
                          max(subzone_id) AS subzone_id, max(subzone) AS subzone,
                          max(zone_name) AS zone_name, max(lgp) AS lgp
                   FROM micromarkets GROUP BY district_id) m USING (district_id)
        ORDER BY a.state, a.district
    """)
    return {"provenance": "mixed", "districts": rows}


@app.get("/api/archetypes")
def archetypes():
    """Zone x TIV tier x HP belt archetypes, labelled by the taxonomy in force.

    Define answers "what kinds of market exist and how big are they", so these rows carry
    fleet, spread and competition -- not demand. Demand is the ranking Plan uses to choose
    between them, and it stays there.
    """
    df = _current_mm()
    rows = _summarise_mm(df)
    rival = _top_branded_rival()
    for r in rows:
        r.update(rival.get(r["archetype_id"], {"rival": None, "rival_share": None}))
        r.pop("potential_units_yr", None)
    tot_tiv = sum(r["tiv"] for r in rows) or 1
    totals = {
        "n_archetypes": len(rows),
        "n_micromarkets": int(len(df)),
        "n_villages": int(df["n_villages"].sum()),
        "tiv": sum(r["tiv"] for r in rows),
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
    # `subzones` and `hp_belts` no longer have a table of their own on the Archetypes
    # screen -- they stay in the payload because Configure lists sub-zones when you compose
    # a zone, and the header states how many of each the current taxonomy uses.
    return {"provenance": "allocated", "archetypes": rows, "totals": clean(totals),
            "hp_belts": belts, "subzones": zones,
            "customised": _TAXONOMY_PATH.exists()}


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


# ---------------------------------------------------------------- define: the taxonomy

class Taxonomy(BaseModel):
    """The whole category set, edited as one document.

    One PUT covers every operation the Configure screen offers -- create, edit and delete a
    TIV tier, an HP belt or a crop category, and merge crops by listing several under one
    category -- because all of those are just a different `tiv_tiers` / `hp_belts` / `crops`
    array. `zones` round-trips unchanged: it is the published ICAR scheme, shown on the
    screen but not editable, because the soil and growing-season data are measured against
    those boundaries.
    """
    version: int = 1
    tiv_tiers: list[dict]
    hp_belts: list[dict]
    crops: list[dict]
    crop_label: dict = Field(default_factory=dict)
    zones: list[dict]


def _taxonomy_state(tax: dict) -> dict:
    from pipeline.cluster import taxonomy as tx
    return {"taxonomy": tax, "customised": _TAXONOMY_PATH.exists(),
            "describes": tx.describe(tax)}


@app.get("/api/taxonomy")
def taxonomy_get():
    """The categories an archetype is built from, and what they currently produce."""
    tax = _load_taxonomy()
    df = _current_mm()
    return clean({**_taxonomy_state(tax),
                  "n_archetypes": int(df["archetype_id"].nunique()),
                  "n_micromarkets": int(len(df)),
                  "subzones": sorted(df["subzone_id"].dropna().unique().tolist()),
                  "crops_present": sorted(df["dominant_crop"].dropna().unique().tolist())})


@app.put("/api/taxonomy")
def taxonomy_put(t: Taxonomy):
    """Save an edited taxonomy and re-label every micro-market against it.

    This is the "re-cluster" the Configure screen offers, and it is honest about what it
    does: micro-market membership is fixed by the pipeline, but which archetype each one
    belongs to is recomputed here, in about a second, for all 23,389.
    """
    from pipeline.cluster import taxonomy as tx
    tax = t.model_dump()
    # Zones are the published ICAR scheme, not a client field: the soil, climate and
    # growing-season figures are measured against those boundaries, so a redrawn zone would
    # carry data that no longer describes it. Whatever came in, the shipped zones win.
    tax["zones"] = tx.load()["zones"]
    tax.pop("crop_label", None)
    problems = tx.validate(tax)
    if problems:
        raise HTTPException(400, {"problems": problems})

    before = _current_mm()["archetype_id"].nunique()
    _save_taxonomy(tax)
    df = _current_mm()
    return clean({**_taxonomy_state(tax),
                  "n_archetypes": int(df["archetype_id"].nunique()),
                  "was": int(before),
                  "moved_micromarkets": int(len(df)),
                  "archetypes": _summarise_mm(df)})


@app.post("/api/taxonomy/reset")
def taxonomy_reset():
    """Back to the shipped taxonomy."""
    if _TAXONOMY_PATH.exists():
        _TAXONOMY_PATH.unlink()
    _current_mm.cache_clear()
    _current_grain_cached.cache_clear()
    _current_ops_cached.cache_clear()
    _archetype_players_cached.cache_clear()
    _approval_cached.cache_clear()
    # Keyed on nothing, so it survives a re-cluster unless said so: the Define archetype
    # table would keep naming rivals for archetype ids the edit deleted.
    _top_branded_rival.cache_clear()
    _district_rivals.cache_clear()
    tax = _load_taxonomy()
    df = _current_mm()
    return clean({**_taxonomy_state(tax),
                  "n_archetypes": int(df["archetype_id"].nunique()),
                  "archetypes": _summarise_mm(df)})


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


# ---------------------------------------------------------------- plan: where to play

def _archetype_players(line: str = "implements") -> pd.DataFrame:
    """Per-archetype OEM share: district player shares, TIV-weighted onto archetypes.

    player_shares is district x category x player. An archetype spans districts, so its
    share for a player is that player's district shares (averaged over categories)
    weighted by the TIV the archetype actually holds in each district.
    """
    return _archetype_players_cached(_stamp(), line).copy()


@lru_cache(maxsize=4)
def _archetype_players_cached(stamp: str, line: str = "implements") -> pd.DataFrame:
    # The TIV weights come from the labels in force rather than the mart, so the competitor
    # board is still populated for an archetype the client created on Configure.
    dw = (_current_grain_cached(stamp, line).groupby(["archetype_id", "district_id"])["tiv"]
          .sum().reset_index(name="w"))
    ps = con().execute("""SELECT district_id, player, avg(share) AS sh
                          FROM player_shares GROUP BY 1, 2""").fetchdf()
    m = dw.merge(ps, on="district_id")
    m["num"] = m["sh"] * m["w"]
    out = m.groupby(["archetype_id", "player"]).agg(num=("num", "sum"), w=("w", "sum")).reset_index()
    out["share"] = out["num"] / out["w"].replace(0, np.nan)
    return out[["archetype_id", "player", "share"]]


def _plan_buckets(line: str = "implements", fit_min: float = 0.55,
                  mode: str = "stronghold", defend_pct: float = 0.75) -> pd.DataFrame:
    """Bucket every archetype into Defend / Grow / No product fit.

    `mode="leader"` is the literal reading -- Defend only where Sonalika is the #1 OEM.
    On today's modelled shares that bucket comes back EMPTY: the unbranded "Local" segment
    leads all 53 archetypes and our share sits between 6.3% and 9.0% everywhere. So the
    default `mode="stronghold"` reads Defend as relative strength instead -- the top
    `defend_pct` of archetypes by our own share, where the product also fits. Both modes
    ship because which one is right depends on real ITL share data we do not have yet.
    """
    a = _current_ops(line)
    # TIV-weighted centroid, so an archetype can be placed on the map without shipping
    # all 23,389 micro-markets to the browser.
    cen = con().execute("""
        SELECT archetype_id,
               sum(lon * tiv) / nullif(sum(tiv), 0) AS lon,
               sum(lat * tiv) / nullif(sum(tiv), 0) AS lat
        FROM micromarket_ops GROUP BY 1""").fetchdf().set_index("archetype_id")
    a["lon"] = a["archetype_id"].map(cen["lon"])
    a["lat"] = a["archetype_id"].map(cen["lat"])
    pl = _archetype_players(line)
    if len(pl):
        pl["rank"] = pl.groupby("archetype_id")["share"].rank(ascending=False, method="min")
        top = pl[pl["rank"] == 1].drop_duplicates("archetype_id").set_index("archetype_id")
        own = pl[pl["player"] == "Sonalika"].set_index("archetype_id")
        a["leader"] = a["archetype_id"].map(top["player"])
        a["leader_share"] = a["archetype_id"].map(top["share"])
        a["own_rank"] = a["archetype_id"].map(own["rank"])
    else:                                     # no competition mart -- degrade, don't 500
        a["leader"], a["leader_share"], a["own_rank"] = None, np.nan, np.nan

    no_fit = a["product_fit"] < fit_min
    if mode == "leader":
        defend = a["own_rank"] == 1
    else:
        defend = a["avg_sonalika_share"] >= a["avg_sonalika_share"].quantile(defend_pct)
    a["bucket"] = np.where(no_fit, "No product fit",
                           np.where(defend, "Defend", "Grow"))
    a["share_gap"] = a["leader_share"] - a["avg_sonalika_share"]
    # The bottom fifth by demand is shown greyed rather than hidden -- dropping it would
    # lose the denominator the percentages are read against.
    a["low_demand"] = a["potential_units_yr"] <= a["potential_units_yr"].quantile(0.20)
    return a.sort_values("potential_units_yr", ascending=False)


@app.get("/api/plan/buckets")
def plan_buckets(fit_min: float = 0.55, mode: str = "stronghold",
                 defend_pct: float = 0.75, product: str = "implements"):
    """Archetypes split Defend / Grow / No product fit, with the rule that produced it."""
    line = _line(product)
    a = _plan_buckets(line, fit_min,
                      mode if mode in ("stronghold", "leader") else "stronghold", defend_pct)
    totals = [{"bucket": b,
               "archetypes": int((a["bucket"] == b).sum()),
               "villages": int(a.loc[a["bucket"] == b, "n_villages"].sum()),
               "micromarkets": int(a.loc[a["bucket"] == b, "n_micromarkets"].sum()),
               "tiv": round(float(a.loc[a["bucket"] == b, "tiv"].sum())),
               "demand": round(float(a.loc[a["bucket"] == b, "potential_units_yr"].sum()))}
              for b in ("Defend", "Grow", "No product fit")]
    cols = ["archetype_id", "archetype", "base_name", "hp_belt", "subzone", "subzone_id",
            "bucket", "low_demand", "avg_sonalika_share", "leader", "leader_share",
            "share_gap", "own_rank", "product_fit", "n_micromarkets", "n_villages", "tiv",
            "potential_units_yr", "sonalika_sales_units", "activities_yr", "enquiries_yr",
            "deliveries_yr", "conversion_rate", "sales_coverage", "diagnosis", "states",
            "lon", "lat"]
    rule = ("Defend = we are the #1 OEM in the archetype"
            if mode == "leader" else
            f"Defend = top {round((1 - defend_pct) * 100)}% of archetypes by our own share")
    return {"product_line": product, "provenance": "modelled",
            "rule": {"mode": mode, "fit_min": fit_min, "defend_pct": defend_pct,
                     "defend": rule,
                     "no_fit": f"No product fit = product fit below {fit_min}",
                     "grow": "Grow = everything else -- the product works, the share doesn't"},
            "totals": totals,
            "archetypes": clean(a[cols].to_dict("records"))}


@app.get("/api/plan/bucket/{archetype_id}/micromarkets")
def plan_bucket_micromarkets(archetype_id: str, limit: int = 400):
    """One archetype's micro-markets, descending by TIV, with the full BD funnel."""
    rows = q("""
        SELECT micro_market_id, district, state, n_villages, tiv, mean_hp, hp_belt,
               sonalika_share, sonalika_sales_units, potential_units_yr,
               activities_yr, enquiries_yr, deliveries_yr, conversion_rate,
               product_fit, dealer_accessibility, lon, lat
        FROM micromarket_ops WHERE archetype_id = ?
        ORDER BY tiv DESC LIMIT ?
    """, [archetype_id, limit])
    return {"archetype_id": archetype_id, "provenance": "modelled", "micromarkets": rows}


# ---------------------------------------------------------------- plan: forecast

class PlanForecast(BaseModel):
    shocks: dict[str, float] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    state: str | None = None
    archetype_id: str | None = None
    metric: str = "demand"                       # demand | registrations
    history_months: int = 12


def _scope_weights(state: str | None, archetype_id: str | None) -> pd.DataFrame:
    """District weights for a scope.

    A state or the whole pilot takes each district whole (weight 1). An archetype takes
    only the slice of a district it actually holds -- its share of that district's TIV --
    because an archetype spans parts of many districts and claiming the whole district's
    registrations would overstate it several-fold.
    """
    if archetype_id:
        w = con().execute("""
            WITH a AS (SELECT district_id, sum(tiv) AS own FROM micromarket_ops
                       WHERE archetype_id = ? GROUP BY 1),
                 d AS (SELECT district_id, sum(tiv) AS tot FROM micromarket_ops GROUP BY 1)
            SELECT a.district_id, a.own / nullif(d.tot, 0) AS w
            FROM a JOIN d USING (district_id)""", [archetype_id]).fetchdf()
    elif state:
        w = con().execute("SELECT district_id, 1.0 AS w FROM district_totals WHERE state = ?",
                          [state]).fetchdf()
    else:
        w = con().execute("SELECT district_id, 1.0 AS w FROM district_totals").fetchdf()
    return w.dropna()


@app.post("/api/plan/forecast")
def plan_forecast(s: PlanForecast):
    """Six months forward, and what the scenario sliders do to it.

    Two separable pieces, deliberately: the SHAPE is the district UCM's own forecast
    (trend + estimated seasonal + drivers at a normal year), and the SHIFT is the shock
    propagated through each district's own beta -- the same elasticity path /api/scenario
    uses, applied month by month instead of to one annual scalar. Factor-weight overrides
    re-score demand, so they come from the scenario re-scorer and land as a level shift.
    """
    w = _scope_weights(s.state, s.archetype_id)
    if w.empty:
        raise HTTPException(404, "no districts in that scope")
    wt = w.set_index("district_id")["w"]

    hist = con().execute("SELECT district_id, month, observed FROM ucm_decomposition").fetchdf()
    fcst = con().execute("SELECT district_id, month, forecast, lo, hi FROM ucm_forecast").fetchdf()
    if fcst.empty:
        raise HTTPException(503, "no forecast mart -- run `python -m pipeline.run --stage ucm`")

    # ---- shock multiplier, per district, from its own betas -----------------
    mult = pd.Series(1.0, index=wt.index)
    var = pd.Series(0.0, index=wt.index)
    applied = []
    if s.shocks:
        B = con().execute("SELECT district_id, regressor, beta, se, usable FROM ucm_betas").fetchdf()
        bad = set(s.shocks) - set(B["regressor"].unique())
        if bad:
            raise HTTPException(400, f"unknown regressors {bad}")
        log_delta = pd.Series(0.0, index=wt.index)
        for r_, sd_ in s.shocks.items():
            br = B[B["regressor"] == r_].set_index("district_id")
            log_delta += wt.index.map(br["beta"]).to_series(index=wt.index).fillna(br["beta"].mean()) * sd_
            var += (wt.index.map(br["se"]).to_series(index=wt.index).fillna(br["se"].mean()) * sd_) ** 2
            applied.append({
                "regressor": r_, "shock_sd": sd_,
                "beta_pooled": round(float(br["beta"].mean()), 4),
                "beta_min": round(float(br["beta"].min()), 4),
                "beta_max": round(float(br["beta"].max()), 4),
                "effect_pct_pooled": round((float(np.exp(br["beta"].mean() * sd_)) - 1) * 100, 2),
                "usable_share": round(float(br["usable"].mean()), 2),
            })
        mult = np.exp(log_delta)

    # Factor weights re-score demand itself, which the time series cannot express -- take
    # the scenario re-scorer's own answer and apply it as a level shift.
    weight_mult = 1.0
    if s.weights:
        sc = scenario(Scenario(weights=s.weights, state=s.state, level="state"))
        base_u = sc["total"]["units_base"] or 1.0
        weight_mult = sc["total"]["units_scenario"] / base_u

    def _agg(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
        d = df[df["district_id"].isin(wt.index)].copy()
        d["w"] = d["district_id"].map(wt)
        for c in cols:
            d[c] = d[c] * d["w"]
        return d.groupby("month", as_index=False)[cols].sum().sort_values("month")

    h = _agg(hist, ["observed"])
    f = _agg(fcst, ["forecast", "lo", "hi"])

    # Scenario line: the district shock multipliers, TIV-weighted onto the scope total.
    d_f = fcst[fcst["district_id"].isin(wt.index)].copy()
    d_f["w"] = d_f["district_id"].map(wt)
    d_f["m"] = d_f["district_id"].map(mult).fillna(1.0)
    scen = (d_f.assign(v=d_f["forecast"] * d_f["w"] * d_f["m"] * weight_mult)
                .groupby("month", as_index=False)["v"].sum().sort_values("month"))
    f = f.merge(scen, on="month", how="left").rename(columns={"v": "scenario"})
    band = float(1.645 * np.sqrt(float((var * wt).sum() / max(wt.sum(), 1e-9))))

    # ---- metric --------------------------------------------------------------
    # Registrations come straight off the model. Demand is the annual implement potential
    # for this scope spread on the SAME estimated monthly shape -- the seasonality is the
    # UCM's, not a flat twelfth.
    unit = "tractor registrations / month"
    if s.metric != "registrations":
        ann = con().execute("SELECT district_id, potential_units_yr FROM district_totals").fetchdf()
        annual = float((ann.set_index("district_id")["potential_units_yr"]
                        .reindex(wt.index).fillna(0.0) * wt).sum())
        series = pd.concat([h.rename(columns={"observed": "v"})[["month", "v"]],
                            f.rename(columns={"forecast": "v"})[["month", "v"]]])
        roll = series.set_index("month")["v"].rolling(12).sum()
        for frame, cols in ((h, ["observed"]), (f, ["forecast", "lo", "hi", "scenario"])):
            share = frame["month"].map(roll)
            for c in cols:
                frame[c] = frame[c] / share * annual
        unit = "implement demand, units / month"

    tail = int(max(s.history_months, 1))
    history = [{"month": r["month"], "actual": round(float(r["observed"]), 1)}
               for _, r in h.tail(tail).iterrows() if np.isfinite(r["observed"])]
    forecast = [{"month": r["month"],
                 "baseline": round(float(r["forecast"]), 1),
                 "scenario": round(float(r["scenario"]), 1),
                 "lo": round(float(r["lo"]) * np.exp(-band), 1),
                 "hi": round(float(r["hi"]) * np.exp(band), 1)}
                for _, r in f.iterrows()]

    base_sum = float(f["forecast"].sum()) or 1.0
    scen_sum = float(f["scenario"].sum())
    by_state = []
    if s.shocks and not s.state and not s.archetype_id:
        st = con().execute("SELECT district_id, state FROM district_totals").fetchdf()
        d_f["state"] = d_f["district_id"].map(st.set_index("district_id")["state"])
        gb = d_f.groupby("state").apply(
            lambda g: pd.Series({
                "units_base": float((g["forecast"] * g["w"]).sum()),
                "units_scenario": float((g["forecast"] * g["w"] * g["m"]).sum() * weight_mult)}),
            include_groups=False).reset_index()
        gb["delta_pct"] = np.where(gb["units_base"] > 0,
                                   (gb["units_scenario"] / gb["units_base"] - 1) * 100, 0.0)
        by_state = clean(gb.sort_values("delta_pct").to_dict("records"))

    return clean({
        "metric": s.metric, "unit": unit, "provenance": "allocated",
        "scope": {"state": s.state, "archetype_id": s.archetype_id,
                  "districts": int(len(wt))},
        "history": history, "forecast": forecast,
        "history_ends": history[-1]["month"] if history else None,
        "total": {"baseline": round(base_sum, 1), "scenario": round(scen_sum, 1),
                  "delta_pct": round((scen_sum / base_sum - 1) * 100, 2),
                  "ci_low_pct": round((np.exp(-band) * scen_sum / base_sum - 1) * 100, 2),
                  "ci_high_pct": round((np.exp(band) * scen_sum / base_sum - 1) * 100, 2)},
        "shocks_applied": applied, "by_state": by_state,
    })


# ---------------------------------------------------------------- plan: targets

@app.get("/api/plan/targets")
def plan_targets(archetype_id: str, target_units: float | None = None,
                 fit_min: float = 0.55, mode: str = "stronghold",
                 defend_pct: float = 0.75, product: str = "implements"):
    """Back-solve the BD funnel for a target, and rank the levers that could close it.

    The funnel is an identity in the marts -- deliveries = share x demand, enquiries =
    deliveries / conversion_rate, activities = enquiries / enquiry_rate -- so inverting it
    is arithmetic, not a model. Peer benchmarks come from Grow archetypes in the same HP
    belt, which is the fairest comparison available.
    """
    a = _plan_buckets(_line(product), fit_min, mode, defend_pct)
    row = a[a["archetype_id"] == archetype_id]
    if row.empty:
        raise HTTPException(404, "archetype not found")
    r = row.iloc[0]

    deliveries = float(r["deliveries_yr"]) or 0.0
    enquiries = float(r["enquiries_yr"]) or 0.0
    activities = float(r["activities_yr"]) or 0.0
    conv = float(r["conversion_rate"]) or 0.0                 # deliveries / enquiries
    enq_rate = enquiries / activities if activities else 0.0  # enquiries / activities
    demand = float(r["potential_units_yr"]) or 0.0
    share = float(r["avg_sonalika_share"]) or 0.0
    gap = float(r["share_gap"]) if pd.notna(r["share_gap"]) else 0.0

    # Default target closes a quarter of the distance to the archetype's leader, so the
    # screen opens on a defensible number instead of a blank box.
    default_units = round(demand * (share + 0.25 * max(gap, 0.0)))
    target = float(target_units) if target_units else float(default_units)

    need_enq = target / conv if conv else 0.0
    need_act = need_enq / enq_rate if enq_rate else 0.0

    peers = a[(a["bucket"] == "Grow") & (a["hp_belt"] == r["hp_belt"])
              & (a["archetype_id"] != archetype_id)]
    peer_conv = float(peers["conversion_rate"].median()) if len(peers) else conv
    peer_cov = float(peers["sales_coverage"].median()) if len(peers) else float(r["sales_coverage"])

    shortfall = max(target - deliveries, 0.0)
    levers = [{
        "lever": "Run more BD activities",
        "detail": f"{round(need_act - activities):,} more activities a year "
                  f"at today's {enq_rate:.0%} enquiry rate and {conv:.0%} conversion",
        "units": round(shortfall),
        "kind": "volume",
    }]
    if peer_conv > conv:
        levers.append({
            "lever": "Lift conversion to the peer median",
            "detail": f"{conv:.1%} today vs {peer_conv:.1%} across Grow archetypes "
                      f"in the {r['hp_belt']} belt",
            "units": round(enquiries * (peer_conv - conv)),
            "kind": "efficiency",
        })
    cov = float(r["sales_coverage"])
    if peer_cov > cov and cov > 0:
        levers.append({
            "lever": "Close the dealer coverage gap",
            "detail": f"{cov:.0%} of this archetype is covered vs {peer_cov:.0%} for peers",
            "units": round(deliveries * ((peer_cov - cov) / cov)),
            "kind": "coverage",
        })
    levers.sort(key=lambda x: -x["units"])
    levers.append({
        "lever": "Product fit ceiling",
        "detail": f"fit is {float(r['product_fit']):.2f}; below {fit_min} no amount of "
                  f"selling moves this archetype",
        "units": 0, "kind": "ceiling",
    })

    return {
        "archetype_id": archetype_id, "archetype": r["archetype"], "bucket": r["bucket"],
        "hp_belt": r["hp_belt"], "provenance": "modelled",
        "current": {"deliveries": round(deliveries), "enquiries": round(enquiries),
                    "activities": round(activities), "conversion_rate": conv,
                    "enquiry_rate": enq_rate, "share": share, "demand": round(demand),
                    "leader": r["leader"], "leader_share": clean(r["leader_share"]),
                    "sales_coverage": cov, "product_fit": float(r["product_fit"])},
        "target": {"units": round(target), "default_units": default_units,
                   "share": target / demand if demand else 0.0,
                   "enquiries": round(need_enq), "activities": round(need_act),
                   "delta_deliveries": round(target - deliveries),
                   "delta_enquiries": round(need_enq - enquiries),
                   "delta_activities": round(need_act - activities)},
        "levers": clean(levers),
    }


# ---------------------------------------------------------------- act: one archetype

# A micro-market counts as "within commercial reach" above this dealer-accessibility score.
# accessibility = exp(-dealer_km / decay), so 0.5 is roughly a dealer within one decay length.
_REACH = 0.5


@lru_cache(maxsize=1)
def _approval_by_archetype() -> pd.Series:
    """Mean loan-approval rate per archetype.

    `approval_rate` is a real column on village_features and the pipeline's own conversion
    identity uses it -- conv = approval_rate x (0.55 + 0.45 x dealer_accessibility)
    (pipeline/simulate/assets.py). That makes it the one place a "finance access" lever can
    move a model input rather than a fudge factor.
    """
    return _approval_cached(_stamp()).copy()


@lru_cache(maxsize=2)
def _approval_cached(stamp: str) -> pd.Series:
    v = con().execute("""
        SELECT v.micro_market_id, avg(f.approval_rate) AS approval, count(*) AS n
        FROM village_features f JOIN village_micromarket v USING (village_id)
        GROUP BY 1""").fetchdf()
    v["archetype_id"] = v["micro_market_id"].map(
        _current_grain_cached(stamp).set_index("micro_market_id")["archetype_id"])
    v = v.dropna(subset=["archetype_id"])
    v["num"] = v["approval"] * v["n"]           # village-count weighted, as the SQL avg was
    g = v.groupby("archetype_id").agg(num=("num", "sum"), n=("n", "sum"))
    return g["num"] / g["n"]


def _archetype_rivals(archetype_id: str, limit: int = 6) -> list[dict]:
    """Winnable and at-risk volume by rival inside one archetype.

    cannibal_ext is village x SKU (3.9M rows), so the district pre-filter matters: it cuts
    the scan to the archetype's own districts before the village join.
    """
    # The membership comes from the taxonomy in force, registered as a frame, so this still
    # finds the villages of an archetype the mart has never seen.
    g = _current_grain()
    mm = g.loc[g["archetype_id"] == archetype_id, ["micro_market_id", "district_id"]]
    con().register("mm_sel", mm)
    try:
        return clean(con().execute("""
            WITH vv AS (SELECT v.village_id FROM village_micromarket v
                        JOIN mm_sel USING (micro_market_id))
            SELECT c.closest_rival AS rival,
                   sum(c.competitor_units) AS their_units,
                   sum(c.winnable_units) AS winnable,
                   sum(c.at_risk_units) AS at_risk,
                   sum(c.sonalika_units) AS our_units
            FROM cannibal_ext c JOIN vv USING (village_id)
            WHERE c.district_id IN (SELECT DISTINCT district_id FROM mm_sel)
            GROUP BY 1 ORDER BY winnable DESC LIMIT ?
        """, [limit]).fetchdf().to_dict("records"))
    finally:
        con().unregister("mm_sel")


def _archetype_row(archetype_id: str, line: str = "implements") -> pd.Series:
    a = _plan_buckets(line)
    row = a[a["archetype_id"] == archetype_id]
    if row.empty:
        raise HTTPException(404, "archetype not found")
    return row.iloc[0]


@app.get("/api/act/summary")
def act_summary(archetype_id: str, product: str = "implements"):
    """Everything the tool knows about one archetype, in one call.

    The heavy time series stay where they already live -- the client calls
    /api/archetype-ucm/uplift for the driver split and /api/plan/forecast for the 6-month
    path -- so this endpoint is one round of small mart reads.
    """
    line = _line(product)
    r = _archetype_row(archetype_id, line)
    prof = q("SELECT * FROM cluster_profiles WHERE archetype_id = ?", [archetype_id])
    mart = q("SELECT * FROM archetypes_mart WHERE archetype_id = ?", [archetype_id])

    # Filtered in pandas against the labels in force, not by SQL against the mart: after a
    # zone is split on Configure the archetype id is new and the mart has no rows for it.
    g = _current_mm()
    g = g[g["archetype_id"] == archetype_id]
    mm = pd.DataFrame([{
        "tiv": g["tiv"].sum(), "mean_hp": g["mean_hp"].mean(),
        "irrigation": g["irrigation_reliability"].mean(),
        **{c: g[c].sum() for c in ("hp_20_35", "hp_35_45", "hp_45_60", "hp_60_plus")},
        **{n: g[f"crop_{n}_share"].mean() for n in
           ("wheat", "rice", "cotton", "soybean", "sugarcane")},
        "rain_mm": g["rain_normal_mm"].mean(), "temp": g["mean_temp"].mean(),
    }])

    go = _current_grain()
    go = go[go["archetype_id"] == archetype_id]
    ops = pd.DataFrame([{
        "accessibility": go["dealer_accessibility"].mean(),
        "service_km": go["service_distance_km"].mean(),
        "tiv_in_reach": go.loc[go["dealer_accessibility"] >= _REACH, "tiv"].sum(),
        "tiv_total": go["tiv"].sum(), "n_mm": len(go),
        "n_districts": go["district_id"].nunique(),
    }])

    states = clean(go.groupby("state")["tiv"].sum().round().sort_values(ascending=False)
                     .reset_index().to_dict("records"))
    players = _archetype_players()
    board = players[players["archetype_id"] == archetype_id].sort_values("share", ascending=False)

    return clean({
        "archetype_id": archetype_id,
        "identity": {
            "name": r["archetype"], "base_name": r["base_name"], "hp_belt": r["hp_belt"],
            "bucket": r["bucket"], "diagnosis": r["diagnosis"],
            "defining": prof[0].get("defining_features") if prof else None,
            # Zone, tier and crop come from the taxonomy in force, not the mart, so they are
            # still right for an archetype the client created by splitting a zone.
            "zone": g["zone"].iloc[0] if len(g) else None,
            "zone_name": g["zone_name"].iloc[0] if len(g) else None,
            "subzone_id": r["subzone_id"], "subzone": r["subzone"],
            "lgp": g["lgp"].iloc[0] if len(g) else None,
            "tiv_tier": g["tiv_tier"].iloc[0] if len(g) else None,
            "top_crops": g["dominant_crop"].mode().iloc[0] if len(g) else None,
            "states": states, "n_districts": int(ops["n_districts"].iloc[0]),
        },
        "size": {
            "micromarkets": int(r["n_micromarkets"]), "villages": int(r["n_villages"]),
            "tiv": round(float(r["tiv"])), "demand_units": round(float(r["potential_units_yr"])),
            "demand_value_inr": mart[0].get("potential_value_inr") if mart else None,
        },
        "position": {
            "share": float(r["avg_sonalika_share"]), "leader": r["leader"],
            "leader_share": clean(r["leader_share"]), "rank": clean(r["own_rank"]),
            "product_fit": float(r["product_fit"]), "cracked_pct": float(r["cracked_pct"]),
            "sales_coverage": float(r["sales_coverage"]),
            "service_coverage": float(r["service_coverage"]),
            "tiv_in_reach": round(float(ops["tiv_in_reach"].iloc[0])),
            "accessibility": float(ops["accessibility"].iloc[0]),
            "service_km": float(ops["service_km"].iloc[0]),
            "approval_rate": clean(_approval_by_archetype().get(archetype_id)),
        },
        "funnel": {
            "activities": int(r["activities_yr"]), "enquiries": int(r["enquiries_yr"]),
            "deliveries": int(r["deliveries_yr"]), "sales_units": int(r["sonalika_sales_units"]),
            "conversion_rate": float(r["conversion_rate"]),
            "enquiry_rate": float(r["enquiries_yr"]) / max(float(r["activities_yr"]), 1),
        },
        "agro": clean(mm.to_dict("records")[0] if len(mm) else {}),
        "leaderboard": clean(board[["player", "share"]].to_dict("records")),
        "rivals": _archetype_rivals(archetype_id),
        "provenance": {"definition": "real", "funnel": "modelled", "share": "modelled",
                       "agro": "real", "network": "real"},
    })


# ---------------------------------------------------------------- act: the playbook

class Assumptions(BaseModel):
    """Survey-shaped inputs. No survey exists yet, so these are the user's own assumptions --
    named on screen, and each one moves a specific number rather than a hidden weight."""
    top_barrier: str = "finance"            # finance | service | awareness | product
    approval_rate: float | None = None      # 0-1; defaults to the archetype's own mean
    awareness: float = 0.38                 # 0-1; scales what extra BD activity yields
    dealer_density_pct: float = 20.0        # the network expansion being priced
    activity_uplift_pct: float = 25.0       # the BD push being priced


class PlaybookReq(BaseModel):
    archetype_id: str
    assumptions: Assumptions = Field(default_factory=Assumptions)


@app.post("/api/act/playbook")
def act_playbook(b: PlaybookReq, product: str = "implements"):
    """Ranked, priced plays for one archetype.

    Every play owns exactly one mechanism, which is what keeps the numbers addable:
    the network play owns REACH (dealer accessibility), the finance play owns APPROVAL,
    the activity play owns EFFORT at fixed rates, the conversion play owns whatever
    execution quality is left after those two, price/promotion owns the UCM's own price and
    promotion betas, and subsidy owns policy. The rival play is deliberately NOT an addend --
    it is the ceiling the rest are measured against.
    """
    line = _line(product)
    a = b.assumptions
    r = _archetype_row(b.archetype_id)
    mm = con().execute("""
        SELECT micro_market_id, tiv, dealer_accessibility, deliveries_yr, potential_units_yr,
               sonalika_share, district, state
        FROM micromarket_ops WHERE archetype_id = ?""", [b.archetype_id]).fetchdf()

    demand = float(r["potential_units_yr"]) or 1.0
    deliveries = float(r["deliveries_yr"])
    enquiries = float(r["enquiries_yr"])
    share = float(r["avg_sonalika_share"])
    conv = float(r["conversion_rate"])
    approval_now = float(_approval_by_archetype().get(b.archetype_id, 0.66))
    # Default to a modest, stated improvement so the play is priced on first load; the panel
    # shows both today's rate and the assumed one, so nothing is hidden.
    approval_new = float(a.approval_rate) if a.approval_rate else min(approval_now + 0.05, 0.95)

    plays: list[dict] = []

    # ---- 1. reach: more dealers -------------------------------------------------------
    # Density scales distance by (1+dd)^-0.5 and accessibility = exp(-km/decay), so the new
    # accessibility is the old one raised to that power. A micro-market that crosses _REACH
    # is newly sellable; one already above it just gets easier to serve.
    dd = max(a.dealer_density_pct, 0.0) / 100.0
    acc = mm["dealer_accessibility"].to_numpy()
    acc_new = np.power(np.clip(acc, 1e-6, 1.0), (1 + dd) ** -0.5)
    crossed = (acc < _REACH) & (acc_new >= _REACH)
    covered = acc >= _REACH
    tiv_reached = float(mm.loc[crossed, "tiv"].sum())
    new_demand = float((mm.loc[crossed, "potential_units_yr"] * share).sum())
    access_lift = (0.55 + 0.45 * acc_new) / (0.55 + 0.45 * acc) - 1
    easier = float((mm["deliveries_yr"].to_numpy() * access_lift * covered).sum())
    if tiv_reached > 0 or easier > 0:
        plays.append({
            "play": f"Expand the dealer network {round(a.dealer_density_pct)}%",
            "owns": "reach",
            "detail": f"{int(crossed.sum())} micro-markets cross into commercial reach, "
                      f"{fmt_units(tiv_reached)} tractors with them; the rest get easier to serve",
            "units": round(new_demand + easier), "tiv_reached": round(tiv_reached),
            "confidence": "estimated", "mode": "grow",
        })

    # ---- 2. approval: finance access --------------------------------------------------
    # conv = approval x (0.55 + 0.45 x accessibility), so a proportional move in approval is a
    # proportional move in conversion, and deliveries follow.
    if approval_new > approval_now:
        plays.append({
            "play": f"Lift loan approval to {approval_new:.0%}",
            "owns": "approval",
            "detail": f"{approval_now:.0%} today across this archetype's villages; conversion "
                      f"moves with it one-for-one in the model's own identity",
            "units": round(deliveries * (approval_new / max(approval_now, 1e-6) - 1)),
            "tiv_reached": None, "confidence": "estimated", "mode": "grow",
        })

    # ---- 3. effort: more BD activity at today's rates ---------------------------------
    # Awareness scales what an extra visit yields; it is the one input with no data proxy
    # anywhere in the repo, so it is labelled an assumption on screen.
    up = max(a.activity_uplift_pct, 0.0) / 100.0
    if up > 0:
        yield_mult = 0.6 + 0.8 * float(np.clip(a.awareness, 0, 1))
        plays.append({
            "play": f"Run {round(a.activity_uplift_pct)}% more BD activities",
            "owns": "effort",
            "detail": f"{int(r['activities_yr'] * up):,} more activities a year at today's "
                      f"{conv:.0%} conversion, scaled by the awareness assumption",
            "units": round(deliveries * up * yield_mult),
            "tiv_reached": None, "confidence": "arithmetic", "mode": "grow",
        })

    # ---- 4. execution quality: whatever peer conversion is left ------------------------
    peers = _plan_buckets(line)
    peers = peers[(peers["hp_belt"] == r["hp_belt"]) & (peers["archetype_id"] != b.archetype_id)]
    peer_conv = float(peers["conversion_rate"].median()) if len(peers) else conv
    claimed = sum(p["units"] for p in plays if p["owns"] in ("reach", "approval"))
    residual = enquiries * max(peer_conv - conv, 0.0) - claimed
    if residual > 0:
        plays.append({
            "play": "Close the rest of the conversion gap",
            "owns": "execution",
            "detail": f"{conv:.1%} today vs {peer_conv:.1%} across the {r['hp_belt']} belt, "
                      f"after what reach and finance already explain",
            "units": round(residual), "tiv_reached": None, "confidence": "arithmetic",
            "mode": "grow",
        })

    # ---- 5. price and promotion, from this archetype's own betas ----------------------
    betas = q("""SELECT regressor, beta, se, significant, sign_ok FROM ucm_arch_betas
                 WHERE archetype_id = ? AND regressor IN ('price_drop_pct', 'is_promotion')""",
              [b.archetype_id])
    for bt in betas:
        if not bt["significant"] or not bt["sign_ok"]:
            continue
        # A window, not the whole year: a 5% price action held for a quarter, or a
        # month-long promotion. Pricing either at 365 days would be a fantasy.
        move, days, label = ((5.0, 90, "Run a 5% price action for a quarter")
                             if bt["regressor"] == "price_drop_pct"
                             else (1.0, 30, "Run a month-long promotion"))
        units_yr = float(bt["beta"]) * move * days
        if units_yr <= 0:
            continue
        plays.append({
            "play": label, "owns": "price",
            "detail": f"this archetype's own estimated beta ({bt['beta']:.2f} units/day per "
                      f"unit of driver) over {days} days, fitted on simulated daily history",
            "units": round(units_yr), "tiv_reached": None, "confidence": "estimated",
            "mode": "grow",
        })

    # ---- 6. subsidy, where the state rate is real and high ----------------------------
    sub = q("""
        WITH st AS (SELECT state, sum(tiv) AS w FROM micromarket_ops
                    WHERE archetype_id = ? GROUP BY 1)
        SELECT s.state, avg(s.subsidy_pct) AS rate, max(s.provenance) AS provenance, max(st.w) AS w
        FROM subsidy s JOIN st USING (state) GROUP BY 1 ORDER BY w DESC""", [b.archetype_id])
    if sub and (sub[0]["rate"] or 0) >= 35:
        prov = sub[0]["provenance"]
        plays.append({
            "play": f"Push the {sub[0]['rate']:.0f}% subsidy in {sub[0]['state']}",
            "owns": "policy",
            "detail": f"state equipment subsidy on this archetype's SKUs "
                      f"({'real rate' if prov == 'real' else 'national SMAM proxy'}); "
                      f"scheme-linked demand runs about 8% above baseline",
            "units": round(deliveries * 0.08), "tiv_reached": None, "mode": "grow",
            "confidence": "arithmetic" if prov == "real" else "proxy",
        })

    # ---- the ceiling, and the product stop -------------------------------------------
    rivals = _archetype_rivals(b.archetype_id, limit=4)
    winnable = float(sum(x["winnable"] or 0 for x in rivals))
    at_risk = float(sum(x["at_risk"] or 0 for x in rivals))

    if r["bucket"] == "No product fit":
        plays = [{
            "play": "Fix the product before spending on selling",
            "owns": "product",
            "detail": f"product fit is {float(r['product_fit']):.0%}, below the floor. "
                      f"At peer share this archetype would be worth "
                      f"{fmt_units(demand * 0.10)} units a year — that is the prize for an "
                      f"adapted {r['hp_belt']} product, not for more calls",
            "units": 0, "tiv_reached": None, "confidence": "arithmetic", "mode": "stop",
        }]
    if r["bucket"] == "Defend" and at_risk > 0 and rivals:
        plays.append({
            "play": f"Hold the line against {rivals[0]['rival']}",
            "owns": "retention",
            "detail": f"{fmt_units(at_risk)} units sit in contests where a rival is closest and "
                      f"our lead is narrow; service coverage here is "
                      f"{float(r['service_coverage']):.0%}, and service is what defends a "
                      f"stronghold rather than new selling",
            "units": round(at_risk), "tiv_reached": None, "confidence": "estimated",
            "mode": "protect",
        })

    # Protect plays lead in a Defend archetype -- the volume already ours is worth more than
    # the volume we might add -- but they are never summed into the growth total.
    plays.sort(key=lambda p: ((0 if p.get("mode") == "protect" else 1)
                              if r["bucket"] == "Defend" else 0, -p["units"]))
    # Rank nudge only -- the barrier assumption never touches a unit figure.
    barrier_owner = {"finance": "approval", "service": "reach",
                     "awareness": "effort", "product": "product"}.get(a.top_barrier)
    if barrier_owner:
        plays.sort(key=lambda p: (0 if p.get("mode") == "protect" and r["bucket"] == "Defend"
                                  else 1 if p["owns"] == barrier_owner else 2, -p["units"]))

    raw = float(sum(p["units"] for p in plays if p.get("mode") == "grow"))
    headroom = max(demand * (1 - share), 0.0)
    # Headroom is the only hard ceiling: we cannot sell more than the archetype's unclaimed
    # demand. `winnable` is narrower -- volume in contests where a rival is closest and
    # beatable -- so it is reported as context, not used to cap plays that grow the category
    # for us rather than take from one named rival.
    capped = min(raw, headroom)
    for p in plays:
        p["share_pts"] = round(p["units"] / demand * 100, 2)

    return clean({
        "archetype_id": b.archetype_id, "bucket": r["bucket"], "archetype": r["archetype"],
        "hp_belt": r["hp_belt"], "provenance": "modelled",
        "situation": {
            "share": share, "leader": r["leader"], "leader_share": clean(r["leader_share"]),
            "product_fit": float(r["product_fit"]), "demand": round(demand),
            "deliveries": round(deliveries), "sales_coverage": float(r["sales_coverage"]),
            "approval_rate": approval_now, "conversion_rate": conv,
        },
        "plays": plays,
        "total": {"raw_units": round(raw), "capped_units": round(capped),
                  "headroom": round(headroom), "winnable_ceiling": round(winnable),
                  "capped_by": "headroom" if capped < raw else None},
        "rivals": rivals, "at_risk": round(at_risk), "winnable": round(winnable),
        "assumptions_used": a.model_dump(),
    })


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


# ---------------------------------------------------------------- review: shared rollups

@lru_cache(maxsize=1)
def _demographics() -> pd.DataFrame:
    """Village demographics rolled up to micro-market grain.

    There is no demographic mart above village level, so this is the rollup: counts sum,
    rates are weighted by whatever they are a rate *of* -- holding size by number of
    holdings, income by households -- because a plain mean would let a 20-household hamlet
    outvote a 700-household town.

    Provenance is `allocated`, and the panel says so: the state totals are published
    (Census 2011 population, the state x tier holding mix), the split down to a village is
    modelled.
    """
    return con().execute("""
        SELECT vm.micro_market_id,
               sum(f.rural_population)                                        AS population,
               sum(f.households)                                             AS households,
               sum(f.n_holdings)                                             AS holdings,
               sum(f.avg_holding_ha * f.n_holdings) / nullif(sum(f.n_holdings), 0)
                                                                             AS avg_holding_ha,
               sum(f.small_marginal_share * f.n_holdings) / nullif(sum(f.n_holdings), 0)
                                                                             AS small_marginal_share,
               sum(f.large_holding_share * f.n_holdings) / nullif(sum(f.n_holdings), 0)
                                                                             AS large_holding_share,
               sum(f.farm_income_inr * f.households) / nullif(sum(f.households), 0)
                                                                             AS farm_income_inr,
               sum(f.net_sown_ha)                                            AS net_sown_ha,
               sum(f.tractors)                                               AS tractors,
               sum(f.fleet_mean_age * f.tractors) / nullif(sum(f.tractors), 0)
                                                                             AS fleet_mean_age,
               sum(f.approval_rate * f.households) / nullif(sum(f.households), 0)
                                                                             AS approval_rate,
               count(*)                                                      AS villages
        FROM village_micromarket vm
        JOIN village_features f USING (village_id)
        GROUP BY 1
    """).fetchdf().set_index("micro_market_id")


@lru_cache(maxsize=1)
def _district_rivals() -> dict:
    """Top branded rival per district, from the same table the archetype column uses.

    There are two tables that could answer this and they disagree: `player_shares` (district
    x category x player, what `_top_branded_rival()` reads) names Landforce in 61 districts,
    while the modal `closest_rival` in `competitive_landscape` names Shaktiman in 55 -- they
    agree on 24 of 114. Both are the same choice model with different seeds, and
    argmax-of-mean-share is simply not the same statistic as mode-of-argmax when five players
    sit within two points of each other.

    So there is one source, and it is the one already on screen elsewhere: a micro-market
    inherits its district's rival, badged district grain, because the source *is* district
    grain. The alternative is a micro-market naming Shaktiman inside an archetype that names
    Landforce, on two tabs of the same tool.
    """
    """Top branded rival per district, TIV-weighted across the nine categories."""
    d = con().execute("""
        WITH w AS (SELECT district_id, sum(tiv) AS tiv FROM micromarket_ops GROUP BY 1)
        SELECT ps.district_id, ps.player, avg(ps.share) AS share
        FROM player_shares ps JOIN w USING (district_id)
        WHERE ps.player NOT IN ('Local', 'Sonalika')
        GROUP BY 1, 2
    """).fetchdf()
    top = (d.sort_values("share", ascending=False)
            .drop_duplicates("district_id").set_index("district_id"))
    return {k: {"rival": v["player"], "rival_share": float(v["share"])}
            for k, v in top.to_dict("index").items()}


def _funnel(g: pd.DataFrame) -> dict:
    """The BD funnel for a set of micro-markets.

    `deliveries_yr` and `sonalika_sales_units` are the same column (operations.py sets one
    from the other), so the funnel stops at deliveries and reports sales as that step's
    value -- drawing both would show a fourth bar at a fictional 100% conversion.
    """
    demand = float(g["potential_units_yr"].sum())
    act = float(g["activities_yr"].sum())
    enq = float(g["enquiries_yr"].sum())
    dlv = float(g["deliveries_yr"].sum())
    tiv = float(g["tiv"].sum())
    return {
        "activities": round(act), "enquiries": round(enq), "deliveries": round(dlv),
        "sales_units": round(dlv), "demand": round(demand), "tiv": round(tiv),
        "sales_value_inr": float(g["sonalika_sales_value_inr"].sum()),
        "share": (dlv / demand) if demand else None,
        "enquiry_rate": (enq / act) if act else None,
        "conversion_rate": (dlv / enq) if enq else None,
        "product_fit": float((g["product_fit"] * g["tiv"]).sum() / tiv) if tiv else None,
        "sales_effort": float((g["sales_effort"] * g["tiv"]).sum() / tiv) if tiv else None,
        "unserved": round(demand - dlv),
    }


@app.get("/api/review/micromarkets")
def review_micromarkets(district: str | None = None, archetype_id: str | None = None,
                        metric: str = "sonalika_sales_units", limit: int = 700,
                        product: str = "implements"):
    metric = metric if metric in _OPS_METRICS else "sonalika_sales_units"
    df = _current_grain(_line(product))
    if district:
        df = df[df["district_id"] == district]
    if archetype_id:
        df = df[df["archetype_id"] == archetype_id]
    df = df.sort_values(metric, ascending=False).head(limit)
    return {"metric": metric, "micromarkets": clean(df.to_dict("records"))}


@app.get("/api/review/micromarket/{mm_id}")
def review_micromarket(mm_id: str, product: str = "implements"):
    line = _line(product)
    g = _current_grain(line)
    df = g[g["micro_market_id"] == mm_id]
    return {"micromarket": clean(df.to_dict("records"))[0] if len(df) else None}


@app.get("/api/review/profile")
def review_profile(level: str, id: str, product: str = "implements"):
    """One panel for either grain: how we are performing here, and what explains it.

    Define's profile answers "what kind of place is this". This answers the next question,
    and carries the three things that decide it: the sales funnel, who farms here, and what
    grows here. Same drill and the same click, so the two stages read as one motion.

    Demand stays on this screen where it comes off Define's: market share IS sales divided
    by demand, and the whole funnel is sized off it -- without it the panel can show sales
    but not whether they are any good.
    """
    line = _line(product)
    if level not in ("district", "micromarket"):
        raise HTTPException(400, "level must be district or micromarket")

    grain = _current_grain(line)
    demo = _demographics()
    if level == "micromarket":
        g = grain[grain["micro_market_id"] == id]
        if g.empty:
            raise HTTPException(404, "micro-market not found")
        r = g.iloc[0]
        district_id, name = r["district_id"], f"{r['district']} · {id}"
        members = [id]
        competitor = {**_district_rivals().get(r["district_id"],
                                               {"rival": None, "rival_share": None}),
                      "leader": "Local", "grain": "district"}
        archetype = {"id": r["archetype_id"], "name": r["archetype"],
                     "diagnosis": r["diagnosis"], "hp_belt": r["hp_belt"],
                     "tiv_tier": r["tiv_tier"]}
        coverage = {"sales": float(r["dealer_accessibility"]),
                    "service": float(r["service_index"]),
                    "service_km": float(r["service_distance_km"])}
    else:
        g = grain[grain["district_id"] == id]
        if g.empty:
            raise HTTPException(404, "district not found")
        district_id, name = id, g["district"].iloc[0]
        members = g["micro_market_id"].tolist()
        competitor = {**_district_rivals().get(id, {"rival": None, "rival_share": None}),
                      "leader": "Local", "grain": "district"}
        archetype = {"id": None,
                     "name": (g["archetype"].mode().iloc[0] if len(g["archetype"].mode()) else None),
                     "diagnosis": (g["diagnosis"].mode().iloc[0]
                                   if len(g["diagnosis"].mode()) else None),
                     "hp_belt": (g["hp_belt"].mode().iloc[0] if len(g["hp_belt"].mode()) else None),
                     "tiv_tier": None}
        coverage = {"sales": float(g["dealer_accessibility"].mean()),
                    "service": float(g["service_index"].mean()),
                    "service_km": float(g["service_distance_km"].mean())}

    d = demo.reindex(members).sum(numeric_only=True)
    holds = float(d.get("holdings") or 0)
    hh = float(d.get("households") or 0)
    sown = float(d.get("net_sown_ha") or 0)
    trac = float(d.get("tractors") or 0)
    sub = demo.reindex(members)

    def _w(col: str, weight: str) -> float | None:
        """Re-weight a rate that was already averaged inside each micro-market."""
        wt = sub[weight].fillna(0)
        return float((sub[col].fillna(0) * wt).sum() / wt.sum()) if wt.sum() else None

    demographics = {
        "population": round(float(d.get("population") or 0)),
        "households": round(hh), "villages": int(d.get("villages") or 0),
        "holdings": round(holds), "net_sown_ha": round(sown),
        "avg_holding_ha": _w("avg_holding_ha", "holdings"),
        "small_marginal_share": _w("small_marginal_share", "holdings"),
        "large_holding_share": _w("large_holding_share", "holdings"),
        # farm_income_inr is per HOLDING per year. The mart's income_per_ha divides it by the
        # whole village's sown area instead of that holding's, which is out by a factor of
        # n_holdings (~176x), so it is deliberately not carried here.
        "farm_income_inr": _w("farm_income_inr", "households"),
        "tractors": round(trac),
        "tractor_density": (trac / sown * 1000) if sown else None,
        "fleet_mean_age": _w("fleet_mean_age", "tractors"),
        "approval_rate": _w("approval_rate", "households"),
    }

    # dominant_crop lives on the micromarkets mart, not the ops one.
    mm = _current_mm()
    mmg = mm[mm["micro_market_id"].isin(members)]
    dominant_crop = (mmg["dominant_crop"].mode().iloc[0]
                     if len(mmg) and len(mmg["dominant_crop"].mode()) else None)
    irrigation = float(mmg["irrigation_reliability"].mean()) if len(mmg) else None

    agro = q("""SELECT district, state, mean_temp, temp_is_allocated, rain_normal_mm,
                       rain_departure_pct, total_crop_area_lha, top_crops,
                       crop_rice_share, crop_wheat_share, crop_maize_share,
                       crop_gram_share, crop_bajra_share, crop_jowar_share
                FROM agroclimate WHERE district_id = ?""", [district_id])
    # A crop the DES extract reports as zero here is a crop not grown here, and an empty bar
    # for it three lines above "most-grown: cotton" reads as a broken chart rather than as
    # two sources. Only crops with area in THIS district reach the panel.
    if agro:
        agro = [{k: v for k, v in agro[0].items()
                 if not (k.startswith("crop_") and not v)}]
    soil = q("""SELECT aesr_code, soil_type, climate, lgp_days, region, sub_region
                FROM district_aesr WHERE district_id = ?""", [district_id])
    geo = q("""SELECT zone, zone_name, subzone_id, subzone, lgp FROM micromarkets
               WHERE district_id = ? LIMIT 1""", [district_id])
    dealers = q("""SELECT product_line, own_dealers, competitor_dealers, n_oems
                   FROM dealer_network WHERE district_id = ?""", [district_id])

    return clean({
        "level": level, "id": id, "name": name, "district_id": district_id,
        "scope": {"micromarkets": int(len(g)), "villages": int(g["n_villages"].sum()),
                  "state": g["state"].iloc[0],
                  "mean_hp": round(float(g["mean_hp"].mean()), 1),
                  "lon": float(g["lon"].mean()), "lat": float(g["lat"].mean())},
        "sales": _funnel(g),
        "demographics": demographics,
        "agro": (agro[0] if agro else {}),
        "soil": (soil[0] if soil else {}),
        "geography": (geo[0] if geo else {}),
        "irrigation": irrigation,
        "archetype": archetype,
        "competitor": competitor,
        "coverage": coverage,
        "dealers": dealers,
        # Two crop facts from two sources, kept apart on purpose: the DES foodgrain areas
        # above are real but cover only foodgrains, while dominant_crop is modelled and does
        # cover cotton, soybean and sugarcane. Merged into one chart they would say cotton is
        # not grown in Punjab, which is false.
        "dominant_crop": dominant_crop,
        "provenance": {
            "sales": "modelled · ITL pending",
            "demographics": ("allocated — published state totals (Census 2011 population, "
                             "the state x tier holding mix) split down by model"),
            "agro": "real · IMD/DES",
            "soil": "real · NBSS-ICAR AESR",
            "competitor": "modelled",
            "grain": ("district measurements shown at micro-market grain"
                      if level == "micromarket" else "district grain"),
        },
    })


@app.get("/api/review/coverage")
def review_coverage(type: str = "sales", product: str = "implements"):
    """Network coverage per archetype and per district, with the rival we are up against.

    Two very different kinds of number share this response and the provenance block says
    which is which. The dealer COUNTS, the OEM list and pct_covered are real, from the
    dealer locator. The coverage INDICES are not: `dealer_accessibility` is an exponential
    decay off a simulated dealer point set, and `service_index` is that discounted and
    noised because ITL has not shared the service master. The endpoint used to badge the
    whole sales response "real", which read as a claim about the coverage bars.

    pct_covered = share of an archetype's micro-markets whose district has at least one
    Sonalika dealer.
    """
    net = con().execute(
        "SELECT district_id, own_dealers FROM dealer_network WHERE product_line = ?",
        [product]).fetchdf()
    covered = set(net.loc[net["own_dealers"] > 0, "district_id"])
    line = _line(product)
    grain = _current_grain(line)
    mmd = grain[["archetype_id", "district_id"]].copy()
    mmd["cov"] = mmd["district_id"].isin(covered)
    pct = mmd.groupby("archetype_id")["cov"].mean().to_dict()

    arch = _current_ops(line)[["archetype_id", "base_name", "hp_belt", "subzone_id", "subzone",
                           "n_micromarkets", "diagnosis", "sales_coverage", "service_coverage",
                           "avg_sonalika_share"]]
    arch["coverage"] = arch["service_coverage" if type == "service" else "sales_coverage"]
    arch["pct_covered"] = arch["archetype_id"].map(pct).fillna(0.0)
    # Same top-branded-rival convention the Define archetype table uses, so the two screens
    # never name a different competitor for the same archetype.
    rival = _top_branded_rival()
    arch["rival"] = arch["archetype_id"].map(lambda a: rival.get(a, {}).get("rival"))
    arch["rival_share"] = arch["archetype_id"].map(lambda a: rival.get(a, {}).get("rival_share"))
    arch = arch.sort_values("coverage")           # worst-covered first = the gap

    # District-grain coverage for the map: the indices averaged over each district's
    # micro-markets, joined to the real dealer counts. Districts the dealer file does not
    # cover at all -- every Punjab district, for implements -- are flagged rather than
    # drawn as zero coverage, which would read as "we are absent" instead of "we cannot say".
    dg = grain.groupby(["district_id", "district", "state"]).agg(
        sales=("dealer_accessibility", "mean"), service=("service_index", "mean"),
        micromarkets=("micro_market_id", "size"), demand=("potential_units_yr", "sum"),
        lon=("lon", "mean"), lat=("lat", "mean"),
    ).reset_index()
    nd = con().execute("""SELECT district_id, own_dealers, competitor_dealers, n_oems
                          FROM dealer_network WHERE product_line = ?""", [product]).fetchdf()
    dg = dg.merge(nd, on="district_id", how="left")
    dg["has_dealer_data"] = dg["own_dealers"].notna()
    dg["coverage"] = dg["service" if type == "service" else "sales"]
    districts = clean(dg.to_dict("records"))

    def _sum(like_not: bool):
        op = "NOT LIKE" if like_not else "LIKE"
        r = q(f"SELECT sum(dealers) d FROM dealer_by_oem WHERE product_line = ? "
              f"AND lower(oem) {op} '%sonalika%'", [product])
        return int(r[0]["d"] or 0)

    oems = q("""SELECT oem, sum(dealers) AS dealers, count(DISTINCT district_id) AS districts
                FROM dealer_by_oem WHERE product_line = ? AND lower(oem) NOT LIKE '%sonalika%'
                GROUP BY oem ORDER BY dealers DESC LIMIT 6""", [product])
    return {"product_line": product, "type": type,
            "provenance": {
                "dealers": "real · dealer locator",
                "coverage": ("modelled · ITL service master pending" if type == "service"
                             else "modelled · distance decay on a simulated dealer network"),
                "rival": "modelled",
            },
            "own_dealers": _sum(False), "competitor_dealers": _sum(True),
            "covered_states": sorted(nd.merge(dg[["district_id", "state"]], on="district_id")
                                       ["state"].unique().tolist()),
            "districts": districts,
            "archetypes": clean(arch.to_dict("records")), "oems": oems}


@app.get("/api/review/archetypes")
def review_archetypes(product: str = "implements"):
    line = _line(product)
    ops = _current_ops(line)
    rows = clean(ops.to_dict("records"))
    d = ops.groupby("diagnosis").agg(archetypes=("archetype_id", "size"),
                                     micromarkets=("n_micromarkets", "sum"),
                                     demand=("potential_units_yr", "sum"),
                                     sales=("sonalika_sales_units", "sum"))
    diag = clean(d.sort_values("demand", ascending=False).reset_index().to_dict("records"))
    tot = clean({"sales": int(ops["sonalika_sales_units"].sum()),
                 "activities": int(ops["activities_yr"].sum()),
                 "enquiries": int(ops["enquiries_yr"].sum()),
                 "deliveries": int(ops["deliveries_yr"].sum()),
                 "demand": int(ops["potential_units_yr"].sum())})
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
def villages_summary(state: str | None = None, district: str | None = None,
                     product: str = "implements"):
    where, p = ["product_line = ?"], [_line(product)]
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
                         FROM village_insights_pl WHERE {w}
                         GROUP BY 1 ORDER BY units DESC""", p),
        "archetypes": q(f"""SELECT archetype, count(*) villages,
                                   sum(potential_units_yr) "units", sum(headroom) headroom,
                                   avg(attach_rate) "attach", avg(opportunity_score) opp
                            FROM village_insights_pl WHERE {w}
                            GROUP BY 1 ORDER BY units DESC""", p),
        "micro": q(f"""SELECT micro_id, archetype, count(*) villages,
                              sum(potential_units_yr) "units", sum(headroom) headroom,
                              avg(opportunity_score) opp, avg(dealer_distance_km) avg_km,
                              avg(attach_rate) "attach", mode(action_segment) main_action,
                              mode(top_sku) top_sku
                       FROM village_insights_pl WHERE {w}
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
def kpis(state: str | None = None, product: str = "implements"):
    """The executive KPI set. One call, everything the summary tiles need."""
    line = _line(product)
    where, p = (["i.product_line = ?"], [line])
    if state:
        where.append("i.state = ?"); p.append(state)
    w = " WHERE " + " AND ".join(where)
    core = q(f"""SELECT count(*) villages, sum(potential_units_yr) demand_units,
                        sum(potential_value_inr) demand_value,
                        sum(headroom) unserved_units, sum(addressable) addressable,
                        sum(owned) "owned", avg(attach_rate) attach_rate,
                        avg(dealer_distance_km) avg_dealer_km,
                        sum(tractors) tractors
                 FROM village_insights_pl i{w}""", p)[0]
    acts = q(f"""SELECT action_segment, count(*) villages, sum(potential_units_yr) "units",
                        sum(headroom) headroom
                 FROM village_insights_pl i{w} GROUP BY 1""", p)
    repl = q(f"""SELECT sum(s.new_units_yr) new_units, sum(s.replacement_units_yr) repl_units
                 FROM village_sku s JOIN village_insights_pl i USING (village_id, product_line)
                 {w}""", p)[0]
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
