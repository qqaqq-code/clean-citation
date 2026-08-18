from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import quote, urljoin

from ..models import Author, PaperHint, PaperRecord
from ..normalization import normalize_doi, normalize_text, title_similarity
from ..transport import HttpResponse, HttpTransport, HttpTransportError
from .base import (
    DEFAULT_USER_AGENT,
    MetadataSource,
    SourceBlockedError,
    UnsupportedLookupError,
    compact,
    utc_now,
)


class OfficialWebSource(MetadataSource):
    """Adapter for official static proceedings and publisher landing pages."""

    def __init__(
        self,
        name: str,
        transport: HttpTransport | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        super().__init__(transport=transport, user_agent=user_agent)
        self.name = name
        self._collection_cache: dict[str, _Page] = {}

    def search(self, hint: PaperHint, limit: int = 5) -> list[PaperRecord]:
        direct_url = self._direct_url(hint)
        if direct_url:
            return [self._record_from_detail(direct_url, hint)]

        if self.name == "aaai" and "AI MAGAZINE" in (hint.venue or "").upper():
            return self._search_aaai_magazine(hint)

        collection_urls = self._collection_urls(hint)
        if not collection_urls:
            raise UnsupportedLookupError(
                f"{self.name} needs an official URL/DOI or a supported venue+year route"
            )

        detail_candidates: list[tuple[float, str]] = []
        for collection_url in collection_urls:
            page = self._get_page(collection_url, cache_collection=True)
            # PMLR's root is a directory of volumes, so descend once into the
            # best venue/year volume before matching paper titles.
            if self.name == "pmlr" and collection_url.rstrip("/") == "https://proceedings.mlr.press":
                volume = _best_pmlr_volume_link(page, hint)
                if volume:
                    page = self._get_page(urljoin(collection_url, volume), cache_collection=True)
            if self.name == "jmlr" and collection_url.rstrip("/") == "https://www.jmlr.org/papers":
                volume = _best_jmlr_volume_link(page, hint)
                if volume:
                    page = self._get_page(urljoin(collection_url, volume), cache_collection=True)
            for anchor_text, href in _candidate_links(self.name, page):
                score = title_similarity(hint.title, anchor_text)
                if score >= 72:
                    detail_candidates.append((score, urljoin(page.url, href)))
            if detail_candidates:
                # URL patterns changed across proceedings generations. Once
                # the current official index contains the title, probing its
                # legacy alias only adds latency and duplicate evidence.
                break

        records: list[PaperRecord] = []
        seen: set[str] = set()
        for _, detail_url in sorted(detail_candidates, reverse=True):
            if detail_url in seen:
                continue
            seen.add(detail_url)
            try:
                record = self._record_from_detail(detail_url, hint)
            except (ValueError, UnsupportedLookupError):
                continue
            records.append(record)
            if len(records) >= limit:
                break
        return records

    def _search_aaai_magazine(self, hint: PaperHint) -> list[PaperRecord]:
        """Resolve AI Magazine papers through AAAI's official OJS archive."""
        archive_root = "https://ojs.aaai.org/aimagazine/index.php/aimagazine/issue/archive"
        issue_links: list[str] = []
        seen_archives: set[str] = set()
        archive_url: str | None = archive_root
        for _ in range(10):
            if not archive_url:
                break
            if archive_url in seen_archives:
                break
            seen_archives.add(archive_url)
            page = self._get_page(archive_url, cache_collection=True)
            matches = _aaai_magazine_issue_links(page, hint)
            if matches:
                issue_links.extend(matches)
                break
            archive_url = next(
                (
                    urljoin(page.url, href)
                    for text, href in page.links
                    if text.strip().casefold() == "next" and "/issue/archive/" in href
                ),
                None,
            )

        for issue_url in issue_links:
            issue_page = self._get_page(issue_url, cache_collection=True)
            detail_candidates = sorted(
                (
                    (title_similarity(hint.title, text), urljoin(issue_page.url, href))
                    for text, href in issue_page.links
                    if "/article/view/" in href
                ),
                reverse=True,
            )
            for score, detail_url in detail_candidates:
                if score >= 90:
                    return [self._record_from_detail(detail_url, hint)]
        return []

    def _direct_url(self, hint: PaperHint) -> str | None:
        if hint.official_url:
            return hint.official_url
        doi = normalize_doi(hint.doi)
        if not doi:
            return None
        if self.name == "ieee":
            return f"https://ieeexplore.ieee.org/document/{quote(doi, safe='')}"
        if self.name == "springer":
            return f"https://link.springer.com/article/{quote(doi, safe='/')}"
        # DOI redirection is object routing only; metadata still comes from the
        # final official publisher page, never from a DOI/Crossref record.
        return f"https://doi.org/{quote(doi, safe='/')}"

    def _collection_urls(self, hint: PaperHint) -> list[str]:
        if not hint.year:
            return []
        year = hint.year
        venue = (hint.venue or "").upper()
        if self.name == "neurips":
            return [
                f"https://proceedings.neurips.cc/paper_files/paper/{year}",
                f"https://proceedings.neurips.cc/paper/{year}",
            ]
        if self.name == "mlsys":
            return [
                f"https://proceedings.mlsys.org/paper_files/paper/{year}",
                f"https://proceedings.mlsys.org/paper/{year}",
            ]
        if self.name == "pmlr":
            return ["https://proceedings.mlr.press/"]
        if self.name == "cvf":
            return [f"https://openaccess.thecvf.com/{venue}{year}?day=all"]
        if self.name == "acl_anthology":
            slug = venue.casefold().replace(" ", "-")
            return [f"https://aclanthology.org/events/{slug}-{year}/"]
        if self.name == "ijcai":
            return [f"https://www.ijcai.org/proceedings/{year}/"]
        if self.name == "jmlr":
            return ["https://www.jmlr.org/papers/"]
        if self.name == "aaai":
            query = quote(hint.title)
            return [f"https://ojs.aaai.org/index.php/AAAI/search/search?query={query}"]
        if self.name == "usenix":
            slug = _usenix_slug(venue, year)
            return [f"https://www.usenix.org/conference/{slug}/technical-sessions"] if slug else []
        return []

    def _record_from_detail(self, url: str, hint: PaperHint) -> PaperRecord:
        page = self._get_page(url)
        bibtex = _extract_bibtex(page.raw_text)
        if not bibtex:
            bib_link = next(
                (
                    href
                    for text, href in page.links
                    if "bibtex" in text.casefold()
                    or "format=bibtex" in href.casefold()
                    or href.casefold().endswith(".bib")
                    or "/bibtex/" in href.casefold()
                ),
                None,
            )
            if bib_link and not (self.name == "aaai" and page.meta.get("citation_title")):
                try:
                    bib_response = self._get(
                        urljoin(page.url, bib_link), accept="text/plain, text/html"
                    )
                    bibtex = _extract_bibtex(
                        bib_response.content.decode("utf-8", errors="replace")
                    )
                except (HttpTransportError, SourceBlockedError):
                    # Citation meta tags on the same official article page are
                    # sufficient when an auxiliary export endpoint is offline.
                    bibtex = None

        fields, entry_type = _parse_bibtex(bibtex or "")
        meta = page.meta
        title = compact(fields.get("title") or _meta_one(meta, "citation_title") or _meta_one(meta, "dc.title"))
        if not title:
            raise UnsupportedLookupError(f"No official citation metadata found at {page.url}")
        authors = _bib_authors(fields.get("author") or "")
        if not authors:
            authors = [Author(literal=value) for value in meta.get("citation_author", []) if value]
        published_date = (
            _meta_one(meta, "citation_publication_date")
            or _meta_one(meta, "citation_date")
            or _meta_one(meta, "dc.date.issued")
            or _meta_one(meta, "dc.date")
        )
        year = _year(fields.get("year") or published_date)
        journal_title = fields.get("journal") or _meta_one(meta, "citation_journal_title")
        venue = compact(
            fields.get("booktitle")
            or journal_title
            or _meta_one(meta, "citation_conference_title")
            or _meta_one(meta, "citation_journal_title")
            or hint.venue
        )
        first_page = fields.get("pages") or _meta_one(meta, "citation_firstpage")
        last_page = _meta_one(meta, "citation_lastpage")
        pages = f"{first_page}-{last_page}" if first_page and last_page and last_page not in first_page else first_page
        doi = normalize_doi(fields.get("doi") or _meta_one(meta, "citation_doi") or hint.doi)
        record_url = _meta_one(meta, "citation_public_url") or page.url
        return PaperRecord(
            title=title,
            authors=authors,
            year=year,
            source=self.name,
            source_id=doi or record_url,
            source_url=page.url,
            retrieved_at=utc_now(),
            doi=doi,
            url=record_url,
            venue=venue,
            publisher=compact(
                fields.get("publisher")
                or _meta_one(meta, "citation_publisher")
                or (
                    "Association for the Advancement of Artificial Intelligence"
                    if self.name == "aaai"
                    else None
                )
            ),
            entry_type=entry_type or (
                "journal-article" if journal_title else "proceedings-article" if venue else None
            ),
            volume=compact(fields.get("volume") or _meta_one(meta, "citation_volume")),
            issue=compact(fields.get("number") or fields.get("issue") or _meta_one(meta, "citation_issue")),
            pages=compact(pages),
            published=compact(published_date),
            extra={"raw_bibtex": bibtex, "official_page": page.url},
        )

    def _get_page(self, url: str, cache_collection: bool = False) -> "_Page":
        if cache_collection and url in self._collection_cache:
            return self._collection_cache[url]
        response = self._get(url, accept="text/html, application/xhtml+xml")
        raw = response.content.decode("utf-8", errors="replace")
        parser = _CitationHtmlParser()
        parser.feed(raw)
        page = _Page(url=response.url, meta=parser.meta, links=parser.links, raw_text=html.unescape(raw))
        if cache_collection:
            self._collection_cache[url] = page
        return page

    def _get(self, url: str, accept: str) -> HttpResponse:
        response = self.transport.get(url, headers={"Accept": accept})
        if response.status_code in {401, 403}:
            raise SourceBlockedError(f"{self.name} returned HTTP {response.status_code}")
        response.raise_for_status()
        preview = response.content[:8192].decode("utf-8", errors="ignore").casefold()
        if any(
            marker in preview
            for marker in (
                "verifying your browser",
                "enable javascript and cookies to continue",
                "challenge-platform",
            )
        ):
            raise SourceBlockedError(
                f"{self.name} returned an interactive browser-verification page"
            )
        return response


class _CitationHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "meta":
            key = (values.get("name") or values.get("property") or "").casefold()
            content = values.get("content", "").strip()
            if key and content:
                self.meta.setdefault(key, []).append(content)
        elif tag.casefold() == "a" and values.get("href"):
            self._href = values["href"]
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._href is not None:
            self.links.append((compact(" ".join(self._anchor_text)) or "", self._href))
            self._href = None
            self._anchor_text = []


class _Page:
    def __init__(self, url: str, meta: dict[str, list[str]], links: list[tuple[str, str]], raw_text: str) -> None:
        self.url = url
        self.meta = meta
        self.links = links
        self.raw_text = raw_text


def _aaai_magazine_issue_links(page: _Page, hint: PaperHint) -> list[str]:
    if not hint.year:
        return []
    matches = [
        (text, urljoin(page.url, href))
        for text, href in page.links
        if "/issue/view/" in href and str(hint.year) in text
    ]
    season = {
        "1": "spring",
        "2": "summer",
        "3": "fall",
        "4": "winter",
    }.get(str(hint.issue or ""))
    if season:
        matches.sort(key=lambda item: season not in item[0].casefold())
    return [url for _, url in matches]


def _best_pmlr_volume_link(page: _Page, hint: PaperHint) -> str | None:
    venue = normalize_text(hint.venue or "")
    venue_terms = {
        "icml": ("icml", "international conference on machine learning"),
        "aistats": ("aistats", "artificial intelligence and statistics"),
        "uai": ("uai", "uncertainty in artificial intelligence"),
        "corl": ("corl", "conference on robot learning"),
    }.get(venue, (venue,))
    year = str(hint.year or "")
    ranked: list[tuple[int, str]] = []
    for match in re.finditer(
        r"(?is)<li[^>]*>\s*<a[^>]+href=[\"']([^\"']*v\d+/?)[\"'][^>]*>.*?</a>(.*?)</li>",
        page.raw_text,
    ):
        href, text = match.group(1), _plain_text(match.group(2))
        folded = normalize_text(text)
        score = (3 if any(term and term in folded for term in venue_terms) else 0) + (
            2 if year and year in text else 0
        )
        if score >= 4:
            ranked.append((score, href))
    return max(ranked, default=(0, ""))[1] or None


def _best_jmlr_volume_link(page: _Page, hint: PaperHint) -> str | None:
    year = str(hint.year or "")
    if not year:
        return None
    for match in re.finditer(
        r"(?is)<a[^>]+href=[\"']([^\"']*/?v\d+/?)[\"'][^>]*>.*?Volume\s+\d+.*?</a>\s*([^<]*)",
        page.raw_text,
    ):
        if year in html.unescape(match.group(2)):
            return match.group(1)
    return None


def _candidate_links(source: str, page: _Page) -> list[tuple[str, str]]:
    links = list(page.links)
    if source == "pmlr":
        # PMLR keeps the paper title in a sibling paragraph; the "abs"
        # anchor itself does not contain the title used for matching.
        for match in re.finditer(
            r"(?is)<p[^>]+class=[\"']title[\"'][^>]*>(.*?)</p>.*?"
            r"<a[^>]+href=[\"']([^\"']+\.html)[\"'][^>]*>\s*abs\s*</a>",
            page.raw_text,
        ):
            links.append((_plain_text(match.group(1)), match.group(2)))
    elif source == "ijcai":
        for block in re.findall(r"(?is)<div id=[\"']paper\d+[\"'][^>]*>(.*?)</div>\s*</div>", page.raw_text):
            title_match = re.search(r"(?is)<div class=[\"']title[\"']>(.*?)</div>", block)
            detail_match = re.search(
                r"(?is)<a[^>]+href=[\"']([^\"']+/proceedings/\d+/\d+|/proceedings/\d+/\d+)[\"'][^>]*>\s*Details",
                block,
            )
            if title_match and detail_match:
                links.append((_plain_text(title_match.group(1)), detail_match.group(1)))
    elif source == "jmlr":
        for match in re.finditer(
            r"(?is)<dt>(.*?)</dt>.*?<a[^>]+href=[\"']([^\"']+\.html)[\"'][^>]*>\s*abs\s*</a>",
            page.raw_text,
        ):
            links.append((_plain_text(match.group(1)), match.group(2)))
    return links


def _plain_text(value: str) -> str:
    return compact(html.unescape(re.sub(r"(?is)<[^>]+>", " ", value))) or ""


def _extract_bibtex(text: str) -> str | None:
    start = re.search(r"@[A-Za-z]+\s*\{", text)
    if not start:
        return None
    depth = 0
    for index in range(start.end() - 1, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return compact(text[start.start() : index + 1])
    return None


def _parse_bibtex(value: str) -> tuple[dict[str, str], str | None]:
    if not value:
        return {}, None
    match = re.match(r"\s*@([A-Za-z]+)\s*\{[^,]*,", value)
    entry_type = match.group(1).casefold() if match else None
    body = value[match.end() :] if match else value
    fields: dict[str, str] = {}
    field_pattern = re.compile(
        r"(?ms)([A-Za-z][A-Za-z0-9_-]*)\s*=\s*(?:\{((?:[^{}]|\{[^{}]*\})*)\}|\"([^\"]*)\")\s*,?"
    )
    for item in field_pattern.finditer(body):
        raw = item.group(2) if item.group(2) is not None else item.group(3)
        fields[item.group(1).casefold()] = compact(_strip_tex(raw or "")) or ""
    return fields, entry_type


def _strip_tex(value: str) -> str:
    value = value.replace("--", "-")
    value = re.sub(r"\\[A-Za-z]+\s*\{([^{}]*)\}", r"\1", value)
    return value.replace("{", "").replace("}", "")


def _bib_authors(value: str) -> list[Author]:
    authors: list[Author] = []
    for item in re.split(r"\s+and\s+", value):
        name = compact(item)
        if not name:
            continue
        if "," in name:
            family, given = [part.strip() for part in name.split(",", 1)]
            authors.append(Author(given=given, family=family))
        else:
            authors.append(Author(literal=name))
    return authors


def _meta_one(meta: dict[str, list[str]], key: str) -> str | None:
    values = meta.get(key.casefold()) or []
    return values[0] if values else None


def _year(value: str | None) -> int | None:
    match = re.search(r"(?:19|20)\d{2}", value or "")
    return int(match.group()) if match else None


def _usenix_slug(venue: str, year: int) -> str | None:
    short = str(year)[-2:]
    if "SECURITY" in venue:
        return f"usenixsecurity{short}"
    for name in ("OSDI", "NSDI"):
        if name in venue:
            return f"{name.casefold()}{short}"
    if "ATC" in venue:
        return f"atc{short}"
    return None
