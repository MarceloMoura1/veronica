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


class GeminiUsageNormalizer:
    """Translate provider and legacy metadata without inventing values."""

    FIELD_ALIASES = {
        "input_tokens": ("prompt_token_count", "promptTokenCount"),
        "visible_output_tokens": ("candidates_token_count", "candidatesTokenCount", "response_token_count", "responseTokenCount"),
        "thinking_tokens": ("thoughts_token_count", "thoughtsTokenCount"),
        "cached_tokens": ("cached_content_token_count", "cachedContentTokenCount"),
        "tool_prompt_tokens": ("tool_use_prompt_token_count", "toolUsePromptTokenCount"),
        "total_tokens": ("total_token_count", "totalTokenCount"),
    }

    DETAIL_ALIASES = {
        "prompt_tokens_details": ("prompt_tokens_details", "promptTokensDetails"),
        "cache_tokens_details": ("cache_tokens_details", "cacheTokensDetails"),
        "output_tokens_details": (
            "response_tokens_details", "responseTokensDetails",
            "candidates_tokens_details", "candidatesTokensDetails",
        ),
        "tool_use_prompt_tokens_details": (
            "tool_use_prompt_tokens_details", "toolUsePromptTokensDetails",
        ),
    }

    @staticmethod
    def _read(metadata: Any, aliases: tuple[str, ...]) -> int | None:
        if metadata is None:
            return None
        for name in aliases:
            raw = metadata.get(name) if isinstance(metadata, dict) else getattr(metadata, name, None)
            if raw is not None:
                return int(raw)
        return None

    @staticmethod
    def _raw(metadata: Any, aliases: tuple[str, ...]) -> Any:
        if metadata is None:
            return None
        for name in aliases:
            if isinstance(metadata, dict):
                if name in metadata:
                    return metadata[name]
            elif hasattr(metadata, name):
                return getattr(metadata, name)
        return None

    @classmethod
    def _details(cls, metadata: Any, aliases: tuple[str, ...]) -> list[dict[str, Any]] | None:
        raw_details = cls._raw(metadata, aliases)
        if raw_details is None:
            return None
        normalized = []
        for detail in raw_details:
            modality = cls._raw(detail, ("modality",))
            token_count = cls._raw(detail, ("token_count", "tokenCount"))
            if modality is not None:
                modality = getattr(modality, "value", modality)
                modality = str(modality).rsplit(".", 1)[-1]
            normalized.append({
                "modality": modality,
                "token_count": int(token_count) if token_count is not None else None,
            })
        return normalized

    @classmethod
    def normalize(cls, metadata: Any) -> dict[str, Any]:
        values = {field: cls._read(metadata, aliases) for field, aliases in cls.FIELD_ALIASES.items()}
        values.update({field: cls._details(metadata, aliases) for field, aliases in cls.DETAIL_ALIASES.items()})
        values["usage_metadata_available"] = metadata is not None
        return values


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
        request_count: int = 1,
        retry_count: int = 0,
        diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        usage = GeminiUsageNormalizer.normalize(usage_metadata)
        record = {
            "timestamp": _now(),
            "integration_id": integration_id,
            "provider": provider,
            "model": model,
            "request_type": request_type,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["visible_output_tokens"],
            "visible_output_tokens": usage["visible_output_tokens"],
            "thinking_tokens": usage["thinking_tokens"],
            "cached_tokens": usage["cached_tokens"],
            "tool_prompt_tokens": usage["tool_prompt_tokens"],
            "prompt_tokens_details": usage["prompt_tokens_details"],
            "cache_tokens_details": usage["cache_tokens_details"],
            "output_tokens_details": usage["output_tokens_details"],
            "tool_use_prompt_tokens_details": usage["tool_use_prompt_tokens_details"],
            "total_tokens": usage["total_tokens"],
            "usage_metadata_available": usage["usage_metadata_available"],
            "request_count": max(1, int(request_count)),
            "retry_count": max(0, int(retry_count)),
            "latency_ms": latency_ms,
            "success": bool(success),
        }
        if diagnostics:
            record["diagnostics"] = self._sanitize_diagnostics(diagnostics)
        with self._lock:
            records = self._read()
            records.append(record)
            self._write(records)
        return record

    @staticmethod
    def _sanitize_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
        """Keep quantitative operational fields and reject content-bearing values."""
        allowed = {
            "logical_session_id", "connection_id", "logical_session_new", "connection_new",
            "reconnected", "resumption_requested", "resumption_accepted",
            "resumption_handle_hash", "fallback_manual_used", "turn_index",
            "connection_turn_index", "cold_start_send_count", "go_away_received",
            "model", "function_tool_count", "google_search_present", "system_instruction_chars",
            "tool_mode", "tool_mode_invalid", "external_tool_count", "internal_action_count",
            "direct_tool_count", "gateway_count", "provider_tool_count", "tool_schema_hash",
            "gateway", "canonical_action", "confirmation_required", "confirmation_outcome",
            "tool_retry", "request_id_hash", "tool_payload_bytes", "tool_result_bytes",
            "tool_outcome", "dispatch_stage", "reason_code", "arguments_container",
            "parse_success", "validation_success", "missing_field_count", "unexpected_field_count",
            "memory_tool", "memory_category", "memory_item_count", "memory_context_chars",
            "memory_estimated_tokens",
            "system_instruction_estimated_tokens", "tool_schema_chars",
            "tool_schema_estimated_tokens", "cold_start_chars", "cold_start_estimated_tokens",
            "cold_start_recent_reference_count", "cold_start_preserved_references",
            "cold_start_has_summary", "cold_start_has_important_turns",
            "cold_start_deduplicated_items", "cold_start_omitted_by_budget",
            "manual_restoration_count", "compression_trigger_tokens",
            "compression_target_tokens", "turn_coverage", "input_transcription_enabled",
            "output_transcription_enabled", "audio_chunks_total", "audio_chunks_active",
            "audio_chunks_inactive", "audio_duration_ms", "compression_inferred",
            "context_policy_route", "retrieval_item_count", "retrieval_context_chars",
            "retrieval_estimated_tokens",
        }
        result = {}
        for key, value in diagnostics.items():
            if key in allowed and isinstance(value, (str, int, float, bool, type(None))):
                result[key] = value
        return result

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

        def known(field: str) -> bool:
            return any(item.get(field) is not None for item in records)

        visible_output_total = sum(
            item.get("visible_output_tokens")
            if item.get("visible_output_tokens") is not None
            else (item.get("output_tokens") or 0)
            for item in records
        )

        tool_records = [item for item in records if item.get("diagnostics", {}).get("tool_outcome")]
        return {
            "period": period,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "visible_output_tokens": visible_output_total,
            "thinking_tokens": total("thinking_tokens") if known("thinking_tokens") else None,
            "cached_tokens": total("cached_tokens") if known("cached_tokens") else None,
            "tool_prompt_tokens": total("tool_prompt_tokens") if known("tool_prompt_tokens") else None,
            "total_tokens": total("total_tokens"),
            "requests": sum(item.get("request_count", 1) for item in records),
            "retries": total("retry_count"),
            "errors": sum(1 for item in records if not item.get("success")),
            "integration_errors": sum(
                1 for item in records
                if not item.get("success") and not item.get("diagnostics", {}).get("tool_outcome")
            ),
            "tool_errors": sum(
                1 for item in tool_records
                if item.get("diagnostics", {}).get("tool_outcome") == "tool_execution_error"
            ),
            "tool_rejections": sum(
                1 for item in tool_records
                if item.get("diagnostics", {}).get("tool_outcome") == "gateway_rejection"
            ),
            "tool_retries": sum(
                item.get("diagnostics", {}).get("tool_retry", 0) or 0 for item in tool_records
            ),
            "confirmation_denials": sum(
                1 for item in tool_records
                if item.get("diagnostics", {}).get("tool_outcome") == "confirmation_denied"
            ),
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
        retry_count: int = 0,
        diagnostics: dict[str, Any] | None = None,
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
            retry_count=retry_count,
            diagnostics=diagnostics,
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
