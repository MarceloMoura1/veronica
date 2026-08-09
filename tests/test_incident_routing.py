import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import ada
import server
from live_tools import DIRECT_HYBRID_TOOLS, HYBRID_GATEWAY_DESCRIPTIONS, HYBRID_GATEWAY_ACTIONS


def _memory_hashes():
    root = Path(__file__).parents[1] / "data" / "memory"
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_import_server_has_no_real_memory_write_and_temp_storage_is_injected(tmp_path):
    before = _memory_hashes()
    assert server.personal_memory is None
    memory, context, analyzer = server.initialize_runtime_memory(tmp_path / "memory")
    assert memory.storage_dir == tmp_path / "memory"
    assert context.memory is memory and analyzer.memory is memory
    assert (tmp_path / "memory" / "conversation_state.json").exists()
    assert _memory_hashes() == before


class FakeSession:
    def __init__(self): self.calls = []
    async def send(self, **kwargs): self.calls.append(kwargs)


def test_incident_ui_event_bypasses_personal_memory_and_returns_real_ack(monkeypatch):
    incident_id = "12345678-1234-5678-9234-567812345678"
    incident = {
        "incident_id": incident_id, "severity": "grave", "source": "synthetic",
        "component": "database", "error_code": "DB_DOWN", "safe_summary": "Unavailable",
        "status": "novo", "occurrence_count": 3, "first_seen": "2026-01-01T00:00:00+00:00",
        "last_seen": "2026-01-01T00:01:00+00:00",
        "metadata": {"password": "synthetic-secret", "stack_trace": "synthetic-stack"},
    }
    session = FakeSession()
    monkeypatch.setattr(server, "audio_loop", SimpleNamespace(session=session))
    monkeypatch.setattr(server, "integration_manager", SimpleNamespace(
        tool_get_incident_details=lambda requested: {"incident": incident if requested == incident_id else None}
    ))
    monkeypatch.setattr(server, "conversation_context", SimpleNamespace(
        build_context=lambda *a, **k: (_ for _ in ()).throw(AssertionError("personal memory called"))
    ))
    ack = asyncio.run(server.ask_veronica_about_incident("sid", {"incident_id": incident_id}))
    assert ack == {"accepted": True, "incident_id": incident_id}
    assert len(session.calls) == 1 and session.calls[0]["end_of_turn"] is True
    sent = session.calls[0]["input"]
    assert "DB_DOWN" in sent and "OBSERVED FACTS" in sent and "CAUSE HYPOTHESES" in sent
    assert "synthetic-secret" not in sent and "synthetic-stack" not in sent


def test_incident_ui_event_validates_id_and_missing_incident(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(server, "audio_loop", SimpleNamespace(session=session))
    monkeypatch.setattr(server, "integration_manager", SimpleNamespace(
        tool_get_incident_details=lambda requested: {"incident": None}
    ))
    invalid = asyncio.run(server.ask_veronica_about_incident("sid", {"incident_id": "not-an-id"}))
    missing = asyncio.run(server.ask_veronica_about_incident(
        "sid", {"incident_id": "12345678-1234-5678-9234-567812345678"}
    ))
    assert invalid == {"accepted": False, "reason": "invalid_incident_id"}
    assert missing == {"accepted": False, "reason": "incident_not_found"}
    assert session.calls == []


def test_hybrid_contract_separates_current_incidents_from_persistent_memory():
    instruction = ada.system_instruction_for_mode("hybrid")
    workspace = HYBRID_GATEWAY_DESCRIPTIONS["workspace_action"]
    actions = HYBRID_GATEWAY_ACTIONS["workspace_action"]
    assert "errors/incidents/health" in instruction
    assert "Personal/project knowledge: call retrieve_memory" in instruction
    assert "incident" not in workspace
    assert "list_system_incidents" in DIRECT_HYBRID_TOOLS
    assert "get_incident_details" in DIRECT_HYBRID_TOOLS
    assert "list_system_incidents" not in actions and "get_incident_details" not in actions
    assert ada.system_instruction_for_mode("legacy") == ada.SYSTEM_INSTRUCTION


def test_real_startup_hook_initializes_memory_but_plain_import_does_not(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "personal_memory", None)
    monkeypatch.setattr(server, "initialize_runtime_memory", lambda storage_dir=None: calls.append(storage_dir))
    class Kasa:
        async def initialize(self): return None
    class Integrations:
        async def test_connection(self, integration_id): return None
    monkeypatch.setattr(server, "kasa_agent", Kasa())
    monkeypatch.setattr(server, "integration_manager", Integrations())
    asyncio.run(server.startup_event())
    assert calls == [None]
