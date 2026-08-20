from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .models import PaperHint
from .registry import SourceRegistry


STATIC_INDEX_SOURCES = {
    "neurips",
    "pmlr",
    "cvf",
    "acl_anthology",
    "ijcai",
    "jmlr",
    "usenix",
    "mlsys",
    "vldb",
}
ARXIV_REQUEST_INTERVAL_SECONDS = 5.0
ARXIV_DEFAULT_THROTTLE_RESERVE_SECONDS = 225


@dataclass(slots=True)
class RunPlan:
    items: list[dict]
    source_counts: dict[str, int]
    missing_credentials: dict[str, str]
    estimated_seconds_min: int
    estimated_seconds_max: int
    assumptions: list[str]

    def to_dict(self) -> dict:
        value = asdict(self)
        value["estimated_time"] = {
            "min": _duration(self.estimated_seconds_min),
            "max": _duration(self.estimated_seconds_max),
        }
        return value


def build_run_plan(
    hints: list[PaperHint],
    registry: SourceRegistry,
    available_sources: set[str],
    missing_credentials: dict[str, str],
) -> RunPlan:
    items: list[dict] = []
    counts: dict[str, int] = {}
    static_indexes: set[tuple[str, str | None, int | None]] = set()
    openreview_v1 = 0
    arxiv_exact = 0
    arxiv_title = 0
    min_requests = 0
    max_requests = 0

    for index, hint in enumerate(hints):
        route = registry.resolve(hint)
        official = [item.adapter for item in route.official]
        for source in official:
            counts[source] = counts.get(source, 0) + 1
            if source not in available_sources:
                continue
            if source in STATIC_INDEX_SOURCES:
                static_indexes.add((source, route.venue, hint.year))
            else:
                min_requests += 1
        if route.review:
            counts[route.review.adapter] = counts.get(route.review.adapter, 0) + 1
            max_requests += 1
            if hint.year and hint.year < 2024:
                openreview_v1 += 1
        if "arxiv" in route.fallback:
            counts["arxiv"] = counts.get("arxiv", 0) + 1
            if hint.arxiv_id:
                arxiv_exact += 1
            else:
                arxiv_title += 1
        items.append(
            {
                "index": index + 1,
                "title": hint.title,
                "venue": route.venue,
                "route_inferred_by": route.inferred_by,
                "official_sources": official,
                "review_source": route.review.adapter if route.review else None,
                "fallback": list(route.fallback),
                "unsupported_official": route.unsupported_official,
            }
        )

    # One collection fetch plus one detail fetch per item is the conservative
    # static-source request model. Persistent cache can eliminate either.
    min_requests += len(static_indexes) + sum(counts.get(source, 0) for source in STATIC_INDEX_SOURCES)
    max_requests += min_requests
    arxiv_batch_calls = math.ceil(arxiv_exact / 50) if arxiv_exact else 0
    arxiv_calls_max = arxiv_batch_calls + arxiv_title
    max_requests += arxiv_calls_max

    estimated_min = max(1, math.ceil(min_requests * 0.35))
    estimated_max = math.ceil(max_requests * 5.0)
    if openreview_v1:
        estimated_max += max(0, openreview_v1 - 1) * 12
    if arxiv_calls_max:
        estimated_max += max(0, arxiv_calls_max - 1) * ARXIV_REQUEST_INTERVAL_SECONDS
        estimated_max += ARXIV_DEFAULT_THROTTLE_RESERVE_SECONDS
    estimated_max = int(math.ceil(estimated_max))

    relevant_missing = {
        adapter: credential
        for adapter, credential in missing_credentials.items()
        if counts.get(adapter, 0) > 0
    }
    return RunPlan(
        items=items,
        source_counts=dict(sorted(counts.items())),
        missing_credentials=relevant_missing,
        estimated_seconds_min=estimated_min,
        estimated_seconds_max=max(estimated_min, estimated_max),
        assumptions=[
            "The minimum assumes responsive official pages and useful cache hits.",
            "The maximum assumes official misses, OpenReview lookup, and arXiv fallback for every eligible item.",
            "arXiv uses one connection, a conservative five-second request cadence, and exact-ID batches of 50.",
            "The maximum reserves one default 225-second arXiv throttling episode; a server Retry-After value can extend the wait.",
            "No model tokens are spent polling: progress is written by the program to progress.json.",
        ],
    )


def _duration(seconds: int) -> str:
    minutes, remaining = divmod(max(0, seconds), 60)
    if minutes:
        return f"{minutes}m {remaining:02d}s"
    return f"{remaining}s"
