"""Ask-the-data chat.

The model -- Azure OpenAI GPT-4.1 or Claude, whichever is configured -- is given a set of
QUERY TOOLS, not the data and not free SQL. Each tool is a parameterised, whitelisted
query against the marts. That has three consequences worth stating:

  * every answer is backed by a query that actually ran, and the trace is returned to
    the UI so the user can see the working;
  * the model cannot invent a number, because it never answers from memory -- it has to
    call a tool to see anything;
  * no arbitrary SQL reaches DuckDB, so a prompt-injected "drop everything" is not
    expressible.

Field names carry their units (`unserved_implements`, not `unserved`) because a bare
count is otherwise easy to read as a percentage -- a real failure observed before the
rename.

Without any credential the same tools are still reachable through a keyword intent
router, so the chat box answers the common questions rather than being dead.
"""
from __future__ import annotations

import json
import re

from api import llm
from pipeline.common import Config, log

LOG = log("chat")

# The nine real categories. A model asked for "horticulture" (a crop, not a category)
# and got an empty result it could not diagnose; the tools now reject an invalid value
# with the valid ones attached, so the next turn can self-correct.
VALID_CATEGORIES = {"tillage", "sowing", "crop_protection", "irrigation", "harvesting",
                    "residue", "post_harvest", "haulage", "precision"}

# Words that appear across many product names and so identify none of them.
GENERIC_WORDS = {"tractor", "tractors", "farm", "agricultural", "agriculture", "machine",
                 "mounted", "driven", "hydraulic", "multi", "crop", "wheel", "four",
                 "cum", "type", "loaded", "spring", "self", "propelled", "power"}

SYSTEM = """You are the analyst behind a demand-planning dashboard for Sonalika, an \
Indian tractor and farm-implement manufacturer. You answer questions about the model's \
data for Punjab, Madhya Pradesh and Maharashtra.

How to work:
- ALWAYS call a tool before stating any number. You have no reliable memory of this \
data; anything you state without a tool call is a guess and unacceptable.
- Call several tools when the question needs them. Prefer one precise tool call over a \
broad one.
- If the tools cannot answer the question, say exactly what is missing rather than \
approximating.

How to write:
- Business English for a commercial leader. No jargon: never say elasticity, \
coefficient, percentile, cluster, propensity score, or standard deviation. Say "how \
strongly sales respond to X", "villages that look alike", "opportunity score".
- Lead with the answer, then the supporting numbers, then what it implies.
- 2-5 sentences unless a list is genuinely clearer. Round numbers sensibly.
- Where the data is simulated (implement sales history, dealer network, competitor \
share, tractor registrations), say so in a short clause when it materially affects the \
answer. Geography, crop mix and soil structure follow published sources.

Reading the numbers correctly:
- Every field ending in _implements, _per_year or _units is a COUNT OF IMPLEMENTS, never a percentage. Only fields ending in _pct or _share are percentages.
- "unserved_implements" is how many more implements that place could absorb, not a share of farms.
- "implements_per_tractor" is a ratio around 0.5-2.0, not a percentage.
- "opportunity_score_0_100" is a rank score out of 100, not units and not a percentage.
If a field name does not tell you the unit, say what you are unsure of rather than guessing.

Memory:
- You can see the earlier turns of this conversation. Resolve follow-ups against them: "what about Maharashtra?" after a question about Punjab means the same question, different state. Never re-ask for something already established in the conversation.
- Even so, you must still call a tool for the NEW numbers. Never reuse a figure from an earlier turn as if it answered a different question.
- When the user tells you something durable about themselves or their priorities -- the territory they cover, the products they are pushing, how they like answers -- call the `remember` tool once with a short third-person fact. Do not remember one-off questions, data values, or anything the tools can look up.

Presenting the answer:
- After you have the data, call `present` to render it, then write your prose. A ranked list of ten districts is far easier to read as a table than as a sentence, and a month-by-month series is far easier as a line.
- Choose the form from the QUESTION, not from habit:
  * `table`   - lists, rankings, "show me", anything with several columns worth comparing.
  * `bar`     - comparing one number across a handful of named things (districts, products, rivals). The default for "top N" questions.
  * `line`    - anything ordered in time: months, seasonality, a trend.
  * `pie`     - parts of one whole, and only when there are 2-6 parts (new vs replacement, share split). Never for rankings.
  * `scatter` - the relationship between two numbers (dealer distance against demand).
- One visual is usually enough; two only if they say genuinely different things.
- Do NOT then repeat the table in prose. Say what it means: the pattern, the outlier, the implication. Two or three sentences.
- If the answer is a single number or a yes/no, skip `present` entirely.

Never invent villages, districts or products that the tools did not return."""

