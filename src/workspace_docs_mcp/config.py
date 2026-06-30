from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from .models import ScopeType
from .paths import RAG_DIRNAME_DEFAULT

CONFIG_FILENAME: Final = ".workspace-docs.toml"

# TOML keys. Array-of-tables uses the singular `source` (each entry is one
# source); list-valued keys are plural (`includes`).
KEY_RAG_DIR: Final = "rag_dir"
KEY_SOURCE: Final = "source"
KEY_NAME: Final = "name"
KEY_PATH: Final = "path"
KEY_SCOPE: Final = "scope"
KEY_PROJECT: Final = "project"
KEY_INCLUDES: Final = "includes"

_TOP_LEVEL_KEYS: Final = frozenset({KEY_RAG_DIR, KEY_SOURCE})
_SOURCE_KEYS: Final = frozenset({KEY_NAME, KEY_PATH, KEY_SCOPE, KEY_PROJECT, KEY_INCLUDES})

# Scope values (also the SourceFile.scope_type values).
SCOPE_WORKSPACE: Final = "workspace"
SCOPE_PROJECT: Final = "project"
_VALID_SCOPES: Final = frozenset({SCOPE_WORKSPACE, SCOPE_PROJECT})

# Security allowlist for sources that live outside the workspace.
ENV_ALLOWED_ROOTS: Final = "WORKSPACE_DOCS_ALLOWED_ROOTS"


@dataclass(frozen=True)
class DocSource:
    """A directory of documentation to index, with its scope metadata."""

    name: str
    root: Path
    scope_type: ScopeType
    # Project identity = the normalized project root path; None for workspace scope.
    project: str | None = None
    # Resolved absolute project root, used to detect the active project from a
    # working file's path (the file is "in" the project if it lives under this).
    project_root: Path | None = None
    includes: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkspaceConfig:
    rag_dir: str
    sources: list[DocSource] = field(default_factory=list)


class ConfigError(ValueError):
    """Raised when the workspace config is missing or invalid."""


def load_config(workspace_root: Path) -> WorkspaceConfig:
    """Load and validate the mandatory workspace configuration.

    Every workspace must provide a ``.workspace-docs.toml`` at its root that
    declares at least one ``[[sources]]`` entry. There is no implicit default
    layout. External source roots must resolve inside the workspace or inside
    ``WORKSPACE_DOCS_ALLOWED_ROOTS``.
    """
    config_path = workspace_root / CONFIG_FILENAME
    if not config_path.is_file():
        raise ConfigError(
            f"{CONFIG_FILENAME} is required but was not found at {config_path}. "
            f"Create it at the workspace root and declare at least one [[sources]] entry."
        )

    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Failed to read {CONFIG_FILENAME}: {exc}") from exc

    _reject_unknown_keys(data.keys(), _TOP_LEVEL_KEYS, location="top level")

    rag_dir = _parse_rag_dir(data.get(KEY_RAG_DIR))

    raw_sources = data.get(KEY_SOURCE)
    if not raw_sources:
        raise ConfigError(
            f"{CONFIG_FILENAME}: at least one [[{KEY_SOURCE}]] entry is required."
        )
    if not isinstance(raw_sources, list):
        raise ConfigError(f"{CONFIG_FILENAME}: '{KEY_SOURCE}' must be an array of tables.")

    allowed_roots = _allowed_roots()
    sources = [_parse_source(workspace_root, allowed_roots, raw, i) for i, raw in enumerate(raw_sources)]
    return WorkspaceConfig(rag_dir=rag_dir, sources=sources)


def resolve_doc_sources(workspace_root: Path) -> list[DocSource]:
    return load_config(workspace_root).sources


def _parse_rag_dir(value: object) -> str:
    if value is None:
        return RAG_DIRNAME_DEFAULT
    if not isinstance(value, str):
        raise ConfigError(f"{CONFIG_FILENAME}: '{KEY_RAG_DIR}' must be a string.")
    return value.strip() or RAG_DIRNAME_DEFAULT


