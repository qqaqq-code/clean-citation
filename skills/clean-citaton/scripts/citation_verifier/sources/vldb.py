from __future__ import annotations

import hashlib
import re
import zlib
from urllib.parse import quote

from ..models import Author, PaperHint, PaperRecord
from ..normalization import normalize_doi
from .base import DEFAULT_USER_AGENT, MetadataSource, UnsupportedLookupError, utc_now


class VldbSource(MetadataSource):
    """Resolve PVLDB papers from the VLDB Endowment's official PDF archive."""

    name = "vldb"

    def __init__(self, transport=None, user_agent: str = DEFAULT_USER_AGENT) -> None:
        super().__init__(transport=transport, user_agent=user_agent)

    def search(self, hint: PaperHint, limit: int = 5) -> list[PaperRecord]:
        urls = self._candidate_urls(hint)
        if not urls:
            raise UnsupportedLookupError(
                "vldb title lookup needs an official URL/DOI or volume, first page, and first author"
            )
        records: list[PaperRecord] = []
        for url in urls:
            response = self.transport.get(url, headers={"Accept": "application/pdf"})
            if response.status_code == 404:
                continue
            response.raise_for_status()
            if not response.content.startswith(b"%PDF-"):
                continue
            record = _record_from_pdf(response.content, response.url, hint)
            if record:
                records.append(record)
                if len(records) >= limit:
                    break
        return records

    @staticmethod
    def _candidate_urls(hint: PaperHint) -> list[str]:
        if hint.official_url:
            return [hint.official_url]
        doi = normalize_doi(hint.doi)
        if doi:
            return [f"https://doi.org/{quote(doi, safe='/')}"]
        volume = re.sub(r"\D", "", hint.volume or "")
        page_match = re.search(r"\d+", hint.pages or "")
        if not (volume and page_match and hint.authors):
            return []
        first_page = page_match.group()
        name_tokens = re.findall(r"[A-Za-z]+", hint.authors[0])
        slugs = []
        for value in (name_tokens[0] if name_tokens else "", name_tokens[-1] if name_tokens else ""):
            slug = value.casefold()
            if slug and slug not in slugs:
                slugs.append(slug)
        return [
            f"https://www.vldb.org/pvldb/vol{volume}/p{first_page}-{slug}.pdf"
            for slug in slugs
        ]


def _record_from_pdf(content: bytes, url: str, hint: PaperHint) -> PaperRecord | None:
    title = _pdf_info_value(content, "Title")
    if not title:
        return None
    author_text = _pdf_info_value(content, "Author") or ""
    authors = [Author(literal=name) for name in _split_authors(author_text)]
    text = _pdf_text(content)
    citation = re.search(
        r"(?s)(\d+)\s*\((\d+)\)\s*:\s*(\d+)\s*-\s*(\d+)\s*,\s*((?:19|20)\d{2})"
        r".{0,160}?doi\s*:\s*(10\.\d{4,9}/[^\s,;]+)",
        text,
        re.IGNORECASE,
    )
    if citation:
        volume, issue, first_page, last_page, year, doi = citation.groups()
        pages = f"{first_page}-{last_page}"
        doi = normalize_doi(doi.rstrip("."))
    else:
        volume_match = re.search(r"/vol(\d+)/p\d+", url, re.IGNORECASE)
        date = _pdf_info_value(content, "CreationDate") or ""
        year_match = re.search(r"(?:19|20)\d{2}", date)
        volume = volume_match.group(1) if volume_match else None
        issue = pages = doi = None
        year = year_match.group() if year_match else None
    return PaperRecord(
        title=title,
        authors=authors,
        year=int(year) if year else None,
        source="vldb",
        source_id=doi or url,
        source_url=url,
        retrieved_at=utc_now(),
        doi=doi,
        url=url,
        venue="Proceedings of the VLDB Endowment",
        publisher="VLDB Endowment",
        entry_type="article",
        volume=volume,
        issue=issue,
        pages=pages,
        extra={
            "official_pdf": url,
            "raw_payload_hash": hashlib.sha256(content).hexdigest(),
        },
    )


def _pdf_info_value(content: bytes, key: str) -> str | None:
    match = re.search(
        rb"/" + key.encode("ascii") + rb"\s*(\((?:\\.|[^\\)])*\)|<[^>]*>)",
        content,
        re.DOTALL,
    )
    if not match:
        return None
    value = match.group(1)
    if value.startswith(b"<"):
        raw = bytes.fromhex(value[1:-1].decode("ascii", errors="ignore"))
        if raw.startswith(b"\xfe\xff"):
            return raw[2:].decode("utf-16-be", errors="replace").strip()
        return raw.decode("utf-8", errors="replace").strip()
    return _decode_pdf_literal(value[1:-1]).strip() or None


def _decode_pdf_literal(value: bytes) -> str:
    def replace(match: re.Match[bytes]) -> bytes:
        token = match.group(1)
        escapes = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}
        if token in escapes:
            return escapes[token]
        if re.fullmatch(rb"[0-7]{1,3}", token):
            return bytes([int(token, 8)])
        return token

    decoded = re.sub(rb"\\([0-7]{1,3}|.)", replace, value)
    return decoded.decode("utf-8", errors="replace")


def _split_authors(value: str) -> list[str]:
    normalized = re.sub(r"\s+and\s+", ", ", value, flags=re.IGNORECASE)
    return [part.strip() for part in normalized.split(",") if part.strip()]


def _pdf_text(content: bytes) -> str:
    values: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", content, re.DOTALL):
        try:
            decoded = zlib.decompress(match.group(1))
        except zlib.error:
            continue
        for literal in re.findall(rb"\((?:\\.|[^\\)])*\)", decoded):
            values.append(_decode_pdf_literal(literal[1:-1]))
    return " ".join(values)
