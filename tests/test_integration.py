from __future__ import annotations

import csv
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_docs_mcp.embeddings import EmbeddingEngine
from workspace_docs_mcp.indexer import Indexer, ReconcileResult
from workspace_docs_mcp.parsers import OCRRuntime
from workspace_docs_mcp.retriever import Retriever
from workspace_docs_mcp.server import _RUNTIMES, get_doc, refresh_docs, search_docs, status_docs


def _write_csv(path: Path, rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)


def _write_xlsx(path: Path, rows: list[list[object]]) -> None:
    from openpyxl import Workbook

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for row in rows:
        ws.append(row)
    wb.save(path)
    wb.close()


class IntegrationTests(unittest.TestCase):
    def tearDown(self) -> None:
        for rt in list(_RUNTIMES.values()):
            try:
                rt.indexer.close()
            except Exception:
                pass
        _RUNTIMES.clear()

    def test_get_doc_uses_parser_for_binaryish_formats(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            csv_path = root / "docs" / "table.csv"
            _write_csv(csv_path, [["name", "score"], ["alice", "99"]])

            idx = Indexer(root, EmbeddingEngine())
            idx.reconcile()
            idx.close()

            result = get_doc(path="docs/table.csv", workspace_root=str(root))
            self.assertIn("name=alice", result["content"].lower())

    def test_reconcile_add_change_delete_for_csv_xlsx_image(self) -> None:
        try:
            from PIL import Image
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"Pillow missing: {exc}")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            csv_path = root / "docs" / "facts.csv"
            xlsx_path = root / "docs" / "facts.xlsx"
            img_path = root / "docs" / "pic.png"

            _write_csv(csv_path, [["k", "v"], ["a", "1"]])
            _write_xlsx(xlsx_path, [["k", "v"], ["b", "2"]])
            img_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (20, 20), "white").save(img_path)

            idx = Indexer(root, EmbeddingEngine())
            with patch.dict("os.environ", {"WORKSPACE_DOCS_ENABLE_IMAGE_OCR": "true"}):
                with patch(
                    "workspace_docs_mcp.parsers._get_ocr_runtime",
                    return_value=OCRRuntime(enabled=True, available=False, reason="missing", lang="eng", timeout_seconds=15),
                ):
                    first = idx.reconcile()
            self.assertGreaterEqual(first.added, 2)
            self.assertGreaterEqual(first.skipped, 1)

            _write_csv(csv_path, [["k", "v"], ["a", "3"]])
            xlsx_path.unlink(missing_ok=True)
            with patch.dict("os.environ", {"WORKSPACE_DOCS_ENABLE_IMAGE_OCR": "true"}):
                with patch(
                    "workspace_docs_mcp.parsers._get_ocr_runtime",
                    return_value=OCRRuntime(enabled=True, available=False, reason="missing", lang="eng", timeout_seconds=15),
                ):
                    second = idx.reconcile()
            self.assertGreaterEqual(second.changed, 1)
            self.assertGreaterEqual(second.deleted, 1)
            idx.close()

    def test_search_docs_can_retrieve_xlsx_csv_content(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            csv_path = root / "docs" / "orders.csv"
            _write_csv(csv_path, [["id", "note"], ["42", "invoice token xyz-123"]])

            idx = Indexer(root, EmbeddingEngine())
            idx.reconcile()
            retriever = Retriever(root, idx.storage, idx.embedding_engine)

            out = retriever.search("xyz-123 invoice", scope="auto", context_path=None, k=5)
            self.assertGreaterEqual(out["count"], 1)
            self.assertIn("orders.csv", out["hits"][0]["relative_path"])
            idx.close()

    def test_status_docs_exposes_ocr_capability_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            _write_csv(root / "docs" / "a.csv", [["x"], ["1"]])
            idx = Indexer(root, EmbeddingEngine())
            idx.reconcile()
            status = idx.status()
            self.assertIn("parsers", status)
            self.assertIn("image_ocr", status["parsers"])
            self.assertIn("enabled", status["parsers"]["image_ocr"])
            self.assertIn("available", status["parsers"]["image_ocr"])
            self.assertIn("timeout_seconds", status["parsers"]["image_ocr"])
            idx.close()

    def test_refresh_docs_is_non_blocking_and_reports_progress(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / "docs").mkdir(parents=True)
            (root / "docs" / "note.md").write_text("hello world", encoding="utf-8")

            original = Indexer.reconcile

            def slow_reconcile(self, *args, **kwargs):
                time.sleep(0.3)
                return original(self, *args, **kwargs)

            with patch.object(Indexer, "reconcile", slow_reconcile):
                t0 = time.monotonic()
                out = refresh_docs(workspace_root=str(root))
                elapsed = time.monotonic() - t0

                self.assertLess(elapsed, 0.2)
                self.assertIn("refresh_job", out)
                self.assertEqual(out["refresh_job"]["status"], "running")

                deadline = time.time() + 5
                current = out
                while time.time() < deadline:
                    current = status_docs(workspace_root=str(root))
                    if current["refresh_job"]["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.05)

                self.assertEqual(current["refresh_job"]["status"], "completed")
                self.assertFalse(current["index_warming"])

    def test_search_docs_returns_warming_flag_during_initial_index(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / "docs").mkdir(parents=True)
            (root / "docs" / "note.md").write_text("invoice alpha", encoding="utf-8")

            original = Indexer.reconcile

            def slow_reconcile(self, *args, **kwargs):
                time.sleep(0.3)
                return original(self, *args, **kwargs)

            with patch.object(Indexer, "reconcile", slow_reconcile):
                first = search_docs("invoice", workspace_root=str(root))
                self.assertIn("index_warming", first)

                deadline = time.time() + 5
                while time.time() < deadline:
                    cur = status_docs(workspace_root=str(root))
                    if cur["refresh_job"]["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.05)

                second = search_docs("invoice", workspace_root=str(root))
                self.assertFalse(second["index_warming"])
                self.assertGreaterEqual(second["count"], 1)

    def test_refresh_scope_auto_with_project_indexes_workspace_and_project(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / "docs").mkdir(parents=True)
            (root / "docs" / "workspace.md").write_text("workspace", encoding="utf-8")
            (root / "projects" / "a" / "docs").mkdir(parents=True)
            (root / "projects" / "a" / "docs" / "a.md").write_text("a", encoding="utf-8")
            (root / "projects" / "b" / "docs").mkdir(parents=True)
            (root / "projects" / "b" / "docs" / "b.md").write_text("b", encoding="utf-8")

            idx = Indexer(root, EmbeddingEngine())
            result = idx.reconcile(scope="auto", project="a")
            self.assertEqual(result.scope_mode, "auto_project")

            rows = idx.storage.conn.execute("SELECT relative_path FROM files ORDER BY relative_path").fetchall()
            paths = [r[0] for r in rows]
            self.assertIn("docs/workspace.md", paths)
            self.assertIn("projects/a/docs/a.md", paths)
            self.assertNotIn("projects/b/docs/b.md", paths)
            idx.close()

    def test_manifest_bootstrap_from_db_when_manifest_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / "docs").mkdir(parents=True)
            (root / "docs" / "x.md").write_text("x", encoding="utf-8")

            idx = Indexer(root, EmbeddingEngine())
            first = idx.reconcile()
            self.assertGreaterEqual(first.added, 1)
            idx.close()

            manifest = root / ".rag" / "manifest.json"
            manifest.unlink(missing_ok=True)

            idx2 = Indexer(root, EmbeddingEngine())
            second = idx2.reconcile()
            self.assertEqual(second.added, 0)
            self.assertEqual(second.changed, 0)
            self.assertTrue(manifest.exists())
            idx2.close()


if __name__ == "__main__":
    unittest.main()
