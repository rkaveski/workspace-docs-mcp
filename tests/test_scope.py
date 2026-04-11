from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_docs_mcp.scope import classify_requested_scope, discover_source_files, infer_active_project


class ScopeTests(unittest.TestCase):
    def test_infer_active_project(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            p = root / "projects" / "alpha" / "src" / "main.py"
            p.parent.mkdir(parents=True)
            p.write_text("x=1")
            self.assertEqual(infer_active_project(str(p), root), "alpha")

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

            with patch.dict("os.environ", {"WORKSPACE_DOCS_ENABLE_IMAGE_OCR": "true"}):
                sources = discover_source_files(root)

            paths = {s.relative_path for s in sources}
            self.assertIn("docs/snap.png", paths)


if __name__ == "__main__":
    unittest.main()
