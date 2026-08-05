"""Content-addressed local storage (Phase 4B).

Bytes are keyed by their SHA-256 (`ab/cd/<sha256>`), so identical content is stored once. Writes are
idempotent (an existing key is never rewritten). Storage can be disabled entirely — observations then
remain URL-only. Image bytes are never embedded in Postgres. No cloud bucket is required in this phase.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class ContentAddressedStore:
    def __init__(self, base_dir: str, *, enabled: bool = True, max_bytes: int = 5_000_000) -> None:
        self.base_dir = Path(base_dir)
        self.enabled = enabled
        self.max_bytes = max_bytes

    def key_for(self, sha256: str) -> str:
        return f"{sha256[:2]}/{sha256[2:4]}/{sha256}"

    def _path(self, sha256: str) -> Path:
        return self.base_dir / self.key_for(sha256)

    def exists(self, sha256: str) -> bool:
        return self.enabled and self._path(sha256).is_file()

    def write(self, sha256: str, data: bytes) -> str | None:
        """Store `data` under its content hash. Returns the storage key, or None when disabled/too
        large. Idempotent: an existing object is left untouched."""
        if not self.enabled:
            return None
        if len(data) > self.max_bytes:
            return None
        key = self.key_for(sha256)
        target = self._path(sha256)
        if target.is_file():
            return key  # already stored — no duplicate byte write
        target.parent.mkdir(parents=True, exist_ok=True)
        # atomic write: temp file in the same dir, then rename
        fd, tmp = tempfile.mkstemp(dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return key

    def read(self, sha256: str) -> bytes | None:
        p = self._path(sha256)
        return p.read_bytes() if p.is_file() else None
