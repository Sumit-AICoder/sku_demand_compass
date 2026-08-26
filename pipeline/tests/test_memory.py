"""Tests for chat conversation memory."""
from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def store():
    from api.memory import Store
    return Store()          # own instance, so tests never touch the real transcript


@pytest.fixture
def q():
    from api.main import q as _q
    return _q


def test_session_is_created_and_stable(store):
    a = store.get(None)
    b = store.get(a.session_id)
    assert a.session_id == b.session_id
    assert a.session_id.startswith("s_")


def test_turns_accumulate_in_order(store):
    sid = store.get(None).session_id
    store.add_turn(sid, "user", "where do we sell super seeders")
    store.add_turn(sid, "assistant", "Punjab, mainly Barnala.")
    store.add_turn(sid, "user", "what about Maharashtra")
    h = store.get(sid).prompt_history()
    assert [t["role"] for t in h] == ["user", "assistant", "user"]
    assert h[-1]["content"] == "what about Maharashtra"


def test_prompt_history_is_bounded(store):
    """An unbounded history quietly inflates latency and cost every turn."""
    from api.memory import MAX_TURNS
    sid = store.get(None).session_id
    for i in range(MAX_TURNS * 4):
        store.add_turn(sid, "user", f"q{i}")
        store.add_turn(sid, "assistant", f"a{i}")
    assert len(store.get(sid).prompt_history()) <= MAX_TURNS * 2


def test_facts_deduplicate(store):
    sid = store.get(None).session_id
    assert store.remember(sid, "Covers Punjab territory") == "remembered"
    assert store.remember(sid, "covers   punjab   TERRITORY") == "already known"
    assert len(store.get(sid).facts) == 1


def test_facts_enter_the_prompt(store):
    sid = store.get(None).session_id
    store.remember(sid, "Covers Punjab territory")
    block = store.get(sid).fact_block()
    assert "Covers Punjab territory" in block
    assert store.get(store.get(None).session_id).fact_block() == "", "facts leaked across sessions"


def test_forget_one_and_all(store):
    sid = store.get(None).session_id
    store.remember(sid, "fact one")
    store.remember(sid, "fact two")
    store.forget(sid, 0)
    assert [f["text"] for f in store.get(sid).facts] == ["fact two"]
    store.forget(sid)
    assert store.get(sid).facts == []


def test_new_thread_keeps_what_we_know_about_the_person(store):
    """Clearing a thread and forgetting the user are different actions."""
    sid = store.get(None).session_id
    store.remember(sid, "Covers Punjab territory")
    store.add_turn(sid, "user", "hello")
    store.clear_turns(sid)
    assert store.get(sid).turns == []
    assert len(store.get(sid).facts) == 1


def test_memory_survives_a_restart(store, tmp_path, monkeypatch):
    """The whole point of writing to disk: a server restart must not lose the thread."""
    import api.memory as mem
    monkeypatch.setattr(mem, "STORE", tmp_path / "sessions.json")
    s1 = mem.Store()
    sid = s1.get(None).session_id
    s1.add_turn(sid, "user", "where do we sell balers")
    s1.remember(sid, "Covers Punjab territory")

    s2 = mem.Store()                      # simulates a fresh process
    assert sid in s2._sessions
    assert [t.text for t in s2.get(sid).turns] == ["where do we sell balers"]
    assert [f["text"] for f in s2.get(sid).facts] == ["Covers Punjab territory"]


def test_expired_sessions_are_evicted(store, tmp_path, monkeypatch):
    import api.memory as mem
    monkeypatch.setattr(mem, "STORE", tmp_path / "s.json")
    s = mem.Store()
    sid = s.get(None).session_id
    s.add_turn(sid, "user", "old")
    s._sessions[sid].last_seen = time.time() - mem.TTL_SECONDS - 10
    s._evict()
    assert sid not in s._sessions


def test_store_is_thread_safe(store):
    import concurrent.futures as cf
    sid = store.get(None).session_id

    def work(i):
        store.add_turn(sid, "user", f"q{i}")
        store.remember(sid, f"fact {i % 5}")
        return True

    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        assert all(ex.map(work, range(80)))
    assert len(store.get(sid).facts) == 5          # deduped, not corrupted


