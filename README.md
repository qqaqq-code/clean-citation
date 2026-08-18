# Clean Citation

Clean Citation is a citation verification Skill for Codex and also provides a standalone CLI. Its core is the bundled Python retrieval and verification runtime, with clear human-machine responsibilities: the host model interprets natural-language needs, structures raw references, prepares retrieval hints, and assists with research and manual-review candidates; the Python runtime owns every formal verification step, including first-party source retrieval, match decisions, canonical field selection, BibTeX and Markdown generation, and read-only publication. Every formal field in the final output must be supported by traceable first-party evidence.

The installed Skill identifier, directory name, and CLI command are `clean-citaton`.

## Motivation

Many citation tools expand coverage through aggregators such as Crossref and Semantic Scholar. Aggregated records may combine preprints, conference papers, journal articles, and author-profile entries for the same research output. Version merging can introduce differences in year, volume, issue, pages, author order, and publication status. Generative models can also infer plausible-looking fields when authoritative evidence is sparse, allowing citation hallucinations to enter academic writing.

Clean Citation focuses on a clean, reviewable, and reproducible Python evidence chain:

1. Official journal, publisher, and conference pages or APIs provide formal publication records.
2. OpenReview provides public submissions, review status, and venue decisions.
3. arXiv provides the latest public preprint record.
4. One selected authority owns the complete set of canonical fields, including title, author order, year, venue, DOI, volume, issue, and pages.
5. The host model structures user input as retrieval hints in `citations.json`.
6. The Python crawler retrieves official pages and APIs, then performs normalization, routing, scoring, and status classification.
7. The Python exporter generates BibTeX, Markdown, JSON audit records, and read-only manifests from the selected official record.
8. Unresolved entries enter a manual-review queue, and confirmed DOI or official URLs return to the next Python verification pass.

All runtime sources belong to the first-party authority chain. Crossref and Semantic Scholar appear only in the project motivation; formal evidence comes from official publication records, OpenReview, and arXiv.

```text
Natural-language request or raw references
  -> Host model structures citations.json retrieval hints
  -> Python crawler checks official sources, OpenReview, and arXiv
  -> Python matcher selects one authoritative record
  -> Python exporter writes references.bib, references.md, and verification.json
```

## Core Design

- **Official-first routing:** formal journal, publisher, and conference records receive the highest priority.
- **Python-owned publication:** the deterministic Python runtime produces every formal field, status, and export file.
- **Model responsibilities:** the host model prepares retrieval hints and high-confidence manual-review candidates.
- **Explicit versions:** OpenReview and arXiv retain submission and preprint identities.
- **Single-source fields:** one selected record supplies the complete canonical field set.
- **Item-level progress:** `progress.json` updates after every completed entry.
- **Traceable evidence:** `verification.json` stores candidates, scores, source URLs, access failures, and credential state.
- **Reproducible runs:** caching, fixed routing, fixed thresholds, and run plans support repeat execution.
- **Protected output:** atomic replacement, read-only attributes, and SHA-256 manifests protect program-owned files.
- **Human review loop:** unresolved entries receive high-confidence first-party candidate pages for human confirmation.

## Evidence Order

| Level | Authority role | Output status |
|---|---|---|
| L1 | Official journal, publisher, conference page, or API | `FINAL` |
| L1 | OpenReview as the venue's formal publication platform | `FINAL` |
| L2 | Other public OpenReview records | `PROVISIONAL_OPENREVIEW`, `OPENREVIEW_SUBMISSION`, `REJECTED_OPENREVIEW` |
| L3 | Latest public arXiv record | `PREPRINT_ARXIV` |

When an official source encounters credential, TLS, HTTP, or network failure, the runtime records the failure and continues through OpenReview and arXiv. Every result retains its own publication identity, while the audit record preserves upstream access details.

## Official Data Sources

| Adapter | First-party source | Access |
|---|---|---|
| `neurips` | NeurIPS Proceedings | Public |
| `pmlr` | Proceedings of Machine Learning Research | Public |
| `mlsys` | MLSys Proceedings | Public |
| `acl_anthology` | ACL Anthology | Public |
| `cvf` | CVF Open Access | Public |
| `usenix` | USENIX Proceedings | Public |
| `aaai` | AAAI OJS | Public |
| `ijcai` | IJCAI Proceedings | Public |
| `jmlr` | Journal of Machine Learning Research | Public |
| `vldb` | VLDB Endowment | Public |
| `openreview` | OpenReview API v2 and v1 | Public, optional short-lived session token |
| `arxiv` | arXiv API | Public |
| `ieee` | IEEE Xplore Metadata API | `IEEE_XPLORE_API_KEY` |
| `springer` | Springer Nature Meta API v2 | `SPRINGER_NATURE_API_KEY` |
| `elsevier` | Elsevier Article Metadata API | `ELSEVIER_API_KEY` |

