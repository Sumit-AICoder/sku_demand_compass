"""Real soil, climate and growing-season data per agro-ecological sub-region.

`inputs/query.kmz` is the NBSS/ICAR **Agro-Ecological Sub-Region** layer: 59 polygons
covering India, each carrying a published soil class, climate and length of growing period.

Soil was the last field on the Define panel with no real source -- the village-level
`soil_texture` in the marts is simulated from a legacy zone vocabulary with no link to NARP,
so it could contradict the zone shown beside it. This replaces it with published data.

**The codes look joinable and are not.** AESR and our NARP sub-zone table share the same
numbering *shape* (2.3, 4.2, 10.3) and the leading number does agree -- but the decimal part
is offset for several: AESR 5.3 is the Coastal Kathiawar Peninsula while our 5.3 is the
Malwa plateau, and our 10.2/10.3 sit one place behind theirs. Joining on the string would
have put coastal soil on landlocked Malwa. So every district is placed by **where it
actually is**: point-in-polygon of its centroid against the AESR geometry.

The KMZ is a zipped KML whose attributes live in an HTML table inside each Placemark's
description, which is why the parsing reads like scraping.
"""
from __future__ import annotations

import html
import re
import zipfile

import numpy as np
import pandas as pd

from pipeline.common import CURATED, MARTS, ROOT, log, read_table, write_table

LOG = log("aesr")

SOURCE = ROOT / "inputs" / "query.kmz"
FIELDS = {"Code": "aesr_code", "Physiographic region": "region",
          "Sub Physiographic region": "sub_region", "Soil Type": "soil_type",
          "Climate": "climate", "lgp": "lgp_days", "area_sqkm": "area_sqkm"}


def _rings(block: str) -> list[list[tuple[float, float]]]:
    """Every outer ring of one Placemark, as (lon, lat) pairs.

    Inner rings (holes) are ignored: an AESR hole is a rounding artefact at this scale, and
    treating one as solid misplaces a district by less than the centroid approximation
    already does.
    """
    out = []
    for ring in re.findall(r"<outerBoundaryIs>.*?<coordinates>(.*?)</coordinates>",
                           block, re.S):
        pts = []
        for tok in ring.split():
            lon, lat, *_ = tok.split(",")
            pts.append((float(lon), float(lat)))
        if len(pts) > 3:
            out.append(pts)
    return out


def _contains(rings: list[list[tuple[float, float]]], lon: float, lat: float) -> bool:
    """Ray casting: is the point inside any ring of this sub-region?"""
    for ring in rings:
        xs = np.array([p[0] for p in ring])
        ys = np.array([p[1] for p in ring])
        if lon < xs.min() or lon > xs.max() or lat < ys.min() or lat > ys.max():
            continue                                   # bbox reject -- most rings, cheaply
        x2, y2 = np.roll(xs, -1), np.roll(ys, -1)
        straddles = (ys > lat) != (y2 > lat)
        with np.errstate(divide="ignore", invalid="ignore"):
            xin = (x2 - xs) * (lat - ys) / (y2 - ys) + xs
        if int(np.count_nonzero(straddles & (lon < xin))) % 2 == 1:
            return True
    return False


def _unescape(s: str) -> str:
    """Entities in this KML are escaped twice -- the attribute table is HTML inside a CDATA
    block inside XML -- so `&amp;lt; 60 days` needs two passes to become `< 60 days`."""
    prev = None
    while s != prev:
        prev, s = s, html.unescape(s)
    return s.strip()


