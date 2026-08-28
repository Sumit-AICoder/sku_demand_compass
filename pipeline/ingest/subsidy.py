"""Real equipment-subsidy rates by state, from the EY Subsidies Master.

The "Combined" sheet carries equipment-wise subsidy fractions per state. Punjab and
Maharashtra are populated in detail (real); Madhya Pradesh has no sheet, so it falls back
to the national SMAM general rate of 40% for any SMAM-eligible implement (allocated).

Equipment names are mapped to the app's SKUs explicitly -- a hand-checked table beats
fuzzy matching for a client-facing number. SKUs with no subsidy line (trolley, water
tanker, PTO pump, GPS kit) are simply absent: they are genuinely not subsidised here.

Output: data/marts/subsidy.parquet  (state x sku x subsidy_pct x provenance).
"""
from __future__ import annotations

import time

import pandas as pd

from pipeline.common import RAW, MARTS, CONFIG, write_table, log, Manifest, FetchRecord, load_yaml

LOG = log("subsidy")
SRC = RAW / "subsidy" / "subsidy.xlsx"
PILOT = ["Punjab", "Madhya Pradesh", "Maharashtra"]
SMAM_GENERAL = 0.40   # national SMAM rate for the "other/general" beneficiary category

# Combined-sheet equipment name -> app SKU id(s). Hand-checked against sku_catalog.yaml.
EQUIP_TO_SKU: dict[str, list[str]] = {
    "Rotavator": ["ROTAVATOR_5FT", "ROTAVATOR_7FT"],
    "MB Plough": ["MB_PLOUGH_2F"],
    "Disc Harrow": ["DISC_HARROW_16"],
    "Power Harrow": ["POWER_HARROW"],
    "Cultivator": ["CULTIVATOR_9T"],
    "Laser Land Leveler": ["LASER_LEVELER"],
    "Subsoiler": ["SUBSOILER"],
    "Hydraulic Reversible Plough": ["REV_PLOUGH_2F"],
    "Seed Drill": ["SEED_DRILL_11T"],
    "Seed-cum-Fertilizer Drill": ["SEED_FERT_DRILL_13T"],
    "Super Seeder": ["SUPER_SEEDER"],
    "Zero Till Drill": ["HAPPY_SEEDER"],
    "Raised Bed Planter": ["RAISED_BED_PLANTER"],
    "Multi Crop Planter": ["MULTICROP_PLANTER"],
    "Pneumatic Planter": ["PNEUMATIC_PLANTER"],
    "Ride-on Paddy Transplanter": ["RICE_TRANSPLANTER"],
    "Tractor Boom Sprayer": ["BOOM_SPRAYER"],
    "Air Assist Sprayer": ["ORCHARD_SPRAYER"],
    "Power Sprayer": ["HTP_SPRAYER"],
    "Drone": ["AGRI_DRONE"],
    "Reaper-cum-Binder": ["REAPER_BINDER"],
    "Tractor Reaper": ["TRACTOR_REAPER"],
    "Groundnut Harvester": ["POTATO_HARVESTER"],
    "Straw Reaper": ["STRAW_REAPER"],
    "Straw Chopper": ["MULCHER"],
    "Round Baler": ["ROUND_BALER"],
    "Hay Rake": ["HAY_RAKE"],
    "Power Maize Sheller": ["MAIZE_SHELLER"],
    "Thresher": ["MULTICROP_THRESHER"],
    "Chaff Cutter": ["CHAFF_CUTTER"],
}


def build() -> None:
    t0 = time.time()
    comb = pd.read_excel(SRC, sheet_name="Combined")
    comb = comb.dropna(subset=["Equipment Name"])
    skus = {s["id"]: s for s in load_yaml("sku_catalog.yaml")["skus"]}

    # {(state, sku_id): pct} from the detailed Punjab / Maharashtra columns
    real: dict[tuple[str, str], float] = {}
    for _, row in comb.iterrows():
        equip = str(row["Equipment Name"]).strip()
        sku_ids = EQUIP_TO_SKU.get(equip)
        if not sku_ids:
            continue
        for state in ("Punjab", "Maharashtra"):
            val = row.get(state)
            if pd.notna(val):
                for sid in sku_ids:
                    key = (state, sid)
                    real[key] = max(real.get(key, 0.0), float(val))

    rows = []
    for (state, sid), pct in real.items():
        rows.append({"state": state, "sku_id": sid, "category": skus[sid]["category"],
                     "subsidy_pct": round(pct * 100, 1), "provenance": "real"})

    # Madhya Pradesh: no state sheet -> national SMAM general rate for every SKU that is
    # subsidised anywhere (i.e. SMAM-eligible), flagged allocated.
    eligible = {sid for _, sid in real}
    for sid in eligible:
        rows.append({"state": "Madhya Pradesh", "sku_id": sid,
                     "category": skus[sid]["category"],
                     "subsidy_pct": round(SMAM_GENERAL * 100, 1), "provenance": "allocated"})

    df = pd.DataFrame(rows).sort_values(["state", "category", "sku_id"])
    write_table(df, MARTS / "subsidy.parquet")

    cov = df[df.provenance == "real"].groupby("state")["sku_id"].nunique().to_dict()
    Manifest.record(FetchRecord(
        source="subsidy", mode="real", rows=len(df), provenance="real",
        vintage="EY Subsidies Master (Combined) 2025-26; MP=national SMAM proxy",
        coverage_pct=round(100 * len(eligible) / len(skus), 1),
        elapsed_s=round(time.time() - t0, 2)))
    LOG.info("subsidy: %d SKUs covered (PB=%s, MH=%s real); MP=%d via SMAM 40%% proxy",
             len(eligible), cov.get("Punjab", 0), cov.get("Maharashtra", 0), len(eligible))


if __name__ == "__main__":
    build()
