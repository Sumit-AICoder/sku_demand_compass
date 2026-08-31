"""ACT (stage 4) -- the archetype execution playbook.

Split out of `main.py` because this is the one endpoint that writes a document rather
than returning a mart. It answers the client's own question -- *"for this archetype,
these are the 10 things I want to do"* -- organised as the seven use cases the business
team defined:

    1 Network Expansion & Development      5 Activity Plan
    2 Customer growth                      6 Sales planning
    3 Product development                  7 Incentives & Consumer Schemes
    4 Inventory

Two rules hold the numbers together.

**One mechanism per play.** The plays are unchanged from the version that lived in
`main.py`: reach owns dealer accessibility, approval owns finance, effort owns BD activity
at today's rates, execution owns whatever conversion quality is left after those two,
price owns the archetype's own fitted betas, policy owns subsidy. Re-homing them under
seven headings moves where they are *printed*, never what they are worth -- every play
appears in exactly one use case, and use cases 2 and 4 carry no addend at all because
they allocate volume the other plays create rather than adding their own.

**Every qualitative line names its evidence.** The survey has not been run (EY Primary,
one-time study -- see `inputs/Micro-market tool - Data points available.xlsx` rows 12-16),
so it is modelled from `village_factors`, whose 44 sub-factors are percentile-ranked
0-100 nationally. A score of 72 on `F1_credit_depth` means this scope sits at the 72nd
percentile of Indian villages for credit depth -- a statement about real data, not an
invented survey response. Each modelled claim carries the sub-factor and percentile that
produced it, and the whole layer hot-swaps at one function when the study lands.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import pandas as pd


def _m():
    """Deferred import of the API module.

    `main` imports this module at load time, so the reverse import has to happen at call
    time. Everything shared -- the connection, the taxonomy-aware grain, the bucket rule,
    the SKU basket -- already lives there and is reused rather than reimplemented.
    """
    from api import main
    return main


# ---------------------------------------------------------------- the modelled survey

# All 44 sub-factors, each percentile-ranked 0-100 across every village in the country
# (verified: mean 50.0, sd 28.9 on every one). Averaged over a scope they read directly
# as "this place versus the average Indian village".
SUBFACTORS = [
    "F1_crop_realisation", "F1_mandi_price_index", "F1_msp_exposure", "F1_credit_depth",
    "F1_input_intensity",
    "F2_avg_holding_ha", "F2_fragmentation", "F2_large_holding_share",
    "F3_tractor_density", "F3_new_sales_trend", "F3_hp_mix_skew",
    "F4_farm_power_kw_ha", "F4_rural_wage_index", "F4_labour_scarcity", "F4_outmigration",
    "F5_cropping_intensity", "F5_high_value_share", "F5_crop_diversity",
    "F6_smam_intensity", "F6_chc_programme", "F6_fpo_density",
    "F7_rainfall_departure", "F7_irrigation_ratio", "F7_reservoir_status", "F7_ndvi_anomaly",
    "F8_chc_density", "F8_rental_ecosystem", "F8_agri_service_prov",
    "F9_precision_adoption", "F9_progressive_farmer", "F9_digital_access",
    "F10_dealer_accessibility", "F10_service_density", "F10_spares_index",
    "F10_demo_activity",
]

SURVEY_PROV = "EY primary · modelled"

# `modelled` means "computed from real inputs through the model". Two things on this page
# are not that, and saying so matters more than a tidy badge:
#
#   JUDGEMENT  -- a stated rule of thumb with no data behind it at all. The split between
#                 activity types, the training list, the advocacy programme: these are
#                 written from experience, not derived. Nothing in the marts carries them.
#   FUNNEL     -- rests on the BD funnel, which `pipeline/simulate/operations.py` generates
#                 and the manifest marks `simulated`. Real once ITL supplies two years of
#                 activity, enquiry and delivery actuals; a plausible shape until then.
JUDGEMENT = "judgement"
FUNNEL_PROV = "simulated · ITL pending"


def _clip(v: float, lo: float, hi: float) -> float:
    return float(min(max(v, lo), hi))


def _pctile_word(v: float) -> str:
    """How far from the national middle, in words. Keeps the evidence strings readable
    without asking the reader to hold "50 = national median" in their head."""
    d = v - 50
    if d >= 25:
        return "far above the national village"
    if d >= 10:
        return "above the national village"
    if d <= -25:
        return "far below the national village"
    if d <= -10:
        return "below the national village"
    return "around the national village"


def _ordinal(n: int) -> str:
    """11th, 21st, 81st -- not "81th". The evidence strings are read by people."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _ev(f: dict, *keys: str) -> str:
    """Evidence string: the sub-factors behind a claim, with their percentiles."""
    parts = [f"{k} at the {_ordinal(round(f.get(k, 50)))} percentile" for k in keys]
    return "; ".join(parts) + f" — {_pctile_word(np.mean([f.get(k, 50) for k in keys]))}."


# Each driver is a named thing a farmer weighs, scored 0-100 from sub-factors that are
# actually observed. `hi` sub-factors push the score up, `lo` ones push it up when they
# are LOW (a thin credit market makes price and EMI matter more, not less).
DRIVERS = [
    ("Running cost / fuel economy", ["F1_input_intensity"], ["F1_crop_realisation"],
     "Lead with fuel-per-acre and cost-per-hour proof, not peak horsepower.",
     "awareness"),
    ("Purchase price & finance terms", [], ["F1_credit_depth", "F1_mandi_price_index"],
     "Lead with EMI, down-payment and tie-up finance; price is the gate here.",
     "finance"),
    ("Timeliness & labour substitution", ["F4_labour_scarcity", "F4_rural_wage_index",
                                          "F4_outmigration"], [],
     "Sell the operation window: acres covered per day when labour is not available.",
     "awareness"),
    ("Service response & spares", [], ["F10_service_density", "F10_spares_index"],
     "Commit to a response time and a spares list before the sale, not after.",
     "service"),
    ("Dealer proximity", [], ["F10_dealer_accessibility"],
     "Distance is doing the deciding here — a touchpoint beats a message.",
     "service"),
    ("Subsidy availability", ["F6_smam_intensity", "F6_chc_programme"], [],
     "Scheme paperwork support at the counter converts better than a discount.",
     "finance"),
    ("Versatility across crops", ["F5_crop_diversity", "F5_cropping_intensity"], [],
     "Position multi-crop use — one machine across the rotation, not one job.",
     "product"),
    ("Proven durability over new features", [], ["F9_precision_adoption", "F9_digital_access"],
     "Proof beats specification: hours run, owners nearby, resale held.",
     "awareness"),
    ("New technology & precision", ["F9_precision_adoption", "F9_progressive_farmer"], [],
     "Progressive-farmer demos and feature-led content will land here.",
     "awareness"),
    ("Rental / custom-hiring economics", ["F8_chc_density", "F8_rental_ecosystem"], [],
     "Sell the payback: hire-out days per year, not ownership pride.",
     "finance"),
]


def _driver_scores(f: dict) -> list[dict]:
    out = []
    for name, hi, lo, implication, barrier in DRIVERS:
        vals = [f.get(k, 50.0) for k in hi] + [100.0 - f.get(k, 50.0) for k in lo]
        score = float(np.mean(vals)) if vals else 50.0
        out.append({
            "driver": name, "score": round(score, 1), "vs_national": round(score - 50, 1),
            "implication": implication, "barrier": barrier,
            "evidence": _ev(f, *(hi + lo)),
        })
    return sorted(out, key=lambda d: -d["score"])


# The complaint set. Whichever scores highest becomes `top_complaint`, and that is what
# re-orders the seven cards and seeds the content and activity themes -- the client's own
# worked example ("the customer is saying mileage, so your content should focus on
# mileage awareness and your activities should focus on mileage") runs through here.
def _complaints(f: dict, product_fit: float) -> list[dict]:
    c = [
        ("Service response and spares availability",
         100 - np.mean([f.get("F10_service_density", 50), f.get("F10_spares_index", 50)]),
         "service", _ev(f, "F10_service_density", "F10_spares_index")),
        ("The nearest dealer is too far to service the machine",
         100 - f.get("F10_dealer_accessibility", 50),
         "service", _ev(f, "F10_dealer_accessibility")),
        ("Running cost — diesel per acre is too high",
         np.mean([f.get("F1_input_intensity", 50), 100 - f.get("F1_crop_realisation", 50)]),
         "awareness", _ev(f, "F1_input_intensity", "F1_crop_realisation")),
        ("Finance approval and the EMI burden",
         100 - f.get("F1_credit_depth", 50),
         "finance", _ev(f, "F1_credit_depth")),
        ("Never seen the machine work on a field like mine",
         100 - f.get("F10_demo_activity", 50),
         "awareness", _ev(f, "F10_demo_activity")),
        ("The product does not suit our soil and crop",
         (1 - float(product_fit)) * 100,
         "product", f"product fit {product_fit:.0%} against the archetype's agro-climate."),
    ]
    return sorted(({"complaint": n, "score": round(float(s), 1), "barrier": b, "evidence": e}
                   for n, s, b, e in c), key=lambda x: -x["score"])


def _praises(f: dict, product_fit: float) -> list[dict]:
    p = [
        ("Dealer is close and reachable", f.get("F10_dealer_accessibility", 50),
         _ev(f, "F10_dealer_accessibility")),
        ("Spares are easy to get", f.get("F10_spares_index", 50), _ev(f, "F10_spares_index")),
        ("The machine suits our soil and crop", float(product_fit) * 100,
         f"product fit {product_fit:.0%} against the archetype's agro-climate."),
        ("Scheme support is available", f.get("F6_smam_intensity", 50),
         _ev(f, "F6_smam_intensity")),
        ("Finance is easy to arrange", f.get("F1_credit_depth", 50), _ev(f, "F1_credit_depth")),
    ]
    return sorted(({"praise": n, "score": round(float(s), 1), "evidence": e} for n, s, e in p),
                  key=lambda x: -x["score"])


# ---------------------------------------------------------------- scope

def scope_grain(line: str, archetype_id: str, district_id: str | None = None,
                micro_market_id: str | None = None) -> pd.DataFrame:
    """The micro-markets a playbook is being written for.

    Narrowing runs off `_current_grain`, not the mart, so a scope still resolves for an
    archetype the client created by splitting a zone on Configure.
    """
    M = _m()
    g = M._current_grain(line)
    g = g[g["archetype_id"] == archetype_id]
    if district_id:
        g = g[g["district_id"] == district_id]
    if micro_market_id:
        g = g[g["micro_market_id"] == micro_market_id]
    return g


@lru_cache(maxsize=8)
def _line_lo_pot(stamp: str, line: str) -> float:
    """The 20th-percentile micro-market demand for the whole line.

    `rollup` uses this to label a diagnosis, and left to its own default it takes the
    quantile of whatever frame it is handed -- which for a single-micro-market scope is
    that micro-market itself, making the label meaningless. Pinning it to the line keeps
    a district's diagnosis comparable with the archetype's.
    """
    return float(_m()._current_grain(line)["potential_units_yr"].quantile(0.20))


def scope_row(line: str, archetype_id: str, grain: pd.DataFrame) -> pd.Series:
    """Situation numbers for the selected scope.

    Reuses the pipeline's own `rollup` -- additive columns summed, rates TIV-weighted,
    coverage a plain mean -- rather than a second aggregation that could drift from it.
    Bucket, leader and rank are properties of the whole archetype and are carried over
    unchanged; the frontend says so on screen rather than implying they were recomputed
    for a single district.
    """
    M = _m()
    from pipeline.simulate.operations import rollup

    arch = M._archetype_row(archetype_id, line)
    if grain.empty:
        return arch
    r = rollup(grain, lo_pot=_line_lo_pot(M._stamp(), line)).iloc[0]
    out = arch.copy()
    for c in ("n_micromarkets", "n_villages", "tiv", "avg_sonalika_share",
              "potential_units_yr", "sonalika_sales_units", "activities_yr",
              "enquiries_yr", "deliveries_yr", "conversion_rate", "product_fit",
              "sales_effort", "cracked_pct", "sales_coverage", "service_coverage",
              "states", "diagnosis"):
        if c in r:
            out[c] = r[c]
    return out


def _scope_villages_sql(grain: pd.DataFrame):
    """Register the scope's micro-markets so the village-grain marts can be joined to it.

    Same `mm_sel` device `_archetype_rivals` already uses: the membership comes from the
    taxonomy in force, so it finds villages the mart has never been told about.
    """
    return grain[["micro_market_id", "district_id"]].drop_duplicates()


@lru_cache(maxsize=64)
def _scope_factors_cached(stamp: str, line: str, aid: str, district_id: str | None,
                          micro_market_id: str | None) -> dict:
    return _scope_factors(scope_grain(line, aid, district_id, micro_market_id))


def scope_factors(grain: pd.DataFrame, key: tuple | None = None) -> dict:
    """Mean sub-factor percentile across the scope's villages. One query, `mm_sel`-joined.

    Cached on the scope key when one is given: 105,246 villages is a real scan, and the
    answer cannot change while the taxonomy and the scope stay put.
    """
    if key is not None:
        return dict(_scope_factors_cached(*key))
    return _scope_factors(grain)


def _scope_factors(grain: pd.DataFrame) -> dict:
    M = _m()
    if grain.empty:
        return {}
    mm = _scope_villages_sql(grain)
    M.con().register("mm_sel", mm)
    try:
        cols = ", ".join(f"avg(f.{c}) AS {c}" for c in SUBFACTORS)
        row = M.con().execute(f"""
            SELECT count(*) AS n_villages, {cols}
            FROM village_factors f
            JOIN village_micromarket v USING (village_id)
            JOIN mm_sel USING (micro_market_id)
        """).fetchdf().to_dict("records")
    finally:
        M.con().unregister("mm_sel")
    return row[0] if row else {}


@lru_cache(maxsize=64)
def _scope_agro_cached(stamp: str, line: str, aid: str, district_id: str | None,
                       micro_market_id: str | None) -> dict:
    return _scope_agro(scope_grain(line, aid, district_id, micro_market_id), line)


def scope_agro(grain: pd.DataFrame, line: str, key: tuple | None = None) -> dict:
    """Field conditions across the scope, from the real village layer -- what an unmet
    product need has to be argued from. The heaviest of the three scans, so it is cached
    on the scope key whenever one is available."""
    if key is not None:
        return dict(_scope_agro_cached(*key))
    return _scope_agro(grain, line)


def _scope_agro(grain: pd.DataFrame, line: str) -> dict:
    M = _m()
    if grain.empty:
        return {}
    mm = _scope_villages_sql(grain)
    M.con().register("mm_sel", mm)
    try:
        row = M.con().execute("""
            SELECT avg(i.avg_holding_ha)          AS holding_ha,
                   avg(i.irrigation_ratio)        AS irrigation,
                   avg(i.workability)             AS workability,
                   avg(i.residue_burden_per_ha)   AS residue,
                   avg(i.attach_rate)             AS attach_rate,
                   avg(i.peer_attach_micro)       AS peer_attach,
                   avg(i.dealer_distance_km)      AS dealer_km,
                   avg(i.replacement_pressure)    AS replacement,
                   mode(i.soil_texture)           AS soil,
                   mode(i.dominant_crop)          AS crop
            FROM village_insights_pl i
            JOIN village_micromarket v USING (village_id)
            JOIN mm_sel USING (micro_market_id)
            WHERE i.product_line = ?
        """, [line]).fetchdf().to_dict("records")
    finally:
        M.con().unregister("mm_sel")
    return row[0] if row else {}


