"""Deterministic global-memory ranking and cross-domain context selection."""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timedelta, timezone

from .entity_resolver import normalize_text


class MemoryIntelligence:
    INTENT_CATEGORIES = {
        "decision": {"decisions"}, "plan": {"plans"}, "event": {"events"},
        "identity": {"people", "profile"}, "personal": {"people", "profile"},
        "preference": {"preferences"}, "operations": {"projects"},
        "overview": {"projects", "people"}, "goals": {"projects", "plans"},
    }
    IMPORTANCE_SCORES = {"low": 0.2, "medium": 0.7, "high": 1.4, "critical": 2.0}
    ACTIVE_PLAN_STATUSES = {"planned", "tentative"}

    def __init__(self, memory_manager, resolver, conversation_state=None, now_fn=None):
        self.memory = memory_manager
        self.resolver = resolver
        self.conversation_state = conversation_state
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def search_global(self, query, entities=None, intent=None, time_filter=None, max_items=16):
        started = time.perf_counter()
        entities = entities if entities is not None else self.resolver.resolve_entities(query)
        intent = intent or self.resolver.resolve_intent(query)
        time_filter = time_filter or self.resolve_time_filter(query)
        query_terms = self.memory._terms(query)
        entity_names = [entity["name"] if isinstance(entity, dict) else str(entity) for entity in entities]
        candidates = []
        for category in ("profile", "preferences", "facts"):
            for key, value in self.memory.get_category(category).items():
                candidates.append(self._candidate(category, None, key, value))
        for category in ("people", "projects"):
            for entity, data in self.memory.get_category(category).items():
                if not isinstance(data, dict):
                    candidates.append(self._candidate(category, entity, "record", data))
                    continue
                for field, value in data.items():
                    if value not in (None, "", [], {}):
                        candidates.append(self._candidate(category, entity, field, value, metadata=data))
        for category in ("events", "decisions", "plans"):
            for memory_id, record in self.memory.get_category(category).items():
                if not isinstance(record, dict):
                    continue
                candidate_entity_names = list(record.get("entities") or [])
                if record.get("project"):
                    candidate_entity_names.append(record["project"])
                candidate = self._candidate(
                    category, candidate_entity_names[0] if candidate_entity_names else None,
                    memory_id, {key: value for key, value in record.items() if key != "source_turn_normalized"},
                    metadata=record,
                )
                candidate["entities"] = candidate_entity_names
                candidates.append(candidate)
        if self.conversation_state:
            state = self.conversation_state.snapshot()
            if state.get("conversation_summary"):
                candidates.append(self._candidate(
                    "conversation_state", state.get("active_topic"), "conversation_summary",
                    state["conversation_summary"], metadata=state,
                ))

        ranked = []
        for candidate in candidates:
            if not self._eligible(candidate, query, intent, time_filter):
                continue
            score, reasons = self._score(candidate, query_terms, entity_names, intent)
            if score <= 0 and entity_names:
                continue
            candidate.update(score=round(score, 3), reasons=reasons)
            ranked.append(candidate)
        ranked.sort(key=lambda item: (-item["score"], self._timestamp(item), item["category"], item["field"]), reverse=False)
        ranked = self._semantic_deduplicate(ranked)
        selected = self._fair_select(ranked, entity_names, max_items)
        elapsed = (time.perf_counter() - started) * 1000
        print(
            f"[MEMORY_INTELLIGENCE] query={query!r} entities={entity_names} intent={intent} "
            f"selected={len(selected)} ranking_ms={elapsed:.2f}"
        )
        return {
            "query": query, "entities": entity_names, "intent": intent,
            "time_filter": time_filter, "selected_memories": selected,
            "ranking_time_ms": round(elapsed, 3),
        }

    def _candidate(self, category, entity, field, value, metadata=None):
        metadata = metadata or {}
        return {
            "category": category, "entity": entity, "entities": [entity] if entity else [],
            "field": field, "value": value, "timestamp": (
                metadata.get("updated_at") or metadata.get("recorded_at") or metadata.get("occurred_at")
            ),
            "status": metadata.get("status"), "confidence": metadata.get("confidence"),
            "importance": metadata.get("importance"), "metadata": metadata,
        }

    def _score(self, candidate, query_terms, entity_names, intent):
        text = f"{candidate['entity'] or ''} {candidate['field']} {json.dumps(candidate['value'], ensure_ascii=False)}"
        candidate_terms = self.memory._terms(text)
        overlap = len(query_terms & candidate_terms)
        score, reasons = overlap * 1.5, []
        if overlap:
            reasons.append("relevance")
        matches = [name for name in entity_names if normalize_text(name) in normalize_text(text)]
        if matches:
            score += 5.0
            reasons.append("entity")
        if candidate["category"] in self.INTENT_CATEGORIES.get(intent, set()):
            score += 3.0
            reasons.append("intent")
        recency = self._recency_score(candidate.get("timestamp"))
        if recency:
            score += recency
            reasons.append("recency")
        importance = self._importance_score(candidate)
        if importance:
            score += importance
            reasons.append("importance")
        confidence = candidate.get("confidence")
        if isinstance(confidence, (int, float)):
            score += max(0.0, min(1.5, confidence * 1.5))
            reasons.append("confidence")
        if len(matches) > 1 or self._relationship_match(candidate, entity_names):
            score += 1.5
            reasons.append("relationship")
        status = candidate.get("status")
        if status == "active" or status in self.ACTIVE_PLAN_STATUSES:
            score += 1.0
            reasons.append("active")
        elif status in {"superseded", "cancelled", "completed", "historical"}:
            score -= 3.0
        return score, reasons

    def _eligible(self, candidate, query, intent, time_filter):
        normalized = normalize_text(query)
        status = candidate.get("status")
        if candidate["category"] == "conversation_state" and intent in {"event", "decision", "plan"}:
            return False
        if candidate["category"] == "plans" and ("ativos" in normalized or "pendente" in normalized):
            if status not in self.ACTIVE_PLAN_STATUSES:
                return False
        if candidate["category"] == "decisions" and status == "superseded":
            if not any(marker in normalized for marker in ("antes", "mudei", "mudou", "histor", "superseded")):
                return False
        if time_filter and candidate["category"] in {"events", "decisions", "plans"}:
            return self._matches_time(candidate.get("timestamp"), time_filter)
        return True

    def resolve_time_filter(self, query):
        normalized = normalize_text(query)
        match = re.search(r"ultimos? (\d+) dias", normalized)
        if match:
            return {"kind": "last_n_days", "days": int(match.group(1))}
        for phrase, kind in (
            ("hoje", "today"), ("ontem", "yesterday"), ("essa semana", "this_week"),
            ("esta semana", "this_week"), ("semana passada", "last_week"),
            ("esse mes", "this_month"), ("este mes", "this_month"),
            ("recentemente", "recent"), ("recentes", "recent"), ("recente", "recent"),
        ):
            if phrase in normalized:
                return {"kind": kind}
        return None

    def _matches_time(self, timestamp, time_filter):
        moment = self._parse_time(timestamp)
        if not moment:
            return False
        now, kind = self.now_fn(), time_filter["kind"]
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        date, today = moment.date(), now.date()
        if kind == "today": return date == today
        if kind == "yesterday": return date == today - timedelta(days=1)
        if kind == "recent": return moment >= now - timedelta(days=30)
        if kind == "last_n_days": return moment >= now - timedelta(days=time_filter["days"])
        week_start = today - timedelta(days=today.weekday())
        if kind == "this_week": return date >= week_start
        if kind == "last_week": return week_start - timedelta(days=7) <= date < week_start
        if kind == "this_month": return date.year == today.year and date.month == today.month
        return True

    def _recency_score(self, timestamp):
        moment = self._parse_time(timestamp)
        if not moment:
            return 0.0
        now = self.now_fn()
        if now.tzinfo is None: now = now.replace(tzinfo=timezone.utc)
        days = max(0.0, (now - moment).total_seconds() / 86400)
        return 2.0 / (1.0 + math.log1p(days))

    @staticmethod
    def _parse_time(timestamp):
        if not timestamp:
            return None
        try:
            value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _importance_score(self, candidate):
        importance = candidate.get("importance")
        if isinstance(importance, (int, float)):
            return max(0.0, min(2.0, float(importance)))
        if isinstance(importance, str):
            normalized = normalize_text(importance)
            for label, score in self.IMPORTANCE_SCORES.items():
                if label in normalized:
                    return score
            if any(marker in normalized for marker in ("muito alta", "alta", "essencial")):
                return 1.4
        relationship = normalize_text(str(candidate.get("metadata", {}).get("relationship", "")))
        return 1.0 if any(marker in relationship for marker in ("mae", "pai", "melhor amigo", "socio")) else 0.0

    @staticmethod
    def _relationship_match(candidate, entity_names):
        text = normalize_text(json.dumps(candidate.get("metadata", {}), ensure_ascii=False))
        return sum(normalize_text(name) in text for name in entity_names) > 1

    @staticmethod
    def _timestamp(candidate):
        return candidate.get("timestamp") or ""

    def _semantic_deduplicate(self, ranked):
        seen, term_sets, result = set(), [], []
        for item in ranked:
            value = item["value"]
            if isinstance(value, dict):
                semantic_value = " ".join(str(value.get(key, "")) for key in (
                    "summary", "decision", "details", "project", "entities", "subject"
                ))
            else:
                semantic_value = str(value)
            normalized = normalize_text(semantic_value)
            identity = (item["category"], item.get("entity"), normalized)
            if identity in seen:
                continue
            terms = {
                re.sub(r"(amos|emos|imos|mos|ando|endo|indo)$", "", term)
                for term in self.memory._terms(normalized)
            }
            duplicate = False
            for prior_category, prior_entity, prior_terms in term_sets:
                if (prior_category, prior_entity) != (item["category"], item.get("entity")):
                    continue
                union = terms | prior_terms
                similarity = len(terms & prior_terms) / len(union) if union else 1.0
                if similarity >= 0.65:
                    duplicate = True
                    break
            if duplicate:
                continue
            seen.add(identity)
            term_sets.append((item["category"], item.get("entity"), terms))
            result.append(item)
        return result

    @staticmethod
    def _fair_select(ranked, entity_names, max_items):
        selected, used = [], set()
        for entity in entity_names:
            for index, item in enumerate(ranked):
                if index in used:
                    continue
                haystack = normalize_text(
                    f"{item.get('entity') or ''} {' '.join(item.get('entities', []))} "
                    f"{json.dumps(item.get('value'), ensure_ascii=False)}"
                )
                if normalize_text(entity) in haystack:
                    selected.append(item); used.add(index)
                    if sum(1 for chosen in selected if normalize_text(entity) in normalize_text(
                        f"{chosen.get('entity') or ''} {json.dumps(chosen.get('value'), ensure_ascii=False)}"
                    )) >= max(2, max_items // max(1, len(entity_names))):
                        break
        for index, item in enumerate(ranked):
            if len(selected) >= max_items:
                break
            if index not in used:
                selected.append(item); used.add(index)
        return selected[:max_items]
