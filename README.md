# workspace-docs-mcp

Documentation memory for coding agents: index your docs once, retrieve the right context on demand — wherever those docs live.

It works with any MCP-compatible AI dev tool (Claude Code, OpenCode, Cursor, Windsurf, VS Code, Zed, …). MCP is a standard protocol, so the server is identical everywhere; only each tool's config file format differs.

## What this memory is (and is not)

This is a **project documentation memory layer**, not a general personal memory system.

It persistently indexes documentation and keeps that project context available for retrieval in future sessions. It does **not** memorize user preferences, personal profile data, or cross-project personal context.

## What it solves

When an agent works in a large repo it usually has two bad options:

- read lots of files and waste the context window, or
- miss important docs that live in project folders (or outside the repo entirely).

`workspace-docs-mcp` indexes docs once and retrieves only relevant chunks at query time — keeping prompts small and targeted.

## Where docs can live

You declare doc sources explicitly in a required `.workspace-docs.toml` (see below). A source can be:

- **inside the repo** (e.g. `./docs`, `./projects/<name>/docs`), or
- **outside the repo** (an absolute path such as a shared handbook, a mounted drive, or a sibling folder).

Each project can point its docs wherever it wants. External paths are **opt-in and bounded**: a path outside the workspace must resolve inside an allowlisted root (`WORKSPACE_DOCS_ALLOWED_ROOTS`), so the indexer never ingests arbitrary disk content.

## Install

Requires **Python 3.11+** and [`uv`](https://docs.astral.sh/uv/).

```bash
# Install from GitHub, pinned to a tag:
uv tool install "git+https://github.com/rkaveski/workspace-docs-mcp@v0.2.0"

# Or run ephemerally (no install):
uvx --from "git+https://github.com/rkaveski/workspace-docs-mcp" workspace-docs-mcp
```

Once published to PyPI, this also works:

```bash
uvx workspace-docs-mcp
```

## Connect it to your AI dev tool

The server is launched over stdio with a `command` + optional `env`. The generic shape, which every MCP client expresses in its own format:

```
command: uvx
args:    ["--from", "git+https://github.com/rkaveski/workspace-docs-mcp", "workspace-docs-mcp"]
env:     { "WORKSPACE_DOCS_ENABLE_IMAGE_OCR": "false" }
```

> Tip: if you ran `uv tool install`, you can use `workspace-docs-mcp` directly as the command instead of the `uvx --from …` form.

Pick your tool below. (Config file locations occasionally change between tool versions — if a snippet doesn't take, check your tool's current MCP docs for the exact path.)

### Claude Code

Easiest via the CLI:

```bash
claude mcp add workspace-docs -- uvx --from "git+https://github.com/rkaveski/workspace-docs-mcp" workspace-docs-mcp
```

Or commit a project-scoped `.mcp.json` at the repo root:

```json
{
  "mcpServers": {
    "workspace-docs": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/rkaveski/workspace-docs-mcp", "workspace-docs-mcp"],
      "env": { "WORKSPACE_DOCS_ENABLE_IMAGE_OCR": "false" }
    }
  }
}
```

### OpenCode

In `opencode.json`:

```json
{
  "mcp": {
    "workspace_docs": {
      "type": "local",
      "timeout": 600000,
      "command": ["uvx", "--from", "git+https://github.com/rkaveski/workspace-docs-mcp", "workspace-docs-mcp"],
      "environment": { "WORKSPACE_DOCS_ENABLE_IMAGE_OCR": "false" },
      "enabled": true
    }
  }
}
```

### Cursor

In `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global):

```json
{
  "mcpServers": {
    "workspace-docs": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/rkaveski/workspace-docs-mcp", "workspace-docs-mcp"]
    }
  }
}
```

### Windsurf

In `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "workspace-docs": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/rkaveski/workspace-docs-mcp", "workspace-docs-mcp"]
    }
  }
}
```

### VS Code (GitHub Copilot agent mode)

In `.vscode/mcp.json` (note the `servers` key and `type`):

```json
{
  "servers": {
    "workspace-docs": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/rkaveski/workspace-docs-mcp", "workspace-docs-mcp"]
    }
  }
}
```

### Claude Desktop

In `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "workspace-docs": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/rkaveski/workspace-docs-mcp", "workspace-docs-mcp"]
    }
  }
}
```

### Zed

In Zed `settings.json`:

```json
{
  "context_servers": {
    "workspace-docs": {
      "command": {
        "path": "uvx",
        "args": ["--from", "git+https://github.com/rkaveski/workspace-docs-mcp", "workspace-docs-mcp"]
      },
      "settings": {}
    }
  }
}
```

### Any other MCP client

Register a **local / stdio** server with command `uvx` and the args above. That's all the protocol needs.

## Configure where docs live: `.workspace-docs.toml`

A `.workspace-docs.toml` at the workspace root is **required** — there is no implicit default layout. It must declare at least one `[[source]]` entry telling the server which directories to index (inside or outside the repo). If the file is missing, malformed, contains unknown keys, or defines no sources, the server fails fast with a clear error rather than silently indexing the wrong thing.

> Naming: array-of-tables uses the singular `[[source]]` (each entry is one source); list-valued keys are plural (`includes`).

A typical setup mirroring the classic `docs/` + per-project layout:

```toml
# Optional. Where the local index lives. Default: ".rag" (inside the repo).
# Use "cache" for an OS cache dir, or an absolute path.
rag_dir = ".rag"

