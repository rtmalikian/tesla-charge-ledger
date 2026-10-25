"""Transport-independent service: takes OpenAI-shaped request dicts,
returns OpenAI-shaped response dicts. The HTTP server (server.py) is a
thin wrapper; tests drive this class directly.

Flow for a user message:
  1. Rules engine answers charging-cost questions from the ledger
     (no AI needed — "what did charging cost in September?").
  2. If rules don't match and ai_mode is auto|always → LLM backend with
     the ledger brief as context. The model explains; it never commands
     a car and never invents a cost — totals come from the ledger.
  3. ai_mode=never (or LLM failure) → capability-list answer.
"""
from __future__ import annotations

import itertools
import re
import time

from .embeddings import embed_text
from .ledger import (ledger_brief, month_label, parse_month_query,
                     report_text, split_text, summarize)
from .llm import LlmBackend

CAPABILITY_BLURB = (
    "I keep your Tesla charging-cost ledger: every session priced with "
    "your real time-of-use rates, monthly and per-car reports, and a "
    "home vs Supercharger cost split. "
    "Try: \"what did charging cost in September?\" or "
    "\"home vs Supercharger split?\" "
    "Connect an LLM backend (TCL_AI_BASE_URL) and I can explain the "
    "ledger in free-form words too. I never control a car — I only "
    "record and price charging."
)


class ChargeLedgerService:
    def __init__(self, settings, sessions_provider=None, llm_factory=None):
        self.settings = settings
        self.sessions_provider = sessions_provider or (lambda: [])
        self.llm_factory = llm_factory
        self._ids = itertools.count(1)

    # -- plumbing ------------------------------------------------------
    def _llm(self):
        if self.llm_factory is not None:
            return self.llm_factory()
        if self.settings.ai_mode == "never":
            return None
        if not self.settings.ai_base_url:
            return None
        return LlmBackend(self.settings.ai_base_url, self.settings.ai_api_key,
                          self.settings.ai_model, self.settings.ai_timeout_seconds)

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}-{int(time.time())}-{next(self._ids)}"

    # -- OpenAI surface ------------------------------------------------
    def models_list(self) -> dict:
        return {"object": "list", "data": [{
            "id": self.settings.model_id,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "tesla-charge-ledger",
        }]}

    def chat_completions(self, body: dict) -> dict:
        messages = body.get("messages") or []
        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = str(m.get("content") or "")
                break
        sessions = self.sessions_provider()
        text = self._rules_answer(user_text, sessions)
        if text is None:
            text = self._llm_answer(user_text, sessions)
        return {
            "id": self._next_id("chatcmpl"),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.settings.model_id,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def embeddings(self, body: dict) -> dict:
        raw = body.get("input", "")
        texts = raw if isinstance(raw, list) else [raw]
        data = []
        for i, t in enumerate(texts):
            data.append({
                "object": "embedding",
                "index": i,
                "embedding": embed_text(str(t), self.settings.embedding_dim),
            })
        return {
            "object": "list",
            "data": data,
            "model": self.settings.model_id,
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    # -- internals -----------------------------------------------------
    def _rules_answer(self, user_text: str, sessions: list) -> str | None:
        q = user_text.lower()
        if not sessions:
            return None
        # Per-car filter: a named car narrows every answer below.
        car_sessions = sessions
        for name in {s.vehicle for s in sessions}:
            if name.lower() in q:
                car_sessions = [s for s in sessions if s.vehicle == name]
                break
        if "split" in q or "home vs" in q or "versus" in q:
            return split_text(car_sessions)
        key = parse_month_query(q, sessions)
        if key is not None and ("cost" in q or "spend" in q or "month" in q
                                or "september" in q or "october" in q
                                or "november" in q or "december" in q
                                or re.search(r"\b(20\d{2})\b", q)
                                or re.search(r"\d{4}-\d{1,2}", q)):
            return report_text(key, car_sessions)
        if "total" in q or "all time" in q or "how much" in q:
            s = summarize(car_sessions)
            return (f"All time: {s['sessions']} sessions, {s['kwh']:.1f} kWh, "
                    f"${s['cost']:.2f}.")
        if "most expensive" in q or "biggest session" in q:
            biggest = max(car_sessions, key=lambda s: s.cost_usd or 0)
            return (f"Your priciest session: {biggest.vehicle} at "
                    f"{biggest.location_name or biggest.location_class} on "
                    f"{biggest.start.strftime('%B %d')}, "
                    f"{biggest.kwh_added:.1f} kWh for "
                    f"${(biggest.cost_usd or 0):.2f}.")
        if "cheapest" in q:
            cheapest = min(car_sessions, key=lambda s: s.cost_usd or 0)
            return (f"Your cheapest session: {cheapest.vehicle} at "
                    f"{cheapest.location_name or cheapest.location_class} on "
                    f"{cheapest.start.strftime('%B %d')}, "
                    f"{cheapest.kwh_added:.1f} kWh for "
                    f"${(cheapest.cost_usd or 0):.2f}.")
        return None

    def _llm_answer(self, user_text: str, sessions: list) -> str:
        llm = self._llm()
        if llm is None:
            return CAPABILITY_BLURB
        try:
            return llm.chat(ledger_brief(sessions), user_text)
        except Exception:
            return CAPABILITY_BLURB + " (The AI backend didn't respond just now.)"