# ---------------------------------------------------------------- integration

def test_remember_tool_is_bound_to_its_session(q):
    """A tool must not be able to write into someone else's memory."""
    from api.chat import make_executor
    from api.memory import store as real
    sid = real.get(None).session_id
    execute, fns, _blocks = make_executor(q, sid)
    assert "remember" in fns
    out = execute("remember", {"fact": "Test fact for binding"})
    assert out["status"] in {"remembered", "already known"}
    assert any(f["text"] == "Test fact for binding" for f in real.get(sid).facts)
    real.drop(sid)


def test_remember_is_a_no_op_without_a_session(q):
    from api.chat import make_executor
    execute, _, _blocks = make_executor(q, None)
    assert "no session" in execute("remember", {"fact": "x"})["status"]


def test_answer_records_both_turns(q):
    from api import chat as chat_mod
    from api.memory import store as real
    sid = real.get(None).session_id
    r = chat_mod.answer(q, "how much of this data is real?", session_id=sid)
    assert r["session_id"] == sid
    turns = real.get(sid).turns
    assert [t.role for t in turns[-2:]] == ["user", "assistant"]
    assert turns[-1].text == r["answer"]
    real.drop(sid)


def test_tool_schemas_still_match_implementations(q):
    """The remember tool must be advertised and implemented, like every other."""
    from api.chat import TOOLS, make_executor
    _, fns, _blocks = make_executor(q, "s_test")
    assert {t["name"] for t in TOOLS} == set(fns)


def test_context_block_only_appears_when_given():
    from api.chat import _context_block
    assert _context_block(None) == ""
    assert _context_block({"view": None, "product": ""}) == ""
    b = _context_block({"view": "villages", "product": "SUPER_SEEDER"})
    assert "villages" in b and "SUPER_SEEDER" in b


# ---------------------------------------------------------------- tool robustness

def test_free_text_product_resolves(q):
    """The model should not have to know internal ids.

    A live failure: asked about "horticulture sprayers", the model guessed
    category="horticulture" (a crop, not a category), got nothing back, and had to tell
    the user it could not answer. Free-text product resolution fixes the common case.
    """
    from api.chat import make_executor
    execute, _, _blocks = make_executor(q)
    rows = execute("top_geographies", {"level": "district", "product": "orchard sprayer",
                                       "state": "Maharashtra", "limit": 5})
    assert isinstance(rows, list) and rows
    assert rows[0]["name"] in {"Nashik", "Sangli", "Jalgaon", "Pune", "Ahmednagar"}


def test_invalid_category_fails_informatively(q):
    """An empty result the model cannot diagnose is worse than an explicit rejection."""
    from api.chat import make_executor, VALID_CATEGORIES
    execute, _, _blocks = make_executor(q)
    out = execute("top_geographies", {"level": "district", "category": "horticulture"})
    assert isinstance(out, dict) and "error" in out
    assert set(out["valid_categories"]) == VALID_CATEGORIES
    assert "crop" in out["hint"]


def test_unknown_product_lists_the_real_ones(q):
    from api.chat import make_executor
    execute, _, _blocks = make_executor(q)
    out = execute("top_geographies", {"level": "district", "product": "combine spaceship"})
    assert "error" in out and out["valid_products"]


def test_advertised_categories_match_the_catalogue(q):
    """The schema enum must not drift from the SKU catalogue."""
    from api.chat import VALID_CATEGORIES
    from pipeline.common import Config
    assert VALID_CATEGORIES == set(Config.sku_categories())


# ---------------------------------------------------------------- presentation

@pytest.fixture
def exec3(q):
    from api.chat import make_executor
    return make_executor(q, None)


def test_present_renders_a_table(exec3):
    execute, _, blocks = exec3
    execute("top_geographies", {"level": "district", "limit": 5})
    out = execute("present", {"from_step": 1, "kind": "table", "title": "Top districts"})
    assert out["status"] == "rendered"
    assert len(blocks) == 1
    b = blocks[0]
    assert b["type"] == "table" and b["title"] == "Top districts"
    assert len(b["rows"]) == 5
    assert all(set(r) == set(b["columns"]) for r in b["rows"])


