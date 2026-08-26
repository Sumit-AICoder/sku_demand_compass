"""Plain-English narratives for every view.

Two stages, always in this order:

  1. `facts_*()` computes a fact pack from DuckDB. Every number in a narrative comes
     from here. This stage is deterministic and testable.
  2. The narrative is written from that fact pack -- by a template that always works,
     and, when a Claude credential is configured, by Claude re-writing the SAME fact
     pack into better prose.

Claude is never asked what the numbers are, only how to say them. That is what makes
the narrative safe to put in front of an executive: a wrong figure is a bug in a query,
not an invention nobody can trace.
"""
from __future__ import annotations

from typing import Any

from api import llm
from pipeline.common import log

LOG = log("narrative")

SYSTEM = """You write short briefings for senior commercial leaders at a tractor and \
farm-implement manufacturer in India. You are given a JSON fact pack computed from the \
company's demand model.

Rules, in priority order:
1. Use ONLY numbers present in the fact pack. Never estimate, extrapolate or invent a \
figure. If something is not in the pack, do not mention it.
2. Write for a business reader, not a data scientist. No jargon: say "how strongly sales \
respond to rainfall", not "rainfall elasticity"; say "villages that look alike", not \
"cluster". Never use the words elasticity, beta, coefficient, percentile, propensity \
score, stochastic, or variance.
3. Lead with the single most decision-relevant fact. Then give the reason. Then the \
implication or action.
4. 3-5 sentences. No bullet points, no headings, no preamble like "This dashboard shows".
5. Round sensibly - "about 12,000 units", not "12,043.7".
6. If the fact pack flags something as simulated or uncertain, say so plainly in one \
short clause. Do not oversell.

Return only the briefing text."""


def _round(n: float | None, unit: str = "") -> str:
    if n is None:
        return "n/a"
    a = abs(n)
    if a >= 1e7:
        return f"₹{n / 1e7:,.0f} cr"
    if unit == "inr":
        return f"₹{n:,.0f}"
    if a >= 10000:
        return f"{n:,.0f}{unit}"
    if a >= 100:
        return f"{n:,.0f}{unit}"
    return f"{n:,.1f}{unit}"


def narrate(view: str, facts: dict[str, Any], template: str) -> dict:
    """Return the narrative plus how it was produced, so the UI can badge it."""
    text, source = template, "computed"
    if llm.available():
        import json
        out = llm.complete(
            SYSTEM,
            f"View: {view}\n\nFact pack:\n{json.dumps(facts, indent=2, default=str)}\n\n"
            f"Write the briefing.",
            max_tokens=700,
        )
        if out:
            text, source = out, llm.provider_name()
    return {"view": view, "text": text, "source": source, "facts": facts}


# ------------------------------------------------------------------ fact packs