TOOLS = [
    {
        "name": "top_geographies",
        "description": "Rank states, districts, blocks or villages by demand potential. "
                       "Use for 'where is the biggest opportunity', 'top districts for X'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {"type": "string", "enum": ["state", "district", "block", "village"]},
                "state": {"type": "string", "description": "Optional filter, e.g. Punjab"},
                "district": {"type": "string", "description": "Optional district name filter"},
                "sku_id": {"type": "string",
                           "description": "Optional exact product id, e.g. SUPER_SEEDER"},
                "product": {"type": "string",
                            "description": "Optional product in plain words, e.g. "
                                           "'orchard sprayer', 'super seeder'. Resolved "
                                           "server-side -- use this when you do not know "
                                           "the exact id."},
                "category": {"type": "string", "enum": ["tillage", "sowing", "crop_protection", "irrigation", "harvesting", "residue", "post_harvest", "haulage", "precision"],
                             "description": "Optional product category. These nine are the "
                                            "only valid values; horticulture is a CROP, not "
                                            "a category -- for orchard/horticulture spraying "
                                            "use category crop_protection or product "
                                            "'orchard sprayer'."},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["level"],
        },
    },
    {
        "name": "top_products",
        "description": "Rank products (SKUs) by demand, optionally within a state or district. "
                       "Returns new vs replacement demand split.",
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {"type": "string"}, "district": {"type": "string"},
                "category": {"type": "string", "enum": ["tillage", "sowing", "crop_protection", "irrigation", "harvesting", "residue", "post_harvest", "haulage", "precision"]},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "village_detail",
        "description": "Full profile of one village: type, recommended action, demand, "
                       "farm and soil characteristics, best products, what makes it distinctive.",
        "input_schema": {
            "type": "object",
            "properties": {"village_id": {"type": "string"},
                           "village_name": {"type": "string"}},
        },
    },
    {
        "name": "find_villages",
        "description": "Find villages matching commercial criteria. Use for targeting questions: "
                       "'villages to convert now in Punjab', 'underserved villages far from a dealer'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {"type": "string"}, "district": {"type": "string"},
                "action_segment": {"type": "string",
                                   "enum": ["Convert now", "Build access", "Defend", "Monitor"]},
                "archetype": {"type": "string"},
                "min_headroom": {"type": "number"},
                "max_dealer_km": {"type": "number"},
                "limit": {"type": "integer", "default": 15},
            },
        },
    },
    {
        "name": "village_segments",
        "description": "Summary of village types (archetypes) and finer pockets (micro-segments), "
                       "with village counts, demand and recommended actions.",
        "input_schema": {
            "type": "object",
            "properties": {"state": {"type": "string"},
                           "detail": {"type": "string", "enum": ["archetype", "micro"]}},
        },
    },
    {
        "name": "sales_drivers",
        "description": "What drives tractor sales in a district and by how much, plus the "
                       "year-on-year breakdown of what caused recent growth or decline.",
        "input_schema": {
            "type": "object",
            "properties": {"district": {"type": "string"}},
            "required": ["district"],
        },
    },
    {
        "name": "data_sources",
        "description": "What data the model uses, which parts are real vs simulated, and "
                       "how accurate the model is. Use for any 'where does this come from' question.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "competition",
        "description": "Competitive position: which rivals hold volume, what Sonalika "
                       "can win back, what is at risk, and how each brand competes on "
                       "price and dealer reach. Use for any question about rivals, "
                       "market share, competitors or losing/winning business.",
        "input_schema": {
            "type": "object",
            "properties": {
                "view": {"type": "string",
                         "enum": ["rivals", "brands", "headtohead", "cannibalisation"],
                         "description": "rivals = who holds what and what is winnable; "
                                        "brands = price and reach position of each brand; "
                                        "headtohead = one named rival in detail; "
                                        "cannibalisation = Sonalika products competing "
                                        "with each other."},
                "rival": {"type": "string", "description": "Required for headtohead."},
                "state": {"type": "string"},
                "category": {"type": "string", "enum": [
                    "tillage", "sowing", "crop_protection", "irrigation", "harvesting",
                    "residue", "post_harvest", "haulage", "precision"]},
            },
            "required": ["view"],
        },
    },
    {
        "name": "present",
        "description": "Render data you have already retrieved as a table or chart. "
                       "Call this AFTER the query tool that produced the data, "
                       "referencing it by step number. Use it whenever the answer is a "
                       "list, a ranking, a comparison or a trend.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_step": {"type": "integer",
                              "description": "Which earlier tool call produced the data "
                                             "(1 = your first call this turn)."},
                "kind": {"type": "string",
                         "enum": ["table", "bar", "line", "pie", "scatter"]},
                "title": {"type": "string"},
                "x": {"type": "string",
                      "description": "Field for the category / x axis. Charts only."},
                "y": {"type": "string",
                      "description": "Numeric field to plot. Charts only."},
                "y2": {"type": "string",
                       "description": "Optional second numeric series, for grouped bars."},
                "columns": {"type": "array", "items": {"type": "string"},
                            "description": "Table only: which fields to show, in order. "
                                           "Omit to show all."},
                "limit": {"type": "integer", "default": 15},
            },
            "required": ["from_step", "kind"],
        },
    },
    {
        "name": "remember",
        "description": "Store a durable fact about THIS USER for future conversations -- "
                       "the territory they cover, products they are prioritising, or how "
                       "they want answers. Use only for lasting context the user states "
                       "about themselves. Never for data values, one-off questions, or "
                       "anything a query tool can look up.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {"type": "string",
                         "description": "Short third-person fact, e.g. "
                                        "'Covers Punjab and Haryana territory'"},
            },
            "required": ["fact"],
        },
    },
    {
        "name": "compare",
        "description": "Compare two geographies side by side on demand, penetration, "
                       "dealer access and farm structure.",
        "input_schema": {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "string"},
                           "level": {"type": "string", "enum": ["state", "district"]}},
            "required": ["a", "b"],
        },
    },
]


