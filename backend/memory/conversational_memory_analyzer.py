"""Selective, deterministic learning from completed conversation turns."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone

from .entity_resolver import normalize_text
from .conversation_state_manager import ConversationStateManager


class ConversationalMemoryAnalyzer:
    IGNORE_PHRASES = {
        "oi", "ola", "kkkk", "kkk", "beleza", "sim", "nao", "entendi",
        "pode continuar", "espera ai", "vou pegar agua", "que calor", "esta calor",
    }
    UNCERTAINTY_MARKERS = ("talvez", "acho que", "pode ser que", "estou pensando em")
    DECISION_MARKERS = ("decidimos", "decidi", "foi decidido", "definimos", "vamos usar")
    PLAN_MARKERS = ("amanha", "planejo", "pretendo", "vou trabalhar", "quero trabalhar")
    PLAN_COMPLETION_MARKERS = ("ja terminei", "terminei", "conclui", "finalizei", "esta pronto", "esta pronta")
    EVENT_MARKERS = (
        "aconteceu", "machuc", "hospital", "caiu", "doente", "acidente",
        "comecou a trabalhar", "viajou", "casou", "nasceu", "demit",
    )
    UPDATE_MARKERS = (
        "ja esta", "esta melhor", "esta pior", "foi ao hospital",
        "precisou ir", "machucou", "nao vai mais", "deixa para",
    )

    def __init__(self, memory_manager, context_builder, now_fn=None, conversation_state=None):
        self.memory = memory_manager
        self.context_builder = context_builder
        self.resolver = context_builder.resolver
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.conversation_state = conversation_state or ConversationStateManager(memory_manager.storage_dir, self.now_fn)
        self.context_builder.conversation_state = self.conversation_state
        self.context_builder.memory_intelligence.conversation_state = self.conversation_state
        restored_entities = [
            self.resolver.resolve_entity(name)
            for name in self.conversation_state.snapshot().get("active_entities", [])
        ]
        self.context_builder.current_entities = [entity for entity in restored_entities if entity]
        if len(self.context_builder.current_entities) >= 2:
            self.context_builder.last_multi_entities = [
                dict(entity) for entity in self.context_builder.current_entities
            ]
            self.context_builder.recent_entity_groups = [[
                dict(entity) for entity in self.context_builder.current_entities
            ]]

    def process_conversation_turn(self, user_text: str, channel: str, conversation_context=None) -> dict:
        text = (user_text or "").strip()
        normalized = normalize_text(text)
        result = {"classification": "ignore", "action": "ignored", "confidence": 0.0}
        if not normalized or normalized in self.IGNORE_PHRASES or len(normalized) < 4:
            return self._finish_turn(text, channel, result, None, reason="low_future_value")
        if self.resolver.is_nonsemantic_greeting(text):
            return self._finish_turn(text, channel, result, None, reason="greeting", interaction_type="greeting")

        turn_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        continuity = self.memory.get_category("continuity")
        processed = list(continuity.get("processed_turn_hashes", []))
        if turn_hash in processed:
            return self._finish_turn(text, channel, {**result, "action": "duplicate"}, None, reason="duplicate_turn")

        explicit = re.search(r"\b(?:lembre|memorize|guarde)(?:-se)?\b|\bme cham(?:a|e) de\b", normalized)
        if explicit:
            captured = self.memory.capture_explicit_memory(text)
            if not captured:
                explicit_key = f"explicit_{turn_hash[:16]}"
                self.memory.save_fact(explicit_key, text)
                captured = {"category": "facts", "key": explicit_key, "value": text}
            result = {
                "classification": "preference" if captured["category"] == "preferences" else "fact",
                "action": "created", "confidence": 0.99, "memory_id": captured.get("key"),
            }
            self._remember_turn(turn_hash, result["memory_id"], None)
            return self._finish_turn(text, channel, result, None)

        if self._looks_like_question(text, normalized):
            subject = self.resolver.resolve_entity(text) or self.context_builder.current_subject
            return self._finish_turn(text, channel, result, subject, reason="question_not_memory")

        entities = self.resolver.resolve_entities(text)
        entity = entities[0] if entities else None
        focus_entity = next((item for item in reversed(entities) if item["category"] == "projects"), entity)
        if len(entities) > 1:
            self.context_builder.current_entities = [dict(item) for item in entities]
            self.conversation_state.record_active_entities(
                entities, focus=focus_entity["name"] if focus_entity else None
            )
        if focus_entity:
            self.context_builder.current_subject = dict(focus_entity)
        subject = focus_entity or self.context_builder.current_subject
        uncertain = any(marker in normalized for marker in self.UNCERTAINTY_MARKERS)

        if any(marker in normalized for marker in self.PLAN_COMPLETION_MARKERS):
            result = self._update_plan_status(text, subject, "completed")
        elif self._is_plan_cancellation(normalized):
            result = self._update_plan(text, normalized, subject)
        elif any(marker in normalized for marker in self.DECISION_MARKERS) and not uncertain:
            decision_entity = next((item for item in entities if item["category"] == "projects"), entity or subject)
            result = self._create_decision(text, normalized, decision_entity, entities)
        elif any(marker in normalized for marker in self.PLAN_MARKERS):
            result = self._create_plan(text, normalized, entity or subject, tentative=uncertain)
        elif re.search(r"\b(?:agora\s+)?(?:eu\s+)?prefiro\b", normalized):
            result = self._save_preference(text, normalized)
        elif self._negates_event(normalized):
            result = {"classification": "ignore", "action": "ignored", "confidence": 0.95}
        elif self._should_update_event(normalized, subject):
            result = self._update_recent_event(text, normalized, subject)
        elif entity and any(marker in normalized for marker in self.EVENT_MARKERS):
            event_entity = next((item for item in entities if item["category"] == "people"), entity)
            result = self._create_event(text, normalized, event_entity, channel, entities)
        elif uncertain and entity:
            result = self._create_plan(text, normalized, entity or subject, tentative=True)
        elif entity and self._is_relationship_fact(normalized):
            result = self._save_relationship_fact(text, normalized, entity)
        else:
            return self._finish_turn(text, channel, result, subject, reason="low_future_value")

        if result.get("action") not in {"ignored", "duplicate"}:
            self._remember_turn(turn_hash, result.get("memory_id"), subject or entity)
        return self._finish_turn(text, channel, result, subject or entity)

    def record_assistant_turn(self, text):
        self.conversation_state.record_assistant_turn(text)

    def build_cold_start_context(self):
        return self.conversation_state.build_restoration_context(self.memory)

    def _finish_turn(self, text, channel, result, subject, reason=None, interaction_type=None):
        self.conversation_state.record_user_turn(
            text, result.get("classification", "ignore"), subject, result.get("memory_id"), interaction_type
        )
        return self._log(channel, result, reason=reason)

    def _create_event(self, text, normalized, entity, channel, resolved_entities=None):
        now = self.now_fn()
        existing = self._find_duplicate("events", normalized, entity["name"])
        if existing:
            return {"classification": "event", "action": "duplicate", "confidence": 0.92, "memory_id": existing["id"]}
        memory_id = uuid.uuid4().hex
        time_reference, occurred_at = self._resolve_time(normalized, now)
        event_type = "injury" if any(token in normalized for token in ("machuc", "hospital", "acidente")) else "life_event"
        entity_names = [
            item["name"] for item in (resolved_entities or [entity])
            if item["category"] in {"people", "projects", "profile"}
        ]
        record = {
            "id": memory_id,
            "entities": entity_names,
            "event_type": event_type,
            "summary": text,
            "details": [],
            "time_reference": time_reference,
            "recorded_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "status": "recovering" if "melhor" in normalized else "active",
            "source": channel,
            "confidence": 0.92,
            "importance": self._entity_importance(entity["name"]),
            "relationships": [{"type": "related_to", "entity": name} for name in entity_names],
            "source_turn_normalized": normalized,
        }
        if occurred_at:
            record["occurred_at"] = occurred_at
        self.memory.save_memory_record("events", memory_id, record)
        return {"classification": "event", "action": "created", "confidence": 0.92, "memory_id": memory_id, "entities": entity_names}

    def _update_recent_event(self, text, normalized, subject):
        entity_name = subject["name"] if subject else None
        recent = self._recent_related_event(entity_name)
        if not recent:
            return {"classification": "ignore", "action": "ignored", "confidence": 0.4}
        record = dict(recent)
        memory_id = record.pop("id")
        details = list(record.get("details", []))
        if normalized not in {normalize_text(item) for item in details}:
            details.append(text)
        record["details"] = details
        record["updated_at"] = self.now_fn().isoformat()
        if any(marker in normalized for marker in ("melhor", "bem agora", "recuper")):
            record["status"] = "recovering"
        elif "pior" in normalized:
            record["status"] = "worsened"
        self.memory.save_memory_record("events", memory_id, record)
        return {"classification": "update", "action": "updated", "confidence": 0.9, "memory_id": memory_id, "entities": record.get("entities", [])}

    def _create_decision(self, text, normalized, entity, resolved_entities=None):
        now = self.now_fn().isoformat()
        project = entity["name"] if entity and entity["category"] == "projects" else None
        participants = self._mentioned_people(text)
        if re.search(r"\beu\b|\bnos\b", normalized) and "Marcelo" not in participants:
            participants.insert(0, "Marcelo")
        memory_id = uuid.uuid4().hex
        subject = self._decision_subject(normalized)
        record = {
            "id": memory_id, "project": project, "subject": subject,
            "decision": text, "participants": participants, "status": "active",
            "recorded_at": now, "updated_at": now, "confidence": 0.94,
            "importance": "high",
            "relationships": (
                ([{"type": "related_to", "entity": project}] if project else [])
                + [{"type": "participant", "entity": participant} for participant in participants]
            ),
            "source_turn_normalized": normalized,
        }
        duplicate = self._find_duplicate("decisions", normalized, project)
        if duplicate:
            return {"classification": "decision", "action": "duplicate", "confidence": 0.94, "memory_id": duplicate["id"]}
        superseded = self._supersede_related_decisions(project, subject, normalized, memory_id)
        if superseded:
            record["supersedes"] = superseded
        self.memory.save_memory_record("decisions", memory_id, record)
        return {"classification": "decision", "action": "created", "confidence": 0.94, "memory_id": memory_id, "entities": [project] if project else []}

    def _create_plan(self, text, normalized, entity, tentative=False):
        now = self.now_fn()
        memory_id = uuid.uuid4().hex
        time_reference, _ = self._resolve_time(normalized, now)
        project = entity["name"] if entity and entity["category"] == "projects" else None
        record = {
            "id": memory_id, "project": project, "summary": text,
            "time_reference": time_reference, "status": "tentative" if tentative else "planned",
            "recorded_at": now.isoformat(), "updated_at": now.isoformat(),
            "confidence": 0.5 if tentative else 0.88, "source_turn_normalized": normalized,
            "importance": "medium",
            "relationships": ([{"type": "related_to", "entity": project}] if project else []),
        }
        duplicate = self._find_duplicate("plans", normalized, project)
        if duplicate:
            return {"classification": "plan", "action": "duplicate", "confidence": record["confidence"], "memory_id": duplicate["id"]}
        self.memory.save_memory_record("plans", memory_id, record)
        return {"classification": "plan", "action": "created", "confidence": record["confidence"], "memory_id": memory_id, "entities": [project] if project else []}

    def _update_plan(self, text, normalized, subject):
        active = self.memory.get_active_plans(limit=10)
        if subject:
            related = [plan for plan in active if plan.get("project") == subject["name"]]
            active = related or active
        if not active:
            return {"classification": "ignore", "action": "ignored", "confidence": 0.5}
        plan = dict(active[0])
        memory_id = plan.pop("id")
        plan.setdefault("history", []).append({"text": text, "at": self.now_fn().isoformat()})
        plan["updated_at"] = self.now_fn().isoformat()
        new_reference, _ = self._resolve_time(normalized, self.now_fn(), exclude="amanha")
        if "deixa para" in normalized and new_reference:
            plan["time_reference"] = new_reference
            plan["status"] = "planned"
            action = "rescheduled"
        else:
            plan["status"] = "cancelled"
            action = "cancelled"
        self.memory.save_memory_record("plans", memory_id, plan)
        return {"classification": "update", "action": action, "confidence": 0.93, "memory_id": memory_id}

    def _update_plan_status(self, text, subject, status):
        active = self.memory.get_active_plans(limit=20)
        if subject:
            related = [plan for plan in active if plan.get("project") == subject["name"]]
            active = related or active
        if not active:
            return {"classification": "ignore", "action": "ignored", "confidence": 0.5}
        plan = dict(active[0])
        memory_id = plan.pop("id")
        plan.setdefault("history", []).append({"text": text, "at": self.now_fn().isoformat()})
        plan["status"] = status
        plan["updated_at"] = self.now_fn().isoformat()
        self.memory.save_memory_record("plans", memory_id, plan)
        return {"classification": "update", "action": status, "confidence": 0.95, "memory_id": memory_id}

    def _supersede_related_decisions(self, project, subject, normalized, replacement_id):
        active = [
            item for item in self.memory.get_recent_decisions(limit=50)
            if item.get("status", "active") == "active" and item.get("project") == project
        ]
        change = any(marker in normalized for marker in ("mudar para", "mudamos para", "alterar para", "agora sera"))
        related = [
            item for item in active
            if change or item.get("subject") == subject
        ]
        superseded = []
        for item in related:
            old = dict(item)
            memory_id = old.pop("id")
            old["status"] = "superseded"
            old["superseded_by"] = replacement_id
            old["updated_at"] = self.now_fn().isoformat()
            self.memory.save_memory_record("decisions", memory_id, old)
            superseded.append(memory_id)
        return superseded

    def _entity_importance(self, entity_name):
        person = self.memory.get_person(entity_name)
        project = self.memory.get_project(entity_name)
        metadata = person if isinstance(person, dict) else project if isinstance(project, dict) else {}
        importance = normalize_text(str(metadata.get("importance", "")))
        if any(marker in importance for marker in ("muito alta", "critical", "critica")):
            return "critical"
        if "alta" in importance or metadata.get("relationship"):
            return "high"
        return "medium"

    def _save_preference(self, text, normalized):
        match = re.search(r"prefiro\s+([a-z0-9]+)(.*)", normalized)
        if not match:
            return {"classification": "ignore", "action": "ignored", "confidence": 0.3}
        key = f"conversational_{match.group(1)}"
        old_value = self.memory.get_preference(key)
        self.memory.save_preference(key, text)
        action = "updated" if old_value is not None and old_value != text else "created"
        return {"classification": "preference", "action": action, "confidence": 0.9, "memory_id": key}

    def _save_relationship_fact(self, text, normalized, entity):
        person = self.memory.get_person(entity["name"])
        if not isinstance(person, dict):
            return {"classification": "ignore", "action": "ignored", "confidence": 0.4}
        person = dict(person)
        person["learned_relationship_context"] = text
        self.memory.save_person(entity["name"], person)
        return {"classification": "fact", "action": "updated", "confidence": 0.88, "memory_id": entity["name"], "entities": [entity["name"]]}

    def _remember_turn(self, turn_hash, memory_id, subject):
        continuity = self.memory.get_category("continuity")
        hashes = (list(continuity.get("processed_turn_hashes", [])) + [turn_hash])[-100:]
        recent_ids = list(continuity.get("recent_memory_ids", []))
        if memory_id:
            recent_ids = (recent_ids + [memory_id])[-20:]
        values = {
            "processed_turn_hashes": hashes,
            "recent_memory_ids": recent_ids,
            "last_session_at": self.now_fn().isoformat(),
        }
        if subject:
            values["last_subject"] = subject
        self.memory.update_continuity(values)

    def _find_duplicate(self, category, normalized, entity_name):
        for record in self.memory.get_recent_memories(limit=50):
            if record.get("category") != category:
                continue
            same_entity = entity_name in (record.get("entities") or []) or record.get("project") == entity_name
            if same_entity and record.get("source_turn_normalized") == normalized:
                return record
        return None

    def _recent_related_event(self, entity_name):
        for event in self.memory.get_recent_events(limit=20):
            if entity_name is None or entity_name in event.get("entities", []):
                return event
        return None

    def _mentioned_people(self, text):
        mentioned = []
        for key, data in self.memory.get_category("people").items():
            names = (key, data.get("name", "") if isinstance(data, dict) else "")
            if any(normalize_text(name) in normalize_text(text) for name in names if name):
                mentioned.append(key)
        return mentioned

    @staticmethod
    def _decision_subject(normalized):
        if any(marker in normalized for marker in ("implantacao", "taxa", "preco", "cobrar", "valor")):
            return "commercial fee"
        if any(marker in normalized for marker in ("mudar para", "mudamos para", "alterar para")):
            return "changed project decision"
        return "project decision"

    @staticmethod
    def _is_relationship_fact(normalized):
        return any(marker in normalized for marker in ("e meu amigo", "e minha amiga", "e meu socio", "trabalha na empresa"))

    def _should_update_event(self, normalized, subject):
        return self._recent_related_event(subject["name"] if subject else None) is not None and (
            any(marker in normalized for marker in self.UPDATE_MARKERS)
            or normalized.startswith(("ele ", "ela "))
        )

    @staticmethod
    def _is_plan_cancellation(normalized):
        return "nao vou mais" in normalized or "deixa para" in normalized or "cancel" in normalized

    def _negates_event(self, normalized):
        return normalized.startswith("nao ") or any(
            f"nao se {marker}" in normalized for marker in ("machuc", "acident")
        )

    @staticmethod
    def _looks_like_question(text, normalized):
        starters = ("quem ", "o que ", "qual ", "quais ", "como ", "quando ", "onde ", "por que ")
        return "?" in text or normalized.startswith(starters)

    @staticmethod
    def _resolve_time(normalized, now, exclude=None):
        references = ("ontem", "hoje", "amanha", "sabado", "domingo", "segunda", "terca", "quarta", "quinta", "sexta")
        reference = next((item for item in references if item != exclude and item in normalized), None)
        occurred_at = (now - timedelta(days=1)).date().isoformat() if reference == "ontem" else None
        return reference, occurred_at

    @staticmethod
    def _log(channel, result, reason=None):
        entities = ",".join(item for item in result.get("entities", []) if item) or "-"
        extra = f" reason={reason}" if reason else ""
        memory_id = f" memory_id={result['memory_id']}" if result.get("memory_id") else ""
        print(
            f"[CONVERSATIONAL_MEMORY] channel={channel} classification={result['classification']} "
            f"entities={entities} action={result['action']} confidence={result.get('confidence', 0):.2f}"
            f"{memory_id}{extra}"
        )
        if reason:
            result["reason"] = reason
        return result
