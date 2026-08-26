"""Village-level layers: soil, cropping pattern, irrigation, holdings.

Soil is one of the very few genuinely village-level public sources (Soil Health Card
and SLUSI), so the connectors target it directly. Cropping pattern, irrigation and
holdings publish at district level and are allocated down -- which is why their
provenance is "allocated", not "real", and why the UI badges them differently.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.common import CURATED, Config, write_table, log
from pipeline.ingest.base import Connector

LOG = log("village_layers")

# Crop systems in districts.csv -> share vector over the 9 crop groups the SKU
# catalogue scores against. Shares are the district's typical gross cropped mix.
CROP_SYSTEM_MIX = {
    "rice_wheat":            {"rice": .44, "wheat": .46, "maize": .03, "pulses": .03, "oilseeds": .02, "cotton": .00, "soybean": .00, "sugarcane": .01, "horticulture": .01},
    "rice_wheat_maize":      {"rice": .34, "wheat": .40, "maize": .16, "pulses": .04, "oilseeds": .03, "cotton": .00, "soybean": .00, "sugarcane": .01, "horticulture": .02},
    "rice_wheat_potato":     {"rice": .38, "wheat": .40, "maize": .04, "pulses": .02, "oilseeds": .02, "cotton": .00, "soybean": .00, "sugarcane": .04, "horticulture": .10},
    "rice_wheat_cotton":     {"rice": .34, "wheat": .40, "maize": .02, "pulses": .03, "oilseeds": .02, "cotton": .17, "soybean": .00, "sugarcane": .01, "horticulture": .01},
    "cotton_wheat":          {"rice": .06, "wheat": .38, "maize": .02, "pulses": .04, "oilseeds": .04, "cotton": .43, "soybean": .00, "sugarcane": .01, "horticulture": .02},
    "wheat_maize":           {"rice": .08, "wheat": .48, "maize": .26, "pulses": .06, "oilseeds": .05, "cotton": .00, "soybean": .00, "sugarcane": .03, "horticulture": .04},
    "wheat_soybean":         {"rice": .05, "wheat": .42, "maize": .04, "pulses": .10, "oilseeds": .05, "cotton": .02, "soybean": .28, "sugarcane": .01, "horticulture": .03},
    "soybean_wheat":         {"rice": .04, "wheat": .34, "maize": .04, "pulses": .09, "oilseeds": .05, "cotton": .03, "soybean": .38, "sugarcane": .01, "horticulture": .02},
    "soybean_cotton":        {"rice": .02, "wheat": .18, "maize": .05, "pulses": .08, "oilseeds": .05, "cotton": .28, "soybean": .31, "sugarcane": .01, "horticulture": .02},
    "soybean_garlic":        {"rice": .02, "wheat": .26, "maize": .03, "pulses": .07, "oilseeds": .05, "cotton": .02, "soybean": .36, "sugarcane": .01, "horticulture": .18},
    "soybean_coriander":     {"rice": .02, "wheat": .24, "maize": .03, "pulses": .08, "oilseeds": .08, "cotton": .02, "soybean": .38, "sugarcane": .01, "horticulture": .14},
    "soybean_pulses":        {"rice": .02, "wheat": .16, "maize": .04, "pulses": .26, "oilseeds": .06, "cotton": .08, "soybean": .34, "sugarcane": .02, "horticulture": .02},
    "wheat_mustard":         {"rice": .06, "wheat": .46, "maize": .03, "pulses": .10, "oilseeds": .28, "cotton": .01, "soybean": .02, "sugarcane": .02, "horticulture": .02},
    "wheat_pulses":          {"rice": .05, "wheat": .44, "maize": .03, "pulses": .34, "oilseeds": .08, "cotton": .01, "soybean": .02, "sugarcane": .01, "horticulture": .02},
    "wheat_rice":            {"rice": .34, "wheat": .48, "maize": .03, "pulses": .07, "oilseeds": .04, "cotton": .01, "soybean": .01, "sugarcane": .01, "horticulture": .01},
    "rice_pulses":           {"rice": .48, "wheat": .12, "maize": .04, "pulses": .26, "oilseeds": .05, "cotton": .01, "soybean": .01, "sugarcane": .01, "horticulture": .02},
    "rice_cotton":           {"rice": .40, "wheat": .10, "maize": .03, "pulses": .10, "oilseeds": .04, "cotton": .28, "soybean": .02, "sugarcane": .01, "horticulture": .02},
    "maize_soybean":         {"rice": .06, "wheat": .18, "maize": .32, "pulses": .08, "oilseeds": .05, "cotton": .04, "soybean": .24, "sugarcane": .01, "horticulture": .02},
    "maize_cotton":          {"rice": .05, "wheat": .14, "maize": .34, "pulses": .09, "oilseeds": .06, "cotton": .28, "soybean": .02, "sugarcane": .00, "horticulture": .02},
    "cotton_soybean":        {"rice": .02, "wheat": .12, "maize": .04, "pulses": .12, "oilseeds": .04, "cotton": .40, "soybean": .23, "sugarcane": .01, "horticulture": .02},
    "cotton_onion":          {"rice": .03, "wheat": .14, "maize": .06, "pulses": .08, "oilseeds": .05, "cotton": .40, "soybean": .06, "sugarcane": .02, "horticulture": .16},
    "cotton_maize":          {"rice": .04, "wheat": .12, "maize": .28, "pulses": .10, "oilseeds": .05, "cotton": .36, "soybean": .03, "sugarcane": .00, "horticulture": .02},
    "cotton_orange":         {"rice": .04, "wheat": .14, "maize": .05, "pulses": .10, "oilseeds": .04, "cotton": .40, "soybean": .10, "sugarcane": .01, "horticulture": .12},
    "banana_cotton":         {"rice": .03, "wheat": .12, "maize": .05, "pulses": .06, "oilseeds": .03, "cotton": .34, "soybean": .04, "sugarcane": .05, "horticulture": .28},
    "sugarcane_wheat":       {"rice": .06, "wheat": .28, "maize": .04, "pulses": .06, "oilseeds": .04, "cotton": .02, "soybean": .06, "sugarcane": .38, "horticulture": .06},
    "sugarcane_rice":        {"rice": .24, "wheat": .10, "maize": .03, "pulses": .05, "oilseeds": .03, "cotton": .01, "soybean": .02, "sugarcane": .44, "horticulture": .08},
    "sugarcane_onion":       {"rice": .04, "wheat": .12, "maize": .06, "pulses": .08, "oilseeds": .04, "cotton": .06, "soybean": .04, "sugarcane": .34, "horticulture": .22},
    "sugarcane_jowar":       {"rice": .03, "wheat": .10, "maize": .08, "pulses": .16, "oilseeds": .06, "cotton": .06, "soybean": .04, "sugarcane": .32, "horticulture": .15},
    "sugarcane_horticulture":{"rice": .05, "wheat": .10, "maize": .05, "pulses": .06, "oilseeds": .03, "cotton": .02, "soybean": .04, "sugarcane": .35, "horticulture": .30},
    "sugarcane_grapes":      {"rice": .04, "wheat": .10, "maize": .04, "pulses": .06, "oilseeds": .03, "cotton": .03, "soybean": .04, "sugarcane": .34, "horticulture": .32},
    "grapes_onion":          {"rice": .04, "wheat": .12, "maize": .06, "pulses": .07, "oilseeds": .04, "cotton": .04, "soybean": .05, "sugarcane": .10, "horticulture": .48},
    "rice_horticulture":     {"rice": .46, "wheat": .06, "maize": .03, "pulses": .06, "oilseeds": .03, "cotton": .01, "soybean": .01, "sugarcane": .04, "horticulture": .30},
    "mango_rice":            {"rice": .34, "wheat": .04, "maize": .02, "pulses": .06, "oilseeds": .03, "cotton": .00, "soybean": .00, "sugarcane": .01, "horticulture": .50},
    "mango_cashew":          {"rice": .22, "wheat": .03, "maize": .02, "pulses": .05, "oilseeds": .03, "cotton": .00, "soybean": .00, "sugarcane": .01, "horticulture": .64},
    "urban":                 {"rice": .30, "wheat": .10, "maize": .05, "pulses": .10, "oilseeds": .05, "cotton": .02, "soybean": .02, "sugarcane": .06, "horticulture": .30},
}
CROPS = ["rice", "wheat", "maize", "pulses", "oilseeds", "cotton", "soybean", "sugarcane", "horticulture"]

HOLDING_CLASSES = ["marginal", "small", "semi_medium", "medium", "large"]
# Operational-holding size-class distribution by state and mechanisation tier.
# Punjab is the national outlier: very few marginal holdings, many medium/large.
HOLDING_MIX = {
    ("Punjab", "high"):            [.14, .18, .27, .29, .12],
    ("Punjab", "medium"):          [.22, .22, .27, .22, .07],
    ("Madhya Pradesh", "high"):    [.36, .24, .21, .15, .04],
    ("Madhya Pradesh", "medium"):  [.44, .24, .18, .11, .03],
    ("Madhya Pradesh", "low"):     [.55, .23, .13, .07, .02],
    ("Maharashtra", "high"):       [.42, .27, .19, .10, .02],
    ("Maharashtra", "medium"):     [.50, .26, .16, .07, .01],
    ("Maharashtra", "low"):        [.58, .24, .12, .05, .01],
}
CLASS_MID_HA = {"marginal": 0.6, "small": 1.5, "semi_medium": 2.8, "medium": 5.8, "large": 13.0}


class SoilConnector(Connector):
    """Soil Health Card nutrients + SLUSI texture/depth, at village level."""

    source_key = "soil_health_card"

    def synthesize(self) -> pd.DataFrame:
        rng = np.random.default_rng(self.seed + 11)
        v = self.spine["villages"]
        d = self.spine["districts"].set_index("district_id")
        sim = Config.sim()["soil"]

        zone = v["district_id"].map(d["zone"])
        tier = v["district_id"].map(d["mech_tier"])

        # Texture drawn from the zone's distribution; spatially smoothed so
        # neighbouring villages share soil, which is how soil actually behaves.
        tex_by_zone = sim["texture_by_zone"]
        texture = np.empty(len(v), dtype=object)
        for z, idx in v.groupby(zone).groups.items():
            mix = tex_by_zone.get(z, tex_by_zone["default"])
            keys = list(mix)
            texture[v.index.get_indexer(idx)] = rng.choice(keys, size=len(idx), p=[mix[k] for k in keys])

        hardness = pd.Series(texture).map(sim["hardness_from_texture"]).to_numpy()
        oc_mean = tier.map(sim["oc_pct_mean"]).astype(float).to_numpy()

        out = pd.DataFrame({
            "village_id": v["village_id"].to_numpy(),
            "texture": texture,
            "hardness": hardness,
            "ph": np.clip(rng.normal(sim["ph_mean"], sim["ph_sigma"], len(v)), 4.5, 9.5),
            "oc_pct": np.clip(rng.normal(oc_mean, 0.14, len(v)), 0.08, 1.6),
            "ec_ds_m": np.clip(rng.lognormal(-1.5, 0.5, len(v)), 0.05, 4.0),
            "n_kg_ha": np.clip(rng.normal(260, 62, len(v)), 90, 560),
            "p_kg_ha": np.clip(rng.normal(19, 7, len(v)), 3, 60),
            "k_kg_ha": np.clip(rng.normal(310, 95, len(v)), 70, 800),
            "zn_ppm": np.clip(rng.normal(0.72, 0.30, len(v)), 0.05, 3.0),
            "depth_cm": np.clip(rng.normal(sim["depth_cm_by_zone_default"], 28, len(v)), 15, 190),
            "slope_pct": np.clip(rng.lognormal(0.35, 0.7, len(v)), 0.2, 30),
        })

        # Workability index: what the iron actually has to cope with.
        # Deep, low-slope, medium-texture soil is the easiest to work.
        tex_score = pd.Series(texture).map({"sandy": 0.85, "loam": 1.0, "clay": 0.62}).to_numpy()
        depth_score = np.clip(out["depth_cm"] / 120.0, 0.25, 1.0)
        slope_score = np.clip(1.0 - out["slope_pct"] / 30.0, 0.3, 1.0)
        out["workability"] = tex_score * depth_score * slope_score

        # Draft-power requirement rises in heavy clay and shallow, sloping soils --
        # this is what makes reversible ploughs and subsoilers relevant.
        out["draft_requirement"] = np.clip(
            pd.Series(texture).map({"sandy": 0.45, "loam": 0.65, "clay": 1.0}).to_numpy()
            * (1.0 + 0.4 * (1 - depth_score)) * (1.0 + 0.3 * (1 - slope_score)), 0.2, 1.6)
        return out


class CroppingConnector(Connector):
    """District cropping pattern allocated to villages with local variation."""

    source_key = "upag_apy"

    def synthesize(self) -> pd.DataFrame:
        rng = np.random.default_rng(self.seed + 12)
        v = self.spine["villages"]
        d = self.spine["districts"].set_index("district_id")
        cs = v["district_id"].map(d["crop_system"])

        base = np.array([[CROP_SYSTEM_MIX[c][k] for k in CROPS] for c in cs])
        # Dirichlet noise keeps village mixes near the district mix but not identical:
        # concentration 55 gives realistic within-district heterogeneity.
        mix = rng.dirichlet(np.ones(len(CROPS)), size=len(v)) * 0.0
        mix = np.array([rng.dirichlet(row * 55 + 0.35) for row in base])

        out = pd.DataFrame(mix, columns=[f"crop_{c}" for c in CROPS])
        out.insert(0, "village_id", v["village_id"].to_numpy())

        irr_tier = v["district_id"].map(d["irrigation_tier"])
        irr_base = irr_tier.map({"high": 0.82, "medium": 0.44, "low": 0.18}).astype(float)
        out["irrigation_ratio"] = np.clip(rng.normal(irr_base, 0.14), 0.02, 0.99)
        # Cropping intensity is driven by water, not land: irrigated villages double-crop.
        out["cropping_intensity"] = np.clip(
            1.0 + 0.95 * out["irrigation_ratio"] + rng.normal(0, 0.10, len(v)), 1.0, 2.6)

        p = mix / mix.sum(1, keepdims=True)
        out["crop_entropy"] = -(p * np.log(p + 1e-12)).sum(1) / np.log(len(CROPS))
        out["dominant_crop"] = [CROPS[i] for i in mix.argmax(1)]
        out["high_value_share"] = out["crop_horticulture"] + out["crop_sugarcane"]

        # Residue burden: the super seeder / baler / mulcher driver from the Excel.
        v_ns = v.set_index("village_id")["net_sown_ha"]
        gca = v_ns.to_numpy() * out["cropping_intensity"].to_numpy()
        out["residue_burden_t"] = gca * (out["crop_rice"] * 5.5 + out["crop_wheat"] * 3.6
                                         + out["crop_sugarcane"] * 2.0)
        out["gross_cropped_ha"] = gca
        return out


class HoldingsConnector(Connector):
    """Agricultural Census operational holdings by size class."""

    source_key = "ag_census_holdings"

    def synthesize(self) -> pd.DataFrame:
        rng = np.random.default_rng(self.seed + 13)
        v = self.spine["villages"]
        d = self.spine["districts"].set_index("district_id")
        state = v["district_id"].map(d["state"])
        tier = v["district_id"].map(d["mech_tier"])

        key = list(zip(state, tier))
        base = np.array([HOLDING_MIX.get(k, HOLDING_MIX[("Madhya Pradesh", "medium")]) for k in key])
        mix = np.array([rng.dirichlet(row * 70 + 0.3) for row in base])

        out = pd.DataFrame(mix, columns=[f"hold_{c}" for c in HOLDING_CLASSES])
        out.insert(0, "village_id", v["village_id"].to_numpy())

        mids = np.array([CLASS_MID_HA[c] for c in HOLDING_CLASSES])
        out["avg_holding_ha"] = mix @ mids
        out["n_holdings"] = np.maximum(
            8, np.round(v["net_sown_ha"].to_numpy() / out["avg_holding_ha"]).astype(int))
        out["large_holding_share"] = out["hold_medium"] + out["hold_large"]

        # Gini of the holding distribution -- inequality of land access, which
        # separates "a few buyers" from "many renters" in the same average.
        share_area = mix * mids
        share_area = share_area / share_area.sum(1, keepdims=True)
        cum_pop = np.cumsum(mix, axis=1)
        cum_area = np.cumsum(share_area, axis=1)
        gini = 1 - np.sum((cum_area[:, 1:] + cum_area[:, :-1]) * np.diff(cum_pop, axis=1), axis=1)
        out["holding_gini"] = np.clip(gini, 0.05, 0.95)

        out["fragmentation"] = np.clip(rng.normal(
            np.where(out["avg_holding_ha"] < 1.5, 3.4, 2.1), 0.6), 1.0, 7.0)
        return out


def build(spine: dict[str, pd.DataFrame], seed: int = 20260822) -> pd.DataFrame:
    soil = SoilConnector(spine, seed).run()
    crop = CroppingConnector(spine, seed).run()
    hold = HoldingsConnector(spine, seed).run()

    prov = pd.DataFrame({
        "village_id": soil["village_id"],
        "prov_soil": soil.pop("provenance"),
        "prov_crop": crop.pop("provenance"),
        "prov_holdings": hold.pop("provenance"),
    })
    out = (soil.merge(crop, on="village_id").merge(hold, on="village_id").merge(prov, on="village_id"))
    from pipeline.common import weakest_provenance
    out["provenance"] = [weakest_provenance(r) for r in
                         zip(out["prov_soil"], out["prov_crop"], out["prov_holdings"])]
    write_table(out, CURATED / "village_layers.parquet")
    return out


if __name__ == "__main__":
    from pipeline.common import read_table
    sp = {k: read_table(CURATED / f"geo_{k}.parquet") for k in ("districts", "blocks", "villages")}
    build(sp)
