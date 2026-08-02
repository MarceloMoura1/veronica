from datetime import datetime, timedelta, timezone

import pytest

from memory import (
    ConversationContextBuilder, ConversationalMemoryAnalyzer,
    EntityResolver, PersonalMemoryManager,
)


PACK = """[PROFILE]
name = Marcelo
[PERSON:Pedro]
name = Pedro
relationship = Melhor amigo de Marcelo
importance = high
[PERSON:Christyan]
name = Christyan
relationship = Melhor amigo e sócio de Marcelo
importance = high
[PROJECT:MegaDesk]
name = MegaDesk
type = SaaS empresarial
business_goal = Atendimento e operação empresarial
core_modules = WhatsApp; CRM; ERP
importance = high
[PROJECT:FaYerS]
name = FaYerS
type = Empresa de engenharia
primary_services = Modelagem 3D; CAD; desenhos técnicos
cad_platform = SolidWorks
importance = high
"""


@pytest.fixture
def stack(tmp_path):
    now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    manager = PersonalMemoryManager(tmp_path)
    manager.import_memory_text(PACK)
    builder = ConversationContextBuilder(manager, max_context_chars=4000)
    analyzer = ConversationalMemoryAnalyzer(manager, builder, now_fn=lambda: now)
    builder.memory_intelligence.now_fn = lambda: now
    return manager, builder, analyzer, now, tmp_path


def test_resolves_megadesk_and_fayers_in_order(stack):
    _, builder, _, _, _ = stack
    entities = builder.resolver.resolve_entities("O que temos no MegaDesk e na FaYerS?")
    assert [item["name"] for item in entities] == ["MegaDesk", "FaYerS"]


def test_resolves_marcelo_christyan_and_megadesk(stack):
    _, builder, _, _, _ = stack
    entities = builder.resolver.resolve_entities("O que eu e o Christyan decidimos no MegaDesk?")
    assert [item["name"] for item in entities] == ["Marcelo", "Christyan", "MegaDesk"]


def test_current_subject_does_not_scope_other_project(stack):
    _, builder, _, _, _ = stack
    builder.build_context("O que é o MegaDesk?")
    result = builder.build_context("O que fazemos na FaYerS?")
    assert result["entity"] == "FaYerS" and "SolidWorks" in result["context"]


def test_current_subject_does_not_scope_people(stack):
    _, builder, _, _, _ = stack
    builder.build_context("O que é a FaYerS?")
    result = builder.build_context("Quem é Pedro?")
    assert result["entity"] == "Pedro" and "Melhor amigo" in result["context"]


def test_cross_domain_context_contains_both_projects(stack):
    _, builder, _, _, _ = stack
    result = builder.build_context("O que temos no MegaDesk e na FaYerS?")
    assert result["entities"] == ["MegaDesk", "FaYerS"]
    assert "SaaS empresarial" in result["context"] and "SolidWorks" in result["context"]


def test_adaptive_context_budget_is_respected(stack):
    manager, _, _, _, _ = stack
    builder = ConversationContextBuilder(manager, max_context_chars=1200)
    result = builder.build_context("Explique MegaDesk e FaYerS em detalhes.")
    assert len(result["context"]) <= 1200
    assert "MegaDesk" in result["context"] and "FaYerS" in result["context"]


def test_uncertain_price_does_not_replace_active_decision(stack):
    manager, builder, analyzer, _, _ = stack
    analyzer.process_conversation_turn("Decidimos cobrar R$700 de implantação no MegaDesk.", "text")
    analyzer.process_conversation_turn("Talvez a gente cobre R$900 no MegaDesk.", "text")
    active = manager.get_active_decisions()
    result = builder.build_context("O que decidimos sobre implantação do MegaDesk?")
    assert len(active) == 1 and "R$700" in active[0]["decision"]
    assert "R$700" in result["context"]


def test_new_confirmed_decision_supersedes_old(stack):
    manager, builder, analyzer, _, _ = stack
    analyzer.process_conversation_turn("Decidimos cobrar R$700 de implantação no MegaDesk.", "text")
    analyzer.process_conversation_turn("Decidimos mudar para R$900.", "text")
    decisions = manager.get_recent_decisions()
    active = [item for item in decisions if item["status"] == "active"]
    old = [item for item in decisions if item["status"] == "superseded"]
    current = builder.build_context("Qual decisão do MegaDesk ainda está ativa?")
    history = builder.build_context("O que eu havia decidido antes e depois mudei no MegaDesk?")
    assert len(active) == len(old) == 1
    assert "R$900" in active[0]["decision"] and old[0]["superseded_by"] == active[0]["id"]
    assert "R$900" in current["context"] and "R$700" not in current["context"]
    assert "R$700" in history["context"] and "R$900" in history["context"]


def test_cancelled_plan_is_not_active(stack):
    manager, _, analyzer, _, _ = stack
    analyzer.process_conversation_turn("Amanhã vou trabalhar na página do MegaDesk.", "text")
    analyzer.process_conversation_turn("Não vou mais fazer isso amanhã.", "text")
    assert manager.get_active_plans() == []


def test_completed_plan_is_not_pending(stack):
    manager, builder, analyzer, _, _ = stack
    analyzer.process_conversation_turn("Amanhã vou trabalhar na página do MegaDesk.", "text")
    result = analyzer.process_conversation_turn("Já terminei a página.", "text")
    context = builder.build_context("Quais planos estão ativos?")
    assert result["action"] == "completed" and manager.get_active_plans() == []
    assert "terminei" not in context["context"]


