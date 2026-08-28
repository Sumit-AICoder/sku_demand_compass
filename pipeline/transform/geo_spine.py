"""Phase 1 -- the spatial spine: State -> District -> Block -> Village.

Everything downstream joins on the ids minted here, so this runs first and every
later table is validated against it.

What is real and what is not, stated plainly:

  * State and district NAMES, counts, agro-climatic zone and crop system are real
    (pipeline/config/districts.csv -- 23 Punjab, 55 MP, 36 Maharashtra districts).
  * District GEOMETRY is real when data/raw/geo/india_district.geojson is present
    (open Census-2011-derived boundaries); village points are then sampled inside
    the true district polygon.
  * Sub-district (block) and village ROWS are synthesised, because LGD's village
    master sits behind a session/CSRF wall with no REST surface. Counts are anchored
    to published Census 2011 totals per state so the spine is the right SIZE and the
    right SHAPE, and ids follow the LGD layout so a real extract can be dropped in.

`code_source` on every row records which of those applies. Nothing here pretends
to be an authentic LGD code.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.common import (
    CURATED, RAW, Config, FetchRecord, Manifest, write_table, log,
)

LOG = log("geo_spine")

# Census 2011 published totals, used to size the synthetic spine correctly.
STATE_ANCHORS = {
    "Punjab":         {"lgd": "03", "villages": 12168, "blocks": 82,  "net_sown_kha": 4130, "pop_rural_lakh": 173},
    "Madhya Pradesh": {"lgd": "23", "villages": 52117, "blocks": 342, "net_sown_kha": 15200, "pop_rural_lakh": 525},
    "Maharashtra":    {"lgd": "27", "villages": 40959, "blocks": 355, "net_sown_kha": 17400, "pop_rural_lakh": 615},
}

# Rough state bounding boxes (lon_min, lat_min, lon_max, lat_max) -- only used as a
# fallback when the real district geojson is unavailable, so the map still renders.
STATE_BBOX = {
    "Punjab":         (73.87, 29.53, 76.93, 32.51),
    "Madhya Pradesh": (74.03, 21.08, 82.81, 26.87),
    "Maharashtra":    (72.66, 15.60, 80.90, 22.03),
}

# Relative district size weights by mechanisation/irrigation tier. Districts in
# hill/forest zones hold fewer, smaller villages; plateau and plain districts more.
ZONE_SIZE_HINT = {
    "Konkan": 0.75, "Jhabua Hills": 0.7, "Northern Hill Chhattisgarh": 0.8,
    "Bundelkhand": 0.9, "Satpura Plateau": 0.95, "Gird Region": 1.05,
    "Malwa Plateau": 1.15, "Central Narmada Valley": 1.1, "Kymore Plateau": 1.0,
    "Vindhya Plateau": 1.0, "Nimar Plains": 1.05,
    "Majha": 1.1, "Doaba": 1.05, "Malwa": 1.15,
    "Western Maharashtra Plain": 1.15, "Western Maharashtra Scarcity": 1.1,
    "Western Khandesh": 1.05, "Marathwada": 1.1,
    "Vidarbha West": 1.05, "Vidarbha East": 0.9,
}

# Primary source: current Indian administrative boundaries (36 states/UTs, post-2019
# reorganisation) which depict Jammu & Kashmir and Ladakh at their full official extent.
# The older GADM file is kept only as a fallback for the handful of districts created
# after it was published.
GEOJSON = RAW / "geo" / "india_official.geojson"
GEOJSON_FALLBACK = RAW / "geo" / "india_district.geojson"


# ------------------------------------------------------------------ geometry

def _load_district_geometry() -> dict[tuple[str, str], dict]:
    """Return {(state, district_norm): {centroid, bbox, ring}} for the pilot states.

    Reads the current-boundary file first, then fills gaps from the older GADM file for
    districts created after it was published. Empty dict if neither is present, in which
    case callers fall back to a schematic layout.
    """
    out: dict[tuple[str, str], dict] = {}
    for path, label in ((GEOJSON, "official"), (GEOJSON_FALLBACK, "gadm")):
        if not path.exists():
            continue
        try:
            gj = json.loads(path.read_text())
        except Exception as exc:                                # noqa: BLE001
            LOG.warning("%s geojson unreadable (%s)", label, exc)
            continue
        added = 0
        for feat in gj.get("features", []):
            props = {k.lower(): v for k, v in (feat.get("properties") or {}).items()}
            st = (props.get("st_nm") or props.get("name_1")
                  or props.get("statename") or props.get("state"))
            dt = (props.get("district") or props.get("name_2")
                  or props.get("distname") or props.get("dtname"))
            if not st or not dt:
                continue
            if _norm(st) not in {_norm(s) for s in STATE_ANCHORS}:
                continue
            key = (_norm(st), _norm(dt))
            if key in out:                       # first source wins
                continue
            pts = _all_points(feat.get("geometry") or {})
            if not pts:
                continue
            arr = np.asarray(pts)
            out[key] = {
                "centroid": (float(arr[:, 0].mean()), float(arr[:, 1].mean())),
                "bbox": (float(arr[:, 0].min()), float(arr[:, 1].min()),
                         float(arr[:, 0].max()), float(arr[:, 1].max())),
                "ring": arr,
                "source": label,
            }
            added += 1
        LOG.info("%s geojson: %d pilot-state districts", label, added)
    LOG.info("district geometry available for %d districts", len(out))
    return out


def _all_points(geom: dict) -> list[list[float]]:
    """Flatten any GeoJSON geometry down to a list of [lon, lat]."""
    t, c = geom.get("type"), geom.get("coordinates")
    if c is None:
        return []
    if t == "Polygon":
        return [p for ring in c for p in ring]
    if t == "MultiPolygon":
        return [p for poly in c for ring in poly for p in ring]
    if t == "Point":
        return [c]
    return []


def _norm(s: str) -> str:
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


# District name aliases: the open geojson predates several renamings.
ALIASES = {
    # The open geojson is a GADM-derived vintage that predates the post-2001 district
    # splits, so several current districts resolve to their PARENT polygon. That is
    # recorded as geometry_source="parent_boundary", and villages of districts sharing
    # a parent are spatially partitioned so they never overlap on the map.
    "tarntaran": ["tarntaran", "amritsar"],
    "pathankot": ["pathankot", "gurdaspur"],
    "shahidbhagatsinghnagar": ["nawanshehar", "nawanshahr"],
    "fazilka": ["fazilka", "firozpur", "ferozepur"],
    "barnala": ["barnala", "sangrur"],
    "sahibzadaajitsinghnagar": ["sasnagar", "mohali", "rupnagar"],
    "malerkotla": ["sangrur"],
    "srimuktsarsahib": ["muktsar"],
    "firozpur": ["ferozepur", "firozpur"],
    "alirajpur": ["alirajpur", "jhabua"],
    "khargone": ["khargone", "westnimar"],
    "khandwa": ["khandwa", "eastnimar"],
    "singrauli": ["singrauli", "sidhi"],
    "narmadapuram": ["narmadapuram", "hoshangabad"],
    "maihar": ["satna"],
    "mauganj": ["rewa"],
    "pandhurna": ["chhindwara"],
    "niwari": ["niwari", "tikamgarh"],
    "agarmalwa": ["agarmalwa", "shajapur"],
    "aurangabad": ["aurangabad", "chhatrapatisambhajinagar"],
    "osmanabad": ["osmanabad", "dharashiv"],
    "beed": ["beed", "bid"],
    "buldhana": ["buldhana", "buldana"],
    "gondia": ["gondia", "gondiya"],
    "gadchiroli": ["gadchiroli", "garhchiroli"],
    "palghar": ["palghar", "thane"],
    "raigad": ["raigad", "raigarh"],
    "mumbaicity": ["mumbai", "greaterbombay"],
    "mumbaisuburban": ["mumbai", "greaterbombay"],
}


def _match_geometry(geo: dict, state: str, district: str):
    """Return (key, geometry) or (None, None). The key lets us detect shared parents."""
    st, dt = _norm(state), _norm(district)
    for cand in ALIASES.get(dt, [dt]):
        if (st, cand) in geo:
            return (st, cand), geo[(st, cand)]
    if (st, dt) in geo:
        return (st, dt), geo[(st, dt)]
    for (gs, gd), v in geo.items():
        if gs == st and (dt in gd or gd in dt):
            return (gs, gd), v
    return None, None


def _assign_capacitated(pts: np.ndarray, seeds: np.ndarray, caps: list[int]) -> np.ndarray:
    """Assign each point to a seed, respecting per-seed capacity.

    Used to split one parent-district polygon between the several present-day
    districts carved out of it: each child gets exactly its village quota, and the
    partitions stay spatially contiguous instead of interleaving.
    """
    d = np.sqrt(((pts[:, None, :] - seeds[None, :, :]) ** 2).sum(-1))
    # Order points by how strongly they prefer their best seed over the next best,
    # so the least ambiguous points claim their slot first.
    best = d.argmin(1)
    second = np.partition(d, 1, axis=1)[:, 1]
    regret = second - d[np.arange(len(pts)), best]
    order = np.argsort(-regret)

    out = np.full(len(pts), -1, dtype=int)
    remaining = list(caps)
    for i in order:
        for j in np.argsort(d[i]):
            if remaining[j] > 0:
                out[i] = j
                remaining[j] -= 1
                break
    return out


def _sample_in_polygon(ring: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    """Rejection-sample n points inside the district's outer ring."""
    lon0, lat0 = ring[:, 0].min(), ring[:, 1].min()
    lon1, lat1 = ring[:, 0].max(), ring[:, 1].max()
    out, tries = [], 0
    while len(out) < n and tries < 40:
        k = max(n * 3, 64)
        cand = np.column_stack([rng.uniform(lon0, lon1, k), rng.uniform(lat0, lat1, k)])
        inside = _points_in_poly(cand, ring)
        out.extend(cand[inside].tolist())
        tries += 1
    if len(out) < n:                       # degenerate ring -- top up from the bbox
        need = n - len(out)
        out.extend(np.column_stack([rng.uniform(lon0, lon1, need),
                                    rng.uniform(lat0, lat1, need)]).tolist())
    return np.asarray(out[:n])