def make_executor(q, session_id: str | None = None):
    """Bind the query tools to a DuckDB query function and, optionally, a session.

    `remember` needs the session and `present` needs the results of earlier calls, so
    both are closed over here. Keeping them out of the tool schema means the model can
    neither address someone else's memory nor fabricate the data it renders -- `present`
    can only point at rows a query actually returned.
    """
    history: list[dict] = []      # every tool result this turn, in order
    blocks: list[dict] = []       # what the UI should render

    def top_geographies(level, state=None, district=None, sku_id=None,
                        category=None, product=None, limit=10):
        limit = min(int(limit or 10), 50)
        if product and not sku_id:
            sku_id = _match_sku(product.lower())
            if not sku_id:
                return {"error": f"no product matching '{product}'",
                        "valid_products": [s["name"] for s in _sku_catalogue()]}
        if category and category not in VALID_CATEGORIES:
            return {"error": f"'{category}' is not a product category",
                    "valid_categories": sorted(VALID_CATEGORIES),
                    "hint": "horticulture is a crop, not a category; orchard sprayers "
                            "sit in crop_protection"}
        where, p = [], []
        if state:
            where.append("v.state = ?"); p.append(state)
        if district:
            where.append("d.district = ?"); p.append(district)
        if sku_id:
            where.append("s.sku_id = ?"); p.append(sku_id)
        if category:
            where.append("s.category = ?"); p.append(category)
        w = (" WHERE " + " AND ".join(where)) if where else ""
        grp = {"state": "v.state", "district": "d.district",
               "block": "v.block_id", "village": "v.village"}[level]
        return q(f"""SELECT {grp} AS "name", v.state,
                            round(sum(s.potential_units_yr)) AS units_per_year,
                            round(sum(s.headroom)) AS unserved_implements,
                            round(sum(s.potential_value_inr)/1e7,1) AS value_crore
                     FROM village_sku s
                     JOIN geo_villages v USING (village_id)
                     JOIN geo_districts d ON d.district_id = v.district_id{w}
                     GROUP BY 1,2 ORDER BY units_per_year DESC LIMIT {limit}""", p)

    def top_products(state=None, district=None, category=None, limit=10):
        limit = min(int(limit or 10), 40)
        where, p = [], []
        if state:
            where.append("v.state = ?"); p.append(state)
        if district:
            where.append("d.district = ?"); p.append(district)
        if category:
            where.append("s.category = ?"); p.append(category)
        w = (" WHERE " + " AND ".join(where)) if where else ""
        return q(f"""SELECT r.name AS product, r.category_label AS category,
                            round(sum(s.potential_units_yr)) AS units_per_year,
                            round(sum(s.new_units_yr)) AS new_demand,
                            round(sum(s.replacement_units_yr)) AS replacement_demand,
                            round(sum(s.potential_value_inr)/1e7,1) AS value_crore
                     FROM village_sku s JOIN sku_ref r USING (sku_id)
                     JOIN geo_villages v USING (village_id)
                     JOIN geo_districts d ON d.district_id = v.district_id{w}
                     GROUP BY 1,2 ORDER BY units_per_year DESC LIMIT {limit}""", p)

    def village_detail(village_id=None, village_name=None):
        if village_id:
            rows = q("SELECT * FROM village_insights WHERE village_id = ?", [village_id])
        elif village_name:
            rows = q("SELECT * FROM village_insights WHERE village ILIKE ? LIMIT 3",
                     [f"%{village_name}%"])
        else:
            return {"error": "give a village_id or village_name"}
        if not rows:
            return {"error": "no such village"}
        v = rows[0]
        keep = ["village", "district", "state", "archetype", "micro_id", "action_segment",
                "action_rationale", "opportunity_score", "rank_in_district",
                "villages_in_district", "potential_units_yr", "headroom", "attach_rate",
                "peer_attach_micro", "tractors", "avg_holding_ha", "dominant_crop",
                "soil_texture", "irrigation_ratio", "dealer_distance_km", "top_sku",
                "distinct_1", "distinct_2", "distinct_3", "headline"]
        out = {k: v[k] for k in keep if k in v}
        out["best_products"] = q("""SELECT r.name, round(s.potential_units_yr,1) "units",
                                          round(s.penetration*100) already_owned_pct
                                   FROM village_sku s JOIN sku_ref r USING (sku_id)
                                   WHERE s.village_id = ?
                                   ORDER BY units DESC LIMIT 5""", [v["village_id"]])
        return out

    def find_villages(state=None, district=None, action_segment=None, archetype=None,
                      min_headroom=None, max_dealer_km=None, limit=15):
        limit = min(int(limit or 15), 60)
        where, p = ["1=1"], []
        for col, val in [("state", state), ("district", district),
                         ("action_segment", action_segment), ("archetype", archetype)]:
            if val:
                where.append(f"{col} = ?"); p.append(val)
        if min_headroom is not None:
            where.append("headroom >= ?"); p.append(float(min_headroom))
        if max_dealer_km is not None:
            where.append("dealer_distance_km <= ?"); p.append(float(max_dealer_km))
        return q(f"""SELECT village, district, state, archetype, action_segment,
                            round(opportunity_score) opportunity_score_0_100,
                            round(potential_units_yr,1) implements_demand_per_year,
                            round(headroom,1) unserved_implements,
                            round(attach_rate,2) implements_per_tractor,
                            round(dealer_distance_km,1) km_to_dealer, top_sku
                     FROM village_insights WHERE {' AND '.join(where)}
                     ORDER BY opportunity_score DESC LIMIT {limit}""", p)

    def village_segments(state=None, detail="archetype"):
        where, p = ([], [])
        if state:
            where.append("state = ?"); p.append(state)
        w = (" WHERE " + " AND ".join(where)) if where else ""
        if detail == "micro":
            return q(f"""SELECT micro_id, count(*) "villages",
                                round(avg(opportunity_score)) avg_opportunity,
                                round(sum(potential_units_yr)) units_per_year,
                                round(avg(dealer_distance_km),1) km_to_dealer,
                                mode(action_segment) main_action
                         FROM village_insights{w}
                         GROUP BY 1 ORDER BY units_per_year DESC LIMIT 25""", p)
        return q(f"""SELECT archetype AS village_type, count(*) "villages",
                            round(sum(potential_units_yr)) units_per_year,
                            round(sum(headroom)) unserved_implements,
                            round(avg(attach_rate),2) implements_per_tractor,
                            mode(action_segment) main_action
                     FROM village_insights{w}
                     GROUP BY 1 ORDER BY units_per_year DESC""", p)

    def sales_drivers(district):
        d = q("SELECT district_id, district FROM geo_districts WHERE district ILIKE ? LIMIT 1",
              [f"%{district}%"])
        if not d:
            return {"error": f"no district matching '{district}'"}
        did = d[0]["district_id"]
        from api.narrative import facts_ucm
        facts, _ = facts_ucm(q, did, d[0]["district"])
        return facts

    def data_sources():
        from pipeline.common import Manifest
        diag = q("""SELECT count(*) districts, sum(CASE WHEN beats_snaive THEN 1 ELSE 0 END) beat,
                           round(median(backtest_mape),1) model_error_pct,
                           round(median(snaive_mape),1) simple_rule_error_pct
                    FROM ucm_diagnostics""")[0]
        origin = q("SELECT origin, count(*) n FROM weight_origin GROUP BY 1")
        return {
            "real": ["district boundaries and names", "district and village counts "
                     "(Census 2011 anchors)", "product range, HP bands and seasonal windows"],
            "simulated": ["implement sales history", "dealer network", "competitor share",
                          "custom-hiring density", "finance penetration",
                          "tractor registrations"],
            "why_simulated": "The government village directory, the Vahan vehicle "
                             "registration dashboard and Agmarknet have no machine-readable "
                             "public interface, so those layers are generated from documented "
                             "assumptions rather than scraped.",
            "model_accuracy": {"districts_modelled": diag["districts"],
                               "beating_simple_benchmark": diag["beat"],
                               "typical_error_pct": diag["model_error_pct"],
                               "simple_rule_error_pct": diag["simple_rule_error_pct"]},
            "weights": {r["origin"]: r["n"] for r in origin},
            "fetch_log": Manifest.summary().to_dict("records"),
        }

    def compare(a, b, level="district"):
        col = "state" if level == "state" else "district"
        out = {}
        for name in (a, b):
            rows = q(f"""SELECT count(*) "villages", round(sum(potential_units_yr)) units_per_year,
                                round(sum(headroom)) unserved_implements,
                                round(avg(attach_rate),2) implements_per_tractor,
                                round(avg(dealer_distance_km),1) km_to_dealer,
                                round(avg(avg_holding_ha),2) avg_farm_ha,
                                round(avg(irrigation_ratio)*100) irrigated_pct,
                                mode(archetype) main_village_type,
                                mode(top_sku) top_product
                         FROM village_insights WHERE {col} ILIKE ?""", [f"%{name}%"])
            out[name] = rows[0] if rows and rows[0]["villages"] else {"error": "not found"}
        return out

    def competition(view: str, rival: str | None = None, state: str | None = None,
                    category: str | None = None):
        where, p = ["1=1"], []
        if state:
            where.append("v.state = ?"); p.append(state)
        if category:
            where.append("c.category = ?"); p.append(category)
        w = " AND ".join(where)

        if view == "brands":
            return q("""SELECT player AS brand, round(avg("share")*100,2) AS share_pct,
                               round(avg(price_index),2) AS price_vs_market,
                               round(avg(reach_km)) AS buyer_travels_km
                        FROM player_shares GROUP BY 1 ORDER BY share_pct DESC""")
        if view == "cannibalisation":
            return q("""SELECT name_a AS product_a, name_b AS product_b,
                               shared_job AS same_job,
                               round(overlap*100) AS overlap_pct,
                               round(displaced_units) AS displaced_implements
                        FROM cannibal_int ORDER BY displaced_units DESC""")
        if view == "headtohead":
            if not rival:
                return {"error": "headtohead needs a rival name"}
            return q(f"""SELECT c.category, round(sum(c.market_units)) AS market_implements,
                                round(sum(c.sonalika_units)) AS sonalika_implements,
                                round(sum(c.competitor_units)) AS rival_implements,
                                round(sum(c.winnable_units)) AS winnable_implements
                         FROM cannibal_ext c JOIN geo_villages v USING (village_id)
                         WHERE {w} AND c.closest_rival = ?
                         GROUP BY 1 ORDER BY rival_implements DESC""", p + [rival])
        return q(f"""SELECT c.closest_rival AS rival,
                            round(sum(c.competitor_units)) AS they_hold_implements,
                            round(sum(c.winnable_units)) AS winnable_implements,
                            round(sum(c.at_risk_units)) AS our_implements_at_risk
                     FROM cannibal_ext c JOIN geo_villages v USING (village_id)
                     WHERE {w} AND c.closest_rival != 'Sonalika'
                     GROUP BY 1 ORDER BY they_hold_implements DESC""", p)

    def present(from_step: int, kind: str, title: str | None = None,
                x: str | None = None, y: str | None = None, y2: str | None = None,
                columns: list | None = None, limit: int = 15):
        steps = [h for h in history if isinstance(h["rows"], list) and h["rows"]]
        if not steps:
            return {"error": "nothing to present yet -- run a query tool first"}
        i = int(from_step) - 1
        src = history[i] if 0 <= i < len(history) else steps[-1]
        rows = src["rows"]
        if not isinstance(rows, list) or not rows:
            return {"error": f"step {from_step} returned no rows to present"}

        fields = list(rows[0])
        limit = max(1, min(int(limit or 15), 60))
        data = rows[:limit]

        if kind == "table":
            cols = [c for c in (columns or fields) if c in fields] or fields
            blocks.append({"type": "table", "title": title,
                           "columns": cols,
                           "rows": [{c: r.get(c) for c in cols} for r in data]})
            return {"status": "rendered", "kind": "table", "rows": len(data)}

        # charts need a category and a number; fall back to the obvious choice rather
        # than failing, because a rejected render leaves the user with nothing.
        num = [f for f in fields
               if all(isinstance(r.get(f), (int, float)) and r.get(f) is not None
                      for r in data[:5])]
        cat = [f for f in fields if f not in num]
        xf = x if x in fields else (cat[0] if cat else fields[0])
        yf = y if y in num else (num[0] if num else None)
        if yf is None:
            return {"error": "no numeric field to plot -- use kind=table instead"}
        series = [yf] + ([y2] if y2 in num and y2 != yf else [])

        blocks.append({"type": "chart", "kind": kind, "title": title,
                       "x": xf, "series": series,
                       "data": [{**{xf: r.get(xf)}, **{s_: r.get(s_) for s_ in series}}
                                for r in data]})
        return {"status": "rendered", "kind": kind, "x": xf, "series": series,
                "rows": len(data)}

    def remember(fact: str):
        if not session_id:
            return {"status": "no session -- not stored"}
        from api.memory import store
        return {"status": store.remember(session_id, fact), "fact": fact}

    fns = {f.__name__: f for f in [top_geographies, top_products, village_detail,
                                   find_villages, village_segments, sales_drivers,
                                   data_sources, compare, remember, present,
                                   competition]}

    def execute(name, args):
        if name not in fns:
            raise KeyError(f"unknown tool {name}")
        out = fns[name](**args)
        if name != "present":
            history.append({"tool": name, "input": args, "rows": out})
        return out

    execute.history = history          # type: ignore[attr-defined]
    return execute, fns, blocks


