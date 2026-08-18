from __future__ import annotations

from collections.abc import Callable

from .models import PaperHint, ScoredCandidate, VerificationResult
from .normalization import score_candidate, work_key
from .registry import SourceRegistry, SourceRoute
from .sources.base import MetadataSource, SourceBlockedError, UnsupportedLookupError


ProgressCallback = Callable[[int, int, str, VerificationResult | None], None]


class CitationVerifier:
    """Resolve one canonical record through L1 -> L2 -> L3 routing."""

    def __init__(
        self,
        sources: list[MetadataSource],
        registry: SourceRegistry | None = None,
        missing_credentials: dict[str, str] | None = None,
        threshold: float = 86.0,
        ambiguity_margin: float = 2.5,
        candidate_limit: int = 5,
    ) -> None:
        if not sources:
            raise ValueError("At least one metadata source must be enabled")
        self.sources = {source.name: source for source in sources}
        self.registry = registry or SourceRegistry.load()
        self.missing_credentials = missing_credentials or {}
        self.threshold = threshold
        self.ambiguity_margin = ambiguity_margin
        self.candidate_limit = candidate_limit

    def close(self) -> None:
        for source in self.sources.values():
            source.close()

    def verify(self, hint: PaperHint) -> VerificationResult:
        first = self._verify_without_arxiv(hint)
        if not first.status.startswith("_PENDING"):
            return first
        return self._verify_arxiv(hint, first)

    def verify_many(
        self,
        hints: list[PaperHint],
        progress: ProgressCallback | None = None,
    ) -> list[VerificationResult]:
        total = len(hints)
        provisional: list[VerificationResult] = []
        for index, hint in enumerate(hints):
            if progress:
                progress(index, total, "official", None)
            result = self._verify_without_arxiv(hint)
            provisional.append(result)
            if not result.status.startswith("_PENDING") and progress:
                progress(index, total, "complete", result)

        pending_hints = [
            hint for hint, result in zip(hints, provisional) if result.status.startswith("_PENDING")
        ]
        arxiv = self.sources.get("arxiv")
        if arxiv and hasattr(arxiv, "prefetch_ids"):
            try:
                arxiv.prefetch_ids(pending_hints)  # type: ignore[attr-defined]
            except Exception:
                # Per-item lookups below retain actionable source errors.
                pass

        output: list[VerificationResult] = []
        for index, (hint, result) in enumerate(zip(hints, provisional)):
            if result.status.startswith("_PENDING"):
                if progress:
                    progress(index, total, "arxiv_fallback", None)
                result = self._verify_arxiv(hint, result)
                if progress:
                    progress(index, total, "complete", result)
            output.append(result)
        return output

    def _verify_without_arxiv(self, hint: PaperHint) -> VerificationResult:
        route = self.registry.resolve(hint)
        if hint.target.startswith("exact_arxiv") or route.inferred_by == "explicit_arxiv":
            return _pending(hint, "The user selected the arXiv object directly.")

        errors: list[str] = []
        source_failures: list[str] = []
        evidence: list[ScoredCandidate] = []
        unavailable: list[tuple[str, str]] = []
        required_credential: str | None = None
        if route.unsupported_official:
            errors.append(
                f"official: venue '{route.venue}' has no configured official adapter; "
                "continuing with lower-authority sources"
            )
        for source_route in route.official:
            outcome = self._lookup_route(
                hint, source_route, errors, source_failures, evidence
            )
            if isinstance(outcome, VerificationResult):
                return outcome
            if outcome:
                unavailable.append((source_route.adapter, outcome))

        if unavailable:
            for adapter, failure in unavailable:
                credential = self.missing_credentials.get(adapter)
                required_credential = required_credential or credential
                detail = (
                    f"missing credential {credential}"
                    if credential
                    else failure
                )
                if not any(item.startswith(f"{adapter}:") for item in errors):
                    message = f"{adapter}: {detail}; continuing with OpenReview/arXiv"
                    errors.append(message)
                    source_failures.append(message)
                elif not any(item.startswith(f"{adapter}:") for item in source_failures):
                    source_failures.append(
                        f"{adapter}: {detail}; continuing with OpenReview/arXiv"
                    )

        review_route = route.review
        if review_route and review_route.adapter not in {item.adapter for item in route.official}:
            review_result = self._lookup_review(
                hint,
                review_route,
                errors,
                source_failures,
                evidence,
                official=False,
            )
            if review_result:
                review_result.required_credential = required_credential
                return review_result

        pending = _pending(
            hint,
            "No canonical official or citable OpenReview record was found.",
        )
        pending.errors = errors
        pending.source_failures = source_failures
        pending.evidence = _top_evidence(evidence)
        pending.required_credential = required_credential
        return pending

    def _lookup_route(
        self,
        hint: PaperHint,
        source_route: SourceRoute,
        errors: list[str],
        source_failures: list[str],
        evidence: list[ScoredCandidate],
    ) -> VerificationResult | str | None:
        if source_route.adapter == "openreview":
            return self._lookup_review(
                hint,
                source_route,
                errors,
                source_failures,
                evidence,
                official=True,
            )
        source = self.sources.get(source_route.adapter)
        if source is None:
            credential = self.missing_credentials.get(source_route.adapter) or source_route.credential
            if credential:
                self.missing_credentials.setdefault(source_route.adapter, credential)
            return "OFFICIAL_SOURCE_UNAVAILABLE"
        try:
            records = source.search(hint, limit=self.candidate_limit)
        except SourceBlockedError as exc:
            message = f"{source.name}: blocked: {exc}"
            errors.append(message)
            source_failures.append(message)
            return "OFFICIAL_SOURCE_BLOCKED"
        except UnsupportedLookupError as exc:
            message = f"{source.name}: unsupported lookup: {exc}"
            errors.append(message)
            if source_route.credential and source_route.adapter in self.missing_credentials:
                source_failures.append(message)
                return "OFFICIAL_SOURCE_UNAVAILABLE"
            return None
        except Exception as exc:
            message = f"{source.name}: {type(exc).__name__}: {exc}"
            errors.append(message)
            source_failures.append(message)
            return "OFFICIAL_SOURCE_UNAVAILABLE"

        selection = self._select(hint, records, evidence)
        if selection is None:
            if source_route.credential and source_route.adapter in self.missing_credentials:
                return "OFFICIAL_SOURCE_UNAVAILABLE"
            return None
        if selection == "AMBIGUOUS":
            return VerificationResult(
                hint=hint,
                status="AMBIGUOUS",
                reason="Multiple distinct official records are within the ambiguity margin.",
                evidence=_top_evidence(evidence),
                errors=errors,
                source_failures=source_failures,
            )
        assert isinstance(selection, ScoredCandidate)
        if selection.title_score < 95:
            return None
        if hint.authors and (selection.author_score is None or selection.author_score < 75):
            return None
        if hint.year is not None and selection.year_score == 0:
            return None
        return VerificationResult(
            hint=hint,
            status="FINAL",
            reason="Matched the venue-routed official publication source.",
            record=selection.record,
            score=selection.score,
            evidence=_top_evidence(evidence),
            errors=errors,
            source_failures=source_failures,
            authority_level="L1",
            source_role=source_route.role,
        )

    def _lookup_review(
        self,
        hint: PaperHint,
        source_route: SourceRoute,
        errors: list[str],
        source_failures: list[str],
        evidence: list[ScoredCandidate],
        official: bool,
    ) -> VerificationResult | str | None:
        source = self.sources.get("openreview")
        if source is None:
            message = "openreview: adapter unavailable; continuing with arXiv"
            errors.append(message)
            source_failures.append(message)
            return "OFFICIAL_SOURCE_UNAVAILABLE" if official else None
        try:
            records = source.search(hint, limit=self.candidate_limit)
        except Exception as exc:
            message = f"openreview: {type(exc).__name__}: {exc}"
            errors.append(message)
            source_failures.append(message)
            return "OFFICIAL_SOURCE_UNAVAILABLE" if official else None
        selection = self._select(hint, records, evidence)
        if selection is None:
            return None
        if selection == "AMBIGUOUS":
            return VerificationResult(
                hint=hint,
                status="AMBIGUOUS",
                reason="Multiple OpenReview records are within the ambiguity margin.",
                evidence=_top_evidence(evidence),
                errors=errors,
                source_failures=source_failures,
            )
        assert isinstance(selection, ScoredCandidate)
        disposition = selection.record.extra.get("disposition", "unknown")
        if disposition == "rejected":
            return VerificationResult(
                hint=hint,
                status="REJECTED_OPENREVIEW",
                reason=(
                    "Citing the public OpenReview manuscript as a rejected submission; "
                    "it is not an accepted proceedings publication."
                ),
                record=selection.record,
                score=selection.score,
                evidence=_top_evidence(evidence),
                errors=errors,
                source_failures=source_failures,
                authority_level="L2",
                source_role="public_rejected_submission",
            )
        if disposition == "withdrawn":
            result = _pending(hint, "The matching OpenReview submission is withdrawn.", "_PENDING_WITHDRAWN_OPENREVIEW")
            result.evidence = _top_evidence(evidence)
            result.errors = errors
            result.source_failures = source_failures
            return result
        if disposition != "accepted":
            return VerificationResult(
                hint=hint,
                status="OPENREVIEW_SUBMISSION",
                reason=(
                    "Citing the public OpenReview manuscript; no final acceptance decision "
                    "was verified, so it is not presented as a proceedings publication."
                ),
                record=selection.record,
                score=selection.score,
                evidence=_top_evidence(evidence),
                errors=errors,
                source_failures=source_failures,
                authority_level="L2",
                source_role="public_submission",
            )
        return VerificationResult(
            hint=hint,
            status="FINAL" if official else "PROVISIONAL_OPENREVIEW",
            reason=(
                "OpenReview is the venue's official proceedings source."
                if official
                else "No final proceedings record was found; using an accepted OpenReview record provisionally."
            ),
            record=selection.record,
            score=selection.score,
            evidence=_top_evidence(evidence),
            errors=errors,
            source_failures=source_failures,
            authority_level="L1" if official else "L2",
            source_role=source_route.role,
        )

    def _verify_arxiv(self, hint: PaperHint, previous: VerificationResult) -> VerificationResult:
        source = self.sources.get("arxiv")
        if source is None:
            message = "arxiv: adapter unavailable"
            previous.errors.append(message)
            previous.source_failures.append(message)
            return _terminal_from_pending(previous)
        evidence = list(previous.evidence)
        errors = list(previous.errors)
        source_failures = list(previous.source_failures)
        try:
            records = source.search(hint, limit=self.candidate_limit)
        except Exception as exc:
            message = f"arxiv: {type(exc).__name__}: {exc}"
            errors.append(message)
            source_failures.append(message)
            previous.source_failures = source_failures
            result = _terminal_from_pending(previous)
            result.errors = errors
            result.source_failures = source_failures
            result.reason += " arXiv fallback also failed."
            return result
        selection = self._select(hint, records, evidence)
        if selection is None:
            result = _terminal_from_pending(previous)
            result.evidence = _top_evidence(evidence)
            result.errors = errors
            return result
        if selection == "AMBIGUOUS":
            return VerificationResult(
                hint=hint,
                status="AMBIGUOUS",
                reason="Multiple arXiv records are within the ambiguity margin.",
                evidence=_top_evidence(evidence),
                errors=errors,
                source_failures=source_failures,
                required_credential=previous.required_credential,
            )
        assert isinstance(selection, ScoredCandidate)
        withdrawn = bool(selection.record.extra.get("withdrawn"))
        return VerificationResult(
            hint=hint,
            status="WITHDRAWN_ARXIV" if withdrawn else "PREPRINT_ARXIV",
            reason=(
                "The latest arXiv record is withdrawn; no older version was substituted."
                if withdrawn
                else "No higher-level canonical record was found; citing the latest arXiv preprint."
            ),
            record=selection.record,
            score=selection.score,
            evidence=_top_evidence(evidence),
            errors=errors,
            source_failures=source_failures,
            authority_level="L3",
            source_role="preprint_repository",
            required_credential=previous.required_credential,
        )

    def _select(
        self,
        hint: PaperHint,
        records: list,
        evidence: list[ScoredCandidate],
    ) -> ScoredCandidate | str | None:
        scored = [score_candidate(hint, record) for record in records]
        evidence.extend(scored)
        accepted = [item for item in scored if item.identifier_match or item.score >= self.threshold]
        accepted.sort(key=lambda item: (item.identifier_match, item.score), reverse=True)
        if not accepted:
            return None
        if len(accepted) > 1:
            first, second = accepted[:2]
            if first.score - second.score < self.ambiguity_margin and work_key(first.record) != work_key(second.record):
                return "AMBIGUOUS"
        return accepted[0]


def _pending(hint: PaperHint, reason: str, status: str = "_PENDING_FALLBACK") -> VerificationResult:
    return VerificationResult(hint=hint, status=status, reason=reason)


def _terminal_from_pending(result: VerificationResult) -> VerificationResult:
    status = {
        "_PENDING_WITHDRAWN_OPENREVIEW": "WITHDRAWN_OPENREVIEW",
    }.get(
        result.status,
        "SOURCE_UNAVAILABLE" if result.source_failures else "UNVERIFIED",
    )
    reason = result.reason
    if status == "SOURCE_UNAVAILABLE":
        reason = (
            "No fallback record was verified because at least one required source "
            "was unavailable. This is a retrieval error, not evidence that the citation is false."
        )
    return VerificationResult(
        hint=result.hint,
        status=status,
        reason=reason,
        evidence=result.evidence,
        errors=result.errors,
        source_failures=result.source_failures,
        required_credential=result.required_credential,
    )


def _top_evidence(values: list[ScoredCandidate]) -> list[ScoredCandidate]:
    return sorted(values, key=lambda item: (item.identifier_match, item.score), reverse=True)[:10]
