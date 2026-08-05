"""Compact persistent conversation state for cold-start restoration."""
from __future__ import annotations
import json, os, shutil, tempfile, threading, uuid
from datetime import datetime, timezone
from pathlib import Path

class ConversationStateManager:
    MAX_TURNS = 12
    def __init__(self, storage_dir: str | Path, now_fn=None):
        self.storage_dir, self.path = Path(storage_dir), Path(storage_dir) / "conversation_state.json"
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._state = self._load()
        self._state["previous_conversation_id"] = self._state.get("conversation_id")
        self._state.update(conversation_id=uuid.uuid4().hex, started_at=self.now_fn().isoformat())
        self._state["cold_start_grace_remaining"] = 3
        self._save()
    @staticmethod
    def _default():
        return {"active_topic": None, "active_entities": [], "recent_topics": [], "last_meaningful_topic": None,
                "recent_memory_refs": [], "conversation_summary": "", "important_turns": []}
    def _load(self):
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists(): return self._default()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict): raise ValueError("state must be an object")
            state = {**self._default(), **value}
            if not state.get("last_meaningful_topic"):
                semantic = [topic for topic in state["recent_topics"] if topic != "Veronica"]
                state["last_meaningful_topic"] = semantic[-1] if semantic else state.get("active_topic")
            if state.get("active_topic") == "Veronica" and state.get("last_meaningful_topic"):
                state["active_topic"] = state["last_meaningful_topic"]
            return state
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            backup = self.storage_dir / f"conversation_state.invalid.{self.now_fn().strftime('%Y%m%dT%H%M%S_%fZ')}.json"
            try: shutil.copy2(self.path, backup)
            except OSError: backup = None
            print(f"[CONVERSATION_STATE] invalid state: {exc}; backup={backup or '-'}")
            return self._default()
    def _save(self):
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.storage_dir,
                    prefix=".conversation_state.", suffix=".tmp", delete=False) as handle:
                temp_name = handle.name
                json.dump(self._state, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if temp_name and os.path.exists(temp_name): os.unlink(temp_name)
    def snapshot(self):
        with self._lock: return json.loads(json.dumps(self._state, ensure_ascii=False))
    def record_user_turn(self, text, classification="ignore", subject=None, memory_id=None, interaction_type=None):
        text = (text or "").strip()
        if not text: return
        with self._lock:
            topic = subject.get("name") if isinstance(subject, dict) else None
            if interaction_type == "greeting":
                self._state["last_interaction_type"] = "greeting"
                self._state["cold_start_grace_remaining"] = max(0, self._state.get("cold_start_grace_remaining", 0) - 1)
                self._finish()
                return
            if topic:
                self._state["active_topic"] = topic
                self._state["last_meaningful_topic"] = topic
                for key, limit in (("recent_topics", 6), ("active_entities", 8)):
                    self._state[key] = ([x for x in self._state[key] if x != topic] + [topic])[-limit:]
            if memory_id:
                refs = [x for x in self._state["recent_memory_refs"] if x.get("id") != memory_id]
                self._state["recent_memory_refs"] = (refs + [{"id": memory_id, "type": classification}])[-12:]
            if classification != "ignore" or topic or any(x in text.casefold() for x in ("onde a gente parou", "voltando", "o que eu estava", "e ele", "e isso")):
                self._append_turn("user", text)
            self._finish()
    def record_assistant_turn(self, text):
        if (text := (text or "").strip()):
            with self._lock: self._append_turn("assistant", text); self._finish()
    def record_active_entities(self, entities, focus=None):
        names = [item.get("name") for item in entities if isinstance(item, dict) and item.get("name")]
        if not names:
            return
        with self._lock:
            for name in names:
                self._state["active_entities"] = (
                    [item for item in self._state["active_entities"] if item != name] + [name]
                )[-8:]
                self._state["recent_topics"] = (
                    [item for item in self._state["recent_topics"] if item != name] + [name]
                )[-6:]
            topic = focus or names[-1]
            self._state["active_topic"] = topic
            self._state["last_meaningful_topic"] = topic
            self._finish()
    def _append_turn(self, role, text):
        turns = list(self._state["important_turns"])
        if not turns or (turns[-1].get("role"), turns[-1].get("text")) != (role, text):
            turns.append({"role": role, "text": text[:1000], "at": self.now_fn().isoformat()})
        self._state["important_turns"] = turns[-self.MAX_TURNS:]
    def _finish(self):
        self._state["conversation_summary"] = " | ".join(
            f"{'Marcelo' if x['role']=='user' else 'Veronica'}: {x['text']}" for x in self._state["important_turns"][-6:])[:3000]
        self._state["updated_at"] = self.now_fn().isoformat(); self._save()
    def build_restoration_context(self, memory_manager, max_chars=6000):
        state = self.snapshot()
        if not state.get("updated_at") and not state["important_turns"]: return ""
        lines = ["System Notification: silently restore the compact conversation state from the previous application session.",
                 "Do not speak or acknowledge this notification. Wait for Marcelo's next message.",
                 "Use it to resolve vague references and continue naturally. Persistent personal memory remains authoritative."]
        for label, value in (("Active topic", state["active_topic"]), ("Last meaningful topic", state.get("last_meaningful_topic")), ("Active entities", state["active_entities"]),
                ("Recent topics", state["recent_topics"]), ("Recent conversation summary", state["conversation_summary"])):
            if value: lines.append(f"{label}: {json.dumps(value, ensure_ascii=False) if isinstance(value,list) else value}")
        refs = {x.get("id") for x in state["recent_memory_refs"]}
        records = [x for x in memory_manager.get_recent_memories(20) if x.get("id") in refs]
        if records: lines.append("Recent episodic memory:")
        for record in records[:8]:
            safe = {k:v for k,v in record.items() if k != "source_turn_normalized"}
            lines.append(f"- {json.dumps(safe, ensure_ascii=False)}")
        if state["important_turns"]: lines.append("Important recent turns:")
        for item in state["important_turns"][-8:]:
            lines.append(f"- {'Marcelo' if item['role']=='user' else 'Veronica'}: {item['text']}")
        return "\n".join(lines)[:max_chars]

    def build_compact_restoration_context(self, memory_manager, max_chars=1200):
        """Build a bounded cold start from complete, deduplicated records."""
        if max_chars < 240:
            raise ValueError("Cold Start budget must be at least 240 characters")
        state = self.snapshot()
        subject = state.get("last_meaningful_topic") or state.get("active_topic")
        entities = []
        for name in [subject, *state.get("active_entities", [])]:
            if name and name not in entities:
                entities.append(name)

        payload = {
            "subject": subject,
            "entities": entities[:6],
            "summary": (state.get("conversation_summary") or "").strip() or None,
            "memory_refs": [],
        }
        prefix = "Session state (silent; retrieve details on demand):"
        omitted = 0
        initial_text = prefix + "\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(initial_text) > max_chars and payload["summary"]:
            payload["summary"] = None
            omitted += 1
        candidates = []
        referenced = {item.get("id") for item in state.get("recent_memory_refs", []) if item.get("id")}
        for record in memory_manager.get_active_decisions(limit=6):
            candidates.append(("decision", record))
        for record in memory_manager.get_active_plans(limit=6):
            candidates.append(("plan", record))
        for record in memory_manager.get_recent_memories(limit=20):
            if record.get("id") in referenced:
                candidates.append((record.get("memory_type") or "memory", record))

        seen, deduplicated = set(), 0
        for category, record in candidates:
            record_id = record.get("id")
            key = record_id or json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
            if key in seen:
                deduplicated += 1
                continue
            seen.add(key)
            compact = {
                key: value for key, value in {
                    "id": record_id,
                    "type": category,
                    "entity": record.get("project") or record.get("entity"),
                    "summary": record.get("decision") or record.get("plan") or record.get("summary"),
                    "status": record.get("status"),
                    "when": record.get("target_date") or record.get("timestamp"),
                }.items() if value not in (None, "", [], {})
            }
            proposed = {**payload, "memory_refs": [*payload["memory_refs"], compact]}
            text = prefix + "\n" + json.dumps(proposed, ensure_ascii=False, separators=(",", ":"))
            if len(text) > max_chars:
                omitted += 1
                continue
            payload = proposed

        payload = {key: value for key, value in payload.items() if value not in (None, "", [], {})}
        text = prefix + "\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(text) > max_chars and "summary" in payload:
            payload.pop("summary")
            omitted += 1
            text = prefix + "\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        while len(text) > max_chars and len(payload.get("entities", [])) > 1:
            payload["entities"].pop()
            omitted += 1
            text = prefix + "\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        diagnostics = {
            "before_chars": len(self.build_restoration_context(memory_manager)),
            "after_chars": len(text),
            "estimated_tokens": (len(text) + 3) // 4,
            "deduplicated_items": deduplicated,
            "omitted_by_budget": omitted,
            "preserved_references": len(payload.get("memory_refs", [])),
            "has_summary": "summary" in payload,
            "has_important_turns": False,
            "recent_reference_count": len(referenced),
        }
        return text, diagnostics