@lru_cache(maxsize=64)
def _scope_segments_cached(stamp: str, line: str, aid: str, district_id: str | None,
                           micro_market_id: str | None) -> tuple:
    return tuple(_scope_segments(scope_grain(line, aid, district_id, micro_market_id), line))


def scope_segments(grain: pd.DataFrame, line: str, key: tuple | None = None) -> list[dict]:
    """Village counts by action segment inside the scope -- the four boxes the Summary
    page derives from 'is there unserved demand here' x 'is a dealer close enough'."""
    if key is not None:
        return [dict(x) for x in _scope_segments_cached(*key)]
    return _scope_segments(grain, line)


def _scope_segments(grain: pd.DataFrame, line: str) -> list[dict]:
    M = _m()
    if grain.empty:
        return []
    mm = _scope_villages_sql(grain)
    M.con().register("mm_sel", mm)
    try:
        return M.clean(M.con().execute("""
            SELECT i.action_segment AS segment, count(*) AS villages,
                   sum(i.potential_units_yr) AS units, sum(i.headroom) AS headroom,
                   avg(i.dealer_distance_km) AS dealer_km,
                   avg(i.attach_rate) AS attach_rate
            FROM village_insights_pl i
            JOIN village_micromarket v USING (village_id)
            JOIN mm_sel USING (micro_market_id)
            WHERE i.product_line = ?
            GROUP BY 1 ORDER BY units DESC
        """, [line]).fetchdf().to_dict("records"))
    finally:
        M.con().unregister("mm_sel")


# ---------------------------------------------------------------- survey, assembled

