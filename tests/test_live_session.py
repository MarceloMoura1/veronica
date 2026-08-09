import asyncio
import json
import pytest
from pathlib import Path
from google import genai
from google.genai import _common, _live_converters, live, types

from backend.live_session import LiveSessionState, compression_limits, short_handle_hash
from backend import ada


def test_session_ids_are_random_and_connections_are_distinct():
    first = LiveSessionState()
    second = LiveSessionState()
    first.begin_connection()
    prior_connection = first.connection_id
    first.begin_connection()

    assert first.logical_session_id != second.logical_session_id
    assert first.connection_id != prior_connection
    assert len(first.logical_session_id) == len(first.connection_id) == 32


def test_cold_start_is_idempotent_and_failure_can_retry():
    state = LiveSessionState()
    state.begin_connection()
    assert state.begin_cold_start() is True
    assert state.begin_cold_start() is False
    state.complete_cold_start(False)
    assert state.begin_cold_start() is True
    state.complete_cold_start(True)
    assert state.begin_cold_start() is False
    assert state.cold_start_send_count == 1


def test_resumption_prevents_cold_start_and_never_exposes_handle():
    state = LiveSessionState(resumption_handle="secret-resumption-handle")
    state.begin_connection()

    assert state.resumption_requested is True
    assert state.begin_cold_start() is False
    diagnostics = state.sanitized()
    assert diagnostics["resumption_handle_hash"] == short_handle_hash("secret-resumption-handle")
    assert "secret-resumption-handle" not in repr(diagnostics)
    assert "secret-resumption-handle" not in repr(state)


def test_resumption_updates_only_with_valid_handle():
    state = LiveSessionState(resumption_handle="old")
    state.begin_connection()
    state.update_resumption(resumable=True, new_handle="new")
    assert state.resumption_handle == "new"
    assert state.resumption_accepted is True
    state.update_resumption(resumable=False, new_handle=None)
    assert state.resumption_handle is None


def test_turn_indexes_are_deterministic_per_connection():
    state = LiveSessionState()
    state.begin_connection()
    state.complete_turn()
    state.complete_turn()
    assert (state.turn_index, state.connection_turn_index) == (2, 2)
    state.begin_connection()
    assert (state.turn_index, state.connection_turn_index) == (2, 0)


def test_compression_defaults_and_validation(monkeypatch):
    monkeypatch.delenv("GEMINI_LIVE_COMPRESSION_TRIGGER_TOKENS", raising=False)
    monkeypatch.delenv("GEMINI_LIVE_COMPRESSION_TARGET_TOKENS", raising=False)
    assert compression_limits() == (6000, 3000)

    monkeypatch.setenv("GEMINI_LIVE_COMPRESSION_TRIGGER_TOKENS", "3000")
    monkeypatch.setenv("GEMINI_LIVE_COMPRESSION_TARGET_TOKENS", "3000")
    with pytest.raises(ValueError):
        compression_limits()


def test_sessions_never_share_resumption_handles():
    first = LiveSessionState(resumption_handle="first")
    second = LiveSessionState()
    first.begin_connection()
    second.begin_connection()
    assert first.resumption_handle == "first"
    assert second.resumption_handle is None


def test_live_source_uses_official_resumption_without_ten_message_fallback():
    source = (Path(__file__).parents[1] / "backend" / "ada.py").read_text(encoding="utf-8")
    assert "types.SessionResumptionConfig" in source
    assert "session_resumption_update" in source
    assert "update.resumable" in source and "update.new_handle" in source
    assert "get_recent_chat_history(limit=10)" not in source
    assert "_send_minimal_reconnect_fallback" in source


def test_real_mldev_converter_serializes_context_compression_without_enterprise_fields():
    client = genai.Client(api_key="test-key", http_options={"api_version": "v1beta"})
    try:
        parameter_model = asyncio.run(
            live._t_live_connect_config(client._api_client, ada.build_live_config(tool_mode="hybrid"))
        )
        request = _common.convert_to_dict(
            _live_converters._LiveConnectParameters_to_mldev(
                api_client=client._api_client,
                from_object=types.LiveConnectParameters(
                    model="models/test-model", config=parameter_model,
                ).model_dump(exclude_none=True),
            )
        )
    finally:
        client.close()
    assert request["setup"]["contextWindowCompression"] == {
        "trigger_tokens": 6000,
        "sliding_window": {"target_tokens": 3000},
    }
    assert "transparent" not in json.dumps(request).lower()


def test_1011_invalidates_old_live_session_reference():
    class ClosedError(RuntimeError):
        code = 1011

    loop = ada.AudioLoop.__new__(ada.AudioLoop)
    session = object()
    loop.session = session
    loop._connection_active = True

    code, reason = loop._connection_error_details(
        ExceptionGroup("connection failed", [ClosedError("Internal error encountered")])
    )
    invalidated = loop.invalidate_live_session(session=session, error=ClosedError())

    assert code == 1011
    assert "Internal error encountered" in reason
    assert invalidated is True
    assert loop.session is None
    assert loop.live_session_available() is False


def test_stale_session_cannot_invalidate_new_connection():
    loop = ada.AudioLoop.__new__(ada.AudioLoop)
    old_session = object()
    new_session = object()
    loop.session = new_session
    loop._connection_active = True

    assert loop.invalidate_live_session(session=old_session, error=RuntimeError("late")) is False
    assert loop.session is new_session
    assert loop.live_session_available() is True


def test_existing_reconnect_loop_owns_one_task_set_per_connection():
    source = Path(ada.__file__).read_text(encoding="utf-8")
    run_source = source[source.index("    async def run(self, start_message=None):"):]
    assert "while not self.stop_event.is_set():" in run_source
    assert "retry_delay = min(retry_delay * 2, 10)" in run_source
    assert run_source.count('tg.create_task(self.listen_audio(), name="voice_input")') == 1
    assert run_source.count('tg.create_task(self.play_audio(), name="voice_output")') == 1
