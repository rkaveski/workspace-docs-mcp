from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_docs_mcp.config import CONFIG_FILENAME
from workspace_docs_mcp.scope import (
    classify_requested_scope,
    discover_source_files,
    infer_active_project,
    resolve_workspace_root,
)


def _write_docs_config(root: Path) -> None:
    (root / CONFIG_FILENAME).write_text(
        '[[source]]\nname = "workspace"\npath = "docs"\nscope = "workspace"\n',
        encoding="utf-8",
    )


class ScopeTests(unittest.TestCase):
    def test_infer_active_project(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            p = root / "projects" / "alpha" / "src" / "main.py"
            p.parent.mkdir(parents=True)
            p.write_text("x=1")
            projects = ((root / "projects" / "alpha", "projects/alpha"),)
            self.assertEqual(infer_active_project(str(p), root, projects), "projects/alpha")
            # A file outside any project root yields no active project.
            other = root / "docs" / "readme.md"
            other.parent.mkdir(parents=True)
            other.write_text("hi")
            self.assertIsNone(infer_active_project(str(other), root, projects))
            # With no configured projects there is no project convention.
            self.assertIsNone(infer_active_project(str(p), root))

    def test_classify_auto_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            mode, proj = classify_requested_scope(
                scope="auto",
                project=None,
                context_path=None,
                workspace_root=root,
                known_projects=["alpha"],
            )
            self.assertEqual(mode, "auto_workspace")
            self.assertIsNone(proj)

    def test_image_extensions_excluded_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            docs = root / "docs"
            docs.mkdir(parents=True)
            (docs / "note.md").write_text("hello", encoding="utf-8")
            (docs / "snap.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            _write_docs_config(root)

            with patch.dict("os.environ", {}, clear=True):
                sources = discover_source_files(root)

            paths = {s.relative_path for s in sources}
            self.assertIn("docs/note.md", paths)
            self.assertNotIn("docs/snap.png", paths)

    def test_image_extensions_included_when_ocr_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            docs = root / "docs"
            docs.mkdir(parents=True)
            (docs / "snap.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            _write_docs_config(root)

            with patch.dict("os.environ", {"WORKSPACE_DOCS_ENABLE_IMAGE_OCR": "true"}):
                sources = discover_source_files(root)

            paths = {s.relative_path for s in sources}
            self.assertIn("docs/snap.png", paths)

    def test_symlinked_docs_file_is_not_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            docs = root / "docs"
            docs.mkdir(parents=True)
            outside = (root.parent / "outside-secret.md").resolve()
            outside.write_text("secret", encoding="utf-8")
            try:
                (docs / "leak.md").symlink_to(outside)
                _write_docs_config(root)
                sources = discover_source_files(root)
                paths = {s.relative_path for s in sources}
                self.assertNotIn("docs/leak.md", paths)
            finally:
                outside.unlink(missing_ok=True)

    def test_resolve_workspace_root_rejects_override_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            default_root = base / "default"
            other_root = base / "other"
            default_root.mkdir(parents=True)
            other_root.mkdir(parents=True)

            with patch.dict("os.environ", {"OPENCODE_WORKSPACE": str(default_root)}, clear=False):
                with self.assertRaises(ValueError):
                    resolve_workspace_root(str(other_root))

    def test_resolve_workspace_root_allows_override_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            default_root = base / "default"
            other_root = base / "other"
            default_root.mkdir(parents=True)
            other_root.mkdir(parents=True)

            with patch.dict(
                "os.environ",
                {
                    "OPENCODE_WORKSPACE": str(default_root),
                    "WORKSPACE_DOCS_ALLOW_WORKSPACE_ROOT_OVERRIDE": "true",
                },
                clear=False,
            ):
                got = resolve_workspace_root(str(other_root))
                self.assertEqual(got, other_root)

    def test_resolve_workspace_root_enforces_allowed_roots(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            default_root = base / "default"
            allowed_root = base / "allowed"
            disallowed_root = base / "disallowed"
            default_root.mkdir(parents=True)
            allowed_root.mkdir(parents=True)
            disallowed_root.mkdir(parents=True)
            allowed_child = allowed_root / "child"
            allowed_child.mkdir(parents=True)

            with patch.dict(
                "os.environ",
                {
                    "OPENCODE_WORKSPACE": str(default_root),
                    "WORKSPACE_DOCS_ALLOW_WORKSPACE_ROOT_OVERRIDE": "true",
                    "WORKSPACE_DOCS_ALLOWED_ROOTS": str(allowed_root),
                },
                clear=False,
            ):
                self.assertEqual(resolve_workspace_root(str(allowed_child)), allowed_child)
                with self.assertRaises(ValueError):
                    resolve_workspace_root(str(disallowed_root))


if __name__ == "__main__":
    unittest.main()
