# Security policy

## Credentials

This Skill never calls a second LLM API. The private values supported by the runtime are `IEEE_XPLORE_API_KEY`, `SPRINGER_NATURE_API_KEY`, `ELSEVIER_API_KEY`, and the optional short-lived `OPENREVIEW_ACCESS_TOKEN`.

Prefer environment variables. A user may alternatively create a repository-external ignored `KEY=VALUE` file and pass `--credentials`; never commit it. Keys are redacted before URL provenance and cache-key generation and are never written to output, logs, BibTeX, or Markdown. If a key appears in chat or version history, revoke and replace it.

`--configure-openreview` uses OpenReview's documented `/login` and MFA endpoints. It never stores the password or verification code, and requests a session token for no more than seven days. Do not copy browser cookies. Authenticated OpenReview responses bypass the persistent cache and the adapter discards any note not explicitly readable by `everyone`.

## Runtime and artifacts

Portable executables are built from tagged source in GitHub Actions. Source fallback verifies `scripts/runtime-files.sha256`; release source files and all generated artifacts are read-only. `manifest.json` hashes final artifacts. These controls prevent accidental/model edits and expose tampering, but the operating-system owner can deliberately remove file protections.

Do not edit Python or generated outputs to “fix” a citation. Change only user input or user registry configuration and rerun.

## External requests

Only structured title/identifier/venue hints are sent to routed scholarly sources. The cache persists public metadata with secret-safe URLs. The runtime does not bypass CAPTCHA, login walls, robots rules, or publisher blocks. After an official-source failure it may continue to OpenReview and arXiv, but labels each lower-authority result and retains the official failure in the audit trail.

Report vulnerabilities with a private GitHub security advisory. Never include working credentials, unpublished manuscripts, or private API responses in a public issue.
