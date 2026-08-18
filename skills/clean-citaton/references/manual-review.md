# Human review candidate workflow

`results/manual-review-queue.json` is the program-owned handoff for entries that still lack an exportable record. The host model researches candidate pages after the deterministic verification run.

## Candidate authority

Keep a candidate only when its page is accessible during research and belongs to one of these first-party authorities:

1. Official journal or publisher article page.
2. Official conference proceedings or paper page.
3. Official OpenReview forum page.
4. Official arXiv abstract page.

A DOI resolver may locate a page, while the recorded candidate URL must be the final first-party landing page. Exclude aggregators, mirrors, author profiles, repository copies, search-result pages, and model-generated URLs.

## High-confidence evidence

Open every candidate URL. Require title agreement plus at least one of these independent checks:

- DOI agreement.
- Author and year agreement.
- Official venue and year agreement.
- Exact OpenReview or arXiv identifier agreement.

Record only `HIGH` confidence candidates. Leave an entry without a candidate when the evidence is weaker.

## Model-owned output

Create `<project>/manual-review/candidates.json` and `<project>/manual-review/candidates.md`. These files are separate from the verifier's `results/` directory and remain available for human annotation.

```json
{
  "schema_version": "1.0",
  "purpose": "human_check_only",
  "generated_at": "2026-01-01T00:00:00Z",
  "items": [
    {
      "index": 1,
      "title": "Example Paper",
      "candidate_url": "https://official.example/article/1",
      "source_name": "Official Publisher",
      "authority_role": "official_publication",
      "access_checked_at": "2026-01-01T00:00:00Z",
      "confidence": "HIGH",
      "evidence": {
        "title": "exact",
        "authors": "matched",
        "year": "matched",
        "doi": "10.0000/example"
      },
      "suggested_input": {
        "official_url": "https://official.example/article/1",
        "doi": "10.0000/example"
      }
    }
  ]
}
```

The Markdown companion summarizes the same fields and labels every link as a human-review candidate. Candidate files never enter BibTeX automatically. A human-confirmed DOI or official URL returns through `input/citations.json` and the normal verifier pipeline.
