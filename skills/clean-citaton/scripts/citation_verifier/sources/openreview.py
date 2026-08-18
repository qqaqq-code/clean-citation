from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..models import Author, PaperHint, PaperRecord
from ..transport import HttpTransport
from .base import DEFAULT_USER_AGENT, MetadataSource, SourceBlockedError, compact, utc_now


class OpenReviewSource(MetadataSource):
    name = "openreview"
    base_urls = ("https://api2.openreview.net", "https://api.openreview.net")

    def __init__(
        self,
        transport: HttpTransport | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        min_interval: float | None = None,
        access_token: str | None = None,
    ) -> None:
        super().__init__(transport=transport, user_agent=user_agent)
        token = (access_token or "").strip()
        if token.casefold().startswith("bearer "):
            token = token[7:].strip()
        self._access_token = token or None
        self._request_headers = (
            {"Authorization": f"Bearer {self._access_token}"}
            if self._access_token
            else None
        )
        if min_interval is None:
            self._min_intervals = {
                "https://api2.openreview.net": 0.75,
                "https://api.openreview.net": 12.1,
            }
        else:
            interval = max(0.0, min_interval)
            self._min_intervals = {base_url: interval for base_url in self.base_urls}
        self._last_requests = {base_url: 0.0 for base_url in self.base_urls}

    def search(self, hint: PaperHint, limit: int = 5) -> list[PaperRecord]:
        forum_id = _forum_id(hint.official_url)
        if forum_id:
            requests = [("/notes", {"id": forum_id, "limit": max(1, min(limit, 20))})]
            requests.extend(
                (
                    "/notes/search",
                    {
                        "term": title,
                        "type": "exact",
                        "content": "title",
                        "source": "forum",
                        "limit": max(1, min(limit, 20)),
                    },
                )
                for title in _title_variants(hint.title)
            )
        else:
            requests = [(
                "/notes/search",
                {
                    "term": hint.title,
                    "type": "exact",
                    "content": "title",
                    "source": "forum",
                    "limit": max(1, min(limit, 20)),
                },
            )]
        records: list[PaperRecord] = []
        seen: set[str] = set()
        errors: list[tuple[str, Exception]] = []
        for endpoint, params in requests:
            for base_url in self._bases_for_hint(hint):
                try:
                    notes = self._get_notes(base_url, endpoint, params)
                    for note in notes:
                        if not _content(note, "title") or _is_imported_profile_record(note):
                            continue
                        if forum_id and endpoint == "/notes/search" and not _same_forum(note, forum_id):
                            # A title can have several yearly submissions. The
                            # user-supplied forum ID remains the exact identity.
                            continue
                        disposition, decision_error = self._resolved_disposition(note, base_url)
                        record = self._parse(note, base_url, disposition, decision_error)
                        key = record.source_id or record.url or record.title
                        if key not in seen:
                            seen.add(key)
                            records.append(record)
                    if forum_id and records:
                        # An exact forum ID is globally unique. Do not query
                        # another API generation after it has been recovered.
                        return records
                except Exception as exc:
                    errors.append((base_url, exc))
        if not records and errors:
            detail = "; ".join(
                f"{_api_label(base_url)}: {type(exc).__name__}: {exc}"
                for base_url, exc in errors
            )
            if all(isinstance(exc, SourceBlockedError) for _, exc in errors):
                raise SourceBlockedError(detail)
            raise RuntimeError(detail)
        return records

    def _get_notes(
        self,
        base_url: str,
        endpoint: str,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        self._respect_rate_limit(base_url)
        try:
            response = self.transport.get(
                f"{base_url}{endpoint}",
                params=params,
                headers=self._request_headers,
            )
        finally:
            # Pace the next request even when this one is blocked or interrupted.
            self._last_requests[base_url] = time.monotonic()
        if response.status_code in {401, 403}:
            raise SourceBlockedError(self._access_error(response))
        response.raise_for_status()
        preview = response.content[:8192].decode("utf-8", errors="ignore").casefold()
        if any(
            marker in preview
            for marker in (
                "verifying your browser",
                "enable javascript and cookies to continue",
                "challenge-platform",
            )
        ):
            raise SourceBlockedError(
                "OpenReview returned an interactive browser-verification page instead of API JSON"
            )
        notes = response.json().get("notes", [])
        if not isinstance(notes, list):
            return []
        if self._access_token:
            # Authentication is used only to pass OpenReview's official API
            # challenge. Citation verification remains restricted to records
            # explicitly readable by everyone; private submissions/reviews are
            # never returned or persisted by this adapter.
            notes = [note for note in notes if _is_public_note(note)]
        return notes

    def _access_error(self, response: Any) -> str:
        name = ""
        message = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                name = str(payload.get("name") or "")
                message = str(payload.get("message") or "")
        except Exception:
            pass
        if name == "ChallengeRequiredError":
            if self._access_token:
                return (
                    "OpenReview still requires challenge verification; the configured "
                    "OPENREVIEW_ACCESS_TOKEN may be expired or rejected"
                )
            return (
                "OpenReview requires interactive challenge verification for anonymous API "
                "requests; configure an official OPENREVIEW_ACCESS_TOKEN session token"
            )
        if response.status_code == 401 and self._access_token:
            return "OpenReview rejected the configured OPENREVIEW_ACCESS_TOKEN (expired or invalid)"
        suffix = f": {message}" if message else ""
        return f"OpenReview returned HTTP {response.status_code}{suffix}"

    def _resolved_disposition(
        self,
        note: dict[str, Any],
        base_url: str,
    ) -> tuple[str, str | None]:
        disposition = _disposition(note, _content(note, "venue"))
        if disposition != "unknown":
            return disposition, None
        forum_id = str(note.get("forum") or note.get("id") or "")
        if not forum_id:
            return disposition, None
        try:
            replies = self._get_notes(
                base_url,
                "/notes",
                {"forum": forum_id, "limit": 1000},
            )
        except Exception as exc:
            # The public submission itself remains useful evidence when a
            # decision thread is temporarily blocked or unavailable.
            return disposition, f"{type(exc).__name__}: {exc}"
        return _thread_disposition(replies), None

    def _bases_for_hint(self, hint: PaperHint) -> tuple[str, ...]:
        if _forum_id(hint.official_url):
            # Exact public forum IDs can live in either API generation. Try
            # v2 first, then v1 if v2 is blocked, empty, or unavailable.
            return self.base_urls
        if hint.year is None:
            return self.base_urls
        # OpenReview's current venues are served from API v2. Legacy venue
        # records are queried from v1 only, avoiding a redundant call and the
        # v1 service's strict 5 requests/minute policy.
        if hint.year >= 2024:
            return (self.base_urls[0],)
        return (self.base_urls[1],)

    def _respect_rate_limit(self, base_url: str) -> None:
        last_request = self._last_requests[base_url]
        wait = self._min_intervals[base_url] - (time.monotonic() - last_request)
        if last_request and wait > 0:
            time.sleep(wait)

    def _parse(
        self,
        note: dict[str, Any],
        base_url: str,
        disposition: str,
        decision_error: str | None,
    ) -> PaperRecord:
        note_id = str(note.get("id") or note.get("forum") or "")
        authors = _content(note, "authors") or []
        if isinstance(authors, str):
            authors = [authors]
        publication_date = _milliseconds_date(note.get("pdate") or note.get("cdate") or note.get("tcdate"))
        venue = _content(note, "venue") or _content(note, "venueid") or _content(note, "venue_id")
        pdf = _content(note, "pdf")
        url = f"https://openreview.net/forum?id={note.get('forum') or note_id}"
        return PaperRecord(
            title=compact(_content(note, "title")) or "",
            authors=[Author(literal=compact(author) or "") for author in authors],
            year=int(publication_date[:4]) if publication_date else None,
            source=self.name,
            source_id=note_id,
            source_url=f"{base_url}/notes?id={note_id}",
            retrieved_at=utc_now(),
            openreview_id=note_id,
            url=url,
            abstract=compact(_content(note, "abstract")),
            venue=compact(venue),
            entry_type="proceedings-article",
            published=publication_date,
            extra={
                "pdf": pdf,
                "forum": note.get("forum"),
                "disposition": disposition,
                "record_kind": "public_submission",
                "decision_lookup_error": decision_error,
                "invitations": note.get("invitations") or ([note.get("invitation")] if note.get("invitation") else []),
            },
        )


def _content(note: dict[str, Any], key: str) -> Any:
    value = (note.get("content") or {}).get(key)
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _milliseconds_date(value: Any) -> str | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()


def _forum_id(value: str | None) -> str | None:
    if not value or "openreview.net" not in value.casefold():
        return None
    return (parse_qs(urlsplit(value).query).get("id") or [None])[0]


def _disposition(note: dict[str, Any], venue: Any) -> str:
    text_parts = [str(venue or "")]
    for key in ("venueid", "venue_id", "decision", "withdrawal_confirmation"):
        value = _content(note, key)
        if value:
            text_parts.append(str(value))
    text = " ".join(text_parts).casefold()
    if "withdraw" in text:
        return "withdrawn"
    if any(marker in text for marker in ("reject", "desk reject", "not accepted")):
        return "rejected"
    if any(marker in text for marker in ("accept", "poster", "oral", "spotlight", "published")):
        return "accepted"
    return "unknown"


def _thread_disposition(notes: list[dict[str, Any]]) -> str:
    dispositions = [_disposition(note, _content(note, "venue")) for note in notes]
    for value in ("withdrawn", "rejected", "accepted"):
        if value in dispositions:
            return value
    return "unknown"


def _is_imported_profile_record(note: dict[str, Any]) -> bool:
    invitations = note.get("invitations") or ([note.get("invitation")] if note.get("invitation") else [])
    text = " ".join(str(value) for value in invitations).casefold()
    return "dblp.org/-/record" in text or "openreview.net/public_article/" in text


def _same_forum(note: dict[str, Any], forum_id: str) -> bool:
    return str(note.get("id") or "") == forum_id or str(note.get("forum") or "") == forum_id


def _title_variants(title: str) -> list[str]:
    """Return conservative publisher/submission spelling variants.

    The result is used only when a globally unique forum ID was supplied and
    every search hit is checked against that ID.
    """
    variants = [title]
    words = title.split()
    if words:
        first = words[0].casefold()
        replacement = "Towards" if first == "toward" else "Toward" if first == "towards" else None
        if replacement:
            variants.append(" ".join([replacement, *words[1:]]))
    return variants


def _is_public_note(note: dict[str, Any]) -> bool:
    readers = note.get("readers")
    return isinstance(readers, list) and any(
        str(reader).casefold() == "everyone" for reader in readers
    )


def _api_label(base_url: str) -> str:
    return "API v2" if "api2." in base_url else "API v1"
