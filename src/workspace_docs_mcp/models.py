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
    project_name: str | None
    mtime_ns: int
    size_bytes: int


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
    project_name: str | None
    section_title: str | None
    page_number: int | None
    content: str
    score: float


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif"}
TABULAR_EXTENSIONS = {".csv", ".xlsx"}
SUPPORTED_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".pdf",
    ".docx",
    *TABULAR_EXTENSIONS,
    *IMAGE_EXTENSIONS,
}
