"""Persistent visual chat history, isolated from model context and personal memory."""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ChatHistoryStore:
    ROLES = {"user", "assistant", "system"}
    SOURCES = {"voice", "text", "assistant", "system"}

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._known_ids: set[str] | None = None

    @staticmethod
    def _timestamp(value: Any = None) -> str:
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
            except ValueError:
                pass
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def normalize(cls, message: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(message, dict):
            raise ValueError("message must be an object")
        message_id = str(message.get("id") or uuid.uuid4()).strip()
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "").strip()
        source = str(message.get("source") or ("assistant" if role == "assistant" else "text")).strip().lower()
        if not message_id or len(message_id) > 160:
            raise ValueError("invalid message id")
        if role not in cls.ROLES:
            raise ValueError("invalid message role")
        if source not in cls.SOURCES:
            raise ValueError("invalid message source")
        if not content:
            raise ValueError("message content is empty")
        return {
            "id": message_id,
            "role": role,
            "content": content[:50000],
            "timestamp": cls._timestamp(message.get("timestamp")),
            "source": source,
        }

    def list_messages(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        messages: dict[str, dict[str, Any]] = {}
        with self._lock:
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            item = self.normalize(json.loads(line))
                        except (ValueError, TypeError, json.JSONDecodeError):
                            continue
                        messages.setdefault(item["id"], item)
            except OSError:
                return []
        return sorted(messages.values(), key=lambda item: (item["timestamp"], item["id"]))

    def append(self, message: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        normalized = self.normalize(message)
        encoded = (json.dumps(normalized, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            if self._known_ids is None:
                self._known_ids = {item["id"] for item in self.list_messages()}
            if normalized["id"] in self._known_ids:
                return normalized, False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                remaining = memoryview(encoded)
                while remaining:
                    remaining = remaining[os.write(descriptor, remaining):]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._known_ids.add(normalized["id"])
        return normalized, True

    def clear(self) -> None:
        with self._lock:
            if self.path.exists():
                self.path.unlink()
            self._known_ids = set()