def _cells(block: str) -> list[str]:
    """The label/value cells of one Placemark's attribute table, unescaped."""
    return [_unescape(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", block, re.S)]


def parse(path=SOURCE) -> pd.DataFrame:
    """One row per AESR sub-region."""
    if not path.exists():
        raise FileNotFoundError(f"{path} not found -- the AESR layer is an input, not generated")
    with zipfile.ZipFile(path) as z:
        kml = z.read("doc.kml").decode("utf8", errors="replace")

    rows = []
    for block in kml.split("<Placemark")[1:]:
        cells = _cells(block)
        rec = {}
        for i, c in enumerate(cells[:-1]):
            if c in FIELDS:
                rec[FIELDS[c]] = cells[i + 1]
        if rec.get("aesr_code"):
            rec["rings"] = _rings(block)
            rows.append(rec)

    df = pd.DataFrame(rows).drop_duplicates("aesr_code")
    # "90-120 days" / "&gt;300 days" -> a tidy band, and a numeric midpoint to sort on
    df["lgp_days"] = df["lgp_days"].str.replace("days", "", case=False).str.strip()
    nums = df["lgp_days"].str.findall(r"\d+")
    df["lgp_mid"] = nums.map(lambda xs: sum(int(x) for x in xs) / len(xs) if xs else None)
    df["area_sqkm"] = pd.to_numeric(df.get("area_sqkm"), errors="coerce")
    df["provenance"] = "real"
    return df.sort_values("aesr_code").reset_index(drop=True)


def assign_districts(sub: pd.DataFrame) -> pd.DataFrame:
    """Place every district in the AESR sub-region its centroid falls inside.

    A centroid is a coarse stand-in for a district, and a handful sit just outside every
    polygon (coastal districts whose centroid lands offshore, mostly). Those fall back to
    the nearest sub-region centre rather than being dropped -- a district with no soil row
    would show a blank on the Define panel, which is worse than a near-neighbour's value.
    """
    d = read_table(CURATED / "geo_districts.parquet")[
        ["district_id", "district", "state", "lon", "lat"]].copy()

    centres = {r.aesr_code: (np.mean([p[0] for ring in r.rings for p in ring]),
                             np.mean([p[1] for ring in r.rings for p in ring]))
               for r in sub.itertuples() if r.rings}

    codes, how = [], []
    for r in d.itertuples():
        hit = next((x.aesr_code for x in sub.itertuples()
                    if x.rings and _contains(x.rings, r.lon, r.lat)), None)
        if hit is None and centres:
            hit = min(centres, key=lambda c: (centres[c][0] - r.lon) ** 2
                                             + (centres[c][1] - r.lat) ** 2)
            how.append("nearest")
        else:
            how.append("inside")
        codes.append(hit)

    d["aesr_code"], d["match"] = codes, how
    cols = ["aesr_code", "region", "sub_region", "soil_type", "climate", "lgp_days", "lgp_mid"]
    out = d.merge(sub[cols], on="aesr_code", how="left")
    out["provenance"] = "real"
    return out


def build() -> pd.DataFrame:
    sub = parse()
    LOG.info("AESR: %d sub-regions, %d distinct soil classes, %d climates",
             len(sub), sub["soil_type"].nunique(), sub["climate"].nunique())
    write_table(sub.drop(columns=["rings"]), MARTS / "aesr_subzones.parquet")

    dist = assign_districts(sub)
    inside = int((dist["match"] == "inside").sum())
    LOG.info("districts placed: %d/%d inside a polygon, %d by nearest centre",
             inside, len(dist), len(dist) - inside)
    LOG.info("  soil classes across the pilot: %s",
             ", ".join(f"{k} ({v})" for k, v in
                       dist["soil_type"].value_counts().head(6).items()))
    write_table(dist, MARTS / "district_aesr.parquet")
    return dist


if __name__ == "__main__":
    out = build()
    # Runnable checks: every district must end up with a soil class (a blank row on the
    # Define panel is the failure this guards), and the placement must be geographically
    # sane -- Punjab is not a black-soil state and Maharashtra is not an alluvial one.
    assert out["soil_type"].notna().all(), "a district came back with no soil class"
    by_state = out.groupby("state")["soil_type"].agg(lambda s: s.mode().iloc[0])
    assert "Black" in by_state["Maharashtra"], f"Maharashtra reads {by_state['Maharashtra']}"
    assert "Black" not in by_state["Punjab"], f"Punjab reads {by_state['Punjab']}"
    print(by_state.to_string())
    print()
    print(out[["district", "state", "aesr_code", "soil_type", "lgp_days", "match"]]
          .head(12).to_string(index=False))
