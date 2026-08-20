# Runtime and file ownership

Select the first runtime that exists. Do not install Python, packages, virtual environments, or this project into the user's global environment.

| Platform | Preferred executable relative to the Skill |
|---|---|
| Windows x64 | `bin/windows-x64/clean-citaton.exe` |
| Linux x64 | `bin/linux-x64/clean-citaton` |
| macOS Apple Silicon | `bin/macos-arm64/clean-citaton` |

The recommended interface is one directory per citation job:

```text
<runtime> --project-dir <workspace>/citation-projects/<project-name> [options]
```

This reads `input/citations.json`, writes public snapshots to `results/`, and keeps implementation cache in `.cache/`. The legacy `--input <input.json> --output-dir <output>` form remains available for automation.

If no matching executable exists, use Python 3.10+ already present on the computer:

```text
python <skill>/scripts/clean_citaton.py --project-dir <project-directory>
```

On Windows try `python`, then `py -3`; on Linux/macOS try `python3`, then `python`. The launcher uses the standard library. The AAAI adapter may invoke the operating-system `curl` executable as a host-restricted TLS bridge after a Python connection-level TLS error. If neither runtime exists, ask the user to download the correct portable GitHub Release; do not install Python.

## Two-pass execution

First write the estimate without network calls:

```text
<runtime> --project-dir <project-directory> --plan-only
```

Report `run-plan.json`, then execute without `--plan-only`. Read `progress.json` for item-level progress.

For runs that can reach arXiv, allow at least 20 minutes of command execution time. The `arxiv_fallback` stage may remain quiet while the cross-process request gate or a server-directed backoff is active. Keep waiting while the process remains active. A transient HTTP 429 stays inside the runtime retry loop and becomes a source-access failure only after four retries are exhausted.

Useful options:

```text
--credentials <ignored-local-file>
--source-config <user-registry-overlay.json>
--show-config
--configure-openreview
--cache-dir <program-cache-directory>
--no-cache
```

When an anonymous OpenReview exact-ID request returns `ChallengeRequiredError`, create or renew its official short-lived session token interactively:

```text
<runtime> --configure-openreview --credentials <repository-external-credentials-file>
```

The password and MFA code are not echoed or stored. Only `OPENREVIEW_ACCESS_TOKEN` is written, for use with public (`readers: everyone`) citation records.

## Ownership

The runtime verifies `scripts/runtime-files.sha256` when using source. Release bundles mark all `.py` files read-only. Do not unlock or edit them during a Skill invocation.

All output snapshots and cache entries are atomically replaced and then marked read-only. On Windows, rerunning the same project temporarily clears the runtime-owned target's read-only attribute and retries transient sharing violations before publishing and locking the new snapshot. Do not create a `-final` project merely because outputs already exist. `manifest.json` records SHA-256 values. The owner of the computer can deliberately remove filesystem protections, so this is strong accidental/tamper detection, not a claim that the OS owner is cryptographically unable to alter a file.
