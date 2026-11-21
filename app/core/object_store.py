"""The object-store seam (KAN-200; Anto, 2026-10-19): where the product keeps
files that are not rows — a user's signature image today (Dealer
Administration, Users → Personal settings), a user's photo, a Betrieb's
logo, imagery and AGB, an export archive. Cross-cutting, like the mailer
seam: one protocol, the local-directory store built now — in production on
a mounted persistent disk (KAN-402, ADR-081 as amended), a stop-gap until a
Swiss S3-compatible store, a follow-up with its own dependency decision.

Keys are paths the caller composes, tenant-partitioned by convention and
checked here: `dealerships/<dealership id>/users/<user id>/signature`. A
key is never built from client input, never contains `..`, and the local
store refuses anything that resolves outside its root. A row holds the key
(`user.signature_image_ref`), never the bytes and never base64 (the
ticket's rule).
"""

import dataclasses
import logging
import os
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import get_settings
from app.core.deployment_environment import DeploymentEnvironment, current_deployment_environment

logger = logging.getLogger("app.core.object_store")

_COPY_CHUNK = 1024 * 1024
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")


class InvalidObjectKey(ValueError):
    """The key is not one this store accepts (shape, `..`, absolute)."""


def validate_key(key: str) -> str:
    if not _KEY.fullmatch(key) or "/../" in f"/{key}/" or "//" in key:
        raise InvalidObjectKey(f"invalid object key: {key!r}")
    return key


@dataclasses.dataclass(frozen=True)
class StoredObject:
    data: bytes
    content_type: str


@dataclasses.dataclass(frozen=True)
class OpenedObject:
    """A stored object opened for a streamed read (KAN-725): the caller
    reads `stream` in chunks — `chunks()` does, and closes it at the end —
    and never holds the whole object in memory. Whoever opens one closes it."""

    stream: BinaryIO
    content_type: str
    size: int

    def chunks(self, chunk_size: int = _COPY_CHUNK) -> Iterator[bytes]:
        try:
            while chunk := self.stream.read(chunk_size):
                yield chunk
        finally:
            self.stream.close()

    def close(self) -> None:
        self.stream.close()


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str) -> None: ...

    def put_file(self, key: str, source: BinaryIO, *, content_type: str) -> None:
        """`source` (an open binary file, read from its current position to
        its end) stored in chunks — never read whole into memory (KAN-715:
        a Betrieb's export archive). Replaces whatever the key held."""
        ...

    def get(self, key: str) -> StoredObject | None: ...

    def open(self, key: str) -> OpenedObject | None:
        """The object opened for a streamed read, or None when the key holds
        nothing (KAN-725: a Betrieb's export archive is downloaded in
        chunks, never read whole into the web process's memory)."""
        ...

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

    def put_file(self, key: str, source: BinaryIO, *, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("wb") as written:
            shutil.copyfileobj(source, written, _COPY_CHUNK)
        tmp.replace(path)
        path.with_name(path.name + ".meta").write_text(content_type, encoding="utf-8")

    def get(self, key: str) -> StoredObject | None:
        path = self._path(key)
        if not path.is_file():
            return None
        meta = path.with_name(path.name + ".meta")
        content_type = meta.read_text(encoding="utf-8").strip() if meta.is_file() else "application/octet-stream"
        return StoredObject(data=path.read_bytes(), content_type=content_type)

    def open(self, key: str) -> OpenedObject | None:
        path = self._path(key)
        try:
            stream = path.open("rb")
        except (FileNotFoundError, IsADirectoryError):
            return None
        meta = path.with_name(path.name + ".meta")
        try:
            content_type = meta.read_text(encoding="utf-8").strip() if meta.is_file() else "application/octet-stream"
            # The open file stays readable if `put`/`delete` replaces or
            # removes the path meanwhile (POSIX): the reader sees one version.
            size = os.fstat(stream.fileno()).st_size
        except BaseException:
            stream.close()
            raise
        return OpenedObject(stream=stream, content_type=content_type, size=size)

    def delete(self, key: str) -> bool:
        path = self._path(key)
        existed = path.is_file()
        for candidate in (path, path.with_name(path.name + ".meta")):
            if candidate.is_file():
                candidate.unlink()
        return existed


def require_mounted_disk(root: Path) -> None:
    """KAN-402: `root` must lie on a mounted file system other than the
    container's root — Render mounts a persistent disk at its `mountPath`;
    anything else on the instance is lost at the next deploy. Raises
    RuntimeError naming the directory; the caller fails the start."""

    resolved = root.resolve()
    for candidate in (resolved, *resolved.parents):
        if candidate == Path(candidate.anchor):
            break
        if os.path.ismount(candidate):
            return
    raise RuntimeError(
        f"DMS_OBJECT_STORE_LOCAL_DIR={str(root)!r} is not on a mounted persistent disk: its files would not survive a "
        "deploy (KAN-402). Mount the service's disk there (render.yaml `disk.mountPath`)."
    )


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
        root = Path(settings.object_store_local_dir)
        if current_deployment_environment(settings) is DeploymentEnvironment.PRODUCTION:  # KAN-499
            # A directory on the web service's own disk is emptied by every
            # deploy (Render), while the rows keep their keys: a signature
            # image would vanish silently. KAN-402 (Anto, 2026-10-29; ADR-081
            # as amended): production keeps its files on a persistent disk
            # mounted at the directory — a stop-gap until a Swiss S3 store —
            # and refuses to start when the directory is not on one.
            require_mounted_disk(root)
        _store = LocalDirectoryObjectStore(root)
    return _store


def set_object_store(store: ObjectStore | None) -> None:
    """Test-only override point, and the one line a bucket-backed store's
    own wiring would call at startup once one exists."""

    global _store
    _store = store