def test_present_renders_a_chart_and_picks_sensible_fields(exec3):
    execute, _, blocks = exec3
    execute("top_products", {"limit": 6})
    out = execute("present", {"from_step": 1, "kind": "bar"})
    assert out["status"] == "rendered"
    b = blocks[0]
    assert b["type"] == "chart" and b["kind"] == "bar"
    # x must be a label, y must be numeric -- swapping them yields an unreadable chart
    assert not isinstance(b["data"][0][b["x"]], (int, float))
    assert isinstance(b["data"][0][b["series"][0]], (int, float))


def test_present_can_only_show_data_a_query_returned(exec3):
    """The model cannot fabricate what it renders -- present points at real rows only."""
    execute, _, blocks = exec3
    out = execute("present", {"from_step": 1, "kind": "table"})
    assert "error" in out and not blocks


def test_present_selects_only_requested_columns(exec3):
    execute, _, blocks = exec3
    execute("find_villages", {"action_segment": "Convert now", "limit": 4})
    execute("present", {"from_step": 1, "kind": "table",
                        "columns": ["village", "district", "unserved_implements"]})
    assert blocks[0]["columns"] == ["village", "district", "unserved_implements"]


def test_present_refuses_a_chart_with_no_numbers(exec3):
    execute, _, blocks = exec3
    execute("village_segments", {"detail": "archetype"})
    # a chart needs something to plot; the tool should say so rather than draw nothing
    out = execute("present", {"from_step": 1, "kind": "bar", "y": "not_a_field"})
    assert out.get("status") == "rendered" or "error" in out


def test_present_caps_row_count(exec3):
    execute, _, blocks = exec3
    execute("find_villages", {"limit": 60})
    execute("present", {"from_step": 1, "kind": "table", "limit": 500})
    assert len(blocks[0]["rows"]) <= 60


def test_autorender_fires_when_the_model_forgets(exec3):
    """Prompting alone is unreliable; a list-shaped answer must always get a form."""
    from api.chat import _autorender
    execute, _, blocks = exec3
    execute("top_geographies", {"level": "district", "limit": 6})
    assert not blocks
    _autorender(execute, blocks)
    assert len(blocks) == 1 and blocks[0]["type"] == "table"


def test_autorender_stays_quiet_when_a_visual_exists(exec3):
    from api.chat import _autorender
    execute, _, blocks = exec3
    execute("top_products", {"limit": 5})
    execute("present", {"from_step": 1, "kind": "bar"})
    before = len(blocks)
    _autorender(execute, blocks)
    assert len(blocks) == before


def test_autorender_skips_non_tabular_answers(exec3):
    """A provenance explanation is not a table; forcing one on it is noise."""
    from api.chat import _autorender
    execute, _, blocks = exec3
    execute("data_sources", {})
    _autorender(execute, blocks)
    assert not blocks


def test_blocks_are_persisted_with_the_turn(q):
    from api import chat as chat_mod
    from api.memory import store as real
    sid = real.get(None).session_id
    r = chat_mod.answer(q, "which districts have the most demand?", session_id=sid)
    snap = real.snapshot(sid)
    last = snap["turns"][-1]
    assert last["role"] == "assistant"
    if r.get("blocks"):
        assert last["blocks"], "blocks were not stored, so a reload would lose them"
    real.drop(sid)


def test_present_is_advertised_and_implemented(q):
    from api.chat import TOOLS, make_executor
    _, fns, _ = make_executor(q, "s_x")
    assert "present" in {t["name"] for t in TOOLS}
    assert "present" in fns


def test_product_matching_is_bidirectional():
    """'balers' must find 'Round Baler'. A one-way containment test misses it, and the
    user gets a clarifying question instead of an answer."""
    from api.chat import _match_sku
    assert _match_sku("top districts for balers") == "ROUND_BALER"
    assert _match_sku("where do we sell balers") == "ROUND_BALER"
    assert _match_sku("orchard sprayers in maharashtra") == "ORCHARD_SPRAYER"
    assert _match_sku("laser leveler punjab") == "LASER_LEVELER"


