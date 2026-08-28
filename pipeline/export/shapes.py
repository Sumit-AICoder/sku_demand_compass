"""Map geometry for the drill-down: India -> state -> district -> block -> village.

The source geojson carries 784,623 coordinates across 594 districts. Shipping that to a
browser would be absurd, so each zoom level gets its own simplification budget: the
India view only needs enough detail to read as India, while a single district can afford
a much finer outline because there is only one of it.

Simplification is Ramer-Douglas-Peucker, written here rather than pulled in, because the
only alternative in the venv would be adding a geometry stack for one function.

Blocks have no real boundaries -- they are constructed in geo_spine -- so instead of
inventing polygons we emit Voronoi cells around the block centroids and let the browser
clip them to the true district outline with an SVG clipPath. The territories are then
honest about their origin: real district edge, derived interior.
"""
from __future__ import annotations

import json

import numpy as np
from scipy.spatial import Voronoi

from pipeline.common import CURATED, MARTS, RAW, read_table, log

LOG = log("shapes")

# Current Indian administrative boundaries: 36 states/UTs, and Jammu & Kashmir and
# Ladakh drawn at their full official extent. The older GADM file only depicts the
# Indian-administered portion of J&K, which is not the correct depiction for a map
# published in India, and it also predates several district reorganisations.
SRC = RAW / "geo" / "india_official.geojson"
SRC_FALLBACK = RAW / "geo" / "india_district.geojson"
OUT = MARTS / "shapes"

PILOT = {"Punjab", "Madhya Pradesh", "Maharashtra"}

# degrees of tolerance per level, and the smallest ring worth keeping
LEVELS = {
    # The current-boundary source is already generalised (25k coordinates for the whole
    # country), so the India view needs only light thinning -- the aggressive tolerance
    # tuned for GADM would erode real coastline here.
    "india":    {"tol": 0.020, "min_ring_area": 0.010},
    "state":    {"tol": 0.012, "min_ring_area": 0.004},
    "district": {"tol": 0.004, "min_ring_area": 0.0008},
}


# ------------------------------------------------------------------ simplify

