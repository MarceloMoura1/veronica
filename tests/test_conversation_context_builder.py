import pytest
from pathlib import Path

from memory import ConversationContextBuilder, PersonalMemoryManager


MEMORY_PACK = """# VERONICA MEMORY PACK V1
[PROFILE]
name = Marcelo
mother = Josiane França de Moura
father = Antonio Jocelio Lacerda de Moura
siblings = Nenhum
only_child = true

[PREFERENCES]
preferred_title = Chefe

[PERSON:JosianeFrancaDeMoura]
name = Josiane França de Moura
relationship = Mãe de Marcelo
family_role = Mãe

[PERSON:AntonioJocelioLacerdaDeMoura]
name = Antonio Jocelio Lacerda de Moura
relationship = Pai de Marcelo
family_role = Pai

[PERSON:Pedro]
name = Pedro
relationship = Melhor amigo de Marcelo

[PERSON:Christyan]
name = Christyan
relationship = Melhor amigo e futuro sócio de Marcelo
business_role = Marketing; comercial; tráfego; prospecção; projetos CAD

[PROJECT:FaYerS]
name = FaYerS
type = Empresa de engenharia, projetos, desenvolvimento de produtos e fabricação terceirizada
primary_services = Modelagem 3D; SolidWorks; desenhos técnicos; detalhamento para fabricação; desenvolvimento de produtos; consultoria técnica; renderização; KeyShot
service_model = Atuar como projetista terceirizado e desenvolver produtos próprios
manufacturing_model = Fabricação por parceiros externos, metalúrgicas e empresas de usinagem
service_flow = Requisitos -> CAD -> revisão -> documentação -> aprovação -> fabricação -> entrega
cad_platform = SolidWorks
render_platform = KeyShot
client_portal_goal = Criar portal para clientes
own_product_goal = Desenvolver produtos próprios
automation_goal = Automatizar tarefas administrativas
christyan_role = Marketing; comercial; prospecção; negociação; projetos CAD

[PROJECT:MegaDesk]
name = MegaDesk
type = SaaS empresarial
business_goal = Organização, atendimento e operação empresarial
core_modules = WhatsApp BOT; Conversas; Chamados; ERP; Estoque; CRM
first_year_goal = Atingir no mínimo 100 clientes pagantes no primeiro ano
revenue_model = Receita recorrente mensal

[PROJECT:Veronica]
name = Verônica
type = Assistente pessoal e profissional de Marcelo
personal_expectation = Conhecer Marcelo; lembrar contexto pessoal; conversar naturalmente
professional_expectation = Aumentar produtividade; executar atividades; organizar tarefas complexas
autonomy_vision = Alcançar alto nível de autonomia dentro das permissões
future_agents = Computer Agent; Developer Agent; Engineering Agent; MegaDesk Agent
memory_goal = Lembrar informações importantes entre sessões

[FACTS]
marcelo_best_friends = Pedro; Christyan
marcelo_has_siblings = false
marcelo_is_only_child = true
fayers_alias_1 = Fayers
megadesk_first_year_goal = No mínimo 100 clientes pagantes
"""