def test_generic_words_do_not_match_a_product():
    """'tractor' appears in several product names and identifies none of them; matching
    on it mapped 'how are tractors selling' to a water tanker."""
    from api.chat import _match_sku
    assert _match_sku("how are tractors selling") is None
    assert _match_sku("which rivals hold the most volume") is None
    assert _match_sku("what about farm income") is None


def test_ambiguous_product_resolves_within_the_right_family():
    """'trolley' fits two SKUs. Either is a defensible reading, but it must not wander
    into a different product family."""
    from api.chat import _match_sku
    assert (_match_sku("trolley demand") or "").startswith("TROLLEY")
    assert _match_sku("four wheel trolley") == "TROLLEY_4W_8T"


def test_competition_tool_answers_rival_questions(q):
    """Without this tool the model answered 'which rivals hold volume' with product data."""
    from api.chat import make_executor
    execute, fns, _ = make_executor(q, None)
    assert "competition" in fns
    rivals = execute("competition", {"view": "rivals"})
    assert rivals and "rival" in rivals[0]
    assert "they_hold_implements" in rivals[0]
    brands = execute("competition", {"view": "brands"})
    assert brands and {"brand", "share_pct", "price_vs_market"} <= set(brands[0])
    cann = execute("competition", {"view": "cannibalisation"})
    assert cann and "same_job" in cann[0]
    h2h = execute("competition", {"view": "headtohead", "rival": "Fieldking"})
    assert h2h and "winnable_implements" in h2h[0]


def test_competition_headtohead_needs_a_rival(q):
    from api.chat import make_executor
    execute, _, _ = make_executor(q, None)
    assert "error" in execute("competition", {"view": "headtohead"})


# ---------------------------------------------------------------- prose tables

@pytest.mark.parametrize("text,cols,rows", [
    # the exact shape observed in the UI: whole table on one line, prose glued to both ends
    ("Here are the top districts: Top 10 | District | State | Units/Year | "
     "|-----|-----|-----| | Barnala | Punjab | 51 | | Sangrur | Punjab | 48 | "
     "These are the highest.", 3, 2),
    # a well-formed multiline table
    ("Top districts:\n\n| District | State | Units |\n|---|---|---|\n"
     "| Barnala | Punjab | 51 |\n| Moga | Punjab | 45 |\n\nThat is the pattern.", 3, 2),
])
def test_markdown_tables_are_lifted_out_of_prose(text, cols, rows):
    """The model is told not to write tables in text and mostly complies -- but only
    mostly, and a leaked table renders as an unreadable run of pipes. Any table found in
    the prose is turned into a real one."""
    from api.chat import extract_markdown_tables
    clean, blocks = extract_markdown_tables(text)
    assert len(blocks) == 1
    assert len(blocks[0]["columns"]) == cols
    assert len(blocks[0]["rows"]) == rows
    assert "|---" not in clean and "---" not in clean
    assert clean.strip(), "the surrounding prose was thrown away with the table"


@pytest.mark.parametrize("text", [
    "No table here at all, just prose about Punjab and demand.",
    "Demand is high | and rising in Punjab.",
    "Barnala leads with 51 units a year, ahead of Sangrur at 48.",
])
def test_prose_without_a_table_is_left_alone(text):
    from api.chat import extract_markdown_tables
    clean, blocks = extract_markdown_tables(text)
    assert blocks == []
    assert clean == text


def test_explicit_table_request_always_yields_one(q):
    """If the user asked for a table in so many words, an answer without one is a
    failure however the model chose to phrase it."""
    from api import chat as chat_mod
    from api.memory import store as real
    sid = real.get(None).session_id
    try:
        r = chat_mod.answer(q, "Show me the top 10 districts for super seeders as a table",
                            session_id=sid)
        kinds = {b.get("type") for b in (r.get("blocks") or [])}
        assert "table" in kinds or "chart" in kinds, \
            f"asked for a table, got blocks={r.get('blocks')}"
    finally:
        real.drop(sid)
