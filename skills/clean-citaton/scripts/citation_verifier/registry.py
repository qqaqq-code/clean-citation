from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .models import PaperHint
from .normalization import normalize_doi


KNOWN_ADAPTERS = {
    "neurips",
    "pmlr",
    "acl_anthology",
    "ieee",
    "cvf",
    "usenix",
    "springer",
    "aaai",
    "ijcai",
    "jmlr",
    "mlsys",
    "vldb",
    "elsevier",
    "openreview",
    "arxiv",
}

@dataclass(frozen=True, slots=True)
class SourceRoute:
    adapter: str
    role: str = "official_publication"
    credential: str | None = None
    optional_when_missing_credential: bool = False


@dataclass(frozen=True, slots=True)
class CitationRoute:
    venue: str | None
    official: tuple[SourceRoute, ...] = ()
    review: SourceRoute | None = None
    fallback: tuple[str, ...] = ("arxiv",)
    inferred_by: str = "none"
    unsupported_official: bool = False


@dataclass(slots=True)
class SourceRegistry:
    venues: list[dict] = field(default_factory=list)
    domain_adapters: dict[str, str] = field(default_factory=dict)
    doi_prefix_adapters: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, user_config: str | Path | None = None) -> "SourceRegistry":
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            bundled = Path(sys._MEIPASS) / "references" / "source-registry.json"
        else:
            bundled = Path(__file__).resolve().parents[2] / "references" / "source-registry.json"
        data = _read_json(bundled)
        if user_config:
            overlay = _read_json(Path(user_config))
            data = _merge_registry(data, overlay)
        _validate_adapters(data)
        return cls(
            venues=list(data.get("venues") or []),
            domain_adapters={str(k).casefold(): str(v) for k, v in (data.get("domains") or {}).items()},
            doi_prefix_adapters={str(k).casefold(): str(v) for k, v in (data.get("doi_prefixes") or {}).items()},
        )

    def resolve(self, hint: PaperHint) -> CitationRoute:
        if hint.target.startswith("exact_arxiv") or hint.arxiv_id and hint.target == "arxiv":
            return CitationRoute(None, fallback=("arxiv",), inferred_by="explicit_arxiv")

        official_url = hint.official_url or ""
        if official_url:
            adapter = self._adapter_for_domain(urlsplit(official_url).hostname or "")
            if adapter:
                if adapter == "openreview" and hint.venue:
                    venue, _ = self._infer_venue(hint)
                    entry = next(
                        (item for item in self.venues if item.get("venue") == venue),
                        None,
                    )
                    if entry:
                        official = tuple(
                            _source_route(item) for item in entry.get("official") or []
                        )
                        if official and not any(
                            item.adapter == "openreview" for item in official
                        ):
                            # An OpenReview URL attached to an IEEE/publisher
                            # citation identifies the review fallback; it must
                            # not replace the venue's formal L1 route.
                            return CitationRoute(
                                venue,
                                official=official,
                                review=SourceRoute("openreview", role="review_platform"),
                                fallback=_fallback_chain(entry.get("fallback")),
                                inferred_by="review_url",
                            )
                return CitationRoute(
                    hint.venue,
                    official=(SourceRoute(adapter),),
                    review=SourceRoute("openreview", role="review_platform"),
                    inferred_by="official_url",
                )

        doi = normalize_doi(hint.doi)
        if doi:
            adapter = self._adapter_for_doi(doi)
            if adapter:
                return CitationRoute(
                    hint.venue,
                    official=(SourceRoute(adapter),),
                    review=SourceRoute("openreview", role="review_platform"),
                    inferred_by="doi_prefix",
                )

        venue, inferred_by = self._infer_venue(hint)
        if venue:
            entry = next((item for item in self.venues if item.get("venue") == venue), None)
            if entry:
                official = tuple(_source_route(item) for item in entry.get("official") or [])
                review_data = entry.get("review")
                if review_data:
                    review = _source_route(review_data)
                elif any(item.adapter == "openreview" for item in official):
                    review = None
                else:
                    review = SourceRoute("openreview", role="review_platform")
                return CitationRoute(
                    venue=venue,
                    official=official,
                    review=review,
                    fallback=_fallback_chain(entry.get("fallback")),
                    inferred_by=inferred_by,
                )

        # An unmapped venue still gets the same explicit lower-authority
        # fallback chain. OpenReview/arXiv results remain visibly lower-authority
        # and never masquerade as the venue's formal publication record.
        if hint.venue:
            return CitationRoute(
                venue=hint.venue,
                review=SourceRoute("openreview", role="review_platform"),
                fallback=("arxiv",),
                inferred_by="explicit_unmapped_venue",
            )
        return CitationRoute(
            venue=None,
            review=SourceRoute("openreview", role="review_platform"),
            inferred_by="no_venue_hint",
        )

    def _adapter_for_domain(self, hostname: str) -> str | None:
        host = hostname.casefold().removeprefix("www.")
        for domain, adapter in self.domain_adapters.items():
            normalized = domain.removeprefix("www.")
            if host == normalized or host.endswith("." + normalized):
                return adapter
        return None

    def _adapter_for_doi(self, doi: str) -> str | None:
        lowered = doi.casefold()
        for prefix, adapter in sorted(self.doi_prefix_adapters.items(), key=lambda item: len(item[0]), reverse=True):
            if lowered.startswith(prefix):
                return adapter
        return None

    def _infer_venue(self, hint: PaperHint) -> tuple[str | None, str]:
        explicit = hint.venue or ""
        haystacks = [(explicit, "explicit_venue"), (hint.original_text or "", "original_text")]
        aliases: list[tuple[str, str]] = []
        for entry in self.venues:
            canonical = str(entry.get("venue") or "")
            for alias in [canonical, *(entry.get("aliases") or [])]:
                aliases.append((str(alias), canonical))
        aliases.sort(key=lambda pair: len(pair[0]), reverse=True)
        for text, method in haystacks:
            folded = text.casefold()
            for alias, canonical in aliases:
                pattern = rf"(?<![\w]){re.escape(alias.casefold())}(?![\w])"
                if re.search(pattern, folded):
                    return canonical, method
        return None, "none"