def _parse_source(
    workspace_root: Path,
    allowed_roots: list[Path],
    raw: object,
    index: int,
) -> DocSource:
    where = f"{KEY_SOURCE}[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{CONFIG_FILENAME}: '{where}' must be a table.")
    _reject_unknown_keys(raw.keys(), _SOURCE_KEYS, location=f"'{where}'")

    raw_path = raw.get(KEY_PATH)
    if not raw_path or not isinstance(raw_path, str):
        raise ConfigError(f"{CONFIG_FILENAME}: '{where}' requires a string '{KEY_PATH}'.")

    scope = raw.get(KEY_SCOPE)
    if not isinstance(scope, str) or scope.strip().lower() not in _VALID_SCOPES:
        raise ConfigError(
            f"{CONFIG_FILENAME}: '{where}.{KEY_SCOPE}' must be "
            f"'{SCOPE_WORKSPACE}' or '{SCOPE_PROJECT}'."
        )
    scope = scope.strip().lower()

    raw_project = raw.get(KEY_PROJECT)
    if scope == SCOPE_PROJECT and not raw_project:
        raise ConfigError(
            f"{CONFIG_FILENAME}: '{where}' with {KEY_SCOPE}='{SCOPE_PROJECT}' requires '{KEY_PROJECT}' "
            f"(the project's root directory)."
        )
    if raw_project is not None and not isinstance(raw_project, str):
        raise ConfigError(f"{CONFIG_FILENAME}: '{where}.{KEY_PROJECT}' must be a string path.")

    includes = raw.get(KEY_INCLUDES) or []
    if not isinstance(includes, list) or not all(isinstance(p, str) for p in includes):
        raise ConfigError(f"{CONFIG_FILENAME}: '{where}.{KEY_INCLUDES}' must be a list of strings.")

    name = raw.get(KEY_NAME)
    if name is not None and not isinstance(name, str):
        raise ConfigError(f"{CONFIG_FILENAME}: '{where}.{KEY_NAME}' must be a string.")

    root = _resolve_source_root(workspace_root, raw_path)
    _ensure_allowed(root, workspace_root, allowed_roots, where)

    project_root: Path | None = None
    project_identity: str | None = None
    if scope == SCOPE_PROJECT:
        project_root = _resolve_source_root(workspace_root, str(raw_project))
        project_identity = _identity(project_root, workspace_root)

    resolved_name = (str(name).strip() if name else "") or root.name
    scope_type: ScopeType = SCOPE_PROJECT if scope == SCOPE_PROJECT else SCOPE_WORKSPACE
    return DocSource(
        name=resolved_name,
        root=root,
        scope_type=scope_type,
        project=project_identity,
        project_root=project_root,
        includes=tuple(includes),
    )


def _identity(path: Path, workspace_root: Path) -> str:
    """Stable string identity for a path: workspace-relative if inside, else absolute."""
    try:
        return path.relative_to(workspace_root).as_posix()
    except ValueError:
        return path.as_posix()


def _reject_unknown_keys(keys, allowed: frozenset[str], *, location: str) -> None:
    unknown = sorted(set(keys) - allowed)
    if unknown:
        allowed_display = ", ".join(sorted(allowed))
        raise ConfigError(
            f"{CONFIG_FILENAME}: unknown key(s) at {location}: {', '.join(unknown)}. "
            f"Allowed: {allowed_display}."
        )


def _resolve_source_root(workspace_root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    return candidate.resolve()


def _ensure_allowed(root: Path, workspace_root: Path, allowed_roots: list[Path], where: str) -> None:
    if _is_within(root, workspace_root):
        return
    if any(_is_within(root, allowed) for allowed in allowed_roots):
        return
    allowed_display = ", ".join(str(p) for p in allowed_roots) or "(none configured)"
    raise ConfigError(
        f"{CONFIG_FILENAME}: '{where}' path '{root}' is outside the workspace and not within "
        f"{ENV_ALLOWED_ROOTS}: {allowed_display}"
    )


def _allowed_roots() -> list[Path]:
    raw = os.getenv(ENV_ALLOWED_ROOTS, "").strip()
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


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
