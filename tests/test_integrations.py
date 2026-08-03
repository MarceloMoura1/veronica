import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from integrations import IntegrationEventStore, IntegrationManager, IntegrationState, TelemetryStore


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
        events_path=tmp_path / "events.json",
        env_path=tmp_path / ".env",
        api_key=api_key,
        client_factory=lambda _key: FakeClient(error),
        event_callback=callback,
    )


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


def test_details_and_tools_use_the_same_integration_telemetry(tmp_path):
    integration_manager = manager(tmp_path, api_key="secret")
    integration_manager.record_usage(
        {"prompt_token_count": 4, "response_token_count": 6, "total_token_count": 10},
        integration_id="gemini",
        request_type="live",
    )
    assert integration_manager.tool_usage("gemini") == integration_manager.get_details("gemini")["usage"]
    assert integration_manager.get_status("gemini")["metadata"]["sdk_name"] == "google-genai"
