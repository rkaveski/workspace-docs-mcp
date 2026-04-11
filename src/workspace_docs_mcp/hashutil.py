from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()