def facts_executive(q) -> tuple[dict, str]:
    tot = q("""SELECT sum(potential_units_yr) "units", sum(potential_value_inr) "value",
                      sum(headroom) headroom, sum(addressable) addressable,
                      sum(owned) "owned"
               FROM village_sku""")[0]
    states = q("""SELECT v.state, sum(s.potential_units_yr) "units",
                         sum(s.potential_value_inr) "value"
                  FROM village_sku s JOIN geo_villages v USING (village_id)
                  GROUP BY 1 ORDER BY units DESC""")
    acts = q("""SELECT action_segment, count(*) "villages", sum(potential_units_yr) "units",
                       sum(headroom) headroom
                FROM village_insights GROUP BY 1 ORDER BY units DESC""")
    top = q("""SELECT r.name, sum(s.potential_units_yr) "units"
               FROM village_sku s JOIN sku_ref r USING (sku_id)
               GROUP BY 1 ORDER BY units DESC LIMIT 3""")
    cat = q("""SELECT category, sum(potential_units_yr) "units"
               FROM village_sku GROUP BY 1 ORDER BY units DESC LIMIT 1""")[0]

    pen = (tot["owned"] / tot["addressable"] * 100) if tot["addressable"] else 0
    convert = next((a for a in acts if a["action_segment"] == "Convert now"), None)
    access = next((a for a in acts if a["action_segment"] == "Build access"), None)

    facts = {
        "total_demand_units_per_year": round(tot["units"]),
        "total_market_value_inr_crore": round(tot["value"] / 1e7),
        "unserved_implements": round(tot["headroom"]),
        "current_penetration_pct": round(pen, 1),
        "by_state": [{"state": s["state"], "units": round(s["units"]),
                      "value_crore": round(s["value"] / 1e7)} for s in states],
        "top_products": [{"name": t["name"], "units": round(t["units"])} for t in top],
        "biggest_category": cat["category"],
        "action_segments": [{"action": a["action_segment"], "villages": a["villages"],
                             "units": round(a["units"]), "unserved": round(a["headroom"])}
                            for a in acts],
        "villages_analysed": sum(a["villages"] for a in acts),
        "note": "Sales history, dealer network and competitor share are simulated; "
                "geography, crop and soil structure follow published sources.",
    }
    lead = states[0]
    t = (f"Across {facts['villages_analysed']:,} villages in three states the model sees "
         f"{_round(tot['units'])} implements of annual demand, worth about "
         f"₹{facts['total_market_value_inr_crore']:,.0f} crore. "
         f"{lead['state']} is the largest single market at {_round(lead['units'])} units. "
         f"Only {pen:.0f}% of the tractors that could carry an implement already have one, "
         f"leaving roughly {_round(tot['headroom'])} unserved. ")
    if convert and access:
        t += (f"The clearest opportunity is {convert['villages']:,} villages where demand "
              f"exists and a dealer is already close — {_round(convert['units'])} units a "
              f"year. A further {access['villages']:,} villages hold {_round(access['units'])} "
              f"units but sit too far from any dealer to capture it today.")
    return facts, t


def facts_overview(q, sku=None, category=None, month=None) -> tuple[dict, str]:
    where, params = [], []
    if sku:
        where.append("s.sku_id = ?"); params.append(sku)
    elif category:
        where.append("s.category = ?"); params.append(category)
    w = (" WHERE " + " AND ".join(where)) if where else ""

    d = q(f"""SELECT d.district, d.state, sum(s.potential_units_yr) "units",
                     sum(s.headroom) headroom
              FROM village_sku s JOIN geo_districts d USING (district_id){w}
              GROUP BY 1,2 ORDER BY "units" DESC LIMIT 5""", params)
    tot = q(f'SELECT sum(potential_units_yr) AS "units", sum(headroom) AS headroom, '
            f'sum(potential_value_inr) AS "value" FROM village_sku s{w}', params)[0]
    scope = sku or category or "all products"
    conc = sum(x["units"] for x in d) / tot["units"] * 100 if tot["units"] else 0

    facts = {
        "scope": scope, "month": month,
        "total_units": round(tot["units"]), "total_value_crore": round(tot["value"] / 1e7),
        "unserved": round(tot["headroom"]),
        "top_5_districts": [{"district": x["district"], "state": x["state"],
                             "units": round(x["units"])} for x in d],
        "share_of_demand_in_top_5_pct": round(conc, 1),
    }
    t = (f"For {scope}, annual demand across the three states is {_round(tot['units'])} "
         f"implements worth about ₹{facts['total_value_crore']:,.0f} crore. "
         f"{d[0]['district']} in {d[0]['state']} leads at {_round(d[0]['units'])} units, "
         f"followed by {d[1]['district']} and {d[2]['district']}. "
         f"The top five districts hold {conc:.0f}% of total demand, so effort concentrated "
         f"there covers a disproportionate share of the opportunity.")
    if month:
        t += f" Figures shown are the run-rate for month {month}, not the annual total."
    return facts, t


