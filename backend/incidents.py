"""Authoritative, privacy-safe operational incident service."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
import queue
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SEVERITIES = ("grave", "medio", "leve")
OPEN_STATUSES = {"novo", "em_analise", "correcao_proposta", "em_correcao", "monitorando", "reaberto"}
STATUSES = OPEN_STATUSES | {"resolvido"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IncidentSanitizer:
    """Redacts secrets and discards content-bearing/private metadata."""
    SENSITIVE_KEYS = re.compile(
        r"api[_-]?key|authorization|bearer|password|passwd|cookie|secret|access[_-]?token|refresh[_-]?token|audio|transcript|message|customer|client|financial|payload",
        re.I,
    )
    PATTERNS = (
        re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
        re.compile(r"(?i)(api[_-]?key|password|secret|access[_-]?token|refresh[_-]?token)\s*[:=]\s*[^\s,;]+"),
        re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    )
    ALLOWED_METADATA = {
        "exception_class", "operation", "route", "http_status", "retry_count",
        "recovered", "reason_code", "gateway", "tool_outcome", "structural_location",
    }

    @classmethod
    def text(cls, value: Any, limit: int = 500) -> str:
        result = str(value or "").replace("\r", " ").replace("\n", " ").strip()
        for pattern in cls.PATTERNS:
            result = pattern.sub("[REDACTED]", result)
        result = re.sub(r"(?:[A-Za-z]:\\|/Users/|/home/)[^\s]+", "[PRIVATE_PATH]", result)
        return result[:limit]

    @classmethod
    def metadata(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        clean = {}
        for key, item in value.items():
            if key not in cls.ALLOWED_METADATA or cls.SENSITIVE_KEYS.search(str(key)):
                continue
            if isinstance(item, (str, int, float, bool, type(None))):
                clean[key] = cls.text(item, 120) if isinstance(item, str) else item
        return clean


class SeverityPolicy:
    """Deterministic defaults; callers may explicitly use the three public levels."""
    @staticmethod
    def classify(*, severity: str | None, category: str, error_code: str, metadata: dict[str, Any]) -> str:
        if severity is not None:
            normalized = str(severity).lower().replace("é", "e")
            aliases = {"critical": "grave", "high": "grave", "medium": "medio", "low": "leve", "warning": "leve"}
            normalized = aliases.get(normalized, normalized)
            if normalized not in SEVERITIES:
                raise ValueError(f"Invalid incident severity: {severity}")
            return normalized
        signal = f"{category} {error_code} {metadata.get('reason_code', '')}".lower()
        if any(word in signal for word in ("security", "corrupt", "data_loss", "database_down", "service_down")):
            return "grave"
        if metadata.get("retry_count", 0) >= 20 or any(word in signal for word in ("gateway_rejection", "tool_execution", "integration")):
            return "medio"
        return "leve"


class IncidentStore:
    def __init__(self, path: Path, resolved_retention_days: int = 180):
        self.path = Path(path)
        self.resolved_retention_days = resolved_retention_days
        self._lock = threading.RLock()

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, records: list[dict[str, Any]]) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.resolved_retention_days)
        retained = []
        for item in records:
            resolved = item.get("resolved_at")
            if item.get("status") != "resolvido" or not resolved:
                retained.append(item)
                continue
            try:
                if datetime.fromisoformat(resolved.replace("Z", "+00:00")) >= cutoff:
                    retained.append(item)
            except (TypeError, ValueError):
                retained.append(item)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(retained, handle, ensure_ascii=False, indent=2)
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name): os.unlink(temp_name)

    def upsert(self, incident: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            records = self._read()
            existing = next((x for x in records if x.get("fingerprint") == incident["fingerprint"]), None)
            if existing:
                existing["last_seen"] = incident["last_seen"]
                existing["updated_at"] = incident["updated_at"]
                existing["occurrence_count"] = int(existing.get("occurrence_count") or 0) + 1
                if existing.get("status") == "resolvido":
                    existing.update(status="reaberto", resolved_at=None, resolution_summary=None)
                result = existing
            else:
                records.append(incident); result = incident
            self._write(records)
            return dict(result)

    def list(self, *, severity=None, status="abertos", period=None) -> list[dict[str, Any]]:
        with self._lock: records = self._read()
        if severity and severity != "todos": records = [x for x in records if x.get("severity") == severity]
        if status == "abertos": records = [x for x in records if x.get("status") in OPEN_STATUSES]
        elif status in STATUSES: records = [x for x in records if x.get("status") == status]
        if period in ("today", "7d", "30d"):
            days = {"today": 1, "7d": 7, "30d": 30}[period]
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            records = [x for x in records if _parse(x.get("last_seen")) >= cutoff]
        rank = {"grave": 0, "medio": 1, "leve": 2}
        return sorted(records, key=lambda x: (rank.get(x.get("severity"), 9), -_parse(x.get("last_seen")).timestamp()))

    def get(self, incident_id: str) -> dict[str, Any] | None:
        with self._lock: return next((dict(x) for x in self._read() if x.get("incident_id") == incident_id), None)

    def transition(self, incident_id: str, status: str, resolution_summary: str | None = None) -> dict[str, Any]:
        if status not in STATUSES: raise ValueError(f"Invalid incident status: {status}")
        with self._lock:
            records = self._read(); item = next((x for x in records if x.get("incident_id") == incident_id), None)
            if not item: raise KeyError(incident_id)
            item["status"] = status; item["updated_at"] = utc_now()
            if status == "resolvido":
                item["resolved_at"] = item["updated_at"]
                item["resolution_summary"] = IncidentSanitizer.text(resolution_summary, 500)
            self._write(records); return dict(item)


def _parse(value: Any) -> datetime:
    try: return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError): return datetime.min.replace(tzinfo=timezone.utc)


class IncidentService:
    DEFAULT_LLM_LIST_LIMIT = 5
    MAX_LLM_LIST_LIMIT = 20
    SUMMARY_FIELDS = (
        "incident_id", "severity", "title", "status", "source", "component",
        "occurrence_count", "last_seen",
    )
    DETAIL_FIELDS = (
        "incident_id", "severity", "source", "component", "category", "error_code",
        "safe_summary", "status", "occurrence_count", "first_seen", "last_seen",
        "diagnosis", "resolution_summary", "resolved_at",
    )
    def __init__(self, store: IncidentStore): self.store = store

    @staticmethod
    def fingerprint(source: str, component: str, error_code: str, metadata: dict[str, Any]) -> str:
        structural = metadata.get("exception_class") or metadata.get("structural_location") or ""
        stable = "|".join(str(x).strip().lower() for x in (source, component, error_code, structural))
        return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]

    def collect(self, *, source: str, component: str, category: str, error_code: str,
                title: str, safe_summary: str, severity: str | None = None,
                metadata: dict[str, Any] | None = None, event_type: str = "error") -> dict[str, Any] | None:
        raw_meta = metadata or {}
        if event_type in {"confirmation_denied", "success", "health_check", "normal"}: return None
        if event_type == "retry" and int(raw_meta.get("retry_count", 0)) < 20: return None
        clean_meta = IncidentSanitizer.metadata(raw_meta)
        level = SeverityPolicy.classify(severity=severity, category=category, error_code=error_code, metadata=clean_meta)
        now = utc_now()
        incident = {
            "incident_id": str(uuid.uuid4()), "fingerprint": self.fingerprint(source, component, error_code, clean_meta),
            "severity": level, "source": IncidentSanitizer.text(source, 80), "component": IncidentSanitizer.text(component, 100),
            "category": IncidentSanitizer.text(category, 80), "error_code": IncidentSanitizer.text(error_code, 100),
            "title": IncidentSanitizer.text(title, 160), "safe_summary": IncidentSanitizer.text(safe_summary),
            "status": "novo", "first_seen": now, "last_seen": now, "occurrence_count": 1,
            "created_at": now, "updated_at": now, "resolved_at": None, "resolution_summary": None,
            "diagnosis": None, "metadata": clean_meta,
        }
        return self.store.upsert(incident)

    def list_system_incidents(self, **filters) -> dict[str, Any]:
        items = self.store.list(**filters)
        open_items = self.store.list(status="abertos")
        counts = {level: sum(x.get("severity") == level for x in open_items) for level in SEVERITIES}
        return {"incidents": items, "counts": counts, "source_of_truth": "incident_store"}

    def get_incident_details(self, incident_id: str) -> dict[str, Any]:
        item = self.store.get(incident_id)
        return {"incident": item, "found": item is not None, "source_of_truth": "incident_store",
                "explanation_instruction": "Separate observed facts from cause hypotheses; never present a hypothesis as certainty."}

    @staticmethod
    def _project(item: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
        projected = {}
        for field in fields:
            value = item.get(field)
            if value is not None:
                projected[field] = IncidentSanitizer.text(value, 500) if isinstance(value, str) else value
        return projected

    def list_system_incidents_for_llm(self, *, severity=None, status="abertos", period=None, limit=DEFAULT_LLM_LIST_LIMIT) -> dict[str, Any]:
        try:
            bounded_limit = max(1, min(int(limit), self.MAX_LLM_LIST_LIMIT))
        except (TypeError, ValueError, OverflowError):
            bounded_limit = self.DEFAULT_LLM_LIST_LIMIT
        items = self.store.list(severity=severity, status=status, period=period)
        selected = items[:bounded_limit]
        return {
            "total": len(items),
            "returned": len(selected),
            "limit": bounded_limit,
            "has_more": len(items) > len(selected),
            "counts_by_severity": {
                level: sum(item.get("severity") == level for item in items) for level in SEVERITIES
            },
            "incidents": [self._project(item, self.SUMMARY_FIELDS) for item in selected],
            "source_of_truth": "incident_store",
            "response_instruction": "Summarize only; do not mention incident IDs or fetch details unless the user asks.",
        }

    def get_incident_details_for_llm(self, incident_id: str) -> dict[str, Any]:
        item = self.store.get(incident_id)
        return {
            "incident": self._project(item, self.DETAIL_FIELDS) if item else None,
            "found": item is not None,
            "source_of_truth": "incident_store",
            "response_instruction": "Separate observed facts from cause hypotheses; never present hypotheses as certainty.",
        }


class IncidentDispatcher:
    """Bounded best-effort worker that keeps incident persistence off Live paths."""
    def __init__(self, service: IncidentService, max_queue: int = 256):
        self.service = service
        self.queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=max_queue)
        self.dropped_count = 0
        self.failure_count = 0
        self._closed = False
        self._worker = threading.Thread(target=self._run, name="incident-worker", daemon=True)
        self._worker.start()

    def submit(self, **incident: Any) -> bool:
        """Never waits for disk. Queue pressure is counted and graves get latest priority."""
        if self._closed:
            return False
        try:
            self.queue.put_nowait(incident)
            return True
        except queue.Full:
            if str(incident.get("severity", "")).lower() == "grave":
                try:
                    self.queue.get_nowait()
                    self.queue.task_done()
                    self.queue.put_nowait(incident)
                    self.dropped_count += 1
                    return True
                except queue.Empty:
                    pass
            self.dropped_count += 1
            return False

    def _run(self) -> None:
        while True:
            item = self.queue.get()
            try:
                if item is None:
                    return
                try:
                    self.service.collect(**item)
                except Exception:
                    # Never recursively report failures of the incident system.
                    self.failure_count += 1
            finally:
                self.queue.task_done()

    def flush(self, timeout: float | None = None) -> bool:
        if timeout is None:
            self.queue.join()
            return True
        deadline = time.monotonic() + timeout
        with self.queue.all_tasks_done:
            while self.queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.queue.all_tasks_done.wait(remaining)
        return True

    def shutdown(self, *, flush: bool = True, timeout: float = 2.0) -> None:
        if self._closed:
            return
        if flush:
            self.flush(timeout=timeout)
        self._closed = True
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            return
        self._worker.join(timeout=timeout)


class CodeRepairProvider:
    """Future controlled repair contract. No shell, exec, or arbitrary editing capability."""
    available = False
    def propose(self, incident: dict[str, Any]) -> dict[str, Any]:
        return {"available": False, "requires_confirmation": True, "reason": "repair_provider_unavailable"}
    def apply(self, incident: dict[str, Any], *, approved: bool) -> dict[str, Any]:
        return {"applied": False, "reason": "approval_required" if not approved else "repair_provider_unavailable"}
