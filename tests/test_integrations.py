import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import SimpleNamespace

import pytest

from integrations import IntegrationEventStore, IntegrationManager, IntegrationState, TelemetryStore
import integrations


class FakeModels:
    def __init__(self, error=None):
        self.error = error

    async def get(self, model):
        if self.error:
            raise self.error
        return SimpleNamespace(name=model)


class FakeClient:
    def __init__(self, error=None):
        self.aio = SimpleNamespace(models=FakeModels(error))


def manager(tmp_path, *, api_key="", error=None, callback=None):
    return IntegrationManager(
        telemetry_path=tmp_path / "telemetry.json",
        preferences_path=tmp_path / "preferences.json",
        events_path=tmp_path / "events.json",
        env_path=tmp_path / ".env",
        api_key=api_key,
        client_factory=lambda _key: FakeClient(error),
        event_callback=callback,
    )


def test_monthly_budget_is_optional_and_persisted_without_secrets(tmp_path):
    integration_manager = manager(tmp_path, api_key="secret")
    assert integration_manager.get_preferences("gemini")["monthly_token_budget"] is None
    assert integration_manager.update_monthly_token_budget("gemini", "1000000") == {
        "monthly_token_budget": 1_000_000
    }
    persisted = (tmp_path / "preferences.json").read_text(encoding="utf-8")
    assert "1000000" in persisted and "secret" not in persisted


@pytest.mark.parametrize("value", [None, "", 0, -1, "1.5", float("nan"), 9_007_199_254_740_992])
def test_monthly_budget_rejects_invalid_values(tmp_path, value):
    with pytest.raises(ValueError):
        manager(tmp_path).update_monthly_token_budget("gemini", value)


def test_details_include_calendar_month_usage_and_budget(tmp_path):
    integration_manager = manager(tmp_path)
    integration_manager.update_monthly_token_budget("gemini", 100)
    integration_manager.record_usage(
        {"total_token_count": 25}, request_type="live"
    )
    details = integration_manager.get_details("gemini", period="today")
    assert details["usage_monthly"]["period"] == "this_month"
    assert details["usage_monthly"]["total_tokens"] == 25
    assert details["preferences"]["monthly_token_budget"] == 100


def test_internal_events_never_inflate_provider_tokens_or_requests(tmp_path):
    store = TelemetryStore(tmp_path / "usage.json")
    store.record(
        model="m", request_type="live", success=True,
        usage_metadata={"total_token_count": 20},
    )
    store.record(
        model="m", request_type="live_tool_call", success=True,
        usage_metadata={"total_token_count": 999},
    )
    summary = store.query()
    assert summary["total_tokens"] == 20
    assert summary["requests"] == 1
    assert summary["internal_events"] == 1


def test_registry_returns_only_real_gemini_integration(tmp_path):
    items = manager(tmp_path).list_integrations()
    assert [item["id"] for item in items] == ["gemini"]
    assert items[0]["provider"] == "Google AI"


def test_missing_key_is_not_configured(tmp_path):
    assert manager(tmp_path).get_status()["status"] == "not_configured"


def test_successful_connection_becomes_active(tmp_path):
    result = asyncio.run(manager(tmp_path, api_key="secret").test_connection())
    assert result["status"] == "active"
    assert result["last_success"]
    assert result["latency_ms"] is not None


def test_failed_connection_becomes_error_with_safe_message(tmp_path):
    result = asyncio.run(manager(tmp_path, api_key="secret", error=RuntimeError("secret rejected")).test_connection())
    assert result["status"] == "error"
    assert result["last_error"] == "[REDACTED] rejected"


def test_api_key_is_saved_but_never_returned(tmp_path):
    integration_manager = manager(tmp_path)
    payload = asyncio.run(integration_manager.update_api_key("super-secret-value"))
    assert "super-secret-value" not in str(payload)
    assert "super-secret-value" not in str(integration_manager.list_integrations())
    assert payload["api_key_configured"] is True
    assert "super-secret-value" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_telemetry_records_real_usage_metadata(tmp_path):
    store = TelemetryStore(tmp_path / "usage.json")
    record = store.record(
        model="gemini-test", request_type="text", success=True,
        usage_metadata={"prompt_token_count": 10, "response_token_count": 4, "total_token_count": 14},
    )
    assert record["input_tokens"] == 10
    assert record["output_tokens"] == 4
    assert record["total_tokens"] == 14


