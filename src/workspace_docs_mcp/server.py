from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .embeddings import EmbeddingEngine
from .indexer import Indexer
from .retriever import Retriever
from .scope import resolve_workspace_root

mcp = FastMCP("workspace_docs_mcp")


@dataclass
class RefreshState:
    status: str = "idle"  # idle | running | completed | failed
    job_id: int = 0
    scope: str = "auto"
    project: str | None = None
    full: bool = False
    started_at_ns: int | None = None
    finished_at_ns: int | None = None
    processed_files: int = 0
    total_files: int = 0
    last_error: str | None = None
    last_result: dict | None = None


@dataclass
class WorkspaceRuntime:
    root: Path
    embedding: EmbeddingEngine
    indexer: Indexer
    retriever: Retriever
    index_ready: bool = False
    refresh: RefreshState = field(default_factory=RefreshState)


_LOCK = threading.RLock()
_RUNTIMES: dict[str, WorkspaceRuntime] = {}


def _runtime(workspace_root: Optional[str]) -> WorkspaceRuntime:
    root = resolve_workspace_root(workspace_root)
    key = str(root)
    with _LOCK:
        runtime = _RUNTIMES.get(key)
        if runtime:
            return runtime

        embedding = EmbeddingEngine()
        indexer = Indexer(root, embedding)
        retriever = Retriever(root, indexer.storage, embedding)

        status = indexer.status()
        index_ready = bool(status.get("last_reconcile_at_ns") or status.get("indexed_files", 0) > 0)

        runtime = WorkspaceRuntime(
            root=root,
            embedding=embedding,
            indexer=indexer,
            retriever=retriever,
            index_ready=index_ready,
        )
        _RUNTIMES[key] = runtime
        return runtime


def _refresh_payload(runtime: WorkspaceRuntime) -> dict:
    data = asdict(runtime.refresh)
    total = int(data.get("total_files") or 0)
    processed = int(data.get("processed_files") or 0)
    data["progress_pct"] = round((processed / total) * 100.0, 2) if total > 0 else 0.0
    data["running"] = data.get("status") == "running"
    return data


def _normalize_scope(scope: str) -> str:
    normalized = (scope or "auto").strip().lower()
    return normalized if normalized in {"auto", "workspace", "project", "all"} else "auto"


def _maybe_start_initial_refresh(runtime: WorkspaceRuntime) -> bool:
    with _LOCK:
        if runtime.index_ready:
            return False
        if runtime.refresh.status == "running":
            return False
    _start_refresh(runtime, scope="auto", project=None, full=False)
    return True


def _start_refresh(
    runtime: WorkspaceRuntime,
    *,
    scope: str,
    project: str | None,
    full: bool,
) -> tuple[bool, dict]:
    key = str(runtime.root)
    normalized_scope = _normalize_scope(scope)

    with _LOCK:
        if runtime.refresh.status == "running":
            return False, _refresh_payload(runtime)

        job_id = runtime.refresh.job_id + 1
        runtime.refresh = RefreshState(
            status="running",
            job_id=job_id,
            scope=normalized_scope,
            project=project,
            full=full,
            started_at_ns=time.time_ns(),
            processed_files=0,
            total_files=0,
            last_error=None,
            last_result=None,
        )

    def run_job() -> None:
        local_embedding = EmbeddingEngine()
        local_indexer = Indexer(runtime.root, local_embedding)
        try:
            def on_progress(payload: dict) -> None:
                with _LOCK:
                    rt = _RUNTIMES.get(key)
                    if not rt or rt.refresh.job_id != job_id:
                        return
                    rt.refresh.processed_files = int(payload.get("processed_files") or 0)
                    rt.refresh.total_files = int(payload.get("total_files") or 0)

            result = local_indexer.reconcile(
                force_hash_all=full,
                scope=normalized_scope,
                project=project,
                progress_callback=on_progress,
            )
            with _LOCK:
                rt = _RUNTIMES.get(key)
                if not rt or rt.refresh.job_id != job_id:
                    return
                rt.refresh.status = "completed"
                rt.refresh.finished_at_ns = time.time_ns()
                rt.refresh.last_result = asdict(result)
                rt.refresh.last_error = None
                rt.refresh.processed_files = result.processed_candidates
                rt.refresh.total_files = result.total_candidates
                rt.index_ready = True
        except Exception as exc:
            with _LOCK:
                rt = _RUNTIMES.get(key)
                if not rt or rt.refresh.job_id != job_id:
                    return
                rt.refresh.status = "failed"
                rt.refresh.finished_at_ns = time.time_ns()
                rt.refresh.last_error = str(exc)
        finally:
            local_indexer.close()

    thread = threading.Thread(target=run_job, name=f"workspace-docs-refresh-{runtime.root.name}", daemon=True)
    thread.start()

    with _LOCK:
        return True, _refresh_payload(runtime)


