from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_docs_mcp.config import CONFIG_FILENAME, load_config
from workspace_docs_mcp.paths import JournalMode, journal_mode_for, resolve_rag_dir
from workspace_docs_mcp.scope import discover_source_files, doc_path_for


class ConfigTests(unittest.TestCase):
    def test_missing_config_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / "docs").mkdir(parents=True)
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(ValueError):
                    load_config(root)

    def test_config_without_sources_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / CONFIG_FILENAME).write_text('rag_dir = "cache"\n', encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(ValueError):
                    load_config(root)

    def test_unknown_key_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / "docs").mkdir(parents=True)
            (root / CONFIG_FILENAME).write_text(
                '[[source]]\npath = "docs"\nscope = "workspace"\ntypo_key = 1\n',
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(ValueError):
                    load_config(root)

    def test_minimal_valid_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / "docs").mkdir(parents=True)
            (root / CONFIG_FILENAME).write_text(
                '[[source]]\nname = "workspace"\npath = "docs"\nscope = "workspace"\n',
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True):
                config = load_config(root)
            self.assertEqual(config.rag_dir, ".rag")
            self.assertEqual([s.name for s in config.sources], ["workspace"])

    def test_toml_external_source_requires_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            root = base / "repo"
            external = base / "outside-docs"
            root.mkdir(parents=True)
            external.mkdir(parents=True)

            (root / CONFIG_FILENAME).write_text(
                f'[[source]]\nname = "shared"\npath = "{external.as_posix()}"\nscope = "workspace"\n',
                encoding="utf-8",
            )

            # Without the allowlist, the external path is rejected.
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(ValueError):
                    load_config(root)

            # With the allowlist, it loads.
            with patch.dict("os.environ", {"WORKSPACE_DOCS_ALLOWED_ROOTS": str(base)}, clear=True):
                config = load_config(root)
            roots = {s.root for s in config.sources}
            self.assertIn(external, roots)

    def test_external_docs_are_indexed_with_absolute_identity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            root = base / "repo"
            external = base / "handbook"
            root.mkdir(parents=True)
            external.mkdir(parents=True)
            (external / "guide.md").write_text("external billing guide", encoding="utf-8")

            (root / CONFIG_FILENAME).write_text(
                f'[[source]]\nname = "shared"\npath = "{external.as_posix()}"\nscope = "workspace"\n',
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"WORKSPACE_DOCS_ALLOWED_ROOTS": str(base)}, clear=True):
                config = load_config(root)
                files = discover_source_files(root, config.sources)

            paths = {f.relative_path for f in files}
            # Outside the workspace ⇒ identity is the absolute posix path.
            self.assertIn((external / "guide.md").as_posix(), paths)

    def test_project_scope_requires_project_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / "docs").mkdir(parents=True)
            (root / CONFIG_FILENAME).write_text(
                '[[source]]\npath = "docs"\nscope = "project"\n',
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(ValueError):
                    load_config(root)

    def test_project_is_root_path_identity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / "services" / "billing" / "docs").mkdir(parents=True)
            (root / CONFIG_FILENAME).write_text(
                '[[source]]\npath = "services/billing/docs"\nscope = "project"\nproject = "services/billing"\n',
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True):
                config = load_config(root)
            source = config.sources[0]
            # The project identity is the normalized root path, and project_root
            # resolves to that directory for working-file detection.
            self.assertEqual(source.project, "services/billing")
            self.assertEqual(source.project_root, root / "services" / "billing")

    def test_include_globs_filter_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            docs = root / "docs"
            docs.mkdir(parents=True)
            (docs / "keep.md").write_text("keep", encoding="utf-8")
            (docs / "skip.txt").write_text("skip", encoding="utf-8")
            (root / CONFIG_FILENAME).write_text(
                '[[source]]\npath = "docs"\nscope = "workspace"\nincludes = ["**/*.md"]\n',
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True):
                config = load_config(root)
                files = discover_source_files(root, config.sources)
            paths = {f.relative_path for f in files}
            self.assertIn("docs/keep.md", paths)
            self.assertNotIn("docs/skip.txt", paths)

    def test_rag_dir_is_parsed_alongside_sources(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / "docs").mkdir(parents=True)
            (root / CONFIG_FILENAME).write_text(
                'rag_dir = "cache"\n\n[[source]]\npath = "docs"\nscope = "workspace"\n',
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True):
                config = load_config(root)
            self.assertEqual(config.rag_dir, "cache")
            self.assertEqual(len(config.sources), 1)


class PathsTests(unittest.TestCase):
    def test_default_rag_dir_is_in_repo(self) -> None:
        root = Path("/tmp/example-workspace")
        self.assertEqual(resolve_rag_dir(root, ".rag"), root / ".rag")
        self.assertEqual(resolve_rag_dir(root, None), root / ".rag")

    def test_cache_rag_dir_is_outside_repo_and_stable(self) -> None:
        root = Path("/tmp/example-workspace").resolve()
        first = resolve_rag_dir(root, "cache")
        second = resolve_rag_dir(root, "cache")
        self.assertEqual(first, second)
        self.assertNotIn(str(root), str(first.parent))
        self.assertIn("example-workspace", first.name)

    def test_explicit_absolute_rag_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td).resolve() / "index-home"
            root = Path("/tmp/example-workspace")
            self.assertEqual(resolve_rag_dir(root, str(target)), target)

    def test_journal_mode_falls_back_on_unc_path(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(journal_mode_for(Path("/local/path")), JournalMode.WAL)
            self.assertEqual(journal_mode_for(Path("\\\\server\\share\\rag")), JournalMode.DELETE)

    def test_journal_mode_env_override(self) -> None:
        with patch.dict("os.environ", {"WORKSPACE_DOCS_SQLITE_JOURNAL_MODE": "DELETE"}, clear=True):
            self.assertEqual(journal_mode_for(Path("/local/path")), JournalMode.DELETE)


class DocPathTests(unittest.TestCase):
    def test_inside_workspace_is_relative(self) -> None:
        root = Path("/work/repo")
        self.assertEqual(doc_path_for(root / "docs" / "a.md", root), "docs/a.md")

    def test_outside_workspace_is_absolute(self) -> None:
        root = Path("/work/repo")
        external = Path("/elsewhere/docs/a.md")
        self.assertEqual(doc_path_for(external, root), external.as_posix())


if __name__ == "__main__":
    unittest.main()