Some Windows Python environments trigger an OpenSSL `record layer failure` with AAAI OJS. The runtime starts with the Python standard network stack and activates the operating-system `curl` TLS channel only for `ojs.aaai.org` after a connection-level TLS failure. Pages, redirect targets, metadata, HTTP status, and access controls remain under the AAAI official domain.

The arXiv adapter follows the legacy API access cadence: one global connection, at least 3.05 seconds between adjacent requests, exact-ID batching, and caching.

## Repository Layout

```text
.
├─ skills/
│  └─ clean-citaton/
│     ├─ SKILL.md
│     ├─ agents/openai.yaml
│     ├─ references/
│     ├─ scripts/
│     └─ bin/
├─ examples/
├─ .github/workflows/
├─ CONTRIBUTING.md
├─ SECURITY.md
└─ pyproject.toml
```

Each verification job uses a dedicated project directory:

```text
citation-projects/<project-name>/
├─ input/
│  └─ citations.json
├─ results/
│  ├─ run-plan.json
│  ├─ progress.json
│  ├─ verification.json
│  ├─ references.bib
│  ├─ references.md
│  ├─ manual-review-queue.json
│  └─ manifest.json
├─ manual-review/
│  ├─ candidates.json
│  └─ candidates.md
└─ .cache/
```

The runtime owns `results/` and `.cache/`. Users, the host model, and human reviewers own `input/` and `manual-review/`. Repeated runs reuse the same project directory. On Windows, the publisher safely unlocks runtime-owned files, replaces them atomically, and restores read-only protection.

## Install in Codex

### Windows source checkout

Run in PowerShell:

```powershell
git clone https://github.com/qqaqq-code/clean-citation.git
Set-Location clean-citation
$skillSource = (Resolve-Path ".\skills\clean-citaton").Path
$skillsRoot = Join-Path $env:USERPROFILE ".codex\skills"
New-Item -ItemType Directory -Force -Path $skillsRoot | Out-Null
New-Item -ItemType Junction -Path (Join-Path $skillsRoot "clean-citaton") -Target $skillSource
```

`$skillSource` resolves to `skills\clean-citaton` inside the actual clone location. Repository updates appear immediately through the Junction.

### macOS and Linux source checkout

Run in a terminal:

```bash
git clone https://github.com/qqaqq-code/clean-citation.git
cd clean-citation
repository_root="$(pwd)"
codex_root="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$codex_root/skills"
ln -s "$repository_root/skills/clean-citaton" "$codex_root/skills/clean-citaton"
```

Portable GitHub Release bundles contain standalone executables:

- `clean-citaton-windows-x64.zip`
- `clean-citaton-linux-x64.tar.gz`
- `clean-citaton-macos-arm64.tar.gz`

## Run

The following commands work from any research project root. `$projectDir` points to a dedicated verification directory in the current project. Save the input JSON as `$projectDir\input\citations.json`.

### Windows with Python

```powershell
$skillRoot = Join-Path $env:USERPROFILE ".codex\skills\clean-citaton"
$runtime = Join-Path $skillRoot "scripts\clean_citaton.py"
$projectDir = Join-Path (Get-Location) "citation-projects\demo"
python $runtime --project-dir $projectDir --plan-only
python $runtime --project-dir $projectDir
```

### Windows portable executable

```powershell
$skillRoot = Join-Path $env:USERPROFILE ".codex\skills\clean-citaton"
$runtime = Join-Path $skillRoot "bin\windows-x64\clean-citaton.exe"
$projectDir = Join-Path (Get-Location) "citation-projects\demo"
& $runtime --project-dir $projectDir --plan-only
& $runtime --project-dir $projectDir
```

### macOS and Linux with Python

```bash
skill_root="${CODEX_HOME:-$HOME/.codex}/skills/clean-citaton"
runtime="$skill_root/scripts/clean_citaton.py"
project_dir="$(pwd)/citation-projects/demo"
python3 "$runtime" --project-dir "$project_dir" --plan-only
python3 "$runtime" --project-dir "$project_dir"
```

The planning pass produces routing and duration estimates. The verification pass retrieves official evidence, evaluates matches, and publishes read-only results one item at a time. Public-source caching shortens repeated runs of the same project.

Input example:

