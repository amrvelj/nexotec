"""The object-store seam (KAN-200; Anto, 2026-10-19): where the product keeps
files that are not rows — a user's signature image today (Dealer
Administration, Users → Personal settings), a photo and a Betrieb's logo
upload later. Cross-cutting, like the mailer seam: one protocol, the
local-disk store built now, an S3-compatible store a follow-up with its
own dependency decision.

Keys are paths the caller composes, tenant-partitioned by convention and
checked here: `dealerships/<dealership id>/users/<user id>/signature`. A
key is never built from client input, never contains `..`, and the local
store refuses anything that resolves outside its root. A row holds the key
(`user.signature_image_ref`), never the bytes and never base64 (the
ticket's rule).
"""

import dataclasses
import logging
import re
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger("app.core.object_store")

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")


class InvalidObjectKey(ValueError):
    """The key is not one this store accepts (shape, `..`, absolute)."""


def validate_key(key: str) -> str:
    if not _KEY.match(key) or "/../" in f"/{key}/" or "//" in key:
        raise InvalidObjectKey(f"invalid object key: {key!r}")
    return key


@dataclasses.dataclass(frozen=True)
class StoredObject:
    data: bytes
    content_type: str


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str) -> None: ...

    def get(self, key: str) -> StoredObject | None: ...

    def delete(self, key: str) -> bool:
        """True when something was removed."""
        ...


class LocalDirectoryObjectStore:
    """Files under one root directory, the content type beside each in a
    `.meta` file. Dev, test and staging; a single-instance store (no
    replication), which is what those are."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self._root / validate_key(key)).resolve()
        if self._root not in path.parents:
            raise InvalidObjectKey(f"object key escapes the store root: {key!r}")
        return path

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        path.with_name(path.name + ".meta").write_text(content_type, encoding="utf-8")

    def get(self, key: str) -> StoredObject | None:
        path = self._path(key)
        if not path.is_file():
            return None
        meta = path.with_name(path.name + ".meta")
        content_type = meta.read_text(encoding="utf-8").strip() if meta.is_file() else "application/octet-stream"
        return StoredObject(data=path.read_bytes(), content_type=content_type)

    def delete(self, key: str) -> bool:
        path = self._path(key)
        existed = path.is_file()
        for candidate in (path, path.with_name(path.name + ".meta")):
            if candidate.is_file():
                candidate.unlink()
        return existed


_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    """FastAPI dependency; tests override it with a store under a temp dir."""

    global _store
    if _store is None:
        settings = get_settings()
        if settings.object_store_backend != "local":
            raise RuntimeError(
                f"DMS_OBJECT_STORE_BACKEND={settings.object_store_backend!r} is not built — only 'local' is (KAN-200)."
            )
        _store = LocalDirectoryObjectStore(Path(settings.object_store_local_dir))
    return _store


def set_object_store(store: ObjectStore | None) -> None:
    """Test-only override point, and the one line a bucket-backed store's
    own wiring would call at startup once one exists."""

    global _store
    _store = store
