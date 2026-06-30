from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Iterable, Literal

from .config import DocSource, resolve_doc_sources
from .models import IMAGE_EXTENSIONS, ScopeType, SourceFile, SUPPORTED_EXTENSIONS

RefreshScopeMode = Literal["all", "workspace", "project", "auto_project"]


def resolve_workspace_root(workspace_root: str | None = None) -> Path:
    default_root = Path(os.getenv("OPENCODE_WORKSPACE") or os.getcwd()).expanduser().resolve()
    if not default_root.exists() or not default_root.is_dir():
        raise ValueError(f"Workspace root does not exist or is not a directory: {default_root}")

    if workspace_root is None:
        return default_root

    requested = Path(workspace_root).expanduser().resolve()
    if not requested.exists() or not requested.is_dir():
        raise ValueError(f"Workspace root does not exist or is not a directory: {requested}")

    allow_override = _env_bool("WORKSPACE_DOCS_ALLOW_WORKSPACE_ROOT_OVERRIDE", False)
    if requested != default_root and not allow_override:
        raise ValueError(
            "workspace_root override is disabled; set WORKSPACE_DOCS_ALLOW_WORKSPACE_ROOT_OVERRIDE=true "
            "to allow selecting a different root"
        )

    allowed_roots = _allowed_workspace_roots()
    if allowed_roots and not any(_is_within(requested, allowed) for allowed in allowed_roots):
        allowed_display = ", ".join(str(p) for p in allowed_roots)
        raise ValueError(f"Workspace root is outside WORKSPACE_DOCS_ALLOWED_ROOTS: {requested}; allowed: {allowed_display}")

    return requested


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


def doc_path_for(absolute_path: Path, workspace_root: Path) -> str:
    """Stable identity for an indexed file.

    Files inside the workspace keep a workspace-relative path (unchanged from
    earlier versions). Files outside the workspace use their absolute posix path,
    which is globally unique and survives the workspace being moved.
    """
    try:
        return absolute_path.relative_to(workspace_root).as_posix()
    except ValueError:
        return absolute_path.as_posix()


def discover_source_files(
    workspace_root: Path,
    sources: list[DocSource] | None = None,
    *,
    scope_mode: RefreshScopeMode = "all",
    target_project: str | None = None,
) -> list[SourceFile]:
    if sources is None:
        sources = resolve_doc_sources(workspace_root)
    allowed_extensions = _allowed_extensions()
    out: list[SourceFile] = []

    for source in sources:
        if not _source_in_scope(scope_mode, target_project, source.scope_type, source.project):
            continue
        if source.root.exists() and source.root.is_dir():
            out.extend(_walk_docs(workspace_root, source, allowed_extensions))

    out.sort(key=lambda f: f.relative_path)
    return out


def infer_active_project(
    context_path: str | None,
    workspace_root: Path,
    projects: tuple[tuple[Path, str], ...] = (),
) -> str | None:
    """Determine the active project from a working file's path.

    ``projects`` maps each project's resolved root directory to its identity. A
    working file is "in" a project when it lives under that project's root; the
    most specific (longest) matching root wins. With no configured project
    sources this returns ``None``.
    """
    if not context_path or not projects:
        return None
    context = Path(context_path).expanduser()
    if not context.is_absolute():
        context = (workspace_root / context).resolve()
    else:
        context = context.resolve()

    best_identity: str | None = None
    best_depth = -1
    for project_root, identity in projects:
        if _is_within(context, project_root):
            depth = len(project_root.parts)
            if depth > best_depth:
                best_depth = depth
                best_identity = identity
    return best_identity


def classify_requested_scope(
    scope: str,
    project: str | None,
    context_path: str | None,
    workspace_root: Path,
    known_projects: Iterable[str],
    projects: tuple[tuple[Path, str], ...] = (),
) -> tuple[str, str | None]:
    normalized = (scope or "auto").strip().lower()
    if normalized not in {"auto", "workspace", "project", "all"}:
        normalized = "auto"

    if normalized == "project":
        target = project or infer_active_project(context_path, workspace_root, projects)
        return "project", target
    if normalized in {"workspace", "all"}:
        return normalized, project

    active_project = infer_active_project(context_path, workspace_root, projects)
    if active_project:
        return "auto_project", active_project

    for name in known_projects:
        if project and project == name:
            return "auto_project", name

    return "auto_workspace", None


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


def _source_in_scope(
    scope_mode: RefreshScopeMode,
    target_project: str | None,
    scope_type: ScopeType,
    project: str | None,
) -> bool:
    if scope_mode == "all":
        return True
    if scope_mode == "workspace":
        return scope_type == "workspace"
    if scope_mode == "project":
        return scope_type == "project" and project == target_project
    if scope_mode == "auto_project":
        if scope_type == "workspace":
            return True
        return scope_type == "project" and project == target_project
    return True


def _walk_docs(
    workspace_root: Path,
    source: DocSource,
    allowed_extensions: set[str],
) -> list[SourceFile]:
    out: list[SourceFile] = []
    source_root = source.root
    for path in source_root.rglob("*"):
        # Never index symlinks to avoid cross-boundary content ingestion.
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in allowed_extensions:
            continue
        if source.includes and not _matches_includes(path, source_root, source.includes):
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        # Stay within this source's declared root (path-traversal boundary).
        if not _is_within(resolved, source_root):
            continue
        stat = path.stat()
        out.append(
            SourceFile(
                workspace_root=workspace_root,
                absolute_path=path,
                relative_path=doc_path_for(path, workspace_root),
                scope_type=source.scope_type,
                project=source.project,
                source_name=source.name,
                mtime_ns=stat.st_mtime_ns,
                size_bytes=stat.st_size,
                created_at_ns=_creation_time_ns(stat),
            )
        )
    return out


def _matches_includes(path: Path, source_root: Path, includes: tuple[str, ...]) -> bool:
    rel = path.relative_to(source_root).as_posix()
    name = path.name
    for pattern in includes:
        if (
            fnmatch.fnmatch(rel, pattern)
            or fnmatch.fnmatch(name, pattern)
            or fnmatch.fnmatch(rel, pattern.removeprefix("**/"))
        ):
            return True
    return False


def _creation_time_ns(stat: os.stat_result) -> int:
    # st_birthtime is available on macOS/BSD/Windows; Linux generally lacks it,
    # so fall back to the modification time (max(created, modified) then == mtime).
    birthtime = getattr(stat, "st_birthtime", None)
    if birthtime is None:
        return stat.st_mtime_ns
    return int(birthtime * 1_000_000_000)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _allowed_workspace_roots() -> list[Path]:
    raw = os.getenv("WORKSPACE_DOCS_ALLOWED_ROOTS", "").strip()
    if not raw:
        return []
    roots: list[Path] = []
    for item in (x.strip() for x in raw.split(",")):
        if not item:
            continue
        root = Path(item).expanduser().resolve()
        if root.exists() and root.is_dir():
            roots.append(root)
    return roots


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
