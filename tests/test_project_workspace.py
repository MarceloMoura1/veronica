import json
from pathlib import Path

import pytest

from project_workspace import ProjectWorkspaceError, ProjectWorkspaceService


@pytest.fixture
def service(tmp_path):
    return ProjectWorkspaceService(tmp_path / "data" / "project_workspaces.json")


def configure(service, tmp_path, project_id="fayers"):
    root = tmp_path / "workspace-root"
    root.mkdir()
    service.configure_root(project_id, str(root))
    return root


def assert_error(code, call):
    with pytest.raises(ProjectWorkspaceError) as caught:
        call()
    assert caught.value.code == code


def test_initial_config_has_only_four_unconfigured_roots(service):
    projects = service.list_projects()
    assert [(item["id"], item["name"]) for item in projects] == [
        ("megadesk", "MegaDesk"),
        ("fayers", "Fayers"),
        ("veronica", "Veronica"),
        ("cad_projects", "Projetos CAD"),
    ]
    assert all(item["status"] == "not_configured" and item["root_path"] is None for item in projects)
    assert {item["id"] for item in json.loads(service.config_path.read_text(encoding="utf-8"))["projects"]} == {
        "megadesk", "fayers", "veronica", "cad_projects"
    }
    assert next(item for item in projects if item["id"] == "cad_projects")["role"] == "cad_default"


def test_bootstrap_without_runtime_json_persists_dynamic_workspace(tmp_path):
    config = tmp_path / "data" / "project_workspaces.json"
    assert not config.exists()

    initial_service = ProjectWorkspaceService(config)
    initial_projects = initial_service.list_projects()
    assert [(item["id"], item["name"]) for item in initial_projects] == [
        ("megadesk", "MegaDesk"),
        ("fayers", "Fayers"),
        ("veronica", "Veronica"),
        ("cad_projects", "Projetos CAD"),
    ]
    assert all(item["root_path"] is None for item in initial_projects)

    root = tmp_path / "dynamic-root"
    root.mkdir()
    created = initial_service.create_workspace("Workspace de Teste", root_path=str(root))

    reloaded_projects = ProjectWorkspaceService(config).list_projects()
    assert len(reloaded_projects) == 5
    assert next(item for item in reloaded_projects if item["id"] == created["id"])["root_path"] == str(root.resolve())


@pytest.mark.parametrize("project_id", ["megadesk", "fayers", "veronica", "cad_projects"])
def test_each_seed_can_be_configured_to_an_existing_temporary_root(service, tmp_path, project_id):
    root = tmp_path / f"root-{project_id}"
    root.mkdir()
    configured = service.configure_root(project_id, str(root))
    assert configured["root_path"] == str(root.resolve())
    assert configured["status"] == "available"


def test_root_unavailable_and_unconfigured_are_structured(service, tmp_path):
    assert_error("project_not_configured", lambda: service.list_directory("fayers"))
    root = configure(service, tmp_path)
    root.rmdir()
    assert_error("project_root_unavailable", lambda: service.list_directory("fayers"))


@pytest.mark.parametrize("path", ["../escape", "folder/../../escape", "C:\\Windows", "D:/outside", "/etc"])
def test_traversal_absolute_and_drive_escape_are_rejected(service, tmp_path, path):
    configure(service, tmp_path)
    assert_error("path_outside_project", lambda: service.list_directory("fayers", path))


@pytest.mark.parametrize("name", ["", "..", "bad:name", "bad/name", "CON", "LPT1.txt", "trailing.", "trailing "])
def test_invalid_windows_names_are_rejected(service, tmp_path, name):
    configure(service, tmp_path)
    assert_error("invalid_name", lambda: service.create_folder("fayers", "", name))