@mcp.tool()
def search_docs(
    query: str,
    scope: str = "auto",
    project: str | None = None,
    context_path: str | None = None,
    k: int = 8,
    workspace_root: str | None = None,
) -> dict:
    """Search docs using scope-aware hybrid retrieval (FTS5 + embeddings)."""
    runtime = _runtime(workspace_root)
    started_initial = _maybe_start_initial_refresh(runtime)

    payload = runtime.retriever.search(
        query=query,
        scope=scope,
        project=project,
        context_path=context_path,
        k=k,
    )

    with _LOCK:
        payload["index_warming"] = runtime.refresh.status == "running"
        payload["refresh_job"] = _refresh_payload(runtime)

    payload["workspace_root"] = str(runtime.root)
    if started_initial:
        payload["initial_refresh_started"] = True
    return payload


@mcp.tool()
def get_doc(
    path: str,
    workspace_root: str | None = None,
    page: int | None = None,
    max_chars: int = 20_000,
) -> dict:
    """Read source file text after retrieval has narrowed targets."""
    runtime = _runtime(workspace_root)
    _maybe_start_initial_refresh(runtime)

    if page is not None and page < 1:
        raise ValueError("page must be >= 1")
    max_chars = max(1_000, min(max_chars, 250_000))

    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (runtime.root / candidate).resolve()

    try:
        relative_path = resolved.relative_to(runtime.root).as_posix()
    except ValueError as exc:
        raise ValueError("Requested path is outside workspace root") from exc

    record = runtime.indexer.storage.get_file_record(relative_path)
    if record is None:
        with _LOCK:
            if runtime.refresh.status == "running":
                raise ValueError("Path is not indexed yet; refresh is still in progress")
        raise ValueError("Path is not indexed; expected docs or project docs path")

    suffix = resolved.suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        text = resolved.read_text(encoding="utf-8", errors="ignore")
    else:
        from .parsers import parse_document

        segments = parse_document(resolved)
        if suffix == ".pdf" and page is not None:
            segments = [s for s in segments if s.page_number == page]
        text = "\n\n".join(s.text for s in segments)

    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n..."

    return {
        "workspace_root": str(runtime.root),
        "relative_path": relative_path,
        "scope_type": record["scope_type"],
        "project_name": record["project_name"],
        "content": text,
        "truncated": len(text) >= max_chars,
    }


@mcp.tool()
def refresh_docs(
    scope: str = "auto",
    project: str | None = None,
    workspace_root: str | None = None,
    full: bool = False,
) -> dict:
    """Start/reuse a non-blocking docs reconcile job for this workspace."""
    runtime = _runtime(workspace_root)

    normalized_scope = _normalize_scope(scope)
    if normalized_scope == "project" and not project:
        raise ValueError("project is required when scope='project'")

    started, job = _start_refresh(runtime, scope=normalized_scope, project=project, full=full)
    return {
        "workspace_root": str(runtime.root),
        "accepted": started,
        "refresh_job": job,
    }


@mcp.tool()
def status_docs(workspace_root: str | None = None) -> dict:
    """Show docs topology and index health for the current workspace."""
    runtime = _runtime(workspace_root)
    started_initial = _maybe_start_initial_refresh(runtime)

    status = runtime.indexer.status()
    with _LOCK:
        status["index_warming"] = runtime.refresh.status == "running"
        status["refresh_job"] = _refresh_payload(runtime)

    if started_initial:
        status["initial_refresh_started"] = True
    return status


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