# ---------------------------------------------------------------- entry point

# Chosen to exercise the different answer shapes: a ranking, a comparison, a driver
# breakdown, a targeting list, a provenance question.
SUGGESTIONS = [
    "Show me the top 10 districts for super seeders as a table",
    "Chart demand across the three states",
    "Which 10 villages in Punjab should I visit first?",
    "Why did tractor sales fall in Sangrur?",
    "Which rivals hold the most volume, and what can we win back?",
    "Compare Punjab and Maharashtra",
    "Which villages have demand but no dealer nearby?",
    "How much of this data is real?",
]


def answer(q, question: str, session_id: str | None = None,
           context: dict | None = None) -> dict:
    """Answer one question, with conversation memory.

    The session carries the earlier turns and any durable facts about the user; both are
    folded into the prompt. The turn is recorded either way, so the transcript survives
    a restart and works identically in fallback mode.
    """
    from api.memory import store

    session = store.get(session_id)
    sid = session.session_id
    store.add_turn(sid, "user", question)

    system = SYSTEM + session.fact_block() + _context_block(context)

    execute, _fns, blocks = make_executor(q, sid)

    if llm.available():
        msgs = session.prompt_history()[:-1] + [{"role": "user", "content": question}]
        text, trace = llm.converse(system, msgs, TOOLS, execute)
        mode = llm.provider_name()
        # Lift any table the model wrote as text into a real block before deciding
        # whether anything is missing.
        text, prose_tables = extract_markdown_tables(text)
        blocks.extend(prose_tables)
        _autorender(execute, blocks, question)
    else:
        out = _fallback(question, execute, blocks)
        text, trace, mode = out["answer"], out["trace"], out["mode"]

    store.add_turn(sid, "assistant", text, trace, blocks)
    return {"answer": text, "trace": trace, "blocks": blocks, "mode": mode,
            "session_id": sid, "facts": store.get(sid).facts}


