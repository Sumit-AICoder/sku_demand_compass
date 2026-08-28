"""Competitor share and the headwind Sonalika faces, per district x SKU category.

Shaped by the qualitative positioning in the "Compete" sheet of Wireframe.xlsx:
Shaktiman/Fieldking own rotavator equity, Mahindra is broad with MITRA in orchard
sprayers, Landforce is Punjab-centric, Khedut is strong in the West, John Deere and
Maschio hold the premium/precision end, and local fabricators dominate trolleys and
threshers. Those become share multipliers, not adjectives.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.common import CURATED, Config, read_table, write_table, log

LOG = log("simulate.competition")

# Player x category affinity, read off the Compete sheet's portfolio descriptions.
AFFINITY = {
    "Sonalika":      {"tillage": 1.0, "sowing": 1.0, "crop_protection": .6, "irrigation": .6, "harvesting": .5, "residue": .7, "post_harvest": .8, "haulage": .9, "precision": .4},
    "Mahindra":      {"tillage": .9, "sowing": .8, "crop_protection": 1.4, "irrigation": .7, "harvesting": 1.2, "residue": .8, "post_harvest": .9, "haulage": .8, "precision": .9},
    "Shaktiman":     {"tillage": 1.6, "sowing": .9, "crop_protection": .5, "irrigation": .3, "harvesting": 1.4, "residue": 1.3, "post_harvest": .7, "haulage": .6, "precision": .3},
    "Fieldking":     {"tillage": 1.5, "sowing": .9, "crop_protection": .5, "irrigation": .3, "harvesting": 1.0, "residue": 1.2, "post_harvest": .7, "haulage": 1.0, "precision": .2},
    "JohnDeere":     {"tillage": .8, "sowing": 1.1, "crop_protection": .7, "irrigation": .3, "harvesting": 1.1, "residue": .7, "post_harvest": .4, "haulage": .3, "precision": 1.8},
    "TAFE":          {"tillage": .9, "sowing": .8, "crop_protection": .6, "irrigation": .6, "harvesting": .8, "residue": .7, "post_harvest": .8, "haulage": .8, "precision": .5},
    "EscortsKubota": {"tillage": .8, "sowing": .8, "crop_protection": .6, "irrigation": .5, "harvesting": 1.0, "residue": .7, "post_harvest": .7, "haulage": .7, "precision": .8},
    "Landforce":     {"tillage": 1.2, "sowing": 1.2, "crop_protection": .8, "irrigation": .4, "harvesting": .9, "residue": 1.3, "post_harvest": .6, "haulage": 1.0, "precision": .2},
    "Maschio":       {"tillage": 1.3, "sowing": 1.4, "crop_protection": .8, "irrigation": .2, "harvesting": .4, "residue": .9, "post_harvest": .3, "haulage": .2, "precision": 1.2},
    "KhedutAgro":    {"tillage": 1.1, "sowing": 1.4, "crop_protection": .7, "irrigation": .5, "harvesting": .9, "residue": .6, "post_harvest": .9, "haulage": .8, "precision": .1},
    "NewHolland":    {"tillage": .7, "sowing": .7, "crop_protection": .5, "irrigation": .2, "harvesting": 1.5, "residue": 1.4, "post_harvest": .4, "haulage": .3, "precision": .8},
    "VST":           {"tillage": .8, "sowing": .7, "crop_protection": .9, "irrigation": .5, "harvesting": .7, "residue": .4, "post_harvest": .8, "haulage": .4, "precision": .4},
    "Local":         {"tillage": 1.2, "sowing": 1.0, "crop_protection": 1.1, "irrigation": 1.4, "harvesting": .5, "residue": .5, "post_harvest": 1.6, "haulage": 2.0, "precision": .1},
}


def build(spine, seed=20260822) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 34)
    cfg = Config.sim()["competition"]
    # Implement categories only: the 13 players below and their affinity weights are
    # implement brands. Tractor competition comes from the real 6-OEM dealer footprint,
    # not from this hand-set choice model.
    cats = list(Config.sku_categories("implements"))
    d = spine["districts"]
    players = cfg["players"]

    rows = []
    for _, dist in d.iterrows():
        bias = cfg["state_bias"].get(dist["state"], {})
        for cat in cats:
            w = np.array([AFFINITY[p][cat] * bias.get(p, 1.0) for p in players])
            w = w * rng.lognormal(0, 0.22, len(players))          # district idiosyncrasy
            share = w / w.sum()
            for p, s in zip(players, share):
                rows.append({"district_id": dist["district_id"], "state": dist["state"],
                             "category": cat, "player": p, "share": float(s)})

    comp = pd.DataFrame(rows)

    son = comp[comp.player == "Sonalika"].set_index(["district_id", "category"])["share"]
    # Headwind = how contested the category is here. High incumbency and a strong
    # leader mean a given unit of demand converts less often for Sonalika.
    leader = (comp[comp.player != "Sonalika"]
              .groupby(["district_id", "category"])["share"].max())
    incumb = pd.Series({c: cfg["category_incumbency"][c] for c in cats})

    head = pd.DataFrame({"sonalika_share": son, "leader_share": leader}).reset_index()
    head["incumbency"] = head["category"].map(incumb)
    head["headwind"] = np.clip(
        1.0 - 0.55 * head["incumbency"] * (head["leader_share"] / (head["sonalika_share"] + 1e-9)).clip(0, 4) / 4.0,
        0.25, 1.0)
    head["provenance"] = "simulated"
    comp["provenance"] = "simulated"

    LOG.info("Sonalika share by category (mean across districts):\n%s",
             head.groupby("category")[["sonalika_share", "headwind"]].mean().round(3).to_string())
    write_table(comp, CURATED / "competition_shares.parquet")
    write_table(head, CURATED / "competition_headwind.parquet")
    return head


if __name__ == "__main__":
    sp = {k: read_table(CURATED / f"geo_{k}.parquet") for k in ("districts", "blocks", "villages")}
    build(sp)
