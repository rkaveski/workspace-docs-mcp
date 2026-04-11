# workspace-docs-mcp

Opinionated local-first docs retrieval MCP server.

## Conventions (default)

The server indexes only:

- `./docs/**`
- `./projects/*/docs/**`

Scope behavior:

- inside `./projects/<project>/...`: prioritize `<project>/docs`, then `./docs`
- workspace-wide tasks: prioritize `./docs`, include project docs only when relevant
- sibling project docs are excluded by default while focused on one project

## Supported formats

- Markdown and text: `.md`, `.markdown`, `.txt`
- PDFs (text layer): `.pdf`
- Word docs: `.docx`
- Tabular: `.csv`, `.xlsx`
- Images (OCR opt-in): `.png`, `.jpg`, `.jpeg`, `.webp`, `.tiff`, `.tif`

## Storage and isolation

Each workspace keeps its own index:

- `./.rag/index.sqlite`
- `./.rag/manifest.json`

No cross-workspace index sharing is used.

## MCP tools

- `search_docs(query, scope="auto", project=null, context_path=null, k=8, workspace_root=null)`
- `get_doc(path, workspace_root=null, page=null, max_chars=20000)`
- `refresh_docs(scope="auto", project=null, workspace_root=null, full=false)`
- `status_docs(workspace_root=null)`

## Refresh model

- `refresh_docs` is non-blocking and starts/reuses a background reconcile job.
- `status_docs` exposes refresh progress/state.
- `search_docs` can return partial results while index warmup is running.

## Install (local development)

```bash
cd ~/Sites/workspace-docs-mcp
uv sync
uv run python -m workspace_docs_mcp.server
```

## Install (package usage)

```bash
uvx workspace-docs-mcp
```

## OCR requirement (images)

Image OCR uses `pytesseract` and requires the `tesseract` binary in your `PATH`.

### macOS

```bash
brew install tesseract
```

### Ubuntu/Debian

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr
```

If OCR is enabled and a file OCR call times out/errors, that file is skipped and indexing continues.

## Environment controls

- `WORKSPACE_DOCS_ROOTS` (optional): comma-separated root overrides
- `WORKSPACE_DOCS_ENABLE_DOCX` (default `true`)
- `WORKSPACE_DOCS_ENABLE_IMAGE_OCR` (default `false`)
- `WORKSPACE_DOCS_OCR_LANG` (default `eng`)
- `WORKSPACE_DOCS_OCR_TIMEOUT_SECONDS` (default `15`)
- `WORKSPACE_DOCS_MAX_ROWS_PER_TABLE_FILE` (default `25000`)
- `WORKSPACE_DOCS_MAX_CELL_CHARS` (default `500`)

## OpenCode example

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

## Notes

- OCR for image-only PDFs is not included.
- Reconciliation supports incremental hash/mtime updates and manual refresh.
