---
name: clean-citaton
description: Verify or correct scholarly references through a clean, official-source evidence chain. Route journal and conference records to first-party publisher or proceedings sources, then OpenReview, then rate-limited arXiv; export read-only JSON, BibTeX, Markdown, provenance, and a manual-review queue. Use for possibly hallucinated citations, bibliography correction, DOI/arXiv/OpenReview checks, auditable .bib generation, or high-confidence official-site research for unresolved entries.
---

# Clean Citaton

Treat model-generated titles and user citations as untrusted hints. Use the host model only to structure input and research manual-review candidates. Let the bundled Python crawler alone retrieve official evidence, select canonical metadata, assign statuses, and generate formal JSON, BibTeX, and Markdown. Do not call a second model API for title extraction or citation metadata.

## Immutable boundaries

- Never create, edit, patch, reformat, or replace files under this Skill's `scripts/` directory. The runtime is read-only and hash-verified.
- Never edit `results/`, `.cache/`, or any generated artifact, including `manual-review-queue.json`. These are program-owned read-only snapshots.
- Never write formal citation fields or formal citation files from model memory. Pass hints through `input/citations.json` and let the Python runtime publish the result.
- Apply citation corrections through `input/citations.json`, a user-owned source overlay, or repository-external credentials, then rerun.
- Store model-researched human-review candidates only under `<project>/manual-review/`.
- Read `manifest.json` before reporting results when integrity is relevant.

## Workflow

1. Create one project folder at `<workspace>/citation-projects/<project-name>/`. Write structured hints to `input/citations.json` according to [references/schemas.md](references/schemas.md). Preserve the original citation in `original_text`; use `null` for unknown values.
2. Read [references/runtime.md](references/runtime.md), select the bundled executable for the computer, and use the source launcher when Python 3.10+ is already available.
3. Run once with `--plan-only`. Read `results/run-plan.json` and report the estimated duration, route counts, and credential state before retrieval.
4. Run the same project without `--plan-only`. The program updates `results/progress.json` after each item.
5. Read `results/verification.json`, `results/references.bib`, and `results/references.md`. Explain every status that is below `FINAL`.
6. When `results/manual-review-queue.json` contains items, follow [references/manual-review.md](references/manual-review.md). Research high-confidence, accessible first-party URLs and publish the model-owned review files under `<project>/manual-review/`.
7. Present program results and human-review candidates as separate outputs. Use confirmed DOI or official URLs to update the input and run a fresh verification pass.

```powershell
<runtime> --project-dir <workspace>/citation-projects/<project-name> --plan-only
<runtime> --project-dir <workspace>/citation-projects/<project-name>
```

## Resolution order

1. Extract exact DOI, official URL, OpenReview forum ID, and arXiv ID from the supplied material.
2. Resolve `venue + year + track` through `references/source-registry.json` plus any overlay passed with `--source-config`.
3. Query the routed official journal, publisher, or conference adapter. Prefer native official BibTeX and citation meta tags.
4. Query OpenReview when the formal source yields no usable record or has an access problem. OpenReview is formal authority for venues whose proceedings live there; other records retain provisional, submitted, rejected, or withdrawn labels.
5. Query arXiv after the official and OpenReview stages. Retain `PREPRINT_ARXIV` and withdrawal labels.

One selected authority owns the complete set of core fields: title, author order, venue, year, DOI, volume, issue, and pages. Never merge these fields across records or use source voting. Crossref and Semantic Scholar are outside this authority chain.

The arXiv legacy API is limited to one request every three seconds across controlled machines and one connection at a time. Keep the bundled 3.05-second cross-process gate, exact-ID batching, and cache.

## Failure and fallback semantics

- Official key, TLS, HTTP, or network failure remains in `source_failures`, while resolution continues through OpenReview and arXiv.
- `SOURCE_UNAVAILABLE` means at least one routed source was inaccessible and every lower fallback missed.
- `UNVERIFIED` means all completed lookups produced no reliable match.
- `AMBIGUOUS` means multiple candidates remain too close.
- `PROVISIONAL_OPENREVIEW`, `OPENREVIEW_SUBMISSION`, `REJECTED_OPENREVIEW`, and `PREPRINT_ARXIV` are exportable with explicit labels.
- Withdrawn records remain outside BibTeX.
- Exit code `2` means the snapshots were published with at least one provisional, preprint, source-access, or unresolved item.

## Credentials and source overlays

Read [references/data-sources.md](references/data-sources.md) before configuring a key or route. Supported private values are:

```text
IEEE_XPLORE_API_KEY=...
SPRINGER_NATURE_API_KEY=...
ELSEVIER_API_KEY=...
OPENREVIEW_ACCESS_TOKEN=...
```

Use environment variables or a repository-external `KEY=VALUE` file. Never place credentials in chat, commands, input, output, logs, or cache. Report only configured/missing state through `--show-config`.

For an OpenReview `ChallengeRequiredError`, let the user run `<runtime> --configure-openreview`. The official login flow supports MFA, stores only a session token for up to seven days, bypasses persistent caching for authenticated responses, and accepts only notes readable by `everyone`.

Map extra venues to bundled adapters through a user JSON overlay passed with `--source-config`. Runtime users configure routes and keys without changing Python.