AUTORENDER_SKIP = {"data_sources", "remember", "sales_drivers", "village_detail"}

# Phrases that make the requested form explicit. If the user asked for a table and none
# came back, that is a failure regardless of what the model thought it was doing.
WANTS_TABLE = ("as a table", "in a table", "tabular", "table format", "as table",
               "show me a table", "list them", "in table form")
WANTS_CHART = ("chart", "graph", "plot", "visualise", "visualize", "bar chart",
               "line chart", "pie chart")

_MD_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_MD_SEP = re.compile(r"^[\s|:\-]+$")


def extract_markdown_tables(text: str) -> tuple[str, list[dict]]:
    """Pull any markdown table out of the prose and return it as a real table block.

    The model is told not to write tables in text, and mostly complies -- but only
    mostly. When it does, the pipes collapse into an unreadable run of characters in a
    chat bubble. Rather than rely on the instruction holding every time, any table found
    in the prose is lifted out and rendered properly, and the prose keeps only the words.
    """
    # A model sometimes emits the whole table on one line, with the sentence that
    # introduces it glued to the front. Split it back into rows before parsing,
    # otherwise the pipes survive into the prose as an unreadable run of characters.
    if "|" in text and re.search(r"\|\s*:?-{3,}", text):
        text = re.sub(r"\|\s*\|", "|\n|", text)
        # Split prose off the ends of a row rather than trying to parse sentences:
        # anything before the first pipe, or after the last, is not part of the table.
        def _split_ends(m):
            head, mid, tail = m.group(1), m.group(2), m.group(3)
            parts = [p_ for p_ in (head.strip(), mid, tail.strip()) if p_]
            return "\n".join(parts)
        text = re.sub(r"(?m)^([^|\n]*)(\|.*\|)([^|\n]*)$", _split_ends, text)

    lines = text.splitlines()
    out_lines: list[str] = []
    blocks: list[dict] = []
    i = 0
    while i < len(lines):
        m = _MD_ROW.match(lines[i])
        if not m:
            out_lines.append(lines[i]); i += 1
            continue
        # a table is a header row, a separator row, then at least one body row
        block = []
        j = i
        while j < len(lines) and _MD_ROW.match(lines[j]):
            block.append(lines[j]); j += 1
        if len(block) >= 3 and _MD_SEP.match(block[1].strip().strip("|")):
            cells = [[c.strip() for c in _MD_ROW.match(b).group(1).split("|")]
                     for b in block]
            header = [h for h in cells[0]]
            body = [r for r in cells[2:] if any(c for c in r)]
            width = len(header)
            rows = [{header[k] or f"col{k+1}": (r[k] if k < len(r) else None)
                     for k in range(width)} for r in body]
            if rows:
                blocks.append({"type": "table", "title": None,
                               "columns": [h or f"col{k+1}" for k, h in enumerate(header)],
                               "rows": rows, "from_prose": True})
                i = j
                continue
        out_lines.extend(block); i = j

    clean = "\n".join(out_lines)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, blocks


