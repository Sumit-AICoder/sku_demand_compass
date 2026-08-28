"""Aggregate the village-level scores up the geography for the API.

The API must answer "top districts", "top blocks in this district", "top villages in
this block" in one query each. Pre-aggregating here keeps those sub-second and, more
importantly, guarantees the levels RECONCILE: a parent's number is by construction the
sum of its children's, so drilling down never changes the total.
"""
from __future__ import annotations

import pandas as pd

from pipeline.common import CURATED, MARTS, Config, read_table, write_table, log

LOG = log("export")

MEASURES = ["addressable", "owned", "headroom", "new_units_yr",
            "replacement_units_yr", "potential_units_yr", "potential_value_inr"]


def build() -> None:
    sc = read_table(MARTS / "village_sku_scores.parquet")
    v = read_table(CURATED / "geo_villages.parquet")[
        ["village_id", "block_id", "district_id", "state", "village", "lon", "lat"]]
    b = read_table(CURATED / "geo_blocks.parquet")[["block_id", "block", "district_id", "lon", "lat"]]
    d = read_table(CURATED / "geo_districts.parquet")[
        ["district_id", "district", "state", "zone", "crop_system", "mech_tier", "lon", "lat"]]

    sc = sc.merge(v[["village_id", "block_id"]], on="village_id", how="left")

    # ---- level x SKU rollups -------------------------------------------------
    for level, keys, geo in [
        ("block", ["block_id", "sku_id", "category"], b),
        ("district", ["district_id", "sku_id", "category"], d),
    ]:
        agg = sc.groupby(keys, observed=True).agg(
            **{m: (m, "sum") for m in MEASURES},
            propensity=("propensity", "mean"),
            villages=("village_id", "size")).reset_index()
        agg["provenance"] = "allocated"
        write_table(agg, MARTS / f"{level}_sku_scores.parquet")

    state_sku = sc.merge(v[["village_id", "state"]], on="village_id").groupby(
        ["state", "sku_id", "category"], observed=True).agg(
        **{m: (m, "sum") for m in MEASURES}, propensity=("propensity", "mean")).reset_index()
    state_sku["provenance"] = "allocated"
    write_table(state_sku, MARTS / "state_sku_scores.parquet")

    # ---- geography totals across all SKUs -----------------------------------
    for level, key, geo, namecol in [
        ("village", "village_id", v, "village"),
        ("block", "block_id", b, "block"),
        ("district", "district_id", d, "district"),
    ]:
        tot = sc.groupby(key, observed=True).agg(
            **{m: (m, "sum") for m in MEASURES}).reset_index()
        top = (sc.sort_values("potential_units_yr", ascending=False)
               .groupby(key, observed=True).head(1)[[key, "sku_id", "category"]]
               .rename(columns={"sku_id": "top_sku", "category": "top_category"}))
        tot = tot.merge(top, on=key, how="left").merge(geo, on=key, how="left")
        tot["attach_gap"] = tot["headroom"] / tot["addressable"].clip(lower=1e-9)
        tot["provenance"] = "allocated"
        write_table(tot, MARTS / f"{level}_totals.parquet")

    # ---- sku reference for the API ------------------------------------------
    cats = Config.sku_categories()
    ref = pd.DataFrame([{
        "sku_id": s["id"], "name": s["name"], "category": s["category"],
        "category_label": cats[s["category"]]["label"],
        "hp_min": s["hp_min"], "hp_max": s["hp_max"], "maturity": s["maturity"],
        "price_inr": s["price_inr"], "life_years": s["life_years"],
        "season": ",".join(map(str, s["season"])),
        "rental_substitutable": s["rental_substitutable"],
        "provenance": "real",
    } for s in Config.skus()])
    write_table(ref, MARTS / "sku_reference.parquet")

    LOG.info("marts exported: village/block/district totals + level x SKU rollups")


if __name__ == "__main__":
    build()
