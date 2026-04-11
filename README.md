# workspace-docs-mcp

Local, scoped documentation retrieval for coding agents.

## What this solves

When an agent works in a large repo, it usually has two bad options:

- read lots of files and waste context window
- miss important docs that live in project folders

`workspace-docs-mcp` solves this by indexing docs once and retrieving only relevant chunks at query time.

## Why this project exists

This project exists to make docs usage:

- local-first
- workspace-isolated
- fast enough for daily use
- opinionated with low setup overhead

It is built for teams that keep docs in predictable places and want agents to use them without stuffing entire documents into prompts.

## Opinionated folder convention

By default, only these paths are indexed:

- `./docs/**`
- `./projects/*/docs/**`

Behavior:

- if you are working inside `./projects/<name>/...`, retrieval prioritizes that project's docs, then shared workspace docs
- workspace-wide tasks prioritize shared docs first
- sibling project docs are not prioritized by default when focused on one project

## How it works (no MCP knowledge needed)

You can use this without knowing any tool names.

1. Open OpenCode in your repo.
2. In chat, ask: `Refresh docs for this workspace`.
3. The server scans docs folders and builds/updates `./.rag/` in the background.
4. Then ask your normal question (for example: `How does report generation work in this project?`).
5. The agent will search indexed docs first, then open specific files only when needed.

This keeps prompts small and targeted.

## Example: you add a new doc

Suppose you create a file:

- `./docs/billing-runbook.md`

With content like:

- `How to rotate billing API keys`
- `How to rerun failed invoice jobs`

Then do this in chat:

1. `Refresh docs for this workspace`
2. `Show docs indexing status`
3. `Search docs for "billing API keys"`

After refresh completes, `search_docs` can retrieve content from `docs/billing-runbook.md`.

## Storage and isolation

Each workspace gets its own local index:

- `./.rag/index.sqlite`
- `./.rag/manifest.json`

No cross-workspace sharing is used.

## Supported file types

- Text and markdown: `.txt`, `.md`, `.markdown`
- PDF (text layer): `.pdf`
- Word: `.docx`
- Tables: `.csv`, `.xlsx`
- Images via OCR (opt-in): `.png`, `.jpg`, `.jpeg`, `.webp`, `.tiff`, `.tif`

## Quick start

### Option A: run from source

```bash
cd ~/Sites/workspace-docs-mcp
uv sync
uv run python -m workspace_docs_mcp.server
```

### Option B: run as package

```bash
uvx workspace-docs-mcp
```

## OpenCode configuration example

```json
{
  "mcp": {
    "workspace_docs": {
      "type": "local",
      "timeout": 600000,
      "command": ["uvx", "workspace-docs-mcp"],
      "environment": {
        "WORKSPACE_DOCS_ENABLE_IMAGE_OCR": "false"
      },
      "enabled": true
    }
  }
}
```

## How to use it day-to-day

Use plain chat requests first:

- `Refresh docs for this workspace`
- `Show docs indexing status`
- `Search docs for "<topic>"`
- `Open the doc that mentions <topic>`

Advanced (explicit tool calls), if your agent needs a hint:

- `Use MCP tool workspace_docs.refresh_docs`
- `Use MCP tool workspace_docs.status_docs`
- `Use MCP tool workspace_docs.search_docs with query "..."`
- `Use MCP tool workspace_docs.get_doc with path "..."`

## Manual workflow today

Today, indexing is refresh-driven (not continuous file watching).  
If you add, edit, move, or delete docs, run refresh again.

Typical manual loop:

1. Make doc changes under `./docs/**` or `./projects/*/docs/**`
2. Trigger refresh:
   - chat: `Refresh docs for this workspace`
   - or tool: `workspace_docs.refresh_docs`
3. Check progress:
   - chat: `Show docs indexing status`
   - or tool: `workspace_docs.status_docs`
4. Query:
   - chat: `Search docs for "<topic>"`
   - or tool: `workspace_docs.search_docs`

## New workspace behavior

In a brand-new workspace (no existing `./.rag/` index), the first docs tool call
starts an initial background refresh automatically.

Calls that can trigger this initial refresh:

- `search_docs`
- `status_docs`
- `get_doc`

After that initial index is created, ongoing doc changes are still manual-refresh:

- run `Refresh docs for this workspace` (or `workspace_docs.refresh_docs`) after changes

## MCP tools

- `search_docs(query, scope="auto", project=null, context_path=null, k=8, workspace_root=null)`
- `get_doc(path, workspace_root=null, page=null, max_chars=20000)`
- `refresh_docs(scope="auto", project=null, workspace_root=null, full=false)`
- `status_docs(workspace_root=null)`

## Refresh behavior and performance

- `refresh_docs` is non-blocking and starts a background job
- `status_docs` shows refresh progress and state
- `search_docs` can return partial results while index warmup is in progress
- first indexing run is usually the slowest; later runs are incremental

## OCR (images) is opt-in

OCR is disabled by default for predictable performance.

To enable:

- set `WORKSPACE_DOCS_ENABLE_IMAGE_OCR=true`
- install Tesseract on your system

macOS:

```bash
brew install tesseract
```

Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr
```

If OCR is enabled and a file times out, that file is skipped and indexing continues.

## Environment variables

- `WORKSPACE_DOCS_ROOTS` optional custom roots (comma-separated)
- `WORKSPACE_DOCS_ENABLE_DOCX` default `true`
- `WORKSPACE_DOCS_ENABLE_IMAGE_OCR` default `false`
- `WORKSPACE_DOCS_OCR_LANG` default `eng`
- `WORKSPACE_DOCS_OCR_TIMEOUT_SECONDS` default `15`
- `WORKSPACE_DOCS_MAX_ROWS_PER_TABLE_FILE` default `25000`
- `WORKSPACE_DOCS_MAX_CELL_CHARS` default `500`
- `WORKSPACE_DOCS_ALLOW_WORKSPACE_ROOT_OVERRIDE` default `false` (when `false`, `workspace_root` must match `OPENCODE_WORKSPACE`/current working directory)
- `WORKSPACE_DOCS_ALLOWED_ROOTS` optional comma-separated allowlist of directories for `workspace_root`

## Current limits

- no OCR for image-only PDFs yet
- uses local index files in each workspace

## License

MIT
