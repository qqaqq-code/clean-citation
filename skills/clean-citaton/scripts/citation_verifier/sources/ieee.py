from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..models import Author, PaperHint, PaperRecord
from ..normalization import normalize_doi
from ..transport import HttpTransport
from .base import DEFAULT_USER_AGENT, MetadataSource, compact, utc_now


class IeeeSource(MetadataSource):
    name = "ieee"
    base_url = "https://ieeexploreapi.ieee.org/api/v1/search/articles"

    def __init__(
        self,
        api_key: str,
        transport: HttpTransport | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        if not api_key:
            raise ValueError("IEEE API key is required")
        super().__init__(transport=transport, user_agent=user_agent)
        self.api_key = api_key
        self._prefetched: dict[str, PaperRecord] = {}

    def prefetch_dois(self, hints: list[PaperHint], batch_size: int = 25) -> None:
        dois = list(
            dict.fromkeys(
                normalize_doi(hint.doi)
                for hint in hints
                if hint.doi and normalize_doi(hint.doi)
            )
        )
        for start in range(0, len(dois), max(1, min(batch_size, 25))):
            chunk = dois[start : start + max(1, min(batch_size, 25))]
            encoded = quote(",".join(chunk), safe=",")
            response = self.transport.get(
                f"https://ieeexploreapi.ieee.org/api/v1/articles/doi/{encoded}",
                params={"apikey": self.api_key, "format": "json"},
            )
            response.raise_for_status()
            for item in response.json().get("articles", []):
                record = self._parse(item)
                if record.doi:
                    self._prefetched[record.doi.casefold()] = record

    def search(self, hint: PaperHint, limit: int = 5) -> list[PaperRecord]:
        normalized_doi = normalize_doi(hint.doi) if hint.doi else None
        if normalized_doi and normalized_doi.casefold() in self._prefetched:
            return [self._prefetched[normalized_doi.casefold()]]
        params: dict[str, Any] = {
            "apikey": self.api_key,
            "format": "json",
            "max_records": max(1, min(limit, 200)),
            "start_record": 1,
        }
        if hint.doi:
            params["doi"] = normalize_doi(hint.doi)
        else:
            params["article_title"] = hint.title
        response = self.transport.get(self.base_url, params=params)
        response.raise_for_status()
        return [self._parse(item) for item in response.json().get("articles", []) if item.get("title")]

    def _parse(self, item: dict[str, Any]) -> PaperRecord:
        doi = normalize_doi(item.get("doi"))
        article_number = str(item.get("article_number") or "")
        author_items = (item.get("authors") or {}).get("authors") or []
        year = item.get("publication_year")
        try:
            parsed_year = int(year) if year else None
        except (TypeError, ValueError):
            parsed_year = None
        start_page, end_page = compact(item.get("start_page")), compact(item.get("end_page"))
        pages = f"{start_page}-{end_page}" if start_page and end_page else start_page or end_page
        return PaperRecord(
            title=compact(item.get("title")) or "",
            authors=[Author(literal=compact(author.get("full_name")) or "") for author in author_items],
            year=parsed_year,
            source=self.name,
            source_id=article_number or doi or str(item.get("html_url", "")),
            source_url=(
                f"https://ieeexploreapi.ieee.org/api/v1/articles/{article_number}"
                if article_number
                else self.base_url
            ),
            retrieved_at=utc_now(),
            doi=doi,
            url=item.get("html_url") or item.get("pdf_url") or (f"https://doi.org/{doi}" if doi else None),
            abstract=compact(item.get("abstract")),
            venue=compact(item.get("publication_title")),
            publisher="IEEE",
            entry_type=compact(item.get("content_type")),
            volume=compact(item.get("volume")),
            issue=compact(item.get("issue")),
            pages=pages,
            published=compact(item.get("publication_date")),
            extra={"article_number": article_number or None, "issn": item.get("issn")},
        )
