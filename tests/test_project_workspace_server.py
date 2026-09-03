from fastapi.testclient import TestClient

import server
from project_workspace import ProjectWorkspaceService


def test_workspace_http_flow_uses_safe_service(tmp_path, monkeypatch):
    service = ProjectWorkspaceService(tmp_path / "project_workspaces.json")
    root = tmp_path / "real-root"
    root.mkdir()
    monkeypatch.setattr(server, "project_workspaces", service)
    client = TestClient(server.app)

    initial = client.get("/api/project-workspaces").json()
    assert initial["ok"] is True and len(initial["projects"]) == 4

    configured = client.put(
        "/api/project-workspaces/fayers/root", json={"root_path": str(root)}
    ).json()
    assert configured["project"]["status"] == "available"

    created = client.post(
        "/api/project-workspaces/fayers/folders", json={"parent_path": "", "name": "Documentos"}
    ).json()
    assert created["item"]["relative_path"] == "Documentos"

    note = client.post(
        "/api/project-workspaces/fayers/text-files",
        json={"parent_path": "Documentos", "name": "leia-me.md", "content": "conteúdo"},
    ).json()
    assert note["item"]["extension"] == ".md"

    listed = client.get("/api/project-workspaces/fayers/directory", params={"path": "Documentos"}).json()
    assert [item["name"] for item in listed["items"]] == ["leia-me.md"]


def test_workspace_http_returns_structured_security_error(tmp_path, monkeypatch):
    service = ProjectWorkspaceService(tmp_path / "project_workspaces.json")
    root = tmp_path / "real-root"
    root.mkdir()
    service.configure_root("fayers", str(root))
    monkeypatch.setattr(server, "project_workspaces", service)
    response = TestClient(server.app).get(
        "/api/project-workspaces/fayers/directory", params={"path": "../../Windows"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "path_outside_project"


def test_workspace_http_creates_and_unlinks_dynamic_project_without_deleting_root(tmp_path, monkeypatch):
    service = ProjectWorkspaceService(tmp_path / "project_workspaces.json")
    root = tmp_path / "linked-root"
    root.mkdir()
    marker = root / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(server, "project_workspaces", service)
    client = TestClient(server.app)

    created = client.post("/api/project-workspaces", json={
        "name": "Projeto Dinâmico", "root_path": str(root), "description": "fixture", "type": "general"
    })
    assert created.status_code == 200
    project_id = created.json()["project"]["id"]
    assert len(client.get("/api/project-workspaces").json()["projects"]) == 5

    removed = client.delete(f"/api/project-workspaces/{project_id}").json()
    assert removed["result"]["files_deleted"] is False and marker.exists()


def test_workspace_http_creates_project_without_root(tmp_path, monkeypatch):
    service = ProjectWorkspaceService(tmp_path / "project_workspaces.json")
    monkeypatch.setattr(server, "project_workspaces", service)
    client = TestClient(server.app)

    response = client.post("/api/project-workspaces", json={"name": "Workspace sem Pasta"})

    assert response.status_code == 200
    project = response.json()["project"]
    assert project["root_path"] is None and project["status"] == "not_configured"
    assert service.get_project(project["id"])["root_path"] is None
