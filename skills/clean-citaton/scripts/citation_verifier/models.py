from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Author:
    given: str = ""
    family: str = ""
    literal: str = ""

    @property
    def display_name(self) -> str:
        if self.literal:
            return self.literal
        return " ".join(part for part in (self.given, self.family) if part).strip()

    @classmethod
    def from_value(cls, value: Any) -> "Author":
        if isinstance(value, str):
            return cls(literal=value.strip())
        if isinstance(value, dict):
            return cls(
                given=str(value.get("given", "") or "").strip(),
                family=str(value.get("family", "") or "").strip(),
                literal=str(value.get("literal", "") or value.get("name", "") or "").strip(),
            )
        return cls(literal=str(value).strip())


@dataclass(slots=True)
class PaperHint:
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    article_number: str | None = None
    track: str | None = None
    target: str = "best_formal_available"
    official_url: str | None = None
    original_text: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PaperHint":
        raw_year = data.get("year")
        try:
            year = int(raw_year) if raw_year not in (None, "") else None
        except (TypeError, ValueError):
            year = None
        authors = data.get("authors") or []
        if isinstance(authors, str):
            authors = [authors]
        return cls(
            title=str(data.get("title", "") or "").strip(),
            authors=[str(author).strip() for author in authors if str(author).strip()],
            year=year,
            doi=_clean_optional(data.get("doi")),
            arxiv_id=_clean_optional(data.get("arxiv_id")),
            venue=_clean_optional(data.get("venue") or data.get("container_title")),
            volume=_clean_optional(data.get("volume")),
            issue=_clean_optional(data.get("issue") or data.get("number")),
            pages=_clean_optional(data.get("pages") or data.get("page")),
            article_number=_clean_optional(data.get("article_number")),
            track=_clean_optional(data.get("track")),
            target=str(data.get("target") or "best_formal_available").strip(),
            official_url=_clean_optional(data.get("official_url") or data.get("url")),
            original_text=_clean_optional(data.get("original_text")),
        )


@dataclass(slots=True)
class PaperRecord:
    title: str
    authors: list[Author]
    year: int | None
    source: str
    source_id: str
    source_url: str
    retrieved_at: str
    doi: str | None = None
    arxiv_id: str | None = None
    openreview_id: str | None = None
    url: str | None = None
    abstract: str | None = None
    venue: str | None = None
    publisher: str | None = None
    entry_type: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    published: str | None = None
    categories: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ScoredCandidate:
    record: PaperRecord
    score: float
    title_score: float
    author_score: float | None = None
    year_score: float | None = None
    identifier_match: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "title_score": round(self.title_score, 2),
            "author_score": None if self.author_score is None else round(self.author_score, 2),
            "year_score": None if self.year_score is None else round(self.year_score, 2),
            "identifier_match": self.identifier_match,
            "record": self.record.to_dict(),
        }


@dataclass(slots=True)
class VerificationResult:
    hint: PaperHint
    status: str
    reason: str
    record: PaperRecord | None = None
    score: float | None = None
    evidence: list[ScoredCandidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    source_failures: list[str] = field(default_factory=list)
    authority_level: str | None = None
    source_role: str | None = None
    required_credential: str | None = None

    @property
    def is_citable(self) -> bool:
        return self.status in {
            "FINAL",
            "PROVISIONAL_OPENREVIEW",
            "OPENREVIEW_SUBMISSION",
            "REJECTED_OPENREVIEW",
            "PREPRINT_ARXIV",
        } and self.record is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "hint": asdict(self.hint),
            "status": self.status,
            "reason": self.reason,
            "score": None if self.score is None else round(self.score, 2),
            "record": self.record.to_dict() if self.record else None,
            "evidence": [item.to_dict() for item in self.evidence],
            "errors": self.errors,
            "source_failures": self.source_failures,
            "authority_level": self.authority_level,
            "source_role": self.source_role,
            "required_credential": self.required_credential,
        }


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
