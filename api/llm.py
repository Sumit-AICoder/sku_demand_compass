"""LLM provider layer for narratives and chat.

Supports **Azure OpenAI (GPT-4.1)** and **Anthropic (Claude)**, auto-detected from the
environment, with a deterministic fallback when neither is configured. The rest of the
app never learns which is in use: both providers are reduced to two calls, `complete()`
and `converse()`, and `converse()` returns the same (answer, trace) contract either way.

The design rule survives the provider switch, because it is the reason the layer is
trustworthy: **the model never supplies facts.** Deterministic Python computes a fact
pack from DuckDB and the model only writes prose over it; for chat, the model may only
see data by calling a whitelisted query tool. A wrong number is therefore a bug in a
query, not an invention nobody can trace.

Configuration -- put these in a project-root `.env` (gitignored):

    # Azure OpenAI (preferred when present)
    AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
    AZURE_OPENAI_API_KEY=<key>
    AZURE_OPENAI_DEPLOYMENT=<your gpt-4.1 deployment name>
    AZURE_OPENAI_API_VERSION=2024-10-21

    # or Anthropic
    ANTHROPIC_API_KEY=<key>
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pipeline.common import ROOT, log

LOG = log("llm")

ANTHROPIC_MODEL = "claude-opus-5"
MAX_TOKENS = 4000


# ---------------------------------------------------------------- env loading

def _load_env() -> None:
    """Load a project-root .env once, without overriding real environment variables.

    Also tolerates the file having been left in .pytest_cache/, which is where it
    first appeared -- but warns, because pytest rewrites that directory and a key
    there is both fragile and easy to commit by accident.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root_env = ROOT / ".env"
    if root_env.exists():
        load_dotenv(root_env, override=False)
    stray = ROOT / ".pytest_cache" / ".env"
    if stray.exists() and stray.stat().st_size > 0:
        load_dotenv(stray, override=False)
        if not root_env.exists():
            LOG.warning(".env found only in .pytest_cache/ -- pytest rewrites that "
                        "directory and will delete it. Copy it to %s", root_env)


_load_env()


# ---------------------------------------------------------------- provider detect

class _Provider:
    name = "none"

    def complete(self, system: str, user: str, max_tokens: int) -> str | None:
        return None

    def converse(self, system, messages, tools, execute, max_turns):
        raise RuntimeError("no provider")


class _Azure(_Provider):
    name = "azure"

    def __init__(self, client, deployment: str):
        self.client, self.deployment = client, deployment

    def complete(self, system, user, max_tokens):
        r = self.client.chat.completions.create(
            model=self.deployment, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return (r.choices[0].message.content or "").strip() or None

    def converse(self, system, messages, tools, execute, max_turns):
        """Function-calling loop.

        Same contract as the Anthropic path: the model may only see data by calling a
        query tool, and every call it makes is recorded in the trace so the UI can show
        its working.
        """
        convo = [{"role": "system", "content": system}] + _to_openai_messages(messages)
        oai_tools = [_to_openai_tool(t) for t in tools]
        trace: list[dict] = []

        for _ in range(max_turns):
            r = self.client.chat.completions.create(
                model=self.deployment, max_tokens=MAX_TOKENS,
                messages=convo, tools=oai_tools, tool_choice="auto",
            )
            msg = r.choices[0].message
            calls = msg.tool_calls or []

            if not calls:
                return (msg.content or "").strip(), trace

            convo.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.function.name,
                                             "arguments": c.function.arguments}}
                               for c in calls],
            })
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                    out, ok = execute(c.function.name, args), True
                except Exception as exc:                            # noqa: BLE001
                    args = {}
                    out, ok = f"{type(exc).__name__}: {exc}", False
                trace.append({"tool": c.function.name, "input": args, "ok": ok,
                              "rows": len(out) if isinstance(out, list) else None})
                convo.append({"role": "tool", "tool_call_id": c.id,
                              "content": json.dumps(out, default=str)[:12000]})

        return ("I ran out of steps working that out — try asking something more specific.",
                trace)


class _Anthropic(_Provider):
    name = "anthropic"

    def __init__(self, client):
        self.client = client

    def complete(self, system, user, max_tokens):
        r = self.client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=max_tokens, system=system,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": user}],
        )
        if r.stop_reason == "refusal":
            LOG.warning("refusal: %s", getattr(r.stop_details, "category", None))
            return None
        return "".join(b.text for b in r.content if b.type == "text").strip() or None

    def converse(self, system, messages, tools, execute, max_turns):
        convo = list(messages)
        trace: list[dict] = []
        for _ in range(max_turns):
            r = self.client.messages.create(
                model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS, system=system,
                thinking={"type": "adaptive"}, tools=tools, messages=convo,
            )
            if r.stop_reason == "refusal":
                return "I can't answer that one.", trace
            convo.append({"role": "assistant", "content": r.content})
            if r.stop_reason != "tool_use":
                return "".join(b.text for b in r.content if b.type == "text").strip(), trace

            results = []
            for b in r.content:
                if b.type != "tool_use":
                    continue
                try:
                    out, ok = execute(b.name, b.input), True
                except Exception as exc:                            # noqa: BLE001
                    out, ok = f"{type(exc).__name__}: {exc}", False
                trace.append({"tool": b.name, "input": b.input, "ok": ok,
                              "rows": len(out) if isinstance(out, list) else None})
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": str(out)[:12000], "is_error": not ok})
            convo.append({"role": "user", "content": results})
        return ("I ran out of steps working that out — try asking something more specific.",
                trace)


