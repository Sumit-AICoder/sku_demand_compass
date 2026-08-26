"""Conversation memory for the chat.

Three distinct things get remembered, and they have different lifetimes on purpose:

  turns    The back-and-forth of one conversation. Lets "what about Maharashtra?"
           resolve against the previous question. Bounded, because an unbounded history
           silently grows the prompt until latency and cost climb for no benefit.

  facts    Durable statements about the USER, not about the data -- "I cover Punjab",
           "we're pushing residue equipment this quarter". These persist across
           conversations and are injected into the system prompt, so the assistant does
           not have to be told twice. The model writes them by calling a `remember`
           tool; it is never asked to infer them silently.

  context  What the user is looking at right now (view, product filter, geography).
           Not memory so much as situational awareness, but it belongs in the same
           envelope because it shapes the same prompt.

Persisted to disk as JSON so memory survives a server restart. Deliberately not a
database: sessions are small, few, and disposable, and a file keeps the whole thing
inspectable and trivially clearable.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field

from pipeline.common import DATA, log

LOG = log("memory")

STORE = DATA / "chat_sessions.json"

MAX_TURNS = 12                 # user+assistant pairs kept in the prompt
MAX_STORED_TURNS = 60          # kept on disk for the transcript view
MAX_FACTS = 25
MAX_SESSIONS = 200
TTL_SECONDS = 14 * 24 * 3600   # a fortnight


@dataclass
class Turn:
    role: str
    text: str
    at: float = field(default_factory=time.time)
    trace: list | None = None
    blocks: list | None = None      # tables/charts rendered with this answer


@dataclass
class Session:
    session_id: str
    turns: list[Turn] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    def prompt_history(self) -> list[dict]:
        """Recent turns as plain role/content pairs for the model."""
        return [{"role": t.role, "content": t.text} for t in self.turns[-MAX_TURNS * 2:]]

    def fact_block(self) -> str:
        if not self.facts:
            return ""
        lines = "\n".join(f"- {f['text']}" for f in self.facts)
        return ("\n\nWhat you already know about this user, from earlier conversations. "
                "Use it to make answers more relevant; do not restate it back at them "
                "unless it matters:\n" + lines)


class Store:
    """Thread-safe session store. The API runs sync endpoints in a threadpool, so every
    mutation takes the lock -- the same lesson as the DuckDB connection."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._load()

    # ---- persistence ----------------------------------------------------

    def _load(self) -> None:
        if not STORE.exists():
            return
        try:
            raw = json.loads(STORE.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            LOG.warning("could not read %s (%s) -- starting empty", STORE.name, exc)
            return
        for sid, s in raw.get("sessions", {}).items():
            self._sessions[sid] = Session(
                session_id=sid,
                turns=[Turn(**t) for t in s.get("turns", [])],
                facts=s.get("facts", []),
                created=s.get("created", time.time()),
                last_seen=s.get("last_seen", time.time()),
            )
        self._evict()
        LOG.info("loaded %d chat sessions", len(self._sessions))

    def _save(self) -> None:
        try:
            STORE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STORE.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "sessions": {sid: {"turns": [asdict(t) for t in s.turns[-MAX_STORED_TURNS:]],
                                   "facts": s.facts, "created": s.created,
                                   "last_seen": s.last_seen}
                             for sid, s in self._sessions.items()}
            }, indent=1))
            tmp.replace(STORE)          # atomic, so a crash mid-write cannot corrupt it
        except OSError as exc:
            LOG.warning("could not persist sessions: %s", exc)

    def _evict(self) -> None:
        now = time.time()
        for sid in [s for s, v in self._sessions.items() if now - v.last_seen > TTL_SECONDS]:
            del self._sessions[sid]
        if len(self._sessions) > MAX_SESSIONS:
            keep = sorted(self._sessions.values(), key=lambda s: -s.last_seen)[:MAX_SESSIONS]
            self._sessions = {s.session_id: s for s in keep}

    # ---- api ------------------------------------------------------------

    def get(self, session_id: str | None) -> Session:
        with self._lock:
            if session_id and session_id in self._sessions:
                s = self._sessions[session_id]
                s.last_seen = time.time()
                return s
            sid = session_id or f"s_{uuid.uuid4().hex[:16]}"
            s = Session(session_id=sid)
            self._sessions[sid] = s
            self._evict()
            return s

    def add_turn(self, session_id: str, role: str, text: str,
                 trace: list | None = None, blocks: list | None = None) -> None:
        with self._lock:
            s = self.get(session_id)
            # copy: the executor keeps mutating its list after the turn is recorded
            s.turns.append(Turn(role=role, text=text, trace=trace,
                                blocks=list(blocks) if blocks else None))
            s.last_seen = time.time()
            self._save()

    def remember(self, session_id: str, text: str) -> str:
        """Store a durable fact, de-duplicating near-identical ones."""
        with self._lock:
            s = self.get(session_id)
            norm = " ".join(text.lower().split())
            for f in s.facts:
                if " ".join(f["text"].lower().split()) == norm:
                    return "already known"
            s.facts.append({"text": text.strip(), "at": time.time()})
            s.facts = s.facts[-MAX_FACTS:]
            self._save()
            LOG.info("remembered for %s: %s", session_id, text[:80])
            return "remembered"

    def forget(self, session_id: str, index: int | None = None) -> None:
        with self._lock:
            s = self.get(session_id)
            if index is None:
                s.facts = []
            elif 0 <= index < len(s.facts):
                s.facts.pop(index)
            self._save()

    def clear_turns(self, session_id: str) -> None:
        """Start a new conversation but keep what we know about the user."""
        with self._lock:
            s = self.get(session_id)
            s.turns = []
            self._save()

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self._save()

    def snapshot(self, session_id: str) -> dict:
        with self._lock:
            s = self.get(session_id)
            return {
                "session_id": s.session_id,
                "facts": s.facts,
                "turns": [{"role": t.role, "text": t.text, "at": t.at,
                           "trace": t.trace, "blocks": t.blocks}
                          for t in s.turns[-MAX_STORED_TURNS:]],
                "created": s.created,
            }


store = Store()
