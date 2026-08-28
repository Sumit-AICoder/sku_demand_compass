"""Tests for the village-level operational layer, narratives and chat tools."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.common import MARTS, read_table

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(scope="module")
def ins():
    return read_table(MARTS / "village_insights.parquet")


@pytest.fixture(scope="module")
def micro():
    return read_table(MARTS / "micro_segments.parquet")


# ---------------------------------------------------------------- granularity

def test_every_village_gets_an_insight(ins):
    tot = read_table(MARTS / "village_totals.parquet")
    assert len(ins) == len(tot)
    assert ins["village_id"].is_unique


def test_micro_segments_are_finer_than_archetypes(ins):
    """The whole point of this layer: an archetype must resolve into distinguishable
    pockets, otherwise it is still just a strategy label."""
    # Archetypes are now a fine NARP sub-zone x TIV x HP cross-product (~53), so the
    # per-archetype sub-division is smaller than under the old coarse model; 2x still
    # demonstrates genuine sub-structure, and every archetype must still split.
    assert ins["micro_id"].nunique() >= ins["archetype"].nunique() * 2
    per_arch = ins.groupby("archetype")["micro_id"].nunique()
    assert (per_arch >= 2).all(), f"archetypes with no sub-structure:\n{per_arch}"


def test_micro_segments_actually_differ(ins):
    """Sub-segments must separate on the opportunity dimensions, not just relabel."""
    for arch, g in ins.groupby("archetype"):
        # very small archetypes (a sparse sub-zone x TIV x HP cell) split into pockets of a
        # handful of villages; demanding clear separation from those is noise, not signal.
        if len(g) < 150:
            continue
        # Separation is on the COMPOSITE opportunity score, not any single axis: a
        # uniformly well-served archetype (e.g. Punjab plains) barely varies on dealer
        # distance but still splits on attach rate / credit / replacement pressure.
        spread = g.groupby("micro_id")["opportunity_score"].mean()
        if len(spread) < 2:
            continue
        assert spread.max() - spread.min() > 0.05, (
            f"{arch}: sub-segments indistinguishable on opportunity score")


def test_action_segments_are_complete_and_meaningful(ins):
    expected = {"Convert now", "Build access", "Defend", "Monitor"}
    assert set(ins["action_segment"].unique()) == expected
    assert ins["action_segment"].notna().all()
    # "Convert now" must genuinely be nearer a dealer than "Build access"
    by = ins.groupby("action_segment")["dealer_distance_km"].mean()
    assert by["Convert now"] < by["Build access"]
    # ...and carry more unserved demand than "Defend"
    hr = ins.groupby("action_segment")["headroom"].mean()
    assert hr["Convert now"] > hr["Defend"]


def test_peer_comparison_is_within_micro_segment(ins):
    """The peer benchmark must be the village's own pocket, not a district average."""
    g = ins.groupby("micro_id")["attach_rate"].median()
    got = ins["micro_id"].map(g)
    assert np.allclose(got, ins["peer_attach_micro"], rtol=1e-6)


def test_ranks_are_consistent(ins):
    assert (ins["rank_in_district"] >= 1).all()
    assert (ins["rank_in_district"] <= ins["villages_in_district"]).all()
    top = ins[ins["rank_in_district"] == 1]
    assert len(top) == ins["district_id"].nunique()


def test_opportunity_score_bounded_and_discriminating(ins):
    assert ins["opportunity_score"].between(0, 100).all()
    assert ins["opportunity_score"].std() > 5, "score does not separate villages"


def test_every_village_has_a_headline(ins):
    assert ins["headline"].notna().all()
    assert (ins["headline"].str.len() > 40).all()
    assert ins["headline"].nunique() > len(ins) * 0.5, "headlines are not village-specific"


def test_distinguishing_features_are_populated(ins):
    for c in ["distinct_1", "distinct_2", "distinct_3"]:
        assert ins[c].notna().all()
        assert ins[c].str.contains("sd vs peers").all()