# ---------------------------------------------------------------- schema bridges

def _to_openai_tool(t: dict) -> dict:
    """Anthropic tool schema -> OpenAI function schema.

    Tools are declared once in Anthropic shape (api/chat.py) and translated here, so
    adding a tool never means maintaining two definitions that can drift apart.
    """
    return {"type": "function",
            "function": {"name": t["name"], "description": t["description"],
                         "parameters": t.get("input_schema",
                                             {"type": "object", "properties": {}})}}


def _to_openai_messages(messages: list[dict]) -> list[dict]:
    """Normalise history to plain OpenAI role/content pairs."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            c = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
        out.append({"role": m["role"], "content": c or ""})
    return out


def _first(*names: str) -> str | None:
    """First non-empty environment variable among several accepted spellings."""
    for n in names:
        v = os.getenv(n)
        if v and v.strip():
            return v.strip()
    return None


# ---------------------------------------------------------------- selection

@lru_cache(maxsize=1)
def _provider() -> _Provider:
    """Pick a provider, verifying it actually answers before claiming availability.

    A liveness probe matters here: the UI badges whether narratives are AI-written, and
    badging on the mere presence of an env var would lie whenever the key is stale.
    """
    # Accept both the AZURE_OPENAI_*-prefixed names and the shorter ones people
    # actually tend to put in a .env, so a working config is not rejected on a
    # naming technicality.
    ep = _first("AZURE_OPENAI_ENDPOINT", "OPENAI_API_BASE", "AZURE_ENDPOINT")
    key = _first("AZURE_OPENAI_API_KEY", "AZURE_API_KEY", "OPENAI_API_KEY")
    dep = _first("AZURE_OPENAI_DEPLOYMENT", "DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT_NAME",
                 "MODEL_NAME")
    if ep and key and dep:
        try:
            from openai import AzureOpenAI
            c = AzureOpenAI(
                azure_endpoint=ep, api_key=key,
                api_version=_first("AZURE_OPENAI_API_VERSION", "API_VERSION")
                or "2024-10-21",
                timeout=60.0, max_retries=2,
            )
            c.chat.completions.create(model=dep, max_tokens=4,
                                      messages=[{"role": "user", "content": "hi"}])
            LOG.info("Azure OpenAI available (deployment '%s')", dep)
            return _Azure(c, dep)
        except Exception as exc:                                    # noqa: BLE001
            LOG.warning("Azure OpenAI configured but unreachable (%s: %s)",
                        type(exc).__name__, str(exc)[:160])
    elif any([ep, key, dep]):
        missing = [n for n, v in [("AZURE_OPENAI_ENDPOINT", ep),
                                  ("AZURE_OPENAI_API_KEY", key),
                                  ("AZURE_OPENAI_DEPLOYMENT / DEPLOYMENT", dep)] if not v]
        LOG.warning("Azure OpenAI partially configured -- missing %s", ", ".join(missing))

    try:
        import anthropic
        c = anthropic.Anthropic()
        c.messages.create(model=ANTHROPIC_MODEL, max_tokens=4,
                          messages=[{"role": "user", "content": "hi"}])
        LOG.info("Anthropic available (%s)", ANTHROPIC_MODEL)
        return _Anthropic(c)
    except Exception:                                               # noqa: BLE001
        pass

    LOG.info("no LLM provider configured -- deterministic narratives and keyword chat")
    return _Provider()


def available() -> bool:
    return _provider().name != "none"


def provider_name() -> str:
    return _provider().name


def status() -> dict:
    """What the UI badge and the Data & method view report."""
    p = _provider()
    return {
        "provider": p.name,
        "available": p.name != "none",
        "model": {"azure": _first("AZURE_OPENAI_DEPLOYMENT", "DEPLOYMENT", "MODEL_NAME"),
                  "anthropic": ANTHROPIC_MODEL}.get(p.name),
        "configured_but_unreachable": bool(
            _first("AZURE_OPENAI_ENDPOINT", "AZURE_ENDPOINT") and p.name != "azure"),
    }


def complete(system: str, user: str, max_tokens: int = MAX_TOKENS) -> str | None:
    try:
        return _provider().complete(system, user, max_tokens)
    except Exception as exc:                                        # noqa: BLE001
        LOG.warning("completion failed (%s)", type(exc).__name__)
        return None


def converse(system: str, messages: list[dict], tools: list[dict],
             execute, max_turns: int = 6) -> tuple[str, list[dict]]:
    return _provider().converse(system, messages, tools, execute, max_turns)
