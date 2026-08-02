import ast
from pathlib import Path

import pytest

from memory import PersonalMemoryManager


def test_import_profile(tmp_path):
    result = PersonalMemoryManager(tmp_path).import_memory_text("[PROFILE]\nname = Marcelo")
    assert result["counts"]["profile"] == 1
    assert PersonalMemoryManager(tmp_path)._get("profile", "name") == "Marcelo"


def test_import_preferred_title(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.import_memory_text("[PREFERENCES]\npreferred_title = Chefe")
    assert manager.get_preference("preferred_title") == "Chefe"


def test_import_person(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.import_memory_text("[PERSON:Christyan]\nrelationship = sócio")
    assert manager.get_person("Christyan") == {"relationship": "sócio"}


def test_import_project(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.import_memory_text("[PROJECT:FaYerS]\ndescription = Engenharia 3D")
    assert manager.get_project("FaYerS")["description"] == "Engenharia 3D"


def test_import_fact(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.import_memory_text("[FACTS]\nproject_orion_internal_code = ZX-4729")
    assert manager.get_fact("project_orion_internal_code") == "ZX-4729"


def test_import_persists_after_manager_restart(tmp_path):
    pack = """# VERONICA MEMORY PACK V1
[PROFILE]
name = Marcelo
[PERSON:Ana]
relationship = amiga
[PROJECT:Orion]
internal_code = ZX-4729
"""
    PersonalMemoryManager(tmp_path).import_memory_text(pack, "memory.txt")
    reloaded = PersonalMemoryManager(tmp_path)
    assert reloaded._get("profile", "name") == "Marcelo"
    assert reloaded.get_person("Ana")["relationship"] == "amiga"
    assert reloaded.get_project("Orion")["internal_code"] == "ZX-4729"


def test_reimport_merges_and_explicit_fields_win(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.import_memory_text("[PROJECT:Orion]\ndescription = Original\ninternal_code = OLD")
    result = manager.import_memory_text("[PROJECT:Orion]\ninternal_code = ZX-4729")
    assert manager.get_project("Orion") == {
        "description": "Original",
        "internal_code": "ZX-4729",
    }
    assert result["overwritten"] == ["projects.Orion.internal_code"]


def test_invalid_pack_does_not_corrupt_existing_memory(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.save_fact("stable", "value")
    before = (tmp_path / "facts.json").read_bytes()
    with pytest.raises(ValueError, match="No valid memory entries"):
        manager.import_memory_text("this is not a memory pack")
    assert (tmp_path / "facts.json").read_bytes() == before
    assert PersonalMemoryManager(tmp_path).get_fact("stable") == "value"


def test_backup_is_created_before_overwrite(tmp_path):
    manager = PersonalMemoryManager(tmp_path)
    manager.save_preference("preferred_title", "Marcelo")
    result = manager.import_memory_text("[PREFERENCES]\npreferred_title = Chefe")
    backups = list((tmp_path / "backups").glob("*/preferences.json"))
    assert result["backup_created"] is True
    assert len(backups) == 1
    assert '"Marcelo"' in backups[0].read_text(encoding="utf-8")
    assert manager.get_preference("preferred_title") == "Chefe"


def test_upload_memory_persists_without_sending_pack_to_gemini():
    server_path = Path(__file__).parents[1] / "backend" / "server.py"
    module = ast.parse(server_path.read_text(encoding="utf-8"))
    handler = next(
        node for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "upload_memory"
    )
    called_attributes = {
        node.func.attr for node in ast.walk(handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "import_memory_text" in called_attributes
    assert "send" not in called_attributes
