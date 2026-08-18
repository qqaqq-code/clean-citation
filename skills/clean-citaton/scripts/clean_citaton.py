#!/usr/bin/env python3
"""Self-contained launcher for the Clean Citaton verification pipeline."""

from __future__ import annotations

import sys


if sys.version_info < (3, 10):
    print("error: Python 3.10 or newer is required", file=sys.stderr)
    raise SystemExit(1)

from citation_verifier.integrity import RuntimeIntegrityError, verify_runtime_integrity  # noqa: E402

try:
    verify_runtime_integrity()
except RuntimeIntegrityError as exc:
    print(f"error: {exc}", file=sys.stderr)
    raise SystemExit(3)

from citation_verifier.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