def rdp(points: np.ndarray, tol: float) -> np.ndarray:
    """Ramer-Douglas-Peucker, iterative so a long coastline cannot blow the stack."""
    n = len(points)
    if n < 4:
        return points
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = points[i], points[j]
        seg = b - a
        L = np.hypot(*seg)
        pts = points[i + 1:j]
        if L < 1e-12:
            d = np.hypot(pts[:, 0] - a[0], pts[:, 1] - a[1])
        else:
            # perpendicular distance from each point to segment a-b
            d = np.abs(seg[0] * (a[1] - pts[:, 1]) - (a[0] - pts[:, 0]) * seg[1]) / L
        k = int(d.argmax())
        if d[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack.append((i, m))
            stack.append((m, j))
    return points[keep]


def _ring_area(r: np.ndarray) -> float:
    x, y = r[:, 0], r[:, 1]
    return abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0


def simplify_geometry(geom: dict, tol: float, min_area: float) -> list[list[list[float]]]:
    """Return a list of simplified outer rings, dropping slivers and small islands."""
    t, c = geom.get("type"), geom.get("coordinates")
    polys = [c] if t == "Polygon" else (c if t == "MultiPolygon" else [])
    out = []
    for poly in polys:
        if not poly:
            continue
        ring = np.asarray(poly[0], dtype=float)      # outer ring only
        if len(ring) < 4 or _ring_area(ring) < min_area:
            continue
        s = rdp(ring, tol)
        if len(s) >= 4:
            out.append([[round(float(x), 4), round(float(y), 4)] for x, y in s])
    return out


# ------------------------------------------------------------------ build

def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    feats = json.loads(SRC.read_text())["features"]
    fallback = (json.loads(SRC_FALLBACK.read_text())["features"]
                if SRC_FALLBACK.exists() else [])

    districts = read_table(CURATED / "geo_districts.parquet")
    totals = read_table(MARTS / "district_totals.parquet")[
        ["district_id", "potential_units_yr"]]
    districts = districts.merge(totals, on="district_id", how="left")

    from pipeline.transform.geo_spine import _match_geometry, _norm
    geo_index = {}
    for source in (feats, fallback):            # current boundaries win
        for f in source:
            p = {k.lower(): v for k, v in f["properties"].items()}
            st = p.get("st_nm") or p.get("name_1")
            dt = p.get("district") or p.get("name_2")
            if st and dt:
                geo_index.setdefault((_norm(st), _norm(dt)), f)

    # ---- India: every state, so the country reads as itself -------------------
    cfg = LEVELS["india"]
    by_state: dict[str, list] = {}
    for f in feats:
        st = f["properties"].get("st_nm")
        if not st:
            continue
        rings = simplify_geometry(f["geometry"], cfg["tol"], cfg["min_ring_area"])
        if rings:
            by_state.setdefault(st, []).extend(rings)
    india = {"level": "india",
             "features": [{"id": st, "name": st, "pilot": st in PILOT, "rings": r}
                          for st, r in sorted(by_state.items())]}
    _write("india.json", india)

    # ---- each pilot state: its districts --------------------------------------
    cfg = LEVELS["state"]
    for state, g in districts.groupby("state"):
        out = []
        for _, d in g.iterrows():
            key, _hit = _match_geometry(
                {k: {"ring": None} for k in geo_index}, state, d["district"])
            feat = geo_index.get(key) if key else None
            if feat is None:
                continue
            rings = simplify_geometry(feat["geometry"], cfg["tol"], cfg["min_ring_area"])
            if not rings:
                continue
            out.append({"id": d["district_id"], "name": d["district"],
                        "state": state, "zone": d["zone"],
                        "shared": False, "rings": rings})
        # districts carved from a shared parent get the same outline; flag it so the UI
        # can say so rather than implying a boundary we do not have
        seen: dict[str, int] = {}
        for o in out:
            k = json.dumps(o["rings"][0][:3])
            seen[k] = seen.get(k, 0) + 1
        for o in out:
            o["shared"] = seen[json.dumps(o["rings"][0][:3])] > 1
        _write(f"state_{_slug(state)}.json", {"level": "state", "state": state,
                                              "features": out})

    # ---- each district: block Voronoi + the true outline ----------------------
    _build_blocks(districts, geo_index)
    LOG.info("shapes written to %s", OUT)


def _build_blocks(districts, geo_index) -> None:
    from pipeline.transform.geo_spine import _match_geometry
    blocks = read_table(CURATED / "geo_blocks.parquet")
    cfg = LEVELS["district"]
    n = 0
    for did, g in blocks.groupby("district_id"):
        row = districts[districts.district_id == did].iloc[0]
        key, _ = _match_geometry({k: {"ring": None} for k in geo_index},
                                 row["state"], row["district"])
        feat = geo_index.get(key) if key else None
        outline = simplify_geometry(feat["geometry"], cfg["tol"], cfg["min_ring_area"]) \
            if feat else []

        pts = g[["lon", "lat"]].to_numpy(float)
        # Clip cells to the district's bounding box before shipping them. Unbounded
        # Voronoi regions are enormous, and although the browser clips them to the true
        # outline, the map still has to FIT them -- leaving the district a speck in the
        # middle of empty space.
        bbox = _bbox(outline) if outline else _bbox([pts.tolist()])
        cells = _voronoi_cells(pts, bbox) if len(g) >= 3 else []
        _write(f"district_{did}.json", {
            "level": "district", "district_id": did, "district": row["district"],
            "state": row["state"], "outline": outline,
            "features": [{"id": b, "name": nm, "cell": c}
                         for b, nm, c in zip(g["block_id"], g["block"], cells)]
            if cells else
            [{"id": b, "name": nm, "lon": float(x), "lat": float(y), "cell": None}
             for b, nm, x, y in zip(g["block_id"], g["block"], g["lon"], g["lat"])],
        })
        n += 1
    LOG.info("block geometry for %d districts", n)


def _bbox(rings, pad_frac: float = 0.03):
    a = np.vstack([np.asarray(r, dtype=float) for r in rings])
    x0, y0, x1, y1 = a[:, 0].min(), a[:, 1].min(), a[:, 0].max(), a[:, 1].max()
    px, py = (x1 - x0) * pad_frac, (y1 - y0) * pad_frac
    return (x0 - px, y0 - py, x1 + px, y1 + py)


def _clip_to_box(poly: np.ndarray, box) -> np.ndarray:
    """Sutherland-Hodgman against an axis-aligned rectangle.

    A rectangle is convex, so the classic algorithm applies directly -- the exact,
    non-convex district edge is left to the browser's clipPath.
    """
    x0, y0, x1, y1 = box
    edges = [(lambda p: p[0] >= x0, 0, x0), (lambda p: p[0] <= x1, 0, x1),
             (lambda p: p[1] >= y0, 1, y0), (lambda p: p[1] <= y1, 1, y1)]
    out = list(poly)
    for inside, axis, val in edges:
        if not out:
            return np.empty((0, 2))
        nxt = []
        for i in range(len(out)):
            a, b = out[i], out[(i + 1) % len(out)]
            ain, bin_ = inside(a), inside(b)
            if ain:
                nxt.append(a)
            if ain != bin_:
                d = b[axis] - a[axis]
                t = 0.0 if abs(d) < 1e-15 else (val - a[axis]) / d
                nxt.append(a + t * (b - a))
        out = nxt
    return np.asarray(out) if out else np.empty((0, 2))


def _voronoi_cells(points: np.ndarray, box=None) -> list[list[list[float]]]:
    """Voronoi cells around block centroids.

    Distant sentinel points bound the diagram so the outer cells are finite; the browser
    clips the result to the real district outline, so the unbounded regions never show.
    """
    span = max(float(np.ptp(points[:, 0])), float(np.ptp(points[:, 1])), 0.5)
    c = points.mean(0)
    far = np.array([[c[0] - 8 * span, c[1] - 8 * span], [c[0] + 8 * span, c[1] - 8 * span],
                    [c[0] - 8 * span, c[1] + 8 * span], [c[0] + 8 * span, c[1] + 8 * span]])
    try:
        vor = Voronoi(np.vstack([points, far]))
    except Exception:                                              # noqa: BLE001
        return []
    cells = []
    for i in range(len(points)):
        region = vor.regions[vor.point_region[i]]
        if not region or -1 in region:
            cells.append(None)
            continue
        poly = vor.vertices[region]
        if box is not None:
            poly = _clip_to_box(poly, box)
            if len(poly) < 3:
                cells.append(None)
                continue
        cells.append([[round(float(x), 4), round(float(y), 4)] for x, y in poly])
    return cells if all(c is not None for c in cells) else []


def _slug(s: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in s)


def _write(name: str, obj: dict) -> None:
    p = OUT / name
    p.write_text(json.dumps(obj, separators=(",", ":")))
    return p


if __name__ == "__main__":
    build()
