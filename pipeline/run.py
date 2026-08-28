"""Pipeline runner.

    python -m pipeline.run                 # full build
    python -m pipeline.run --stage ucm     # one stage
    python -m pipeline.run --from features # this stage onward
    python -m pipeline.run --manifest      # what ran real vs synthetic

Stages are ordered by dependency; each reads the previous stage's parquet from
data/curated or data/marts, so any stage can be re-run alone once its inputs exist.
"""
from __future__ import annotations

import argparse
import time

from pipeline.common import CURATED, Manifest, read_table, log

LOG = log("run")

# micromarkets reads village_totals (export) + competitive_landscape (compete) + agroclimate,
# and rewrites the segmentation that insights consumes -- so it runs after compete, before
# insights. Demand scoring does not need the new labels (they are display-only).
STAGES = ["geo", "ingest", "assets", "competition", "sku", "features", "agroclimate",
          "ucm", "cluster", "factors", "score", "export", "compete", "micromarkets",
          "operations", "archetype_sales", "archetype_ucm", "insights", "shapes",
          "dealers", "subsidy"]


def _spine():
    return {k: read_table(CURATED / f"geo_{k}.parquet")
            for k in ("districts", "blocks", "villages")}


def stage_geo():
    from pipeline.transform import geo_spine
    geo_spine.build()


def stage_ingest():
    from pipeline.ingest import village_layers, district_series
    sp = _spine()
    village_layers.build(sp)
    district_series.build(sp)


def stage_assets():
    from pipeline.simulate import assets
    assets.build(_spine(), read_table(CURATED / "village_layers.parquet"),
                 read_table(CURATED / "district_series.parquet"))


def stage_competition():
    from pipeline.simulate import competition
    competition.build(_spine())


def stage_sku():
    from pipeline.simulate import sku_history
    sku_history.build(_spine(), read_table(CURATED / "village_layers.parquet"),
                      read_table(CURATED / "village_assets.parquet"))


def stage_features():
    from pipeline.features import build as fb
    fb.build()


def stage_ucm():
    from pipeline.ucm import model
    model.fit_all()


def stage_cluster():
    from pipeline.cluster import segment
    segment.build()


def stage_micromarkets():
    """Micro-market layer + base-segment x HP-belt archetypes; rewrites the segmentation."""
    from pipeline.cluster import micromarkets
    micromarkets.build()


def stage_operations():
    """Modelled operational metrics per micro-market + product/sales-issue diagnosis."""
    from pipeline.simulate import operations
    operations.build()


def stage_archetype_sales():
    """Simulated daily sales history per archetype -- input panel for the archetype UCM."""
    from pipeline.simulate import archetype_sales
    archetype_sales.build()


def stage_archetype_ucm():
    """Per-archetype UCM: baseline trend+seasonal vs weather/holiday/promo/price/competitor uplift."""
    from pipeline.ucm import archetype_model
    archetype_model.fit_all()


def stage_factors():
    from pipeline.score import factors
    factors.build()


def stage_score():
    from pipeline.score import propensity
    propensity.build()


def stage_export():
    from pipeline.export import marts
    marts.build()


def stage_compete():
    """Competitive choice model, external contests and internal cannibalisation."""
    from pipeline.score import competition_model
    competition_model.build()


def stage_shapes():
    """Simplified map geometry per zoom level for the drill-down map."""
    from pipeline.export import shapes
    shapes.build()


def stage_dealers():
    """Real dealer-network coverage (own vs competitor) from the ITL/EY dealer databases."""
    from pipeline.ingest import dealers
    dealers.build()


def stage_agroclimate():
    """Real agro-climatic district profile (temperature, rainfall, crop-mix)."""
    from pipeline.ingest import agroclimate
    agroclimate.build()


def stage_subsidy():
    """Real equipment-subsidy rates by state (PB/MH real, MP via SMAM proxy)."""
    from pipeline.ingest import subsidy
    subsidy.build()


def stage_insights():
    """Village-level micro-segments, action segments and per-village narratives.

    Runs after export because it reads village_totals -- the operational layer sits on
    top of the scored marts rather than beside them.
    """
    from pipeline.cluster import village_insights
    village_insights.build()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=STAGES)
    ap.add_argument("--from", dest="start", choices=STAGES)
    ap.add_argument("--manifest", action="store_true")
    a = ap.parse_args()

    if a.manifest:
        m = Manifest.summary()
        cols = [c for c in ["source", "mode", "provenance", "rows", "coverage_pct", "error"]
                if c in m.columns]
        print(m[cols].to_string(index=False))
        return

    todo = ([a.stage] if a.stage
            else STAGES[STAGES.index(a.start):] if a.start
            else STAGES)

    t0 = time.time()
    for s in todo:
        LOG.info("=" * 20 + f" stage: {s} " + "=" * 20)
        t = time.time()
        globals()[f"stage_{s}"]()
        LOG.info("stage %s done in %.1fs", s, time.time() - t)
    LOG.info("pipeline complete in %.1fs", time.time() - t0)


if __name__ == "__main__":
    main()
