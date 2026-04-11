from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_docs_mcp import models
from workspace_docs_mcp.parsers import OCRRuntime, ParserError, _get_ocr_runtime, parse_document


class ParserTests(unittest.TestCase):
    def test_parse_csv_header_aware_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sample.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["name", "age"])
                writer.writerow(["Alice", 30])
                writer.writerow(["Bob", 42])

            segments = parse_document(path)
            self.assertGreaterEqual(len(segments), 2)
            self.assertEqual(segments[0].section_title, "sheet:csv")
            self.assertIn("name=Alice", segments[0].text)
            self.assertIn("age=30", segments[0].text)

    def test_parse_xlsx_multiple_sheets_header_aware_rows(self) -> None:
        try:
            from openpyxl import Workbook
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"openpyxl missing: {exc}")

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sample.xlsx"
            wb = Workbook()
            s1 = wb.active
            s1.title = "Main"
            s1.append(["id", "title"])
            s1.append([1, "Alpha"])
            s2 = wb.create_sheet(title="Meta")
            s2.append(["key", "value"])
            s2.append(["env", "prod"])
            wb.save(path)
            wb.close()

            segments = parse_document(path)
            self.assertGreaterEqual(len(segments), 2)
            titles = {s.section_title for s in segments}
            self.assertIn("sheet:Main", titles)
            self.assertIn("sheet:Meta", titles)

    def test_table_guardrail_max_rows_applied(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sample.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["k", "v"])
                writer.writerow(["a", "1"])
                writer.writerow(["b", "2"])
                writer.writerow(["c", "3"])

            with patch.dict("os.environ", {"WORKSPACE_DOCS_MAX_ROWS_PER_TABLE_FILE": "2"}):
                segments = parse_document(path)

            self.assertEqual(len(segments), 3)
            self.assertTrue(segments[-1].text.startswith("TRUNCATED:"))

    def test_cell_truncation_applied(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sample.csv"
            long_value = "x" * 40
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["name", "payload"])
                writer.writerow(["A", long_value])

            with patch.dict("os.environ", {"WORKSPACE_DOCS_MAX_CELL_CHARS": "10"}):
                segments = parse_document(path)

            self.assertIn("payload=xxxxxxx...", segments[0].text)

    def test_image_ocr_default_is_disabled(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            runtime = _get_ocr_runtime()
        self.assertFalse(runtime.enabled)

    def test_image_ocr_missing_tesseract_graceful_skip(self) -> None:
        try:
            from PIL import Image
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"Pillow missing: {exc}")

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "img.png"
            Image.new("RGB", (20, 20), "white").save(path)

            with patch(
                "workspace_docs_mcp.parsers._get_ocr_runtime",
                return_value=OCRRuntime(enabled=True, available=False, reason="tesseract missing", lang="eng", timeout_seconds=15),
            ):
                with self.assertRaises(ParserError):
                    parse_document(path)

    def test_image_ocr_timeout_raises_parser_error(self) -> None:
        try:
            from PIL import Image
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"Pillow missing: {exc}")

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "img.png"
            Image.new("RGB", (20, 20), "white").save(path)

            with patch(
                "workspace_docs_mcp.parsers._get_ocr_runtime",
                return_value=OCRRuntime(enabled=True, available=True, reason=None, lang="eng", timeout_seconds=1),
            ):
                with patch("pytesseract.image_to_string", side_effect=RuntimeError("timeout")):
                    with self.assertRaises(ParserError) as ctx:
                        parse_document(path)
            self.assertIn("timeout", str(ctx.exception).lower())

    def test_image_ocr_success(self) -> None:
        try:
            from PIL import Image, ImageDraw
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"image deps unavailable: {exc}")

        with patch.dict("os.environ", {"WORKSPACE_DOCS_ENABLE_IMAGE_OCR": "true"}):
            runtime = _get_ocr_runtime()
        if not runtime.available:
            self.skipTest("tesseract not available in environment")

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ocr.png"
            img = Image.new("RGB", (800, 240), "white")
            draw = ImageDraw.Draw(img)
            draw.text((20, 80), "HELLO OCR SAMPLE", fill="black")
            img.save(path)

            with patch.dict("os.environ", {"WORKSPACE_DOCS_ENABLE_IMAGE_OCR": "true"}):
                segments = parse_document(path)
            if not segments:
                self.skipTest("OCR returned empty text in this environment")
            joined = "\n".join(s.text for s in segments).upper()
            self.assertTrue("OCR" in joined or "HELLO" in joined)

    def test_supported_extensions_include_tables_and_images(self) -> None:
        expected = {".csv", ".xlsx", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif"}
        self.assertTrue(expected.issubset(models.SUPPORTED_EXTENSIONS))


if __name__ == "__main__":
    unittest.main()
