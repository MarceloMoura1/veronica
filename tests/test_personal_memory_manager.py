import json

from memory.personal_memory_manager import PersonalMemoryManager


def test_preference_survives_new_instance(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.save_preference("preferred_title", "Chefe")
    assert PersonalMemoryManager(tmp_path).get_preference("preferred_title") == "Chefe"


def test_project_code_survives_reload(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.save_project("Orion", {"internal_code": "ZX-4729"})
    assert PersonalMemoryManager(tmp_path).get_project("orion")["internal_code"] == "ZX-4729"


def test_relevant_context_excludes_unrelated_projects(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.save_project("FaYerS", {"secret": "do-not-send-fayers"})
    manager.save_project("MegaDesk", {"secret": "do-not-send-megadesk"})
    manager.save_project("Orion", {"internal_code": "ZX-4729"})
    context = manager.get_relevant_context("Qual é o código do Orion?")
    assert "Orion" in context and "ZX-4729" in context
    assert "FaYerS" not in context and "MegaDesk" not in context


def test_missing_storage_is_created(tmp_path):
    storage = tmp_path / "new" / "memory"
    PersonalMemoryManager(storage)
    assert all((storage / f"{name}.json").exists() for name in PersonalMemoryManager.CATEGORIES)


def test_invalid_json_uses_safe_fallback(tmp_path, capsys):
    invalid = tmp_path / "facts.json"
    invalid.write_text("{invalid", encoding="utf-8")
    manager = PersonalMemoryManager(tmp_path)
    assert manager.get_fact("anything") is None
    assert "safe fallback" in capsys.readouterr().out
    assert invalid.read_text(encoding="utf-8") == "{invalid"


def test_explicit_commands_are_captured(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.capture_explicit_memory("De agora em diante me chama de Chefe.")
    manager.capture_explicit_memory("Memorize que o código interno do Projeto Orion é ZX-4729.")
    reloaded = PersonalMemoryManager(tmp_path)
    assert reloaded.get_preference("preferred_title") == "Chefe"
    assert reloaded.get_project("Orion")["internal_code"] == "ZX-4729"
    data = json.loads((tmp_path / "preferences.json").read_text(encoding="utf-8"))
    assert data["preferred_title"] == "Chefe"


def test_title_question_retrieves_preference(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.save_preference("preferred_title", "Chefe")
    context = manager.get_relevant_context("Como você deve me chamar?")
    assert "preferred_title" in context and "Chefe" in context
