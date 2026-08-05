"""Small, JSON-backed personal memory store."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import tempfile
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PersonalMemoryManager:
    CATEGORIES = (
        "profile", "preferences", "people", "facts", "projects",
        "events", "decisions", "plans", "continuity", "aliases", "relations",
    )
    MAX_IMPORT_BYTES = 256 * 1024
    MAX_BACKUPS = 20
    RELATION_STATUSES = {"planned", "active", "tentative", "completed", "cancelled"}

    def __init__(self, storage_dir: str | Path | None = None):
        project_root = Path(__file__).resolve().parents[2]
        self.storage_dir = Path(storage_dir) if storage_dir else project_root / "data" / "memory"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = {category: self._load(category) for category in self.CATEGORIES}

    def _path(self, category: str) -> Path:
        return self.storage_dir / f"{category}.json"

    def _load(self, category: str) -> dict[str, Any]:
        path = self._path(category)
        if not path.exists():
            if category not in {"aliases", "relations"}:
                self._atomic_write(path, {})
            return {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            if not isinstance(value, dict):
                raise ValueError("top-level JSON value must be an object")
            return value
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[MEMORY] error reading {path.name}: {exc}; using safe fallback")
            return {}

    @staticmethod
    def _atomic_write(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
                suffix=".tmp", delete=False
            ) as handle:
                temp_name = handle.name
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    def _save(self, category: str) -> None:
        self._atomic_write(self._path(category), self._data[category])

    @staticmethod
    def _matching_key(data: dict[str, Any], key: str) -> str | None:
        folded = key.casefold()
        return next((item for item in data if item.casefold() == folded), None)

    def _set(self, category: str, key: str, value: Any) -> None:
        with self._lock:
            existing = self._matching_key(self._data[category], key)
            self._data[category][existing or key] = value
            self._save(category)

    def _get(self, category: str, key: str, default: Any = None) -> Any:
        existing = self._matching_key(self._data[category], key)
        return self._data[category].get(existing, default) if existing else default

    def save_fact(self, key: str, value: Any) -> None:
        self._set("facts", key, value)
        print(f"[MEMORY] saved fact {key}")

    def get_fact(self, key: str, default: Any = None) -> Any:
        return self._get("facts", key, default)

    def save_preference(self, key: str, value: Any) -> None:
        self._set("preferences", key, value)
        print(f"[MEMORY] saved preference {key}={value}")

    def get_preference(self, key: str, default: Any = None) -> Any:
        return self._get("preferences", key, default)

    def save_person(self, name: str, data: dict[str, Any]) -> None:
        self._set("people", name, data)
        print(f"[MEMORY] saved person {name}")

    def get_person(self, name: str) -> dict[str, Any] | None:
        return self._get("people", name)

    def save_project(self, name: str, data: dict[str, Any]) -> None:
        self._set("projects", name, data)
        print(f"[MEMORY] saved project {name}")

    def get_project(self, name: str) -> dict[str, Any] | None:
        value = self._get("projects", name)
        if value is not None:
            print("[MEMORY] project retrieved")
        return value

    def get_category(self, category: str) -> dict[str, Any]:
        """Return a shallow snapshot for deterministic context construction."""
        if category not in self.CATEGORIES:
            raise ValueError(f"Unknown memory category: {category}")
        with self._lock:
            return dict(self._data[category])

    def _entity_catalog(self) -> list[dict[str, str]]:
        catalog = []
        for category in ("projects", "people"):
            for key, value in self._data[category].items():
                names = {str(key)}
                if isinstance(value, dict):
                    names.update(str(value.get(field, "")) for field in ("name", "canonical_name"))
                catalog.append({"name": str(key), "category": category, "identities": names})
        profile_name = self._data["profile"].get("name")
        if profile_name:
            catalog.append({"name": str(profile_name), "category": "profile", "identities": {str(profile_name)}})
        return catalog

    def _resolve_canonical_entity(self, value: str) -> dict[str, str]:
        from .entity_resolver import normalize_text
        normalized = normalize_text(str(value or ""))
        if not normalized:
            raise ValueError("Canonical entity must contain letters or numbers.")
        matches = [
            {"name": item["name"], "category": item["category"]}
            for item in self._entity_catalog()
            if any(normalize_text(identity) == normalized for identity in item["identities"] if identity)
        ]
        unique = {(item["name"], item["category"]): item for item in matches}
        if not unique:
            raise ValueError(f"Unknown canonical entity: {value}")
        if len(unique) > 1:
            raise ValueError("Canonical entity is ambiguous after normalization.")
        return next(iter(unique.values()))

    def _transactional_category_write(self, category: str, updated: dict[str, Any]) -> str:
        previous = self._data[category]
        backup_dir = self._create_backup()
        self._data[category] = updated
        try:
            self._save(category)
        except Exception:
            self._data[category] = previous
            raise
        self._prune_backups()
        return backup_dir.name

    def add_entity_alias(self, canonical_name: str, alias: str) -> dict[str, Any]:
        """Persist an alias for a known person or project without exposing memory data."""
        canonical_name = str(canonical_name or "").strip()
        alias = str(alias or "").strip()
        if not canonical_name or not alias:
            raise ValueError("Canonical name and alias are required.")
        from .entity_resolver import normalize_text
        entity = self._resolve_canonical_entity(canonical_name)
        canonical, category = entity["name"], entity["category"]
        normalized_alias = normalize_text(alias)
        if not normalized_alias:
            raise ValueError("Alias must contain letters or numbers.")

        with self._lock:
            canonical_owners = {
                item["name"] for item in self._entity_catalog()
                if any(normalize_text(identity) == normalized_alias for identity in item["identities"] if identity)
            }
            if canonical_owners == {canonical}:
                return {"canonical_name": canonical, "category": category, "alias": alias,
                        "changed": False, "backup_id": None}
            if canonical_owners:
                raise ValueError("Alias conflicts with another canonical entity.")

            alias_owners = {
                owner for owner, aliases in self._data["aliases"].items()
                if isinstance(aliases, list)
                and any(normalize_text(str(item)) == normalized_alias for item in aliases)
            }
            if alias_owners - {canonical} or len(alias_owners) > 1:
                raise ValueError("Ambiguous alias or alias already belongs to another entity.")
            aliases = list(self._data["aliases"].get(canonical, []))
            if any(normalize_text(str(item)) == normalized_alias for item in aliases):
                return {"canonical_name": canonical, "category": category, "alias": alias,
                        "changed": False, "backup_id": None}
            aliases.append(alias)
            updated = dict(self._data["aliases"])
            updated[canonical] = aliases
            backup_id = self._transactional_category_write("aliases", updated)
        return {"canonical_name": canonical, "category": category, "alias": alias,
                "changed": True, "backup_id": backup_id}

    def add_entity_relation(
        self, source_entity: str, relation_type: str, target_entity: str, *,
        status: str, summary: str, source: str,
        confidence: float = 1.0, importance: str = "high",
    ) -> dict[str, Any]:
        """Persist a validated directional relation between two canonical entities."""
        source_item = self._resolve_canonical_entity(source_entity)
        target_item = self._resolve_canonical_entity(target_entity)
        if source_item == target_item:
            raise ValueError("Self-relations are not allowed.")
        relation_type = str(relation_type or "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", relation_type):
            raise ValueError("Relation type must be a lowercase snake_case identifier.")
        status = str(status or "").strip().casefold()
        if status not in self.RELATION_STATUSES:
            raise ValueError("Unsupported relation status.")
        summary = str(summary or "").strip()
        source = str(source or "").strip()
        importance = str(importance or "").strip().casefold()
        if not summary or not source:
            raise ValueError("Relation summary and source are required.")
        try:
            confidence = float(confidence)
        except (TypeError, ValueError) as exc:
            raise ValueError("Relation confidence must be numeric.") from exc
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("Relation confidence must be between 0 and 1.")

        identity = "|".join((source_item["name"], relation_type, target_item["name"]))
        relation_id = f"relation_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"
        comparable = {
            "source_entity": source_item["name"], "source_category": source_item["category"],
            "relation_type": relation_type,
            "target_entity": target_item["name"], "target_category": target_item["category"],
            "entities": [source_item["name"], target_item["name"]],
            "status": status, "summary": summary, "source": source,
            "confidence": confidence, "importance": importance,
            "relationships": [{"type": relation_type, "entity": target_item["name"]}],
        }
        with self._lock:
            existing = self._data["relations"].get(relation_id)
            if existing:
                existing_comparable = {key: existing.get(key) for key in comparable}
                if existing_comparable == comparable:
                    return {"relation_id": relation_id, "changed": False, "backup_id": None}
                raise ValueError("Conflicting relation already exists.")
            now = datetime.now(timezone.utc).isoformat()
            record = {"id": relation_id, **comparable, "recorded_at": now, "updated_at": now}
            updated = dict(self._data["relations"])
            updated[relation_id] = record
            backup_id = self._transactional_category_write("relations", updated)
        return {"relation_id": relation_id, "changed": True, "backup_id": backup_id}

    def save_memory_record(self, category: str, memory_id: str, record: dict[str, Any]) -> None:
        if category not in {"events", "decisions", "plans"}:
            raise ValueError(f"Unsupported conversational memory category: {category}")
        self._set(category, memory_id, record)

    def update_continuity(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._data["continuity"].update(values)
            self._save("continuity")

    def get_recent_memories(self, limit: int = 10) -> list[dict[str, Any]]:
        records = []
        for category in ("events", "decisions", "plans"):
            for memory_id, record in self._data[category].items():
                if isinstance(record, dict):
                    records.append({"id": memory_id, "category": category, **record})
        return sorted(
            records,
            key=lambda item: item.get("updated_at") or item.get("recorded_at") or "",
            reverse=True,
        )[:limit]

    def get_recent_events(self, limit: int = 10) -> list[dict[str, Any]]:
        return self._recent_category("events", limit)

    def get_recent_decisions(self, limit: int = 10) -> list[dict[str, Any]]:
        return self._recent_category("decisions", limit)

    def get_active_decisions(self, limit: int = 10) -> list[dict[str, Any]]:
        return [
            record for record in self._recent_category("decisions", limit * 3)
            if record.get("status", "active") == "active"
        ][:limit]

    def get_active_plans(self, limit: int = 10) -> list[dict[str, Any]]:
        return [
            record for record in self._recent_category("plans", limit * 2)
            if record.get("status") in {"planned", "tentative"}
        ][:limit]

    def get_recent_plans(self, limit: int = 10) -> list[dict[str, Any]]:
        return self._recent_category("plans", limit)

    def _recent_category(self, category: str, limit: int) -> list[dict[str, Any]]:
        records = [
            {"id": memory_id, **record}
            for memory_id, record in self._data[category].items()
            if isinstance(record, dict)
        ]
        return sorted(
            records,
            key=lambda item: item.get("updated_at") or item.get("recorded_at") or "",
            reverse=True,
        )[:limit]

    def import_memory_text(self, text: str, source_name: str | None = None) -> dict[str, Any]:
        """Parse and merge a human-editable Veronica Memory Pack V1."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Memory import is empty.")
        if len(text.encode("utf-8")) > self.MAX_IMPORT_BYTES:
            raise ValueError(f"Memory import exceeds the {self.MAX_IMPORT_BYTES}-byte limit.")

        staged = {category: {} for category in self.CATEGORIES}
        ignored = []
        errors = []
        section = None
        entity_name = None

        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            header = re.fullmatch(r"\[([A-Za-z]+)(?::([^\]]+))?\]", line)
            if header:
                section = header.group(1).casefold()
                entity_name = header.group(2).strip() if header.group(2) else None
                if section not in {"profile", "preferences", "facts", "person", "project"}:
                    ignored.append({"line": line_number, "content": raw_line, "reason": "unknown section"})
                    section = None
                elif section in {"person", "project"} and entity_name == "":
                    entity_name = None
                continue

            if section is None:
                ignored.append({"line": line_number, "content": raw_line, "reason": "outside a supported section"})
                continue

            pair = re.fullmatch(r"([^=:]+?)\s*(?:=|:)\s*(.*)", line)
            if not pair:
                ignored.append({"line": line_number, "content": raw_line, "reason": "expected key = value"})
                continue
            key, value = pair.group(1).strip(), pair.group(2).strip()
            if not key or not value:
                ignored.append({"line": line_number, "content": raw_line, "reason": "empty key or value"})
                continue

            if section in {"profile", "preferences", "facts"}:
                staged[section][key] = value
                continue

            category = "people" if section == "person" else "projects"
            if entity_name:
                staged[category].setdefault(entity_name, {})[key] = value
            elif key.casefold() == "name":
                entity_name = value
                staged[category].setdefault(entity_name, {})
            elif entity_name:
                staged[category].setdefault(entity_name, {})[key] = value
            else:
                ignored.append({"line": line_number, "content": raw_line, "reason": f"{section} name must be declared first"})

        counts = {
            "profile": len(staged["profile"]),
            "preferences": len(staged["preferences"]),
            "people": len(staged["people"]),
            "projects": len(staged["projects"]),
            "facts": len(staged["facts"]),
        }
        if not any(counts.values()):
            raise ValueError("No valid memory entries were found.")

        changed_categories = set()
        overwritten = []
        with self._lock:
            merged = {category: dict(values) for category, values in self._data.items()}
            for category in ("profile", "preferences", "facts"):
                for key, value in staged[category].items():
                    existing_key = self._matching_key(merged[category], key)
                    if existing_key and merged[category][existing_key] != value:
                        overwritten.append(f"{category}.{existing_key}")
                    target_key = existing_key or key
                    if merged[category].get(target_key) != value:
                        merged[category][target_key] = value
                        changed_categories.add(category)

            for category in ("people", "projects"):
                for name, fields in staged[category].items():
                    existing_name = self._matching_key(merged[category], name)
                    target_name = existing_name or name
                    current = merged[category].get(target_name, {})
                    current = dict(current) if isinstance(current, dict) else {}
                    for key, value in fields.items():
                        existing_key = self._matching_key(current, key)
                        target_key = existing_key or key
                        if existing_key and current[existing_key] != value:
                            overwritten.append(f"{category}.{target_name}.{existing_key}")
                        if current.get(target_key) != value:
                            current[target_key] = value
                            changed_categories.add(category)
                    merged[category][target_name] = current

            backup_dir = None
            has_existing_memory = any(self._data[category] for category in self.CATEGORIES)
            if changed_categories and has_existing_memory:
                backup_dir = self._create_backup()
            for category in changed_categories:
                self._data[category] = merged[category]
                self._save(category)
            if backup_dir is not None:
                self._prune_backups()

        safe_source = Path(str(source_name)).name[:255] if source_name else None
        result = {
            "success": True,
            "source_name": safe_source,
            "counts": counts,
            "ignored_lines": ignored,
            "errors": errors,
            "overwritten": overwritten,
            "backup_created": backup_dir is not None,
            "changed_categories": sorted(changed_categories),
        }
        print(f"[MEMORY] imported pack {safe_source or '<unnamed>'}: {counts}")
        return result

    def _create_backup(self) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        backup_dir = self.storage_dir / "backups" / timestamp
        backup_dir.mkdir(parents=True, exist_ok=False)
        for category in self.CATEGORIES:
            source = self._path(category)
            if source.exists():
                shutil.copy2(source, backup_dir / source.name)
        print(f"[MEMORY] backup created id={backup_dir.name}")
        return backup_dir

    def _prune_backups(self) -> None:
        backup_root = self.storage_dir / "backups"
        if not backup_root.exists():
            return
        backups = sorted((path for path in backup_root.iterdir() if path.is_dir()), reverse=True)
        for expired in backups[self.MAX_BACKUPS :]:
            if expired.resolve().parent == backup_root.resolve():
                shutil.rmtree(expired)

    @staticmethod
    def _terms(text: str) -> set[str]:
        text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
        normalized = unicodedata.normalize("NFKD", text.casefold())
        normalized = "".join(c for c in normalized if not unicodedata.combining(c))
        stopwords = {
            "a", "as", "o", "os", "de", "da", "das", "do", "dos", "e", "em",
            "na", "nas", "no", "nos", "um", "uma", "qual", "que", "me", "meu",
            "minha", "the", "a", "an", "of", "to", "in", "is", "my", "what",
        }
        return {
            term for term in re.findall(r"[a-z0-9]+", normalized)
            if len(term) > 1 and term not in stopwords
        }

    def search(self, query: str) -> list[dict[str, Any]]:
        query_terms = self._terms(query)
        results = []
        for category, items in self._data.items():
            if category in {"events", "decisions", "plans", "continuity", "aliases"}:
                continue
            for key, value in items.items():
                terms = self._terms(f"{key} {json.dumps(value, ensure_ascii=False)}")
                score = len(query_terms & terms)
                if score:
                    results.append({"category": category, "key": key, "value": value, "score": score})
        if query_terms & {"chamar", "chame", "chama", "tratamento", "titulo"}:
            title = self.get_preference("preferred_title")
            if title is not None and not any(
                item["category"] == "preferences" and item["key"] == "preferred_title"
                for item in results
            ):
                results.append({
                    "category": "preferences", "key": "preferred_title",
                    "value": title, "score": 2,
                })
        return sorted(results, key=lambda item: (-item["score"], item["category"], item["key"]))

    def get_relevant_context(self, query: str, max_items: int = 3) -> str:
        results = self.search(query)[:max_items]
        if not results:
            print("[MEMORY] no relevant memory")
            return ""
        lines = ["Relevant persistent personal memory:"]
        for item in results:
            value = json.dumps(item["value"], ensure_ascii=False) if isinstance(item["value"], (dict, list)) else str(item["value"])
            lines.append(f"- {item['category']}.{item['key']}: {value}")
            print(f"[MEMORY] retrieved category={item['category']}")
        return "\n".join(lines)

    def search_global(self, query, entities=None, intent=None, time_filter=None, max_items=16):
        """Structured Global Brain retrieval with explainable ranking."""
        from .entity_resolver import EntityResolver
        from .memory_intelligence import MemoryIntelligence
        resolver = EntityResolver(self)
        return MemoryIntelligence(self, resolver).search_global(
            query, entities=entities, intent=intent, time_filter=time_filter, max_items=max_items
        )

    def capture_explicit_memory(self, text: str) -> dict[str, Any] | None:
        """Capture deliberately stated, high-confidence Portuguese patterns."""
        cleaned = text.strip().rstrip(".!?")
        title = re.search(r"(?:de agora em diante\s+)?me cham(?:a|e) de\s+(.+)$", cleaned, re.I)
        if title:
            value = title.group(1).strip(" \"'")
            self.save_preference("preferred_title", value)
            return {"category": "preferences", "key": "preferred_title", "value": value}

        personal_fact = re.search(r"meu\s+(.+?)\s+[eé]\s+(.+)$", cleaned, re.I)
        if personal_fact:
            key, value = personal_fact.groups()
            key = re.sub(r"\W+", "_", self._strip_accents(key.casefold())).strip("_")
            self.save_fact(key, value.strip())
            return {"category": "facts", "key": key, "value": value.strip()}

        project_code = re.search(
            r"(?:memorize|lembre|guarde)(?:-se)?\s+que\s+o\s+c[oó]digo(?:\s+interno)?\s+do\s+projeto\s+(.+?)\s+[eé]\s+(.+)$",
            cleaned, re.I,
        ) or re.search(r"o\s+c[oó]digo(?:\s+interno)?\s+do\s+projeto\s+(.+?)\s+[eé]\s+(.+)$", cleaned, re.I)
        if project_code:
            name, code = (part.strip(" \"'") for part in project_code.groups())
            current = self.get_project(name) or {}
            current["internal_code"] = code
            self.save_project(name, current)
            return {"category": "projects", "key": name, "value": current}

        explicit = re.search(r"(?:memorize|lembre|guarde)(?:-se)?\s+que\s+(.+?)\s+[eé]\s+(.+)$", cleaned, re.I)
        if explicit:
            key, value = explicit.groups()
            key = re.sub(r"\W+", "_", self._strip_accents(key.casefold())).strip("_")
            self.save_fact(key, value.strip())
            return {"category": "facts", "key": key, "value": value.strip()}
        return None

    @staticmethod
    def _strip_accents(text: str) -> str:
        return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
