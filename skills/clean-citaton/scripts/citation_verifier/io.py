from __future__ import annotations

import json
import hashlib
import os
import stat
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import PaperHint, VerificationResult


def read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8-sig")


def write_json(path: str | Path, value: Any, protect: bool = True) -> None:
    protected_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        protect=protect,
    )


def load_hints(path: str | Path) -> list[PaperHint]:
    data = json.loads(read_text(path))
    if isinstance(data, dict):
        data = data.get("papers", data.get("items", []))
    if not isinstance(data, list):
        raise ValueError("Input JSON must be an array or an object containing a 'papers' array")
    hints = [PaperHint.from_dict(item) for item in data if isinstance(item, dict)]
    invalid = [index for index, hint in enumerate(hints) if not hint.title]
    if invalid:
        raise ValueError(f"Every paper hint needs a non-empty title; invalid indexes: {invalid}")
    return hints


def write_hints(path: str | Path, hints: list[PaperHint], mode: str) -> None:
    write_json(path, {"schema_version": "1.0", "mode": mode, "papers": [asdict(hint) for hint in hints]})


def write_audit(path: str | Path, results: list[VerificationResult], enabled_sources: list[str]) -> None:
    citable = sum(result.is_citable for result in results)
    final = sum(result.status == "FINAL" for result in results)
    source_unavailable = sum(result.status == "SOURCE_UNAVAILABLE" for result in results)
    unresolved = sum(
        not result.is_citable and result.status != "SOURCE_UNAVAILABLE"
        for result in results
    )
    write_json(
        path,
        {
            "schema_version": "1.0",
            "summary": {
                "total": len(results),
                "final": final,
                "citable": citable,
                "source_unavailable": source_unavailable,
                "unresolved": unresolved,
                "not_citable": len(results) - citable,
                "sources": enabled_sources,
            },
            "results": [result.to_dict() for result in results],
        },
    )


def protected_write_text(path: str | Path, content: str, protect: bool = True) -> None:
    """Atomically publish a program-owned snapshot and lock it read-only."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    make_writable(target, strict=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(Path(temp_name), target)
        if protect:
            make_read_only(target, strict=True)
    except Exception:
        # If replacement failed, restore protection on the old snapshot. This
        # matters on Windows because the target is temporarily made writable.
        if protect and target.exists():
            make_read_only(target)
        raise
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass


def _replace_with_retry(source: Path, target: Path) -> None:
    """Replace a snapshot despite transient Windows readers/indexers.

    Windows readers commonly share a file for reading but briefly deny delete
    access, which `os.replace` needs. Retrying preserves atomic publication and
    avoids creating a second `-final` project directory.
    """
    delays = (0.0, 0.05, 0.10, 0.20, 0.40, 0.80, 1.20, 1.50)
    last_error: PermissionError | None = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            os.replace(source, target)
            return
        except PermissionError as exc:
            last_error = exc
            # Read-only state may have been restored by a scanner between
            # attempts. Clear it again and retry the same atomic replacement.
            make_writable(target, strict=True)
    assert last_error is not None
    raise PermissionError(
        f"Could not replace program-owned output after {len(delays)} attempts: {target}. "
        "Close any application holding the file open and rerun the same project."
    ) from last_error


def make_writable(path: str | Path, *, strict: bool = False) -> None:
    target = Path(path)
    if not target.exists():
        return
    try:
        if os.name == "nt":
            # On Windows chmod maps the write bit to the FILE_ATTRIBUTE_READONLY
            # flag. Supplying a complete read/write mode is more reliable than
            # OR-ing the synthetic stat mode returned for a read-only file.
            target.chmod(stat.S_IREAD | stat.S_IWRITE)
        else:
            target.chmod(target.stat().st_mode | stat.S_IWUSR)
        if not target.stat().st_mode & stat.S_IWUSR:
            raise PermissionError(f"Could not clear read-only protection: {target}")
    except OSError:
        if strict:
            raise


def make_read_only(path: str | Path, *, strict: bool = False) -> None:
    target = Path(path)
    try:
        target.chmod(target.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    except OSError:
        if strict:
            raise


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