def _points_in_poly(pts: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Vectorised ray-casting point-in-polygon."""
    x, y = pts[:, 0], pts[:, 1]
    px, py = poly[:, 0], poly[:, 1]
    qx, qy = np.roll(px, -1), np.roll(py, -1)
    inside = np.zeros(len(pts), dtype=bool)
    for i in range(len(px)):
        x1, y1, x2, y2 = px[i], py[i], qx[i], qy[i]
        if y1 == y2:
            continue
        cond = ((y1 > y) != (y2 > y)) & (x < (x2 - x1) * (y - y1) / (y2 - y1) + x1)
        inside ^= cond
    return inside


# ------------------------------------------------------------------ build

def build(seed: int = 20260822) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    districts = Config.districts()
    geo = _load_district_geometry()

    # ---- pass 1: size every district and resolve its geometry -----------------
    plan: list[dict] = []
    for state, sdf in districts.groupby("state", sort=False):
        anchor = STATE_ANCHORS[state]
        hint = sdf["zone"].map(ZONE_SIZE_HINT).fillna(1.0).to_numpy()
        share = hint * rng.uniform(0.85, 1.15, len(sdf))
        share = share / share.sum()
        vill = np.maximum(40, np.round(share * anchor["villages"]).astype(int))
        blks = np.maximum(2, np.round(share * anchor["blocks"]).astype(int))
        nsa = share * anchor["net_sown_kha"] * 1000.0
        pop = share * anchor["pop_rural_lakh"] * 100_000.0

        for i, (_, d) in enumerate(sdf.iterrows()):
            gkey, g = _match_geometry(geo, state, d["district"])
            plan.append({
                "state": state, "state_code": anchor["lgd"], "district": d["district"],
                "district_id": f"{anchor['lgd']}{i + 1:03d}",
                "zone": d["zone"], "crop_system": d["crop_system"],
                "mech_tier": d["mech_tier"], "irrigation_tier": d["irrigation_tier"],
                "residue_belt": int(d["residue_belt"]),
                "n_blocks": int(blks[i]), "n_villages": int(vill[i]),
                "net_sown_ha": float(nsa[i]), "rural_population": float(pop[i]),
                "gkey": gkey, "_ring": None if g is None else g["ring"],
            })

    # ---- pass 2: sample village coordinates, partitioning shared parents ------
    shared = pd.Series([p["gkey"] for p in plan if p["gkey"]]).value_counts()
    by_key: dict = {}
    for p_ in plan:
        by_key.setdefault(p_["gkey"], []).append(p_)

    for gkey, members in by_key.items():
        if gkey is None:
            for m in members:                                   # schematic fallback
                lon0, lat0, lon1, lat1 = STATE_BBOX[m["state"]]
                m["_pts"] = np.column_stack([
                    rng.normal((lon0 + lon1) / 2, 0.30, m["n_villages"]),
                    rng.normal((lat0 + lat1) / 2, 0.30, m["n_villages"])])
                m["geometry_source"] = "schematic"
            continue

        ring = members[0]["_ring"]
        total = sum(m["n_villages"] for m in members)
        pts = _sample_in_polygon(ring, total, rng)

        if len(members) == 1:
            members[0]["_pts"] = pts
            members[0]["geometry_source"] = "real_boundary"
            continue

        # Several present-day districts share this historical polygon: carve it up.
        seeds = pts[rng.choice(len(pts), size=len(members), replace=False)]
        for _ in range(12):                                     # Lloyd relaxation
            lab = _assign_capacitated(pts, seeds, [m["n_villages"] for m in members])
            new_seeds = np.array([pts[lab == j].mean(0) if (lab == j).any() else seeds[j]
                                  for j in range(len(members))])
            if np.allclose(new_seeds, seeds, atol=1e-4):
                seeds = new_seeds
                break
            seeds = new_seeds
        lab = _assign_capacitated(pts, seeds, [m["n_villages"] for m in members])
        for j, m in enumerate(members):
            m["_pts"] = pts[lab == j]
            m["geometry_source"] = "parent_boundary"

    # ---- pass 3: emit districts, blocks, villages -----------------------------
    d_rows, b_rows, v_rows = [], [], []
    for m in plan:
        pts = m["_pts"]
        nv = len(pts)
        nb = min(m["n_blocks"], nv)
        clon, clat = float(pts[:, 0].mean()), float(pts[:, 1].mean())

        d_rows.append({
            **{k: m[k] for k in ("state", "state_code", "district", "district_id", "zone",
                                 "crop_system", "mech_tier", "irrigation_tier",
                                 "residue_belt", "net_sown_ha", "rural_population")},
            "n_blocks": nb, "n_villages": nv, "lon": clon, "lat": clat,
            "geometry_source": m["geometry_source"],
            "code_source": "synthetic_district_code",
            "provenance": "real" if m["geometry_source"] != "schematic" else "simulated",
        })

        # Blocks: nearest-seed clustering keeps each block a contiguous neighbourhood,
        # which the spatial-lag features and dealer territories both depend on.
        seeds = pts[rng.choice(nv, size=nb, replace=False)]
        blk_idx = ((pts[:, None, :] - seeds[None, :, :]) ** 2).sum(-1).argmin(1)

        for b in range(nb):
            b_rows.append({
                "state": m["state"], "district": m["district"], "district_id": m["district_id"],
                "block_id": f"{m['district_id']}B{b + 1:03d}",
                "block": f"{m['district']} Block {b + 1}",
                "lon": float(seeds[b][0]), "lat": float(seeds[b][1]),
                "n_villages": int((blk_idx == b).sum()),
                "code_source": "synthetic", "provenance": "simulated",
            })

        area = rng.lognormal(math.log(max(m["net_sown_ha"] / nv, 20.0)), 0.55, nv)
        pop = np.maximum(80, rng.lognormal(math.log(max(m["rural_population"] / nv, 300.0)), 0.75, nv))
        for j in range(nv):
            v_rows.append({
                "state": m["state"], "district": m["district"], "district_id": m["district_id"],
                "block_id": f"{m['district_id']}B{int(blk_idx[j]) + 1:03d}",
                "village_id": f"{m['district_id']}V{j + 1:05d}",
                "village": f"{m['district']} V{j + 1:05d}",
                "lon": float(pts[j][0]), "lat": float(pts[j][1]),
                "geo_area_ha": float(area[j]), "rural_population": float(pop[j]),
                "households": float(max(20, pop[j] / rng.uniform(4.6, 5.6))),
                "code_source": "synthetic", "provenance": "simulated",
            })

    dist_df, blk_df, vil_df = pd.DataFrame(d_rows), pd.DataFrame(b_rows), pd.DataFrame(v_rows)

    vil_df["net_sown_ha"] = vil_df["geo_area_ha"] * 0.62
    scale = (dist_df.set_index("district_id")["net_sown_ha"]
             / vil_df.groupby("district_id")["net_sown_ha"].sum())
    vil_df["net_sown_ha"] *= vil_df["district_id"].map(scale)

    src = dist_df["geometry_source"].value_counts().to_dict()
    LOG.info("geometry: %s", src)
    LOG.info("spine: %d districts, %d blocks, %d villages",
             len(dist_df), len(blk_df), len(vil_df))

    write_table(dist_df, CURATED / "geo_districts.parquet")
    write_table(blk_df, CURATED / "geo_blocks.parquet")
    write_table(vil_df, CURATED / "geo_villages.parquet")

    real = int(len(dist_df) - src.get("schematic", 0))
    Manifest.record(FetchRecord(
        source="geo_spine", mode="real" if real else "synthetic", rows=len(vil_df),
        provenance="real" if real == len(dist_df) else "allocated",
        coverage_pct=round(100.0 * real / len(dist_df), 1),
        vintage="GADM district boundaries + Census 2011 size anchors",
    ))
    return {"districts": dist_df, "blocks": blk_df, "villages": vil_df}


if __name__ == "__main__":
    build()
