# Official source registry

The bundled `source-registry.json` owns venue routing. A user may overlay existing adapters without changing Python:

```json
{
  "venues": [
    {
      "venue": "My IEEE Venue",
      "aliases": ["MIV"],
      "official": [
        {
          "adapter": "ieee",
          "role": "official_publication",
          "credential": "IEEE_XPLORE_API_KEY"
        }
      ],
      "fallback": ["arxiv"]
    }
  ]
}
```

Pass it with `--source-config`. Overlay entries replace the same canonical venue. Supported adapter names are `neurips`, `pmlr`, `mlsys`, `acl_anthology`, `ieee`, `cvf`, `usenix`, `springer`, `elsevier`, `aaai`, `ijcai`, `jmlr`, `vldb`, `openreview`, and `arxiv`.

## Trust order

1. L1 official journal/conference/publisher page, API, or native BibTeX.
2. L2 OpenReview record, with accepted, submitted, and rejected states kept distinct.
3. L3 latest arXiv record.

OpenReview is L1 for a venue such as ICLR whose official proceedings live there. CVF is an L1 no-key fallback for CVPR/ICCV/WACV when IEEE is not configured. Never combine core citation fields across levels.

An empty L1 search, missing adapter, authentication failure, publisher block, SSL error, or network failure continues to OpenReview and then arXiv. Keep the L1 problem in the audit record and keep any fallback visibly provisional/preprint.

Crossref is disabled and has no adapter, command option, credential, or fallback role. A DOI is used only to identify an object and route it to its publisher.

## Bundled official adapters

| Adapter | Official data | Key |
|---|---|---|
| `neurips` | `proceedings.neurips.cc` annual index and native BibTeX | No |
| `pmlr` | `proceedings.mlr.press` volume page and native BibTeX | No |
| `mlsys` | Official MLSys proceedings index and article metadata | No |
| `acl_anthology` | ACL Anthology event/article citation metadata | No |
| `ieee` | IEEE Xplore Metadata API | `IEEE_XPLORE_API_KEY` |
| `cvf` | CVF Open Access collection/article BibTeX | No |
| `usenix` | Official conference/article citation metadata | No |
| `springer` | Springer Nature Meta API v2; landing page for exact URL | `SPRINGER_NATURE_API_KEY` for title search |
| `elsevier` | Elsevier Article Metadata API / ScienceDirect records | `ELSEVIER_API_KEY` |
| `aaai` | AAAI OJS article citation export | No |
| `ijcai` | IJCAI annual proceedings and per-paper BibTeX | No |
| `jmlr` | JMLR official article index/metadata | No |
| `vldb` | VLDB Endowment official PVLDB PDFs and metadata | No |

Static collection pages are fetched once per source/venue/year in a run and cached persistently. Official native BibTeX is retained as raw provenance and exported before synthesized fields.

The ACM DL, Taylor & Francis, Now Publishers, and SIAM HTML adapters were removed after repeatable access blocks. The runtime does not substitute mirrors or scrape around those blocks; it enters the normal OpenReview → arXiv chain.

If a named journal has no configured publisher adapter, an accepted OpenReview result stays `PROVISIONAL_OPENREVIEW`, and an arXiv result stays `PREPRINT_ARXIV`; neither is presented as the formal journal record.

## OpenReview

Public reads normally need no account. Current-year records use API v2 (`https://api2.openreview.net`); legacy years use v1 (`https://api.openreview.net`) to avoid redundant calls. V1 is paced at 5 requests per minute. Parse wrapped v2 values and plain v1 values. Inspect venue/decision text and classify accepted, rejected, withdrawn, or unknown.

Title search excludes DBLP/ORCID `Public_Article` imports because they are profile bibliography records, not venue submissions. An exact forum URL uses `/notes?id=...`; the adapter reads `/notes?forum=...` to inspect public decision replies.

Unknown/submitted records are exportable as `OPENREVIEW_SUBMISSION`. Rejected public manuscripts are exportable as `REJECTED_OPENREVIEW`. Both use `@misc` with an explicit warning and are never labeled as accepted proceedings. Withdrawn records remain non-citable.

OpenReview may return HTTP 403 `ChallengeRequiredError` for an anonymous API session even while the same page opens in a logged-in browser. The browser and Python process do not share a session. This is not a bad paper ID and not evidence that the citation is false. The runtime does not bypass CAPTCHA or copy browser cookies. Instead, the user may run `<runtime> --configure-openreview --credentials <file>`; the command logs in through OpenReview's documented API, supports TOTP/email OTP, requests a session token for at most seven days, and stores only `OPENREVIEW_ACCESS_TOKEN` in the repository-external credential file. Authenticated OpenReview responses are not persistently cached, and only records explicitly readable by `everyone` are eligible.

If the token is absent, expired, or still challenged, the adapter records the access problem and continues through arXiv. A publisher WAF block follows the same fallback rule. Publishers with repeatable, non-API access blocks are omitted from active routing.

For an exact OpenReview forum URL, the adapter tries API v2 and then API v1 because public submissions span both API generations. If direct ID lookup is blocked, it queries the official title-search endpoint and accepts only the note whose `id`/`forum` equals the supplied forum ID; this prevents same-title submissions from different years being mixed. Disposition detection includes `venueid`, where OpenReview exposes values such as `Rejected_Submission`. A browser-verification HTML page returned with HTTP 200 is still treated as a block. When all API paths fail and arXiv has no match, the terminal status is `SOURCE_UNAVAILABLE`, not `UNVERIFIED`.

An OpenReview URL on a citation whose formal venue has another official adapter (for example IEEE) is routed as the L2 review fallback, not as a replacement L1 publication source.

AI Magazine is resolved through AAAI's official OJS archive, issue, and article pages at `ojs.aaai.org`. Some Windows Python/OpenSSL combinations return a connection-level TLS record error for this host; after that error the AAAI transport switches to the operating-system `curl` TLS stack for the same allowlisted host. Redirects remain restricted to `ojs.aaai.org`, HTTP access responses remain intact, and citation fields come from OJS meta tags.

## arXiv

Use `https://export.arxiv.org/api/query`. Public reads need no key. The official API Terms of Use currently require at most one request every three seconds across all controlled machines and only one connection at a time. The runtime uses a conservative five-second cadence, batches exact IDs through comma-delimited `id_list`, and caches results for 24 hours. A transient HTTP 429 remains inside four internal retries with 15, 30, 60, and 120-second default delays. A numeric `Retry-After` or `RateLimit-Reset` header takes priority up to five minutes. Only retry-budget exhaustion enters the source-access failure audit.

Resolve the base ID to the latest record and retain version, updated timestamp, comment, and withdrawal state. If latest is withdrawn, return `WITHDRAWN_ARXIV`; never substitute an older PDF as “latest.”

Official references:

- arXiv API terms: <https://info.arxiv.org/help/api/tou.html>
- arXiv API manual: <https://info.arxiv.org/help/api/user-manual.html>
- OpenReview API: <https://docs.openreview.net/reference/api-v2/openapi-definition>
- IEEE getting started: <https://developer.ieee.org/getting_started>
- IEEE search parameters: <https://developer.ieee.org/docs/read/Metadata_API_details>
- Springer Meta API v2: <https://dev.springernature.com/docs/api-endpoints/meta-api/>
