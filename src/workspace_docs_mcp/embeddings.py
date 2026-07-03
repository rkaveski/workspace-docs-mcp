from __future__ import annotations

import hashlib
import math
import os
import re
import threading
from collections import Counter
from enum import Enum
from typing import Final

TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{2,}")

DEFAULT_MODEL_NAME: Final = "BAAI/bge-small-en-v1.5"

# Embedding backend selection. `auto` tries fastembed with a bounded init wait;
# `fallback` never touches fastembed (no model download, no network — right for
# locked-down machines); `fastembed` waits for fastembed without a timeout.
ENV_EMBEDDING_MODE: Final = "WORKSPACE_DOCS_EMBEDDING_MODE"
# How long `auto` waits for fastembed to initialize (first run may download the
# model from Hugging Face) before falling back. Firewalls that silently drop
# traffic hang the download instead of failing it, so a bound is essential.
ENV_EMBEDDING_INIT_TIMEOUT: Final = "WORKSPACE_DOCS_EMBEDDING_INIT_TIMEOUT_SECONDS"
DEFAULT_INIT_TIMEOUT_SECONDS: Final = 30.0

MODE_LABEL_FASTEMBED: Final = "fastembed"
MODE_LABEL_FALLBACK: Final = "hashed-fallback"


class EmbeddingMode(str, Enum):
    AUTO = "auto"
    FASTEMBED = "fastembed"
    FALLBACK = "fallback"


def _configured_mode() -> EmbeddingMode:
    raw = os.getenv(ENV_EMBEDDING_MODE, "").strip().lower()
    try:
        return EmbeddingMode(raw) if raw else EmbeddingMode.AUTO
    except ValueError:
        return EmbeddingMode.AUTO


def _configured_init_timeout() -> float:
    raw = os.getenv(ENV_EMBEDDING_INIT_TIMEOUT, "").strip()
    try:
        value = float(raw) if raw else DEFAULT_INIT_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_INIT_TIMEOUT_SECONDS
    return max(1.0, value)


class EmbeddingEngine:
    """Embeddings with fastembed primary and deterministic local fallback.

    fastembed downloads its model from Hugging Face on first use. On networks
    that silently drop that traffic (common on VDI), the download hangs rather
    than raising, so `auto` mode initializes fastembed in a worker thread with a
    bounded wait and falls back to hashed embeddings if it doesn't come up.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME, dim: int = 256) -> None:
        self.model_name = model_name
        self.dim = dim
        self._model = None
        self._available = False

        requested = _configured_mode()
        if requested == EmbeddingMode.FALLBACK:
            return

        if requested == EmbeddingMode.FASTEMBED:
            # Explicitly requested: wait as long as it takes (e.g. a deliberate
            # first-run model download); errors still degrade to the fallback.
            self._model = self._build_model()
            self._available = self._model is not None
            return

        self._model = self._build_model_with_timeout(_configured_init_timeout())
        self._available = self._model is not None

    def _build_model(self):
        try:
            from fastembed import TextEmbedding

            return TextEmbedding(model_name=self.model_name)
        except Exception:
            return None

    def _build_model_with_timeout(self, timeout_seconds: float):
        result: dict = {}

        def target() -> None:
            result["model"] = self._build_model()

        worker = threading.Thread(target=target, name="workspace-docs-embedding-init", daemon=True)
        worker.start()
        worker.join(timeout_seconds)
        if worker.is_alive():
            # Init is stuck (likely a stalled model download). Leave the daemon
            # thread behind and serve hashed fallback for this process.
            return None
        return result.get("model")

    @property
    def available(self) -> bool:
        return self._available

    @property
    def mode(self) -> str:
        return MODE_LABEL_FASTEMBED if self._available else MODE_LABEL_FALLBACK

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._available and self._model is not None:
            return [list(map(float, vec)) for vec in self._model.embed(texts)]
        return [self._fallback_embed(text) for text in texts]

    def embed_one(self, text: str) -> list[float]:
        items = self.embed_many([text])
        return items[0] if items else []

    def _fallback_embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = TOKEN_RE.findall((text or "").lower())
        counts = Counter(tokens)
        if not counts:
            return vec

        for token, weight in counts.items():
            digest = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:2], "big") % self.dim
            sign = 1.0 if digest[2] % 2 == 0 else -1.0
            vec[idx] += sign * float(weight)

        norm = math.sqrt(sum(v * v for v in vec))
        if norm <= 0:
            return vec
        return [v / norm for v in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))