def _source_route(data: dict) -> SourceRoute:
    return SourceRoute(
        adapter=str(data["adapter"]),
        role=str(data.get("role") or "official_publication"),
        credential=data.get("credential"),
        optional_when_missing_credential=bool(data.get("optional_when_missing_credential", False)),
    )


def _fallback_chain(value: object) -> tuple[str, ...]:
    configured = [str(item) for item in (value or ["arxiv"])]
    return tuple(dict.fromkeys(configured))


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Source registry must be a JSON object: {path}")
    return value


def _merge_registry(base: dict, overlay: dict) -> dict:
    merged = dict(base)
    merged["domains"] = {**(base.get("domains") or {}), **(overlay.get("domains") or {})}
    merged["doi_prefixes"] = {
        **(base.get("doi_prefixes") or {}),
        **(overlay.get("doi_prefixes") or {}),
    }
    by_venue = {item["venue"]: dict(item) for item in base.get("venues") or []}
    for item in overlay.get("venues") or []:
        if not isinstance(item, dict) or not item.get("venue"):
            raise ValueError("Every custom venue route needs a non-empty 'venue'")
        by_venue[str(item["venue"])] = dict(item)
    merged["venues"] = list(by_venue.values())
    return merged


def _validate_adapters(data: dict) -> None:
    used: set[str] = set((data.get("domains") or {}).values())
    used.update((data.get("doi_prefixes") or {}).values())
    for venue in data.get("venues") or []:
        used.update(str(item.get("adapter")) for item in venue.get("official") or [])
        if venue.get("review"):
            used.add(str(venue["review"].get("adapter")))
        used.update(str(item) for item in venue.get("fallback") or [])
    unknown = sorted(used - KNOWN_ADAPTERS)
    if unknown:
        raise ValueError(
            "Registry refers to unsupported adapter(s): "
            + ", ".join(unknown)
            + ". Configure a bundled adapter instead of modifying runtime Python."
        )