def test_missing_usage_metadata_does_not_invent_tokens(tmp_path):
    record = TelemetryStore(tmp_path / "usage.json").record(
        model="gemini-test", request_type="live", success=True, usage_metadata=None
    )
    assert record["input_tokens"] is None
    assert record["output_tokens"] is None
    assert record["total_tokens"] is None


def test_today_aggregation_counts_tokens_requests_and_errors(tmp_path):
    store = TelemetryStore(tmp_path / "usage.json")
    store.record(model="m", request_type="text", success=True, usage_metadata={"prompt_token_count": 5, "response_token_count": 3, "total_token_count": 8})
    store.record(model="m", request_type="text", success=False, usage_metadata=None)
    summary = store.query(period="today")
    assert summary["input_tokens"] == 5
    assert summary["output_tokens"] == 3
    assert summary["total_tokens"] == 8
    assert summary["requests"] == 2
    assert summary["errors"] == 1


def test_last_seven_days_and_custom_ranges(tmp_path):
    store = TelemetryStore(tmp_path / "usage.json")
    now = datetime.now(timezone.utc)
    records = [
        {"timestamp": (now - timedelta(days=3)).isoformat(), "provider": "Google", "model": "m", "request_type": "text", "input_tokens": 2, "output_tokens": 1, "total_tokens": 3, "latency_ms": 1, "success": True},
        {"timestamp": (now - timedelta(days=10)).isoformat(), "provider": "Google", "model": "m", "request_type": "text", "input_tokens": 9, "output_tokens": 1, "total_tokens": 10, "latency_ms": 1, "success": True},
    ]
    store._write(records)
    seven_days = store.query(period="last_7_days", now=now)
    custom = store.query(period="custom", start_date=(now - timedelta(days=11)).date().isoformat(), end_date=(now - timedelta(days=9)).date().isoformat(), now=now)
    assert seven_days["total_tokens"] == 3
    assert custom["total_tokens"] == 10


def test_registry_supports_multiple_integrations(tmp_path):
    integration_manager = manager(tmp_path)
    integration_manager.register(IntegrationState(id="future", name="Future", provider="Test", status="inactive", configured=False))
    assert [item["id"] for item in integration_manager.list_integrations()] == ["gemini", "future"]


def test_event_payload_never_contains_api_key(tmp_path):
    payloads = []
    integration_manager = manager(tmp_path, api_key="do-not-leak", callback=payloads.append)
    asyncio.run(integration_manager.test_connection())
    assert payloads
    assert "do-not-leak" not in str(payloads)
    assert "api_key" not in str(payloads).replace("api_key_configured", "")


def test_tools_share_registry_status_and_usage(tmp_path):
    integration_manager = manager(tmp_path, api_key="secret")
    integration_manager.record_usage({"prompt_token_count": 7, "response_token_count": 2, "total_token_count": 9}, request_type="live")
    assert integration_manager.tool_status() == integration_manager.get_status()
    assert integration_manager.tool_usage() == integration_manager.get_details()["usage"]


def test_telemetry_is_bounded(tmp_path):
    store = TelemetryStore(tmp_path / "usage.json", max_records=2)
    for _ in range(3):
        store.record(model="m", request_type="text", success=True, usage_metadata=None)
    assert len(store._read()) == 2


def test_telemetry_is_isolated_by_integration(tmp_path):
    store = TelemetryStore(tmp_path / "usage.json")
    store.record(integration_id="gemini", model="m", request_type="text", success=True, usage_metadata={"total_token_count": 3})
    store.record(integration_id="future", provider="Test", model="m", request_type="text", success=True, usage_metadata={"total_token_count": 9})
    assert store.query(integration_id="gemini")["total_tokens"] == 3
    assert store.query(integration_id="future")["total_tokens"] == 9


def test_reports_return_real_events_without_fake_entries(tmp_path):
    integration_manager = manager(tmp_path, api_key="secret", error=RuntimeError("service unavailable"))
    asyncio.run(integration_manager.test_connection())
    reports = integration_manager.get_reports("gemini")["reports"]
    assert reports["errors"]
    assert reports["errors"][0]["event"] == "connection_test"
    assert reports["warnings"] == []


def test_event_store_is_isolated_by_integration(tmp_path):
    store = IntegrationEventStore(tmp_path / "events.json")
    store.record(integration_id="gemini", level="info", event="one", message="Gemini event")
    store.record(integration_id="future", level="warning", event="two", message="Future warning")
    assert len(store.query("gemini")["events"]) == 1
    assert store.query("gemini")["warnings"] == []
    assert len(store.query("future")["warnings"]) == 1


