"""Unified context construction for typed and Gemini Live conversations."""

from __future__ import annotations

import json

from .entity_resolver import EntityResolver, normalize_text


class ConversationContextBuilder:
    PROJECT_PRIORITIES = {
        "overview": (
            "name", "canonical_name", "type", "status", "domain", "business_goal",
            "primary_services", "service_model", "manufacturing_model", "core_modules",
            "revenue_model", "founders_context",
        ),
        "operations": (
            "primary_services", "service_model", "manufacturing_model", "service_flow",
            "cad_platform", "render_platform", "own_product_goal", "automation_goal",
            "client_portal_goal", "delivery_goal", "revenue_streams", "ticket_flow",
            "core_modules", "erp_primary", "erp_additional",
        ),
        "goals": (
            "first_year_goal", "business_goal", "success_definition", "automation_goal",
            "architecture_goal", "security_goal", "own_product_goal", "vision_goal",
            "long_term_vision", "personal_expectation", "professional_expectation",
        ),
        "future": (
            "vision", "vision_goal", "long_term_vision", "autonomy_vision", "execution_vision",
            "personal_expectation", "professional_expectation", "future_agents", "memory_goal",
            "voice_goal", "business_goal", "automation_goal", "ai_strategy",
        ),
    }
    INTENT_LIMITS = {
        "identity": 5, "personal": 7, "preference": 5, "overview": 12,
        "standard": 12, "operations": 16, "goals": 14, "relationship": 8,
        "future": 20, "detail": 30,
        "event": 10, "decision": 10, "plan": 10,
    }

    def __init__(self, memory_manager, max_context_chars: int = 7000):
        self.memory = memory_manager
        self.resolver = EntityResolver(memory_manager)
        self.max_context_chars = max_context_chars
        continuity = memory_manager.get_category("continuity")
        last_subject = continuity.get("last_subject")
        self.current_subject = dict(last_subject) if isinstance(last_subject, dict) else None
        self.previous_subjects = []

    def build_context(self, user_text: str, channel: str = "text") -> dict:
        query = (user_text or "").strip()
        intent = self.resolver.resolve_intent(query)
        if intent == "session_resume" and getattr(self, "conversation_state", None):
            return self._session_resume_context(query, channel)
        nonsemantic_greeting = self.resolver.is_nonsemantic_greeting(query)
        resolved = self.resolver.resolve_entity(query)
        prior_subject = self.current_subject
        if not nonsemantic_greeting and resolved is None and prior_subject and self.resolver.is_continuation(query):
            resolved = dict(prior_subject)
        elif resolved:
            if self.current_subject and self.current_subject["name"] != resolved["name"]:
                self.previous_subjects = ([self.current_subject] + self.previous_subjects)[:4]
            self.current_subject = dict(resolved)

        items = []
        if nonsemantic_greeting:
            items = []
        elif resolved:
            items.extend(self._entity_items(resolved, intent))
            if (
                resolved["category"] == "people" and prior_subject
                and prior_subject["category"] == "projects"
                and self.resolver.is_continuation(query)
            ):
                items.extend(self._project_person_items(prior_subject["name"], resolved["name"]))
        else:
            items.extend(self._general_items(query, intent))

        items.extend(self._conversational_items(query, resolved, intent))

        items = self._deduplicate(items)
        context = self._format_context(items)
        entity_name = resolved["name"] if resolved else None
        print(
            f"[MEMORY_CONTEXT] channel={channel} query={query!r} "
            f"entity={entity_name or '-'} intent={intent} items={len(items)}"
        )
        return {
            "query": query,
            "channel": channel,
            "entity": entity_name,
            "intent": intent,
            "items": items,
            "item_count": len(items),
            "context": context,
        }

    def _session_resume_context(self, query, channel):
        state = self.conversation_state.snapshot()
        topic = state.get("last_meaningful_topic") or state.get("active_topic")
        resolved = self.resolver.resolve_entity(topic or "")
        if resolved:
            self.current_subject = dict(resolved)
        restored = self.conversation_state.build_restoration_context(self.memory)
        has_context = bool(state.get("important_turns") or state.get("conversation_summary"))
        prefix = (
            "Session resume context is available. Answer what the prior conversation was about; "
            "never claim that no recent context is saved.\n"
            if has_context else "No prior session context is available.\n"
        )
        context = prefix + restored
        print(
            f"[MEMORY_CONTEXT] channel={channel} query={query!r} entity={topic or '-'} "
            f"intent=session_resume has_context={has_context}"
        )
        return {
            "query": query, "channel": channel, "entity": topic, "intent": "session_resume",
            "items": [], "item_count": 0, "context": context, "has_context": has_context,
        }

    def _entity_items(self, entity, intent):
        category, name = entity["category"], entity["name"]
        if category == "projects":
            data = self.memory.get_project(name) or {}
            return self._project_items(name, data, intent)
        if category == "people":
            data = self.memory.get_person(name) or {}
            items = self._field_items("people", name, data, tuple(data), self.INTENT_LIMITS.get(intent, 8))
            profile = self.memory.get_category("profile")
            for field in ("mother", "father", "siblings", "only_child"):
                value = profile.get(field)
                if value and normalize_text(str(value)) in normalize_text(json.dumps(data, ensure_ascii=False)):
                    items.append(self._item("profile", None, field, value))
            return items
        return self._general_items(name, intent)

    def _project_items(self, name, data, intent):
        limit = self.INTENT_LIMITS.get(intent, 12)
        if intent == "detail":
            ordered = tuple(data)
        else:
            priority = self.PROJECT_PRIORITIES.get(intent, self.PROJECT_PRIORITIES["overview"])
            ordered = priority + tuple(key for key in data if key not in priority)
        items = self._field_items("projects", name, data, ordered, limit)
        prefix = normalize_text(name).replace(" ", "")
        facts = self.memory.get_category("facts")
        for key, value in facts.items():
            if normalize_text(key).replace(" ", "").startswith(prefix):
                items.append(self._item("facts", None, key, value))
                if len(items) >= limit:
                    break
        return items[:limit]

    def _project_person_items(self, project_name, person_name):
        project = self.memory.get_project(project_name) or {}
        person_token = normalize_text(person_name).split()[0]
        return [
            self._item("projects", project_name, key, value)
            for key, value in project.items()
            if person_token in normalize_text(key) or person_token in normalize_text(str(value))
        ][:5]

    def _general_items(self, query, intent):
        normalized = normalize_text(query)
        targeted = []
        field_groups = {
            "siblings": ("sibling", "irmao", "only child", "filho unico"),
            "friends": ("best friend", "melhores amigos", "amigos"),
            "title": ("preferred title", "chamar", "tratamento"),
        }
        selected_group = next(
            (name for name, aliases in field_groups.items() if any(alias in normalized for alias in aliases)),
            None,
        )
        for category in ("profile", "preferences", "facts"):
            for key, value in self.memory.get_category(category).items():
                key_text = normalize_text(key)
                include = False
                if selected_group == "siblings":
                    include = any(token in key_text for token in ("sibling", "only child"))
                elif selected_group == "friends":
                    include = "best friend" in key_text
                elif selected_group == "title":
                    include = "preferred title" in key_text
                if include:
                    targeted.append(self._item(category, None, key, value))
        if targeted:
            return targeted[:self.INTENT_LIMITS.get(intent, 8)]

        results = self.memory.search(query)
        return [
            self._item(item["category"], None, item["key"], item["value"])
            for item in results[:self.INTENT_LIMITS.get(intent, 8)]
        ]

    @staticmethod
    def _field_items(category, entity, data, ordered_keys, limit):
        items = []
        for key in ordered_keys:
            if key in data and data[key] not in (None, "", [], {}):
                items.append(ConversationContextBuilder._item(category, entity, key, data[key]))
                if len(items) >= limit:
                    break
        return items

    def _conversational_items(self, query, resolved, intent):
        entity_name = resolved["name"] if resolved else None
        records = []
        if intent == "event":
            source = self.memory.get_recent_events(limit=20)
        elif intent == "decision":
            source = self.memory.get_recent_decisions(limit=20)
        elif intent == "plan":
            source = self.memory.get_recent_plans(limit=20)
        else:
            return records
        normalized_query = normalize_text(query)
        for record in source:
            related = not entity_name
            if entity_name:
                related = entity_name in record.get("entities", []) or record.get("project") == entity_name
            if not related and entity_name:
                haystack = normalize_text(json.dumps(record, ensure_ascii=False))
                related = normalize_text(entity_name) in haystack
            if related:
                memory_id = record.get("id", "recent")
                value = {key: value for key, value in record.items() if key != "source_turn_normalized"}
                records.append(self._item(intent + "s", memory_id, "record", value))
            if len(records) >= self.INTENT_LIMITS[intent]:
                break
        return records

    @staticmethod
    def _item(category, entity, field, value):
        return {"category": category, "entity": entity, "field": field, "value": value}

    @staticmethod
    def _deduplicate(items):
        seen, result = set(), []
        for item in items:
            identity = (item["category"], item["entity"], item["field"])
            if identity not in seen:
                seen.add(identity)
                result.append(item)
        return result

    def _format_context(self, items):
        if not items:
            return ""
        lines = [
            "Persistent facts relevant to the user's request:",
            "Use these as authoritative user facts. Answer naturally without mentioning memory retrieval.",
            "If the requested fact is absent, say you do not have that information; do not invent it.",
        ]
        for item in items:
            path = ".".join(str(part) for part in (item["category"], item["entity"], item["field"]) if part)
            value = json.dumps(item["value"], ensure_ascii=False) if isinstance(item["value"], (dict, list)) else str(item["value"])
            candidate = f"- {path}: {value}"
            if len("\n".join(lines + [candidate])) > self.max_context_chars:
                break
            lines.append(candidate)
        return "\n".join(lines)
