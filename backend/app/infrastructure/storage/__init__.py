"""Document storage abstractions and local implementation."""

from app.infrastructure.storage.local import (
    DocumentStorage,
    LocalDocumentStorage,
    StoredDocument,
)

__all__ = ["DocumentStorage", "LocalDocumentStorage", "StoredDocument"]