def test_recent_event_filter_excludes_old_event(stack):
    manager, builder, _, now, _ = stack
    for memory_id, summary, when in (
        ("recent", "Pedro foi ao hospital recentemente", now - timedelta(days=2)),
        ("old", "Pedro viajou há muitos meses", now - timedelta(days=120)),
    ):
        manager.save_memory_record("events", memory_id, {
            "id": memory_id, "entities": ["Pedro"], "summary": summary,
            "status": "active", "confidence": 0.9,
            "recorded_at": when.isoformat(), "updated_at": when.isoformat(),
        })
    result = builder.build_context("O que aconteceu recentemente?")
    assert "hospital" in result["context"] and "muitos meses" not in result["context"]


def test_active_plan_query_returns_only_active(stack):
    manager, builder, _, now, _ = stack
    for memory_id, status in (("active", "planned"), ("cancelled", "cancelled"), ("done", "completed")):
        manager.save_memory_record("plans", memory_id, {
            "id": memory_id, "project": "MegaDesk", "summary": f"plano {status}",
            "status": status, "confidence": 0.9,
            "recorded_at": now.isoformat(), "updated_at": now.isoformat(),
        })
    result = builder.build_context("Quais planos estão ativos?")
    assert "plano planned" in result["context"]
    assert "plano cancelled" not in result["context"] and "plano completed" not in result["context"]


def test_multi_entity_aliases(stack):
    _, builder, _, _, _ = stack
    entities = builder.resolver.resolve_entities("Compare Fires, Mega Desk e Cristian.")
    assert [item["name"] for item in entities] == ["FaYerS", "MegaDesk", "Christyan"]


def test_plural_reference_reuses_recent_entities(stack):
    _, builder, _, _, _ = stack
    builder.build_context("Quero falar de MegaDesk e FaYerS.")
    result = builder.build_context("O que temos pendente nas duas?")
    assert result["entities"] == ["MegaDesk", "FaYerS"]
    assert "MegaDesk" in result["context"] and "FaYerS" in result["context"]


def test_returns_to_last_multi_entity_group_after_single_focus(stack):
    _, builder, _, _, _ = stack
    builder.build_context("O que temos no MegaDesk e na FaYerS?")
    single = builder.build_context("Agora fala só da FaYerS.")
    combined = builder.build_context("Volta para os dois.")
    assert single["entities"] == ["FaYerS"]
    assert combined["entities"] == ["MegaDesk", "FaYerS"]
    assert "MegaDesk" in combined["context"] and "FaYerS" in combined["context"]


def test_plural_group_is_selected_by_current_focus(stack):
    _, builder, _, _, _ = stack
    builder.build_context("O que temos no MegaDesk e na FaYerS?")
    builder.build_context("O que Pedro e Christyan fizeram?")
    builder.build_context("Agora fala só da FaYerS.")
    combined = builder.build_context("Volta para os dois.")
    assert combined["entities"] == ["MegaDesk", "FaYerS"]


def test_semantic_duplicates_are_consolidated(stack):
    manager, builder, _, now, _ = stack
    for memory_id, summary in (
        ("one", "FaYerS usa SolidWorks para projetos CAD"),
        ("two", "Na FaYerS usamos SolidWorks em projetos CAD"),
    ):
        manager.save_memory_record("events", memory_id, {
            "id": memory_id, "entities": ["FaYerS"], "summary": summary,
            "status": "active", "recorded_at": now.isoformat(), "updated_at": now.isoformat(),
        })
    result = builder.memory_intelligence.search_global(
        "SolidWorks na FaYerS", entities=[{"name": "FaYerS", "category": "projects"}],
        intent="event", max_items=20,
    )
    matching = [
        item for item in result["selected_memories"]
        if item["category"] == "events" and "SolidWorks" in str(item["value"])
    ]
    assert len(matching) == 1


def test_global_brain_has_no_entity_silo(stack):
    _, builder, _, _, _ = stack
    sequence = ("MegaDesk", "FaYerS", "Pedro", "MegaDesk")
    queries = ("O que é MegaDesk?", "O que é FaYerS?", "Quem é Pedro?", "Volte ao MegaDesk.")
    assert [builder.build_context(query)["entity"] for query in queries] == list(sequence)


def test_text_and_voice_use_same_multi_entity_layer(stack):
    _, builder, _, _, _ = stack
    text = builder.build_context("Compare MegaDesk e FaYerS.", channel="text")
    voice = builder.build_context("Compare MegaDesk e FaYerS.", channel="voice")
    assert text["entities"] == voice["entities"] == ["MegaDesk", "FaYerS"]
    assert text["context"] == voice["context"]


def test_session_resume_regression_with_memory_intelligence(stack):
    _, _, analyzer, _, path = stack
    analyzer.process_conversation_turn("Pedro caiu e machucou o braço.", "voice")
    del analyzer
    manager = PersonalMemoryManager(path)
    builder = ConversationContextBuilder(manager)
    restarted = ConversationalMemoryAnalyzer(manager, builder)
    result = builder.build_context("O que a gente tava conversando?")
    followup = builder.build_context("E ele ficou bem?")
    assert result["intent"] == "session_resume" and "Pedro" in result["context"]
    assert followup["entity"] == "Pedro"


def test_cold_start_restores_multi_entity_state(stack):
    _, builder, analyzer, _, path = stack
    builder.build_context("Quero falar de MegaDesk e FaYerS.", channel="voice")
    analyzer.process_conversation_turn("Quero falar de MegaDesk e FaYerS.", "voice")
    del analyzer
    manager = PersonalMemoryManager(path)
    restarted_builder = ConversationContextBuilder(manager)
    ConversationalMemoryAnalyzer(manager, restarted_builder)
    assert {entity["name"] for entity in restarted_builder.current_entities} >= {"MegaDesk", "FaYerS"}


def test_memory_import_remains_compatible(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    result = manager.import_memory_text(PACK)
    assert result["success"] is True
    assert manager.get_project("MegaDesk") and manager.get_person("Pedro")