```json
{
  "papers": [
    {
      "title": "Attention Is All You Need",
      "authors": ["Ashish Vaswani"],
      "year": 2017,
      "venue": "NeurIPS",
      "arxiv_id": "1706.03762",
      "original_text": "Vaswani et al. Attention Is All You Need. 2017."
    }
  ]
}
```

## Manual Review Loop

The runtime writes entries awaiting citable records to `results/manual-review-queue.json`. The host model reads this queue, visits official publisher, official conference, OpenReview, or arXiv pages, and writes high-confidence candidates to `manual-review/candidates.json` and `manual-review/candidates.md`.

Each candidate includes:

- an accessible first-party URL;
- the source name and authority role;
- the access-check time;
- title, author, year, DOI, or identifier evidence;
- a `HIGH` confidence label;
- a suggested `official_url` and DOI for the next pass.

Human-confirmed fields return to `input/citations.json`, followed by a fresh deterministic verification pass. Candidate research and formal BibTeX remain in separate directories with explicit ownership boundaries.

## Optional Credentials

The repository contains an empty variable template in `.env.example`. Personal credentials live under the user's home directory:

```text
Windows:     %USERPROFILE%\.clean-citaton\credentials.env
macOS/Linux: ~/.clean-citaton/credentials.env
```

File format:

```text
IEEE_XPLORE_API_KEY=
SPRINGER_NATURE_API_KEY=
ELSEVIER_API_KEY=
OPENREVIEW_ACCESS_TOKEN=
```

Environment variables have higher priority. `--show-config` reports only `configured`, `missing`, and `public mode` states.

```powershell
$runtime = Join-Path $env:USERPROFILE ".codex\skills\clean-citaton\scripts\clean_citaton.py"
python $runtime --show-config
```

When an anonymous OpenReview request returns `ChallengeRequiredError`, the official login flow can create a session token for up to seven days:

```powershell
$runtime = Join-Path $env:USERPROFILE ".codex\skills\clean-citaton\scripts\clean_citaton.py"
python $runtime --configure-openreview
```

The flow supports MFA and stores only the session token in the credential file. IEEE, Springer Nature, and Elsevier developer platforms manage application status and request quotas.

## Extend Venues and Journals

User configuration can map additional venues to existing adapters while the Python runtime remains in its released state. Example configuration:

```json
{
  "venues": [
    {
      "venue": "Example IEEE Conference",
      "aliases": ["EIC"],
      "official": [
        {
          "adapter": "ieee",
          "role": "official_publication",
          "credential": "IEEE_XPLORE_API_KEY"
        }
      ],
      "fallback": ["openreview", "arxiv"]
    }
  ]
}
```

Run with an overlay:

```powershell
$runtime = Join-Path $env:USERPROFILE ".codex\skills\clean-citaton\scripts\clean_citaton.py"
$projectDir = Join-Path (Get-Location) "citation-projects\demo"
python $runtime --project-dir $projectDir --source-config ".\my-sources.json"
```

Maintainers implement new official API adapters on a development branch together with captured response samples, rate limits, cache policy, credential redaction, and authority-role documentation.

## Result Statuses

| Status | Meaning | BibTeX behavior |
|---|---|---|
| `FINAL` | Formal authoritative record | Exported |
| `PROVISIONAL_OPENREVIEW` | Accepted OpenReview record for a venue with another native publication source | Exported with a label |
| `OPENREVIEW_SUBMISSION` | Public submission record | Exported as `@misc` |
| `REJECTED_OPENREVIEW` | Public rejected submission | Exported as labeled `@misc` |
| `PREPRINT_ARXIV` | Latest arXiv preprint | Exported with a label |
| `SOURCE_UNAVAILABLE` | Upstream access failure followed by zero fallback matches | Sent to manual review |
| `UNVERIFIED` | All queries completed with zero reliable matches | Sent to manual review |
| `AMBIGUOUS` | Multiple candidates received similar scores | Sent to manual review |
| `WITHDRAWN_*` | Official withdrawal state | Retained in the audit record |

Exit code `0` means every entry reached `FINAL`. Exit code `2` means the published results contain another status. Exit code `3` means runtime integrity validation triggered.

## Project Integrity

The project uses runtime hashes, read-only publication, cache redaction, Skill structure validation, and GitHub Actions cross-platform builds.

Further design details are available in [Data Sources](skills/clean-citaton/references/data-sources.md), [Runtime](skills/clean-citaton/references/runtime.md), [Schemas](skills/clean-citaton/references/schemas.md), and [Manual Review](skills/clean-citaton/references/manual-review.md).

## License

MIT License
