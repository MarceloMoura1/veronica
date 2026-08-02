"""Small, JSON-backed personal memory store."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import unicodedata
from pathlib import Path
from typing import Any


class PersonalMemoryManager:
    CATEGORIES = ("profile", "preferences", "people", "facts", "projects")

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
            print(f"[MEMORY] retrieved {name}")
        return value

    @staticmethod
    def _terms(text: str) -> set[str]:
        normalized = unicodedata.normalize("NFKD", text.casefold())
        normalized = "".join(c for c in normalized if not unicodedata.combining(c))
        return {term for term in re.findall(r"[a-z0-9_-]+", normalized) if len(term) > 1}

    def search(self, query: str) -> list[dict[str, Any]]:
        query_terms = self._terms(query)
        results = []
        for category, items in self._data.items():
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
            print(f"[MEMORY] retrieved {item['category']}.{item['key']}")
        return "\n".join(lines)

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
