import asyncio
from types import SimpleNamespace

import ada
import server

from chat_history import ChatHistoryStore


def message(message_id, role, content, timestamp, source=None):
    return {
        "id": message_id,
        "role": role,
        "content": content,
        "timestamp": timestamp,
        "source": source or ("assistant" if role == "assistant" else "text"),
    }


def test_missing_history_is_empty_without_creating_storage(tmp_path):
    path = tmp_path / "conversations" / "default.jsonl"
    store = ChatHistoryStore(path)
    assert store.list_messages() == []
    assert not path.exists()


def test_history_persists_utf8_in_chronological_order_across_restart(tmp_path):
    path = tmp_path / "default.jsonl"
    store = ChatHistoryStore(path)
    store.append(message("assistant-1", "assistant", "Olá, Marcelo.", "2026-01-01T10:00:02+00:00"))
    store.append(message("user-1", "user", "Bom dia, Verônica.", "2026-01-01T10:00:01+00:00"))

    restored = ChatHistoryStore(path).list_messages()

    assert [item["id"] for item in restored] == ["user-1", "assistant-1"]
    assert restored[0]["content"] == "Bom dia, Verônica."
    assert "Verônica" in path.read_text(encoding="utf-8")


def test_duplicate_id_is_not_appended_twice(tmp_path):
    path = tmp_path / "default.jsonl"
    store = ChatHistoryStore(path)
    first, created = store.append(message("same-id", "user", "Primeira", "2026-01-01T10:00:00+00:00"))
    duplicate, duplicate_created = store.append(message("same-id", "user", "Retry", "2026-01-01T10:00:01+00:00"))

    assert created is True and duplicate_created is False
    assert first["id"] == duplicate["id"] == "same-id"
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_invalid_or_partial_jsonl_line_does_not_hide_valid_history(tmp_path):
    path = tmp_path / "default.jsonl"
    path.write_text(
        '{"id":"valid","role":"user","content":"Visível","timestamp":"2026-01-01T00:00:00+00:00","source":"voice"}\n{"id":',
        encoding="utf-8",
    )
    assert [item["id"] for item in ChatHistoryStore(path).list_messages()] == ["valid"]


class Analyzer:
    def __init__(self):
        self.user_turns = []
        self.assistant_turns = []

    def process_conversation_turn(self, text, **kwargs):
        self.user_turns.append((text, kwargs))
        return {"action": "none"}

    def record_assistant_turn(self, text):
        self.assistant_turns.append(text)


def test_completed_voice_turn_emits_one_final_message_per_role():
    emitted = []
    loop = ada.AudioLoop.__new__(ada.AudioLoop)
    loop.on_transcription = emitted.append
    loop._voice_turn_text = "Pergunta final"
    loop._assistant_turn_text = "Resposta final"
    loop._voice_message_id = "voice-id"
    loop._assistant_message_id = "assistant-id"
    loop.conversational_memory_analyzer = Analyzer()
    loop.conversation_context_builder = SimpleNamespace(current_subject=None)

    loop._process_completed_voice_turn()
    loop._process_completed_assistant_turn()

    assert [(item["message_id"], item["final"], item["text"]) for item in emitted] == [
        ("voice-id", True, "Pergunta final"),
        ("assistant-id", True, "Resposta final"),
    ]
    assert loop._voice_turn_text == loop._assistant_turn_text == ""


def test_typed_message_event_persists_once_and_returns_same_id(tmp_path, monkeypatch):
    store = ChatHistoryStore(tmp_path / "default.jsonl")

    class Session:
        async def send(self, **kwargs):
            self.sent = kwargs

    session = Session()
    monkeypatch.setattr(server, "chat_history", store)
    monkeypatch.setattr(server, "audio_loop", SimpleNamespace(
        session=session, project_manager=None, _latest_image_payload=None,
        live_session_available=lambda: True,
    ))
    monkeypatch.setattr(server, "conversation_context", SimpleNamespace(
        build_context=lambda *args, **kwargs: {"context": ""},
    ))
    monkeypatch.setattr(server, "conversational_memory", SimpleNamespace(
        process_conversation_turn=lambda *args, **kwargs: {"action": "none", "confidence": 0},
    ))

    payload = {"text": "Mensagem persistente", "message_id": "typed-id", "timestamp": "2026-01-01T12:00:00+00:00"}
    first = asyncio.run(server.user_input("sid", payload))
    retry = asyncio.run(server.user_input("sid", payload))

    assert first["accepted"] is True and retry["accepted"] is True
    assert first["message"]["id"] == retry["message"]["id"] == "typed-id"
    assert [item["content"] for item in ChatHistoryStore(store.path).list_messages()] == ["Mensagem persistente"]
    assert session.sent["end_of_turn"] is True


def test_typed_message_rejects_dead_live_session_without_sending(tmp_path, monkeypatch):
    store = ChatHistoryStore(tmp_path / "default.jsonl")

    class Session:
        async def send(self, **_kwargs):
            raise AssertionError("dead session must not be used")

    monkeypatch.setattr(server, "chat_history", store)
    monkeypatch.setattr(server, "audio_loop", SimpleNamespace(
        session=Session(), project_manager=None, _latest_image_payload=None,
        live_session_available=lambda: False,
    ))

    result = asyncio.run(server.user_input("sid", {"text": "olá"}))

    assert result["accepted"] is False
    assert result["reason"] == "live_session_unavailable"


def test_typed_send_failure_invalidates_same_session(tmp_path, monkeypatch):
    store = ChatHistoryStore(tmp_path / "default.jsonl")
    published = []

    class Session:
        async def send(self, **_kwargs):
            raise RuntimeError("1011 Internal error encountered")

    session = Session()
    loop = SimpleNamespace(
        session=session, project_manager=None, _latest_image_payload=None,
        live_session_available=lambda: True,
        invalidate_live_session=lambda session=None, error=None: True,
        _connection_error_details=lambda error: (1011, str(error)),
        _publish_connection_state=lambda state, **details: published.append((state, details)),
    )
    monkeypatch.setattr(server, "chat_history", store)
    monkeypatch.setattr(server, "audio_loop", loop)
    monkeypatch.setattr(server, "conversation_context", SimpleNamespace(
        build_context=lambda *args, **kwargs: {"context": ""},
    ))
    monkeypatch.setattr(server, "conversational_memory", SimpleNamespace(
        process_conversation_turn=lambda *args, **kwargs: {"action": "none", "confidence": 0},
    ))

    result = asyncio.run(server.user_input("sid", {"text": "olá"}))

    assert result["accepted"] is False
    assert result["reason"] == "live_send_failed"
    assert published == [("closed", {"code": 1011, "reason": "1011 Internal error encountered"})]
