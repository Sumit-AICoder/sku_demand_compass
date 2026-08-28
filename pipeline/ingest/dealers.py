"""Real dealer-network coverage from the ITL/EY dealer databases.

Two files ship as raw inputs under data/raw/dealers/:
  implements.xlsx : OEM, Dealer name, Address, State/UT, PIN   -- includes Sonalika (own)
  tractors.csv    : OEM, Dealer name, Address, State/UT        -- competitor-only, no Sonalika

Each dealer is geocoded to a pilot district by matching a known district name inside
the address. Indian dealer addresses almost always carry the district as the comma
segment just before the state ("..., Harda, Madhya Pradesh - 461331"), so we look
there first, then fall back to scanning the whole address. Old district names
(East Nimar -> Khandwa) are resolved through geo_spine's alias table.

This is REAL data. The only modelled step is the name match; its hit-rate is recorded
in the manifest as coverage_pct so the UI can be honest about it.
"""
from __future__ import annotations

import re
import time

import pandas as pd

from pipeline.common import RAW, MARTS, CURATED, read_table, write_table, log, Manifest, FetchRecord
from pipeline.transform.geo_spine import ALIASES, _norm

LOG = log("dealers")

PILOT = ["Punjab", "Madhya Pradesh", "Maharashtra"]
SRC = RAW / "dealers"


def _own(oem: str) -> bool:
    """Is this Sonalika's own network? (International Tractors Ltd brand.)"""
    o = str(oem).lower()
    return "sonalika" in o or "international tractor" in o or o.strip() == "itl"


def _district_lookup(state: str, districts: pd.DataFrame) -> dict[str, str]:
    """Map every normalised district name AND known alias -> district_id, for one state."""
    sub = districts[districts.state == state]
    out: dict[str, str] = {}
    for _, r in sub.iterrows():
        did, name = r["district_id"], r["district"]
        out[_norm(name)] = did
        # geo_spine.ALIASES maps current-name -> [historical/spelling variants]
        for alias in ALIASES.get(_norm(name), []):
            out.setdefault(_norm(alias), did)
    return out


def _match_district(address: str, state: str, lut: dict[str, str]) -> str | None:
    """Return district_id for an address, or None if no known district name is found."""
    if not isinstance(address, str) or not address.strip():
        return None
    segs = [s.strip() for s in re.split(r"[,\-]", address) if s.strip()]
    norm_state = _norm(state)
    # Prefer the segment(s) immediately before the state name -- that is the district.
    ordered = []
    for i, s in enumerate(segs):
        if _norm(s) == norm_state and i > 0:
            ordered = [segs[i - 1]] + ([segs[i - 2]] if i > 1 else [])
            break
    ordered += segs  # then fall back to scanning everything
    for seg in ordered:
        hit = lut.get(_norm(seg))
        if hit:
            return hit
    # last resort: substring scan of the whole normalised address
    naddr = _norm(address)
    for name, did in sorted(lut.items(), key=lambda kv: -len(kv[0])):
        if len(name) >= 5 and name in naddr:
            return did
    return None


def _load(path, product_line: str) -> pd.DataFrame:
    df = pd.read_excel(path) if path.suffix == ".xlsx" else pd.read_csv(path)
    df.columns = [str(c).strip() for c in df.columns]
    state_col = next(c for c in df.columns if "state" in c.lower())
    addr_col = next(c for c in df.columns if "address" in c.lower())
    oem_col = next(c for c in df.columns if c.lower() == "oem")
    df = df[df[state_col].isin(PILOT)].copy()
    df["product_line"] = product_line
    df["state"] = df[state_col]
    df["address"] = df[addr_col]
    df["oem"] = df[oem_col]
    df["is_own"] = df["oem"].map(_own)
    return df[["product_line", "state", "address", "oem", "is_own"]]


def build() -> None:
    t0 = time.time()
    districts = read_table(CURATED / "geo_districts.parquet")[["district_id", "district", "state"]]
    luts = {s: _district_lookup(s, districts) for s in PILOT}

    frames = []
    for fname, line in [("implements.xlsx", "implements"), ("tractors.csv", "tractors")]:
        raw = _load(SRC / fname, line)
        raw["district_id"] = [
            _match_district(a, s, luts[s]) for a, s in zip(raw["address"], raw["state"])
        ]
        matched = raw["district_id"].notna().mean() * 100
        LOG.info("%s: %d dealers in pilot states, %.0f%% geocoded to a district (own=%d)",
                 line, len(raw), matched, int(raw["is_own"].sum()))
        Manifest.record(FetchRecord(
            source=f"dealers_{line}", mode="real", rows=len(raw), provenance="real",
            coverage_pct=round(float(matched), 1),
            vintage="dealer locator scrape (tractorjunction / OEM locators)",
            elapsed_s=round(time.time() - t0, 2)))
        frames.append(raw)

    alld = pd.concat(frames, ignore_index=True)

    # district x product_line aggregate: own vs competitor counts + distinct rivals
    placed = alld[alld["district_id"].notna()]
    agg = (placed.groupby(["product_line", "district_id"])
           .agg(own_dealers=("is_own", "sum"),
                total_dealers=("is_own", "size"),
                n_oems=("oem", "nunique"))
           .reset_index())
    agg["own_dealers"] = agg["own_dealers"].astype(int)
    agg["competitor_dealers"] = agg["total_dealers"] - agg["own_dealers"]
    agg = agg.merge(districts, on="district_id", how="left")
    agg["provenance"] = "real"
    write_table(agg, MARTS / "dealer_network.parquet")

    # per-OEM district presence, for the competition angle
    per_oem = (placed.groupby(["product_line", "oem", "district_id"]).size()
               .reset_index(name="dealers"))
    per_oem["provenance"] = "real"
    write_table(per_oem, MARTS / "dealer_by_oem.parquet")

    LOG.info("dealer network: %d district-line rows | %d districts with a Sonalika implement dealer",
             len(agg), int((agg[(agg.product_line == "implements")].own_dealers > 0).sum()))


if __name__ == "__main__":
    build()
