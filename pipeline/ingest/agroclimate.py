"""Real agro-climatic district profile: temperature, rainfall, crop-mix.

Three real EY-secondary sources, all district level, filtered to the pilot states:
  data/raw/agroclimate/temperature.csv  IMD station temperature (min/max), 1969-2020
  data/raw/agroclimate/rainfall.pdf     IMD district rainfall (seasonal actual/normal/dep)
  data/raw/agroclimate/des_crop.xlsx    DES district crop Area/Production/Yield 2024-25

Output: data/marts/agroclimate.parquet -- one row per pilot district with real climate
and crop-mix features. Temperature stations cover ~1/4 of districts, so gaps are filled
from the geographically nearest station-backed district (provenance drops to 'allocated'
for those). Rainfall and crop cover every district. Feeds the Define stage and the
archetype re-fit.
"""
from __future__ import annotations

import re
import time

import numpy as np
import pandas as pd

from pipeline.common import RAW, MARTS, CURATED, read_table, write_table, log, Manifest, FetchRecord
from pipeline.transform.geo_spine import ALIASES, _norm

LOG = log("agroclimate")
PILOT = ["Punjab", "Madhya Pradesh", "Maharashtra"]
SRC = RAW / "agroclimate"
KEY_CROPS = ["rice", "wheat", "cotton", "soybean", "sugarcane", "maize",
             "gram", "bajra", "jowar", "groundnut"]


def _lut(districts: pd.DataFrame) -> dict[tuple[str, str], str]:
    """{(norm state, norm district-or-alias): district_id} across the pilot states."""
    out: dict[tuple[str, str], str] = {}
    for _, r in districts.iterrows():
        st, name, did = _norm(r["state"]), _norm(r["district"]), r["district_id"]
        out[(st, name)] = did
        for alias in ALIASES.get(name, []):
            out.setdefault((st, _norm(alias)), did)
    return out


def _match(state: str, district: str, lut) -> str | None:
    st, dt = _norm(state), _norm(district)
    if (st, dt) in lut:
        return lut[(st, dt)]
    for (gs, gd), did in lut.items():             # loose contains, same state
        if gs == st and (dt in gd or gd in dt) and len(dt) >= 4:
            return did
    return None


# ------------------------------------------------------------------ temperature

def _temperature(lut) -> pd.DataFrame:
    t = pd.read_csv(SRC / "temperature.csv")
    t["state_name"] = t["state_name"].str.strip()
    t = t[t["state_name"].str.lower().isin([s.lower() for s in PILOT])].copy()
    t["month"] = pd.to_datetime(t["date"], format="mixed", errors="coerce").dt.month
    t["val"] = pd.to_numeric(t["average_temperature"], errors="coerce")

    grp = ["state_name", "district_name"]
    mn = t[t.parameter.str.contains("min")].groupby(grp)["val"].mean()
    mx = t[t.parameter.str.contains("max")].groupby(grp)["val"].mean()
    monthly = t.groupby(grp + ["month"])["val"].mean().groupby(grp).agg(lambda s: s.max() - s.min())

    df = pd.DataFrame({"mean_min_temp": mn, "mean_max_temp": mx,
                       "temp_seasonality": monthly}).reset_index()
    df["mean_temp"] = (df.mean_min_temp + df.mean_max_temp) / 2
    df["district_id"] = [_match(s, d, lut) for s, d in zip(df.state_name, df.district_name)]
    df = df.dropna(subset=["district_id"]).drop_duplicates("district_id")
    return df[["district_id", "mean_temp", "mean_min_temp", "mean_max_temp", "temp_seasonality"]]