def facts_geography(q, level: str, node_id: str, name: str) -> tuple[dict, str]:
    col = {"district": "district_id", "block": "block_id", "state": "state"}[level]
    tbl = "village_insights"
    rows = q(f"""SELECT count(*) "villages", sum(potential_units_yr) "units",
                        sum(headroom) headroom, avg(attach_rate) "attach",
                        avg(dealer_distance_km) dealer_km
                 FROM {tbl} WHERE {col} = ?""", [node_id])[0]
    acts = q(f"""SELECT action_segment, count(*) n, sum(potential_units_yr) "units"
                 FROM {tbl} WHERE {col} = ? GROUP BY 1 ORDER BY units DESC""", [node_id])
    skus = q(f"""SELECT r.name, sum(s.potential_units_yr) "units"
                 FROM village_sku s JOIN sku_ref r USING (sku_id)
                 JOIN {tbl} i USING (village_id)
                 WHERE i.{col} = ? GROUP BY 1 ORDER BY units DESC LIMIT 3""", [node_id])
    arch = q(f"""SELECT archetype, count(*) n FROM {tbl} WHERE {col} = ?
                 GROUP BY 1 ORDER BY n DESC LIMIT 2""", [node_id])

    facts = {
        "level": level, "name": name,
        "villages": rows["villages"], "units": round(rows["units"]),
        "unserved": round(rows["headroom"]),
        "avg_implements_per_tractor": round(rows["attach"], 2),
        "avg_km_to_dealer": round(rows["dealer_km"], 1),
        "village_types": [{"type": a["archetype"], "villages": a["n"]} for a in arch],
        "actions": [{"action": a["action_segment"], "villages": a["n"],
                     "units": round(a["units"])} for a in acts],
        "top_products": [{"name": s["name"], "units": round(s["units"])} for s in skus],
    }
    top_act = acts[0]
    t = (f"{name} covers {rows['villages']:,} villages with {_round(rows['units'])} "
         f"implements of annual demand and {_round(rows['headroom'])} still unserved. "
         f"Villages here average {rows['attach']:.2f} implements per tractor and sit "
         f"{rows['dealer_km']:.0f} km from the nearest dealer. "
         f"Most are '{arch[0]['archetype']}' villages. "
         f"The largest group — {top_act['n']:,} villages worth "
         f"{_round(top_act['units'])} units — falls in '{top_act['action_segment']}'. "
         f"{skus[0]['name']} is the strongest product here.")
    return facts, t


def facts_village(q, village_id: str) -> tuple[dict, str]:
    v = q("SELECT * FROM village_insights WHERE village_id = ?", [village_id])
    if not v:
        raise KeyError(village_id)
    v = v[0]
    skus = q("""SELECT r.name, s.potential_units_yr units, s.penetration
                FROM village_sku s JOIN sku_ref r USING (sku_id)
                WHERE s.village_id = ? ORDER BY units DESC LIMIT 4""", [village_id])
    facts = {
        "village": v["village"], "district": v["district"], "state": v["state"],
        "village_type": v["archetype"], "similar_villages_group": v["micro_id"],
        "recommended_action": v["action_segment"], "why": v["action_rationale"],
        "opportunity_rank_in_district": f"{v['rank_in_district']} of {v['villages_in_district']}",
        "annual_demand_units": round(v["potential_units_yr"], 1),
        "unserved_implements": round(v["headroom"], 1),
        "implements_per_tractor": round(v["attach_rate"], 2),
        "peer_implements_per_tractor": round(v["peer_attach_micro"], 2),
        "tractors": round(v["tractors"]),
        "avg_farm_size_ha": round(v["avg_holding_ha"], 2),
        "main_crop": v["dominant_crop"], "soil": v["soil_texture"],
        "irrigated_share_pct": round(v["irrigation_ratio"] * 100),
        "km_to_dealer": round(v["dealer_distance_km"], 1),
        "what_makes_it_different": [v["distinct_1"], v["distinct_2"], v["distinct_3"]],
        "best_products": [{"name": s["name"], "units": round(s["units"], 1),
                           "already_owned_pct": round(s["penetration"] * 100)} for s in skus],
    }
    return facts, v["headline"]


