from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{2,}")


class EmbeddingEngine:
    """Embeddings with fastembed primary and deterministic local fallback."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", dim: int = 256) -> None:
        self.model_name = model_name
        self.dim = dim
        self._model = None
        self._available = False

        try:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=model_name)
            self._available = True
        except Exception:
            self._model = None
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def mode(self) -> str:
        return "fastembed" if self._available else "hashed-fallback"

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
