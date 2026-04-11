# AGENTS.md

Instructions for coding agents working in this repository.

## Project Overview

- Python MCP server for local, scoped documentation retrieval.
- Indexes docs into `./.rag/index.sqlite` and `./.rag/manifest.json`.
- Main tool surface lives in `src/workspace_docs_mcp/server.py`.

## Setup Commands

- Install dependencies: `uv sync`
- Run server from source: `uv run python -m workspace_docs_mcp.server`
- Run package entrypoint: `uvx workspace-docs-mcp`

## Test Commands

- Run full test suite: `uv run python -m unittest discover -s tests -p 'test_*.py' -q`
- Run one test module: `uv run python -m unittest -q tests.test_scope`
- Run one test case: `uv run python -m unittest -q tests.test_integration.IntegrationTests.test_refresh_docs_is_non_blocking_and_reports_progress`

Always run relevant tests for changed files before finishing.

## Code Map

- `src/workspace_docs_mcp/server.py`: MCP tools (`search_docs`, `get_doc`, `refresh_docs`, `status_docs`) and refresh lifecycle.
- `src/workspace_docs_mcp/indexer.py`: reconcile logic, incremental hashing, manifest coordination.
- `src/workspace_docs_mcp/retriever.py`: hybrid retrieval/scoring and result payloads.
- `src/workspace_docs_mcp/storage.py`: SQLite schema and persistence.
- `src/workspace_docs_mcp/scope.py`: workspace/project scope resolution and docs discovery.
- `src/workspace_docs_mcp/parsers.py`: document parsing (text, pdf, docx, csv/xlsx, optional OCR).
- `tests/`: unit/integration coverage for parser, scope, refresh, and retrieval behavior.

## Do

- Keep changes small and focused; preserve existing API/tool contracts.
- Prefer incremental fixes over broad refactors unless explicitly requested.
- Update tests when behavior changes.
- Preserve docs/indexing semantics:
  - new workspace can auto-warm on first docs tool call
  - ongoing doc changes require refresh
- Use `Path`/workspace-relative safety checks for filesystem access.

## Don't

- Do not introduce network-dependent runtime behavior in core indexing/retrieval paths.
- Do not silently change on-disk index locations or schema behavior without tests and docs updates.
- Do not add heavyweight dependencies without explicit justification.
- Do not bypass existing scope rules (`docs/**`, `projects/*/docs/**`) without updating docs and tests.

## Safety and Permissions

Allowed without asking:

- Read/search files.
- Edit source/tests/docs in this repository.
- Run targeted tests and local validation commands.

Ask first:

- Installing or upgrading dependencies.
- Destructive actions (`rm`, history rewrites, schema-destroying migrations).
- Pushing branches, creating releases, or changing CI/workflow files.

## Documentation Rules

- If behavior changes, update `README.md` in the same change.
- Keep user-facing wording clear about refresh/index lifecycle and scope behavior.
- Prefer explicit examples over abstract descriptions.

## PR / Change Checklist

- Tests relevant to changed behavior pass.
- README/AGENTS/docs updated when behavior or workflow changes.
- No unrelated formatting churn.
- Diff summary explains what changed and why.

## When Stuck

- State assumptions and constraints clearly.
- Propose a minimal plan and implement the least risky step first.
- If a change is ambiguous or high-risk, ask a focused clarifying question before proceeding.