def _autorender(execute, blocks: list, question: str = "") -> None:


    """Attach a table when the model produced list-shaped data but forgot to present it.

    Prompting alone is not enough -- the model reliably renders when asked directly for a
    table or chart, and intermittently forgets on questions that merely imply one. A
    server-side fallback means a list-shaped answer always gets a readable form, which is
    the whole point of the feature.
    """
    ql = question.lower()
    asked = any(k in ql for k in WANTS_TABLE) or any(k in ql for k in WANTS_CHART)
    if blocks and not (asked and not any(b.get("type") in ("table", "chart")
                                         for b in blocks)):
        return
    if blocks:
        return
    history = getattr(execute, "history", [])
    for h in reversed(history):
        if h["tool"] in AUTORENDER_SKIP:
            continue
        rows = h["rows"]
        if not isinstance(rows, list) or len(rows) < 3 or not isinstance(rows[0], dict):
            continue
        step = history.index(h) + 1
        try:
            execute("present", {"from_step": step, "kind": "table",
                                "title": "Supporting data", "limit": 15})
        except Exception:                                           # noqa: BLE001
            pass
        return


def _context_block(context: dict | None) -> str:
    """What the user is looking at, so answers land in the right frame."""
    if not context:
        return ""
    bits = [f"{k.replace('_', ' ')}: {v}" for k, v in context.items() if v]
    if not bits:
        return ""
    return ("\n\nThe user is currently looking at -- " + "; ".join(bits) +
            ". If their question is ambiguous, assume it refers to this.")