def test_event_persistence_permission_error_is_fail_open(tmp_path, monkeypatch, capsys):
    store = IntegrationEventStore(tmp_path / "integration_events.json")
    attempts = 0

    def denied(_source, _destination):
        nonlocal attempts
        attempts += 1
        raise PermissionError(5, "Access denied")

    monkeypatch.setattr(integrations.os, "replace", denied)
    record = store.record(
        integration_id="gemini", level="error", event="live_error", message="1011"
    )

    assert record["event"] == "live_error"
    assert attempts == 3
    assert "[TELEMETRY] persistence_failed" in capsys.readouterr().out


def test_concurrent_event_writes_remain_valid_json(tmp_path):
    path = tmp_path / "integration_events.json"
    store = IntegrationEventStore(path)
    threads = [
        threading.Thread(
            target=store.record,
            kwargs={"integration_id": "gemini", "level": "info", "event": f"event-{index}", "message": "ok"},
        )
        for index in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload) == 20


def test_details_and_tools_use_the_same_integration_telemetry(tmp_path):
    integration_manager = manager(tmp_path, api_key="secret")
    integration_manager.record_usage(
        {"prompt_token_count": 4, "response_token_count": 6, "total_token_count": 10},
        integration_id="gemini",
        request_type="live",
    )
    assert integration_manager.tool_usage("gemini") == integration_manager.get_details("gemini")["usage"]
    assert integration_manager.get_status("gemini")["metadata"]["sdk_name"] == "google-genai"


def test_candidates_are_visible_and_thoughts_are_separate(tmp_path):
    record = TelemetryStore(tmp_path / "usage.json").record(
        model="m", request_type="live", success=True,
        usage_metadata={
            "prompt_token_count": 20, "candidates_token_count": 7,
            "thoughts_token_count": 11, "total_token_count": 38,
        },
    )
    assert record["visible_output_tokens"] == record["output_tokens"] == 7
    assert record["thinking_tokens"] == 11
    assert record["total_tokens"] == 38


def test_provider_total_is_preserved_when_components_differ(tmp_path):
    record = TelemetryStore(tmp_path / "usage.json").record(
        model="m", request_type="live", success=True,
        usage_metadata={"prompt_token_count": 10, "candidates_token_count": 2, "total_token_count": 19},
    )
    assert record["total_tokens"] == 19
    assert record["thinking_tokens"] is None


def test_camel_case_metadata_and_cache_tools_are_supported(tmp_path):
    record = TelemetryStore(tmp_path / "usage.json").record(
        model="m", request_type="live", success=True,
        usage_metadata={
            "promptTokenCount": 10, "candidatesTokenCount": 2,
            "cachedContentTokenCount": 4, "toolUsePromptTokenCount": 3,
            "totalTokenCount": 19,
        },
    )
    assert (record["cached_tokens"], record["tool_prompt_tokens"]) == (4, 3)


def test_retry_is_counted_and_metadata_absence_remains_explicit(tmp_path):
    store = TelemetryStore(tmp_path / "usage.json")
    record = store.record(
        model="m", request_type="live_reconnect", success=False,
        usage_metadata=None, retry_count=1,
    )
    summary = store.query()
    assert record["usage_metadata_available"] is False
    assert record["thinking_tokens"] is None
    assert summary["requests"] == 1 and summary["retries"] == 1


def test_old_events_without_new_fields_remain_readable(tmp_path):
    store = TelemetryStore(tmp_path / "usage.json")
    store._write([{
        "timestamp": datetime.now(timezone.utc).isoformat(), "provider": "Google",
        "model": "old", "request_type": "live", "input_tokens": 5,
        "output_tokens": 3, "total_tokens": 8, "success": True,
    }])
    summary = store.query()
    assert summary["visible_output_tokens"] == 3
    assert summary["thinking_tokens"] is None


class FakeMediaModality(Enum):
    TEXT = "TEXT"
    AUDIO = "AUDIO"


def test_live_response_token_details_are_normalized(tmp_path):
    record = TelemetryStore(tmp_path / "usage.json").record(
        model="live", request_type="live", success=True,
        usage_metadata=SimpleNamespace(response_tokens_details=[
            SimpleNamespace(modality=FakeMediaModality.AUDIO, token_count=12),
            SimpleNamespace(modality="TEXT", token_count=3),
        ], total_token_count=29),
    )
    assert record["output_tokens_details"] == [
        {"modality": "AUDIO", "token_count": 12},
        {"modality": "TEXT", "token_count": 3},
    ]
    assert record["total_tokens"] == 29


