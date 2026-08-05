from datetime import datetime, timezone
from pathlib import Path
import json

import pytest

from memory import ConversationContextBuilder, ConversationalMemoryAnalyzer, PersonalMemoryManager


PACK = """[PERSON:Pedro]
name = Pedro
relationship = Melhor amigo
importance = Muito alta
[PERSON:Christyan]
name = Christyan
relationship = Melhor amigo e sócio
[PERSON:Veronica]
name = Verônica
[PROJECT:MegaDesk]
name = MegaDesk
type = SaaS empresarial
"""


@pytest.fixture
def memory_stack(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.import_memory_text(PACK)
    builder = ConversationContextBuilder(manager)
    analyzer = ConversationalMemoryAnalyzer(
        manager, builder,
        now_fn=lambda: datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    )
    return manager, builder, analyzer, tmp_path


def test_event_is_created(memory_stack):
    manager, _, analyzer, _ = memory_stack
    result = analyzer.process_conversation_turn("Pedro se machucou ontem.", "text")
    event = manager.get_recent_events()[0]
    assert result["classification"] == "event" and result["action"] == "created"
    assert event["entities"] == ["Pedro"] and event["occurred_at"] == "2026-07-31"


def test_followup_hospital_updates_same_event(memory_stack):
    manager, _, analyzer, _ = memory_stack
    first = analyzer.process_conversation_turn("Pedro se machucou ontem.", "text")
    second = analyzer.process_conversation_turn("Ele foi ao hospital.", "text")
    assert second["classification"] == "update" and second["memory_id"] == first["memory_id"]
    assert manager.get_recent_events()[0]["details"] == ["Ele foi ao hospital."]


def test_recovery_updates_event_status(memory_stack):
    manager, _, analyzer, _ = memory_stack
    analyzer.process_conversation_turn("Pedro se machucou ontem.", "text")
    analyzer.process_conversation_turn("Agora ele já está melhor.", "text")
    assert manager.get_recent_events()[0]["status"] == "recovering"


def test_restart_retrieves_complete_event(memory_stack):
    manager, _, analyzer, path = memory_stack
    analyzer.process_conversation_turn("Pedro se machucou ontem.", "voice")
    analyzer.process_conversation_turn("Ele foi ao hospital.", "voice")
    analyzer.process_conversation_turn("Agora ele está melhor.", "voice")
    reloaded = PersonalMemoryManager(path)
    context = ConversationContextBuilder(reloaded).build_context("O que aconteceu com Pedro?")["context"]
    assert all(value in context for value in ("machucou", "hospital", "melhor", "recovering"))


def test_decision_is_created(memory_stack):
    manager, _, analyzer, _ = memory_stack
    result = analyzer.process_conversation_turn(
        "Eu e o Christyan decidimos cobrar R$700 de implantação no MegaDesk.", "text"
    )
    decision = manager.get_recent_decisions()[0]
    assert result["classification"] == "decision"
    assert decision["project"] == "MegaDesk" and "Christyan" in decision["participants"]
    assert "700" in decision["decision"]


def test_decision_is_retrieved(memory_stack):
    _, builder, analyzer, _ = memory_stack
    analyzer.process_conversation_turn("Decidimos cobrar R$700 no MegaDesk.", "text")
    context = builder.build_context("O que decidimos sobre o preço do MegaDesk?")["context"]
    assert "R$700" in context


def test_plan_is_created(memory_stack):
    manager, _, analyzer, _ = memory_stack
    result = analyzer.process_conversation_turn(
        "Amanhã vou trabalhar na página de clientes do MegaDesk.", "text"
    )
    plan = manager.get_active_plans()[0]
    assert result["classification"] == "plan"
    assert plan["time_reference"] == "amanha" and plan["status"] == "planned"


def test_plan_can_be_cancelled(memory_stack):
    manager, _, analyzer, _ = memory_stack
    analyzer.process_conversation_turn("Amanhã vou trabalhar na página de clientes.", "text")
    result = analyzer.process_conversation_turn("Não vou mais fazer isso amanhã.", "text")
    assert result["action"] == "cancelled"
    assert manager.get_recent_plans()[0]["status"] == "cancelled"


def test_preference_is_created(memory_stack):
    manager, _, analyzer, _ = memory_stack
    result = analyzer.process_conversation_turn("Eu prefiro programar à noite.", "text")
    assert result["classification"] == "preference"
    assert "noite" in manager.get_preference("conversational_programar")


def test_preference_is_updated(memory_stack):
    manager, _, analyzer, _ = memory_stack
    analyzer.process_conversation_turn("Eu prefiro programar à noite.", "text")
    result = analyzer.process_conversation_turn("Agora prefiro programar de manhã.", "text")
    assert result["action"] == "updated"
    assert "manhã" in manager.get_preference("conversational_programar")
    assert "noite" not in manager.get_preference("conversational_programar")


def test_uncertain_price_does_not_replace_decision(memory_stack):
    manager, _, analyzer, _ = memory_stack
    analyzer.process_conversation_turn("Decidimos cobrar R$700 no MegaDesk.", "text")
    result = analyzer.process_conversation_turn("Talvez a gente cobre R$900 no MegaDesk.", "text")
    assert result["classification"] != "decision"
    assert len(manager.get_recent_decisions()) == 1
    assert "700" in manager.get_recent_decisions()[0]["decision"]


@pytest.mark.parametrize("phrase", ["kkkk", "beleza", "Que calor."])
def test_disposable_conversation_is_ignored(memory_stack, phrase):
    manager, _, analyzer, _ = memory_stack
    before = manager.get_recent_memories()
    result = analyzer.process_conversation_turn(phrase, "text")
    assert result["classification"] == "ignore"
    assert manager.get_recent_memories() == before


def test_duplicate_turn_creates_one_event(memory_stack):
    manager, _, analyzer, _ = memory_stack
    analyzer.process_conversation_turn("Pedro se machucou ontem.", "voice")
    duplicate = analyzer.process_conversation_turn("Pedro se machucou ontem.", "voice")
    assert duplicate["action"] == "duplicate"
    assert len(manager.get_recent_events()) == 1


def test_text_and_voice_have_equivalent_classification(tmp_path):
    results = []
    for channel in ("text", "voice"):
        path = tmp_path / channel
        manager = PersonalMemoryManager(path)
        manager.import_memory_text(PACK)
        builder = ConversationContextBuilder(manager)
        analyzer = ConversationalMemoryAnalyzer(manager, builder)
        results.append(analyzer.process_conversation_turn("Pedro se machucou ontem.", channel))
    assert results[0]["classification"] == results[1]["classification"] == "event"
    assert results[0]["confidence"] == results[1]["confidence"]


def test_negated_injury_does_not_create_event(memory_stack):
    manager, _, analyzer, _ = memory_stack
    result = analyzer.process_conversation_turn("Pedro não se machucou.", "text")
    assert result["classification"] == "ignore"
    assert manager.get_recent_events() == []


def test_explicit_memory_has_high_confidence(memory_stack):
    manager, _, analyzer, _ = memory_stack
    result = analyzer.process_conversation_turn(
        "Lembre que Pedro prefere ser chamado de Pedrão.", "voice"
    )
    assert result["classification"] == "fact" and result["confidence"] == 0.99
    assert any("Pedrão" in str(value) for value in manager.get_category("facts").values())


def test_questions_do_not_create_memories(memory_stack):
    manager, _, analyzer, _ = memory_stack
    result = analyzer.process_conversation_turn("O que aconteceu com Pedro?", "voice")
    assert result["reason"] == "question_not_memory"
    assert manager.get_recent_memories() == []


def test_continuity_restores_last_subject(memory_stack):
    _, _, analyzer, path = memory_stack
    analyzer.process_conversation_turn("Pedro se machucou ontem.", "text")
    reloaded = PersonalMemoryManager(path)
    builder = ConversationContextBuilder(reloaded)
    result = builder.build_context("E como ele está?")
    assert result["entity"] == "Pedro" and result["intent"] == "event"


def test_voice_integration_waits_for_explicit_turn_complete():
    source = (Path(__file__).parents[1] / "backend" / "ada.py").read_text(encoding="utf-8")
    assert "if response.server_content.turn_complete:" in source
    assert "if voice_turn_complete:" in source
    assert "self._process_completed_voice_turn()" in source


def _cold_restart(path):
    manager = PersonalMemoryManager(path)
    builder = ConversationContextBuilder(manager)
    analyzer = ConversationalMemoryAnalyzer(manager, builder)
    return manager, builder, analyzer


def test_cold_restart_restores_pedro_event_and_pronoun(memory_stack):
    _, _, analyzer, path = memory_stack
    analyzer.process_conversation_turn(
        "O Pedro se machucou ontem jogando bola, bateu o braço e precisou ir ao hospital. "
        "Depois ele me falou que estava melhor.",
        "voice",
    )
    analyzer.record_assistant_turn("Que bom que ele já está melhor.")

    del analyzer
    manager, builder, restarted = _cold_restart(path)
    direct = builder.build_context("O que aconteceu com o Pedro?")["context"]
    vague = builder.build_context("E ele ficou bem?")
    startup = restarted.build_cold_start_context()

    assert all(token in direct for token in ("machucou", "braço", "hospital", "melhor"))
    assert vague["entity"] == "Pedro"
    assert all(token in startup for token in ("Pedro", "braço", "hospital", "melhor"))
    assert "Que bom" in startup
    assert manager.get_recent_events()[0]["status"] == "recovering"


def test_cold_restart_restores_megadesk_decision(memory_stack):
    _, _, analyzer, path = memory_stack
    analyzer.process_conversation_turn(
        "Eu e o Christyan decidimos cobrar R$700 de implantação no MegaDesk.", "voice"
    )
    del analyzer
    _, builder, restarted = _cold_restart(path)
    context = builder.build_context("Quanto a gente tinha decidido cobrar?")["context"]
    startup = restarted.build_cold_start_context()
    assert "R$700" in context
    assert "R$700" in startup and "MegaDesk" in startup


def test_cold_restart_restores_megadesk_plan(memory_stack):
    _, _, analyzer, path = memory_stack
    analyzer.process_conversation_turn(
        "Amanhã quero trabalhar na página de clientes do MegaDesk.", "voice"
    )
    del analyzer
    _, builder, restarted = _cold_restart(path)
    context = builder.build_context("O que eu tinha planejado fazer?")["context"]
    startup = restarted.build_cold_start_context()
    assert "página de clientes" in context
    assert "página de clientes" in startup and "MegaDesk" in startup


def test_conversation_state_is_compact_and_rotates_session_id(memory_stack):
    _, _, analyzer, path = memory_stack
    analyzer.process_conversation_turn("Pedro se machucou ontem.", "voice")
    first = analyzer.conversation_state.snapshot()
    _, _, restarted = _cold_restart(path)
    second = restarted.conversation_state.snapshot()
    assert second["previous_conversation_id"] == first["conversation_id"]
    assert second["conversation_id"] != first["conversation_id"]
    assert len(second["important_turns"]) <= 12


def test_invalid_conversation_state_is_backed_up(tmp_path):
    state_path = tmp_path / "conversation_state.json"
    state_path.write_text("{invalid", encoding="utf-8")
    manager = PersonalMemoryManager(tmp_path)
    builder = ConversationContextBuilder(manager)
    analyzer = ConversationalMemoryAnalyzer(manager, builder)
    assert analyzer.conversation_state.snapshot()["important_turns"] == []
    assert list(tmp_path.glob("conversation_state.invalid.*.json"))


def test_audio_loop_silently_preloads_cold_start_state():
    source = (Path(__file__).parents[1] / "backend" / "ada.py").read_text(encoding="utf-8")
    assert "await self._send_cold_start_once()" in source
    assert "self.live_session.begin_cold_start()" in source
    assert "await self.session.send(input=restoration, end_of_turn=False)" in source
    assert source.index("await self._send_cold_start_once()") < source.index(
        'tg.create_task(self.listen_audio(), name="voice_input")'
    )


def test_session_resume_survives_greeting_after_cold_restart(memory_stack):
    _, _, analyzer, path = memory_stack
    analyzer.process_conversation_turn(
        "O Pedro estava jogando bola, caiu e machucou o tornozelo.", "voice"
    )
    analyzer.process_conversation_turn("Ele vai fazer três meses de fisioterapia.", "voice")
    del analyzer

    _, builder, restarted = _cold_restart(path)
    greeting = "Bom dia, Verônica, está me ouvindo?"
    greeting_context = builder.build_context(greeting, channel="voice")
    restarted.process_conversation_turn(greeting, "voice")
    assert greeting_context["entity"] is None and greeting_context["item_count"] == 0
    assert builder.current_subject["name"] == "Pedro"
    assert restarted.conversation_state.snapshot()["active_topic"] == "Pedro"

    resumed = builder.build_context("O que a gente tava conversando?", channel="voice")
    assert resumed["intent"] == "session_resume" and resumed["has_context"] is True
    assert resumed["entity"] == "Pedro"
    assert all(token in resumed["context"] for token in ("Pedro", "tornozelo", "fisioterapia"))

    followup = builder.build_context("E ele ficou bem?", channel="voice")
    assert followup["entity"] == "Pedro"


def test_vocative_does_not_win_over_real_subject(memory_stack):
    _, builder, _, _ = memory_stack
    result = builder.build_context("Verônica, o MegaDesk precisa de uma nova página.")
    assert result["entity"] == "MegaDesk"


def test_veronica_can_still_be_the_subject(memory_stack):
    _, builder, _, _ = memory_stack
    result = builder.build_context("Verônica, quais funções você possui?")
    assert result["entity"] == "Veronica"


def test_trivial_turns_preserve_last_meaningful_topic(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.import_memory_text(PACK + "\n[PROJECT:FaYerS]\nname = FaYerS\n")
    builder = ConversationContextBuilder(manager)
    analyzer = ConversationalMemoryAnalyzer(manager, builder)
    analyzer.process_conversation_turn("Agora quero falar sobre FaYerS.", "voice")
    for phrase in ("beleza", "kkkk", "entendi", "boa noite, Verônica"):
        builder.build_context(phrase, channel="voice")
        analyzer.process_conversation_turn(phrase, "voice")
    state = analyzer.conversation_state.snapshot()
    assert state["active_topic"] == state["last_meaningful_topic"] == "FaYerS"


def test_explicit_topic_change_replaces_active_topic(memory_stack):
    _, _, analyzer, _ = memory_stack
    analyzer.process_conversation_turn("Quero falar sobre Pedro.", "voice")
    analyzer.process_conversation_turn("Agora quero falar sobre MegaDesk.", "voice")
    state = analyzer.conversation_state.snapshot()
    assert state["active_topic"] == state["last_meaningful_topic"] == "MegaDesk"


def test_live_session_resume_requires_retrieval_without_refresh_after_greeting():
    source = (Path(__file__).parents[1] / "backend" / "ada.py").read_text(encoding="utf-8")
    assert "For any request to resume or recall the prior conversation, always call retrieve_memory" in source
    assert "Never claim there is no saved context without calling it" in source
    assert 'learning_result.get("reason") == "greeting"' not in source
    assert "refreshing after greeting" not in source
    assert source.count("await self._send_cold_start_once()") == 1


def test_compact_cold_start_is_bounded_and_omits_raw_turn_section(memory_stack):
    _, builder, analyzer, _ = memory_stack
    analyzer.process_conversation_turn("Agora quero falar sobre MegaDesk.", "voice")
    analyzer.process_conversation_turn(
        "Amanhã quero revisar o cadastro de clientes do MegaDesk.", "voice"
    )
    context, diagnostics = analyzer.build_cold_start_context(
        max_chars=800, include_diagnostics=True
    )
    assert len(context) <= 800
    assert builder.current_subject["name"] == "MegaDesk"
    assert '"subject":"MegaDesk"' in context
    assert "Important recent turns" not in context
    assert diagnostics["has_important_turns"] is False
    assert diagnostics["after_chars"] < diagnostics["before_chars"]


def test_compact_cold_start_excludes_superseded_decisions(memory_stack):
    manager, _, analyzer, _ = memory_stack
    manager.save_memory_record("decisions", "old", {
        "id": "old", "decision": "old private choice", "project": "MegaDesk",
        "status": "superseded", "timestamp": "2026-01-01T00:00:00+00:00",
    })
    manager.save_memory_record("decisions", "new", {
        "id": "new", "decision": "current sanitized choice", "project": "MegaDesk",
        "status": "active", "timestamp": "2026-01-02T00:00:00+00:00",
    })
    context = analyzer.build_cold_start_context(max_chars=800)
    assert "current sanitized choice" in context
    assert "old private choice" not in context


def test_compact_cold_start_budget_keeps_valid_json(memory_stack):
    _, _, analyzer, _ = memory_stack
    context, diagnostics = analyzer.build_cold_start_context(
        max_chars=240, include_diagnostics=True
    )
    payload = context.split("\n", 1)[1]
    assert isinstance(json.loads(payload), dict)
    assert len(context) <= 240
    assert diagnostics["after_chars"] == len(context)
