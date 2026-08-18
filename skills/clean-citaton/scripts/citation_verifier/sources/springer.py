from __future__ import annotations

from typing import Any

from ..models import Author, PaperHint, PaperRecord
from ..normalization import normalize_doi
from ..transport import HttpTransport
from .base import DEFAULT_USER_AGENT, MetadataSource, compact, utc_now


class SpringerSource(MetadataSource):
    name = "springer"
    base_url = "https://api.springernature.com/meta/v2/json"

    def __init__(
        self,
        api_key: str,
        transport: HttpTransport | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        if not api_key:
            raise ValueError("Springer Nature API key is required")
        super().__init__(transport=transport, user_agent=user_agent)
        self.api_key = api_key

    def search(self, hint: PaperHint, limit: int = 5) -> list[PaperRecord]:
        doi = normalize_doi(hint.doi)
        query = f"doi:{doi}" if doi else f'title:"{hint.title.replace(chr(34), " ")}"'
        response = self.transport.get(
            self.base_url,
            params={
                "api_key": self.api_key,
                "q": query,
                "s": 1,
                "p": max(1, min(limit, 20)),
            },
        )
        response.raise_for_status()
        payload = response.json()
        records = payload.get("records") or payload.get("result") or []
        return [self._parse(item) for item in records if isinstance(item, dict) and item.get("title")]

    def _parse(self, item: dict[str, Any]) -> PaperRecord:
        doi = normalize_doi(item.get("doi"))
        publication_date = compact(item.get("publicationDate") or item.get("onlineDate"))
        year = None
        if publication_date and publication_date[:4].isdigit():
            year = int(publication_date[:4])
        creators = item.get("creators") or item.get("authors") or []
        authors: list[Author] = []
        for creator in creators:
            if isinstance(creator, dict):
                authors.append(Author(literal=compact(creator.get("creator") or creator.get("name")) or ""))
            elif creator:
                authors.append(Author(literal=compact(creator) or ""))
        url = item.get("url")
        if isinstance(url, list):
            url = next(
                (
                    entry.get("value") or entry.get("url")
                    for entry in url
                    if isinstance(entry, dict) and (entry.get("value") or entry.get("url"))
                ),
                None,
            )
        return PaperRecord(
            title=compact(item.get("title")) or "",
            authors=authors,
            year=year,
            source=self.name,
            source_id=doi or str(item.get("identifier") or url or ""),
            source_url=self.base_url,
            retrieved_at=utc_now(),
            doi=doi,
            url=str(url) if url else (f"https://doi.org/{doi}" if doi else None),
            abstract=compact(item.get("abstract")),
            venue=compact(item.get("publicationName") or item.get("journalTitle") or item.get("bookTitle")),
            publisher=compact(item.get("publisher") or "Springer Nature"),
            entry_type=compact(item.get("contentType") or item.get("genre")),
            volume=compact(item.get("volume")),
            issue=compact(item.get("number") or item.get("issue")),
            pages=compact(item.get("startingPage") or item.get("pages")),
            published=publication_date,
            extra={"copyright": item.get("copyright")},
        )
