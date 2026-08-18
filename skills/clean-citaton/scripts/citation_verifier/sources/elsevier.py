from __future__ import annotations

from typing import Any

from ..models import Author, PaperHint, PaperRecord
from ..normalization import normalize_doi
from .base import DEFAULT_USER_AGENT, MetadataSource, SourceBlockedError, compact, utc_now


class ElsevierSource(MetadataSource):
    """Official ScienceDirect Article Metadata API adapter."""

    name = "elsevier"
    base_url = "https://api.elsevier.com/content/metadata/article"

    def __init__(self, api_key: str, transport=None, user_agent: str = DEFAULT_USER_AGENT) -> None:
        super().__init__(transport=transport, user_agent=user_agent)
        self.api_key = api_key

    def search(self, hint: PaperHint, limit: int = 5) -> list[PaperRecord]:
        query = f'doi("{normalize_doi(hint.doi)}")' if hint.doi else f'title("{hint.title.replace(chr(34), " ")}")'
        response = self.transport.get(
            self.base_url,
            params={"query": query, "count": max(1, min(limit, 25)), "view": "STANDARD"},
            headers={"Accept": "application/json", "X-ELS-APIKey": self.api_key},
        )
        if response.status_code in {401, 403}:
            raise SourceBlockedError(f"elsevier returned HTTP {response.status_code}")
        response.raise_for_status()
        payload = response.json()
        search_results = payload.get("search-results") if isinstance(payload, dict) else None
        entries = search_results.get("entry") if isinstance(search_results, dict) else []
        if isinstance(entries, dict):
            entries = [entries]
        return [record for item in entries or [] if isinstance(item, dict) if (record := _parse_record(item))]


def _parse_record(item: dict[str, Any]) -> PaperRecord | None:
    title = compact(item.get("dc:title") or item.get("title"))
    if not title:
        return None
    authors = _authors(item)
    cover_date = compact(item.get("prism:coverDate") or item.get("prism:coverDisplayDate"))
    year = None
    if cover_date and len(cover_date) >= 4 and cover_date[:4].isdigit():
        year = int(cover_date[:4])
    doi = normalize_doi(item.get("prism:doi") or item.get("doi") or item.get("dc:identifier"))
    links = item.get("link") or []
    if isinstance(links, dict):
        links = [links]
    article_url = next(
        (
            link.get("@href")
            for link in links
            if isinstance(link, dict) and link.get("@ref") in {"scidir", "self"} and link.get("@href")
        ),
        None,
    )
    article_url = article_url or item.get("prism:url") or (f"https://doi.org/{doi}" if doi else None)
    first_page = compact(item.get("prism:startingPage"))
    last_page = compact(item.get("prism:endingPage"))
    page_range = compact(item.get("prism:pageRange"))
    pages = page_range or (
        f"{first_page}-{last_page}" if first_page and last_page and first_page != last_page else first_page
    )
    source_id = compact(item.get("pii") or item.get("dc:identifier")) or doi or str(article_url)
    return PaperRecord(
        title=title,
        authors=authors,
        year=year,
        source="elsevier",
        source_id=source_id,
        source_url=ElsevierSource.base_url,
        retrieved_at=utc_now(),
        doi=doi,
        url=str(article_url) if article_url else None,
        venue=compact(item.get("prism:publicationName")),
        publisher="Elsevier",
        entry_type="article",
        volume=compact(item.get("prism:volume")),
        issue=compact(item.get("prism:issueIdentifier") or item.get("prism:number")),
        pages=pages,
        published=cover_date,
    )


def _authors(item: dict[str, Any]) -> list[Author]:
    container = item.get("authors") or {}
    values = container.get("author") if isinstance(container, dict) else []
    if isinstance(values, dict):
        values = [values]
    authors: list[Author] = []
    for value in values or []:
        if not isinstance(value, dict):
            continue
        literal = compact(value.get("$"))
        given = compact(value.get("given-name")) or ""
        family = compact(value.get("surname")) or ""
        authors.append(Author(given=given, family=family, literal=literal or ""))
    if not authors and item.get("dc:creator"):
        authors.append(Author(literal=str(item["dc:creator"])))
    return authors