def facts_ucm(q, district_id: str, name: str) -> tuple[dict, str]:
    up = q("""WITH d AS (SELECT * FROM ucm_decomposition WHERE district_id = ?)
              SELECT 1""", [district_id])                      # existence probe
    dec = q("SELECT * FROM ucm_decomposition WHERE district_id = ? ORDER BY month", [district_id])
    if not dec:
        raise KeyError(district_id)
    diag = q("SELECT * FROM ucm_diagnostics WHERE district_id = ?", [district_id])[0]
    betas = q("""SELECT regressor, beta, significant, sign_ok, usable FROM ucm_betas
                 WHERE district_id = ? ORDER BY abs(beta) DESC LIMIT 5""", [district_id])

    cur = dec[-12:]; prev = dec[-24:-12]
    import math
    growth = (math.exp(sum(r["observed_log"] for r in cur) / 12
                       - sum(r["observed_log"] for r in prev) / 12) - 1) * 100
    comps = []
    for c in [k for k in dec[0] if k.startswith("contrib_")] + ["trend", "seasonal", "cycle"]:
        delta = sum(r[c] for r in cur) / 12 - sum(r[c] for r in prev) / 12
        comps.append({"driver": c.replace("contrib_", "").replace("_", " "),
                      "effect_pp": round(delta * 100, 1)})
    comps.sort(key=lambda x: -abs(x["effect_pp"]))

    LABEL = {"rainfall departure": "rainfall", "mandi price index": "crop prices",
             "credit depth": "farm credit", "subsidy intensity": "government subsidy",
             "diesel price": "diesel cost", "ndvi anomaly": "crop health",
             "rural wage index": "farm wages", "reservoir status": "water storage",
             "pmfby claims": "insurance payouts", "fertilizer offtake": "fertiliser use",
             "msp change": "support prices", "trend": "underlying growth",
             "seasonal": "seasonal timing", "cycle": "multi-year cycle"}
    for c in comps:
        c["driver"] = LABEL.get(c["driver"], c["driver"])

    facts = {
        "district": name,
        "tractor_sales_change_pct_year_on_year": round(growth, 1),
        "what_drove_it": comps[:6],
        "model_accuracy_pct_error": round(diag["backtest_mape"], 1),
        "simple_benchmark_error_pct": round(diag["snaive_mape"], 1),
        "beats_benchmark": bool(diag["beats_snaive"]),
        "strongest_drivers": [{"driver": LABEL.get(b["regressor"].replace("_", " "),
                                                   b["regressor"].replace("_", " ")),
                               "strength": round(b["beta"], 2),
                               "reliable": bool(b["usable"])} for b in betas],
        "note": "Tractor registrations are simulated in this build; the method is "
                "validated by recovering coefficients known by construction.",
    }
    up_, down_ = [c for c in comps if c["effect_pp"] > 0], [c for c in comps if c["effect_pp"] < 0]
    t = (f"Tractor sales in {name} moved {growth:+.0f}% over the last year. "
         f"The biggest single influence was {comps[0]['driver']} "
         f"({comps[0]['effect_pp']:+.0f} points). ")
    if up_ and down_:
        t += (f"{up_[0]['driver'].capitalize()} pushed sales up "
              f"{up_[0]['effect_pp']:+.0f} points while {down_[0]['driver']} pulled them "
              f"down {down_[0]['effect_pp']:.0f}. ")
    t += (f"The model's forecasts are off by about {diag['backtest_mape']:.0f}% when tested "
          f"on months it had not seen, against {diag['snaive_mape']:.0f}% for a simple "
          f"repeat-last-year rule — so it is materially better than guessing.")
    return facts, t


