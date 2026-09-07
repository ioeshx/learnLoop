"""Local storage, parsing, web safety, and embedding unit tests."""

import io
from pathlib import Path

import httpx
import pytest

from app.infrastructure.embeddings import (
    LocalHashEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)
from app.infrastructure.parsers import (
    SafeWebPageFetcher,
    chunk_document,
    parse_document,
)
from app.infrastructure.storage import LocalDocumentStorage


def test_local_storage_deduplicates_recycles_and_blocks_traversal(
    tmp_path: Path,
) -> None:
    storage = LocalDocumentStorage(tmp_path / "files", max_bytes=100)
    first = storage.save(io.BytesIO(b"same document"))
    second = storage.save(io.BytesIO(b"same document"))

    assert first.key == second.key
    assert first.deduplicated is False
    assert second.deduplicated is True
    with storage.open(first.key) as stored:
        assert stored.read() == b"same document"
    with pytest.raises(ValueError, match="escapes"):
        storage.open("../secret")

    recycled = storage.recycle(first.key)
    assert recycled is not None
    with pytest.raises(FileNotFoundError):
        storage.open(first.key)
    storage.restore(recycled, first.key)
    with storage.open(first.key) as restored:
        assert restored.read() == b"same document"


def test_storage_rejects_oversized_and_empty_files(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path / "files", max_bytes=5)
    with pytest.raises(ValueError, match="exceeds"):
        storage.save(io.BytesIO(b"123456"))
    with pytest.raises(ValueError, match="empty"):
        storage.save(io.BytesIO(b""))


def test_markdown_html_and_pdf_parsing_preserve_citation_metadata() -> None:
    plain = parse_document(
        "UTF-8 纯文本资料".encode(),
        filename="notes.txt",
        media_type="text/plain",
    )
    assert plain.sections[0].text == "UTF-8 纯文本资料"

    markdown = parse_document(
        "# 图算法\n\n## BFS\n\n广度优先搜索使用队列。".encode(),
        filename="graph.md",
        media_type="text/markdown",
    )
    chunks = chunk_document(markdown, chunk_size=100, overlap=10)
    assert markdown.title == "图算法"
    assert chunks[0].section == "BFS"
    assert "队列" in chunks[0].content

    html = parse_document(
        b"<html><head><title>Queues</title><script>bad()</script></head>"
        b"<main><h1>BFS</h1><p>Use a queue.</p></main></html>",
        filename="page.html",
        media_type="text/html",
    )
    assert html.title == "Queues"
    assert "bad" not in html.sections[0].text

    pdf = parse_document(
        _text_pdf(), filename="lesson.pdf", media_type="application/pdf"
    )
    assert pdf.sections[0].page_number == 1
    assert "BFS uses a queue" in pdf.sections[0].text

    with pytest.raises(ValueError, match="no extractable text"):
        parse_document(
            _blank_pdf(), filename="scan.pdf", media_type="application/pdf"
        )


@pytest.mark.asyncio
async def test_local_and_remote_embedding_providers() -> None:
    local = LocalHashEmbeddingProvider(dimensions=64)
    first = await local.embed_query("BFS uses a queue")
    second = await local.embed_query("BFS uses a queue")
    assert first == second
    assert len(first) == 64
    assert sum(value * value for value in first) == pytest.approx(1.0)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [1.0, 0.0]},
                    {"index": 1, "embedding": [0.0, 1.0]},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    remote = OpenAICompatibleEmbeddingProvider(
        api_key="secret",
        model="embed-test",
        base_url="https://embedding.test/v1",
        dimensions=2,
        client=client,
    )
    assert await remote.embed_documents(["one", "two"]) == [
        [1.0, 0.0],
        [0.0, 1.0],
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_web_fetcher_blocks_private_hosts_and_reads_public_html() -> None:
    async def resolver(hostname: str) -> list[str]:
        return ["127.0.0.1"] if hostname == "private.test" else ["93.184.216.34"]

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=b"<main>public lesson</main>",
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = SafeWebPageFetcher(
        max_bytes=1_000,
        timeout_seconds=1,
        client=client,
        resolver=resolver,
    )
    with pytest.raises(ValueError, match="non-public"):
        await fetcher.fetch("http://private.test/page")
    fetched = await fetcher.fetch("https://public.test/page")
    assert fetched.content == b"<main>public lesson</main>"
    await client.aclose()


def _blank_pdf() -> bytes:
    from pypdf import PdfWriter

    target = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(target)
    return target.getvalue()


def _text_pdf() -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    target = io.BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=100)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 50 Td (BFS uses a queue.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(target)
    return target.getvalue()
