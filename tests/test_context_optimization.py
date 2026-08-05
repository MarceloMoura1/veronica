"""Configuration-level regression tests for sustainable Gemini Live context."""
import json
import hashlib
import asyncio
from pathlib import Path

import ada
import pytest
from google.genai import _live_converters
from live_session import LiveSessionState


class _FakeSession:
    def __init__(self, fail_once=False):
        self.sent = []
        self.fail_once = fail_once

    async def send(self, **payload):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("send failed")
        self.sent.append(payload)


class _FakeAnalyzer:
    def build_cold_start_context(self, **kwargs):
        return "compact state", {
            "before_chars": 1000, "after_chars": 13, "estimated_tokens": 4,
            "deduplicated_items": 2, "omitted_by_budget": 0,
            "preserved_references": 1, "has_summary": True,
            "has_important_turns": False, "recent_reference_count": 1,
        }


def _bare_audio_loop(session=None, handle=None):
    loop = ada.AudioLoop.__new__(ada.AudioLoop)
    loop.live_session = LiveSessionState(resumption_handle=handle)
    loop.live_session.begin_connection()
    loop.session = session or _FakeSession()
    loop.conversational_memory_analyzer = _FakeAnalyzer()
    loop._cold_start_diagnostics = {}
    loop._manual_restoration_count = 0
    loop._fallback_pending = False
    return loop


def test_live_static_context_is_compact_and_keeps_all_tools():
    diagnostics = ada.static_context_diagnostics()
    assert diagnostics["function_tool_count"] == 21
    assert diagnostics["google_search_present"] is True
    assert diagnostics["tool_schema_chars"] <= 3400
    assert diagnostics["system_instruction_estimated_tokens"] + diagnostics["tool_schema_estimated_tokens"] <= 1100
    canonical_tools = json.dumps(ada.tools, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    assert hashlib.sha256(canonical_tools.encode()).hexdigest() == "2649bc2f3e58f86a103e8697b31f186d0b6c5c2463034b9c3732a34084097d79"


def test_tool_contracts_survive_compaction():
    declarations = ada.tools[1]["function_declarations"]
    by_name = {item["name"]: item for item in declarations}
    assert by_name["retrieve_memory"]["parameters"]["required"] == ["query"]
    assert by_name["add_entity_alias"]["parameters"]["required"] == ["canonical_name", "alias"]
    assert by_name["add_entity_relation"]["parameters"]["required"] == [
        "source_entity", "relation_type", "target_entity", "status",
        "summary", "source", "confidence", "importance",
    ]
    assert set(by_name["control_light"]["parameters"]["required"]) == {"target", "action"}
    assert by_name["generate_cad"]["behavior"] == "NON_BLOCKING"
    assert "add_entity_relation" not in str(ada.config.system_instruction)
    source = (Path(__file__).parents[1] / "backend" / "ada.py").read_text(encoding="utf-8")
    assert 'confirmation_required = False if fc.name == "retrieve_memory" else self.permissions.get(fc.name, True)' in source


def test_live_history_has_bounded_progressive_compression():
    diagnostics = ada.static_context_diagnostics()
    assert diagnostics["compression_target_tokens"] == 3000
    assert diagnostics["compression_trigger_tokens"] == 6000
    assert ada.config.context_window_compression is not None
    assert ada.config.context_window_compression.trigger_tokens == 6000
    assert ada.config.context_window_compression.sliding_window.target_tokens == 3000
    assert ada.config.session_resumption.transparent is None


def test_developer_api_can_serialize_session_resumption_config():
    """The Developer API rejects the Enterprise-only ``transparent`` field."""
    serialized = _live_converters._SessionResumptionConfig_to_mldev(
        ada.config.session_resumption
    )
    assert serialized == {}

    resumed = ada.build_live_config("opaque-handle")
    serialized = _live_converters._SessionResumptionConfig_to_mldev(
        resumed.session_resumption
    )
    assert serialized == {"handle": "opaque-handle"}


def test_static_diagnostics_never_expose_prompts_or_secrets():
    serialized = json.dumps(ada.static_context_diagnostics())
    assert str(ada.config.system_instruction) not in serialized
    assert "GEMINI_API_KEY" not in serialized


def test_audio_loop_sends_cold_start_once_and_failed_send_can_retry():
    loop = _bare_audio_loop(_FakeSession(fail_once=True))
    with pytest.raises(RuntimeError):
        asyncio.run(loop._send_cold_start_once())
    assert loop.live_session.cold_start_send_count == 0
    assert asyncio.run(loop._send_cold_start_once()) is True
    assert asyncio.run(loop._send_cold_start_once()) is False
    assert loop.live_session.cold_start_send_count == 1
    assert len(loop.session.sent) == 1


def test_resumed_connection_sends_neither_cold_start_nor_manual_fallback():
    loop = _bare_audio_loop(handle="opaque-handle")
    assert asyncio.run(loop._send_cold_start_once()) is False
    assert loop.session.sent == []
    assert loop._manual_restoration_count == 0


def test_manual_fallback_is_single_bounded_context():
    loop = _bare_audio_loop()
    loop._fallback_pending = True
    assert asyncio.run(loop._send_minimal_reconnect_fallback()) is True
    assert len(loop.session.sent) == 1
    assert loop.session.sent[0]["end_of_turn"] is False
    assert loop._manual_restoration_count == 1
    assert loop.live_session.fallback_manual_used is True