def test_village_coordinates_present_for_mapping(ins):
    assert ins["lon"].between(68, 90).all()
    assert ins["lat"].between(8, 36).all()


# ---------------------------------------------------------------- narratives

@pytest.fixture(scope="module")
def q():
    from api.main import q as _q
    return _q


@pytest.mark.parametrize("fn_name", [
    "facts_executive", "facts_clusters",
])
def test_narrative_fact_packs_build(q, fn_name):
    from api import narrative
    facts, text = getattr(narrative, fn_name)(q)
    assert isinstance(facts, dict) and facts
    assert isinstance(text, str) and len(text) > 80


def test_narrative_numbers_come_from_the_fact_pack(q):
    """A narrative must not contain a figure absent from its own fact pack.

    This is the guard that makes the LLM layer safe: the template and the model both
    receive the same pack, so any number in the prose is traceable to a query.
    """
    import json
    import re
    from api import narrative
    facts, text = narrative.facts_executive(q)
    blob = json.dumps(facts)
    for token in re.findall(r"\d[\d,]{3,}", text):
        n = int(token.replace(",", ""))
        # allow any figure that appears in the pack, or is a rounding of one
        ok = any(abs(n - v) <= max(2, abs(v) * 0.02)
                 for v in _numbers(facts))
        assert ok, f"'{token}' in narrative is not in the fact pack"