def test_non_live_candidate_token_details_are_normalized(tmp_path):
    record = TelemetryStore(tmp_path / "usage.json").record(
        model="text", request_type="text", success=True,
        usage_metadata={"candidatesTokensDetails": [
            {"modality": "MediaModality.TEXT", "tokenCount": 7},
        ]},
    )
    assert record["output_tokens_details"] == [{"modality": "TEXT", "token_count": 7}]


def test_input_cache_and_tool_modalities_are_normalized_without_inventing_values(tmp_path):
    record = TelemetryStore(tmp_path / "usage.json").record(
        model="m", request_type="text", success=True,
        usage_metadata={
            "prompt_tokens_details": [{"modality": FakeMediaModality.TEXT, "token_count": 8}],
            "cacheTokensDetails": [{"modality": "AUDIO", "tokenCount": 2}],
            "tool_use_prompt_tokens_details": [SimpleNamespace(modality="TEXT", token_count=1)],
            "total_token_count": 17,
        },
    )
    assert record["prompt_tokens_details"] == [{"modality": "TEXT", "token_count": 8}]
    assert record["cache_tokens_details"] == [{"modality": "AUDIO", "token_count": 2}]
    assert record["tool_use_prompt_tokens_details"] == [{"modality": "TEXT", "token_count": 1}]
    assert record["output_tokens_details"] is None
    assert record["visible_output_tokens"] is None
    assert record["total_tokens"] == 17


def test_telemetry_diagnostics_are_sanitized(tmp_path):
    store = TelemetryStore(tmp_path / "usage.json")
    record = store.record(
        model="live-model", request_type="live", success=True,
        diagnostics={
            "logical_session_id": "random-id", "turn_index": 2,
            "resumption_handle_hash": "abc123",
            "resumption_handle": "must-not-be-stored", "transcript": "private content",
        },
    )
    assert record["diagnostics"] == {
        "logical_session_id": "random-id", "turn_index": 2,
        "resumption_handle_hash": "abc123",
    }
    persisted = (tmp_path / "usage.json").read_text(encoding="utf-8")
    assert "private content" not in persisted
    assert "must-not-be-stored" not in persisted


def test_usage_distinguishes_provider_tool_and_confirmation_outcomes(tmp_path):
    store = TelemetryStore(tmp_path / "usage.json")
    store.record(model="m", request_type="live", success=False)
    store.record(
        model="m", request_type="live_tool_routing", success=False, retry_count=1,
        diagnostics={"tool_outcome": "gateway_rejection", "tool_retry": 1},
    )
    store.record(
        model="m", request_type="live_tool_call", success=False,
        diagnostics={"tool_outcome": "tool_execution_error"},
    )
    store.record(
        model="m", request_type="live_tool_call", success=True,
        diagnostics={"tool_outcome": "confirmation_denied"},
    )
    summary = store.query()
    assert summary["integration_errors"] == 1
    assert summary["tool_rejections"] == 1
    assert summary["tool_errors"] == 1
    assert summary["tool_retries"] == 1
    assert summary["confirmation_denials"] == 1
    assert summary["requests"] == 1
    assert summary["internal_events"] == 3
    assert summary["telemetry_events"] == 4


def test_live_turn_diagnostics_derive_modalities_and_compression_state(tmp_path):
    store = TelemetryStore(tmp_path / "usage.json")
    record = store.record(
        model="m", request_type="live", success=True,
        usage_metadata={
            "prompt_token_count": 6100,
            "prompt_tokens_details": [
                {"modality": "TEXT", "token_count": 700},
                {"modality": "AUDIO", "token_count": 5400},
            ],
            "tool_use_prompt_token_count": 21,
            "total_token_count": 6200,
        },
        diagnostics={
            "compression_trigger_tokens": 6000,
            "compression_target_tokens": 3000,
        },
    )
    diagnostics = record["diagnostics"]
    assert diagnostics["context_compression_configured"] is True
    assert diagnostics["compression_threshold_crossed"] is True
    assert diagnostics["compression_provider_confirmed"] is None
    assert diagnostics["prompt_text_tokens"] == 700
    assert diagnostics["prompt_audio_tokens"] == 5400
    assert diagnostics["prompt_tool_tokens"] == 21
