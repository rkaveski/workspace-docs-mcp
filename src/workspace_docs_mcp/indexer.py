from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .chunking import build_chunks
from .embeddings import EmbeddingEngine
from .hashutil import sha256_file
from .models import SourceFile
from .parsers import ParserError, get_parser_capabilities, parse_document
from .scope import RefreshScopeMode, discover_source_files, resolve_refresh_scope
from .storage import Storage


@dataclass
class ReconcileResult:
    workspace_root: str
    added: int = 0
    changed: int = 0
    deleted: int = 0
    skipped: int = 0
    chunks_rebuilt: int = 0
    duration_ms: int = 0
    parser_failures: int = 0
    has_docs: bool = False
    embedding_mode: str = "unknown"
    total_candidates: int = 0
    processed_candidates: int = 0
    scope_mode: str = "all"
    project: str | None = None


class Indexer:
    def __init__(self, workspace_root: Path, embedding_engine: EmbeddingEngine) -> None:
        self.workspace_root = workspace_root
        self.embedding_engine = embedding_engine
        self.storage = Storage(workspace_root)
        self.manifest_path = self.storage.rag_dir / "manifest.json"

    def close(self) -> None:
        self.storage.close()

    def reconcile(
        self,
        force_hash_all: bool = False,
        scope: str = "all",
        project: str | None = None,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> ReconcileResult:
        started = time.time_ns()
        scope_mode, target_project = resolve_refresh_scope(scope, project)

        result = ReconcileResult(
            workspace_root=str(self.workspace_root),
            embedding_mode=self.embedding_engine.mode,
            scope_mode=scope_mode,
            project=target_project,
        )

        manifest = self._load_manifest()
        prev_files: dict[str, dict] = manifest.get("files", {})
        current_sources = discover_source_files(
            self.workspace_root,
            scope_mode=scope_mode,
            target_project=target_project,
        )
        result.has_docs = len(current_sources) > 0
        result.total_candidates = len(current_sources)

        current_map = {s.relative_path: s for s in current_sources}
        curr_paths = set(current_map.keys())

        managed_prev_paths = {
            relative_path
            for relative_path, meta in prev_files.items()
            if self._manifest_in_scope(meta, scope_mode, target_project)
        }

        new_manifest_files: dict[str, dict] = dict(prev_files)

        deleted = sorted(managed_prev_paths - curr_paths)
        for relative_path in deleted:
            if self.storage.delete_by_relative_path(relative_path):
                result.deleted += 1
            new_manifest_files.pop(relative_path, None)

        self._emit_progress(progress_callback, result)
        self._save_manifest_snapshot(new_manifest_files)

        checkpoint_every = 10
        for i, source in enumerate(current_sources, start=1):
            prev = prev_files.get(source.relative_path)
            file_hash: str
            needs_reindex = prev is None

            if prev is None:
                file_hash = sha256_file(source.absolute_path)
                result.added += 1
                needs_reindex = True
            else:
                unchanged_hint = (
                    int(prev.get("mtime_ns", -1)) == source.mtime_ns
                    and int(prev.get("size_bytes", -1)) == source.size_bytes
                )
                if unchanged_hint and not force_hash_all:
                    file_hash = str(prev.get("file_hash", ""))
                    needs_reindex = False
                else:
                    file_hash = sha256_file(source.absolute_path)
                    prev_hash = str(prev.get("file_hash", ""))
                    needs_reindex = file_hash != prev_hash
                    if needs_reindex:
                        result.changed += 1

            indexed_ok = True
            parse_error: str | None = None
            last_indexed_ns = int(prev.get("last_indexed_ns", time.time_ns())) if prev else time.time_ns()

            if needs_reindex:
                try:
                    chunks_count = self._index_source(source, file_hash)
                    result.chunks_rebuilt += chunks_count
                    last_indexed_ns = time.time_ns()
                except ParserError as exc:
                    indexed_ok = False
                    parse_error = str(exc)
                    result.skipped += 1
                    result.parser_failures += 1
                    self.storage.delete_by_relative_path(source.relative_path)
                    last_indexed_ns = time.time_ns()
                except Exception as exc:
                    indexed_ok = False
                    parse_error = str(exc)
                    result.skipped += 1
                    self.storage.delete_by_relative_path(source.relative_path)
                    last_indexed_ns = time.time_ns()

            new_manifest_files[source.relative_path] = {
                "scope_type": source.scope_type,
                "project_name": source.project_name,
                "mtime_ns": source.mtime_ns,
                "size_bytes": source.size_bytes,
                "file_hash": file_hash,
                "last_indexed_ns": last_indexed_ns,
                "indexed_ok": indexed_ok,
                "parse_error": parse_error,
            }

            result.processed_candidates = i
            if i % checkpoint_every == 0:
                self._save_manifest_snapshot(new_manifest_files)
                self._emit_progress(progress_callback, result)

        self._save_manifest_snapshot(new_manifest_files)
        result.duration_ms = int((time.time_ns() - started) / 1_000_000)
        self._emit_progress(progress_callback, result)
        return result

    def status(self) -> dict:
        manifest = self._load_manifest()
        files = manifest.get("files", {})
        projects = sorted(
            {
                v.get("project_name")
                for v in files.values()
                if v.get("scope_type") == "project" and v.get("project_name")
            }
        )
        return {
            "workspace_root": str(self.workspace_root),
            "docs_found": len(files) > 0,
            "projects_found": projects,
            "indexed_files": self.storage.count_indexed_files(),
            "last_reconcile_at_ns": manifest.get("updated_at_ns"),
            "embedding_mode": self.embedding_engine.mode,
            "embedding_available": self.embedding_engine.available,
            "manifest_path": str(self.manifest_path),
            "index_path": str(self.storage.db_path),
            "parsers": get_parser_capabilities(),
        }

    def _index_source(self, source: SourceFile, file_hash: str) -> int:
        segments = parse_document(source.absolute_path)
        chunks = build_chunks(source.relative_path, segments)
        embeddings = self.embedding_engine.embed_many([chunk.content for chunk in chunks])
        parser_name = source.absolute_path.suffix.lower().lstrip(".") or "text"
        return self.storage.upsert_file_chunks(
            source_file=source,
            file_hash=file_hash,
            parser_name=parser_name,
            chunks=chunks,
            embeddings=embeddings,
            embedding_model=self.embedding_engine.mode,
            indexed_at_ns=time.time_ns(),
        )

    def _load_manifest(self) -> dict:
        if not self.manifest_path.exists():
            return self._bootstrap_manifest_from_db(save_snapshot=True)

        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if isinstance(data.get("files"), dict):
                return data
        except Exception:
            pass

        return self._bootstrap_manifest_from_db(save_snapshot=True)

    def _bootstrap_manifest_from_db(self, save_snapshot: bool) -> dict:
        files: dict[str, dict] = {}
        for row in self.storage.iter_indexed_files():
            files[str(row["relative_path"])] = {
                "scope_type": row["scope_type"],
                "project_name": row["project_name"],
                "mtime_ns": int(row["modified_at_ns"]),
                "size_bytes": int(row["size_bytes"]),
                "file_hash": str(row["file_hash"]),
                "last_indexed_ns": int(row["indexed_at"]),
                "indexed_ok": True,
                "parse_error": None,
            }

        manifest = {
            "workspace_root": str(self.workspace_root),
            "updated_at_ns": (time.time_ns() if files else None),
            "files": files,
        }
        if save_snapshot:
            self._save_manifest(manifest)
        return manifest

    def _save_manifest_snapshot(self, files: dict[str, dict]) -> None:
        manifest_out = {
            "workspace_root": str(self.workspace_root),
            "updated_at_ns": time.time_ns(),
            "files": files,
        }
        self._save_manifest(manifest_out)

    def _save_manifest(self, data: dict) -> None:
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.manifest_path)

    def _manifest_in_scope(
        self,
        meta: dict,
        scope_mode: RefreshScopeMode,
        target_project: str | None,
    ) -> bool:
        scope_type = meta.get("scope_type")
        project_name = meta.get("project_name")
        if scope_mode == "all":
            return True
        if scope_mode == "workspace":
            return scope_type == "workspace"
        if scope_mode == "project":
            return scope_type == "project" and project_name == target_project
        if scope_mode == "auto_project":
            return scope_type == "workspace" or (scope_type == "project" and project_name == target_project)
        return True

    def _emit_progress(self, progress_callback: Callable[[dict], None] | None, result: ReconcileResult) -> None:
        if not progress_callback:
            return
        payload = {
            "workspace_root": result.workspace_root,
            "scope_mode": result.scope_mode,
            "project": result.project,
            "processed_files": result.processed_candidates,
            "total_files": result.total_candidates,
            "added": result.added,
            "changed": result.changed,
            "deleted": result.deleted,
            "skipped": result.skipped,
            "parser_failures": result.parser_failures,
            "chunks_rebuilt": result.chunks_rebuilt,
            "duration_ms": result.duration_ms,
        }
        try:
            progress_callback(payload)
        except Exception:
            return
