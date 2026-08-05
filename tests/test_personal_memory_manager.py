import json

import pytest

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
    eager_categories = set(PersonalMemoryManager.CATEGORIES) - {"aliases", "relations"}
    assert all((storage / f"{name}.json").exists() for name in eager_categories)
    assert not (storage / "aliases.json").exists()
    assert not (storage / "relations.json").exists()


def test_alias_storage_is_created_only_when_first_alias_is_added(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.save_project("Orion", {"name": "Orion"})
    assert not (tmp_path / "aliases.json").exists()
    manager.add_entity_alias("Orion", "Projeto O")
    assert (tmp_path / "aliases.json").exists()


def sanitized_entities(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.save_project("Alpha", {"name": "Alpha"})
    manager.save_project("Beta", {"name": "Beta"})
    manager.save_person("Casey", {"name": "Casey"})
    manager._set("profile", "name", "Jordan")
    return manager


def test_alias_transaction_creates_sanitized_backup_only_for_real_change(tmp_path):
    manager = sanitized_entities(tmp_path)
    created = manager.add_entity_alias("Alpha", "A-One")
    backup_root = tmp_path / "backups"
    assert created["changed"] is True
    assert created["backup_id"] and "/" not in created["backup_id"] and "\\" not in created["backup_id"]
    assert (backup_root / created["backup_id"]).is_dir()
    backup_count = len(list(backup_root.iterdir()))

    repeated = manager.add_entity_alias("Alpha", "a one")
    canonical_noop = manager.add_entity_alias("Alpha", "Alpha")
    assert repeated["changed"] is canonical_noop["changed"] is False
    assert repeated["backup_id"] is canonical_noop["backup_id"] is None
    assert len(list(backup_root.iterdir())) == backup_count


@pytest.mark.parametrize("alias", ["", "...", "---"])
def test_alias_rejects_empty_or_punctuation_without_backup(tmp_path, alias):
    manager = sanitized_entities(tmp_path)
    with pytest.raises(ValueError):
        manager.add_entity_alias("Alpha", alias)
    assert not (tmp_path / "backups").exists()


def test_alias_rejects_unknown_canonical_and_canonical_name_conflict(tmp_path):
    manager = sanitized_entities(tmp_path)
    with pytest.raises(ValueError, match="Unknown canonical entity"):
        manager.add_entity_alias("Missing", "Alias")
    with pytest.raises(ValueError, match="another canonical entity"):
        manager.add_entity_alias("Alpha", "Beta")
    assert not (tmp_path / "backups").exists()


def test_alias_rejects_other_owner_and_ambiguous_normalized_alias(tmp_path):
    manager = sanitized_entities(tmp_path)
    manager.add_entity_alias("Alpha", "Shared Name")
    backup_count = len(list((tmp_path / "backups").iterdir()))
    with pytest.raises(ValueError, match="another entity"):
        manager.add_entity_alias("Beta", "shared-name")
    manager._data["aliases"] = {"Beta": ["Ambiguous"], "Casey": ["ambiguous!"]}
    with pytest.raises(ValueError, match="(?i)ambiguous"):
        manager.add_entity_alias("Alpha", "AMBIGUOUS")
    assert len(list((tmp_path / "backups").iterdir())) == backup_count


def test_alias_supports_people_and_profile_entities(tmp_path):
    manager = sanitized_entities(tmp_path)
    assert manager.add_entity_alias("Casey", "C")["category"] == "people"
    assert manager.add_entity_alias("Jordan", "J")["category"] == "profile"
    from memory.entity_resolver import EntityResolver
    assert EntityResolver(manager).resolve_entity("Fale com J") == {"name": "Jordan", "category": "profile"}


def test_alias_write_failure_preserves_backup_and_rolls_back_memory(tmp_path, monkeypatch):
    manager = sanitized_entities(tmp_path)
    monkeypatch.setattr(manager, "_save", lambda _category: (_ for _ in ()).throw(OSError("write failed")))
    with pytest.raises(OSError, match="write failed"):
        manager.add_entity_alias("Alpha", "A-One")
    assert manager.get_category("aliases") == {}
    assert len(list((tmp_path / "backups").iterdir())) == 1


def test_backup_retention_is_limited(tmp_path):
    manager = sanitized_entities(tmp_path)
    manager.MAX_BACKUPS = 2
    for alias in ("One", "Two", "Three"):
        manager.add_entity_alias("Alpha", alias)
    assert len(list((tmp_path / "backups").iterdir())) == 2


def relation_payload(**overrides):
    payload = {
        "source_entity": "Alpha", "relation_type": "operates_for", "target_entity": "Beta",
        "status": "planned", "summary": "Alpha will operate for Beta.",
        "source": "explicit_user_statement", "confidence": 1.0, "importance": "high",
    }
    payload.update(overrides)
    return payload


def test_relation_is_transactional_utc_and_idempotent(tmp_path):
    manager = sanitized_entities(tmp_path)
    created = manager.add_entity_relation(**relation_payload())
    record = manager.get_category("relations")[created["relation_id"]]
    backup_count = len(list((tmp_path / "backups").iterdir()))
    repeated = manager.add_entity_relation(**relation_payload())
    assert created["changed"] is True and created["backup_id"]
    assert repeated == {"relation_id": created["relation_id"], "changed": False, "backup_id": None}
    assert record["recorded_at"].endswith("+00:00") and record["updated_at"].endswith("+00:00")
    assert record["entities"] == ["Alpha", "Beta"]
    assert len(list((tmp_path / "backups").iterdir())) == backup_count


def test_relation_rejects_unknown_self_and_conflicting_records_without_backup(tmp_path):
    manager = sanitized_entities(tmp_path)
    with pytest.raises(ValueError, match="Unknown canonical entity"):
        manager.add_entity_relation(**relation_payload(target_entity="Missing"))
    with pytest.raises(ValueError, match="Self-relations"):
        manager.add_entity_relation(**relation_payload(target_entity="Alpha"))
    assert not (tmp_path / "backups").exists()
    manager.add_entity_relation(**relation_payload())
    backup_count = len(list((tmp_path / "backups").iterdir()))
    with pytest.raises(ValueError, match="Conflicting relation"):
        manager.add_entity_relation(**relation_payload(summary="Conflicting statement."))
    assert len(list((tmp_path / "backups").iterdir())) == backup_count


def test_relation_write_failure_preserves_backup_and_hides_content_from_logs(tmp_path, monkeypatch, capsys):
    manager = sanitized_entities(tmp_path)
    monkeypatch.setattr(manager, "_save", lambda _category: (_ for _ in ()).throw(OSError("write failed")))
    with pytest.raises(OSError, match="write failed"):
        manager.add_entity_relation(**relation_payload(summary="private relation marker"))
    assert manager.get_category("relations") == {}
    assert len(list((tmp_path / "backups").iterdir())) == 1
    assert "private relation marker" not in capsys.readouterr().out


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