def test_symlink_cannot_escape_root(service, tmp_path):
    root = configure(service, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable in this environment")
    assert_error("path_outside_project", lambda: service.list_directory("fayers", "linked"))
    assert all(item["name"] != "linked" for item in service.list_directory("fayers")["items"])


def test_permission_denied_is_structured(service, tmp_path, monkeypatch):
    root = configure(service, tmp_path)
    original = Path.iterdir

    def denied(path):
        if path == root.resolve():
            raise PermissionError("fixture")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", denied)
    assert_error("permission_denied", lambda: service.list_directory("fayers"))


def test_filesystem_is_source_of_truth_and_text_operations_are_real(service, tmp_path):
    root = configure(service, tmp_path)
    first = service.create_folder("fayers", "", "Clientes")
    assert first["relative_path"] == "Clientes"
    service.create_folder("fayers", "Clientes", "Cliente Real")
    service.create_folder("fayers", "Clientes/Cliente Real", "Projetos")
    note = service.save_text_file("fayers", "Clientes/Cliente Real", "notas.md", "conteúdo explícito")
    assert note["relative_path"] == "Clientes/Cliente Real/notas.md"
    assert (root / "Clientes" / "Cliente Real" / "notas.md").read_text(encoding="utf-8") == "conteúdo explícito"

    external = root / "adicionado-pelo-windows.txt"
    external.write_text("externo", encoding="utf-8")
    listed = service.list_directory("fayers")
    assert {item["name"] for item in listed["items"]} == {"Clientes", "adicionado-pelo-windows.txt"}


def test_rename_and_duplicate_creation_are_safe(service, tmp_path):
    root = configure(service, tmp_path)
    service.create_folder("fayers", "", "Original")
    renamed = service.rename_item("fayers", "Original", "Renomeada")
    assert renamed["relative_path"] == "Renomeada" and (root / "Renomeada").is_dir()
    assert_error("already_exists", lambda: service.create_folder("fayers", "", "Renomeada"))


def test_only_txt_and_markdown_can_be_written(service, tmp_path):
    configure(service, tmp_path)
    assert_error("invalid_name", lambda: service.save_text_file("fayers", "", "model.stl", "bytes não permitidos"))


def test_dynamic_workspace_links_existing_folder_with_stable_uuid(service, tmp_path):
    root = tmp_path / "dynamic-root"
    root.mkdir()
    project = service.create_workspace(
        "Workspace Dinâmico", root_path=str(root), description="Arquivos reais", icon="book", project_type="study"
    )
    assert project["id"] not in {item["id"] for item in service._default_config()["projects"]}
    assert project["name"] == "Workspace Dinâmico" and project["status"] == "available"
    assert project["description"] == "Arquivos reais" and project["icon"] == "book"
    assert service.get_project(project["id"])["id"] == project["id"]
    assert len(service.list_projects()) == 5


def test_workspace_without_root_can_be_reloaded_configured_and_listed(service, tmp_path):
    project = service.create_workspace("Workspace sem Storage", description="Somente lógico")
    assert project["root_path"] is None and project["status"] == "not_configured"
    project_id = project["id"]

    reloaded = ProjectWorkspaceService(service.config_path)
    assert reloaded.get_project(project_id)["root_path"] is None
    assert_error("project_not_configured", lambda: reloaded.list_directory(project_id))

    root = tmp_path / "later-root"
    root.mkdir()
    reloaded.configure_root(project_id, str(root))
    listed = reloaded.list_directory(project_id, "")
    assert listed["items"] == [] and listed["relative_path"] == ""


def test_binding_existing_root_only_reads_and_does_not_change_contents(service, tmp_path):
    project = service.create_workspace("Workspace para Bind")
    root = tmp_path / "existing-root"
    root.mkdir()
    marker = root / "existing.txt"
    marker.write_text("preservado", encoding="utf-8")
    before = [(item.name, item.read_bytes() if item.is_file() else None) for item in root.iterdir()]

    configured = service.configure_root(project["id"], str(root))

    after = [(item.name, item.read_bytes() if item.is_file() else None) for item in root.iterdir()]
    assert configured["status"] == "available"
    assert before == after == [("existing.txt", b"preservado")]


def test_binding_unreadable_root_has_specific_operation_context(service, tmp_path, monkeypatch):
    project = service.create_workspace("Workspace sem Leitura")
    root = tmp_path / "unreadable-root"
    root.mkdir()
    original_iterdir = Path.iterdir

    def denied(path):
        if path == root.resolve():
            raise PermissionError("fixture")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", denied)
    with pytest.raises(ProjectWorkspaceError) as caught:
        service.configure_root(project["id"], str(root))
    assert caught.value.code == "permission_denied"
    assert caught.value.context == {"operation": "bind_existing_root"}
    assert service.get_project(project["id"])["root_path"] is None


def test_dynamic_workspace_can_create_root_and_unlink_without_deleting_it(service, tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    project = service.create_workspace("Operação Real", parent_path=str(parent), folder_name="Operacao")
    physical_root = parent / "Operacao"
    (physical_root / "arquivo.txt").write_text("permanece", encoding="utf-8")
    removed = service.remove_workspace(project["id"])
    assert removed["files_deleted"] is False
    assert physical_root.is_dir() and (physical_root / "arquivo.txt").exists()
    assert all(item["id"] != project["id"] for item in service.list_projects())


def test_legacy_map_config_is_migrated_to_dynamic_collection(tmp_path):
    config = tmp_path / "legacy.json"
    config.write_text(json.dumps({"version": 1, "projects": {"legacy": {
        "id": "legacy", "name": "Legacy", "root_path": None,
        "metadata": {"description": "migrado", "tags": [], "links": []},
    }}}), encoding="utf-8")
    migrated = ProjectWorkspaceService(config)
    assert migrated.list_projects()[0]["description"] == "migrado"
    stored = json.loads(config.read_text(encoding="utf-8"))
    assert stored["version"] == 2 and isinstance(stored["projects"], list)
