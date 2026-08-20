# Contributing

Runtime users configure sources through JSON and credentials; they do not edit Python. Maintainers changing the bundled runtime work in a development checkout, review captured response samples, regenerate `scripts/runtime-files.sha256`, and restore read-only attributes before release.

Keep these trust boundaries:

1. No second-model extraction API.
2. No Crossref authority or fallback.
3. One source owns all core canonical fields; no multi-source voting.
4. Official source failure is distinct from official record absence; retain it in the audit while continuing the fixed fallback chain.
5. OpenReview stays after non-native official sources and preserves accepted, submitted, rejected, and withdrawn states.
6. arXiv stays last, single-connection, and at least five seconds between requests; transient HTTP 429 responses use the bounded internal backoff policy.
7. Core runtime dependencies stay inside the Python standard library; the approved AAAI TLS bridge may call the operating-system `curl` executable after a Python TLS connection failure.

Before a pull request, validate the Skill metadata and runtime configuration:

```powershell
$codexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
$validator = Join-Path $codexRoot "skills\.system\skill-creator\scripts\quick_validate.py"
python $validator skills/clean-citaton
python skills/clean-citaton/scripts/clean_citaton.py --show-config
```

Review every source change with local captured responses. Do not use live keys or commit private responses. Document the endpoint, authentication, rate limits, authority role, cache policy, fallback behavior, and whether the source is official or an aggregator.
