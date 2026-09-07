"""Path-safe, content-addressed local document storage."""

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class StoredDocument:
    key: str
    sha256: str
    size_bytes: int
    deduplicated: bool


class DocumentStorage(Protocol):
    def save(self, source: BinaryIO) -> StoredDocument: ...

    def open(self, key: str) -> BinaryIO: ...

    def recycle(self, key: str) -> str | None: ...

    def restore(self, recycled_key: str, key: str) -> None: ...


class LocalDocumentStorage:
    def __init__(self, root: Path, *, max_bytes: int = 20 * 1024 * 1024) -> None:
        self._root = root.resolve()
        self._objects = self._root / "objects"
        self._temporary = self._root / "tmp"
        self._trash = self._root / "trash"
        self._max_bytes = max_bytes
        for directory in (self._objects, self._temporary, self._trash):
            directory.mkdir(parents=True, exist_ok=True)

    def save(self, source: BinaryIO) -> StoredDocument:
        digest = hashlib.sha256()
        size = 0
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="upload-", dir=self._temporary
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as target:
                while block := source.read(64 * 1024):
                    size += len(block)
                    if size > self._max_bytes:
                        raise ValueError(
                            f"document exceeds the {self._max_bytes} byte limit"
                        )
                    digest.update(block)
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            if size == 0:
                raise ValueError("document must not be empty")
            sha256 = digest.hexdigest()
            key = f"{sha256[:2]}/{sha256[2:]}"
            destination = self._resolve_object(key)
            destination.parent.mkdir(parents=True, exist_ok=True)
            deduplicated = destination.exists()
            if deduplicated:
                temporary_path.unlink()
            else:
                os.replace(temporary_path, destination)
            return StoredDocument(
                key=key,
                sha256=sha256,
                size_bytes=size,
                deduplicated=deduplicated,
            )
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise

    def open(self, key: str) -> BinaryIO:
        return self._resolve_object(key).open("rb")

    def recycle(self, key: str) -> str | None:
        source = self._resolve_object(key)
        if not source.exists():
            return None
        recycled_key = f"{uuid4()}-{source.name}"
        destination = self._resolve_trash(recycled_key)
        os.replace(source, destination)
        _remove_empty_parents(source.parent, self._objects)
        return recycled_key

    def restore(self, recycled_key: str, key: str) -> None:
        source = self._resolve_trash(recycled_key)
        if not source.is_file():
            raise FileNotFoundError(recycled_key)
        destination = self._resolve_object(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            source.unlink()
            return
        os.replace(source, destination)

    def purge_trash(self) -> None:
        for entry in self._trash.iterdir():
            if entry.is_file():
                entry.unlink()
            elif entry.is_dir():
                shutil.rmtree(entry)

    def _resolve_object(self, key: str) -> Path:
        return _safe_child(self._objects, key)

    def _resolve_trash(self, key: str) -> Path:
        return _safe_child(self._trash, key)


def _safe_child(root: Path, key: str) -> Path:
    if not key or Path(key).is_absolute():
        raise ValueError("storage key must be a non-empty relative path")
    resolved = (root / key).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("storage key escapes the storage root")
    return resolved


def _remove_empty_parents(current: Path, boundary: Path) -> None:
    while current != boundary:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
