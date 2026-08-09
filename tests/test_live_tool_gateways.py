"""Regression and security tests for compact Gemini Live tool gateways."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from google import genai
from google.genai import _live_converters, types

import ada
from live_tools import (
    DIRECT_HYBRID_TOOLS, GATEWAY_ACTIONS, GATEWAY_MODE, HYBRID_GATEWAY_ACTIONS, HYBRID_MODE,
    LEGACY_MODE, ToolRoutingError, resolve_tool_mode,
    route_gateway_call, safe_routing_error, schema_metrics,
)


LEGACY_HASH = "2649bc2f3e58f86a103e8697b31f186d0b6c5c2463034b9c3732a34084097d79"


def _route(gateway, action, arguments=None, **extra):
    payload = {
        "action": action,
        "arguments": json.dumps(arguments or {}, ensure_ascii=False),
        **extra,
    }
    return route_gateway_call(gateway, "call-1", payload, ada.ACTION_REGISTRY)


def test_missing_mode_defaults_to_legacy(monkeypatch):
    monkeypatch.delenv("LIVE_TOOL_MODE", raising=False)
    assert resolve_tool_mode() == (LEGACY_MODE, False)
    selected, mode, invalid = ada.tools_for_mode()
    assert selected is ada.tools and mode == LEGACY_MODE and invalid is False


def test_invalid_mode_falls_back_safely(monkeypatch):
    monkeypatch.setenv("LIVE_TOOL_MODE", "unsafe")
    selected, mode, invalid = ada.tools_for_mode()
    assert selected is ada.tools and mode == LEGACY_MODE and invalid is True


def test_legacy_mode_is_byte_stable_and_keeps_provider_search():
    selected, _, _ = ada.tools_for_mode("legacy")
    canonical = json.dumps(selected, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    assert len(selected[1]["function_declarations"]) == 21
    assert selected[0] == {"google_search": {}}
    assert hashlib.sha256(canonical.encode()).hexdigest() == LEGACY_HASH


def test_gateway_mode_has_four_declarations_and_provider_search():
    selected, _, _ = ada.tools_for_mode("gateway")
    assert selected[0] == {"google_search": {}}
    assert [item["name"] for item in selected[1]["function_declarations"]] == list(GATEWAY_ACTIONS)


def test_hybrid_mode_exposes_three_direct_core_tools_and_four_gateways():
    selected, mode, invalid = ada.tools_for_mode("hybrid")
    declarations = selected[1]["function_declarations"]
    assert mode == HYBRID_MODE and invalid is False
    assert selected[0] == {"google_search": {}}
    assert [item["name"] for item in declarations] == [
        *DIRECT_HYBRID_TOOLS, *HYBRID_GATEWAY_ACTIONS,
    ]
    assert declarations[0] is ada.tools[1]["function_declarations"][
        next(i for i, item in enumerate(ada.tools[1]["function_declarations"]) if item["name"] == "retrieve_memory")
    ]
    assert declarations[0] == ada._legacy_declarations_by_name["retrieve_memory"]


def test_hybrid_mapping_covers_gateways_plus_direct_core_tools_once():
    mapped = [action for actions in HYBRID_GATEWAY_ACTIONS.values() for action in actions]
    assert len(mapped) == len(set(mapped)) == 20
    assert not set(DIRECT_HYBRID_TOOLS) & set(mapped)
    assert set(mapped) | (set(DIRECT_HYBRID_TOOLS) - {"set_voice_output"}) == set(ada.ACTION_REGISTRY)
    assert HYBRID_GATEWAY_ACTIONS["memory_admin_action"] == (
        "add_entity_alias", "add_entity_relation",
    )


def test_memory_admin_rejects_retrieval_as_cross_domain_action():
    with pytest.raises(ToolRoutingError) as captured:
        route_gateway_call(
            "memory_admin_action", "1",
            {"action": "retrieve_memory", "arguments": '{"query":"fixture"}'},
            ada.ACTION_REGISTRY, HYBRID_GATEWAY_ACTIONS,
        )
    assert captured.value.code == "action_domain_mismatch"


def test_all_internal_actions_are_mapped_exactly_once():
    mapped = [action for actions in GATEWAY_ACTIONS.values() for action in actions]
    assert len(mapped) == len(set(mapped)) == 23
    assert set(mapped) == set(ada.ACTION_REGISTRY)
    assert all(spec.handler == spec.name for spec in ada.ACTION_REGISTRY.values())


def test_incident_actions_are_direct_read_only_and_absent_from_workspace_gateway():
    assert "list_system_incidents" not in HYBRID_GATEWAY_ACTIONS["workspace_action"]
    assert "get_incident_details" not in HYBRID_GATEWAY_ACTIONS["workspace_action"]
    assert ada.ACTION_REGISTRY["list_system_incidents"].confirmation_required is False
    assert ada.ACTION_REGISTRY["get_incident_details"].nature == "read"


def test_gateway_routes_to_canonical_action_and_preserves_correlation():
    routed = _route("memory_action", "retrieve_memory", {"query": "safe fixture"}, request_id="req-1")
    assert routed.canonical_name == "retrieve_memory"
    assert routed.external_name == "memory_action"
    assert routed.call_id == "call-1" and routed.request_id == "req-1"
    assert routed.args == {"query": "safe fixture"}


def test_workspace_create_project_accepts_sdk_mapping_arguments():
    function_call = types.FunctionCall(
        id="sdk-call", name="workspace_action",
        args={"action": "create_project", "arguments": {"name": "Fixture"}},
    )
    loop = ada.AudioLoop.__new__(ada.AudioLoop)
    loop._active_tool_mode = HYBRID_MODE
    loop.integration_manager = None
    internal, routed, error = loop._prepare_live_tool_call(function_call)
    assert error is None
    assert internal.id == "sdk-call" and internal.name == "create_project"
    assert internal.args == {"name": "Fixture"}
    assert routed.arguments_container == "mapping"
    assert ada.ACTION_REGISTRY[internal.name].confirmation_required is True


def test_workspace_create_project_keeps_valid_json_string_support():
    routed = _route("workspace_action", "create_project", {"name": "Fixture"})
    assert routed.canonical_name == "create_project"
    assert routed.arguments_container == "json_string"


def test_mapping_arguments_keep_closed_schema_validation():
    with pytest.raises(ToolRoutingError) as captured:
        route_gateway_call(
            "workspace_action", "call-1",
            {"action": "create_project", "arguments": {"name": "Fixture", "extra": True}},
            ada.ACTION_REGISTRY,
        )
    assert captured.value.code == "extra_action_field"


@pytest.mark.parametrize("gateway,action,code", [
    ("memory_action", "eval", "action_domain_mismatch"),
    ("memory_action", "__import__", "action_domain_mismatch"),
    ("memory_action", "write_file", "action_domain_mismatch"),
    ("not_a_module", "retrieve_memory", "unknown_gateway"),
])
def test_unknown_cross_domain_and_code_like_actions_are_rejected(gateway, action, code):
    with pytest.raises(ToolRoutingError, match="Unknown|not allowed") as captured:
        _route(gateway, action)
    assert captured.value.code == code


def test_invalid_json_extra_missing_and_wrong_types_are_rejected():
    with pytest.raises(ToolRoutingError) as captured:
        route_gateway_call("memory_action", "1", {"action": "retrieve_memory", "arguments": "{"}, ada.ACTION_REGISTRY)
    assert captured.value.code == "invalid_json"
    with pytest.raises(ToolRoutingError) as captured:
        _route("memory_action", "retrieve_memory", {"query": "x", "module": "os"})
    assert captured.value.code == "extra_action_field"
    with pytest.raises(ToolRoutingError) as captured:
        _route("memory_action", "retrieve_memory", {})
    assert captured.value.code == "missing_action_field"
    with pytest.raises(ToolRoutingError) as captured:
        _route("memory_action", "retrieve_memory", {"query": 7})
    assert captured.value.code == "invalid_action_type"
    with pytest.raises(ToolRoutingError) as captured:
        route_gateway_call(
            "memory_action", "1",
            {"action": "retrieve_memory", "arguments": '{"query":"a","query":"b"}'},
            ada.ACTION_REGISTRY,
        )
    assert captured.value.code == "duplicate_action_field"


def test_oversized_payload_and_invalid_request_id_are_rejected():
    with pytest.raises(ToolRoutingError) as captured:
        _route("memory_action", "retrieve_memory", {"query": "x" * 17000})
    assert captured.value.code == "payload_too_large"
    with pytest.raises(ToolRoutingError) as captured:
        _route("memory_action", "retrieve_memory", {"query": "x"}, request_id="bad request")
    assert captured.value.code == "invalid_request_id"


def test_routing_error_is_structured_repairable_and_content_free():
    error = ToolRoutingError("invalid_arguments", "private payload value")
    payload = safe_routing_error(error)
    assert payload == {
        "ok": False,
        "error": {"code": "invalid_arguments", "retryable": True},
    }
    assert "private payload value" not in json.dumps(payload)


def test_external_response_uses_gateway_name_id_and_action():
    loop = ada.AudioLoop.__new__(ada.AudioLoop)
    internal = types.FunctionResponse(id="call-1", name="retrieve_memory", response={"result": "ok"})
    routed = _route("memory_action", "retrieve_memory", {"query": "fixture"}, request_id="req-1")
    response = loop._externalize_tool_responses([internal], [routed])[0]
    assert response.id == "call-1" and response.name == "memory_action"
    assert response.response == {"result": "ok", "action": "retrieve_memory", "request_id": "req-1"}


def test_multiple_out_of_order_responses_remain_correlated():
    first = _route("workspace_action", "read_file", {"path": "a.txt"})
    second_payload = {"action": "list_projects", "arguments": "{}"}
    second = route_gateway_call("workspace_action", "call-2", second_payload, ada.ACTION_REGISTRY)
    responses = [
        types.FunctionResponse(id="call-2", name="list_projects", response={"result": []}),
        types.FunctionResponse(id="call-1", name="read_file", response={"result": "ok"}),
    ]
    external = ada.AudioLoop._externalize_tool_responses(responses, [first, second])
    assert [(item.id, item.name, item.response["action"]) for item in external] == [
        ("call-2", "workspace_action", "list_projects"),
        ("call-1", "workspace_action", "read_file"),
    ]


def test_gateway_schema_reduces_external_schema_by_at_least_55_percent():
    legacy = schema_metrics(ada.tools[1]["function_declarations"])
    gateway = schema_metrics(ada.gateway_tools[1]["function_declarations"])
    assert gateway["count"] == 4
    assert gateway["chars"] <= legacy["chars"] * 0.50


@pytest.mark.parametrize("mode", ["legacy", "gateway", "hybrid"])
def test_real_sdk_converter_accepts_complete_live_config(mode):
    client = genai.Client(api_key="test-key", http_options={"api_version": "v1beta"})
    parent = {}
    try:
        converted = _live_converters._LiveConnectConfig_to_mldev(
            client._api_client, ada.build_live_config(tool_mode=mode), parent
        )
    finally:
        client.close()
    assert isinstance(converted, dict)
    expected = {"legacy": 21, "gateway": 4, "hybrid": 8}[mode]
    assert len(parent["setup"]["tools"][1]["functionDeclarations"]) == expected
    if mode == "hybrid":
        names = [item.name for item in parent["setup"]["tools"][1]["functionDeclarations"]]
        assert names[:len(DIRECT_HYBRID_TOOLS)] == list(DIRECT_HYBRID_TOOLS)


def test_gateway_preparation_does_not_resolve_arbitrary_callables():
    loop = ada.AudioLoop.__new__(ada.AudioLoop)
    loop._active_tool_mode = GATEWAY_MODE
    call = SimpleNamespace(
        id="1", name="workspace_action",
        args={"action": "read_file", "arguments": '{"path":"fixture.txt"}'},
    )
    internal, routed, error = loop._prepare_live_tool_call(call)
    assert error is None and internal.name == routed.canonical_name == "read_file"
    assert not hasattr(internal, "module") and not callable(internal.name)


@pytest.mark.parametrize("name,args", [
    ("retrieve_memory", {"query": "fixture"}),
    ("list_system_incidents", {"severity": "grave"}),
    ("get_incident_details", {"incident_id": "synthetic-id"}),
])
def test_hybrid_direct_tools_bypass_gateway_and_preserve_name_and_id(name, args):
    loop = ada.AudioLoop.__new__(ada.AudioLoop)
    loop._active_tool_mode = HYBRID_MODE
    call = types.FunctionCall(id="sdk-direct-call", name=name, args=args)
    internal, routed, error = loop._prepare_live_tool_call(call)
    assert internal is call and routed is None and error is None
    response = types.FunctionResponse(
        id=internal.id, name=internal.name, response={"result": "sanitized fixture"}
    )
    assert response.id == "sdk-direct-call" and response.name == name


@pytest.mark.parametrize("name", ["list_system_incidents", "get_incident_details"])
def test_direct_incident_tools_are_auto_allowed(name):
    loop = ada.AudioLoop.__new__(ada.AudioLoop)
    loop.permissions = {name: True}
    loop.on_tool_confirmation = lambda data: (_ for _ in ()).throw(AssertionError("confirmation requested"))
    call = types.FunctionCall(id="sdk-direct-call", name=name, args={})
    assert asyncio.run(loop._authorize_live_tool_call(call)) is True


def test_hybrid_system_instruction_keeps_direct_memory_guidance():
    instruction = ada.system_instruction_for_mode("hybrid")
    assert "call retrieve_memory" in instruction
    assert "memory_action with action retrieve_memory" not in instruction
    assert "MegaDesk" not in instruction
    assert "list_system_incidents" in instruction
    assert "get_incident_details only" in instruction


def test_tool_contract_separates_persistent_memory_from_current_operations():
    declarations = {
        item["name"]: item for item in ada.hybrid_tools[1]["function_declarations"]
    }
    assert "memory" in declarations["retrieve_memory"]["description"].lower()
    workspace = declarations["workspace_action"]["description"].lower()
    assert "current integration" in workspace
    assert "create_project" in workspace and "name" in workspace
    assert "gemini" not in workspace


def test_hybrid_build_failure_falls_back_to_legacy(monkeypatch):
    monkeypatch.setattr(ada, "build_hybrid_tools", lambda: (_ for _ in ()).throw(RuntimeError("fixture")))
    selected, mode, invalid = ada.tools_for_mode("hybrid")
    assert selected is ada.tools and mode == LEGACY_MODE and invalid is True


def test_tool_telemetry_contains_metadata_but_not_payload():
    class FakeManager:
        def __init__(self):
            self.records = []

        def record_usage(self, *args, **kwargs):
            self.records.append((args, kwargs))

    loop = ada.AudioLoop.__new__(ada.AudioLoop)
    loop.integration_manager = FakeManager()
    loop.permissions = {"read_file": False}
    routed = _route(
        "workspace_action", "read_file",
        {"path": "private/fixture.txt"}, request_id="private-request-id",
    )
    response = types.FunctionResponse(id="call-1", name="read_file", response={"result": "private content"})
    loop._record_gateway_tool_telemetry([routed], [response], 12)
    diagnostics = loop.integration_manager.records[0][1]["diagnostics"]
    serialized = json.dumps(diagnostics)
    assert diagnostics["gateway"] == "workspace_action"
    assert diagnostics["canonical_action"] == "read_file"
    assert diagnostics["confirmation_outcome"] == "not_required"
    assert diagnostics["tool_payload_bytes"] > 0 and diagnostics["tool_result_bytes"] > 0
    assert "private/fixture.txt" not in serialized
    assert "private content" not in serialized
    assert "private-request-id" not in serialized


def test_gateway_rejection_telemetry_is_sanitized_and_retry_is_bounded():
    class FakeManager:
        def __init__(self):
            self.records = []

        def record_usage(self, *args, **kwargs):
            self.records.append(kwargs)

    loop = ada.AudioLoop.__new__(ada.AudioLoop)
    loop._active_tool_mode = HYBRID_MODE
    loop.integration_manager = FakeManager()
    call = SimpleNamespace(
        id="private-id", name="workspace_action",
        args={"action": "create_project", "arguments": {"name": 123}},
    )
    first = loop._prepare_live_tool_call(call)[2]
    second = loop._prepare_live_tool_call(call)[2]
    assert first.response["error"]["retryable"] is True
    assert second.response["error"]["retryable"] is False
    first_diag = loop.integration_manager.records[0]["diagnostics"]
    second_diag = loop.integration_manager.records[1]["diagnostics"]
    assert first_diag["tool_outcome"] == "gateway_rejection"
    assert first_diag["arguments_container"] == "mapping"
    assert first_diag["reason_code"] == "invalid_action_type"
    assert first_diag["tool_retry"] == 0 and second_diag["tool_retry"] == 1
    serialized = json.dumps(loop.integration_manager.records)
    assert "private-id" not in serialized and '"name": 123' not in serialized


def test_extra_field_telemetry_records_only_sanitized_field_name():
    class FakeManager:
        def __init__(self):
            self.records = []

        def record_usage(self, *args, **kwargs):
            self.records.append(kwargs)

    loop = ada.AudioLoop.__new__(ada.AudioLoop)
    loop._active_tool_mode = HYBRID_MODE
    loop.integration_manager = FakeManager()
    call = SimpleNamespace(
        id="call-sensitive", name="workspace_action",
        args={
            "action": "create_project",
            "arguments": '{"name":"private value","project_name":"private value"}',
        },
    )
    error = loop._prepare_live_tool_call(call)[2]
    diagnostics = loop.integration_manager.records[0]["diagnostics"]
    assert error.response["error"]["code"] == "extra_action_field"
    assert diagnostics["field_names"] == "project_name"
    assert "private value" not in json.dumps(diagnostics)


def test_confirmation_denial_is_not_classified_as_provider_error():
    class FakeManager:
        def __init__(self):
            self.records = []

        def record_usage(self, *args, **kwargs):
            self.records.append(kwargs)

    loop = ada.AudioLoop.__new__(ada.AudioLoop)
    loop.integration_manager = FakeManager()
    loop.permissions = {"create_project": True}
    routed = _route("workspace_action", "create_project", {"name": "Fixture"})
    response = types.FunctionResponse(
        id="call-1", name="create_project",
        response={"result": "User denied the request to use this tool."},
    )
    loop._record_gateway_tool_telemetry([routed], [response], 4)
    record = loop.integration_manager.records[0]
    assert record["success"] is True
    assert record["diagnostics"]["tool_outcome"] == "confirmation_denied"


def test_real_sdk_create_project_reaches_confirmation_and_denial_skips_handler():
    loop = ada.AudioLoop.__new__(ada.AudioLoop)
    loop._active_tool_mode = HYBRID_MODE
    loop.integration_manager = None
    loop.permissions = {"create_project": True}
    loop._pending_confirmations = {}
    confirmations = []
    handler_calls = []

    def deny(data):
        confirmations.append({key: data[key] for key in ("gateway", "tool")})
        loop.resolve_tool_confirmation(data["id"], False)

    loop.on_tool_confirmation = deny
    external = types.FunctionCall(
        id="sdk-create-call", name="workspace_action",
        args={"action": "create_project", "arguments": '{"name":"Fixture"}'},
    )
    internal, routed, error = loop._prepare_live_tool_call(external)
    assert error is None and internal.name == "create_project"

    async def guarded_dispatch():
        if await loop._authorize_live_tool_call(internal, routed):
            handler_calls.append(internal.name)

    asyncio.run(guarded_dispatch())
    assert confirmations == [{"gateway": "workspace_action", "tool": "create_project"}]
    assert handler_calls == []


def test_direct_memory_telemetry_omits_query_entities_and_content():
    class FakeManager:
        def __init__(self):
            self.records = []

        def record_usage(self, *args, **kwargs):
            self.records.append(kwargs)

    loop = ada.AudioLoop.__new__(ada.AudioLoop)
    loop.integration_manager = FakeManager()
    loop._active_tool_mode = HYBRID_MODE
    result = {
        "route": "entity_lookup", "item_count": 2,
        "context": "private fixture content",
        "entity": "private fixture entity",
        "context_diagnostics": {"components": [{"estimated_tokens": 9}]},
    }
    loop._record_direct_memory_telemetry(result, 7)
    record = loop.integration_manager.records[0]
    serialized = json.dumps(record)
    assert record["diagnostics"]["memory_tool"] == "retrieve_memory"
    assert record["diagnostics"]["memory_category"] == "entity_lookup"
    assert record["diagnostics"]["memory_item_count"] == 2
    assert "private fixture content" not in serialized
    assert "private fixture entity" not in serialized


def test_hybrid_schema_stays_at_least_twenty_five_percent_below_legacy():
    legacy = schema_metrics(ada.tools[1]["function_declarations"])
    hybrid = schema_metrics(ada.hybrid_tools[1]["function_declarations"])
    assert hybrid["count"] == 8
    assert hybrid["chars"] <= legacy["chars"] * 0.75