def _sku_catalogue() -> list[dict]:
    from api.main import q as _q
    return _q("SELECT sku_id, name FROM sku_ref")


def _sku_name(sku_id: str) -> str:
    return next((s["name"] for s in _sku_catalogue() if s["sku_id"] == sku_id), sku_id)


def _match_sku(ql: str) -> str | None:
    """Map a product named in plain words to its SKU id.

    Matches against the catalogue's own names rather than a hard-coded list, so it stays
    correct when the SKU catalogue changes. Longest match wins, so "orchard sprayer"
    beats the bare word "sprayer".
    """
    try:
        skus = _sku_catalogue()
        ceilings = {k["id"]: k.get("attach_rate_ceiling", 0) for k in Config.skus()}
        for k in skus:
            k["attach_rate_ceiling"] = ceilings.get(k["sku_id"], 0)
    except Exception:                                               # noqa: BLE001
        return None
    ql = re.sub(r"[^a-z ]", " ", ql)
    ql = re.sub(r"\s+", " ", ql).strip()
    qwords = set(ql.split())

    # Match in BOTH directions. "round baler" is not a substring of the question
    # "top districts for balers", so a one-way containment test misses the obvious
    # product and the user gets a clarifying question instead of an answer.
    cands: list[tuple[float, str, float]] = []
    for s in skus:
        variants = {s["name"].lower(), s["sku_id"].lower().replace("_", " ")}
        for v in variants:
            v = re.sub(r"[^a-z ]", " ", v)
            v = re.sub(r"\s+", " ", v).strip()
            if not v:
                continue
            score = 0.0
            if len(v) > 4 and v in ql:
                score = len(v) * 2.0                    # full product named outright
            else:
                # "tractor", "farm" and friends appear in many product names, so matching
                # on them alone maps "how are tractors selling" to a water tanker.
                vwords = [w for w in v.split()
                          if len(w) > 3 and w not in GENERIC_WORDS]
                if vwords:
                    # singular/plural tolerant word overlap
                    hits = sum(1 for w in vwords
                               if w in qwords or w + "s" in qwords or w.rstrip("s") in qwords)
                    # a single word out of several is usually coincidence
                    if hits and hits / len(vwords) >= 0.5:
                        score = hits / len(vwords) * len(v)
            if score > 0:
                cands.append((score, s["sku_id"], s.get("attach_rate_ceiling", 0)))

    if not cands:
        return None
    top = max(c[0] for c in cands)
    if top < 5:
        return None
    # Where several products match about as well ("trolley" fits two SKUs), an
    # unqualified mention almost always means the mass-market variant, so prefer the one
    # with the wider addressable base rather than whichever name happened to score higher.
    close = [c for c in cands if c[0] >= top * 0.75]
    return max(close, key=lambda c: (c[2], c[0]))[1]


