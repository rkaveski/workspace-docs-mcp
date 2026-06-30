from __future__ import annotations

import hashlib
import os
from enum import Enum
from pathlib import Path
from typing import Final

from platformdirs import user_cache_dir

# rag_dir resolution -----------------------------------------------------------

RAG_DIRNAME_DEFAULT: Final = ".rag"
# Sentinel value for `rag_dir` that redirects the index to a per-workspace
# location under the OS cache directory instead of inside the workspace tree.
RAG_DIR_CACHE_SENTINEL: Final = "cache"
APP_CACHE_NAME: Final = "workspace-docs-mcp"

# Length of the workspace-path hash used to disambiguate cache directories.
_CACHE_KEY_HASH_LEN: Final = 8


class JournalMode(str, Enum):
    """SQLite journal modes relevant to this server."""

    WAL = "WAL"
    DELETE = "DELETE"
    TRUNCATE = "TRUNCATE"


# Admins on mapped network drives (which we cannot always auto-detect) can force
# a safe journal mode explicitly. WAL is unsupported on network filesystems.
ENV_JOURNAL_MODE: Final = "WORKSPACE_DOCS_SQLITE_JOURNAL_MODE"


def resolve_rag_dir(workspace_root: Path, setting: str | None) -> Path:
    """Resolve the configured `rag_dir` setting to a concrete directory.

    Modes:
      - default / empty: ``<workspace_root>/.rag`` (self-contained, portable)
      - ``"cache"``: a per-workspace directory under the OS cache dir
      - any other value: treated as a path (absolute, or relative to the
        workspace root); ``~`` is expanded
    """
    value = (setting or "").strip()
    if not value or value == RAG_DIRNAME_DEFAULT:
        return workspace_root / RAG_DIRNAME_DEFAULT

    if value == RAG_DIR_CACHE_SENTINEL:
        return _cache_rag_dir(workspace_root)

    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    return candidate.resolve()


def _cache_rag_dir(workspace_root: Path) -> Path:
    base = Path(user_cache_dir(APP_CACHE_NAME))
    # Case-fold the path so case-insensitive filesystems (Windows/macOS) do not
    # produce two cache directories for what is really one workspace.
    digest = hashlib.sha256(str(workspace_root).casefold().encode("utf-8")).hexdigest()
    key = f"{workspace_root.name}-{digest[:_CACHE_KEY_HASH_LEN]}"
    return base / key


# Journal-mode selection -------------------------------------------------------


def is_network_path(path: Path) -> bool:
    """Best-effort detection of a network/UNC path.

    We can reliably detect Windows UNC paths (``\\\\server\\share``). Mapped
    network drives (e.g. ``H:``) are indistinguishable from local drives without
    platform-specific calls, so those are handled via ``ENV_JOURNAL_MODE``.
    """
    raw = str(path)
    if raw.startswith("\\\\") or raw.startswith("//"):
        return True
    drive = getattr(path, "drive", "")
    return drive.startswith("\\\\") or drive.startswith("//")


def journal_mode_for(rag_dir: Path) -> JournalMode:
    forced = os.getenv(ENV_JOURNAL_MODE, "").strip().upper()
    if forced:
        try:
            return JournalMode(forced)
        except ValueError:
            # Unknown override value: ignore and fall through to auto-detection.
            pass

    if is_network_path(rag_dir):
        return JournalMode.DELETE
    return JournalMode.WAL
