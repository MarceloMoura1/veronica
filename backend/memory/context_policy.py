"""Deterministic context routing, budgeting and privacy-safe diagnostics."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .entity_resolver import normalize_text


@dataclass(frozen=True)
class ContextRoute:
    category: str
    confidence: float
    reason: str
    token_budget: int
    memory_mode: str
    tools_mode: str


class ContextPolicy:
    TOKEN_BUDGETS = {
        "minimal": 900,
        "entity_lookup": 1600,
        "relational": 3000,
        "complex_task": 6000,
    }
    ACTION_SIGNALS = (
        "crie", "abra", "execute", "rode", "envie", "salve", "escreva", "leia",
        "pesquise", "imprima", "ligue", "desligue", "controle", "gere", "acesse",
    )
    COMPLEX_SIGNALS = (
        "planeje", "analise", "arquitetura", "automatize", "relatorio", "compare",
        "implemente", "investigue", "estrategia", "passo a passo",
    )
    RELATION_SIGNALS = (
        "relacao", "entre", "ambos", "ambas", "impacta", "afeta", "comparacao",
        "nas duas", "nos dois", "multiempresa",
    )

    def classify(self, query: str, *, intent: str, entities: list[dict], is_greeting: bool) -> ContextRoute:
        text = normalize_text(query)
        actionable = any(re.search(rf"\b{re.escape(signal)}\b", text) for signal in self.ACTION_SIGNALS)
        if actionable:
            return self._route("complex_task", .92, "action_or_tool_signal")
        if is_greeting and len(text.split()) <= 12:
            return self._route("minimal", .99, "nonsemantic_conversation")
        if len(entities) > 1 or any(signal in text for signal in self.RELATION_SIGNALS):
            return self._route("relational", .90, "multi_entity_or_relation")
        if entities or intent in {"identity", "personal", "preference", "event", "decision", "plan"}:
            return self._route("entity_lookup", .91, "directed_memory_lookup")
        if any(signal in text for signal in self.COMPLEX_SIGNALS) or len(text.split()) >= 24:
            return self._route("complex_task", .82, "complexity_signal")
        # Unknown requests retain the broadest safe policy; no extra LLM call.
        return self._route("complex_task", .45, "low_confidence_fallback")

    def _route(self, category: str, confidence: float, reason: str) -> ContextRoute:
        return ContextRoute(
            category=category,
            confidence=confidence,
            reason=reason,
            token_budget=self.TOKEN_BUDGETS[category],
            memory_mode="none" if category == "minimal" else "selective",
            tools_mode="none" if category == "minimal" else ("directed" if category == "entity_lookup" else "full"),
        )


class ContextBudget:
    """Budget formatted memory by complete records, never by string truncation."""

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return math.ceil(len(text.encode("utf-8")) / 4) if text else 0

    def select(self, items: list[dict], *, max_chars: int) -> tuple[list[dict], dict]:
        selected, seen_paths, seen_values = [], set(), set()
        removed_duplicates = 0
        used_chars = 0
        for item in items:
            path = (item.get("category"), item.get("entity"), item.get("field"))
            value_key = normalize_text(str(item.get("value", "")))
            if path in seen_paths or (value_key and value_key in seen_values):
                removed_duplicates += 1
                continue
            estimated_chars = len(str(item.get("value", ""))) + sum(len(str(x or "")) for x in path) + 6
            if used_chars + estimated_chars > max_chars:
                continue
            selected.append(item)
            seen_paths.add(path)
            if value_key:
                seen_values.add(value_key)
            used_chars += estimated_chars
        return selected, {
            "candidate_items": len(items),
            "included_items": len(selected),
            "removed_duplicates": removed_duplicates,
            "deferred_items": len(items) - len(selected) - removed_duplicates,
            "limit_chars": max_chars,
        }


def context_diagnostics(route: ContextRoute, context: str, item_stats: dict, *, included: bool) -> dict:
    """Return counts only. Query and memory content are deliberately excluded."""
    return {
        "route": route.category,
        "confidence": route.confidence,
        "route_reason": route.reason,
        "token_budget": route.token_budget,
        "memory_mode": route.memory_mode,
        "tools_mode": route.tools_mode,
        "components": [{
            "name": "retrieved_memory",
            "included": included,
            "reason": "selective_retrieval" if included else "not_required",
            "items": item_stats.get("included_items", 0),
            "characters": len(context),
            "estimated_tokens": ContextBudget.estimate_tokens(context),
            "limit_chars": item_stats.get("limit_chars"),
            "removed_duplicates": item_stats.get("removed_duplicates", 0),
            "deferred_items": item_stats.get("deferred_items", 0),
        }],
    }
