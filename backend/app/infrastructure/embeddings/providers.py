"""Local and OpenAI-compatible embedding providers."""

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Protocol

import httpx
import numpy as np


class EmbeddingProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class LocalHashEmbeddingProvider:
    """Dependency-free local feature hashing suitable for an offline baseline."""

    def __init__(self, *, dimensions: int = 384) -> None:
        if dimensions < 32:
            raise ValueError("embedding dimensions must be at least 32")
        self._dimensions = dimensions

    @property
    def model_name(self) -> str:
        return f"local-hash-v1-{self._dimensions}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    # feature hashing implementation
    def _embed(self, text: str) -> list[float]:
        vector = np.zeros(self._dimensions, dtype=np.float64)
        features = _text_features(text)
        if not features:
            raise ValueError("cannot embed empty text")
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
            index = int.from_bytes(digest[:8], "big") % self._dimensions
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign
        norm = float(np.linalg.norm(vector))
        if norm:
            vector /= norm
        return [float(value) for value in vector]


class FakeEmbeddingProvider:
    def __init__(
        self,
        vectors: Mapping[str, Sequence[float]],
        *,
        model_name: str = "fake-embedding-v1",
    ) -> None:
        if not vectors:
            raise ValueError("fake embedding vectors must not be empty")
        self._vectors = {
            text: [float(value) for value in vector]
            for text, vector in vectors.items()
        }
        dimensions = {len(vector) for vector in self._vectors.values()}
        if len(dimensions) != 1 or next(iter(dimensions)) == 0:
            raise ValueError("fake embedding vectors must have one non-zero dimension")
        self._dimensions = next(iter(dimensions))
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._get(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._get(text)

    def _get(self, text: str) -> list[float]:
        try:
            return list(self._vectors[text])
        except KeyError as error:
            raise ValueError(f"no fake embedding configured for {text!r}") from error


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        dimensions: int,
        timeout_seconds: float = 60,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("embedding API key must not be empty")
        self._model = model
        self._dimensions = dimensions
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("embedding input must contain non-empty text")
        response = await self._client.post(
            self._url,
            headers=self._headers,
            json={
                "model": self._model,
                "input": list(texts),
                "dimensions": self._dimensions,
                "encoding_format": "float",
            },
        )
        response.raise_for_status()
        payload = response.json()
        raw_data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(raw_data, list) or len(raw_data) != len(texts):
            raise ValueError("embedding response has an invalid data array")
        ordered: list[tuple[int, list[float]]] = []
        for fallback_index, item in enumerate(raw_data):
            if not isinstance(item, dict) or not isinstance(
                item.get("embedding"), list
            ):
                raise ValueError("embedding response item is invalid")
            vector = [float(value) for value in item["embedding"]]
            if len(vector) != self._dimensions:
                raise ValueError("embedding response dimension does not match settings")
            index = item.get("index", fallback_index)
            if not isinstance(index, int):
                raise ValueError("embedding response index must be an integer")
            ordered.append((index, vector))
        ordered.sort(key=lambda pair: pair[0])
        return [vector for _, vector in ordered]

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _text_features(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    words = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]", normalized)
    compact = normalized.replace(" ", "")
    character_ngrams = [
        compact[index : index + 3]
        for index in range(max(0, len(compact) - 2))
    ]
    return [*words, *character_ngrams]
