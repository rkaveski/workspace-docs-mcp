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


def _write_workspace_config(root: Path, projects: tuple[str, ...] = ()) -> None:
    blocks = ['[[source]]', 'name = "workspace"', 'path = "docs"', 'scope = "workspace"', '']
    for name in projects:
        blocks += [
            '[[source]]',
            f'name = "{name}"',
            f'path = "projects/{name}/docs"',
            'scope = "project"',
            f'project = "projects/{name}"',
            '',
        ]
    (root / ".workspace-docs.toml").write_text("\n".join(blocks), encoding="utf-8")


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
    def setUp(self) -> None:
        self._env_patch = patch.dict(
            "os.environ",
            {"WORKSPACE_DOCS_ALLOW_WORKSPACE_ROOT_OVERRIDE": "true"},
            clear=False,
        )
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        for rt in list(_RUNTIMES.values()):
            try:
                rt.indexer.close()
            except Exception:
                pass
        _RUNTIMES.clear()

    def test_get_doc_uses_parser_for_binaryish_formats(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            _write_workspace_config(root)
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
            _write_workspace_config(root)
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
            _write_workspace_config(root)
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
            _write_workspace_config(root)
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
            _write_workspace_config(root)
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
            _write_workspace_config(root)
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
            _write_workspace_config(root, projects=("a", "b"))
            (root / "docs").mkdir(parents=True)
            (root / "docs" / "workspace.md").write_text("workspace", encoding="utf-8")
            (root / "projects" / "a" / "docs").mkdir(parents=True)
            (root / "projects" / "a" / "docs" / "a.md").write_text("a", encoding="utf-8")
            (root / "projects" / "b" / "docs").mkdir(parents=True)
            (root / "projects" / "b" / "docs" / "b.md").write_text("b", encoding="utf-8")

            idx = Indexer(root, EmbeddingEngine())
            result = idx.reconcile(scope="auto", project="projects/a")
            self.assertEqual(result.scope_mode, "auto_project")

            rows = idx.storage.conn.execute("SELECT relative_path FROM files ORDER BY relative_path").fetchall()
            paths = [r[0] for r in rows]
            self.assertIn("docs/workspace.md", paths)
            self.assertIn("projects/a/docs/a.md", paths)
            self.assertNotIn("projects/b/docs/b.md", paths)
            idx.close()

    def test_search_recency_prefers_newer_doc(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            _write_workspace_config(root)
            docs = root / "docs"
            docs.mkdir(parents=True)
            old_path = docs / "dataflow_old.md"
            new_path = docs / "dataflow_new.md"
            old_path.write_text("billing data flow ingestion pipeline", encoding="utf-8")
            new_path.write_text("billing data flow ingestion pipeline", encoding="utf-8")

            idx = Indexer(root, EmbeddingEngine())
            idx.reconcile()

            # Deterministically age the "old" doc (both created + modified) so the
            # recency basis (max of the two) is clearly older than the new doc.
            now_ns = time.time_ns()
            old_ns = now_ns - 400 * 86400 * 1_000_000_000
            idx.storage.conn.execute(
                "UPDATE files SET modified_at_ns = ?, created_at_ns = ? WHERE relative_path = ?",
                (old_ns, old_ns, "docs/dataflow_old.md"),
            )
            idx.storage.conn.execute(
                "UPDATE files SET modified_at_ns = ?, created_at_ns = ? WHERE relative_path = ?",
                (now_ns, now_ns, "docs/dataflow_new.md"),
            )
            idx.storage.conn.commit()

            retriever = Retriever(root, idx.storage, idx.embedding_engine)
            out = retriever.search("billing data flow ingestion", scope="auto", context_path=None, k=5)
            self.assertGreaterEqual(out["count"], 2)
            self.assertIn("dataflow_new.md", out["hits"][0]["relative_path"])
            idx.close()

    def test_reconcile_backfills_legacy_created_at_ns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            _write_workspace_config(root)
            docs = root / "docs"
            docs.mkdir(parents=True)
            (docs / "legacy.md").write_text("billing data flow ingestion pipeline", encoding="utf-8")

            idx = Indexer(root, EmbeddingEngine())
            idx.reconcile()

            # Simulate a row indexed before created_at_ns existed (migration default).
            idx.storage.conn.execute(
                "UPDATE files SET created_at_ns = 0 WHERE relative_path = ?",
                ("docs/legacy.md",),
            )
            idx.storage.conn.commit()

            # A normal reconcile (no content change) should self-heal the column.
            idx.reconcile()
            row = idx.storage.get_file_record("docs/legacy.md")
            self.assertIsNotNone(row)
            self.assertGreater(int(row["created_at_ns"]), 0)
            idx.close()

    def test_search_recency_uses_creation_when_newer_than_modified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            _write_workspace_config(root)
            docs = root / "docs"
            docs.mkdir(parents=True)
            (docs / "stale_mtime.md").write_text("billing data flow ingestion pipeline", encoding="utf-8")
            (docs / "old.md").write_text("billing data flow ingestion pipeline", encoding="utf-8")

            idx = Indexer(root, EmbeddingEngine())
            idx.reconcile()

            now_ns = time.time_ns()
            old_ns = now_ns - 400 * 86400 * 1_000_000_000
            # stale_mtime.md simulates a freshly-copied file: stale mtime but recent
            # creation. max(created, modified) should treat it as the recent doc.
            idx.storage.conn.execute(
                "UPDATE files SET modified_at_ns = ?, created_at_ns = ? WHERE relative_path = ?",
                (old_ns, now_ns, "docs/stale_mtime.md"),
            )
            idx.storage.conn.execute(
                "UPDATE files SET modified_at_ns = ?, created_at_ns = ? WHERE relative_path = ?",
                (old_ns, old_ns, "docs/old.md"),
            )
            idx.storage.conn.commit()

            retriever = Retriever(root, idx.storage, idx.embedding_engine)
            out = retriever.search("billing data flow ingestion", scope="auto", context_path=None, k=5)
            self.assertGreaterEqual(out["count"], 2)
            self.assertIn("stale_mtime.md", out["hits"][0]["relative_path"])
            idx.close()

    def test_search_hit_exposes_modified_at(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            _write_workspace_config(root)
            (root / "docs").mkdir(parents=True)
            (root / "docs" / "note.md").write_text("invoice alpha token", encoding="utf-8")

            idx = Indexer(root, EmbeddingEngine())
            idx.reconcile()
            retriever = Retriever(root, idx.storage, idx.embedding_engine)

            out = retriever.search("invoice alpha", scope="auto", context_path=None, k=5)
            self.assertGreaterEqual(out["count"], 1)
            hit = out["hits"][0]
            self.assertIn("modified_at", hit)
            self.assertNotIn("modified_at_ns", hit)
            idx.close()

    def test_external_docs_outside_repo_are_searchable_and_readable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            root = base / "repo"
            external = base / "handbook"
            root.mkdir(parents=True)
            external.mkdir(parents=True)
            (external / "billing.md").write_text(
                "billing api key rotation runbook xyz-987", encoding="utf-8"
            )
            (root / ".workspace-docs.toml").write_text(
                f'[[source]]\nname = "handbook"\npath = "{external.as_posix()}"\nscope = "workspace"\n',
                encoding="utf-8",
            )

            with patch.dict(
                "os.environ",
                {"WORKSPACE_DOCS_ALLOWED_ROOTS": str(base)},
                clear=False,
            ):
                refresh_docs(workspace_root=str(root))
                deadline = time.time() + 5
                while time.time() < deadline:
                    cur = status_docs(workspace_root=str(root))
                    if cur["refresh_job"]["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.05)

                out = search_docs("xyz-987 billing rotation", workspace_root=str(root))
                self.assertGreaterEqual(out["count"], 1)
                abs_id = (external / "billing.md").as_posix()
                self.assertEqual(out["hits"][0]["relative_path"], abs_id)

                doc = get_doc(path=abs_id, workspace_root=str(root))
                self.assertIn("rotation runbook", doc["content"])

    def test_manifest_bootstrap_from_db_when_manifest_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            _write_workspace_config(root)
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
