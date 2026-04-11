from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .models import ParsedSegment


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    content: str
    page_number: int | None
    section_title: str | None
    chunk_index: int


def build_chunks(
    relative_path: str,
    segments: list[ParsedSegment],
    *,
    chunk_size: int = 1200,
    overlap: int = 180,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    index = 0
    for segment in segments:
        text = (segment.text or "").strip()
        if not text:
            continue

        start = 0
        while start < len(text):
            end = min(len(text), start + chunk_size)
            if end < len(text):
                # Prefer breaking at natural boundaries to avoid jagged snippets.
                for sep in ("\n\n", "\n", ". ", " "):
                    pos = text.rfind(sep, start, end)
                    if pos > start + 250:
                        end = pos + (2 if sep == ". " else len(sep))
                        break

            content = text[start:end].strip()
            if content:
                digest = hashlib.sha256(
                    f"{relative_path}:{index}:{content}".encode("utf-8")
                ).hexdigest()[:16]
                chunks.append(
                    Chunk(
                        chunk_id=f"{relative_path}::{digest}",
                        content=content,
                        page_number=segment.page_number,
                        section_title=segment.section_title,
                        chunk_index=index,
                    )
                )
                index += 1

            if end >= len(text):
                break
            start = max(start + 1, end - overlap)

    return chunks
