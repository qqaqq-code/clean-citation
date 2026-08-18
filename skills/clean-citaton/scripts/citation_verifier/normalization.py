from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .models import Author, PaperHint, PaperRecord, ScoredCandidate


_NON_WORD = re.compile(r"[^a-z0-9]+")
_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_ARXIV_PREFIX = re.compile(r"^(?:arxiv:|https?://arxiv\.org/(?:abs|pdf)/)", re.IGNORECASE)
_ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(_NON_WORD.sub(" ", value).split())


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    return _DOI_PREFIX.sub("", value.strip()).rstrip(". ").lower() or None


def normalize_arxiv_id(value: str | None) -> str | None:
    if not value:
        return None
    clean = _ARXIV_PREFIX.sub("", value.strip()).removesuffix(".pdf")
    return _ARXIV_VERSION.sub("", clean).strip() or None


def title_similarity(left: str, right: str) -> float:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 100.0
    direct = SequenceMatcher(None, a, b).ratio()
    token_sort = SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()
    a_tokens, b_tokens = set(a.split()), set(b.split())
    jaccard = len(a_tokens & b_tokens) / max(1, len(a_tokens | b_tokens))
    return round(100 * (0.85 * max(direct, token_sort) + 0.15 * jaccard), 4)


def _family_token(name: str) -> str:
    normalized = normalize_text(name)
    return normalized.split()[-1] if normalized else ""


def author_similarity(hint_authors: list[str], record_authors: list[Author]) -> float | None:
    if not hint_authors or not record_authors:
        return None
    left = {_family_token(name) for name in hint_authors} - {""}
    right = {_family_token(author.display_name) for author in record_authors} - {""}
    if not left or not right:
        return None
    # A short citation often supplies only the first author. Measure how many
    # supplied names are confirmed, rather than penalizing a complete API
    # record for containing additional co-authors.
    return 100.0 * len(left & right) / len(left)


def score_candidate(hint: PaperHint, record: PaperRecord) -> ScoredCandidate:
    title_score = title_similarity(hint.title, record.title)
    author_score = author_similarity(hint.authors, record.authors)
    year_score: float | None = None
    if hint.year is not None and record.year is not None:
        delta = abs(hint.year - record.year)
        year_score = 100.0 if delta == 0 else 60.0 if delta == 1 else 0.0

    doi_match = bool(hint.doi and normalize_doi(hint.doi) == normalize_doi(record.doi))
    arxiv_match = bool(
        hint.arxiv_id and normalize_arxiv_id(hint.arxiv_id) == normalize_arxiv_id(record.arxiv_id)
    )
    identifier_match = doi_match or arxiv_match

    weighted = [(title_score, 0.82)]
    if author_score is not None:
        weighted.append((author_score, 0.13))
    if year_score is not None:
        weighted.append((year_score, 0.05))
    score = sum(value * weight for value, weight in weighted) / sum(weight for _, weight in weighted)
    if identifier_match:
        score = max(score, 99.0)
    else:
        # An exact/common title must not override explicit contradictory hints.
        if author_score == 0.0:
            score = min(score, 82.0)
        if hint.year is not None and record.year is not None and abs(hint.year - record.year) >= 4:
            score = min(score, 84.0)
    return ScoredCandidate(
        record=record,
        score=round(score, 4),
        title_score=title_score,
        author_score=author_score,
        year_score=year_score,
        identifier_match=identifier_match,
    )


def work_key(record: PaperRecord) -> str:
    doi = normalize_doi(record.doi)
    if doi:
        return f"doi:{doi}"
    arxiv_id = normalize_arxiv_id(record.arxiv_id)
    if arxiv_id:
        return f"arxiv:{arxiv_id.lower()}"
    return f"title:{normalize_text(record.title)}:{record.year or ''}"
