from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ScopeType = Literal["workspace", "project"]


@dataclass(frozen=True)
class SourceFile:
    workspace_root: Path
    absolute_path: Path
    relative_path: str
    scope_type: ScopeType
    # Project identity = the project's root path (normalized); None for workspace scope.
    project: str | None
    source_name: str
    mtime_ns: int
    size_bytes: int
    created_at_ns: int


@dataclass(frozen=True)
class ParsedSegment:
    text: str
    page_number: int | None = None
    section_title: str | None = None


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    relative_path: str
    scope_type: ScopeType
    project: str | None
    section_title: str | None
    page_number: int | None
    content: str
    score: float
    recency_at_ns: int | None = None


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif"}
TABULAR_EXTENSIONS = {".csv", ".xlsx"}
SUBTITLE_EXTENSIONS = {".srt"}
SUPPORTED_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".pdf",
    ".docx",
    *TABULAR_EXTENSIONS,
    *SUBTITLE_EXTENSIONS,
    *IMAGE_EXTENSIONS,
}
