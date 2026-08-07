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
    item_budget: int


class ContextPolicy:
    TOKEN_BUDGETS = {
        "minimal": 900,
        "operational": 0,
        "operational_context": 500,
        "fallback_standard": 700,
        "entity_lookup": 1600,
        "relational": 3000,
        "complex_task": 6000,
    }
    ITEM_BUDGETS = {
        "minimal": 0, "operational": 0, "operational_context": 4, "fallback_standard": 3,
        "entity_lookup": 5, "relational": 10, "complex_task": 16,
    }
    ACTION_SIGNALS = (
        "crie", "abra", "execute", "rode", "envie", "salve", "escreva", "leia",
        "pesquise", "imprima", "ligue", "desligue", "controle", "gere", "acesse",
        "liste", "verifique",
    )
    OPERATIONAL_NOUNS = (
        "projeto", "projetos", "arquivo", "status", "gemini", "integracao",
        "impressora", "dispositivo", "luz", "diretorio",
    )
    CONTEXT_DEPENDENCIES = (
        "que discutimos", "que falamos", "de ontem", "anterior", "ultimo",
        "aquele", "aquela", "esse", "essa", "ele", "ela",
    )
    DIRECT_TOOL_PHRASES = ("status do", "status da", "liste meus", "liste os", "liste as")
    CURRENT_OPERATIONAL_SIGNALS = (
        "status atual", "esta online", "disponibilidade", "erros recentes",
        "latencia", "conexao", "uso atual",
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
        explicit_tool_phrase = any(phrase in text for phrase in self.DIRECT_TOOL_PHRASES)
        current_operational = any(signal in text for signal in self.CURRENT_OPERATIONAL_SIGNALS)
        operational = current_operational or (
            (actionable or explicit_tool_phrase) and any(
                re.search(rf"\b{re.escape(noun)}\b", text) for noun in self.OPERATIONAL_NOUNS
            )
        )
        context_dependent = any(
            re.search(rf"(?:^|\s){re.escape(marker)}(?:$|\s)", text)
            for marker in self.CONTEXT_DEPENDENCIES
        )
        if operational and context_dependent:
            return self._route("operational_context", .90, "tool_action_needs_context")
        if operational:
            return self._route("operational", .96, "explicit_tool_action")
        if actionable:
            return self._route("complex_task", .92, "action_or_tool_signal")
        if is_greeting and len(text.split()) <= 12:
            return self._route("minimal", .99, "nonsemantic_conversation")
        if entities and intent == "detail":
            return self._route("complex_task", .93, "directed_detail_request")
        if len(entities) > 1 or any(signal in text for signal in self.RELATION_SIGNALS):
            return self._route("relational", .90, "multi_entity_or_relation")
        if entities or intent in {"identity", "personal", "preference", "event", "decision", "plan"}:
            return self._route("entity_lookup", .91, "directed_memory_lookup")
        if any(signal in text for signal in self.COMPLEX_SIGNALS) or len(text.split()) >= 24:
            return self._route("complex_task", .82, "complexity_signal")
        return self._route("fallback_standard", .45, "low_confidence_fallback")

    def _route(self, category: str, confidence: float, reason: str) -> ContextRoute:
        return ContextRoute(
            category=category,
            confidence=confidence,
            reason=reason,
            token_budget=self.TOKEN_BUDGETS[category],
            memory_mode="none" if category in {"minimal", "operational"} else "selective",
            tools_mode=(
                "none" if category == "minimal" else
                "directed" if category in {"entity_lookup", "operational_context", "fallback_standard"} else
                "full"
            ),
            item_budget=self.ITEM_BUDGETS[category],
        )


class ContextBudget:
    """Budget formatted memory by complete records, never by string truncation."""

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return math.ceil(len(text.encode("utf-8")) / 4) if text else 0

    def select(self, items: list[dict], *, max_chars: int, max_items: int | None = None) -> tuple[list[dict], dict]:
        selected, seen_paths, seen_values = [], set(), set()
        removed_duplicates = 0
        used_chars = 0
        for item in items:
            if max_items is not None and len(selected) >= max_items:
                break
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
            "limit_items": max_items,
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
            "candidate_count": item_stats.get("candidate_items", 0),
            "selected_count": item_stats.get("included_items", 0),
            "characters": len(context),
            "estimated_tokens": ContextBudget.estimate_tokens(context),
            "limit_chars": item_stats.get("limit_chars"),
            "limit_items": item_stats.get("limit_items"),
            "removed_duplicates": item_stats.get("removed_duplicates", 0),
            "deferred_items": item_stats.get("deferred_items", 0),
        }],
    }
