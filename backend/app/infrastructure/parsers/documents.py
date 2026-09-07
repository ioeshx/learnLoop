"""Parsers for the stage-seven TXT, Markdown, PDF, and HTML formats."""

import io
import re
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from pypdf import PdfReader

_MARKDOWN_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_TOKEN = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]")


@dataclass(frozen=True, slots=True)
class ParsedSection:
    text: str
    page_number: int | None = None
    section: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    title: str | None
    sections: tuple[ParsedSection, ...]


@dataclass(frozen=True, slots=True)
class TextChunk:
    content: str
    token_count: int
    page_number: int | None
    section: str | None


def parse_document(
    content: bytes, *, filename: str | None, media_type: str
) -> ParsedDocument:
    suffix = Path(filename or "").suffix.lower()
    normalized_media = media_type.partition(";")[0].strip().lower()
    if suffix == ".pdf" or normalized_media == "application/pdf":
        return _parse_pdf(content, filename)
    if suffix in {".md", ".markdown"} or normalized_media in {
        "text/markdown",
        "text/x-markdown",
    }:
        return _parse_markdown(_decode_text(content), filename)
    if suffix in {".html", ".htm"} or normalized_media == "text/html":
        return _parse_html(_decode_text(content), filename)
    if suffix == ".txt" or normalized_media in {
        "text/plain",
        "application/octet-stream",
    }:
        return _parse_plain_text(_decode_text(content), filename)
    raise ValueError(
        "unsupported document type; use TXT, Markdown, PDF, or an HTML URL"
    )


def chunk_document(
    document: ParsedDocument,
    *,
    chunk_size: int = 1_000,
    overlap: int = 150,
) -> tuple[TextChunk, ...]:
    if chunk_size < 100:
        raise ValueError("chunk_size must be at least 100 characters")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be non-negative and less than chunk_size")
    chunks: list[TextChunk] = []
    for section in document.sections:
        for piece in _split_text(section.text, chunk_size, overlap):
            token_count = len(_TOKEN.findall(piece))
            if token_count == 0:
                continue
            chunks.append(
                TextChunk(
                    content=piece,
                    token_count=token_count,
                    page_number=section.page_number,
                    section=section.section,
                )
            )
    if not chunks:
        raise ValueError("document contains no searchable text")
    return tuple(chunks)


def _parse_plain_text(content: str, filename: str | None) -> ParsedDocument:
    normalized = _normalize_text(content)
    if not normalized:
        raise ValueError("text document contains no searchable text")
    title = Path(filename).stem if filename else None
    return ParsedDocument(title=title, sections=(ParsedSection(normalized),))


def _parse_markdown(content: str, filename: str | None) -> ParsedDocument:
    sections: list[ParsedSection] = []
    current_heading: str | None = None
    current_lines: list[str] = []
    document_title: str | None = None

    def flush() -> None:
        text = _clean_markdown("\n".join(current_lines))
        if text:
            sections.append(ParsedSection(text=text, section=current_heading))
        current_lines.clear()

    for line in content.splitlines():
        heading = _MARKDOWN_HEADING.match(line)
        if heading is None:
            current_lines.append(line)
            continue
        flush()
        current_heading = _clean_markdown(heading.group(2))
        if document_title is None and heading.group(1) == "#":
            document_title = current_heading
    flush()
    if not sections:
        raise ValueError("Markdown document contains no searchable text")
    return ParsedDocument(
        title=document_title or (Path(filename).stem if filename else None),
        sections=tuple(sections),
    )


def _parse_pdf(content: bytes, filename: str | None) -> ParsedDocument:
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as error:
        raise ValueError("invalid or encrypted PDF document") from error
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as error:
            raise ValueError("encrypted PDF documents are not supported") from error
    sections: list[ParsedSection] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = _normalize_text(page.extract_text() or "")
        if text:
            sections.append(ParsedSection(text=text, page_number=page_number))
    if not sections:
        raise ValueError(
            "PDF contains no extractable text; scanned PDFs require OCR"
        )
    metadata_title = reader.metadata.title if reader.metadata else None
    return ParsedDocument(
        title=metadata_title or (Path(filename).stem if filename else None),
        sections=tuple(sections),
    )


def _parse_html(content: str, filename: str | None) -> ParsedDocument:
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "template", "noscript", "svg"]):
        tag.decompose()
    title = (
        _normalize_text(soup.title.get_text(" ", strip=True))
        if soup.title
        else None
    )
    root = soup.find("main") or soup.find("article") or soup.body or soup
    text = _normalize_text(root.get_text("\n", strip=True))
    if not text:
        raise ValueError("web page contains no searchable text")
    return ParsedDocument(
        title=title or (Path(filename).stem if filename else None),
        sections=(ParsedSection(text=text, section=title),),
    )


def _split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    normalized = _normalize_text(text)
    if len(normalized) <= chunk_size:
        return [normalized] if normalized else []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        hard_end = min(start + chunk_size, len(normalized))
        end = hard_end
        if hard_end < len(normalized):
            candidates = [
                normalized.rfind(separator, start + chunk_size // 2, hard_end)
                for separator in ("\n\n", "\n", "。", ". ", " ")
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + 1
        piece = normalized[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(normalized):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _decode_text(content: bytes) -> str:
    if b"\x00" in content:
        raise ValueError("binary content cannot be parsed as text")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("text documents must use UTF-8 encoding") from error


def _clean_markdown(value: str) -> str:
    value = re.sub(r"```.*?```", " ", value, flags=re.DOTALL)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"^\s*>\s?", "", value, flags=re.MULTILINE)
    value = re.sub(r"^\s*[-*+]\s+", "", value, flags=re.MULTILINE)
    value = re.sub(r"[*_~]", "", value)
    return _normalize_text(value)


def _normalize_text(value: str) -> str:
    lines = [re.sub(r"[\t ]+", " ", line).strip() for line in value.splitlines()]
    compact: list[str] = []
    previous_blank = True
    for line in lines:
        if line:
            compact.append(line)
            previous_blank = False
        elif not previous_blank:
            compact.append("")
            previous_blank = True
    return "\n".join(compact).strip()
