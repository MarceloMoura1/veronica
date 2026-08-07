"""Closed, reversible tool routing for Gemini Live.

The model may select only a gateway and an allowlisted action.  Handler lookup
remains in ``ada.AudioLoop``; this module never resolves Python callables from
model-controlled strings.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


LEGACY_MODE = "legacy"
GATEWAY_MODE = "gateway"
HYBRID_MODE = "hybrid"
LIVE_TOOL_MODE_ENV = "LIVE_TOOL_MODE"
MAX_GATEWAY_PAYLOAD_BYTES = 16 * 1024
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

GATEWAY_ACTIONS = {
    "memory_action": (
        "retrieve_memory", "add_entity_alias", "add_entity_relation",
    ),
    "engineering_action": (
        "generate_cad", "iterate_cad", "print_stl", "discover_printers",
        "get_print_status",
    ),
    "device_action": ("control_light", "list_smart_devices"),
    "workspace_action": (
        "run_web_agent", "write_file", "read_file", "read_directory",
        "create_project", "switch_project", "list_projects",
        "test_integration_connection", "get_integration_status",
        "get_integration_usage", "get_integration_reports",
    ),
}

HYBRID_GATEWAY_ACTIONS = {
    "memory_admin_action": ("add_entity_alias", "add_entity_relation"),
    "engineering_action": GATEWAY_ACTIONS["engineering_action"],
    "device_action": GATEWAY_ACTIONS["device_action"],
    "workspace_action": GATEWAY_ACTIONS["workspace_action"],
}

READ_ONLY_ACTIONS = {
    "retrieve_memory", "discover_printers", "get_print_status",
    "list_smart_devices", "read_file", "read_directory", "list_projects",
    "test_integration_connection", "get_integration_status",
    "get_integration_usage", "get_integration_reports",
}
AUTO_ALLOW_ACTIONS = {
    "retrieve_memory", "test_integration_connection", "get_integration_status",
    "get_integration_usage", "get_integration_reports",
}

GATEWAY_DESCRIPTIONS = {
    "memory_action": "Memory retrieval or validated entity metadata update.",
    "engineering_action": "CAD and 3D-printer operation.",
    "device_action": "Smart-device listing or control.",
    "workspace_action": "Web, file, project, or integration operation.",
}
HYBRID_GATEWAY_DESCRIPTIONS = {
    **GATEWAY_DESCRIPTIONS,
    "memory_admin_action": "Administrative memory writes; never use for retrieval.",
    "workspace_action": "Workspace/current integration operations; create_project arguments JSON uses only name.",
}


class ToolRoutingError(ValueError):
    """Safe validation failure for a model-supplied tool call."""

    def __init__(self, code: str, message: str, *, field_names: Iterable[Any] = ()):
        super().__init__(message)
        self.code = code
        self.field_names = tuple(
            name for name in field_names
            if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name)
        )[:12]


@dataclass(frozen=True)
class ActionSpec:
    name: str
    domain: str
    schema: Mapping[str, Any]
    handler: str
    permission_key: str
    confirmation_required: bool
    timeout_seconds: int
    nature: str
    idempotent: bool
    result_sanitizer: str = "bounded_result"
    error_sanitizer: str = "safe_error"


@dataclass(frozen=True)
class RoutedToolCall:
    external_name: str
    canonical_name: str
    call_id: str | None
    request_id: str | None
    args: Mapping[str, Any]
    payload_size: int
    arguments_container: str


def resolve_tool_mode(value: str | None = None) -> tuple[str, bool]:
    raw = os.getenv(LIVE_TOOL_MODE_ENV) if value is None else value
    normalized = (raw or LEGACY_MODE).strip().lower()
    if normalized in {LEGACY_MODE, GATEWAY_MODE, HYBRID_MODE}:
        return normalized, False
    return LEGACY_MODE, True


def build_action_registry(declarations: Iterable[Mapping[str, Any]]) -> dict[str, ActionSpec]:
    by_name = {item["name"]: item for item in declarations}
    expected = {action for actions in GATEWAY_ACTIONS.values() for action in actions}
    if set(by_name) != expected:
        missing, extra = sorted(expected - set(by_name)), sorted(set(by_name) - expected)
        raise RuntimeError(f"Live action registry mismatch: missing={missing} extra={extra}")
    registry: dict[str, ActionSpec] = {}
    for domain, actions in GATEWAY_ACTIONS.items():
        for action in actions:
            registry[action] = ActionSpec(
                name=action,
                domain=domain,
                schema=by_name[action]["parameters"],
                handler=action,
                permission_key=action,
                confirmation_required=action not in AUTO_ALLOW_ACTIONS,
                timeout_seconds=60,
                nature="read" if action in READ_ONLY_ACTIONS else "write",
                idempotent=action in READ_ONLY_ACTIONS,
            )
    return registry


def build_gateway_declarations(
    gateway_actions: Mapping[str, tuple[str, ...]] = GATEWAY_ACTIONS,
) -> list[dict[str, Any]]:
    declarations = []
    for gateway, actions in gateway_actions.items():
        argument_schema = {"type": "STRING"}
        declarations.append({
            "name": gateway,
            "description": HYBRID_GATEWAY_DESCRIPTIONS[gateway],
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "action": {"type": "STRING", "enum": list(actions)},
                    "arguments": argument_schema,
                },
                "required": ["action", "arguments"],
            },
        })
    return declarations


def schema_metrics(declarations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(declarations)
    serialized = json.dumps(items, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return {
        "count": len(items),
        "chars": len(serialized),
        "estimated_tokens": (len(serialized) + 3) // 4,
        "hash": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    }


def route_gateway_call(
    gateway: str,
    call_id: str | None,
    payload: Mapping[str, Any],
    registry: Mapping[str, ActionSpec],
    gateway_actions: Mapping[str, tuple[str, ...]] = GATEWAY_ACTIONS,
) -> RoutedToolCall:
    if gateway not in gateway_actions:
        raise ToolRoutingError("unknown_gateway", "Unknown Live tool gateway.")
    if not isinstance(payload, Mapping):
        raise ToolRoutingError("invalid_payload", "Gateway payload must be an object.")
    extra = set(payload) - {"action", "arguments", "request_id"}
    if extra:
        raise ToolRoutingError("extra_gateway_field", "Gateway payload contains unsupported fields.")
    action = payload.get("action")
    if not isinstance(action, str):
        raise ToolRoutingError("invalid_action", "Gateway action must be a string.")
    if action not in gateway_actions[gateway]:
        raise ToolRoutingError("action_domain_mismatch", "Action is not allowed for this gateway.")
    if action in {"eval", "exec", "__import__"} or action not in registry:
        raise ToolRoutingError("unknown_action", "Unknown gateway action.")
    request_id = payload.get("request_id")
    if request_id is not None and (
        not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id)
    ):
        raise ToolRoutingError("invalid_request_id", "Invalid gateway request_id.")
    raw_arguments = payload.get("arguments")
    if isinstance(raw_arguments, str):
        arguments_container = "json_string"
        payload_size = len(raw_arguments.encode("utf-8"))
    elif isinstance(raw_arguments, Mapping):
        arguments_container = "mapping"
        try:
            encoded_arguments = json.dumps(raw_arguments, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ToolRoutingError("invalid_arguments", "Gateway arguments must be JSON-compatible.") from exc
        payload_size = len(encoded_arguments.encode("utf-8"))
    else:
        raise ToolRoutingError("invalid_arguments", "Gateway arguments must be a JSON string or object.")
    if payload_size > MAX_GATEWAY_PAYLOAD_BYTES:
        raise ToolRoutingError("payload_too_large", "Gateway arguments exceed the size limit.")
    if arguments_container == "json_string":
        try:
            arguments = json.loads(raw_arguments, object_pairs_hook=_reject_duplicate_keys)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ToolRoutingError("invalid_json", "Gateway arguments are not valid JSON.") from exc
    else:
        arguments = dict(raw_arguments)
    if not isinstance(arguments, dict):
        raise ToolRoutingError("invalid_arguments", "Gateway arguments must decode to an object.")
    _validate_arguments(arguments, registry[action].schema)
    return RoutedToolCall(gateway, action, call_id, request_id, arguments, payload_size, arguments_container)


def _reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ToolRoutingError("duplicate_action_field", "Action arguments contain duplicate fields.")
        value[key] = item
    return value


def _validate_arguments(arguments: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    properties = schema.get("properties", {})
    extra = set(arguments) - set(properties)
    if extra:
        raise ToolRoutingError(
            "extra_action_field", "Action arguments contain unsupported fields.",
            field_names=sorted(extra, key=str),
        )
    missing = set(schema.get("required", [])) - set(arguments)
    if missing:
        raise ToolRoutingError(
            "missing_action_field", "Action arguments are missing required fields.",
            field_names=sorted(missing),
        )
    for name, value in arguments.items():
        expected = properties[name].get("type")
        valid = {
            "STRING": lambda item: isinstance(item, str),
            "NUMBER": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "INTEGER": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "BOOLEAN": lambda item: isinstance(item, bool),
            "ARRAY": lambda item: isinstance(item, list),
            "OBJECT": lambda item: isinstance(item, dict),
        }.get(expected, lambda item: True)(value)
        if not valid:
            raise ToolRoutingError("invalid_action_type", f"Invalid type for action field '{name}'.")
        enum = properties[name].get("enum")
        if enum is not None and value not in enum:
            raise ToolRoutingError("invalid_action_value", f"Invalid value for action field '{name}'.")


def safe_routing_error(error: ToolRoutingError, *, retryable: bool | None = None) -> dict[str, Any]:
    if retryable is None:
        retryable = error.code in {
            "invalid_arguments", "invalid_json", "missing_action_field",
            "extra_action_field", "invalid_action_type", "invalid_action_value",
        }
    return {"ok": False, "error": {"code": error.code, "retryable": retryable}}