def _fill_nearest(base: pd.DataFrame, districts: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Fill missing district rows from the nearest district that has data (by centroid)."""
    have = base.merge(districts[["district_id", "lon", "lat"]], on="district_id")
    out = districts[["district_id", "lon", "lat"]].merge(base, on="district_id", how="left")
    miss = out[out[cols[0]].isna()]
    src = have[["lon", "lat"] + cols].to_numpy()
    for i, r in miss.iterrows():
        d = (src[:, 0] - r.lon) ** 2 + (src[:, 1] - r.lat) ** 2
        out.loc[i, cols] = src[int(d.argmin()), 2:]
    out["temp_is_allocated"] = ~out["district_id"].isin(base["district_id"])
    return out.drop(columns=["lon", "lat"])


# ------------------------------------------------------------------ rainfall

def _rainfall(lut) -> pd.DataFrame:
    from pypdf import PdfReader
    reader = PdfReader(SRC / "rainfall.pdf")
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    rows = []
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^(\d+)\s+([A-Za-z][A-Za-z .&'\-]+?)\s+([\d.]+.*)$", s)
        if not m:
            continue
        name, tail = m.group(2).strip(), m.group(3)
        pcts = re.findall(r"(-?\d+)%", tail)
        # strip the %-departure tokens first, else their digits pollute the float list
        tail_no_pct = re.sub(r"-?\d+%", " ", tail)
        floats = re.findall(r"\d+\.\d+|\b\d+\b", tail_no_pct)
        if len(floats) < 2:
            continue
        # columns are [daily actual, daily normal, seasonal actual, seasonal normal];
        # the seasonal normal (cumulative-period climatology) is the last float.
        seasonal_normal = float(floats[-1])
        dep = float(pcts[-1]) if pcts else np.nan
        rows.append((name, seasonal_normal, dep))

    df = pd.DataFrame(rows, columns=["name", "rain_normal_mm", "rain_departure_pct"])
    # match against all pilot districts regardless of the PDF's state hierarchy
    by_name: dict[str, str] = {}
    for (st, nm), did in lut.items():
        by_name.setdefault(nm, did)
    df["district_id"] = [by_name.get(_norm(n)) for n in df.name]
    df = df.dropna(subset=["district_id"]).drop_duplicates("district_id")
    return df[["district_id", "rain_normal_mm", "rain_departure_pct"]]


# ------------------------------------------------------------------ crop mix

def _crop(lut) -> pd.DataFrame:
    raw = pd.read_excel(SRC / "des_crop.xlsx", sheet_name="Data Sheet", skiprows=6)
    raw.columns = ["state", "district", "crop", "season", "area", "production", "yield"]
    raw[["state", "district", "crop"]] = raw[["state", "district", "crop"]].ffill()
    d = raw[raw["season"].astype(str).str.strip().str.lower() == "total"].copy()
    d = d[d["state"].isin(PILOT)]
    # drop aggregate/category rows -- keep genuine leaf crops only. DES mixes in roll-ups
    # ("Total Food Grains", "Nutri/Coarse Cereals", "Shree Anna", "Total Oilseeds", ...).
    AGG_TOKENS = ("total", "cereal", "pulse", "oilseed", "grain", "nutri",
                  "coarse", "shree anna", "non-food", "misc")
    cl = d["crop"].astype(str).str.strip().str.lower()
    d = d[~cl.apply(lambda c: any(tok in c for tok in AGG_TOKENS))]
    d["area"] = pd.to_numeric(d["area"], errors="coerce").fillna(0)
    d["crop_l"] = d["crop"].astype(str).str.strip().str.lower()
    d["district_id"] = [_match(s, dt, lut) for s, dt in zip(d.state, d.district)]
    d = d.dropna(subset=["district_id"])

    out = []
    for did, g in d.groupby("district_id"):
        tot = g["area"].sum()
        if tot <= 0:
            continue
        by_crop = g.groupby("crop_l")["area"].sum().sort_values(ascending=False)
        row = {"district_id": did, "total_crop_area_lha": round(float(tot), 3),
               "top_crops": ", ".join(by_crop.head(3).index)}
        for c in KEY_CROPS:
            row[f"crop_{c}_share"] = round(float(by_crop.get(c, 0.0) / tot), 4)
        out.append(row)
    return pd.DataFrame(out)


# ------------------------------------------------------------------ build

def build() -> None:
    t0 = time.time()
    districts = read_table(CURATED / "geo_districts.parquet")[
        ["district_id", "district", "state", "lon", "lat"]]
    lut = _lut(districts)

    temp = _temperature(lut)
    LOG.info("temperature: %d/%d districts have a station", len(temp), len(districts))
    temp = _fill_nearest(temp, districts,
                         ["mean_temp", "mean_min_temp", "mean_max_temp", "temp_seasonality"])

    rain = _rainfall(lut)
    LOG.info("rainfall: %d/%d districts matched from IMD PDF", len(rain), len(districts))

    crop = _crop(lut)
    LOG.info("crop-mix: %d/%d districts from DES", len(crop), len(districts))

    ac = (districts[["district_id", "district", "state"]]
          .merge(temp, on="district_id", how="left")
          .merge(rain, on="district_id", how="left")
          .merge(crop, on="district_id", how="left"))

    # provenance: real where every layer is a direct district match; allocated if the
    # temperature was borrowed from a neighbour (the only interpolated layer).
    ac["provenance"] = np.where(ac["temp_is_allocated"].fillna(True), "allocated", "real")
    for c in ["rain_normal_mm", "rain_departure_pct", "total_crop_area_lha"]:
        ac[c] = pd.to_numeric(ac[c], errors="coerce")
    write_table(ac, MARTS / "agroclimate.parquet")

    Manifest.record(FetchRecord(
        source="agroclimate", mode="real", rows=len(ac), provenance="real",
        coverage_pct=round(100 * ac["rain_normal_mm"].notna().mean(), 1),
        vintage="IMD temp 1969-2020 + IMD rainfall 2026 + DES crop 2024-25",
        elapsed_s=round(time.time() - t0, 2)))
    LOG.info("agroclimate: %d districts | temp real for %d | rain for %d | crop for %d",
             len(ac), int((~ac.temp_is_allocated.fillna(True)).sum()),
             int(ac.rain_normal_mm.notna().sum()), int(ac.total_crop_area_lha.notna().sum()))


if __name__ == "__main__":
    build()