# Workspace-wide docs inside the repo.
[[source]]
name = "workspace"
path = "docs"
scope = "workspace"

# Shared docs that live OUTSIDE the repo (requires the allowlist, see below).
[[source]]
name = "handbook"
path = "~/company/handbook"
scope = "workspace"

# A project whose docs live in the repo. `project` is the project's root dir.
[[source]]
name = "billing-docs"
path = "projects/billing/docs"
scope = "project"
project = "projects/billing"

# A project whose docs live on a shared drive, but whose code is in the repo.
[[source]]
name = "design-docs"
path = "/mnt/shared/design-docs"
scope = "project"
project = "projects/design"
includes = ["**/*.md", "**/*.pdf"]   # optional per-source filters
```

Top-level keys:

| Key | Required | Meaning |
|-----|----------|---------|
| `rag_dir` | no | Where the index lives (`".rag"` default, `"cache"`, or a path). See [Where the index lives](#where-the-index-lives-rag_dir). |
| `source` | yes | Array of `[[source]]` tables (below). At least one required. |

Per-source fields:

| Field | Required | Meaning |
|-------|----------|---------|
| `path` | yes | Directory of docs to index. Relative (to workspace root) or absolute. `~` is expanded. |
| `scope` | yes | `"workspace"` (shared) or `"project"` (scoped to one project). |
| `project` | when `scope = "project"` | The project's **root directory** (relative or absolute). Serves as both the project's identity and the path used to detect when you're working in it. See the rule below. |
| `name` | no | Label for the source (defaults to the docs folder name). |
| `includes` | no | List of `fnmatch`-style globs; if set, only matching files are indexed. |

The config must define at least one source. A missing file, invalid TOML, an unknown key, or an empty `[[source]]` list each raise a specific, field-pointing error — so configuration mistakes surface immediately instead of being silently ignored.

#### Choosing a scope

`scope` controls how a source is prioritized during retrieval — it does not affect where files live.

| Your situation | Use |
|----------------|-----|
| A single project, or a flat repo with one set of docs | `scope = "workspace"` |
| One workspace holding several projects, and you want per-project precision | `scope = "project"` + `project = "<root dir>"` |

`project` scope only pays off in a **multi-project** workspace. There, when the agent works inside one project, that project's docs are boosted, shared `workspace` docs sit just below, and *other* projects' docs are actively penalized — so a sibling project's "retry logic" doc won't surface while you're working on a different one.

For a single project, `project` scope buys nothing — `workspace` is the simpler, equivalent choice.

##### How `project` works

`project` is **the project's root directory** — nothing is derived from a folder name, and you choose the path:

- **Identity:** sources sharing the same `project` path belong to the same project.
- **Detection:** the server treats a project as active when the file you're working in lives **under** its `project` directory. Editing `projects/billing/src/foo.py` activates the source whose `project = "projects/billing"`, so its docs are prioritized automatically — no need to name it on each query. The most specific (deepest) matching root wins.
- **Decoupled from docs:** `project` (the root) and `path` (the docs) are independent — a project's docs can live in the repo, on a shared drive, anywhere. Only `project` determines identity and detection.

This is deterministic (an explicit path you declare, not an inferred convention) and fully customizable (any directory layout — `projects/…`, `services/…`, `packages/…`).

> **Windows paths in TOML:** backslashes are escape characters in basic strings. Use a literal string with single quotes (`path = 'C:\Users\me\docs'`) or forward slashes (`path = "C:/Users/me/docs"`).

### Allowing docs outside the repo

By default the server only indexes docs **inside** the workspace. The moment a source `path` points outside the repo, it's rejected — unless that path sits inside a directory you've allowlisted via the `WORKSPACE_DOCS_ALLOWED_ROOTS` environment variable (comma-separated). A source under an allowed root loads; anything else fails with a clear error.

**This is set in your MCP client config, not in `.workspace-docs.toml`** — in the `env` block where you register the server:

```json
{
  "mcpServers": {
    "workspace-docs": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/rkaveski/workspace-docs-mcp", "workspace-docs-mcp"],
      "env": { "WORKSPACE_DOCS_ALLOWED_ROOTS": "/Users/me/Documents,/mnt/shared" }
    }
  }
}
```

(`env` is just the environment variables your AI tool passes to the server process when it launches it. OpenCode calls this block `environment`; the idea is the same.)

#### Why an env var and not a TOML key

`.workspace-docs.toml` is **committed into the repo**, so any repo you clone could declare whatever paths it wants. If the allowlist lived there too, a repo could grant *itself* permission to read arbitrary files on your machine and feed them to the model. Keeping the allowlist in the environment means it's a decision **you** make on the machine running the server (machine-level trust), separate from the repo's "which folders are docs" request. The repo says *what* it wants; your env says *what's permitted* — and both must agree.

Practical notes:

- You only need this when docs live **outside** the repo. In-repo docs never require it.
- Point it at a **parent** directory; everything beneath it is allowed (e.g. `/Users/me/Documents` covers `~/Documents/acme-docs`).
- Env vars are read when the server **starts**, so after changing it, restart/reload the MCP server.

#### Set it once, scope it sensibly

Because the allowlist is process-global, you set it **once** and it applies to every workspace that server instance handles — you don't reconfigure it per repo. So in practice you point it at a parent directory broad enough to cover wherever your docs usually live, and forget it.

The tradeoff is blast radius: the wider the root, the more a stray or untrusted `.workspace-docs.toml` could reach. Choose based on whose repos you run:

| You run… | Reasonable setting | Why |
|----------|--------------------|-----|
| Only your own, trusted repos | A broad root like `~/Documents`, or even your home folder | Convenient; set once and forget. The only configs that exist are yours. |
| Repos from others (cloned, shared) | A **dedicated** docs tree, e.g. `~/docs` or `~/company` | A config you didn't scrutinize can't reach sensitive dot-folders (`~/.ssh`, `~/.aws`, browser profiles, financial docs). |

Rule of thumb: scope it as narrowly as is still convenient. Setting it to your **entire home folder** is fine if every repo is yours, but it effectively turns the boundary off for any repo config you haven't read — since `.workspace-docs.toml` travels with a cloned repo, that config could point a source at any sensitive file under home and have it indexed and served to the model.

## Where the index lives (`rag_dir`)

| `rag_dir` value | Result |
|-----------------|--------|
| `".rag"` (default) | `./.rag/` inside the repo — self-contained and portable. |
| `"cache"` | A per-workspace dir under the OS cache (`%LOCALAPPDATA%` on Windows, `~/Library/Caches` on macOS, `$XDG_CACHE_HOME`/`~/.cache` on Linux). |
| an absolute path | Exactly that directory. |

The default is safe everywhere. Reach for `"cache"` or an explicit path when the repo tree is read-only/ephemeral, when docs live entirely outside the repo, or on a synced filesystem.

## Windows & VDI guidance

The default (`.rag/` in-repo) works on Windows and VDI out of the box. Two notes for managed VDI environments:

- **Index location.** On VDI the repo often sits on a mapped network home drive. The in-repo index is safe there because the SQLite journal mode automatically falls back from WAL (which is unsupported on network filesystems) to `DELETE`. If your VDI uses **FSLogix** or another persistent profile disk, set `rag_dir = "cache"` for local-speed *and* persistence. If you need to force a journal mode on a mapped drive we can't auto-detect, set `WORKSPACE_DOCS_SQLITE_JOURNAL_MODE=DELETE`.
- **Launching the server.** Some Windows MCP clients need the command wrapped: use `cmd` with args `["/c", "uvx", "--from", "git+…", "workspace-docs-mcp"]`. And confirm `uv`/Python 3.11+ are permitted on the VDI image — on fully locked-down images you may need an admin to provide them.

## How it works (no MCP knowledge needed)

You can use this without knowing any tool names.

1. Add a `.workspace-docs.toml` at the repo root declaring your doc sources (see above). This is required — the server errors without it.
2. Open your AI dev tool in the repo.
3. In chat: `Refresh docs for this workspace`.
4. The server scans your doc sources and builds/updates the local index in the background.
5. Ask your normal question (e.g. `How does report generation work in this project?`).
6. The agent searches indexed docs first, then opens specific files only when needed.

Scope behavior:

- working inside a project's root directory prioritizes that project's docs, then shared docs;
- workspace-wide tasks prioritize shared docs first;
- sibling project docs are not prioritized when focused on one project.

## Day-to-day use

Plain chat requests:

- `Refresh docs for this workspace`
- `Show docs indexing status`
- `Search docs for "<topic>"`
- `Open the doc that mentions <topic>`

### Every time you add, edit, move, or delete a doc

**Indexing is refresh-driven, not automatic.** The server does not watch the filesystem — a newly added or changed file is invisible to search until you refresh. Every time docs change, whether they live in an in-repo `docs/` folder or an external source, do this:

1. In chat: `Refresh docs for this workspace`. This is non-blocking — it kicks off a background job and returns immediately.
2. Optionally confirm it finished: `Show docs indexing status`.

That's it — no restart, no re-running setup, no config changes. Refresh is incremental: unchanged files are skipped by content hash, so only new/edited/deleted files are actually re-processed, and this stays fast even on repeated runs. The **only** exception is the very first time a workspace has no index at all — then the first `search_docs` / `status_docs` / `get_doc` call auto-starts an initial refresh for you. After that initial bootstrap, refreshing is always a manual step.

## MCP tools

- `search_docs(query, scope="auto", project=null, context_path=null, k=8, workspace_root=null)`
- `get_doc(path, workspace_root=null, page=null, max_chars=20000)`
- `refresh_docs(scope="auto", project=null, workspace_root=null, full=false)`
- `status_docs(workspace_root=null)`

`refresh_docs` is non-blocking and starts a background job; `status_docs` shows progress; `search_docs` can return partial results while warmup is in progress. For external docs, `search_docs` returns the absolute path as the hit's identity, and `get_doc` accepts that same path.

## Supported file types

- Text and markdown: `.txt`, `.md`, `.markdown`
- PDF (text layer): `.pdf`
- Word: `.docx`
- Tables: `.csv`, `.xlsx`
- Subtitles: `.srt`
- Images via OCR (opt-in): `.png`, `.jpg`, `.jpeg`, `.webp`, `.tiff`, `.tif`

## Recency weighting

Ranking blends lexical + semantic relevance with a recency boost based on the most recent of each file's creation and modification times. Among similarly relevant results on the same subject, newer documents rank higher. The boost decays with an exponential half-life (default 30 days), tunable via `WORKSPACE_DOCS_RECENCY_WEIGHT` / `WORKSPACE_DOCS_RECENCY_HALF_LIFE_DAYS` (set the weight to `0` to disable). Each hit includes a `modified_at` date.

## OCR (images) is opt-in

OCR is disabled by default for predictable performance. To enable: set `WORKSPACE_DOCS_ENABLE_IMAGE_OCR=true` and install Tesseract.

```bash
# macOS
brew install tesseract
# Ubuntu/Debian
sudo apt-get update && sudo apt-get install -y tesseract-ocr
```

If OCR is enabled and a file times out, that file is skipped and indexing continues.

## Upgrading from earlier versions

Existing in-repo indexes keep their identity — the `source_name` column backfills on the next reconcile, so **no re-index is required** after upgrading. Just run `Refresh docs for this workspace` once.

## Environment variables

- `WORKSPACE_DOCS_ALLOWED_ROOTS` — comma-separated allowlist of directories permitted for external doc sources and `workspace_root`.
- `WORKSPACE_DOCS_SQLITE_JOURNAL_MODE` — force `WAL` / `DELETE` / `TRUNCATE` (useful on mapped network drives).
- `WORKSPACE_DOCS_ENABLE_DOCX` default `true`
- `WORKSPACE_DOCS_ENABLE_IMAGE_OCR` default `false`
- `WORKSPACE_DOCS_OCR_LANG` default `eng`
- `WORKSPACE_DOCS_OCR_TIMEOUT_SECONDS` default `15`
- `WORKSPACE_DOCS_MAX_ROWS_PER_TABLE_FILE` default `25000`
- `WORKSPACE_DOCS_MAX_CELL_CHARS` default `500`
- `WORKSPACE_DOCS_RECENCY_WEIGHT` default `0.15` (set `0` to disable)
- `WORKSPACE_DOCS_RECENCY_HALF_LIFE_DAYS` default `30`
- `WORKSPACE_DOCS_ALLOW_WORKSPACE_ROOT_OVERRIDE` default `false`

## Storage and isolation

Each workspace gets its own local index (`index.sqlite` + `manifest.json`) under its `rag_dir`. No cross-workspace sharing.

## Run from source

```bash
cd workspace-docs-mcp
uv sync
uv run python -m workspace_docs_mcp.server
```

## License

MIT
