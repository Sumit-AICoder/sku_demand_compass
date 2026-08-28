"""The segmentation taxonomy: one definition of what an archetype is made of.

Before this module the categories lived in five hardcoded copies -- the HP-belt if-ladder
and the TIV median split in micromarkets.py, the crop shortlist beside them, the zone table
in narp.py, and the dropdowns in Configure.tsx -- so a client edit could only ever be
cosmetic, and the screen could disagree with the mart.

Now `assign()` is the single labelling function. The pipeline calls it with the shipped
`config/taxonomy.yaml`; the API calls it with the user's edited copy against the same
micro-markets, which is why Configure can re-label 23,389 rows in about a second instead of
re-running a pipeline.

An archetype is ZONE x TIV TIER x HP BELT. Dominant crop names it and gets its own column,
but is deliberately not part of the key: as a key axis it produces 144 archetypes, most of
them too thin to fit a model on.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import yaml

from pipeline.common import CONFIG

DEFAULT_PATH = CONFIG / "taxonomy.yaml"


def load(path=None) -> dict[str, Any]:
    """The shipped taxonomy."""
    with open(path or DEFAULT_PATH) as f:
        return yaml.safe_load(f)


# ------------------------------------------------------------------ the pieces

def tiv_tier(tiv: pd.Series, tiers: list[dict]) -> pd.Series:
    """Quantile-cut the fleet into named tiers.

    Ranked before cutting so ties (common at small fleet counts) split evenly rather than
    piling into one tier and leaving another empty.
    """
    tiers = sorted(tiers, key=lambda t: t["upto"])
    r = tiv.rank(method="first", pct=True)
    out = pd.Series(tiers[-1]["name"], index=tiv.index, dtype=object)
    for t in reversed(tiers):
        out[r <= float(t["upto"])] = t["name"]
    return out


def hp_belt(mean_hp: pd.Series, belts: list[dict]) -> pd.Series:
    """Band the TIV-weighted mean tractor HP. `upto: null` is the open-ended top band."""
    ordered = sorted(belts, key=lambda b: (b["upto"] is None, b["upto"] or 0))
    out = pd.Series(ordered[-1]["name"], index=mean_hp.index, dtype=object)
    for b in reversed(ordered):
        if b["upto"] is not None:
            out[mean_hp <= float(b["upto"])] = b["name"]
    return out


def zone_of(subzone_id: pd.Series, zones: list[dict]) -> tuple[pd.Series, pd.Series]:
    """Map each sub-zone to its zone id and canonical name.

    One name per zone id, always -- the NARP table has zone 4 under two different names and
    zone 19 under two more, which is what made a zone-keyed archetype ambiguous before.
    """
    zid, zname = {}, {}
    for z in zones:
        for sz in z["subzones"]:
            zid[sz] = str(z["id"])
            zname[sz] = z["name"]
    return subzone_id.map(zid).fillna(""), subzone_id.map(zname).fillna("")


def crop_vocabulary(tax: dict) -> dict[str, str]:
    """Raw dominant_crop value -> the category label it belongs to.

    Editing this on Configure is what "edit the crop categories" means: rename a category,
    merge several raw crops into one by listing them together, or delete a category so it
    stops naming archetypes at all.
    """
    out = {}
    for c in tax.get("crops", []):
        for v in c.get("values") or [c["name"].lower()]:
            out[str(v).lower()] = c["name"]
    return out


def crop_label(mm: pd.DataFrame, tax: dict, key: pd.Series | None = None) -> pd.Series:
    """Name each group by the crop category most grown in it.

    The first version z-scored crop shares across zones to pick a *distinctive* crop, which
    read well in isolation and badly on screen: a row could be called "Cotton High-TIV" with
    a Most-grown column saying sugarcane, and no one can be told which of the two is the
    crop grown there. The archetype is named by its own modal crop instead, so the name and
    the column are the same fact.

    A raw crop outside the vocabulary is skipped rather than guessed at, so deleting a
    category moves those archetypes onto their next-biggest crop -- and an archetype whose
    every crop has been deleted honestly reads "Mixed".
    """
    if key is None:
        key = pd.Series("all", index=mm.index)
    raw = mm["dominant_crop"] if "dominant_crop" in mm.columns else _modal_crop(mm, tax)
    named = raw.astype(str).str.lower().map(crop_vocabulary(tax))
    modal = named.groupby(key).agg(lambda s: s.mode().iloc[0] if len(s.dropna().mode()) else None)
    return key.map(modal).fillna("Mixed").astype(str)


def _modal_crop(mm: pd.DataFrame, tax: dict) -> pd.Series:
    """Fall back to the biggest crop share per row when the mart has no dominant_crop."""
    cols = [c["share_column"] for c in tax["crops"] if c.get("share_column") in mm.columns]
    if not cols:
        return pd.Series("Mixed", index=mm.index, dtype=object)
    label = {c["share_column"]: c["name"] for c in tax["crops"] if c.get("share_column")}
    return mm[cols].idxmax(axis=1).map(label)


# ------------------------------------------------------------------ the whole label

def assign(mm: pd.DataFrame, tax: dict) -> pd.DataFrame:
    """Label micro-markets with the taxonomy's categories and the archetype they form.

    Returns a copy with zone, zone_name, tiv_tier, hp_belt, crop_label, base_name,
    archetype and archetype_id set. Everything else on the frame is left alone, so this is
    safe to run over a mart read straight from parquet.
    """
    out = mm.copy()
    out["zone"], out["zone_name"] = zone_of(out["subzone_id"], tax["zones"])
    out["tiv_tier"] = tiv_tier(out["tiv"], tax["tiv_tiers"])
    out["hp_belt"] = hp_belt(out["mean_hp"], tax["hp_belts"])

    tier_code = {t["name"]: str(t.get("code") or t["name"][:1]).upper() for t in tax["tiv_tiers"]}
    belt_code = {b["name"]: str(b.get("code") or b["name"]) for b in tax["hp_belts"]}
    out["archetype_id"] = (out["zone"] + "|"
                           + out["tiv_tier"].map(tier_code).fillna("?") + "|"
                           + out["hp_belt"].map(belt_code).fillna("?"))
    # The crop is named per archetype, not per zone, so it agrees with the Most-grown column.
    out["crop_label"] = crop_label(out, tax, out["archetype_id"])
    out["base_name"] = out["crop_label"] + " " + out["tiv_tier"] + "-TIV"
    out["archetype"] = out["base_name"] + " · " + out["hp_belt"]
    return out


def describe(tax: dict) -> str:
    """One line for a log or a screen: what this taxonomy would produce."""
    return (f"{len(tax['zones'])} zones x {len(tax['tiv_tiers'])} TIV tiers "
            f"x {len(tax['hp_belts'])} HP belts")


def validate(tax: dict) -> list[str]:
    """Problems that would make a taxonomy unusable. Empty list means it is fine."""
    errs = []
    for key in ("tiv_tiers", "hp_belts", "zones"):
        if not tax.get(key):
            errs.append(f"{key} is empty")
    ids = [str(z["id"]) for z in tax.get("zones", [])]
    if len(ids) != len(set(ids)):
        errs.append("two zones share an id")
    seen: dict[str, str] = {}
    for z in tax.get("zones", []):
        for sz in z["subzones"]:
            if sz in seen:
                errs.append(f"sub-zone {sz} is in both zone {seen[sz]} and zone {z['id']}")
            seen[sz] = str(z["id"])
    cuts = [float(t["upto"]) for t in tax.get("tiv_tiers", [])]
    if cuts and abs(max(cuts) - 1.0) > 1e-9:
        errs.append("the top TIV tier must end at 1.0")
    # A raw crop in two categories would make the archetype's name depend on dict order.
    owner: dict[str, str] = {}
    for c in tax.get("crops", []):
        for v in c.get("values") or [c["name"].lower()]:
            v = str(v).lower()
            if v in owner:
                errs.append(f"crop '{v}' is in both {owner[v]} and {c['name']}")
            owner[v] = c["name"]
    return errs


if __name__ == "__main__":                                     # a runnable self-check
    t = load()
    assert not validate(t), validate(t)
    n = 600
    rng = np.random.default_rng(0)
    demo = pd.DataFrame({
        "subzone_id": rng.choice(["2.3", "4.2", "4.3", "6.4", "10.3", "19.2"], n),
        "tiv": rng.lognormal(4, 1, n),
        "mean_hp": rng.uniform(22, 70, n),
        "crop_wheat_share": rng.random(n), "crop_rice_share": rng.random(n),
        "crop_cotton_share": rng.random(n), "crop_soybean_share": rng.random(n),
        "crop_sugarcane_share": rng.random(n),
    })
    got = assign(demo, t)
    counts = got["tiv_tier"].value_counts()
    # Even-sized to within a couple of rows: the quantile cuts land between ranks, so an
    # exact three-way split is not achievable and not the point.
    assert counts.max() - counts.min() <= 3, f"tiers are not terciles: {counts.to_dict()}"
    assert (got["zone"] != "").all(), "a sub-zone fell outside every zone"
    assert got.groupby("zone")["zone_name"].nunique().max() == 1, "a zone has two names"
    assert got.groupby("archetype_id")["crop_label"].nunique().max() == 1, "two crops on one archetype"
    assert got["archetype_id"].str.count(r"\|").eq(2).all(), "malformed archetype id"
    print(f"taxonomy ok: {describe(t)} -> {got['archetype_id'].nunique()} archetypes on demo data")
