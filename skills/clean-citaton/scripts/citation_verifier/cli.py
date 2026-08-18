from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .io import load_hints
from .openreview_auth import OpenReviewLoginError, configure_openreview_token
from .planner import build_run_plan
from .publisher import OutputPublisher
from .registry import SourceRegistry
from .sources import (
    ArxivSource,
    ElsevierSource,
    IeeeSource,
    OfficialWebSource,
    OpenReviewSource,
    SpringerSource,
    VldbSource,
)
from .sources.base import DEFAULT_USER_AGENT, MetadataSource
from .transport import CachedTransport, HostTlsFallbackTransport, UrllibTransport
from .verifier import CitationVerifier


CREDENTIAL_NAMES = (
    "IEEE_XPLORE_API_KEY",
    "SPRINGER_NATURE_API_KEY",
    "ELSEVIER_API_KEY",
    "OPENREVIEW_ACCESS_TOKEN",
)
WEB_ADAPTERS = (
    "neurips",
    "pmlr",
    "cvf",
    "acl_anthology",
    "usenix",
    "aaai",
    "ijcai",
    "jmlr",
    "mlsys",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clean-citaton",
        description=(
            "Resolve scholarly citations through venue-routed official sources, then OpenReview "
            "and rate-limited arXiv."
        ),
    )
    parser.add_argument("--input", "-i", help="Input titles JSON path")
    parser.add_argument("--output-dir", "-o", help="Legacy direct results directory")
    parser.add_argument(
        "--project-dir",
        help="Project folder; reads input/citations.json by default and writes results/ plus .cache/",
    )
    parser.add_argument(
        "--source-config",
        help="Optional JSON overlay that maps additional venues to bundled adapters",
    )
    parser.add_argument(
        "--credentials",
        help="Optional local KEY=VALUE file; environment variables take precedence",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Persistent program-owned metadata cache",
    )
    parser.add_argument("--no-cache", action="store_true", help="Disable persistent HTTP cache")
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Write run-plan.json and stop before any network request",
    )
    parser.add_argument("--show-config", action="store_true", help="Print adapter/key availability and exit")
    parser.add_argument(
        "--configure-openreview",
        action="store_true",
        help="Interactively obtain an official short-lived OpenReview session token",
    )
    parser.add_argument("--threshold", type=float, default=86.0)
    parser.add_argument("--ambiguity-margin", type=float, default=2.5)
    parser.add_argument("--candidate-limit", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=25.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.configure_openreview:
            credential_path = Path(args.credentials) if args.credentials else _default_credentials_path()
            saved_path = configure_openreview_token(
                credential_path,
                timeout=args.timeout,
            )
            print(f"OpenReview session token saved to {saved_path}; password was not stored.")
            return 0
        credential_path = Path(args.credentials) if args.credentials else _default_credentials_path()
        credentials = _load_credentials(str(credential_path) if credential_path.exists() else None)
        registry = SourceRegistry.load(args.source_config)
        input_path, output_dir, cache_dir = _resolve_run_paths(args)
        args.cache_dir = str(cache_dir)
        sources, missing = _build_sources(args, credentials)
        if args.show_config:
            _print_config(sources, missing, args.source_config)
            return 0

        assert input_path is not None and output_dir is not None
        hints = load_hints(input_path)
        source_names = sorted(source.name for source in sources)
        plan = build_run_plan(hints, registry, set(source_names), missing)
        publisher = OutputPublisher(output_dir, hints, source_names)
        publisher.initialize(plan)
        plan_data = plan.to_dict()
        estimate = plan_data["estimated_time"]
        print(
            f"Plan: {len(hints)} item(s), estimated {estimate['min']}–{estimate['max']}. "
            f"Details: {(output_dir / 'run-plan.json').resolve()}",
            flush=True,
        )
        if plan.missing_credentials:
            print(
                "Optional/required credentials not configured for routed items: "
                + ", ".join(sorted(plan.missing_credentials.values())),
                flush=True,
            )
        if args.plan_only:
            print("Plan-only mode: no network requests were made.", flush=True)
            return 0

        verifier = CitationVerifier(
            sources=sources,
            registry=registry,
            missing_credentials=missing,
            threshold=args.threshold,
            ambiguity_margin=args.ambiguity_margin,
            candidate_limit=args.candidate_limit,
        )

        def publish(index, total, stage, result):
            publisher.update(index, total, stage, result)
            if result is not None:
                print(f"[{index + 1}/{total}] {result.status}: {result.hint.title}", flush=True)

        try:
            results = verifier.verify_many(hints, progress=publish)
        finally:
            verifier.close()
        publisher.finalize(results)

        final = sum(result.status == "FINAL" for result in results)
        citable = sum(result.is_citable for result in results)
        source_unavailable = sum(
            result.status == "SOURCE_UNAVAILABLE" for result in results
        )
        unresolved = sum(
            not result.is_citable and result.status != "SOURCE_UNAVAILABLE"
            for result in results
        )
        print(
            f"Complete: {final} final, {citable - final} provisional/preprint, "
            f"{source_unavailable} source unavailable, {unresolved} unresolved. "
            f"Outputs: {output_dir.resolve()}",
            flush=True,
        )
        return 0 if final == len(results) else 2
    except (OSError, ValueError, OpenReviewLoginError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _build_sources(
    args: argparse.Namespace,
    credentials: dict[str, str],
) -> tuple[list[MetadataSource], dict[str, str]]:
    cache_root = Path(args.cache_dir or _default_cache_dir())

    def transport(name: str, ttl_seconds: float, *, cache_allowed: bool = True):
        inner = UrllibTransport(user_agent=DEFAULT_USER_AGENT, timeout=args.timeout, retries=1)
        if name == "aaai":
            inner = HostTlsFallbackTransport(
                inner,
                allowed_hosts={"ojs.aaai.org"},
                user_agent=DEFAULT_USER_AGENT,
                timeout=args.timeout,
            )
        if args.no_cache or not cache_allowed:
            return inner
        return CachedTransport(inner, cache_root / name, ttl_seconds=ttl_seconds)

    openreview_token = credentials.get("OPENREVIEW_ACCESS_TOKEN")
    sources: list[MetadataSource] = [
        ArxivSource(
            transport=transport("arxiv", 24 * 3600),
            rate_state_path=cache_root / "arxiv-rate-limit.state",
        ),
        OpenReviewSource(
            transport=transport(
                "openreview",
                12 * 3600,
                cache_allowed=not bool(openreview_token),
            ),
            access_token=openreview_token,
        ),
    ]
    sources.extend(
        OfficialWebSource(name, transport=transport(name, 14 * 24 * 3600))
        for name in WEB_ADAPTERS
    )
    sources.append(VldbSource(transport=transport("vldb", 14 * 24 * 3600)))
    missing: dict[str, str] = {}

    ieee_key = credentials.get("IEEE_XPLORE_API_KEY")
    if ieee_key:
        sources.append(IeeeSource(ieee_key, transport=transport("ieee", 7 * 24 * 3600)))
    else:
        missing["ieee"] = "IEEE_XPLORE_API_KEY"

    springer_key = credentials.get("SPRINGER_NATURE_API_KEY")
    if springer_key:
        sources.append(SpringerSource(springer_key, transport=transport("springer", 7 * 24 * 3600)))
    else:
        # The public landing-page adapter can still resolve an exact official
        # URL. A missing title-search key is retained in the audit while the
        # verifier continues to OpenReview/arXiv.
        sources.append(OfficialWebSource("springer", transport=transport("springer", 7 * 24 * 3600)))
        missing["springer"] = "SPRINGER_NATURE_API_KEY"

    elsevier_key = credentials.get("ELSEVIER_API_KEY")
    if elsevier_key:
        sources.append(ElsevierSource(elsevier_key, transport=transport("elsevier", 7 * 24 * 3600)))
    else:
        missing["elsevier"] = "ELSEVIER_API_KEY"
    return sources, missing


def _load_credentials(path: str | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if path:
        for line_number, raw in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ValueError(f"Invalid credential line {line_number}: expected KEY=VALUE")
            key, value = (part.strip() for part in line.split("=", 1))
            if key not in CREDENTIAL_NAMES:
                raise ValueError(f"Unsupported credential name in line {line_number}: {key}")
            if value:
                values[key] = value
    for name in CREDENTIAL_NAMES:
        if os.getenv(name):
            values[name] = os.environ[name]
    return values


def _print_config(
    sources: list[MetadataSource],
    missing: dict[str, str],
    source_config: str | None,
) -> None:
    print("Available adapters: " + ", ".join(sorted(source.name for source in sources)))
    print("Credential state:")
    credential_adapters = {
        "IEEE_XPLORE_API_KEY": "ieee",
        "SPRINGER_NATURE_API_KEY": "springer",
        "ELSEVIER_API_KEY": "elsevier",
    }
    for name in CREDENTIAL_NAMES:
        if name == "OPENREVIEW_ACCESS_TOKEN":
            openreview = next(source for source in sources if source.name == "openreview")
            state = "configured" if getattr(openreview, "_access_token", None) else "optional (public mode)"
            print(f"  {name}: {state}")
            continue
        adapter = credential_adapters[name]
        print(f"  {name}: {'missing' if adapter in missing else 'configured'}")
    print(f"Registry overlay: {source_config or 'bundled defaults'}")


def _default_cache_dir() -> Path:
    if os.name == "nt" and os.getenv("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "clean-citaton" / "cache"
    return Path.home() / ".cache" / "clean-citaton"


def _default_credentials_path() -> Path:
    primary = Path.home() / ".clean-citaton" / "credentials.env"
    legacy = Path.home() / ".citation-verifier" / "credentials.env"
    return legacy if legacy.exists() and not primary.exists() else primary


def _resolve_run_paths(args: argparse.Namespace) -> tuple[Path | None, Path | None, Path]:
    if args.show_config:
        return None, None, Path(args.cache_dir) if args.cache_dir else _default_cache_dir()
    if args.project_dir and args.output_dir:
        raise ValueError("Use --project-dir or --output-dir, not both")
    if args.project_dir:
        project_dir = Path(args.project_dir)
        input_path = Path(args.input) if args.input else project_dir / "input" / "citations.json"
        output_dir = project_dir / "results"
        cache_dir = Path(args.cache_dir) if args.cache_dir else project_dir / ".cache"
        return input_path, output_dir, cache_dir
    if not args.input or not args.output_dir:
        raise ValueError("Use --project-dir, or provide both --input and --output-dir")
    return (
        Path(args.input),
        Path(args.output_dir),
        Path(args.cache_dir) if args.cache_dir else _default_cache_dir(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
