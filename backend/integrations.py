"""Operational integration registry and Gemini telemetry.

This module is deliberately separate from personal memory. It stores only
operational metadata and never conversation content or API keys.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from dotenv import load_dotenv

STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
STATUS_ERROR = "error"
STATUS_CHECKING = "checking"
STATUS_NOT_CONFIGURED = "not_configured"
VALID_STATUSES = {
    STATUS_ACTIVE, STATUS_INACTIVE, STATUS_ERROR, STATUS_CHECKING, STATUS_NOT_CONFIGURED
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_error(error: Exception | str) -> str:
    text = str(error).replace("\n", " ").strip()
    return text[:300] or "Unknown integration error"


@dataclass
class IntegrationState:
    id: str
    name: str
    provider: str
    status: str
    configured: bool
    last_check: str | None = None
    last_success: str | None = None
    last_error: str | None = None
    latency_ms: int | None = None
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["api_key_configured"] = self.configured
        return data


class TelemetryStore:
    def __init__(self, path: Path, max_records: int = 5000):
        self.path = Path(path)
        self.max_records = max_records
        self._lock = threading.RLock()

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(records[-self.max_records :], handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def record(
        self,
        *,
        integration_id: str = "gemini",
        provider: str = "Google AI",
        model: str,
        request_type: str,
        success: bool,
        latency_ms: int | None = None,
        usage_metadata: Any = None,
    ) -> dict[str, Any]:
        def value(name: str) -> int | None:
            if usage_metadata is None:
                return None
            raw = (
                usage_metadata.get(name)
                if isinstance(usage_metadata, dict)
                else getattr(usage_metadata, name, None)
            )
            return int(raw) if raw is not None else None

        record = {
            "timestamp": _now(),
            "integration_id": integration_id,
            "provider": provider,
            "model": model,
            "request_type": request_type,
            "input_tokens": value("prompt_token_count"),
            "output_tokens": value("response_token_count"),
            "total_tokens": value("total_token_count"),
            "latency_ms": latency_ms,
            "success": bool(success),
        }
        with self._lock:
            records = self._read()
            records.append(record)
            self._write(records)
        return record

    @staticmethod
    def _parse(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def query(
        self,
        *,
        integration_id: str = "gemini",
        period: str = "today",
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 20,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now().astimezone()
        local_tz = current.tzinfo or timezone.utc
        today = current.astimezone(local_tz).date()

        if period == "yesterday":
            start = today - timedelta(days=1)
            end = today - timedelta(days=1)
        elif period == "last_7_days":
            start, end = today - timedelta(days=6), today
        elif period == "this_month":
            start, end = today.replace(day=1), today
        elif period == "custom" and start_date and end_date:
            start = datetime.fromisoformat(start_date).date()
            end = datetime.fromisoformat(end_date).date()
        else:
            period = "today"
            start = end = today

        with self._lock:
            records = [
                item for item in self._read()
                if item.get("integration_id", "gemini") == integration_id
                and start <= self._parse(item["timestamp"]).astimezone(local_tz).date() <= end
            ]

        def total(field: str) -> int:
            return sum(item.get(field) or 0 for item in records)

        return {
            "period": period,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "total_tokens": total("total_tokens"),
            "requests": len(records),
            "errors": sum(1 for item in records if not item.get("success")),
            "estimated_cost": None,
            "cost_status": "not_configured",
            "recent": list(reversed(records[-max(0, min(limit, 100)) :])),
        }


class IntegrationEventStore:
    def __init__(self, path: Path, max_records: int = 2000):
        self.path = Path(path)
        self.max_records = max_records
        self._lock = threading.RLock()

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def record(self, *, integration_id: str, level: str, event: str, message: str) -> dict[str, Any]:
        record = {
            "timestamp": _now(),
            "integration_id": integration_id,
            "level": level,
            "event": event,
            "message": _safe_error(message),
        }
        with self._lock:
            records = self._read()
            records.append(record)
            TelemetryStore(self.path, self.max_records)._write(records)
        return record

    def query(self, integration_id: str, limit: int = 20) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            records = [item for item in self._read() if item.get("integration_id") == integration_id]
        bounded = max(0, min(limit, 100))

        def recent(level: str) -> list[dict[str, Any]]:
            items = [item for item in records if item.get("level") == level]
            return list(reversed(items[-bounded:]))

        return {
            "errors": recent("error"),
            "warnings": recent("warning"),
            "events": list(reversed(records[-bounded:])),
        }


class IntegrationManager:
    def __init__(
        self,
        *,
        telemetry_path: Path | None = None,
        events_path: Path | None = None,
        env_path: Path | None = None,
        api_key: str | None = None,
        client_factory: Callable[[str], Any] | None = None,
        event_callback: Callable[[dict[str, Any]], Any] | None = None,
        main_model: str = "gemini-2.5-flash-native-audio-preview-12-2025",
        live_model: str = "models/gemini-2.5-flash-native-audio-preview-12-2025",
    ):
        project_root = Path(__file__).resolve().parent.parent
        self.env_path = Path(env_path or project_root / ".env")
        load_dotenv(self.env_path, override=False)
        self._api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY")
        self._client_factory = client_factory
        self._event_callback = event_callback
        self.main_model = main_model
        self.live_model = live_model
        self.telemetry = TelemetryStore(
            telemetry_path or project_root / "data" / "telemetry" / "gemini_usage.json"
        )
        self.events = IntegrationEventStore(
            events_path or project_root / "data" / "telemetry" / "integration_events.json"
        )
        try:
            sdk_version = importlib.metadata.version("google-genai")
        except importlib.metadata.PackageNotFoundError:
            sdk_version = None
        self._states: dict[str, IntegrationState] = {}
        self.register(
            IntegrationState(
                id="gemini",
                name="Gemini",
                provider="Google AI",
                status=STATUS_INACTIVE if self._api_key else STATUS_NOT_CONFIGURED,
                configured=bool(self._api_key),
                capabilities=["live_audio", "text", "tools", "cad", "web"],
                metadata={
                    "main_model": self.main_model,
                    "live_model": self.live_model,
                    "sdk_name": "google-genai",
                    "sdk_version": sdk_version,
                    "cost_available": False,
                },
            )
        )

    def register(self, state: IntegrationState) -> None:
        if state.status not in VALID_STATUSES:
            raise ValueError(f"Invalid integration status: {state.status}")
        self._states[state.id] = state

    def list_integrations(self) -> list[dict[str, Any]]:
        return [state.public_dict() for state in self._states.values()]

    def get_status(self, integration_id: str = "gemini") -> dict[str, Any]:
        return self._states[integration_id].public_dict()

    def get_details(
        self,
        integration_id: str = "gemini",
        *,
        period: str = "today",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        details = self.get_status(integration_id)
        details["usage"] = self.telemetry.query(
            integration_id=integration_id,
            period=period,
            start_date=start_date,
            end_date=end_date,
        )
        return details

    def get_reports(self, integration_id: str = "gemini", *, limit: int = 20) -> dict[str, Any]:
        return {
            "integration": self.get_status(integration_id),
            "reports": self.events.query(integration_id, limit=limit),
        }

    async def _notify(self) -> None:
        if not self._event_callback:
            return
        result = self._event_callback({"integrations": self.list_integrations()})
        if asyncio.iscoroutine(result):
            await result

    def _notify_soon(self) -> None:
        if not self._event_callback:
            return
        try:
            asyncio.get_running_loop().create_task(self._notify())
        except RuntimeError:
            return

    def _record_event(self, integration_id: str, level: str, event: str, message: str) -> None:
        self.events.record(
            integration_id=integration_id,
            level=level,
            event=event,
            message=message,
        )

    def _redact_error(self, error: Exception | str) -> str:
        message = _safe_error(error)
        return message.replace(self._api_key, "[REDACTED]") if self._api_key else message

    def _client(self):
        if not self._api_key:
            return None
        if self._client_factory:
            return self._client_factory(self._api_key)
        from google import genai
        return genai.Client(
            http_options={"api_version": "v1beta"}, api_key=self._api_key
        )

    async def test_connection(self, integration_id: str = "gemini") -> dict[str, Any]:
        state = self._states[integration_id]
        if not self._api_key:
            state.configured = False
            state.status = STATUS_NOT_CONFIGURED
            state.last_check = _now()
            state.last_error = None
            self._record_event(integration_id, "warning", "not_configured", "API não configurada")
            await self._notify()
            return self.get_status(integration_id)

        state.configured = True
        state.status = STATUS_CHECKING
        state.last_check = _now()
        state.last_error = None
        await self._notify()
        started = perf_counter()
        try:
            client = self._client()
            await client.aio.models.get(model=self.main_model)
            state.latency_ms = round((perf_counter() - started) * 1000)
            state.status = STATUS_ACTIVE
            state.last_success = _now()
            self.telemetry.record(
                integration_id=integration_id,
                provider=state.provider,
                model=self.main_model,
                request_type="health_check",
                success=True,
                latency_ms=state.latency_ms,
                usage_metadata=None,
            )
            self._record_event(integration_id, "info", "connection_test", "Conexão testada com sucesso")
        except Exception as error:
            state.latency_ms = round((perf_counter() - started) * 1000)
            state.status = STATUS_ERROR
            state.last_error = self._redact_error(error)
            self.telemetry.record(
                integration_id=integration_id,
                provider=state.provider,
                model=self.main_model,
                request_type="health_check",
                success=False,
                latency_ms=state.latency_ms,
                usage_metadata=None,
            )
            self._record_event(integration_id, "error", "connection_test", state.last_error)
        await self._notify()
        return self.get_status(integration_id)

    async def mark_live_connected(self, integration_id: str = "gemini") -> None:
        state = self._states[integration_id]
        state.configured = bool(self._api_key)
        state.status = STATUS_ACTIVE if self._api_key else STATUS_NOT_CONFIGURED
        state.last_check = _now()
        if self._api_key:
            state.last_success = state.last_check
            state.last_error = None
            self._record_event(integration_id, "info", "live_connected", "Sessão Live conectada")
        await self._notify()

    async def mark_live_error(self, error: Exception | str, integration_id: str = "gemini") -> None:
        state = self._states[integration_id]
        state.configured = bool(self._api_key)
        state.status = STATUS_ERROR if self._api_key else STATUS_NOT_CONFIGURED
        state.last_check = _now()
        state.last_error = self._redact_error(error) if self._api_key else None
        if state.last_error:
            self._record_event(integration_id, "error", "live_error", state.last_error)
        await self._notify()

    def record_usage(
        self,
        usage_metadata: Any,
        *,
        integration_id: str = "gemini",
        request_type: str,
        model: str | None = None,
        success: bool = True,
        latency_ms: int | None = None,
    ) -> dict[str, Any]:
        state = self._states[integration_id]
        record = self.telemetry.record(
            integration_id=integration_id,
            provider=state.provider,
            model=model or self.live_model,
            request_type=request_type,
            success=success,
            latency_ms=latency_ms,
            usage_metadata=usage_metadata,
        )
        self._record_event(
            integration_id,
            "info" if success else "error",
            "request",
            f"{request_type}: {'sucesso' if success else 'erro'}",
        )
        self._notify_soon()
        return record

    async def update_api_key(self, api_key: str) -> dict[str, Any]:
        key = (api_key or "").strip()
        if not key:
            raise ValueError("API key is required")
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        lines = self.env_path.read_text(encoding="utf-8").splitlines() if self.env_path.exists() else []
        replacement = "GEMINI_API_KEY=" + key
        updated = False
        safe_lines = []
        for line in lines:
            if line.lstrip().startswith("GEMINI_API_KEY="):
                if not updated:
                    safe_lines.append(replacement)
                    updated = True
            else:
                safe_lines.append(line)
        if not updated:
            safe_lines.append(replacement)

        fd, temp_name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=self.env_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("\n".join(safe_lines) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.env_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

        self._api_key = key
        os.environ["GEMINI_API_KEY"] = key
        state = self._states["gemini"]
        state.configured = True
        state.status = STATUS_INACTIVE
        state.last_error = None
        self._record_event("gemini", "info", "configuration", "API configurada")
        await self._notify()
        return self.get_status("gemini")

    def tool_status(self, integration_id: str = "gemini") -> dict[str, Any]:
        return self.get_status(integration_id)

    def tool_usage(
        self,
        integration_id: str = "gemini",
        period: str = "today",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        return self.telemetry.query(
            integration_id=integration_id,
            period=period,
            start_date=start_date,
            end_date=end_date,
        )