def _fallback(question: str, execute, blocks: list | None = None) -> dict:
    """Keyword intent router, used when no Claude credential is configured.

    Deliberately narrow: it answers the common shapes and says plainly when it cannot,
    rather than guessing and sounding confident.
    """
    ql = question.lower()
    trace = []

    step = {"n": 0}

    def run(tool, **kw):
        trace.append({"tool": tool, "input": kw, "ok": True, "rows": None})
        step["n"] += 1
        return execute(tool, kw)

    def show(kind, **kw):
        """Render the most recent result. Without an LLM to choose a form, the router
        picks one per intent -- a keyword answer should not be visually poorer."""
        if blocks is not None:
            try:
                execute("present", {"from_step": step["n"], "kind": kind, **kw})
            except Exception:                                       # noqa: BLE001
                pass

    state = next((s for s in ["Punjab", "Madhya Pradesh", "Maharashtra"]
                  if s.lower() in ql), None)

    # A product named in plain words ("super seeders", "orchard sprayer") must not fall
    # through to a generic district ranking -- that answer looks confident and is wrong.
    sku_id = _match_sku(ql)
    if sku_id and any(k in ql for k in ["where", "which", "district", "state", "biggest",
                                        "best", "opportunity", "top", "market"]):
        r = run("top_geographies", level="district", sku_id=sku_id, state=state, limit=5)
        if r:
            show("bar", title=f"{_sku_name(sku_id)} — demand by district",
                 x="name", y="units_per_year")
            tot = sum(x["units_per_year"] for x in r[:4])
            return {"answer":
                    f"For {_sku_name(sku_id)}, the strongest districts are " +
                    ", ".join(f"{x['name']} ({x['state']}, {x['units_per_year']:,.0f} units)"
                              for x in r[:4]) +
                    f" — about {tot:,.0f} units a year between them.",
                    "trace": trace, "mode": "fallback"}

    if any(k in ql for k in ["real", "source", "where does", "simulated", "accurate", "trust"]):
        d = run("data_sources")
        acc = d["model_accuracy"]
        return {"answer":
                f"Geography, district and village counts, and the product range are real. "
                f"Implement sales history, the dealer network, competitor share and tractor "
                f"registrations are simulated — {d['why_simulated']} "
                f"The sales model is tested on months it had not seen and is off by about "
                f"{acc['typical_error_pct']}%, against {acc['simple_rule_error_pct']}% for a "
                f"repeat-last-year rule, across {acc['districts_modelled']} districts.",
                "trace": trace, "mode": "fallback"}

    if "compare" in ql or " vs " in ql or " versus " in ql:
        names = re.findall(r"(Punjab|Madhya Pradesh|Maharashtra)", question, re.I)
        if len(names) >= 2:
            r = run("compare", a=names[0], b=names[1], level="state")
            k = list(r)
            x, y = r[k[0]], r[k[1]]
            return {"answer":
                    f"{k[0]}: {x['units_per_year']:,} units a year across "
                    f"{x['villages']:,} villages, {x['implements_per_tractor']} implements "
                    f"per tractor, {x['km_to_dealer']} km to a dealer, "
                    f"{x['irrigated_pct']}% irrigated. "
                    f"{k[1]}: {y['units_per_year']:,} units across {y['villages']:,} villages, "
                    f"{y['implements_per_tractor']} per tractor, {y['km_to_dealer']} km, "
                    f"{y['irrigated_pct']}% irrigated.",
                    "trace": trace, "mode": "fallback"}

    if any(k in ql for k in ["why", "drove", "driver", "fell", "declin", "grew", "growth"]):
        m = re.search(r"in ([A-Z][a-zA-Z ]+)", question)
        if m:
            r = run("sales_drivers", district=m.group(1).strip())
            if "error" not in r:
                w = r["what_drove_it"][:3]
                return {"answer":
                        f"Tractor sales in {r['district']} moved "
                        f"{r['tractor_sales_change_pct_year_on_year']:+.0f}% year on year. "
                        f"The main influences were " +
                        ", ".join(f"{c['driver']} ({c['effect_pp']:+.0f} points)" for c in w) +
                        ".", "trace": trace, "mode": "fallback"}

    if any(k in ql for k in ["convert", "target", "priorit", "which village", "villages"]):
        seg = ("Build access" if any(k in ql for k in ["no dealer", "far", "access"])
               else "Convert now")
        r = run("find_villages", state=state, action_segment=seg, limit=10)
        if r:
            show("table", title=f"Villages to work — {seg}",
                 columns=["village", "district", "opportunity_score_0_100",
                          "unserved_implements", "km_to_dealer", "top_sku"])
            top = r[0]
            return {"answer":
                    f"{len(r)} villages returned under '{seg}'"
                    f"{' in ' + state if state else ''}. The strongest is {top['village']} "
                    f"({top['district']}) — {top['unserved_implements']:.0f} implements "
                    f"unserved, {top['km_to_dealer']:.0f} km to a dealer, "
                    f"best product {top['top_sku']}. "
                    f"See the Village finder for the full list.",
                    "trace": trace, "mode": "fallback"}

    if any(k in ql for k in ["product", "sku", "sell", "implement"]):
        r = run("top_products", state=state, limit=5)
        show("bar", title="Top products by annual demand",
             x="product", y="units_per_year")
        return {"answer":
                f"Top products{' in ' + state if state else ''}: " +
                ", ".join(f"{x['product']} ({x['units_per_year']:,} units)" for x in r[:3]) +
                f". {r[0]['product']} splits {r[0]['new_demand']:,} new against "
                f"{r[0]['replacement_demand']:,} replacement.",
                "trace": trace, "mode": "fallback"}

    if any(k in ql for k in ["type", "segment", "cluster", "archetype"]):
        r = run("village_segments", state=state, detail="archetype")
        show("table", title="Village types")
        return {"answer":
                "Village types by demand: " +
                "; ".join(f"{x['village_type']} ({x['villages']:,} villages, "
                          f"{x['units_per_year']:,} units, mainly '{x['main_action']}')"
                          for x in r[:4]) + ".",
                "trace": trace, "mode": "fallback"}

    r = run("top_geographies", level="district", state=state, limit=5)
    show("bar", title="Biggest districts by demand", x="name", y="units_per_year")
    return {"answer":
            f"Biggest districts by demand{' in ' + state if state else ''}: " +
            ", ".join(f"{x['name']} ({x['units_per_year']:,} units)" for x in r) +
            ". Ask about products, villages to target, what drives sales in a district, "
            "or where the data comes from.",
            "trace": trace, "mode": "fallback"}