def facts_clusters(q) -> tuple[dict, str]:
    micro = q("""SELECT * FROM micro_segments ORDER BY avg_opportunity DESC LIMIT 6""")
    arch = q("""SELECT archetype, count(*) "villages", sum(potential_units_yr) "units",
                       avg(attach_rate) "attach"
                FROM village_insights GROUP BY 1 ORDER BY units DESC""")
    acts = q("""SELECT action_segment, count(*) n, sum(headroom) headroom
                FROM village_insights GROUP BY 1 ORDER BY headroom DESC""")
    facts = {
        "village_types": [{"type": a["archetype"], "villages": a["villages"],
                           "units": round(a["units"]),
                           "implements_per_tractor": round(a["attach"], 2)} for a in arch],
        "top_pockets": [{"pocket": m["micro_id"], "villages": m["n_villages"],
                         "districts": m["districts"],
                         "opportunity_score": m["avg_opportunity"],
                         "action": m["dominant_action"],
                         "km_to_dealer": m["dealer_km"]} for m in micro],
        "actions": [{"action": a["action_segment"], "villages": a["n"],
                     "unserved": round(a["headroom"])} for a in acts],
    }
    top = micro[0]
    t = (f"The {sum(a['villages'] for a in arch):,} villages fall into "
         f"{len(arch)} broad types, each split further into pockets that behave "
         f"differently on the ground. "
         f"'{arch[0]['archetype']}' is the largest by demand, averaging "
         f"{arch[0]['attach']:.2f} implements per tractor. "
         f"The single best pocket is {top['micro_id']} — {top['n_villages']:,} villages "
         f"concentrated in {top['districts']}, averaging {top['dealer_km']:.0f} km to a "
         f"dealer, where the recommended action is '{top['dominant_action']}'.")
    return facts, t


def facts_sku(q, category=None) -> tuple[dict, str]:
    w, p = (" WHERE s.category = ?", [category]) if category else ("", [])
    rows = q(f"""SELECT r.name, r.category_label, sum(s.potential_units_yr) "units",
                        sum(s.new_units_yr) new_u, sum(s.replacement_units_yr) repl,
                        sum(s.potential_value_inr) "value"
                 FROM village_sku s JOIN sku_ref r USING (sku_id){w}
                 GROUP BY 1,2 ORDER BY units DESC LIMIT 8""", p)
    tot = sum(r["units"] for r in rows)
    facts = {
        "scope": category or "all categories",
        "top_products": [{"name": r["name"], "category": r["category_label"],
                          "units": round(r["units"]),
                          "new_demand": round(r["new_u"]),
                          "replacement_demand": round(r["repl"]),
                          "value_crore": round(r["value"] / 1e7)} for r in rows],
    }
    lead = rows[0]
    repl_share = lead["repl"] / lead["units"] * 100 if lead["units"] else 0
    t = (f"{lead['name']} is the largest opportunity at {_round(lead['units'])} units a "
         f"year, worth about ₹{lead['value'] / 1e7:,.0f} crore. "
         f"{repl_share:.0f}% of that is replacement of implements already in the field, "
         f"which is defended business; the rest is new demand and therefore contested. "
         f"{rows[1]['name']} and {rows[2]['name']} follow. "
         f"Products with a high replacement share need service and parts coverage more "
         f"than new selling effort.")
    return facts, t


def facts_scenario(q, result: dict, shocks: dict) -> tuple[dict, str]:
    tot = result["total"]
    by = result["by_level"]
    facts = {
        "changes_applied": shocks,
        "baseline_units": tot["units_base"], "scenario_units": tot["units_scenario"],
        "change_pct": tot["delta_pct"],
        "range_low_pct": tot["ci_low_pct"], "range_high_pct": tot["ci_high_pct"],
        "by_state": [{"state": r.get("state") or r.get("district_id"),
                      "change_pct": round(r["delta_pct"], 1)} for r in by],
    }
    worst = min(by, key=lambda r: r["delta_pct"])
    best = max(by, key=lambda r: r["delta_pct"])
    t = (f"Under this scenario demand moves {tot['delta_pct']:+.0f}%, from "
         f"{_round(tot['units_base'])} to {_round(tot['units_scenario'])} units a year, "
         f"with a likely range of {tot['ci_low_pct']:+.0f}% to {tot['ci_high_pct']:+.0f}%. ")
    if len(by) > 1 and abs(worst["delta_pct"] - best["delta_pct"]) > 2:
        t += (f"The impact is uneven: {worst.get('state', '')} moves "
              f"{worst['delta_pct']:+.0f}% while {best.get('state', '')} moves only "
              f"{best['delta_pct']:+.0f}%, because their farming systems respond "
              f"differently to this change. Plan stock and field effort accordingly.")
    return facts, t
