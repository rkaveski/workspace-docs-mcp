from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Literal

from .models import IMAGE_EXTENSIONS, ScopeType, SourceFile, SUPPORTED_EXTENSIONS

RefreshScopeMode = Literal["all", "workspace", "project", "auto_project"]


def resolve_workspace_root(workspace_root: str | None = None) -> Path:
    base = workspace_root or os.getenv("OPENCODE_WORKSPACE") or os.getcwd()
    root = Path(base).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Workspace root does not exist or is not a directory: {root}")
    return root


def resolve_refresh_scope(scope: str, project: str | None) -> tuple[RefreshScopeMode, str | None]:
    normalized = (scope or "auto").strip().lower()
    if normalized not in {"auto", "workspace", "project", "all"}:
        normalized = "auto"

    if normalized == "workspace":
        return "workspace", None
    if normalized == "project":
        return "project", project
    if normalized == "all":
        return "all", None

    if project:
        return "auto_project", project
    return "all", None


def discover_source_files(
    workspace_root: Path,
    *,
    scope_mode: RefreshScopeMode = "all",
    target_project: str | None = None,
) -> list[SourceFile]:
    roots = _resolve_doc_roots(workspace_root)
    allowed_extensions = _allowed_extensions()
    out: list[SourceFile] = []

    for docs_dir, scope_type, project_name in roots:
        if not _root_in_scope(scope_mode, target_project, scope_type, project_name):
            continue
        if docs_dir.exists() and docs_dir.is_dir():
            out.extend(
                _walk_docs(
                    workspace_root,
                    docs_dir,
                    scope_type,
                    project_name,
                    allowed_extensions,
                )
            )

    out.sort(key=lambda f: f.relative_path)
    return out


def infer_active_project(context_path: str | None, workspace_root: Path) -> str | None:
    if not context_path:
        return None
    context = Path(context_path).expanduser()
    if not context.is_absolute():
        context = (workspace_root / context).resolve()
    else:
        context = context.resolve()

    try:
        rel = context.relative_to(workspace_root)
    except ValueError:
        return None

    parts = rel.parts
    if len(parts) >= 2 and parts[0] == "projects":
        return parts[1]
    return None


def classify_requested_scope(
    scope: str,
    project: str | None,
    context_path: str | None,
    workspace_root: Path,
    known_projects: Iterable[str],
) -> tuple[str, str | None]:
    normalized = (scope or "auto").strip().lower()
    if normalized not in {"auto", "workspace", "project", "all"}:
        normalized = "auto"

    if normalized == "project":
        target = project or infer_active_project(context_path, workspace_root)
        return "project", target
    if normalized in {"workspace", "all"}:
        return normalized, project

    active_project = infer_active_project(context_path, workspace_root)
    if active_project:
        return "auto_project", active_project

    for name in known_projects:
        if project and project == name:
            return "auto_project", name

    return "auto_workspace", None


def _resolve_doc_roots(
    workspace_root: Path,
) -> list[tuple[Path, ScopeType, str | None]]:
    override = os.getenv("WORKSPACE_DOCS_ROOTS", "").strip()
    if not override:
        return _default_doc_roots(workspace_root)

    roots: list[tuple[Path, ScopeType, str | None]] = []
    for raw in (x.strip() for x in override.split(",")):
        if not raw:
            continue
        rel = raw.replace("\\", "/")
        path = (workspace_root / rel).resolve()
        if "projects/*/docs" in rel:
            projects_root = workspace_root / "projects"
            if projects_root.exists() and projects_root.is_dir():
                for child in projects_root.iterdir():
                    if not child.is_dir():
                        continue
                    project_docs = child / "docs"
                    roots.append((project_docs, "project", child.name))
        else:
            scope_type: ScopeType = "workspace"
            project_name: str | None = None
            parts = Path(rel).parts
            if len(parts) >= 3 and parts[0] == "projects" and parts[2] == "docs":
                scope_type = "project"
                project_name = parts[1]
            roots.append((path, scope_type, project_name))

    return roots or _default_doc_roots(workspace_root)


def _default_doc_roots(workspace_root: Path) -> list[tuple[Path, ScopeType, str | None]]:
    roots: list[tuple[Path, ScopeType, str | None]] = []
    docs_root = workspace_root / "docs"
    roots.append((docs_root, "workspace", None))

    projects_root = workspace_root / "projects"
    if projects_root.exists() and projects_root.is_dir():
        for child in projects_root.iterdir():
            if not child.is_dir():
                continue
            roots.append((child / "docs", "project", child.name))
    return roots


def _allowed_extensions() -> set[str]:
    exts = set(SUPPORTED_EXTENSIONS)
    enable_docx = os.getenv("WORKSPACE_DOCS_ENABLE_DOCX", "true").strip().lower()
    if enable_docx in {"0", "false", "no", "off"}:
        exts.discard(".docx")

    # OCR is disabled by default in v3 for predictable first-index latency.
    enable_image_ocr = os.getenv("WORKSPACE_DOCS_ENABLE_IMAGE_OCR", "false").strip().lower()
    if enable_image_ocr in {"0", "false", "no", "off"}:
        exts.difference_update(IMAGE_EXTENSIONS)

    return exts


def _root_in_scope(
    scope_mode: RefreshScopeMode,
    target_project: str | None,
    scope_type: ScopeType,
    project_name: str | None,
) -> bool:
    if scope_mode == "all":
        return True
    if scope_mode == "workspace":
        return scope_type == "workspace"
    if scope_mode == "project":
        return scope_type == "project" and project_name == target_project
    if scope_mode == "auto_project":
        if scope_type == "workspace":
            return True
        return scope_type == "project" and project_name == target_project
    return True


def _walk_docs(
    workspace_root: Path,
    docs_dir: Path,
    scope_type: ScopeType,
    project_name: str | None,
    allowed_extensions: set[str],
) -> list[SourceFile]:
    out: list[SourceFile] = []
    for path in docs_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in allowed_extensions:
            continue
        stat = path.stat()
        out.append(
            SourceFile(
                workspace_root=workspace_root,
                absolute_path=path,
                relative_path=path.relative_to(workspace_root).as_posix(),
                scope_type=scope_type,
                project_name=project_name,
                mtime_ns=stat.st_mtime_ns,
                size_bytes=stat.st_size,
            )
        )
    return out