MONTHS = ["", "January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

BARRIER_LABEL = {"finance": "Finance access", "service": "Service & reach",
                 "awareness": "Awareness", "product": "Product fit"}


def build_survey(f: dict, agro: dict, ctx: dict) -> dict:
    """The customer layer, modelled.

    Nothing here is a survey response. Every figure is a transformation of village data
    the pipeline already publishes, and carries the sub-factor and percentile it came
    from, so a reader can disagree with the mapping rather than having to take the number
    on faith. When the EY primary study lands, this function is the only thing that has
    to change.
    """
    if not f:
        return {"provenance": SURVEY_PROV, "n_villages": 0, "purchase_drivers": [],
                "perception": {}, "buying_behaviour": {}, "switching_triggers": [],
                "unmet_needs": [], "channel_mix": {}, "top_barrier": "finance"}

    fit = float(ctx.get("product_fit") or 0.0)
    drivers = _driver_scores(f)
    complaints = _complaints(f, fit)
    praises = _praises(f, fit)

    # --- perception. An experience index off the four things a customer actually meets
    # after the sale, plus how well we convert relative to the belt -- a scope where we
    # convert far below peers is one where something in the experience is wrong.
    conv, peer_conv = float(ctx.get("conversion_rate") or 0), float(ctx.get("peer_conv") or 0)
    conv_idx = _clip(50 * conv / peer_conv, 0, 100) if peer_conv > 0 else 50.0
    exp = float(np.mean([f.get("F10_service_density", 50), f.get("F10_spares_index", 50),
                         f.get("F10_dealer_accessibility", 50), conv_idx, fit * 100]))
    satisfied = round(_clip(25 + 0.60 * exp, 20, 85))
    detractor = round(_clip(60 - 0.50 * exp, 5, 55))

    # --- buying behaviour
    own = round(_clip(50 + 0.4 * (f.get("F2_avg_holding_ha", 50) - 50)
                      - 0.3 * (f.get("F8_rental_ecosystem", 50) - 50), 10, 95))
    finance_led = round(_clip(35 + 0.5 * (f.get("F1_credit_depth", 50) - 50), 15, 85))
    subsidy_led = round(_clip(15 + 0.5 * (f.get("F6_smam_intensity", 50) - 50)
                              + float(ctx.get("avg_subsidy") or 0) / 4, 5, 70))
    infl = max([
        ("progressive farmers who already own one", f.get("F9_progressive_farmer", 50)),
        ("custom-hiring and rental operators", f.get("F8_rental_ecosystem", 50)),
        ("the dealer counter and its sales staff", f.get("F10_dealer_accessibility", 50)),
        ("FPO and cooperative office-bearers", f.get("F6_fpo_density", 50)),
    ], key=lambda x: x[1])

    # --- channels. Digital share rises with real digital access; the dealer counter takes
    # a slice that grows with proximity; BTL is what is left, which is why a remote,
    # low-connectivity scope ends up BTL-heavy rather than being asserted as such.
    digital = round(_clip(15 + 0.6 * (f.get("F9_digital_access", 50) - 50), 5, 70))
    dealer_ch = round(_clip(20 + 0.3 * (f.get("F10_dealer_accessibility", 50) - 50), 10, 45))
    btl = max(100 - digital - dealer_ch, 0)

    # --- switching triggers, anchored on the rival who is actually closest
    rivals = ctx.get("rivals") or []
    top_rival = rivals[0]["rival"] if rivals else "the local unbranded segment"
    triggers = sorted([
        {"trigger": "Service and spares reliability", "from_rival": top_rival,
         "strength": round(100 - np.mean([f.get("F10_service_density", 50),
                                          f.get("F10_spares_index", 50)]), 1),
         "evidence": _ev(f, "F10_service_density", "F10_spares_index")},
        {"trigger": f"Price and scheme aggression from {top_rival}", "from_rival": top_rival,
         "strength": round(_clip(float(ctx.get("leader_share") or 0) * 100 * 2.5, 5, 95), 1),
         "evidence": f"{top_rival} holds {(ctx.get('leader_share') or 0):.0%} against our "
                     f"{(ctx.get('share') or 0):.1%} in this scope."},
        {"trigger": "The machine does not suit the soil and crop here", "from_rival": top_rival,
         "strength": round((1 - fit) * 100, 1),
         "evidence": f"product fit {fit:.0%}; dominant soil {agro.get('soil') or 'n/a'}, "
                     f"crop {agro.get('crop') or 'n/a'}."},
        {"trigger": "Dealer too far to keep the machine running", "from_rival": top_rival,
         "strength": round(100 - f.get("F10_dealer_accessibility", 50), 1),
         "evidence": _ev(f, "F10_dealer_accessibility")},
    ], key=lambda x: -x["strength"])

    # --- unmet needs, argued from the field conditions rather than asserted
    needs = []
    hold = float(agro.get("holding_ha") or 0)
    if hold and hold < 2.0:
        needs.append(("A compact variant sized for sub-2 ha holdings",
                      round(_clip((2.0 - hold) * 45, 5, 95), 1),
                      f"mean holding {hold:.2f} ha across the scope's villages."))
    if float(agro.get("workability") or 1) < 0.5:
        needs.append(("Heavier frame and higher torque for hard-working soils",
                      round(_clip((0.5 - float(agro['workability'])) * 200, 5, 95), 1),
                      f"workability {float(agro['workability']):.2f} on "
                      f"{agro.get('soil') or 'local'} soil."))
    if float(agro.get("residue") or 0) > 1.0:
        needs.append(("Residue-handling capability in the same pass",
                      round(_clip(float(agro["residue"]) * 30, 5, 95), 1),
                      f"residue burden {float(agro['residue']):.2f} t/ha — straw has to go "
                      f"somewhere before the next sowing."))
    if float(agro.get("irrigation") or 1) < 0.4:
        needs.append(("A rainfed / dryland configuration",
                      round(_clip((0.4 - float(agro["irrigation"])) * 200, 5, 95), 1),
                      f"irrigation ratio {float(agro['irrigation']):.2f} — the window is "
                      f"short and moisture-dependent."))
    if fit < 0.6:
        needs.append((f"An adapted {ctx.get('hp_belt') or ''} product for this agro-climate".strip(),
                      round((1 - fit) * 100, 1),
                      f"product fit {fit:.0%}, below the 0.55 floor the bucket rule uses."))
    gap = float(agro.get("peer_attach") or 0) - float(agro.get("attach_rate") or 0)
    if gap > 0.05:
        needs.append(("Implements the peer micro-markets attach that we do not sell here",
                      round(_clip(gap * 200, 5, 95), 1),
                      f"attach rate {float(agro.get('attach_rate') or 0):.2f} against a peer "
                      f"{float(agro.get('peer_attach') or 0):.2f} in like micro-markets."))
    unmet = sorted(({"need": n, "severity": s, "evidence": e} for n, s, e in needs),
                   key=lambda x: -x["severity"])[:5]

    top_barrier = complaints[0]["barrier"] if complaints else "finance"
    peak = int(ctx.get("peak_month") or 0)

    return {
        "provenance": SURVEY_PROV,
        "n_villages": int(f.get("n_villages") or 0),
        "purchase_drivers": drivers[:6],
        "perception": {
            "satisfied_pct": satisfied, "detractor_pct": detractor,
            "neutral_pct": max(100 - satisfied - detractor, 0),
            "nps_proxy": satisfied - detractor,
            "top_praise": praises[0]["praise"] if praises else None,
            "praise_evidence": praises[0]["evidence"] if praises else None,
            "top_complaint": complaints[0]["complaint"] if complaints else None,
            "complaint_evidence": complaints[0]["evidence"] if complaints else None,
            "complaints": complaints[:4],
            "evidence": f"experience index {exp:.0f}/100 from service density, spares, dealer "
                        f"reach, product fit and our conversion against the "
                        f"{ctx.get('hp_belt') or ''} belt median.",
        },
        "buying_behaviour": {
            "own_vs_rent_pct": own, "rent_pct": 100 - own,
            "finance_led_pct": finance_led, "subsidy_led_pct": subsidy_led,
            "season_peak_month": MONTHS[peak] if 1 <= peak <= 12 else None,
            "influencer": infl[0],
            "evidence": _ev(f, "F2_avg_holding_ha", "F8_rental_ecosystem", "F1_credit_depth",
                            "F6_smam_intensity"),
        },
        "switching_triggers": triggers,
        "unmet_needs": unmet,
        "channel_mix": {
            "digital_pct": digital, "btl_pct": btl, "dealer_pct": dealer_ch,
            "evidence": _ev(f, "F9_digital_access", "F10_dealer_accessibility"),
        },
        "top_barrier": top_barrier,
        "top_barrier_label": BARRIER_LABEL.get(top_barrier, top_barrier),
    }


# ---------------------------------------------------------------- plays

# Which of the seven cards each mechanism is printed under. The mapping is presentational
# only -- a play's units are computed once and appear once, so re-homing cannot
# double-count. Cards 2 (Customer growth) and 4 (Inventory) own no mechanism at all: they
# allocate and target the volume the other cards create.
PLAY_CARD = {
    "reach": "network", "retention": "network",
    "product": "product",
    "effort": "activity",
    "approval": "sales", "execution": "sales",
    "price": "incentives", "policy": "incentives",
}

# One new touchpoint is assumed to bring about three neighbouring micro-markets inside
# reach. Stated here rather than buried, because it is the number that turns "these
# micro-markets are too far" into "open this many dealers".
MM_PER_TOUCHPOINT = 3.0

# One field-facing person is assumed to run 8 activities a month. Also an assumption, also
# named on screen -- it is what converts an activity target into a manpower number.
ACTIVITIES_PER_FTE_MONTH = 8.0


def _where_rows(g: pd.DataFrame, why, limit: int = 10) -> list[dict]:
    """The named places a play is executed in, biggest first."""
    if g.empty:
        return []
    g = g.sort_values("potential_units_yr", ascending=False).head(limit)
    return [{
        "micro_market": str(row["micro_market_id"]), "district": str(row["district"]),
        "state": str(row["state"]), "tiv": round(float(row["tiv"])),
        "units": round(float(row["potential_units_yr"])), "why_here": why(row),
    } for _, row in g.iterrows()]


def _kpi(metric: str, baseline, target, by_when: str) -> dict:
    return {"metric": metric, "baseline": baseline, "target": target, "by_when": by_when}


def build_plays(line: str, aid: str, grain: pd.DataFrame, r: pd.Series, a,
                survey: dict, basket: list[dict], rivals: list[dict],
                approval_now: float, approval_new: float, peer_conv: float) -> list[dict]:
    """The six mechanism plays, plus the stop and protect modes -- unchanged arithmetic,
    now each carrying the execution spec that says who does what, where, and by when."""
    M = _m()
    demand = float(r["potential_units_yr"]) or 1.0
    deliveries = float(r["deliveries_yr"])
    enquiries = float(r["enquiries_yr"])
    activities = float(r["activities_yr"])
    share = float(r["avg_sonalika_share"])
    conv = float(r["conversion_rate"])
    fit = float(r["product_fit"])
    complaint = (survey.get("perception") or {}).get("top_complaint") or "no dominant complaint"
    drivers = survey.get("purchase_drivers") or []
    top_driver = drivers[0]["driver"] if drivers else "cost of ownership"

    plays: list[dict] = []

    # ---- 1. reach: more dealers ------------------------------------------------------
    # Density scales distance by (1+dd)^-0.5 and accessibility = exp(-km/decay), so the new
    # accessibility is the old one raised to that power. A micro-market that crosses _REACH
    # is newly sellable; one already above it just gets easier to serve.
    dd = max(a.dealer_density_pct, 0.0) / 100.0
    acc = grain["dealer_accessibility"].to_numpy()
    acc_new = np.power(np.clip(acc, 1e-6, 1.0), (1 + dd) ** -0.5)
    crossed = (acc < M._REACH) & (acc_new >= M._REACH)
    covered = acc >= M._REACH
    tiv_reached = float(grain.loc[crossed, "tiv"].sum())
    new_demand = float((grain.loc[crossed, "potential_units_yr"] * share).sum())
    access_lift = (0.55 + 0.45 * acc_new) / (0.55 + 0.45 * acc) - 1
    easier = float((grain["deliveries_yr"].to_numpy() * access_lift * covered).sum())

    cg = grain.loc[crossed].copy()
    by_district = (cg.groupby(["district", "state"])
                     .agg(mms=("micro_market_id", "size"), tiv=("tiv", "sum"),
                          units=("potential_units_yr", "sum"))
                     .reset_index().sort_values("units", ascending=False)) if len(cg) else pd.DataFrame()
    touchpoints = [{
        "district": str(x["district"]), "state": str(x["state"]),
        "micro_markets": int(x["mms"]),
        "touchpoints": int(max(1, math.ceil(x["mms"] / MM_PER_TOUCHPOINT))),
        "tiv_reached": round(float(x["tiv"])), "units": round(float(x["units"]) * share),
        "rationale": f"{int(x['mms'])} micro-markets and {round(float(x['tiv'])):,} tractors "
                     f"here sit outside commercial reach today",
    } for _, x in by_district.iterrows()][:12]
    n_touchpoints = sum(t["touchpoints"] for t in touchpoints)

    if tiv_reached > 0 or easier > 0:
        acc_map = dict(zip(grain["micro_market_id"], acc_new))
        plays.append({
            "play": f"Expand the dealer network {round(a.dealer_density_pct)}%",
            "owns": "reach", "use_case": "network",
            "detail": f"{int(crossed.sum())} micro-markets cross into commercial reach, "
                      f"{M.fmt_units(tiv_reached)} tractors with them; the rest get easier to serve",
            "units": round(new_demand + easier), "tiv_reached": round(tiv_reached),
            "confidence": "estimated", "mode": "grow",
            "execution": {
                "objective": f"Open {n_touchpoints} new dealers, so "
                             f"{M.fmt_units(tiv_reached)} tractors that are currently too far "
                             f"from any counter come within selling distance.",
                "why": [
                    f"“{complaint}” is the loudest thing customers here say — "
                    f"{(survey.get('perception') or {}).get('complaint_evidence') or ''}",
                    f"Dealer coverage across this scope is {float(r['sales_coverage']):.0%}; "
                    f"{int(crossed.sum())} of {len(grain)} micro-markets sit below the "
                    f"{M._REACH:.0%} accessibility floor.",
                    f"The average farmer here is "
                    f"{round(float(grain['service_distance_km'].mean()))} km from a counter. "
                    f"You cannot advertise your way past that.",
                ],
                "how": [
                    {"step": 1, "what": "Check the gap list is real",
                     "detail": "Go through the districts below, biggest first, and confirm "
                               "against the actual dealer file which really have no dealer "
                               "and which are just missing from our records.",
                     "when": "week 1–2"},
                    {"step": 2, "what": f"Appoint {n_touchpoints} new dealers",
                     "detail": f"About one for every {MM_PER_TOUCHPOINT:.0f} micro-markets "
                               f"that are out of reach. Put each one in the middle of its "
                               f"cluster rather than in the biggest village, so it can serve "
                               f"all of them.",
                     "when": "month 1–4"},
                    {"step": 3, "what": "Get spares in before the first sale",
                     "detail": "A counter that can sell but cannot repair gets one sale and "
                               "then nothing.",
                     "when": "with each appointment"},
                    {"step": 4, "what": "Run an opening event around each new dealer",
                     "detail": f"Build it around {top_driver.lower()} — the thing farmers "
                               f"here care about most.",
                     "when": "within 30 days of opening"},
                ],
                "where": _where_rows(
                    cg, lambda x: f"accessibility {float(x['dealer_accessibility']):.2f} → "
                                  f"{acc_map.get(x['micro_market_id'], 0):.2f}, "
                                  f"{round(float(x['tiv'])):,} tractors"),
                "cadence": f"{n_touchpoints} dealers over 4 months, then review each quarter",
                "owner": "Area Sales Manager · network development",
                "cost_note": f"The dealer count is an estimate at about "
                             f"{MM_PER_TOUCHPOINT:.0f} micro-markets each — not a site survey.",
                "kpi": _kpi("Micro-markets with a dealer close enough",
                            f"{int(covered.sum())} of {len(grain)}",
                            f"{int(covered.sum() + crossed.sum())} of {len(grain)}",
                            "12 months"),
            },
            "touchpoints": touchpoints,
        })

    # ---- 2. approval: finance access -------------------------------------------------
    # conv = approval x (0.55 + 0.45 x accessibility), so a proportional move in approval is
    # a proportional move in conversion, and deliveries follow.
    if approval_new > approval_now:
        units = round(deliveries * (approval_new / max(approval_now, 1e-6) - 1))
        plays.append({
            "play": f"Lift loan approval to {approval_new:.0%}",
            "owns": "approval", "use_case": "sales",
            "detail": f"{approval_now:.0%} today across this scope's villages; conversion "
                      f"moves with it one-for-one in the model's own identity",
            "units": units, "tiv_reached": None, "confidence": "estimated", "mode": "grow",
            "execution": {
                "objective": f"Get loan approvals from {approval_now:.0%} up to "
                             f"{approval_new:.0%}, so about {M.fmt_units(units)} sales a year "
                             f"stop falling through at the finance stage.",
                "why": [
                    f"{(survey.get('buying_behaviour') or {}).get('finance_led_pct', 0)}% of "
                    f"purchases here are credit-led — "
                    f"{(survey.get('buying_behaviour') or {}).get('evidence') or ''}",
                    f"Approval rate is real data, and it is what the sales model itself "
                    f"uses to work out how many enquiries turn into deliveries.",
                ],
                "how": [
                    {"step": 1, "what": "Find out why loans get rejected",
                     "detail": "Pull the last six months of rejected files and sort them by "
                               "financier and reason — papers, land record, credit score, "
                               "income proof.",
                     "when": "week 1–3"},
                    {"step": 2, "what": "Add a second and third financier",
                     "detail": "With one lender you are stuck with that lender's cut-off. Two "
                               "more, who lend differently, get more of the same farmers "
                               "approved.",
                     "when": "month 1–2"},
                    {"step": 3, "what": "Run loan camps before the season starts",
                     "detail": "Papers collected before the buying season turn into sales. "
                               "Papers collected during it turn into lost ones.",
                     "when": f"6 weeks before "
                             f"{(survey.get('buying_behaviour') or {}).get('season_peak_month') or 'the peak'}"},
                    {"step": 4, "what": "Train the counter on filling files properly",
                     "detail": "Most rejections are bad paperwork, not bad customers.",
                     "when": "month 2"},
                ],
                "where": _where_rows(
                    grain, lambda x: f"{round(float(x['enquiries_yr'])):,} enquiries a year at "
                                     f"{float(x['conversion_rate']):.0%} conversion"),
                "cadence": "one camp per district each quarter; review the financiers monthly",
                "owner": "Area Sales Manager · with the finance partner",
                "cost_note": None,
                "kpi": _kpi("Loans approved", f"{approval_now:.0%}", f"{approval_new:.0%}",
                            "2 quarters"),
            },
        })

    # ---- 3. effort: more BD activity at today's rates --------------------------------
    # Awareness scales what an extra visit yields; it is the one input with no data proxy
    # anywhere in the repo, so it stays an assumption named on screen.
    up = max(a.activity_uplift_pct, 0.0) / 100.0
    if up > 0:
        yield_mult = 0.6 + 0.8 * float(np.clip(a.awareness, 0, 1))
        extra_act = int(activities * up)
        units = round(deliveries * up * yield_mult)
        cm = survey.get("channel_mix") or {}
        plays.append({
            "play": f"Run {round(a.activity_uplift_pct)}% more BD activities",
            "owns": "effort", "use_case": "activity",
            "detail": f"{extra_act:,} more activities a year at today's {conv:.0%} conversion, "
                      f"scaled by the awareness assumption",
            "units": units, "tiv_reached": None, "confidence": "arithmetic", "mode": "grow",
            "execution": {
                "objective": f"Run {extra_act:,} more activities a year — about "
                             f"{round(extra_act / 12):,} a month — in the places that already "
                             f"convert best. Worth about {M.fmt_units(units)} more machines.",
                "why": [
                    f"The first thing farmers here say is “{complaint.lower()}”. Every "
                    f"activity has to answer that, not just be a general event.",
                    f"Of everyone we can reach here, {cm.get('btl_pct', 0)}% are reached on "
                    f"the ground, {cm.get('digital_pct', 0)}% on a phone, and "
                    f"{cm.get('dealer_pct', 0)}% at the counter.",
                    f"Today: {round(activities):,} activities → {round(enquiries):,} enquiries "
                    f"→ {round(deliveries):,} deliveries.",
                ],
                "how": [
                    {"step": 1, "what": "Decide the message before the count",
                     "detail": f"Every activity here leads with the answer to "
                               f"“{complaint.lower()}”.",
                     "when": "before the first activity"},
                    {"step": 2, "what": "Split the plan by micro-market, not by district",
                     "detail": "Put the extra activities where the unsold demand is, using "
                               "the table below. A district-wide average sends people to "
                               "empty villages.",
                     "when": "week 1"},
                    {"step": 3, "what": "Pick the right kind of activity",
                     "detail": f"{cm.get('btl_pct', 0)}% of the reach here is on the ground — "
                               f"field demos, harvest days, van campaigns. Use the "
                               f"{cm.get('digital_pct', 0)}% phone reach to follow up, not to "
                               f"lead.",
                     "when": "week 1–2"},
                    {"step": 4, "what": "Call back every enquiry the same day",
                     "detail": "An enquiry nobody follows up is money spent for nothing.",
                     "when": "ongoing"},
                ],
                "where": _where_rows(
                    grain, lambda x: f"{round(float(x['activities_yr'])):,} activities today → "
                                     f"{round(float(x['activities_yr']) * (1 + up)):,}; "
                                     f"{round(float(x['potential_units_yr'])):,} units of demand"),
                "cadence": f"about {round(extra_act / 12):,} extra activities a month, "
                           f"reviewed against enquiries monthly",
                "owner": "Area Sales Manager · with dealer sales teams",
                "cost_note": "How many people already know the brand is the one thing we have "
                             "no data for anywhere. It decides how much an extra visit is "
                             "worth, and it is yours to set.",
                "kpi": _kpi("Activities a year", f"{round(activities):,}",
                            f"{round(activities * (1 + up)):,}", "12 months"),
            },
        })

    # ---- 4. execution quality: whatever peer conversion is left ----------------------
    claimed = sum(p["units"] for p in plays if p["owns"] in ("reach", "approval"))
    residual = enquiries * max(peer_conv - conv, 0.0) - claimed
    if residual > 0:
        plays.append({
            "play": "Close the rest of the conversion gap",
            "owns": "execution", "use_case": "sales",
            "detail": f"{conv:.1%} today vs {peer_conv:.1%} across the {r['hp_belt']} belt, "
                      f"after what reach and finance already explain",
            "units": round(residual), "tiv_reached": None, "confidence": "arithmetic",
            "mode": "grow",
            "execution": {
                "objective": f"Close {peer_conv:.1%} of enquiries instead of {conv:.1%} — "
                             f"what comparable markets already manage. Worth about "
                             f"{M.fmt_units(residual)} more machines, on top of what better "
                             f"dealer reach and finance already give us.",
                "why": [
                    f"Markets like this one close {peer_conv:.1%} of their enquiries. We "
                    f"close {conv:.1%}. Dealer distance and finance explain part of the gap; "
                    f"the rest is how we sell.",
                    f"Satisfied customers here run at "
                    f"{(survey.get('perception') or {}).get('satisfied_pct', 0)}% against "
                    f"{(survey.get('perception') or {}).get('detractor_pct', 0)}% detractors.",
                ],
                "how": [
                    {"step": 1, "what": "Count the drop-off at each step",
                     "detail": "Enquiry, demo, quote, loan file, delivery. The problem is at "
                               "one of those five steps — an overall percentage tells you "
                               "nothing about which.",
                     "when": "week 1–4"},
                    {"step": 2, "what": "Make sure every serious enquiry gets a demo",
                     "detail": "A machine nobody has seen working loses to the one the "
                               "neighbour already owns.",
                     "when": "month 1"},
                    {"step": 3, "what": "Give the counter an answer sheet per rival",
                     "detail": "One page each: what they claim, what we say back, what we "
                               "show. The Customer growth tab has the content.",
                     "when": "month 1–2"},
                    {"step": 4, "what": "Review every lost sale weekly, with a reason",
                     "detail": "Price, finance, product, stock, doubts about service — which "
                               "reason wins tells you which of the other six tabs to push.",
                     "when": "weekly"},
                ],
                "where": _where_rows(
                    grain[grain["conversion_rate"] < peer_conv] if len(grain) else grain,
                    lambda x: f"converting {float(x['conversion_rate']):.0%} against a belt "
                              f"median of {peer_conv:.0%}"),
                "cadence": "review lost sales weekly, the step-by-step drop-off monthly",
                "owner": "Area Sales Manager · with the dealer principal",
                "cost_note": None,
                "kpi": _kpi("Enquiries we close", f"{conv:.1%}", f"{peer_conv:.1%}",
                            "12 months"),
            },
        })
    return plays


def build_scheme_plays(line: str, aid: str, grain: pd.DataFrame, r: pd.Series,
                       survey: dict, basket: list[dict], scale: float = 1.0) -> list[dict]:
    """Price, promotion and subsidy -- the two levers that are policy rather than effort.

    The UCM betas are fitted per archetype and there is no district-level fit to narrow
    them to, so the *elasticity* stays archetype-grain and the card says so. The *units*
    do not: a beta is a response rate for the whole archetype, and handing all of it to
    one micro-market would price a scope of 103 units at 254 units of price response.
    `scale` is the scope's share of the archetype's demand, and it is what keeps a narrow
    selection's plays inside its own headroom.
    """
    M = _m()
    plays: list[dict] = []
    deliveries = float(r["deliveries_yr"])
    bb = survey.get("buying_behaviour") or {}
    peak = bb.get("season_peak_month") or "the buying peak"

    # ---- 5. price and promotion, from this archetype's own betas ---------------------
    betas = M.q("""SELECT regressor, beta, se, significant, sign_ok FROM ucm_arch_betas
                   WHERE archetype_id = ? AND regressor IN ('price_drop_pct', 'is_promotion')""",
                [aid])
    for bt in betas:
        if not bt["significant"] or not bt["sign_ok"]:
            continue
        # A window, not the whole year: a 5% price action held for a quarter, or a
        # month-long promotion. Pricing either at 365 days would be a fantasy.
        move, days, label = ((5.0, 90, "Run a 5% price action for a quarter")
                             if bt["regressor"] == "price_drop_pct"
                             else (1.0, 30, "Run a month-long promotion"))
        units_yr = float(bt["beta"]) * move * days * scale
        if units_yr <= 0:
            continue
        plays.append({
            "play": label, "owns": "price", "use_case": "incentives",
            "detail": f"this archetype's own estimated beta ({bt['beta']:.2f} units/day per "
                      f"unit of driver) over {days} days, fitted on simulated daily history",
            "units": round(units_yr), "tiv_reached": None, "confidence": "estimated",
            "mode": "grow",
            "execution": {
                "objective": f"Run it only inside the buying window and take about "
                             f"{M.fmt_units(units_yr)} extra machines — without the discount "
                             f"leaking into the rest of the year.",
                "why": [
                    f"This archetype's own sales history says the effect is real and points "
                    f"the right way. Most archetypes fail that test and get no price play at "
                    f"all.",
                    f"{bb.get('subsidy_led_pct', 0)}% of buying here is scheme-led, so a "
                    f"price action stacked on an existing subsidy window compounds.",
                ],
                "how": [
                    {"step": 1, "what": "Set the dates",
                     "detail": f"{days} days, ending at {peak}. A discount outside the buying "
                               f"season just pays for sales you were getting anyway.",
                     "when": f"{days} days to {peak}"},
                    {"step": 2, "what": "Limit where it applies",
                     "detail": "Only these districts and these products. An open-ended offer "
                               "gets paid on the whole country's sales.",
                     "when": "before launch"},
                    {"step": 3, "what": "Run it alongside a finance offer",
                     "detail": "A discount and a lower down-payment attract different buyers. "
                               "Offer both and let the counter pick.",
                     "when": "at launch"},
                    {"step": 4, "what": "Measure against the weeks just before it",
                     "detail": "Not against last year. Compare like with like, week by week.",
                     "when": "weekly through the offer"},
                ],
                "where": _where_rows(
                    grain, lambda x: f"{round(float(x['deliveries_yr'])):,} deliveries a year, "
                                     f"{float(x['sonalika_share']):.1%} share"),
                "cadence": f"one {days}-day offer, reviewed weekly",
                "owner": "Marketing · pricing, with the regional sales head",
                "cost_note": "Beta is fitted per archetype on simulated daily history — "
                             "direction is sound, magnitude approximate — and is shown here "
                             f"at this scope's {scale:.0%} share of the archetype's demand.",
                "kpi": _kpi("Machines sold during the offer",
                            f"{round(deliveries * days / 365):,} at today's run rate",
                            f"{round(deliveries * days / 365 + units_yr):,}", f"{days} days"),
            },
        })

    # ---- 6. subsidy, weighted by this archetype's own SKU basket ---------------------
    sub_rows = [x for x in basket if x.get("subsidy_pct") is not None]
    sub_units = sum(x["units"] for x in sub_rows)
    avg_subsidy = (sum(x["units"] * x["subsidy_pct"] for x in sub_rows) / sub_units) if sub_units else 0.0
    if avg_subsidy >= 35:
        prov = "real" if any(x["subsidy_provenance"] == "real" for x in sub_rows) else "allocated"
        top_state = M.q("""SELECT state, sum(tiv) AS w FROM micromarket_ops
                           WHERE archetype_id = ? GROUP BY 1 ORDER BY w DESC LIMIT 1""", [aid])
        state_name = top_state[0]["state"] if top_state else "its states"
        # Calibrated so the reference point this heuristic always used -- roughly the 40%
        # MP SMAM proxy rate -- still lands at the original 8% uplift; a genuinely richer
        # or thinner scheme now scales proportionally.
        units = round(deliveries * 0.08 * (avg_subsidy / 40.0))
        if units > 0:
            plays.append({
                "play": f"Push the ~{avg_subsidy:.0f}% subsidy across this scope's SKUs",
                "owns": "policy", "use_case": "incentives",
                "detail": f"demand-weighted subsidy rate across the archetype's own basket, "
                          f"strongest in {state_name} "
                          f"({'real rates' if prov == 'real' else 'national SMAM proxy'}); "
                          f"scheme-linked demand scales with how generous the rate actually is",
                "units": units, "tiv_reached": None, "mode": "grow",
                "confidence": "arithmetic" if prov == "real" else "proxy",
                "execution": {
                    "objective": f"Turn the {avg_subsidy:.0f}% government scheme into about "
                                 f"{M.fmt_units(units)} extra machines — by doing the "
                                 f"paperwork for the farmer, not by advertising the rate.",
                    "why": [
                        f"{bb.get('subsidy_led_pct', 0)}% of buying in this scope is "
                        f"scheme-led — {bb.get('evidence') or ''}",
                        f"Rates are {'real, published state rates' if prov == 'real' else 'the national SMAM proxy where the state rate is unpublished'}, "
                        f"strongest in {state_name}.",
                    ],
                    "how": [
                        {"step": 1, "what": "Put a scheme desk at every counter",
                         "detail": "Form, land record, bank details and photo, all collected "
                                   "at the counter. The scheme is not the problem — the "
                                   "paperwork is.",
                         "when": "month 1"},
                        {"step": 2, "what": "Push when the scheme window is open",
                         "detail": "Each state opens and closes applications. A campaign that "
                                   "runs after the window shuts sells nothing.",
                         "when": "when the window opens"},
                        {"step": 3, "what": "Cover the wait for the money",
                         "detail": "Arrange short-term finance for the subsidy amount so the "
                                   "machine can be delivered before the government pays.",
                         "when": "month 1–2"},
                        {"step": 4, "what": "Keep the farmer updated on their claim",
                         "detail": "Silence after the sale is the main reason a subsidised "
                                   "customer never sends us a second one.",
                         "when": "ongoing"},
                    ],
                    "where": _where_rows(
                        grain, lambda x: f"{str(x['state'])} — {round(float(x['potential_units_yr'])):,} "
                                         f"units of demand at ~{avg_subsidy:.0f}% support"),
                    "cadence": "desk staffed all year; campaigns timed to each state's window",
                    "owner": "Dealer principal · with the institutional sales desk",
                    "cost_note": None if prov == "real" else
                                 "Where a state rate is unpublished this uses the national "
                                 "SMAM rate as a proxy.",
                    "kpi": _kpi("Sales claimed under a scheme",
                                f"{bb.get('subsidy_led_pct', 0)}%",
                                f"{min(bb.get('subsidy_led_pct', 0) + 15, 85)}%", "12 months"),
                },
            })
    return plays


def build_edge_plays(r: pd.Series, grain: pd.DataFrame, survey: dict, rivals: list[dict],
                     at_risk: float, demand: float) -> tuple[list[dict], bool]:
    """The two non-growth modes: stop (no product fit) and protect (defend).

    Returns the plays and whether they REPLACE the growth set -- a "No product fit"
    archetype has no selling play at all, and printing one next to a product verdict is
    how a tool talks a client into spending money that cannot work.
    """
    M = _m()
    fit = float(r["product_fit"])
    unmet = survey.get("unmet_needs") or []

    if r["bucket"] == "No product fit":
        return ([{
            "play": "Fix the product before spending on selling",
            "owns": "product", "use_case": "product",
            "detail": f"product fit is {fit:.0%}, below the floor. At peer share this scope "
                      f"would be worth {M.fmt_units(demand * 0.10)} units a year — that is the "
                      f"prize for an adapted {r['hp_belt']} product, not for more calls",
            "units": 0, "tiv_reached": None, "confidence": "arithmetic", "mode": "stop",
            "execution": {
                "objective": f"This is a product decision, not a sales one. At a normal "
                             f"share this scope would be worth {M.fmt_units(demand * 0.10)} "
                             f"machines a year — and none of it is winnable with the "
                             f"{r['hp_belt']} range we sell today.",
                "why": [
                    f"Our machines suit the land here only {fit:.0%} as well as they need "
                    f"to. It is the product that is failing, not the sales team.",
                ] + [f"Unmet need: {u['need']} — {u['evidence']}" for u in unmet[:3]],
                "how": [
                    {"step": 1, "what": "Stop adding sales spend here",
                     "detail": "More activities will not help if the machine does not suit "
                               "the land. You will get the same close rate however many you "
                               "run.",
                     "when": "immediately"},
                    {"step": 2, "what": "Take the list of unmet needs to the product team",
                     "detail": "These come from the actual soil, farm size, crop residue and "
                               "irrigation in these villages — not from a wish list.",
                     "when": "month 1"},
                    {"step": 3, "what": "Trial it in the field before building anything",
                     "detail": "Run the closest existing model here for a season and measure "
                               "what happens, rather than designing off a spreadsheet.",
                     "when": "one season"},
                    {"step": 4, "what": "Come back to this page after the trial",
                     "detail": "Everything else here is blocked by product fit. Move it, and "
                               "the whole plan changes.",
                     "when": "after the season"},
                ],
                "where": _where_rows(
                    grain, lambda x: f"{round(float(x['potential_units_yr'])):,} units of demand "
                                     f"at {float(x['product_fit']):.0%} fit"),
                "cadence": "one product review, then a season-long trial",
                "owner": "Product management · with R&D",
                "cost_note": None,
                "kpi": _kpi("How well the product suits the land", f"{fit:.0%}", "55% or better",
                            "one product cycle"),
            },
        }], True)

    if r["bucket"] == "Defend" and at_risk > 0 and rivals:
        rival = rivals[0]["rival"]
        return ([{
            "play": f"Hold the line against {rival}",
            "owns": "retention", "use_case": "network",
            "detail": f"{M.fmt_units(at_risk)} units sit in contests where a rival is closest "
                      f"and our lead is narrow; service coverage here is "
                      f"{float(r['service_coverage']):.0%}, and service is what defends a "
                      f"stronghold rather than new selling",
            "units": round(at_risk), "tiv_reached": None, "confidence": "estimated",
            "mode": "protect",
            "execution": {
                "objective": f"Hold on to the {M.fmt_units(at_risk)} machines a year that "
                             f"{rival} is closest to taking — by being better to own, not by "
                             f"discounting.",
                "why": [
                    f"Our service reach here scores {float(r['service_coverage']):.0%}. A "
                    f"strong market is lost in the workshop, not in the showroom.",
                    f"The strongest switching trigger here is "
                    f"“{(survey.get('switching_triggers') or [{}])[0].get('trigger', 'n/a')}”.",
                ],
                "how": [
                    {"step": 1, "what": "Promise a repair time, in writing",
                     "detail": "Publish it, staff for it and measure it. A promise nobody "
                               "measures is what makes people switch.",
                     "when": "month 1"},
                    {"step": 2, "what": "Keep the common spares locally",
                     "detail": "A part that takes a week costs you their next machine.",
                     "when": "month 1–2"},
                    {"step": 3, "what": "Run an owners' group in the biggest micro-markets",
                     "detail": "Existing owners are the cheapest defence you have, and the "
                               "most believable voice against a rival's pitch.",
                     "when": "every quarter"},
                    {"step": 4, "what": "Check the at-risk list every month",
                     "detail": "It moves. Pull it again each month rather than working off "
                               "this one snapshot.",
                     "when": "monthly"},
                ],
                "where": _where_rows(
                    grain, lambda x: f"{float(x['sonalika_share']):.1%} share held against "
                                     f"{rival}; {round(float(x['potential_units_yr'])):,} units"),
                "cadence": "review the at-risk list monthly; owners' group each quarter",
                "owner": "Area Sales Manager · with service",
                "cost_note": "Service reach is a modelled score for now — ITL's own service "
                             "records will replace it.",
                "kpi": _kpi("Machines a year at risk to the closest rival", f"{round(at_risk):,}",
                            f"{round(at_risk * 0.6):,}", "12 months"),
            },
        }], False)

    return ([], False)


# ---------------------------------------------------------------- the seven cards

def _sec(title: str, kind: str, bullet: str | None = None, note: str | None = None,
         prov: str | None = None, empty: str | None = None, wide: bool = False, **data) -> dict:
    """One section inside a use-case card.

    `title` is plain English, because the people using this run territories rather than
    read decks. `bullet` keeps the business team's own wording from the Act slide so the
    page can still be traced back to the brief -- the UI shows it on hover.

    `empty` is not optional in spirit: a table with no rows has to say WHY it has no rows.
    "No dealer data for Punjab" and "no gaps here" look identical as a blank table and mean
    opposite things.
    """
    return {"title": title, "bullet": bullet, "kind": kind, "note": note,
            "provenance": prov, "empty": empty, "wide": wide, **data}


def _cols(*specs) -> list[dict]:
    """(key, label) or (key, label, align) tuples -> column definitions."""
    return [{"key": s[0], "label": s[1], "align": s[2] if len(s) > 2 else "left"}
            for s in specs]


def card_network(ctx: dict) -> dict:
    """1 · Network Expansion & Development.

    The only card whose first two sections are real published data end to end. That makes
    the missing-data case matter more here than anywhere else: the implements dealer file
    has no Punjab rows at all, so a Punjab scope must say "we cannot see the network here",
    never "we have no dealers here". Those are opposite claims.
    """
    M, grain, r = _m(), ctx["grain"], ctx["row"]
    dists = set(grain["district_id"].unique())
    net = [d for d in M.network(ctx["line"])["districts"] if d["district_id"] in dists]
    net.sort(key=lambda d: -(d.get("demand_units") or 0))
    own = sum(d["own_dealers"] for d in net)
    comp = sum(d["competitor_dealers"] for d in net)
    no_data = [d for d in net if d["status"] == "no_data"]
    blind = len(no_data) == len(net) and net          # we cannot see this scope at all

    unreached = grain[grain["dealer_accessibility"] < M._REACH]
    reach_play = next((p for p in ctx["plays"] if p["owns"] == "reach"), None)
    tps = (reach_play or {}).get("touchpoints") or []
    top_oems = M.q("""SELECT oem, sum(dealers) AS dealers FROM dealer_by_oem
                      WHERE product_line = ? AND lower(oem) NOT LIKE '%sonalika%'
                      GROUP BY 1 ORDER BY dealers DESC LIMIT 5""", [ctx["line"]])
    states = ", ".join(sorted({d["state"] for d in no_data})) or "this state"

    if blind:
        summary = (f"We have no dealer list for {states} on the {ctx['line']} side, so we "
                   f"cannot say who is on the ground here. Everything else on this page "
                   f"still works — only the dealer counts are blank.")
    else:
        summary = (f"We have {own} dealer{'' if own == 1 else 's'} here against "
                   f"{comp} competitor shops, across {len(net)} district"
                   f"{'' if len(net) == 1 else 's'}. "
                   + (f"{len(unreached)} of our {len(grain)} micro-markets are too far from "
                      f"any dealer to sell to properly."
                      if len(unreached) else
                      "Every micro-market here already has a dealer close enough to sell from."))

    return {
        "key": "network", "n": 1, "title": "Network Expansion & Development",
        "summary": summary,
        "sections": [
            _sec("Our dealers vs. the competition", "table", wide=True, prov="real",
                 bullet="Visualise own vs. competition sales and service network",
                 note="Dealer counts come from the real OEM dealer files. Nationally the "
                      "biggest rival networks are "
                      + ", ".join(f"{o['oem']} ({o['dealers']})" for o in top_oems) + ".",
                 empty=f"We have no dealer file for {states} on the {ctx['line']} side. "
                       f"This is missing data, not an empty market.",
                 columns=_cols(("district", "District"), ("state", "State"),
                               ("own_dealers", "Our dealers", "right"),
                               ("competitor_dealers", "Competitor shops", "right"),
                               ("n_oems", "Brands present", "right"),
                               ("demand_units", "Demand units/yr", "right"),
                               ("status", "Status")),
                 rows=[] if blind else
                      [{k: d.get(k) for k in ("district", "state", "own_dealers",
                                              "competitor_dealers", "n_oems",
                                              "demand_units", "status")} for d in net[:20]]),
            _sec("Where we have no dealer close by", "table", wide=True, prov="modelled",
                 bullet="Identify coverage gaps and network white spaces",
                 note=(f"{len(no_data)} district(s) here are missing from the dealer file "
                       f"altogether, so they show as 'no data' rather than as a gap — we "
                       f"cannot see a gap we have no records for. " if no_data else "")
                      + f"A micro-market counts as reachable at {M._REACH:.0%} accessibility.",
                 empty=(f"We cannot check this without the dealer list for {states}."
                        if blind else
                        "No gaps here — every micro-market in this scope already has a dealer "
                        "close enough."),
                 columns=_cols(("micro_market", "Micro-market"), ("district", "District"),
                               ("tiv", "Tractors", "right"), ("units", "Demand units/yr", "right"),
                               ("km", "Km to dealer", "right")),
                 rows=[{"micro_market": str(x["micro_market_id"]), "district": str(x["district"]),
                        "tiv": round(float(x["tiv"])),
                        "units": round(float(x["potential_units_yr"])),
                        "km": round(float(x["service_distance_km"]), 1)}
                       for _, x in unreached.sort_values("potential_units_yr",
                                                         ascending=False).head(15).iterrows()]),
            _sec("Where to open new dealers, and how many", "table", wide=True, prov="modelled",
                 bullet="Recommend locations and number of new touchpoints with rationale",
                 note=f"We assume one new dealer brings about "
                      f"{MM_PER_TOUCHPOINT:.0f} nearby micro-markets within reach. That is an "
                      f"estimate, not a site survey.",
                 empty=(f"We cannot recommend locations without the dealer list for {states}."
                        if blind else
                        "Nothing to open here — no micro-market in this scope would cross into "
                        "reach from a new dealer at the expansion you have set."),
                 columns=_cols(("district", "District"), ("state", "State"),
                               ("micro_markets", "Micro-markets out of reach", "right"),
                               ("touchpoints", "New dealers to open", "right"),
                               ("tiv_reached", "Tractors it reaches", "right"),
                               ("units", "Units/yr", "right"), ("rationale", "Why here")),
                 rows=tps),
        ],
        "provenance": "real",
    }


STAGES = [
    ("Awareness", "they hear about the machine"),
    ("Enquiry", "they ask the price and the spec"),
    ("Demo", "they watch it work on a field like theirs"),
    ("Close", "they sign, arrange the loan and take delivery"),
    ("After the sale", "service, spares, and the next referral"),
]


def card_customer(ctx: dict) -> dict:
    """2 · Customer growth. Carries no addend: it aims the volume the other cards create,
    and adding a units figure here would double-count them."""
    M, survey, r = _m(), ctx["survey"], ctx["row"]
    segs = ctx["segments"]
    cm = survey.get("channel_mix") or {}
    bb = survey.get("buying_behaviour") or {}
    drivers = survey.get("purchase_drivers") or []
    complaint = (survey.get("perception") or {}).get("top_complaint") or ""
    rival = (ctx["rivals"][0]["rival"] if ctx["rivals"] else "the local unbranded segment")

    hooks = []
    for s in segs:
        seg = s["segment"]
        if seg == "Build access":
            hook, chan = ("They are too far from a dealer. Open a counter first — a message "
                          "will not fix distance.", "new dealer, then a van campaign")
        elif seg == "Convert now":
            hook, chan = (f"They are ready to buy. Show them "
                          f"{(drivers[0]['driver'] if drivers else 'the running cost').lower()} "
                          f"on their own field.", "field demo, then a call from the counter")
        elif seg == "Defend":
            hook, chan = ("They already buy from us. Keep them by fixing service, spares and "
                          "resale — not by discounting.", "service camp and an owners' meet")
        else:
            hook, chan = ("Low potential. Stay visible cheaply and spend the money elsewhere.",
                          f"{cm.get('digital_pct', 0)}% digital only")
        hooks.append({"title": f"{seg} — {round(s['villages']):,} villages, "
                               f"{round(s['units'] or 0):,} units a year",
                      "detail": f"{hook} Best channel: {chan}. The people they listen to: "
                                f"{bb.get('influencer') or 'the dealer counter'}.",
                      "tag": f"{round(float(s.get('dealer_km') or 0), 1)} km to a dealer"})

    stage_rows = []
    for name, what in STAGES:
        if name == "Awareness":
            msg = f"{drivers[0]['driver'] if drivers else 'Lower cost per acre'}"
            proof = "cost per acre, worked out on the crop they actually grow"
            counter = f"{rival} sells on being available, not on running cost"
        elif name == "Enquiry":
            msg = f"Answer “{complaint.lower()}” before they ask it"
            proof = "a spec sheet against the two brands they will also ask about"
            counter = f"a one-page answer sheet for {rival}"
        elif name == "Demo":
            msg = "Same soil, same crop, same size of farm — theirs or a neighbour's"
            proof = "measured: acres an hour, fuel an acre, depth held"
            counter = "run it side by side with the machine they were considering"
        elif name == "Close":
            msg = (f"Talk monthly instalment and scheme, not sticker price — "
                   f"{bb.get('finance_led_pct', 0)}% here buy on credit")
            proof = "loan approved in principle, and the scheme form filled at the counter"
            counter = f"match what they actually pay in total, not {rival}'s headline price"
        else:
            msg = "A service response time and a spares list, in writing"
            proof = "first free service booked on the day of delivery"
            counter = "a happy owner sells more than any leaflet"
        stage_rows.append({"stage": name, "what": what, "message": msg,
                           "proof": proof, "counter": counter})

    return {
        "key": "customer", "n": 2, "title": "Customer growth",
        "summary": f"{bb.get('own_vs_rent_pct', 0)}% here buy a machine of their own rather "
                   f"than hire one, {bb.get('finance_led_pct', 0)}% buy on credit, and the "
                   f"people they listen to are {bb.get('influencer') or 'the dealer counter'}.",
        "sections": [
            _sec("Which customers to go after first", "table", prov="modelled",
                 bullet="Prioritise customer segments by micro-market archetype",
                 note="Every village falls into one of four boxes, from two questions: is "
                      "there demand here we are not serving, and is a dealer close enough to "
                      "capture it?",
                 empty="No village data for this scope.",
                 columns=_cols(("segment", "Group"), ("villages", "Villages", "right"),
                               ("units", "Units/yr", "right"),
                               ("dealer_km", "Km to dealer", "right")),
                 rows=[{"segment": s["segment"], "villages": round(s["villages"]),
                        "units": round(s["units"] or 0),
                        "dealer_km": round(float(s.get("dealer_km") or 0), 1)} for s in segs]),
            _sec("How to reach each group", "list", prov=SURVEY_PROV,
                 bullet="Define segment-wise engagement hooks, channels and influencers",
                 note=f"Of everyone we can reach here, {cm.get('btl_pct', 0)}% are reached "
                      f"on the ground, {cm.get('digital_pct', 0)}% on a phone and "
                      f"{cm.get('dealer_pct', 0)}% at the dealer counter. {cm.get('evidence') or ''}",
                 empty="No segments in this scope.", items=hooks),
            _sec("What to say at each step of the sale", "table", wide=True, prov=SURVEY_PROV,
                 bullet="Recommend stage-wise messages, proof points and competition counters",
                 note=f"The closest competitor in this scope is {rival}.",
                 columns=_cols(("stage", "Step"), ("message", "What to say"),
                               ("proof", "What to show"), ("counter", "How to beat the rival")),
                 rows=stage_rows),
        ],
        "provenance": SURVEY_PROV,
    }


SHORT_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _months_phrase(season: str | None) -> str | None:
    """"4,5,6,10,11" -> "Apr-Jun and Oct-Nov".

    `sku_ref.season` is a comma-separated list of month numbers. Printed raw it reads as a
    string of digits in the middle of a sentence a salesperson is meant to say out loud.
    """
    if not season:
        return None
    try:
        ms = sorted({int(x) for x in str(season).split(",") if x.strip()})
    except ValueError:
        return None
    if not ms:
        return None
    runs, run = [], [ms[0]]
    for m in ms[1:]:
        if m == run[-1] + 1:
            run.append(m)
        else:
            runs.append(run); run = [m]
    runs.append(run)
    parts = [SHORT_MONTHS[r[0]] if len(r) == 1
             else f"{SHORT_MONTHS[r[0]]}–{SHORT_MONTHS[r[-1]]}" for r in runs]
    return parts[0] if len(parts) == 1 else " and ".join([", ".join(parts[:-1]), parts[-1]])


def card_product(ctx: dict) -> dict:
    """3 · Product development. The SKU basket is the archetype's own TIV-weighted mix, so
    "sells more here than elsewhere" is measured against the national basket, not guessed."""
    M, survey, r = _m(), ctx["survey"], ctx["row"]
    basket, agro = ctx["basket"], ctx["agro"]
    ref = {x["sku_id"]: x for x in M.q(
        "SELECT sku_id, price_inr, season, life_years, rental_substitutable FROM sku_ref")}

    def position(x):
        idx = x.get("index_vs_national")
        if idx and idx >= 1.3:
            return "Sells much more here than in the rest of the country"
        if idx and idx <= 0.7:
            return "Sells less here — either they do not need it, or we are not selling it"
        return "Sells about as much here as anywhere"

    crop = agro.get("crop") or "the local crop"
    ha = float(agro.get("holding_ha") or 0)
    prop_rows = []
    for x in basket[:10]:
        rf = ref.get(x["sku_id"], {})
        # A 0-1 score, not a flag: above ~0.4 the machine is commonly hired rather than
        # owned, which changes the pitch from "it pays back on your land" to "it pays back
        # on other people's".
        rent = float(rf.get("rental_substitutable") or 0)
        months = _months_phrase(rf.get("season"))
        pitch = ("It earns its money back by hiring it out as well as using it"
                 if rent >= 0.40 else
                 "It earns its money back on their own land — hiring it out is rare here")
        prop_rows.append({
            "sku": x["name"], "units": round(x["units"]), "price": rf.get("price_inr"),
            "position": position(x),
            "value_prop": (f"{pitch}, on {crop} at {ha:.1f} ha average farm size"
                           + (f". Used {months}." if months else ".")),
        })

    gaps = []
    fit = float(r["product_fit"])
    if fit < 0.7:
        gaps.append({"gap": f"Our {r['hp_belt']} range does not suit the land here",
                     "evidence": f"product fit {fit:.0%} on {agro.get('soil') or 'local'} soil",
                     "units": round(float(r["potential_units_yr"]) * (1 - fit) * 0.10)})
    attach_gap = float(agro.get("peer_attach") or 0) - float(agro.get("attach_rate") or 0)
    if attach_gap > 0:
        gaps.append({"gap": "Farmers in similar places own more machines than they do here",
                     "evidence": f"{float(agro.get('attach_rate') or 0):.2f} machines per "
                                 f"tractor here against {float(agro.get('peer_attach') or 0):.2f} "
                                 f"in comparable micro-markets",
                     "units": round(float(r["potential_units_yr"]) * attach_gap)})
    for x in basket[:12]:
        idx = x.get("index_vs_national")
        if idx is not None and idx < 0.75 and x["units"] > 0:
            gaps.append({"gap": f"We under-sell {x['name']} here",
                         "evidence": f"it sells at {idx:.2f} times the national rate even "
                                     f"though local demand is {round(x['units']):,} units",
                         "units": round(x["units"] * (1 - idx))})
    gaps = sorted(gaps, key=lambda g: -g["units"])[:8]

    contested = max(ctx["rivals"], key=lambda v: v.get("winnable") or 0, default=None)
    req_rows = [{
        "requirement": u["need"], "severity": u["severity"], "evidence": u["evidence"],
        "rival": (contested or {}).get("rival"),
        "units": round(float(r["potential_units_yr"]) * u["severity"] / 100 * 0.10),
    } for u in survey.get("unmet_needs") or []]

    return {
        "key": "product", "n": 3, "title": "Product development",
        "summary": f"Our machines score {fit:.0%} on how well they suit "
                   f"{agro.get('soil') or 'the local'} soil under "
                   f"{agro.get('crop') or 'the local crop'}. "
                   f"{len(survey.get('unmet_needs') or [])} things farmers here need are not "
                   f"in the range today.",
        "sections": [
            _sec("What to sell, and how to pitch it", "table", wide=True, prov="modelled",
                 bullet="Define product-wise value propositions and positioning",
                 note="Position is worked out by comparing this scope's share of a product "
                      "against the national share.",
                 empty="No product basket for this scope.",
                 columns=_cols(("sku", "Product"), ("units", "Demand units/yr", "right"),
                               ("price", "Price ₹", "right"), ("position", "How it sells here"),
                               ("value_prop", "What to tell the farmer")),
                 rows=prop_rows),
            _sec("Where our product falls short", "table", prov="modelled",
                 bullet="Identify product, feature and pricing gaps by segment",
                 empty="No product gaps stand out in this scope.",
                 columns=_cols(("gap", "The gap"), ("evidence", "How we know"),
                               ("units", "Units/yr it costs us", "right")),
                 rows=gaps),
            _sec("What to build next", "table", prov=SURVEY_PROV,
                 bullet="Prioritise product and feature requirements vs. competition",
                 note="Ranked by how strongly the land, farm size, crop residue and irrigation "
                      "in these villages argue for it.",
                 empty="Nothing to change in the product for this scope.",
                 columns=_cols(("requirement", "What farmers need"),
                               ("severity", "How badly", "right"),
                               ("rival", "Who we would beat"), ("units", "Units/yr", "right"),
                               ("evidence", "How we know")),
                 rows=req_rows),
        ],
        "plays": [p for p in ctx["plays"] if p["use_case"] == "product"], "provenance": "modelled",
    }


def card_inventory(ctx: dict) -> dict:
    """4 · Inventory. Also carries no addend -- stock norms and demo placement move volume
    the other cards already priced, they do not create their own."""
    M, r, grain = _m(), ctx["row"], ctx["grain"]
    basket, survey = ctx["basket"], ctx["survey"]
    cover = float(ctx["assumptions"].months_of_cover)
    top = basket[:10]
    ids = [x["sku_id"] for x in top]
    seas: dict = {}
    if ids:
        ph = ", ".join("?" * len(ids))
        for row in M.q(f"SELECT sku_id, month_of_year, season_index FROM seasonality "
                       f"WHERE sku_id IN ({ph})", ids):
            seas.setdefault(row["sku_id"], {})[int(row["month_of_year"])] = float(row["season_index"])

    norm_rows = []
    for x in top:
        s = seas.get(x["sku_id"]) or {}
        peak_m = max(s, key=s.get) if s else None
        peak_units = (x["units"] / 12.0) * (s.get(peak_m, 1.0) if peak_m else 1.0)
        norm_rows.append({
            "sku": x["name"], "units": round(x["units"]),
            "peak_month": MONTHS[peak_m] if peak_m else "—",
            "peak_units": round(peak_units), "hold": round(peak_units * cover),
        })

    total_hold = sum(n["hold"] for n in norm_rows)
    alloc = [{
        "micro_market": str(x["micro_market_id"]), "district": str(x["district"]),
        "share_pct": round(float(x["potential_units_yr"]) / max(float(r["potential_units_yr"]), 1) * 100, 1),
        "units": round(float(x["potential_units_yr"])),
        "hold": round(total_hold * float(x["potential_units_yr"]) / max(float(r["potential_units_yr"]), 1)),
    } for _, x in grain.sort_values("potential_units_yr", ascending=False).head(12).iterrows()]

    sections = [
        _sec("How much stock to keep, by model", "table", prov="modelled",
             bullet="Set model-wise inventory norms by micro-market archetype",
             note=f"Peak-month demand is the yearly figure spread over the season we estimate "
                  f"for each product. The norm holds {cover:g} month(s) of that peak — a "
                  f"number you set, not a measured stocking policy.",
             empty="No product basket for this scope.",
             columns=_cols(("sku", "Model"), ("units", "Demand units/yr", "right"),
                           ("peak_month", "Busiest month"),
                           ("peak_units", "Sold in that month", "right"),
                           ("hold", "Units to keep in stock", "right")),
             rows=norm_rows),
        _sec("Where to keep that stock", "table", prov="modelled",
             bullet="Allocate that stock across the scope's micro-markets",
             empty="No micro-markets in this scope.",
             columns=_cols(("micro_market", "Micro-market"), ("district", "District"),
                           ("units", "Demand units/yr", "right"),
                           ("share_pct", "% of this scope", "right"),
                           ("hold", "Units to keep here", "right")),
             rows=alloc),
    ]

    if ctx["line"] == "tractors":
        f = ctx["factors"]
        demo_gap = 100 - f.get("F10_demo_activity", 50)
        g2 = grain.copy()
        g2["demo_score"] = g2["tiv"] * (demo_gap / 100.0)
        g2 = g2.sort_values("demo_score", ascending=False).head(12)
        total_score = float(g2["demo_score"].sum()) or 1.0
        fleet = int(ctx["assumptions"].demo_units)
        demo_rows, left = [], fleet
        for _, x in g2.iterrows():
            n = min(max(1, round(fleet * float(x["demo_score"]) / total_score)), left) if left > 0 else 0
            left -= n
            demo_rows.append({
                "micro_market": str(x["micro_market_id"]), "district": str(x["district"]),
                "tiv": round(float(x["tiv"])), "units": round(float(x["potential_units_yr"])),
                "demo_units": n, "weeks": max(2, round(52 * float(x["demo_score"]) / total_score)),
                "why": f"{round(float(x['tiv'])):,} tractors here, and we demo less than most "
                       f"of the country does",
            })
        sections.append(
            _sec("Where to send demo vehicles", "table", wide=True, prov="ITL pending",
                 bullet="Determine demo vehicle requirement and deployment location",
                 note=f"We have no list of Sonalika's actual demo vehicles. This says where a "
                      f"fleet of {fleet} would do the most good — the places with the most "
                      f"tractors and the least demo activity — not what the real fleet is "
                      f"doing today.",
                 empty="No micro-markets in this scope.",
                 columns=_cols(("micro_market", "Micro-market"), ("district", "District"),
                               ("tiv", "Tractors", "right"), ("units", "Demand units/yr", "right"),
                               ("demo_units", "Demo vehicles", "right"),
                               ("weeks", "Weeks a year", "right"), ("why", "Why here")),
                 rows=demo_rows))

    return {
        "key": "inventory", "n": 4, "title": "Inventory",
        "summary": f"Keep about {total_hold:,} units in stock across {len(norm_rows)} models, "
                   f"split between micro-markets by how much each one sells.",
        "sections": sections, "provenance": "modelled",
    }


# What answers each complaint, on the ground. This is the client's own worked example --
# "the customer is saying mileage, so your activities should focus on mileage" -- written
# out for every complaint the modelled survey can surface.
THEME_FOR_BARRIER = {
    "awareness": [
        ("Show them what it costs to run, on their own field",
         "A field demo with a meter on the fuel tank",
         "Nobody believes a fuel claim until they see the measuring jar themselves."),
        ("Let a nearby owner do the talking",
         "An owners' meet at an existing customer's farm",
         "The owner in the next village sells better than any leaflet."),
    ],
    "service": [
        ("Promise a repair time and stick to it",
         "A service camp with a published turnaround and a spares counter",
         "Worries about service are answered by a van in the village, not by a brochure."),
        ("Have the common spares in stock",
         "A fast-moving spares display at every activity",
         "A part that takes a week costs you the next sale, not this one."),
    ],
    "finance": [
        ("Talk about the monthly payment, not the price",
         "A loan camp with two or more financiers at the same table",
         "It is approval that fails here, not price."),
        ("Fill the subsidy form at the counter",
         "A subsidy desk timed to when the scheme window opens",
         "The scheme is not the problem — the paperwork is."),
    ],
    "product": [
        ("Show the version that suits this soil and this farm size",
         "A side-by-side trial against the machine they were considering",
         "Where the product is the problem, an activity can only prove it — not fix it."),
        ("Show what it does across the whole crop cycle",
         "A demo across the season, not one job",
         "Being useful for the second crop is what closes the sale."),
    ],
}

# The base split between activity types. There is NO data behind these percentages: the
# marts carry a single `activities_yr` count with no breakdown by type, and ITL has not
# supplied one. This is a stated rule of thumb, badged `judgement` on screen so nobody
# mistakes it for a measurement, and it is the first thing to replace when the real
# activity mix arrives.
FORMAT_MIX = [
    ("Field demonstration", 0.35, "on the farmer's own crop and soil"),
    ("Village meet / chaupal", 0.25, "held around an existing owner"),
    ("Service & spares camp", 0.15, "answers the worry about after-sales before the sale"),
    ("Finance / subsidy desk", 0.15, "turns an interested farmer into an approved loan"),
    ("Van campaign / mandi day", 0.10, "reach, where phones do not go"),
]

# Which format answers each barrier. The base split above is flat everywhere; this tilts it
# towards whatever the scope's own customers complain about most, so the mix at least moves
# with the data even though its starting point does not come from any.
BARRIER_FORMAT = {"awareness": "Field demonstration", "service": "Service & spares camp",
                  "finance": "Finance / subsidy desk", "product": "Field demonstration"}
FORMAT_TILT = 0.10


def _format_mix(barrier: str) -> list[tuple[str, float, str]]:
    """The base split, tilted towards the format that answers this scope's top complaint."""
    lead = BARRIER_FORMAT.get(barrier)
    if not lead:
        return FORMAT_MIX
    others = sum(sh for f, sh, _ in FORMAT_MIX if f != lead)
    if others <= 0:
        return FORMAT_MIX
    return [(f, sh + FORMAT_TILT if f == lead else sh * (1 - FORMAT_TILT / others), w)
            for f, sh, w in FORMAT_MIX]


def card_activity(ctx: dict) -> dict:
    """5 · Activity Plan. Owns the effort play -- the only card whose units come from doing
    more of what we already do, at the rates we already achieve."""
    M, r, survey = _m(), ctx["row"], ctx["survey"]
    cm = survey.get("channel_mix") or {}
    bb = survey.get("buying_behaviour") or {}
    barrier = survey.get("top_barrier", "finance")
    complaint = (survey.get("perception") or {}).get("top_complaint") or ""
    drivers = survey.get("purchase_drivers") or []
    act_today = float(r["activities_yr"])
    act_target = ctx["targets"]["activities"]["target"]

    themes = [{"title": t, "detail": f"{fmt}. {why}", "tag": "start here"}
              for t, fmt, why in THEME_FOR_BARRIER.get(barrier, [])]
    for dr in drivers[1:4]:
        themes.append({"title": dr["driver"], "detail": dr["implication"],
                       "tag": f"{dr['score']:.0f}/100"})

    mix = _format_mix(barrier)
    mix_rows = [{"format": f, "share_pct": round(s * 100), "why": w,
                 "per_month": round(act_target * s / 12)} for f, s, w in mix]

    # Monthly beat: the scope's own product seasonality decides where the effort goes, so the
    # plan peaks when the buying does rather than spreading flat across the year.
    ids = [x["sku_id"] for x in ctx["basket"][:8]]
    wt = {x["sku_id"]: x["units"] for x in ctx["basket"][:8]}
    name = {x["sku_id"]: x["name"] for x in ctx["basket"][:8]}
    month_idx = {m: 0.0 for m in range(1, 13)}
    month_top = {m: (None, 0.0) for m in range(1, 13)}
    if ids:
        ph = ", ".join("?" * len(ids))
        for row in M.q(f"SELECT sku_id, month_of_year, season_index FROM seasonality "
                       f"WHERE sku_id IN ({ph})", ids):
            m, w = int(row["month_of_year"]), float(row["season_index"]) * wt.get(row["sku_id"], 0)
            month_idx[m] += w
            if w > month_top[m][1]:
                month_top[m] = (name.get(row["sku_id"]), w)
    tot = sum(month_idx.values()) or 12.0
    theme_cycle = [t["title"] for t in themes] or ["Cost per acre"]
    beat = [{
        "month": MONTHS[m], "busy": round(month_idx[m] / (tot / 12), 2) if tot else 1.0,
        "activities": round(act_target * month_idx[m] / tot) if tot else round(act_target / 12),
        "focus_sku": month_top[m][0] or "—",
        "theme": theme_cycle[(m - 1) % len(theme_cycle)],
    } for m in range(1, 13)]

    advocacy = [
        {"title": f"Set up an owners' group in the top {min(6, len(ctx['grain']))} micro-markets",
         "detail": f"Get {bb.get('influencer') or 'existing owners'} on our side. Meet them "
                   f"once a quarter, let them try new machines first, and thank them when "
                   f"they send a customer.",
         "tag": "every quarter"},
        {"title": "Collect real results from each micro-market",
         "detail": f"One measured result per micro-market on "
                   f"{ctx['agro'].get('crop') or 'the local crop'} — acres an hour, fuel an "
                   f"acre — that the field team can show, instead of a national brochure.",
         "tag": "build once, use all year"},
        {"title": "Train the local mechanics and rental operators",
         "detail": "The people who repair machines and hire them out decide what gets bought "
                   "next. Train them and stock them, and they answer the service worry for us.",
         "tag": "twice a year"},
    ]

    return {
        "key": "activity", "n": 5, "title": "Activity Plan",
        "summary": f"We run {round(act_today):,} activities a year here; the plan needs "
                   f"{round(act_target):,}. Lead every one of them with "
                   f"“{complaint.lower()}”, and run {cm.get('btl_pct', 0)}% of them on the "
                   f"ground rather than on a phone.",
        "sections": [
            _sec("What our activities should be about", "list", prov=SURVEY_PROV,
                 bullet="Recommend activity themes and activation formats",
                 note=f"The first two come straight from the loudest thing customers here say: "
                      f"“{complaint}”.",
                 empty="No clear theme for this scope.", items=themes),
            _sec("What kind of activities, and how many", "table", prov=JUDGEMENT,
                 bullet="Define channel and activity mix by micro-market archetype",
                 note=f"The percentages are a rule of thumb, not a measurement — nothing in "
                      f"the data records what kind of activity gets run, only how many. They "
                      f"are tilted here towards {BARRIER_FORMAT.get(barrier, 'field demos')} "
                      f"because that is what answers this scope's top complaint. The "
                      f"per-month counts come from the activity target, which rests on the "
                      f"simulated BD funnel. Reach here splits {cm.get('btl_pct', 0)}% on the "
                      f"ground, {cm.get('digital_pct', 0)}% on a phone, "
                      f"{cm.get('dealer_pct', 0)}% at the counter.",
                 columns=_cols(("format", "Type of activity"),
                               ("share_pct", "% of all activities", "right"),
                               ("per_month", "How many a month", "right"), ("why", "Why")),
                 rows=mix_rows),
            _sec("Building the brand for the long run", "list", prov=JUDGEMENT,
                 bullet="Plan brand-building and advocacy interventions",
                 note="A standard advocacy programme, written from experience. Only the "
                      "people it names — who this scope's farmers actually listen to — come "
                      "from the data.",
                 items=advocacy),
            _sec("Month-by-month plan", "table", wide=True, prov=FUNNEL_PROV,
                 note="The shape of the year is estimated from each product's own "
                      "seasonality; the activity counts rest on the simulated BD funnel.",
                 columns=_cols(("month", "Month"), ("busy", "How busy", "right"),
                               ("activities", "Activities", "right"),
                               ("focus_sku", "Product to push"), ("theme", "Theme")),
                 rows=beat),
        ],
        "plays": [p for p in ctx["plays"] if p["use_case"] == "activity"], "provenance": "modelled",
    }


def card_sales(ctx: dict) -> dict:
    """6 · Sales planning. The targets are the funnel identity inverted -- the same
    arithmetic the Plan stage's target screen uses, run at this scope."""
    M, r, grain = _m(), ctx["row"], ctx["grain"]
    t = ctx["targets"]
    conv, peer_conv = float(r["conversion_rate"]), ctx["peer_conv"]

    target_rows = [
        {"metric": "Machines delivered a year", **t["deliveries"]},
        {"metric": "Enquiries needed", **t["enquiries"]},
        {"metric": "Activities needed", **t["activities"]},
    ]

    fte_now = float(r["activities_yr"]) / 12 / ACTIVITIES_PER_FTE_MONTH
    fte_need = t["activities"]["target"] / 12 / ACTIVITIES_PER_FTE_MONTH
    training = [
        {"title": "Log every enquiry, and log why it was lost",
         "detail": "We cannot fix the drop-off without the reasons. One line per enquiry, the "
                   "day it happens.", "tag": "everyone in the field"},
        {"title": "Fill loan files properly",
         "detail": f"Approval runs at {ctx['approval_now']:.0%} here. Most rejections are bad "
                   f"paperwork, not bad customers.", "tag": "counter staff"},
        {"title": "Answer the competition",
         "detail": f"One rehearsed page per rival. The closest one here is "
                   f"{(ctx['rivals'][0]['rival'] if ctx['rivals'] else 'the local segment')}.",
         "tag": "sales staff"},
        {"title": "Run a proper demo",
         "detail": "Measure it — acres an hour, fuel an acre, depth held — and leave the "
                   "numbers with the farmer. An unmeasured demo is a day out.",
         "tag": "demo operators"},
    ]

    net = {d["district_id"]: d for d in M.network(ctx["line"])["districts"]}
    sc = (grain.groupby(["district_id", "district", "state"])
               .agg(activities=("activities_yr", "sum"), enquiries=("enquiries_yr", "sum"),
                    deliveries=("deliveries_yr", "sum"))
               .reset_index().sort_values("deliveries", ascending=False))
    score_rows = []
    for _, x in sc.head(20).iterrows():
        n = net.get(x["district_id"], {})
        dealers = int(n.get("own_dealers") or 0)
        has_data = n.get("status") != "no_data"
        score_rows.append({
            "district": str(x["district"]), "state": str(x["state"]),
            "dealers": dealers if has_data else None,
            "activities": round(float(x["activities"])),
            "enquiries": round(float(x["enquiries"])),
            "deliveries": round(float(x["deliveries"])),
            "conversion": round(float(x["deliveries"]) / max(float(x["enquiries"]), 1) * 100, 1),
            "per_dealer": round(float(x["deliveries"]) / dealers) if dealers else None,
            "status": ("Covered" if dealers else
                       "No dealer list for this state" if not has_data else "No dealer of ours"),
        })

    return {
        "key": "sales", "n": 6, "title": "Sales planning",
        "summary": f"To deliver {round(t['deliveries']['target']):,} machines we need "
                   f"{round(t['enquiries']['target']):,} enquiries and "
                   f"{round(t['activities']['target']):,} activities — about "
                   f"{math.ceil(fte_need)} people in the field, against "
                   f"{math.ceil(fte_now)} today.",
        "sections": [
            _sec("The targets to hit", "table", prov=FUNNEL_PROV,
                 bullet="Set activity, enquiry and delivery targets from the sales forecast",
                 note="Straight arithmetic, not a second forecast: deliveries divided by our "
                      "close rate gives the enquiries we need, and enquiries divided by our "
                      "enquiry rate gives the activities.",
                 columns=_cols(("metric", "What"), ("today", "Today", "right"),
                               ("target", "Target", "right"), ("delta", "More needed", "right")),
                 rows=target_rows),
            _sec("People needed", "facts", prov=JUDGEMENT,
                 bullet="Recommend manpower requirements and training interventions",
                 note=f"We assume one field person runs {ACTIVITIES_PER_FTE_MONTH:.0f} "
                      f"activities a month. That assumption is the only thing turning an "
                      f"activity target into a headcount.",
                 items=[
                     {"k": "Field people today", "v": f"{math.ceil(fte_now)}",
                      "note": f"running {round(float(r['activities_yr'])):,} activities a year"},
                     {"k": "Field people needed", "v": f"{math.ceil(fte_need)}",
                      "note": f"to run {round(t['activities']['target']):,} a year"},
                     {"k": "Shortfall", "v": f"{max(math.ceil(fte_need) - math.ceil(fte_now), 0)}",
                      "note": "hire, or move people off a low-potential area"},
                     {"k": "Close rate to reach", "v": f"{peer_conv:.1%}",
                      "note": f"we close {conv:.1%} of enquiries here today"},
                 ]),
            _sec("What to train them on", "list", prov=JUDGEMENT,
                 note="A standard training list. The numbers quoted inside it — approval "
                      "rate, the closest rival — are from the data; the list itself is not.",
                 items=training),
            _sec("Dealer scorecard", "table", wide=True, prov="mixed",
                 bullet="Track dealer performance through a scorecard",
                 note="Dealer counts are real. The activity, enquiry and delivery columns are "
                      "modelled until ITL gives us two years of actuals — at which point "
                      "per-dealer becomes a measurement instead of a division.",
                 empty="No districts in this scope.",
                 columns=_cols(("district", "District"), ("dealers", "Our dealers", "right"),
                               ("activities", "Activities", "right"),
                               ("enquiries", "Enquiries", "right"),
                               ("deliveries", "Delivered", "right"),
                               ("conversion", "Close rate %", "right"),
                               ("per_dealer", "Per dealer", "right"), ("status", "Coverage")),
                 rows=score_rows),
        ],
        "plays": [p for p in ctx["plays"] if p["use_case"] == "sales"], "provenance": "mixed",
    }


def card_incentives(ctx: dict) -> dict:
    """7 · Incentives & Consumer Schemes. Owns price and subsidy -- the two levers that are
    bought rather than worked."""
    M, r, survey = _m(), ctx["row"], ctx["survey"]
    t = ctx["targets"]
    bb = survey.get("buying_behaviour") or {}

    def worth(owns):
        return sum(p["units"] for p in ctx["plays"] if p["owns"] == owns and p["mode"] == "grow")

    inc_rows = [
        {"trigger": "Activities actually done", "threshold": f"90% of {round(t['activities']['target']):,} a year",
         "who": "Field staff", "basis": "a fixed amount per activity, paid monthly",
         "funded_by": round(worth("effort"))},
        {"trigger": "Enquiries turned into sales", "threshold": f"{ctx['peer_conv']:.1%} or better",
         "who": "Dealer and counter staff", "basis": "a bonus for every point above that",
         "funded_by": round(worth("execution"))},
        {"trigger": "Loans approved at the counter", "threshold": f"{ctx['approval_new']:.0%} or better",
         "who": "Counter staff", "basis": "per approved loan, capped each month",
         "funded_by": round(worth("approval"))},
        {"trigger": "New dealer opened and stocked", "threshold": "spares in place before the first sale",
         "who": "The new dealer", "basis": "a one-time opening support payment",
         "funded_by": round(worth("reach"))},
    ]

    states = (ctx["grain"].groupby("state")["potential_units_yr"].sum().sort_values(ascending=False))
    sub = {row["state"]: row for row in M.q(
        "SELECT state, avg(subsidy_pct) AS pct, "
        "max(CASE WHEN provenance = 'real' THEN 1 ELSE 0 END) AS real FROM subsidy GROUP BY 1")}
    gap = float(r.get("share_gap") or 0) if pd.notna(r.get("share_gap")) else 0.0
    scheme_rows = []
    for st, units in states.items():
        s = sub.get(st) or {}
        pct = float(s.get("pct") or 0)
        crit = round(_clip(units / max(float(r["potential_units_yr"]), 1) * 100 * 0.5
                           + bb.get("subsidy_led_pct", 0) * 0.3 + max(gap, 0) * 100 * 0.2, 0, 100), 1)
        scheme_rows.append({
            "state": str(st), "units": round(float(units)),
            "subsidy_pct": round(pct, 1) if pct else None,
            "basis": "real published rate" if s.get("real") else
                     ("national rate used as a stand-in" if pct else "no rate on file"),
            "criticality": crit,
            "action": ("Staff a scheme desk and time the push to the application window"
                       if crit >= 50 else "Help at the counter; do not build a campaign"),
        })
    scheme_rows.sort(key=lambda x: -x["criticality"])

    sim_rows = []
    for bt in M.q("""SELECT regressor, beta, significant, sign_ok FROM ucm_arch_betas
                     WHERE archetype_id = ? AND regressor IN
                           ('price_drop_pct', 'is_promotion', 'subsidy_intensity')""",
                  [ctx["archetype_id"]]):
        move, days, label = ((5.0, 90, "A 5% price cut, held for three months")
                             if bt["regressor"] == "price_drop_pct" else
                             (1.0, 30, "A month-long promotion")
                             if bt["regressor"] == "is_promotion" else
                             (1.0, 180, "More subsidy support, for six months"))
        usable = bool(bt["significant"]) and bool(bt["sign_ok"])
        units = float(bt["beta"]) * move * days * ctx["scale"]
        sim_rows.append({
            "lever": label, "window_days": days,
            "units": round(units) if usable and units > 0 else None,
            "value_inr": round(units * float(ctx["price_per_unit"])) if usable and units > 0 else None,
            "note": "Worth doing — the effect is real in this archetype's history" if usable else
                    ("The data points the wrong way here" if not bt["sign_ok"]
                     else "We cannot tell it apart from normal variation here"),
        })

    return {
        "key": "incentives", "n": 7, "title": "Incentives & Consumer Schemes",
        "summary": f"{bb.get('subsidy_led_pct', 0)}% of buying here is driven by a government "
                   f"scheme. Of the price and promotion levers we can test, "
                   f"{sum(1 for s in sim_rows if s['units'])} of {len(sim_rows)} actually work "
                   f"in this archetype.",
        "sections": [
            _sec("Incentives that pay for effort", "table", prov=JUDGEMENT,
                 bullet="Recommend effort-linked dealer & manpower incentives",
                 note="The incentive structure is written from experience; the thresholds in "
                      "it are the targets from the other use cases. "
                      "Each incentive is triggered by something another use case is already "
                      "committed to moving, and is paid out of the units that use case is "
                      "worth — so the payout is capped by the plan, not by appetite.",
                 columns=_cols(("trigger", "Paid for"), ("threshold", "The bar"),
                               ("who", "Who gets it"), ("basis", "How it is paid"),
                               ("funded_by", "Units that fund it", "right")),
                 rows=inc_rows),
            _sec("Which customer schemes matter most", "table", prov="mixed",
                 bullet="Prioritise consumer schemes based on market criticality",
                 note="Rates are the real published state rates where the state publishes "
                      "them, and the national rate as a stand-in where it does not.",
                 empty="No states in this scope.",
                 columns=_cols(("state", "State"), ("units", "Demand units/yr", "right"),
                               ("subsidy_pct", "Subsidy %", "right"), ("basis", "Where the rate is from"),
                               ("criticality", "How much it matters", "right"),
                               ("action", "What to do")),
                 rows=scheme_rows),
            _sec("What a discount or promotion would do", "table", prov="modelled",
                 bullet="Simulate scheme impact",
                 note="Worked out from this archetype's own sales history. Where the history "
                      "cannot tell us anything reliable, we say so rather than putting up a "
                      "number.",
                 empty="This archetype has no fitted price or promotion history to test "
                       "against.",
                 columns=_cols(("lever", "If we do this"), ("window_days", "For (days)", "right"),
                               ("units", "Extra units", "right"),
                               ("value_inr", "Extra value ₹", "right"), ("note", "Verdict")),
                 rows=sim_rows),
        ],
        "plays": [p for p in ctx["plays"] if p["use_case"] == "incentives"], "provenance": "modelled",
    }
CARD_BUILDERS = [card_network, card_customer, card_product, card_inventory,
                 card_activity, card_sales, card_incentives]

# Which card the survey's top barrier promotes to the front. Ordering is presentational --
# it changes what you read first, never what anything is worth.
BARRIER_LEADS = {"service": "network", "finance": "incentives",
                 "awareness": "activity", "product": "product"}


# ---------------------------------------------------------------- targets, tracking, list

def build_targets(r: pd.Series, capped: float) -> dict:
    """Invert the BD funnel for the volume the plays are worth.

    Same arithmetic the Plan stage's target screen uses: deliveries = share x demand,
    enquiries = deliveries / conversion, activities = enquiries / enquiry rate. Inverting
    an identity is arithmetic -- it is not a second forecast stacked on the first.
    """
    deliveries = float(r["deliveries_yr"])
    enquiries = float(r["enquiries_yr"])
    activities = float(r["activities_yr"])
    conv = float(r["conversion_rate"]) or (deliveries / enquiries if enquiries else 0.0)
    enq_rate = enquiries / activities if activities else 0.0

    target_del = deliveries + max(capped, 0.0)
    need_enq = target_del / conv if conv else enquiries
    need_act = need_enq / enq_rate if enq_rate else activities
    return {
        "deliveries": {"today": round(deliveries), "target": round(target_del),
                       "delta": round(target_del - deliveries),
                       "rate": "share × demand"},
        "enquiries": {"today": round(enquiries), "target": round(need_enq),
                      "delta": round(need_enq - enquiries),
                      "rate": f"at today's {conv:.1%} conversion"},
        "activities": {"today": round(activities), "target": round(need_act),
                       "delta": round(need_act - activities),
                       "rate": f"at today's {enq_rate:.1%} enquiry rate"},
    }


def build_tracking(cards: list[dict], ctx: dict) -> list[dict]:
    """Delta impact, per card, on the same numbers the plays were priced on.

    The target column is the play arithmetic, not a second guess -- which is what makes
    "did the playbook work" answerable rather than arguable. `actual` stays null until ITL
    supplies the two years of activity, enquiry and delivery history the ACT brief names.
    """
    M, r, t = _m(), ctx["row"], ctx["targets"]
    survey = ctx["survey"]
    grain = ctx["grain"]
    units_by = {}
    for c in cards:
        units_by[c["key"]] = sum(p["units"] for p in (c.get("plays") or [])
                                 if p.get("mode") == "grow")
    covered = int((grain["dealer_accessibility"] >= M._REACH).sum())
    reach_play = next((p for p in ctx["plays"] if p["owns"] == "reach"), None)
    crossing = int((reach_play or {}).get("tiv_reached", 0) and
                   sum(tp["micro_markets"] for tp in (reach_play or {}).get("touchpoints") or []))

    rows = [
        ("network", "Micro-markets with a dealer close enough", covered,
         covered + crossing, "quarterly"),
        ("customer", "Villages worth working", 
         round(sum(s["villages"] for s in ctx["segments"]
                   if s["segment"] in ("Convert now", "Defend"))),
         round(sum(s["villages"] for s in ctx["segments"]
                   if s["segment"] in ("Convert now", "Defend", "Build access"))), "half-yearly"),
        ("product", "How well the product suits the land (%)", round(float(r["product_fit"]) * 100, 1),
         max(round(float(r["product_fit"]) * 100, 1), 55.0), "per product cycle"),
        # Baseline is None, not zero: we do not know what stock is held today, and printing
        # 0 would assert an empty yard we have no data for.
        ("inventory", "Months of stock held at the peak",
         None, float(ctx["assumptions"].months_of_cover), "monthly"),
        ("activity", "Activities a year", t["activities"]["today"],
         t["activities"]["target"], "monthly"),
        # Conversion is the right metric only where there is a gap to the belt median. Where
        # the scope already converts at or above it, the sales card's live play is finance,
        # and reporting a conversion target equal to today's would read as "nothing to do"
        # next to a units figure that says otherwise.
        (("sales", "Enquiries we close (%)",
          round(float(r["conversion_rate"]) * 100, 1), round(ctx["peer_conv"] * 100, 1),
          "weekly")
         if ctx["peer_conv"] > float(r["conversion_rate"]) else
         ("sales", "Loans approved (%)", round(ctx["approval_now"] * 100, 1),
          round(ctx["approval_new"] * 100, 1), "weekly")),
        ("incentives", "Sales claimed under a scheme (%)",
         (survey.get("buying_behaviour") or {}).get("subsidy_led_pct", 0),
         min((survey.get("buying_behaviour") or {}).get("subsidy_led_pct", 0) + 15, 85),
         "per scheme window"),
    ]
    title = {c["key"]: c["title"] for c in cards}
    return [{
        "use_case": title.get(k, k), "key": k, "metric": m,
        "baseline_now": b, "target": tg,
        "delta": round(float(tg) - float(b), 2) if b is not None and tg is not None else None,
        "units_at_stake": round(units_by.get(k, 0)),
        "review_cadence": cad,
        "actual": None,          # ITL pending -- two years of funnel actuals
    } for k, m, b, tg, cad in rows]


def build_action_list(cards: list[dict], ctx: dict) -> list[dict]:
    """The client's own ask: "for this archetype, these are the 10 things I want to do".

    Sequenced by when the work has to start, not by what it is worth -- a dealer opened in
    month four cannot host a demo in month two. Two things it will not do: print an action
    whose number is zero ("appoint 0 new dealers" is noise), and print a selling plan for a
    scope where the product itself does not fit, which is how a tool talks a client into
    spending money that cannot work.
    """
    r, t, survey = ctx["row"], ctx["targets"], ctx["survey"]
    complaint = (survey.get("perception") or {}).get("top_complaint") or "the top complaint"
    bb = survey.get("buying_behaviour") or {}
    peak = bb.get("season_peak_month") or "the buying season"
    agro = ctx["agro"]
    worth = {p["owns"]: p["units"] for p in ctx["plays"]}
    by_key = {c["key"]: c for c in cards}
    reach = next((p for p in ctx["plays"] if p["owns"] == "reach"), None)
    tps = sum(x["touchpoints"] for x in (reach or {}).get("touchpoints") or [])
    net = by_key.get("network", {})
    blind = "no dealer list" in (net.get("summary") or "")
    unmet = survey.get("unmet_needs") or []

    if r["bucket"] == "No product fit":
        # One decision, and the work that supports it. Nothing about selling harder.
        items = [
            (f"Stop adding sales spend here until the product changes — it suits the land "
             f"only {float(r['product_fit']):.0%} as well as it needs to",
             "product", "now", "Regional sales head", 0),
            (f"Take the {len(unmet)} things farmers here need to the product team, with the "
             f"field evidence attached", "product", "month 1", "Product management", 0),
            (f"Run the nearest existing model on {agro.get('soil') or 'this'} soil under "
             f"{agro.get('crop') or 'the local crop'} for one season and measure it",
             "product", "one season", "Product management with R&D", 0),
            ("Keep the existing customers happy — service and spares only, no new push",
             "network", "ongoing", "Area Sales Manager", 0),
            ("Re-run this page after the trial; if the product fits, the whole plan changes",
             "product", "after the season", "Product management", 0),
        ]
    else:
        items = [
            (f"Make “{complaint.lower()}” the one thing every activity and every counter "
             f"conversation answers here", "activity", "week 1", "Marketing with the ASM", 0),
            (f"Split the {round(t['activities']['target']):,} activities across micro-markets "
             f"by where the unsold demand is, not by district", "activity", "week 2",
             "Area Sales Manager", worth.get("effort", 0)),
            (f"Add a second and third financier at every counter, and run loan camps six "
             f"weeks before {peak}", "sales", "month 1–2", "ASM with the finance partner",
             worth.get("approval", 0)),
            ("Put a subsidy desk at every counter and time the push to when each state's "
             "scheme window opens", "incentives", "month 1", "Dealer principal",
             worth.get("policy", 0)),
            ("Record every enquiry and every lost sale with a reason, and review them weekly",
             "sales", "month 1", "Dealer principal", worth.get("execution", 0)),
            (f"Keep {round(sum(x.get('hold', 0) for x in (by_key.get('inventory', {}).get('sections') or [{}])[0].get('rows', []))):,} "
             f"units in stock, split by how much each micro-market sells", "inventory",
             "month 1–2", "Supply chain", 0),
            ("Publish a dealer scorecard and pay the effort-linked incentives against it "
             "every month", "incentives", "from month 2", "Regional sales head", 0),
        ]
        if blind:
            items.insert(1, ("Get the dealer list for this state — we cannot plan the network "
                             "without it", "network", "week 1", "Network development", 0))
        elif tps:
            items.insert(1, (f"Check the gap list against the real dealer file, then open "
                             f"{tps} new dealer{'' if tps == 1 else 's'} and stock the common "
                             f"spares before the first sale", "network", "month 1–4",
                             "Network development", worth.get("reach", 0)))
        if unmet:
            items.append((f"Send the {len(unmet)} unmet needs to the product team with the "
                          f"field evidence", "product", "month 1", "Product management", 0))

    title = {c["key"]: c["title"] for c in cards}
    return [{"n": i + 1, "action": a, "use_case": title.get(k, k), "key": k,
             "when": w, "owner": o, "worth_units": round(u) or None}
            for i, (a, k, w, o, u) in enumerate(items[:10])]



# ---------------------------------------------------------------- request models

from pydantic import BaseModel, Field  # noqa: E402  (kept next to what it describes)


class Assumptions(BaseModel):
    """The inputs that are yours, not the data's.

    Each one is named on screen next to the modelled default it overrides. `top_barrier`
    is the only one that changes nothing quantitative -- it re-orders the seven cards so
    the one your customers actually complain about leads, and leaving it null lets the
    modelled survey pick it.
    """
    top_barrier: str | None = None          # finance | service | awareness | product
    approval_rate: float | None = None      # 0-1; defaults to the scope's own mean
    awareness: float = 0.38                 # 0-1; scales what extra BD activity yields
    dealer_density_pct: float = 20.0        # the network expansion being priced
    activity_uplift_pct: float = 25.0       # the BD push being priced
    months_of_cover: float = 1.5            # peak-month stock cover for the inventory norm
    demo_units: int = 12                    # demo fleet to place (tractors)


class PlaybookReq(BaseModel):
    archetype_id: str
    district_id: str | None = None
    micro_market_id: str | None = None
    assumptions: Assumptions = Field(default_factory=Assumptions)


# ---------------------------------------------------------------- assembly

def scale_basket(basket: list[dict], scale: float) -> list[dict]:
    """The archetype's SKU mix, carrying the scope's volumes.

    `_archetype_sku_basket` is computed at archetype grain because the mix is an archetype
    property -- which implements a sugarcane >50 HP market buys does not change between two
    of its districts. The volumes are not: leaving them at archetype scale would have a
    single micro-market's inventory norm hold the whole archetype's peak. Rates and
    indices (subsidy %, index vs national, cannibalisation %) are ratios and are left
    alone.
    """
    if abs(scale - 1.0) < 1e-9:
        return basket
    out = []
    for x in basket:
        y = dict(x)
        for k in ("units", "value", "new_units", "replacement_units", "headroom", "addressable"):
            if y.get(k) is not None:
                y[k] = float(y[k]) * scale
        out.append(y)
    return out


def _avg_subsidy(basket: list[dict]) -> tuple[float, str]:
    rows = [x for x in basket if x.get("subsidy_pct") is not None]
    units = sum(x["units"] for x in rows)
    if not units:
        return 0.0, "none"
    pct = sum(x["units"] * x["subsidy_pct"] for x in rows) / units
    prov = "real" if any(x["subsidy_provenance"] == "real" for x in rows) else "allocated"
    return pct, prov


def scope_approval(grain: pd.DataFrame, fallback: float) -> float:
    """Mean loan approval across the scope's villages.

    `approval_rate` is a real column on the village layer and the pipeline's own conversion
    identity uses it, so narrowing it to a district is a genuine narrowing rather than a
    re-labelled archetype average.
    """
    M = _m()
    if grain.empty:
        return fallback
    mm = _scope_villages_sql(grain)
    M.con().register("mm_sel", mm)
    try:
        v = M.con().execute("""
            SELECT avg(f.approval_rate) AS approval
            FROM village_features f
            JOIN village_micromarket v USING (village_id)
            JOIN mm_sel USING (micro_market_id)
        """).fetchdf()
    finally:
        M.con().unregister("mm_sel")
    val = float(v["approval"].iloc[0]) if len(v) and pd.notna(v["approval"].iloc[0]) else np.nan
    return float(val) if np.isfinite(val) else fallback


def _peak_month(basket: list[dict]) -> int:
    """The month this scope's own SKU mix peaks in."""
    M = _m()
    ids = [x["sku_id"] for x in basket[:8]]
    if not ids:
        return 0
    wt = {x["sku_id"]: x["units"] for x in basket[:8]}
    acc = {m: 0.0 for m in range(1, 13)}
    ph = ", ".join("?" * len(ids))
    for row in M.q(f"SELECT sku_id, month_of_year, season_index FROM seasonality "
                   f"WHERE sku_id IN ({ph})", ids):
        acc[int(row["month_of_year"])] += float(row["season_index"]) * wt.get(row["sku_id"], 0)
    return max(acc, key=acc.get) if any(acc.values()) else 0


def build(b: PlaybookReq, product: str = "implements") -> dict:
    """The whole Act page for one scope: survey, seven cards, tracking, ten actions."""
    M = _m()
    line = M._line(product)
    a = b.assumptions
    aid = b.archetype_id

    grain = scope_grain(line, aid, b.district_id, b.micro_market_id)
    if grain.empty:
        # An archetype always has micro-markets; a district or micro-market filter that
        # matches none of them is a stale selection from a previous archetype, and saying
        # so beats returning a playbook for the wrong place.
        raise M.HTTPException(404, "no micro-markets match that scope")

    r = scope_row(line, aid, grain)
    demand = float(r["potential_units_yr"]) or 1.0
    share = float(r["avg_sonalika_share"])
    conv = float(r["conversion_rate"])

    # The scope's share of its archetype. Anything computed at archetype grain -- the SKU
    # basket's volumes, the fitted price and promotion betas -- is read down by this, so a
    # single micro-market is never handed the whole archetype's response.
    arch_demand = float(M._archetype_row(aid, line)["potential_units_yr"]) or 1.0
    scale = float(min(max(demand / arch_demand, 0.0), 1.0))

    basket = scale_basket(M._archetype_sku_basket(aid), scale)
    rivals = M._archetype_rivals(aid, 4, b.district_id, b.micro_market_id)
    rivals_by_sku = M._archetype_rivals_by_sku(aid, 200, b.district_id, b.micro_market_id)
    key = (M._stamp(), line, aid, b.district_id, b.micro_market_id)
    factors = scope_factors(grain, key)
    agro = scope_agro(grain, line, key)
    segments = scope_segments(grain, line, key)

    approval_now = scope_approval(
        grain, float(M._approval_by_archetype().get(aid, 0.66)))
    # Default to a modest, stated improvement so the play is priced on first load; the
    # panel shows both today's rate and the assumed one, so nothing is hidden.
    approval_new = float(a.approval_rate) if a.approval_rate else min(approval_now + 0.05, 0.95)

    peers = M._plan_buckets(line)
    peers = peers[(peers["hp_belt"] == r["hp_belt"]) & (peers["archetype_id"] != aid)]
    peer_conv = float(peers["conversion_rate"].median()) if len(peers) else conv

    avg_subsidy, _sub_prov = _avg_subsidy(basket)
    survey = build_survey(factors, agro, {
        "product_fit": float(r["product_fit"]), "conversion_rate": conv,
        "peer_conv": peer_conv, "approval_rate": approval_now, "share": share,
        "leader_share": r.get("leader_share"), "hp_belt": r["hp_belt"],
        "rivals": rivals, "avg_subsidy": avg_subsidy, "peak_month": _peak_month(basket),
    })
    # A stated barrier overrides the modelled one; it re-orders the cards and nothing else.
    if a.top_barrier in ("finance", "service", "awareness", "product"):
        survey["top_barrier"] = a.top_barrier
        survey["top_barrier_label"] = BARRIER_LABEL[a.top_barrier]
        survey["barrier_source"] = "your override"
    else:
        survey["barrier_source"] = "modelled from this scope's villages"

    at_risk = float(sum(x["at_risk"] or 0 for x in rivals))
    winnable = float(sum(x["winnable"] or 0 for x in rivals))

    edge, replaces = build_edge_plays(r, grain, survey, rivals, at_risk, demand)
    if replaces:
        plays = edge
    else:
        plays = build_plays(line, aid, grain, r, a, survey, basket, rivals,
                            approval_now, approval_new, peer_conv)
        plays += build_scheme_plays(line, aid, grain, r, survey, basket, scale)
        plays += edge

    # Rank the flat list the way it has always been ranked: in a Defend scope what is
    # already ours leads, then by what a play is worth, then a nudge so the play answering
    # the top barrier comes first. Cards inherit this order for the plays they own. It is a
    # rank change only -- no branch here touches a units figure.
    plays.sort(key=lambda p: ((0 if p.get("mode") == "protect" else 1)
                              if r["bucket"] == "Defend" else 0, -p["units"]))
    barrier_owner = {"finance": "approval", "service": "reach",
                     "awareness": "effort", "product": "product"}.get(survey["top_barrier"])
    if barrier_owner:
        plays.sort(key=lambda p: (0 if p.get("mode") == "protect" and r["bucket"] == "Defend"
                                  else 1 if p["owns"] == barrier_owner else 2, -p["units"]))

    raw = float(sum(p["units"] for p in plays if p.get("mode") == "grow"))
    headroom = max(demand * (1 - share), 0.0)
    # Headroom is the only hard ceiling: we cannot sell more than the scope's unclaimed
    # demand. `winnable` is narrower -- volume in contests where a rival is closest and
    # beatable -- so it is context, not a cap on plays that grow the category for us.
    capped = min(raw, headroom)
    for p in plays:
        p["share_pts"] = round(p["units"] / demand * 100, 2)

    value = sum(x.get("value") or 0 for x in basket)
    units_all = sum(x.get("units") or 0 for x in basket)
    price_per_unit = (value / units_all) if units_all else 0.0

    ctx = {
        "line": line, "archetype_id": aid, "grain": grain, "row": r, "assumptions": a,
        "survey": survey, "factors": factors, "agro": agro, "segments": segments,
        "basket": basket, "rivals": rivals, "rivals_by_sku": rivals_by_sku,
        "plays": plays, "peer_conv": peer_conv, "approval_now": approval_now,
        "approval_new": approval_new, "targets": build_targets(r, capped),
        "price_per_unit": price_per_unit, "scale": scale,
    }
    cards = [f(ctx) for f in CARD_BUILDERS]
    for c in cards:
        c.setdefault("plays", [p for p in plays if p["use_case"] == c["key"]])
        c["units"] = round(sum(p["units"] for p in c["plays"] if p.get("mode") == "grow"))

    # Presentational order only: the card that answers the loudest complaint leads, the
    # rest keep their canonical numbering so the page still reads as the seven use cases.
    lead = ("product" if r["bucket"] == "No product fit"
            else BARRIER_LEADS.get(survey.get("top_barrier")))
    cards.sort(key=lambda c: (0 if c["key"] == lead else 1, c["n"]))

    scope = {
        "level": "micro-market" if b.micro_market_id else "district" if b.district_id else "archetype",
        "archetype_id": aid, "district_id": b.district_id,
        "micro_market_id": b.micro_market_id,
        "district": str(grain["district"].iloc[0]) if b.district_id else None,
        "micromarkets": int(len(grain)), "villages": int(grain["n_villages"].sum()),
        "districts": int(grain["district_id"].nunique()),
        "states": ", ".join(sorted(grain["state"].unique())),
    }

    return M.clean({
        "archetype_id": aid, "bucket": r["bucket"], "archetype": r["archetype"],
        "hp_belt": r["hp_belt"], "provenance": "modelled", "scope": scope,
        "situation": {
            "share": share, "leader": r["leader"], "leader_share": M.clean(r["leader_share"]),
            "product_fit": float(r["product_fit"]), "demand": round(demand),
            "deliveries": round(float(r["deliveries_yr"])),
            "activities": round(float(r["activities_yr"])),
            "enquiries": round(float(r["enquiries_yr"])),
            "tiv": round(float(r["tiv"])),
            "sales_coverage": float(r["sales_coverage"]),
            "service_coverage": float(r["service_coverage"]),
            "approval_rate": approval_now, "conversion_rate": conv, "peer_conversion": peer_conv,
            "scope_note": "Bucket, leader and rank are properties of the whole archetype; "
                          "everything else is computed for the selected scope.",
        },
        "survey": survey,
        "cards": cards,
        "plays": plays,
        "targets": ctx["targets"],
        "tracking": build_tracking(cards, ctx),
        "action_list": build_action_list(cards, ctx),
        "total": {"raw_units": round(raw), "capped_units": round(capped),
                  "headroom": round(headroom), "winnable_ceiling": round(winnable),
                  "capped_by": "headroom" if capped < raw else None},
        "rivals": rivals, "at_risk": round(at_risk), "winnable": round(winnable),
        "assumptions_used": a.model_dump(),
    })