@pytest.fixture
def builder(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.import_memory_text(MEMORY_PACK)
    manager.add_entity_alias("FaYerS", "Fires")
    return ConversationContextBuilder(manager)


def context_for(builder, query, channel="text"):
    return builder.build_context(query, channel=channel)["context"]


def test_mother_is_retrieved(builder):
    assert "Josiane França de Moura" in context_for(builder, "Quem é minha mãe?")


def test_father_is_retrieved(builder):
    assert "Antonio Jocelio Lacerda de Moura" in context_for(builder, "Quem é meu pai?")


def test_best_friends_are_retrieved(builder):
    context = context_for(builder, "Quem são meus melhores amigos?")
    assert "Pedro" in context and "Christyan" in context


def test_fayers_overview_contains_real_business_details(builder):
    context = context_for(builder, "O que é a FaYerS?")
    assert all(value in context for value in ("engenharia", "SolidWorks", "fabricação terceirizada"))


def test_fayers_operations_prioritize_operational_fields(builder):
    context = context_for(builder, "O que fazemos na FaYerS?")
    assert all(value in context for value in ("Modelagem 3D", "desenhos técnicos", "metalúrgicas", "KeyShot"))
    assert builder.build_context("O que faríamos na FaYerS?")["intent"] == "operations"


def test_follow_up_keeps_fayers_and_expands_detail(builder):
    builder.build_context("O que é a FaYerS?")
    result = builder.build_context("Me dê mais detalhes.")
    assert result["entity"] == "FaYerS"
    assert result["intent"] == "detail"
    assert "client_portal_goal" in result["context"]


def test_megadesk_overview_contains_saas_modules(builder):
    context = context_for(builder, "O que é o MegaDesk?")
    assert "SaaS empresarial" in context and "WhatsApp BOT" in context and "ERP" in context


def test_megadesk_first_year_goal(builder):
    context = context_for(builder, "Qual a meta do MegaDesk no primeiro ano?")
    assert "100 clientes pagantes" in context


def test_veronica_future_expectations(builder):
    context = context_for(builder, "O que eu espero da Verônica?")
    assert all(value in context for value in ("autonomia", "produtividade", "Computer Agent", "Lembrar informações"))


def test_siblings_answer_is_grounded(builder):
    context = context_for(builder, "Tenho irmãos?")
    assert "Nenhum" in context or "false" in context


def test_alias_and_project_person_continuation(builder):
    assert builder.build_context("O que fazemos na Fires?")["entity"] == "FaYerS"
    builder.build_context("O que é a FaYerS?")
    result = builder.build_context("E o Christyan nessa empresa?")
    assert result["entity"] == "Christyan"
    assert "projects.FaYerS.christyan_role" in result["context"]


def test_text_and_voice_use_equivalent_context(builder):
    text = builder.build_context("O que fazemos na FaYerS?", channel="text")
    voice = builder.build_context("O que fazemos na FaYerS?", channel="voice")
    assert text["context"] == voice["context"]
    assert text["entity"] == voice["entity"] == "FaYerS"
    assert text["intent"] == voice["intent"] == "operations"


def test_irrelevant_question_does_not_dump_memory(builder):
    result = builder.build_context("Explique o que é torque.")
    assert result["context"] == ""
    assert result["item_count"] == 0


def test_text_and_live_integrations_call_the_shared_builder():
    project_root = Path(__file__).parents[1]
    server_source = (project_root / "backend" / "server.py").read_text(encoding="utf-8")
    ada_source = (project_root / "backend" / "ada.py").read_text(encoding="utf-8")
    assert 'conversation_context.build_context(text, channel="text")' in server_source
    assert '"name": "retrieve_memory"' in ada_source
    assert 'query, channel="voice"' in ada_source
    assert 'confirmation_required = False if fc.name == "retrieve_memory"' in ada_source
    assert "Relevant memory retrieval was already completed for this turn" in server_source


def test_current_subject_never_scopes_global_memory(builder):
    sequence = (
        ("O que é o MegaDesk?", "MegaDesk", "SaaS empresarial"),
        ("O que fazemos na FaYerS?", "FaYerS", "SolidWorks"),
        ("Quem é Pedro?", "Pedro", "Melhor amigo"),
        ("Voltando ao MegaDesk, qual a meta?", "MegaDesk", "100 clientes"),
    )
    for query, entity, expected in sequence:
        result = builder.build_context(query)
        assert result["entity"] == entity
        assert expected in result["context"]


def test_greeting_uses_minimal_context_without_memory(builder):
    result = builder.build_context("Olá Verônica, está me ouvindo?")
    assert result["route"] == "minimal"
    assert result["context"] == "" and result["item_count"] == 0
    assert result["context_diagnostics"]["memory_mode"] == "none"
    assert result["context_diagnostics"]["tools_mode"] == "none"


def test_actionable_request_never_takes_greeting_fast_path(builder):
    result = builder.build_context("Olá, ligue a luz do escritório")
    assert result["route"] == "operational"
    assert result["item_count"] == 0
    assert result["context_diagnostics"]["tools_mode"] == "full"


def test_entity_lookup_is_directed_and_budgeted(builder):
    result = builder.build_context("O que você sabe sobre o MegaDesk?")
    assert result["route"] == "entity_lookup"
    assert result["entity"] == "MegaDesk"
    assert result["context_diagnostics"]["token_budget"] == 1600
    assert result["context_diagnostics"]["components"][0]["estimated_tokens"] <= 1600
    assert result["item_count"] <= 6


@pytest.mark.parametrize("query", [
    "megadesk", "MEGADESK", "Me fale sobre o megadesk!", "Me fale sobre o MegaDesk.",
])
def test_canonical_entity_resolves_across_case_compaction_and_punctuation(builder, query):
    result = builder.build_context(query)
    assert result["entity"] == "MegaDesk"
    assert result["route"] == "entity_lookup"


def test_unknown_entity_is_not_invented(builder):
    result = builder.build_context("Me fale sobre Projeto Inexistente Zeta")
    assert result["entity"] is None


def test_fallback_standard_reports_candidates_and_selected_budget(builder):
    result = builder.build_context("Consulta genérica sobre assunto desconhecido")
    component = result["context_diagnostics"]["components"][0]
    assert result["route"] == "fallback_standard"
    assert component["candidate_count"] >= component["selected_count"]
    assert component["selected_count"] == result["item_count"]
    assert result["item_count"] <= 3


@pytest.mark.parametrize("query", [
    "Crie um projeto chamado Fixture.",
    "Liste meus projetos.",
    "Qual o status do Gemini?",
    "Abra o arquivo fixture.txt",
    "Qual o status atual da integração Acme?",
    "A integração Acme está online?",
    "Quais erros recentes apareceram na integração Acme?",
])
def test_explicit_operational_requests_skip_memory_prefetch(builder, query):
    result = builder.build_context(query)
    assert result["route"] == "operational"
    assert result["context"] == "" and result["item_count"] == 0


def test_context_dependent_operation_allows_directed_retrieval(builder):
    builder.build_context("Me fale sobre o MegaDesk")
    result = builder.build_context("Abra o projeto que discutimos ontem")
    assert result["route"] == "operational_context"
    assert result["entity"] == "MegaDesk"
    assert 0 < result["item_count"] <= 4


def test_relational_query_keeps_multiple_global_entities(builder):
    result = builder.build_context("Qual a relação entre MegaDesk e FaYerS?")
    assert result["route"] == "relational"
    assert set(result["entities"]) == {"MegaDesk", "FaYerS"}
    assert "MegaDesk" in result["context"] and "FaYerS" in result["context"]


def test_low_confidence_falls_back_to_bounded_standard_policy(builder):
    result = builder.build_context("Explique torque em linguagem simples")
    diagnostics = result["context_diagnostics"]
    assert result["route"] == "fallback_standard"
    assert diagnostics["confidence"] < .5
    assert diagnostics["tools_mode"] == "directed"
    assert result["item_count"] <= 3


def test_diagnostics_never_contain_query_or_memory_values(builder):
    secret_query = "O que você sabe sobre MegaDesk?"
    result = builder.build_context(secret_query)
    diagnostics = str(result["context_diagnostics"])
    assert secret_query not in diagnostics
    assert "100 clientes pagantes" not in diagnostics


def test_live_voice_retrieval_uses_the_same_context_policy(builder):
    cases = (
        ("Olá Verônica, está me ouvindo?", "minimal"),
        ("O que você sabe sobre o MegaDesk?", "entity_lookup"),
        ("Qual a relação entre MegaDesk e FaYerS?", "relational"),
        ("Analise uma estratégia empresarial detalhada", "complex_task"),
    )
    for query, expected in cases:
        result = builder.build_context(query, channel="voice")
        assert result["route"] == expected
    assert builder.build_context(cases[0][0], channel="voice")["context"] == ""


def test_memory_diagnostic_log_does_not_print_query_or_entity(builder, capsys):
    query = "O que você sabe sobre MegaDesk?"
    builder.build_context(query, channel="voice")
    captured = capsys.readouterr().out
    assert query not in captured
    assert "MegaDesk" not in captured


def test_context_build_does_not_modify_memory_files(builder):
    before = {path: path.read_bytes() for path in builder.memory.storage_dir.glob("*.json")}
    builder.build_context("O que é o MegaDesk?")
    after = {path: path.read_bytes() for path in builder.memory.storage_dir.glob("*.json")}
    assert after == before
