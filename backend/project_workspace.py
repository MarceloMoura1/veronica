from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any


INITIAL_PROJECTS = (
    {"id": "megadesk", "name": "MegaDesk", "icon": "layers", "type": "general"},
    {"id": "fayers", "name": "Fayers", "icon": "boxes", "type": "general"},
    {"id": "veronica", "name": "Veronica", "icon": "cpu", "type": "general"},
    {"id": "cad_projects", "name": "Projetos CAD", "icon": "cad", "type": "general", "role": "cad_default"},
)
INVALID_NAME_CHARS = set('<>:"/\\|?*')
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


class ProjectWorkspaceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, context: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context
        self.status = status

    def payload(self) -> dict[str, Any]:
        error = {"code": self.code, "message": self.message}
        if self.context:
            error["context"] = self.context
        return {"ok": False, "error": error}


class ProjectWorkspaceService:
    """Safe filesystem facade for configured project roots."""

    def __init__(self, config_path: str | Path | None = None):
        repo_root = Path(__file__).resolve().parents[1]
        self.config_path = Path(config_path) if config_path else repo_root / "data" / "project_workspaces.json"
        self._lock = threading.RLock()
        self._ensure_config()

    def _default_config(self) -> dict[str, Any]:
        return {
            "version": 2,
            "projects": [
                {
                    **seed,
                    "root_path": None,
                    "description": "",
                    "created_at": None,
                    "metadata": {"tags": [], "links": []},
                }
                for seed in INITIAL_PROJECTS
            ],
        }

    def _ensure_config(self) -> None:
        with self._lock:
            if not self.config_path.exists():
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                self._write_config(self._default_config())
                return
            data = self._load_json()
            projects = data.get("projects") if isinstance(data, dict) else None
            if isinstance(projects, dict):
                migrated = []
                for project_id, project in projects.items():
                    item = dict(project)
                    item.setdefault("id", project_id)
                    metadata = item.pop("metadata", {})
                    item.setdefault("description", metadata.pop("description", ""))
                    item.setdefault("type", "general")
                    item.setdefault("icon", "folder")
                    item.setdefault("created_at", None)
                    item["metadata"] = metadata
                    migrated.append(item)
                data["projects"] = migrated
                data["version"] = 2
                self._write_config(data)

    def _load_json(self) -> dict[str, Any]:
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProjectWorkspaceError("io_error", "Não foi possível ler a configuração de projetos.", 500) from error

    def _read_config(self) -> dict[str, Any]:
        with self._lock:
            try:
                data = self._load_json()
            except ProjectWorkspaceError:
                raise
            projects = data.get("projects") if isinstance(data, dict) else None
            if not isinstance(projects, list):
                raise ProjectWorkspaceError("io_error", "Configuração de projetos inválida.", 500)
            return data

    def _write_config(self, data: dict[str, Any]) -> None:
        temporary = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.config_path)
        except OSError as error:
            raise ProjectWorkspaceError("io_error", "Não foi possível salvar a configuração de projetos.", 500) from error

    def _project_config(self, project_id: str) -> dict[str, Any]:
        project = next((item for item in self._read_config()["projects"] if item.get("id") == project_id), None)
        if not project:
            raise ProjectWorkspaceError("project_not_found", "Projeto não encontrado.", 404)
        return project

    @staticmethod
    def _status(project: dict[str, Any]) -> str:
        root_path = project.get("root_path")
        if not root_path:
            return "not_configured"
        root = Path(root_path)
        return "available" if root.exists() and root.is_dir() else "storage_unavailable"

    def list_projects(self) -> list[dict[str, Any]]:
        projects = self._read_config()["projects"]
        return [self._project_summary(project) for project in projects]

    def get_project(self, project_id: str) -> dict[str, Any]:
        return self._project_summary(self._project_config(project_id))

    def _project_summary(self, project: dict[str, Any]) -> dict[str, Any]:
        status = self._status(project)
        summary = {
            "id": project["id"],
            "name": project["name"],
            "status": status,
            "root_path": project.get("root_path"),
            "description": project.get("description", ""),
            "type": project.get("type", "general"),
            "icon": project.get("icon", "folder"),
            "role": project.get("role"),
            "created_at": project.get("created_at"),
            "metadata": project.get("metadata", {}),
            "item_count": None,
            "modified_at": None,
        }
        if status == "available":
            try:
                root = Path(project["root_path"])
                summary["item_count"] = sum(1 for _ in root.iterdir())
                summary["modified_at"] = self._iso_time(root.stat().st_mtime)
            except OSError:
                summary["status"] = "storage_unavailable"
        return summary

    def configure_root(self, project_id: str, root_path: str) -> dict[str, Any]:
        print(f"[PROJECT_WORKSPACE] action=bind_root project_id={project_id} stage=validate_existing status=requested")
        try:
            canonical = self._validate_existing_root(root_path, operation="bind_existing_root")
        except ProjectWorkspaceError as error:
            print(
                f"[PROJECT_WORKSPACE] action=bind_root project_id={project_id} "
                f"stage=validate_existing status=error code={error.code}"
            )
            raise

        with self._lock:
            data = self._read_config()
            project = next((item for item in data["projects"] if item.get("id") == project_id), None)
            if not project:
                raise ProjectWorkspaceError("project_not_found", "Projeto não encontrado.", 404)
            project["root_path"] = str(canonical)
            self._write_config(data)
        print(f"[PROJECT_WORKSPACE] action=bind_root project_id={project_id} stage=validate_existing status=success")
        return self.get_project(project_id)

    @staticmethod
    def _validate_existing_root(root_path: str, *, operation: str) -> Path:
        if not isinstance(root_path, str) or not root_path.strip():
            raise ProjectWorkspaceError("invalid_path", "Selecione uma pasta válida.")
        root = Path(root_path).expanduser()
        if not root.is_absolute():
            raise ProjectWorkspaceError("invalid_path", "A pasta do projeto deve usar um caminho absoluto.")
        if not root.exists() or not root.is_dir():
            raise ProjectWorkspaceError("project_root_unavailable", "A pasta selecionada não está disponível.", 404)
        try:
            canonical = root.resolve(strict=True)
            next(canonical.iterdir(), None)
            return canonical
        except PermissionError as error:
            raise ProjectWorkspaceError(
                "permission_denied",
                "A pasta selecionada não pode ser lida.",
                403,
                {"operation": operation},
            ) from error
        except OSError as error:
            raise ProjectWorkspaceError("project_root_unavailable", "A pasta selecionada não está disponível.", 404) from error

    @staticmethod
    def validate_project_name(name: str) -> str:
        if not isinstance(name, str) or not name.strip():
            raise ProjectWorkspaceError("invalid_name", "Informe o nome do projeto.")
        clean = name.strip()
        if len(clean) > 120 or any(ord(char) < 32 for char in clean):
            raise ProjectWorkspaceError("invalid_name", "O nome do projeto é inválido.")
        return clean

    def create_workspace(
        self,
        name: str,
        *,
        root_path: str | None = None,
        description: str = "",
        icon: str = "folder",
        project_type: str = "general",
        parent_path: str | None = None,
        folder_name: str | None = None,
    ) -> dict[str, Any]:
        clean_name = self.validate_project_name(name)
        allowed_icons = {"folder", "layers", "boxes", "cpu", "cad", "book", "briefcase"}
        clean_icon = icon if icon in allowed_icons else "folder"
        clean_type = project_type if project_type in {"general", "business", "study", "personal", "cad"} else "general"

        with self._lock:
            existing = self._read_config()["projects"]
            if any(item.get("name", "").casefold() == clean_name.casefold() for item in existing):
                raise ProjectWorkspaceError("already_exists", "Já existe um projeto com esse nome.", 409)

        if root_path and parent_path:
            raise ProjectWorkspaceError("invalid_path", "Escolha vincular uma pasta ou criar uma nova pasta.")
        if parent_path:
            parent = Path(parent_path).expanduser()
            if not parent.is_absolute() or not parent.exists() or not parent.is_dir():
                raise ProjectWorkspaceError("project_root_unavailable", "A pasta pai não está disponível.", 404)
            clean_folder = self.validate_name(folder_name or clean_name)
            target = parent.resolve(strict=True) / clean_folder
            try:
                target.mkdir()
            except FileExistsError as error:
                raise ProjectWorkspaceError("already_exists", "Já existe uma pasta com esse nome.", 409) from error
            except PermissionError as error:
                raise ProjectWorkspaceError("permission_denied", "Permissão negada.", 403) from error
            except OSError as error:
                raise ProjectWorkspaceError("io_error", "Não foi possível criar a pasta raiz.", 500) from error
            canonical_root = target.resolve(strict=True)
        elif root_path:
            canonical_root = self._validate_existing_root(root_path, operation="bind_existing_root")
        else:
            canonical_root = None

        now = datetime.now(timezone.utc).isoformat()
        project = {
            "id": str(uuid.uuid4()),
            "name": clean_name,
            "root_path": str(canonical_root) if canonical_root else None,
            "description": str(description).strip()[:1000],
            "created_at": now,
            "type": clean_type,
            "icon": clean_icon,
            "metadata": {"tags": [], "links": []},
        }
        with self._lock:
            data = self._read_config()
            if any(item.get("name", "").casefold() == clean_name.casefold() for item in data["projects"]):
                raise ProjectWorkspaceError("already_exists", "Já existe um projeto com esse nome.", 409)
            data["projects"].append(project)
            self._write_config(data)
        return self._project_summary(project)

    def remove_workspace(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._read_config()
            project = next((item for item in data["projects"] if item.get("id") == project_id), None)
            if not project:
                raise ProjectWorkspaceError("project_not_found", "Projeto não encontrado.", 404)
            data["projects"] = [item for item in data["projects"] if item.get("id") != project_id]
            self._write_config(data)
        return {"id": project_id, "name": project.get("name"), "root_path": project.get("root_path"), "files_deleted": False}

    def _root(self, project_id: str) -> Path:
        project = self._project_config(project_id)
        if not project.get("root_path"):
            raise ProjectWorkspaceError("project_not_configured", "Pasta não configurada.", 409)
        root = Path(project["root_path"])
        if not root.exists() or not root.is_dir():
            raise ProjectWorkspaceError("project_root_unavailable", "Unidade/pasta indisponível.", 409)
        try:
            return root.resolve(strict=True)
        except OSError as error:
            raise ProjectWorkspaceError("project_root_unavailable", "Unidade/pasta indisponível.", 409) from error

    @staticmethod
    def validate_name(name: str) -> str:
        if not isinstance(name, str):
            raise ProjectWorkspaceError("invalid_name", "Nome inválido.")
        clean = name.strip()
        if clean != name:
            raise ProjectWorkspaceError("invalid_name", "O nome não pode começar ou terminar com espaço.")
        if not clean or clean in {".", ".."} or any(char in INVALID_NAME_CHARS for char in clean):
            raise ProjectWorkspaceError("invalid_name", "O nome contém caracteres inválidos para o Windows.")
        if clean.endswith((".", " ")):
            raise ProjectWorkspaceError("invalid_name", "O nome não pode terminar com ponto ou espaço.")
        stem = clean.split(".", 1)[0].upper()
        if stem in RESERVED_NAMES or re.fullmatch(r"(?:COM|LPT)[1-9]", stem):
            raise ProjectWorkspaceError("invalid_name", "Este nome é reservado pelo Windows.")
        return clean

    @staticmethod
    def _relative_parts(relative_path: str | None) -> tuple[str, ...]:
        value = "" if relative_path is None else str(relative_path).strip()
        if not value or value == ".":
            return ()
        windows = PureWindowsPath(value)
        if windows.is_absolute() or windows.drive or value.startswith(("/", "\\")):
            raise ProjectWorkspaceError("path_outside_project", "O caminho deve ser relativo ao projeto.")
        normalized = value.replace("\\", "/")
        parts = tuple(part for part in normalized.split("/") if part)
        if any(part in {".", ".."} for part in parts):
            raise ProjectWorkspaceError("path_outside_project", "O caminho tenta sair da pasta do projeto.")
        for part in parts:
            ProjectWorkspaceService.validate_name(part)
        return parts

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        try:
            stat = path.lstat()
        except OSError:
            return False
        return path.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & 0x400)

    def _resolve(self, project_id: str, relative_path: str | None, *, must_exist: bool = True) -> tuple[Path, Path]:
        root = self._root(project_id)
        parts = self._relative_parts(relative_path)
        current = root
        for part in parts:
            current = current / part
            if current.exists() and self._is_reparse_point(current):
                raise ProjectWorkspaceError("path_outside_project", "Links simbólicos e junctions não podem ser navegados.")
        try:
            resolved = current.resolve(strict=must_exist)
        except FileNotFoundError as error:
            raise ProjectWorkspaceError("folder_not_found", "Pasta ou arquivo não encontrado.", 404) from error
        except PermissionError as error:
            raise ProjectWorkspaceError("permission_denied", "Permissão negada.", 403) from error
        except OSError as error:
            raise ProjectWorkspaceError("io_error", "Não foi possível acessar o caminho solicitado.", 500) from error
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ProjectWorkspaceError("path_outside_project", "O caminho está fora da pasta do projeto.") from error
        return root, resolved

    def list_directory(self, project_id: str, relative_path: str = "") -> dict[str, Any]:
        root, directory = self._resolve(project_id, relative_path)
        if not directory.is_dir():
            raise ProjectWorkspaceError("folder_not_found", "A pasta não foi encontrada.", 404)
        try:
            entries = [self._metadata(root, child) for child in directory.iterdir() if not self._is_reparse_point(child)]
        except PermissionError as error:
            raise ProjectWorkspaceError("permission_denied", "Permissão negada.", 403) from error
        except OSError as error:
            raise ProjectWorkspaceError("io_error", "Não foi possível listar esta pasta.", 500) from error
        entries.sort(key=lambda item: (item["kind"] != "directory", item["name"].casefold()))
        return {"project": self.get_project(project_id), "relative_path": "/".join(self._relative_parts(relative_path)), "items": entries}

    def create_folder(self, project_id: str, parent_path: str, name: str) -> dict[str, Any]:
        root, parent = self._resolve(project_id, parent_path)
        if not parent.is_dir():
            raise ProjectWorkspaceError("folder_not_found", "A pasta de destino não foi encontrada.", 404)
        clean_name = self.validate_name(name)
        target = parent / clean_name
        self._ensure_target_inside(root, target)
        try:
            target.mkdir()
        except FileExistsError as error:
            raise ProjectWorkspaceError("already_exists", "Já existe um item com esse nome.", 409) from error
        except PermissionError as error:
            raise ProjectWorkspaceError("permission_denied", "Permissão negada.", 403) from error
        except OSError as error:
            raise ProjectWorkspaceError("io_error", "Não foi possível criar a pasta.", 500) from error
        return self._metadata(root, target)

    def save_text_file(self, project_id: str, parent_path: str, name: str, content: str) -> dict[str, Any]:
        root, parent = self._resolve(project_id, parent_path)
        if not parent.is_dir():
            raise ProjectWorkspaceError("folder_not_found", "A pasta de destino não foi encontrada.", 404)
        clean_name = self.validate_name(name)
        if Path(clean_name).suffix.lower() not in {".txt", ".md"}:
            raise ProjectWorkspaceError("invalid_name", "Nesta fase, apenas arquivos .txt e .md podem ser salvos.")
        target = parent / clean_name
        self._ensure_target_inside(root, target)
        if target.exists():
            raise ProjectWorkspaceError("already_exists", "Já existe um item com esse nome.", 409)
        try:
            target.write_text(str(content), encoding="utf-8")
        except PermissionError as error:
            raise ProjectWorkspaceError("permission_denied", "Permissão negada.", 403) from error
        except OSError as error:
            raise ProjectWorkspaceError("io_error", "Não foi possível salvar o arquivo.", 500) from error
        return self._metadata(root, target)

    def rename_item(self, project_id: str, relative_path: str, name: str) -> dict[str, Any]:
        root, source = self._resolve(project_id, relative_path)
        if source == root:
            raise ProjectWorkspaceError("path_outside_project", "A raiz do projeto não pode ser renomeada.")
        clean_name = self.validate_name(name)
        target = source.with_name(clean_name)
        self._ensure_target_inside(root, target)
        if target.exists():
            raise ProjectWorkspaceError("already_exists", "Já existe um item com esse nome.", 409)
        try:
            source.rename(target)
        except PermissionError as error:
            raise ProjectWorkspaceError("permission_denied", "Permissão negada.", 403) from error
        except OSError as error:
            raise ProjectWorkspaceError("io_error", "Não foi possível renomear o item.", 500) from error
        return self._metadata(root, target)

    def get_open_target(self, project_id: str, relative_path: str) -> str:
        _, target = self._resolve(project_id, relative_path)
        return str(target)

    @staticmethod
    def _ensure_target_inside(root: Path, target: Path) -> None:
        try:
            target.resolve(strict=False).relative_to(root)
        except ValueError as error:
            raise ProjectWorkspaceError("path_outside_project", "O caminho está fora da pasta do projeto.") from error

    @staticmethod
    def _iso_time(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()

    @staticmethod
    def _metadata(root: Path, path: Path) -> dict[str, Any]:
        stat = path.stat()
        is_directory = path.is_dir()
        return {
            "name": path.name,
            "relative_path": path.relative_to(root).as_posix(),
            "kind": "directory" if is_directory else "file",
            "extension": "" if is_directory else path.suffix.lower(),
            "size": None if is_directory else stat.st_size,
            "modified_at": ProjectWorkspaceService._iso_time(stat.st_mtime),
        }
