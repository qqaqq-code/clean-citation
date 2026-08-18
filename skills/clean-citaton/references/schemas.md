# Input and output schemas

## Input

Accept an array or an object containing `papers`:

```json
{
  "papers": [
    {
      "title": "Attention Is All You Need",
      "authors": ["Ashish Vaswani"],
      "year": 2017,
      "venue": "NeurIPS",
      "track": "main",
      "volume": "30",
      "issue": null,
      "pages": "5998-6008",
      "article_number": null,
      "doi": null,
      "arxiv_id": "1706.03762",
      "official_url": null,
      "target": "best_formal_available",
      "original_text": "optional unmodified input fragment"
    }
  ]
}
```

Only `title` is required. All fields are hints until an official adapter verifies them. Preserve supplied `volume`, `issue`, `pages`, and `article_number`; they can make official publisher lookup deterministic but never override verified metadata. `target` may be `best_formal_available`, `arxiv`, or an explicit arXiv-version target.

## Program-owned output

| File | Purpose |
|---|---|
| `titles.json` | Normalized copy of verifier input |
| `run-plan.json` | Routes, source counts, credentials state, runtime estimate |
| `progress.json` | Per-item queued/lookup/complete state |
| `verification.json` | Canonical results, evidence, provenance, failures |
| `references.bib` | Citable final/provisional/preprint records only |
| `references.md` | Readable references plus audit statuses |
| `manual-review-queue.json` | Structured non-citable items for model research and human review |
| `manifest.json` | SHA-256 and read-only ownership metadata |

Never edit these files. Rerun from changed input/config.

Status codes: `FINAL`, `PROVISIONAL_OPENREVIEW`, `OPENREVIEW_SUBMISSION`, `REJECTED_OPENREVIEW`, `PREPRINT_ARXIV`, `WITHDRAWN_ARXIV`, `AMBIGUOUS`, `UNVERIFIED`, `SOURCE_UNAVAILABLE`, `WITHDRAWN_OPENREVIEW`.

`FINAL`, `PROVISIONAL_OPENREVIEW`, `OPENREVIEW_SUBMISSION`, `REJECTED_OPENREVIEW`, and `PREPRINT_ARXIV` are exportable but remain visibly distinct. Submitted/rejected OpenReview records are emitted as labeled `@misc`, not proceedings papers. Withdrawn, ambiguous, unavailable, and unverified records are excluded from BibTeX.

`SOURCE_UNAVAILABLE` means an official/API source was blocked, missing a required credential, or failed while all lower fallbacks also missed. It is a source-access error, not a finding that the citation is false. `UNVERIFIED` is reserved for completed lookups that produced no reliable match. The audit summary reports `source_unavailable` and `unresolved` separately; `not_citable` is their combined non-exportable count.

The host model reads `manual-review-queue.json` and writes high-confidence first-party website candidates under the separate user/model-owned `manual-review/` directory. See [manual-review.md](manual-review.md). Human-confirmed identifiers return through input and a fresh verifier run.

Exit codes: `0` means every item is final; `1` means invalid input/configuration; `2` means outputs exist but at least one item is not final; `3` means source-runtime integrity failed.