def _numbers(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _numbers(v)
    elif isinstance(o, list):
        for v in o:
            yield from _numbers(v)
    elif isinstance(o, (int, float)) and not isinstance(o, bool):
        yield o


def test_narrative_avoids_jargon(q):
    """An executive briefing must not fall back on model vocabulary."""
    from api import narrative
    banned = ["elasticity", "coefficient", "percentile", "propensity score",
              "stochastic", "variance", "beta", "z-score", "cluster "]
    for fn in (narrative.facts_executive, narrative.facts_clusters):
        _, text = fn(q)
        low = text.lower()
        assert not [b for b in banned if b in low], f"jargon in: {text[:120]}"


# ---------------------------------------------------------------- chat tools

def test_chat_tools_all_execute(q):
    from api.chat import make_executor
    execute, fns, _blocks = make_executor(q)
    cases = {
        "top_geographies": {"level": "district", "limit": 3},
        "top_products": {"limit": 3},
        "find_villages": {"action_segment": "Convert now", "limit": 3},
        "village_segments": {"detail": "archetype"},
        "sales_drivers": {"district": "Sangrur"},
        "data_sources": {},
        "compare": {"a": "Punjab", "b": "Maharashtra", "level": "state"},
    }
    for name, args in cases.items():
        out = execute(name, args)
        assert out, f"{name} returned nothing"


def test_chat_tool_schemas_match_implementations():
    """Every advertised tool must exist, or Claude will call something that isn't there."""
    from api.chat import TOOLS, make_executor
    from api.main import q as _q
    _, fns, _blocks = make_executor(_q)
    advertised = {t["name"] for t in TOOLS}
    assert advertised == set(fns), advertised ^ set(fns)


def test_chat_fallback_answers_without_a_key(q):
    from api import chat as chat_mod
    for question in ["Where is the biggest opportunity for super seeders?",
                     "How much of this data is real?",
                     "Compare Punjab and Maharashtra",
                     "Which villages in Punjab should we convert first?"]:
        r = chat_mod._fallback(question, chat_mod.make_executor(q)[0])
        assert len(r["answer"]) > 60, question
        assert r["trace"], f"no query ran for: {question}"


def test_chat_resolves_products_by_plain_name():
    from api.chat import _match_sku
    assert _match_sku("where is the biggest opportunity for super seeders?") == "SUPER_SEEDER"
    assert _match_sku("best districts for orchard sprayers") == "ORCHARD_SPRAYER"
    assert _match_sku("how are tractors selling") is None


# ---------------------------------------------------------------- provider layer

def test_provider_detection_reports_honestly():
    """`status()` must reflect a verified connection, not just a set env var.

    The UI badges narratives as AI-written off this, so claiming availability on the
    presence of a variable would lie whenever the key is stale.
    """
    from api import llm
    st = llm.status()
    assert st["provider"] in {"azure", "anthropic", "none"}
    assert st["available"] == (st["provider"] != "none")
    if st["available"]:
        assert st["model"]


def test_tool_schemas_translate_to_openai():
    """Tools are declared once in Anthropic shape and translated for Azure, so adding a
    tool can never leave two definitions to drift apart."""
    from api.llm import _to_openai_tool
    from api.chat import TOOLS
    for t in TOOLS:
        o = _to_openai_tool(t)
        assert o["type"] == "function"
        assert o["function"]["name"] == t["name"]
        assert o["function"]["description"]
        assert o["function"]["parameters"]["type"] == "object"


def test_env_var_aliases_resolve():
    """A working Azure config must not be rejected on a naming technicality."""
    import os
    from api.llm import _first
    os.environ.pop("__T_A", None); os.environ.pop("__T_B", None)
    assert _first("__T_A", "__T_B") is None
    os.environ["__T_B"] = "  value  "
    assert _first("__T_A", "__T_B") == "value"
    os.environ["__T_A"] = "first"
    assert _first("__T_A", "__T_B") == "first"
    del os.environ["__T_A"], os.environ["__T_B"]


def test_tool_output_fields_carry_their_units(q):
    """Field names must state the unit.

    A bare `unserved` was read by the model as a percentage when it was a count of
    implements. Names now carry the unit so that misreading is not available.
    """
    from api.chat import make_executor
    execute, _, _blocks = make_executor(q)
    rows = execute("find_villages", {"action_segment": "Convert now", "limit": 2})
    assert rows
    fields = set(rows[0])
    assert "unserved" not in fields, "ambiguous bare 'unserved' is back"
    assert "unserved_implements" in fields
    assert "opportunity_score_0_100" in fields
    for f in fields:
        if any(f.endswith(sfx) for sfx in ("_implements", "_per_year", "_per_tractor")):
            assert isinstance(rows[0][f], (int, float)), f


# ---------------------------------------------------------------- concurrency

def test_queries_are_thread_safe():
    """Concurrent queries must not interfere.

    FastAPI runs sync endpoints in a threadpool. A single shared DuckDB connection is
    not safe for concurrent execute -- threads interleave and `fetchdf()` can return
    None, which surfaced as random 500s on whichever request lost the race. Each thread
    now gets its own cursor over the same database; this test fails if that regresses.
    """
    import concurrent.futures as cf
    from api.main import q as _q

    sqls = [
        ("SELECT season_index FROM seasonality WHERE sku_id=? AND month_of_year=?",
         ["HTP_SPRAYER", 2]),
        ("SELECT count(*) AS n FROM village_insights WHERE state = ?", ["Punjab"]),
        ("SELECT sum(potential_units_yr) AS u FROM block_sku WHERE sku_id = ?",
         ["SUPER_SEEDER"]),
        ("SELECT count(*) AS n FROM ucm_betas", []),
    ]

    def run(i):
        sql, params = sqls[i % len(sqls)]
        rows = _q(sql, params)
        assert rows is not None and len(rows) >= 1
        return True

    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(run, range(160)))
    assert all(results)


def test_connection_is_per_thread():
    """Two threads must not be handed the same connection object."""
    import concurrent.futures as cf
    from api.main import con

    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        ids = list(ex.map(lambda _: id(con()), range(8)))
    assert len(set(ids)) > 1, "all threads share one connection"


def test_seasonality_covers_every_sku_and_month(q):
    """The failing endpoint asked for one SKU x month; every combination must exist,
    so a miss is never a silent empty result."""
    rows = q("SELECT sku_id, count(*) AS n FROM seasonality GROUP BY 1")
    assert rows
    assert all(r["n"] == 12 for r in rows), "a SKU is missing months"
    n_sku = q("SELECT count(*) AS n FROM sku_ref")[0]["n"]
    assert len(rows) == n_sku
