from __future__ import annotations

import os
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .embeddings import EmbeddingEngine, cosine_similarity
from .models import ChunkRecord
from .scope import classify_requested_scope
from .storage import Storage

TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{2,}")

ENV_RECENCY_WEIGHT = "WORKSPACE_DOCS_RECENCY_WEIGHT"
ENV_RECENCY_HALF_LIFE_DAYS = "WORKSPACE_DOCS_RECENCY_HALF_LIFE_DAYS"
DEFAULT_RECENCY_WEIGHT = 0.15
DEFAULT_RECENCY_HALF_LIFE_DAYS = 30.0
NS_PER_DAY = 86_400 * 1_000_000_000


class Retriever:
    def __init__(
        self,
        workspace_root: Path,
        storage: Storage,
        embedding_engine: EmbeddingEngine,
        projects: tuple[tuple[Path, str], ...] = (),
    ) -> None:
        self.workspace_root = workspace_root
        self.storage = storage
        self.embedding_engine = embedding_engine
        self.projects = projects

    def search(
        self,
        query: str,
        *,
        scope: str = "auto",
        project: str | None = None,
        context_path: str | None = None,
        k: int = 8,
    ) -> dict:
        clean_query = (query or "").strip()
        if not clean_query:
            return {"hits": [], "count": 0}

        known_projects = self.storage.get_known_projects()
        scope_mode, target_project = classify_requested_scope(
            scope=scope,
            project=project,
            context_path=context_path,
            workspace_root=self.workspace_root,
            known_projects=known_projects,
            projects=self.projects,
        )

        fts_query = self._fts_query(clean_query)
        if not fts_query:
            return {"hits": [], "count": 0}

        limit = max(40, min(200, k * 10))
        candidates = self.storage.search_candidates(
            fts_query,
            scope_mode=scope_mode,
            target_project=target_project,
            limit=limit,
        )
        if not candidates:
            return {"hits": [], "count": 0}

        query_embedding = self.embedding_engine.embed_one(clean_query)
        bm25_vals = [max(0.0, float(c["bm25"])) for c in candidates]
        bm25_max = max(bm25_vals) if bm25_vals else 1.0

        recency_weight = _env_float(ENV_RECENCY_WEIGHT, DEFAULT_RECENCY_WEIGHT, minimum=0.0, maximum=1.0)
        half_life_days = _env_float(
            ENV_RECENCY_HALF_LIFE_DAYS, DEFAULT_RECENCY_HALF_LIFE_DAYS, minimum=0.1, maximum=36_500.0
        )
        now_ns = time.time_ns()

        records: list[ChunkRecord] = []
        for c in candidates:
            bm25_raw = max(0.0, float(c["bm25"]))
            lex = 1.0 - (bm25_raw / bm25_max if bm25_max > 0 else 0.0)
            sem = (cosine_similarity(query_embedding, c.get("embedding") or []) + 1.0) / 2.0
            boost = self._scope_boost(scope_mode, target_project, c.get("scope_type"), c.get("project"))
            recency_at_ns = _recency_timestamp_ns(c.get("modified_at_ns"), c.get("created_at_ns"))
            recency = _recency_factor(recency_at_ns, now_ns, half_life_days) if recency_weight > 0 else 0.0
            score = 0.6 * lex + 0.35 * sem + boost + recency_weight * recency
            records.append(
                ChunkRecord(
                    chunk_id=str(c["chunk_id"]),
                    relative_path=str(c["relative_path"]),
                    scope_type=str(c["scope_type"]),
                    project=(str(c["project"]) if c.get("project") else None),
                    section_title=(str(c["section_title"]) if c.get("section_title") else None),
                    page_number=(int(c["page_number"]) if c.get("page_number") is not None else None),
                    content=str(c["content"]),
                    score=score,
                    recency_at_ns=recency_at_ns,
                )
            )

        records.sort(key=lambda r: r.score, reverse=True)
        hits = [self._format_hit(rec) for rec in records[: max(1, min(k, 50))]]
        return {
            "count": len(hits),
            "scope_mode": scope_mode,
            "project": target_project,
            "hits": hits,
        }

    def _format_hit(self, rec: ChunkRecord) -> dict:
        data = asdict(rec)
        snippet = rec.content
        if len(snippet) > 380:
            snippet = snippet[:377].rstrip() + "..."
        data["snippet"] = snippet
        data.pop("content", None)
        recency_at_ns = data.pop("recency_at_ns", None)
        data["modified_at"] = _iso_date(recency_at_ns)
        return data

    def _scope_boost(
        self,
        scope_mode: str,
        target_project: str | None,
        scope_type: str | None,
        project: str | None,
    ) -> float:
        if scope_mode == "workspace":
            return 0.12 if scope_type == "workspace" else -0.2
        if scope_mode == "project":
            if scope_type == "project" and project == target_project:
                return 0.16
            return -0.25
        if scope_mode == "auto_project":
            if scope_type == "project" and project == target_project:
                return 0.15
            if scope_type == "workspace":
                return 0.08
            return -0.25
        if scope_mode == "all":
            return 0.0
        if scope_mode == "auto_workspace":
            return 0.1 if scope_type == "workspace" else -0.05
        return 0.0

    def _fts_query(self, query: str) -> str:
        tokens = TOKEN_RE.findall(query.lower())
        tokens = [t for t in tokens if len(t) > 1]
        if not tokens:
            return ""
        escaped = [t.replace('"', "") for t in tokens[:12]]
        return " OR ".join(f'"{t}"*' for t in escaped)


def _recency_timestamp_ns(modified_at_ns: int | None, created_at_ns: int | None) -> int | None:
    # New files mostly arrive rather than get edited, so use the most recent of
    # creation/modification to represent when the information became available.
    candidates = [int(ts) for ts in (modified_at_ns, created_at_ns) if ts]
    return max(candidates) if candidates else None


def _recency_factor(recency_at_ns: int | None, now_ns: int, half_life_days: float) -> float:
    if recency_at_ns is None or half_life_days <= 0:
        return 0.0
    age_days = max(0.0, (now_ns - int(recency_at_ns)) / NS_PER_DAY)
    return 0.5 ** (age_days / half_life_days)


def _iso_date(modified_at_ns: int | None) -> str | None:
    if modified_at_ns is None:
        return None
    return datetime.fromtimestamp(int(modified_at_ns) / 1_000_000_000, tz=timezone.utc).date().isoformat()


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except Exception:
        return default
    return min(maximum, max(minimum, value))
