from __future__ import annotations

import hashlib
import sys
from pathlib import Path


class RuntimeIntegrityError(RuntimeError):
    pass


def verify_runtime_integrity() -> None:
    """Reject locally modified source runtimes; frozen executables are sealed."""
    if getattr(sys, "frozen", False):
        return
    scripts_dir = Path(__file__).resolve().parents[1]
    manifest = scripts_dir / "runtime-files.sha256"
    if not manifest.exists():
        raise RuntimeIntegrityError(
            "runtime-files.sha256 is missing; reinstall the Skill from a trusted release"
        )
    for line_number, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            expected, relative = line.split("  ", 1)
        except ValueError as exc:
            raise RuntimeIntegrityError(f"invalid runtime manifest line {line_number}") from exc
        path = scripts_dir / relative
        if not path.is_file():
            raise RuntimeIntegrityError(f"runtime file is missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeIntegrityError(
                f"runtime file was modified: {relative}; reinstall or use the bundled executable"
            )
