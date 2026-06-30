from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .chunking import Chunk
from .models import SourceFile
from .paths import journal_mode_for


class Storage:
    def __init__(self, rag_dir: Path, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.rag_dir = rag_dir
        self.rag_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.rag_dir / "index.sqlite"
        self.journal_mode = journal_mode_for(self.rag_dir)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def close(self) -> None:
        self.conn.close()

    def _init_db(self) -> None:
        # WAL is unsupported on network filesystems; journal_mode_for falls back
        # to DELETE there (and honors an explicit override).
        self.conn.executescript(
            f"""
            PRAGMA journal_mode={self.journal_mode.value};
            PRAGMA synchronous=NORMAL;

            CREATE TABLE IF NOT EXISTS files (
              file_id INTEGER PRIMARY KEY,
              workspace_root TEXT NOT NULL,
              relative_path TEXT NOT NULL UNIQUE,
              abs_path TEXT NOT NULL,
              scope_type TEXT NOT NULL,
              project TEXT,
              source_name TEXT,
              file_hash TEXT NOT NULL,
              modified_at_ns INTEGER NOT NULL,
              created_at_ns INTEGER NOT NULL DEFAULT 0,
              size_bytes INTEGER NOT NULL,
              parser TEXT NOT NULL,
              indexed_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chunks (
              chunk_id TEXT PRIMARY KEY,
              file_id INTEGER NOT NULL,
              chunk_index INTEGER NOT NULL,
              content TEXT NOT NULL,
              content_len INTEGER NOT NULL,
              section_title TEXT,
              page_number INTEGER,
              start_offset INTEGER,
              end_offset INTEGER,
              FOREIGN KEY(file_id) REFERENCES files(file_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chunk_embeddings (
              chunk_id TEXT PRIMARY KEY,
              embedding_model TEXT NOT NULL,
              dim INTEGER NOT NULL,
              vector_json TEXT NOT NULL,
              FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
              chunk_id UNINDEXED,
              content
            );
            """
        )
        # Migrate before creating the project index, since it references the column.
        self._migrate_schema()
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_files_scope_project ON files(scope_type, project)")
        self.conn.commit()

    def _migrate_schema(self) -> None:
        existing_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(files)")}
        if "created_at_ns" not in existing_columns:
            self.conn.execute("ALTER TABLE files ADD COLUMN created_at_ns INTEGER NOT NULL DEFAULT 0")
        # Additive migration: existing in-repo rows keep their identity; source_name
        # backfills on the next reconcile, so no re-index is required on upgrade.
        if "source_name" not in existing_columns:
            self.conn.execute("ALTER TABLE files ADD COLUMN source_name TEXT")
        # The project identity moved from a free-text name to the project root path;
        # the column was renamed to match. Rename in place to preserve existing rows.
        if "project" not in existing_columns and "project_name" in existing_columns:
            self.conn.execute("ALTER TABLE files RENAME COLUMN project_name TO project")

    def get_known_projects(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT project FROM files WHERE project IS NOT NULL ORDER BY project"
        ).fetchall()
        return [str(r[0]) for r in rows if r[0]]

    def count_indexed_files(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()
        return int(row["c"] if row else 0)

    def iter_indexed_files(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
              relative_path,
              scope_type,
              project,
              source_name,
              modified_at_ns,
              size_bytes,
              file_hash,
              indexed_at,
              parser
            FROM files
            ORDER BY relative_path
            """
        ).fetchall()

    def upsert_file_chunks(
        self,
        source_file: SourceFile,
        file_hash: str,
        parser_name: str,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        embedding_model: str,
        indexed_at_ns: int,
    ) -> int:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings length mismatch")

        with self.conn:
            existing = self.conn.execute(
                "SELECT file_id FROM files WHERE relative_path = ?",
                (source_file.relative_path,),
            ).fetchone()

            if existing:
                file_id = int(existing["file_id"])
                self.conn.execute(
                    "DELETE FROM chunk_embeddings WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE file_id = ?)",
                    (file_id,),
                )
                for row in self.conn.execute("SELECT chunk_id FROM chunks WHERE file_id = ?", (file_id,)).fetchall():
                    self.conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (row["chunk_id"],))
                self.conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
                self.conn.execute(
                    """
                    UPDATE files
                    SET workspace_root = ?, abs_path = ?, scope_type = ?, project = ?, source_name = ?,
                        file_hash = ?, modified_at_ns = ?, created_at_ns = ?, size_bytes = ?, parser = ?, indexed_at = ?
                    WHERE file_id = ?
                    """,
                    (
                        str(source_file.workspace_root),
                        str(source_file.absolute_path),
                        source_file.scope_type,
                        source_file.project,
                        source_file.source_name,
                        file_hash,
                        source_file.mtime_ns,
                        source_file.created_at_ns,
                        source_file.size_bytes,
                        parser_name,
                        indexed_at_ns,
                        file_id,
                    ),
                )
            else:
                cur = self.conn.execute(
                    """
                    INSERT INTO files(
                      workspace_root, relative_path, abs_path, scope_type, project, source_name,
                      file_hash, modified_at_ns, created_at_ns, size_bytes, parser, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(source_file.workspace_root),
                        source_file.relative_path,
                        str(source_file.absolute_path),
                        source_file.scope_type,
                        source_file.project,
                        source_file.source_name,
                        file_hash,
                        source_file.mtime_ns,
                        source_file.created_at_ns,
                        source_file.size_bytes,
                        parser_name,
                        indexed_at_ns,
                    ),
                )
                file_id = int(cur.lastrowid)

            for i, chunk in enumerate(chunks):
                self.conn.execute(
                    """
                    INSERT INTO chunks(
                      chunk_id, file_id, chunk_index, content, content_len,
                      section_title, page_number, start_offset, end_offset
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.chunk_id,
                        file_id,
                        chunk.chunk_index,
                        chunk.content,
                        len(chunk.content),
                        chunk.section_title,
                        chunk.page_number,
                        0,
                        len(chunk.content),
                    ),
                )
                self.conn.execute(
                    "INSERT INTO chunk_embeddings(chunk_id, embedding_model, dim, vector_json) VALUES (?, ?, ?, ?)",
                    (
                        chunk.chunk_id,
                        embedding_model,
                        len(embeddings[i]),
                        json.dumps(embeddings[i]),
                    ),
                )
                self.conn.execute(
                    "INSERT INTO chunks_fts(chunk_id, content) VALUES (?, ?)",
                    (chunk.chunk_id, chunk.content),
                )

        return len(chunks)

    def delete_by_relative_path(self, relative_path: str) -> bool:
        with self.conn:
            row = self.conn.execute(
                "SELECT file_id FROM files WHERE relative_path = ?", (relative_path,)
            ).fetchone()
            if not row:
                return False
            file_id = int(row["file_id"])
            for chunk_row in self.conn.execute(
                "SELECT chunk_id FROM chunks WHERE file_id = ?", (file_id,)
            ).fetchall():
                self.conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_row["chunk_id"],))
            self.conn.execute(
                "DELETE FROM chunk_embeddings WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE file_id = ?)",
                (file_id,),
            )
            self.conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
            self.conn.execute("DELETE FROM files WHERE file_id = ?", (file_id,))
        return True

    def backfill_created_at_ns(self, relative_path: str, created_at_ns: int) -> bool:
        # Cheap, idempotent self-heal for rows indexed before created_at_ns existed:
        # only touches rows still at the default 0, no re-embedding required.
        if not created_at_ns:
            return False
        with self.conn:
            cur = self.conn.execute(
                "UPDATE files SET created_at_ns = ? WHERE relative_path = ? AND created_at_ns = 0",
                (created_at_ns, relative_path),
            )
        return cur.rowcount > 0

    def get_file_record(self, relative_path: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM files WHERE relative_path = ?", (relative_path,)
        ).fetchone()

    def search_candidates(
        self,
        query: str,
        *,
        scope_mode: str,
        target_project: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        where_clauses = ["chunks_fts MATCH ?"]
        params: list[Any] = [query]

        if scope_mode == "workspace":
            where_clauses.append("f.scope_type = 'workspace'")
        elif scope_mode == "project" and target_project:
            where_clauses.append("f.scope_type = 'project' AND f.project = ?")
            params.append(target_project)
        elif scope_mode == "auto_project" and target_project:
            where_clauses.append("(f.scope_type = 'workspace' OR (f.scope_type = 'project' AND f.project = ?))")
            params.append(target_project)
        elif scope_mode == "auto_workspace":
            where_clauses.append("f.scope_type = 'workspace'")

        where_sql = " AND ".join(where_clauses)

        sql = f"""
            SELECT
              c.chunk_id,
              c.content,
              c.section_title,
              c.page_number,
              f.relative_path,
              f.scope_type,
              f.project,
              f.modified_at_ns,
              f.created_at_ns,
              bm25(chunks_fts) AS bm25
            FROM chunks_fts
            JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
            JOIN files f ON f.file_id = c.file_id
            WHERE {where_sql}
            ORDER BY bm25(chunks_fts)
            LIMIT ?
        """
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            emb_row = self.conn.execute(
                "SELECT vector_json FROM chunk_embeddings WHERE chunk_id = ?",
                (row["chunk_id"],),
            ).fetchone()
            out.append(
                {
                    "chunk_id": row["chunk_id"],
                    "content": row["content"],
                    "section_title": row["section_title"],
                    "page_number": row["page_number"],
                    "relative_path": row["relative_path"],
                    "scope_type": row["scope_type"],
                    "project": row["project"],
                    "modified_at_ns": row["modified_at_ns"],
                    "created_at_ns": row["created_at_ns"],
                    "bm25": float(row["bm25"]),
                    "embedding": json.loads(emb_row["vector_json"]) if emb_row else [],
                }
            )
        return out
