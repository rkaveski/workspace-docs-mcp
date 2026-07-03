from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from workspace_docs_mcp.embeddings import (
    ENV_EMBEDDING_INIT_TIMEOUT,
    ENV_EMBEDDING_MODE,
    MODE_LABEL_FALLBACK,
    EmbeddingEngine,
)


class EmbeddingModeTests(unittest.TestCase):
    def test_fallback_mode_never_touches_fastembed(self) -> None:
        with patch.dict("os.environ", {ENV_EMBEDDING_MODE: "fallback"}, clear=False):
            with patch.object(EmbeddingEngine, "_build_model", side_effect=AssertionError("must not init")):
                engine = EmbeddingEngine()
        self.assertFalse(engine.available)
        self.assertEqual(engine.mode, MODE_LABEL_FALLBACK)
        # Fallback embeddings still work and are deterministic.
        first = engine.embed_one("billing invoice retry")
        second = engine.embed_one("billing invoice retry")
        self.assertEqual(first, second)
        self.assertTrue(any(v != 0.0 for v in first))

    def test_auto_mode_times_out_hung_init_and_falls_back(self) -> None:
        def hang(self):  # simulates a stalled Hugging Face download
            time.sleep(10)
            return object()

        env = {ENV_EMBEDDING_MODE: "auto", ENV_EMBEDDING_INIT_TIMEOUT: "1"}
        with patch.dict("os.environ", env, clear=False):
            with patch.object(EmbeddingEngine, "_build_model", hang):
                started = time.monotonic()
                engine = EmbeddingEngine()
                elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5.0)
        self.assertFalse(engine.available)
        self.assertEqual(engine.mode, MODE_LABEL_FALLBACK)

    def test_init_error_degrades_to_fallback(self) -> None:
        with patch.dict("os.environ", {ENV_EMBEDDING_MODE: "fastembed"}, clear=False):
            with patch.object(EmbeddingEngine, "_build_model", return_value=None):
                engine = EmbeddingEngine()
        self.assertFalse(engine.available)
        self.assertEqual(engine.mode, MODE_LABEL_FALLBACK)


if __name__ == "__main__":
    unittest.main()
