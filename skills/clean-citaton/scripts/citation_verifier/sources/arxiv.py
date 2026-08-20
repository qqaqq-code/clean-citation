from __future__ import annotations

import os
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..models import Author, PaperHint, PaperRecord
from ..normalization import normalize_arxiv_id
from ..transport import HttpTransport
from .base import DEFAULT_USER_AGENT, MetadataSource, compact, utc_now


ATOM = "http://www.w3.org/2005/Atom"
ARXIV = "http://arxiv.org/schemas/atom"


class ArxivSource(MetadataSource):
    name = "arxiv"
    base_url = "https://export.arxiv.org/api/query"

    def __init__(
        self,
        transport: HttpTransport | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        min_interval: float = 5.0,
        rate_state_path: str | Path | None = None,
    ) -> None:
        super().__init__(transport=transport, user_agent=user_agent)
        self.min_interval = max(0.0, min_interval)
        self._last_request = 0.0
        self._prefetched: dict[str, PaperRecord] = {}
        self.rate_state_path = Path(rate_state_path) if rate_state_path else None

    def prefetch_ids(self, hints: list[PaperHint], batch_size: int = 50) -> None:
        """Batch exact IDs; arXiv permits a comma-delimited id_list."""
        ids = list(
            dict.fromkeys(
                normalize_arxiv_id(hint.arxiv_id)
                for hint in hints
                if hint.arxiv_id and normalize_arxiv_id(hint.arxiv_id)
            )
        )
        for start in range(0, len(ids), max(1, batch_size)):
            chunk = ids[start : start + max(1, batch_size)]
            response = self._query(
                {"id_list": ",".join(chunk), "start": 0, "max_results": len(chunk)}
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for entry in root.findall(f"{{{ATOM}}}entry"):
                record = self._parse(entry)
                if record.arxiv_id:
                    self._prefetched[record.arxiv_id] = record

    def search(self, hint: PaperHint, limit: int = 5) -> list[PaperRecord]:
        normalized_id = normalize_arxiv_id(hint.arxiv_id) if hint.arxiv_id else None
        if normalized_id and normalized_id in self._prefetched:
            return [self._prefetched[normalized_id]]
        params: dict[str, object] = {"start": 0, "max_results": max(1, min(limit, 20))}
        if hint.arxiv_id:
            params["id_list"] = normalize_arxiv_id(hint.arxiv_id)
        else:
            safe_title = re.sub(r'["\\]', " ", hint.title)
            params["search_query"] = f'ti:"{safe_title}"'
        response = self._query(params)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        return [self._parse(entry) for entry in root.findall(f"{{{ATOM}}}entry")]

    def _respect_rate_limit(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if self._last_request and wait > 0:
            time.sleep(wait)

    def _query(self, params: dict[str, object]):
        get_cached = getattr(self.transport, "get_cached", None)
        if get_cached:
            cached = get_cached(self.base_url, params=params)
            if cached is not None:
                return cached
        with self._cross_process_gate():
            if get_cached:
                cached = get_cached(self.base_url, params=params)
                if cached is not None:
                    return cached
            self._respect_rate_limit()
            response = self.transport.get(
                self.base_url,
                params=params,
                headers={"Accept": "application/atom+xml"},
            )
            self._last_request = time.monotonic()
            return response

    @contextmanager
    def _cross_process_gate(self) -> Iterator[None]:
        """Enforce arXiv's one-connection/three-second rule across processes."""
        if self.rate_state_path is None:
            yield
            return
        state_path = self.rate_state_path
        state_path.parent.mkdir(parents=True, exist_ok=True)

        # Keep the byte-range lock on a stable, unbuffered file.  The previous
        # implementation truncated and rewrote the same buffered file while a
        # Windows msvcrt byte lock was active.  On the second request the C
        # descriptor position could differ from Python's buffered position, so
        # LK_UNLCK targeted a different byte and raised PermissionError.
        lock_path = state_path.with_name(state_path.name + ".lock")
        with lock_path.open("a+b", buffering=0) as lock_handle:
            if lock_handle.seek(0, os.SEEK_END) == 0:
                lock_handle.write(b"\0")
                lock_handle.flush()
            _lock_file(lock_handle)
            try:
                try:
                    previous = float(state_path.read_text(encoding="ascii").strip() or 0)
                except (OSError, ValueError):
                    previous = 0.0
                wait = self.min_interval - (time.time() - previous)
                if previous and wait > 0:
                    time.sleep(wait)
                yield
            finally:
                try:
                    _write_rate_state(state_path, time.time())
                finally:
                    _unlock_file(lock_handle)

    def _parse(self, entry: ET.Element) -> PaperRecord:
        identifier_url = _text(entry, f"{{{ATOM}}}id") or ""
        arxiv_id = normalize_arxiv_id(identifier_url)
        published = _text(entry, f"{{{ATOM}}}published")
        updated = _text(entry, f"{{{ATOM}}}updated")
        doi = _text(entry, f"{{{ARXIV}}}doi")
        comment = _text(entry, f"{{{ARXIV}}}comment")
        categories = [node.attrib.get("term", "") for node in entry.findall(f"{{{ATOM}}}category")]
        journal_ref = _text(entry, f"{{{ARXIV}}}journal_ref")
        version_match = re.search(r"(v\d+)(?:$|[?#])", identifier_url)
        title = compact(_text(entry, f"{{{ATOM}}}title")) or ""
        abstract = compact(_text(entry, f"{{{ATOM}}}summary"))
        withdrawal_text = " ".join(value for value in (title, abstract, comment) if value).casefold()
        withdrawn = "withdrawn" in withdrawal_text or "withdrawal" in withdrawal_text
        return PaperRecord(
            title=title,
            authors=[
                Author(literal=compact(_text(author, f"{{{ATOM}}}name")) or "")
                for author in entry.findall(f"{{{ATOM}}}author")
            ],
            year=int(published[:4]) if published else None,
            source=self.name,
            source_id=arxiv_id or identifier_url,
            source_url=f"https://export.arxiv.org/api/query?id_list={arxiv_id}" if arxiv_id else self.base_url,
            retrieved_at=utc_now(),
            doi=doi,
            arxiv_id=arxiv_id,
            url=identifier_url or None,
            abstract=abstract,
            venue=journal_ref,
            entry_type="posted-content",
            published=published,
            categories=[category for category in categories if category],
            extra={
                "primary_category": categories[0] if categories else None,
                "version": version_match.group(1) if version_match else None,
                "versioned_id": f"{arxiv_id}{version_match.group(1)}" if arxiv_id and version_match else arxiv_id,
                "updated": updated,
                "comment": comment,
                "withdrawn": withdrawn,
            },
        )


def _text(parent: ET.Element, path: str) -> str | None:
    node = parent.find(path)
    return node.text.strip() if node is not None and node.text else None


def _lock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(handle.fileno(), 0, os.SEEK_SET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(handle.fileno(), 0, os.SEEK_SET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_rate_state(path: Path, timestamp: float) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as handle:
            handle.write(f"{timestamp:.6f}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass
