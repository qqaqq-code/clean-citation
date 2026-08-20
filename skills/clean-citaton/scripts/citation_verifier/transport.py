from __future__ import annotations

import json
import base64
import hashlib
import os
import shutil
import socket
import ssl
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


SECRET_QUERY_KEYS = {"apikey", "api_key", "access_token", "key", "token"}
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
SENSITIVE_HEADER_KEYS = {"authorization", "cookie", "proxy-authorization", "x-api-key"}
RATE_LIMIT_INITIAL_DELAY = 15.0
MAX_RETRY_DELAY = 300.0


class HttpTransportError(RuntimeError):
    """A secret-safe HTTP failure."""


@dataclass(slots=True)
class HttpResponse:
    status_code: int
    content: bytes
    headers: dict[str, str]
    url: str

    def json(self) -> Any:
        try:
            return json.loads(self.content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpTransportError(f"Invalid JSON response from {self.url}") from exc

    def raise_for_status(self) -> None:
        if not 200 <= self.status_code < 300:
            raise HttpTransportError(f"HTTP {self.status_code} from {self.url}")


class HttpTransport(Protocol):
    def get(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse: ...


class UrllibTransport:
    """Small standard-library HTTP transport with bounded, polite retries."""

    def __init__(
        self,
        user_agent: str,
        timeout: float = 30.0,
        retries: int = 1,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = max(0, retries)

    def get(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        full_url = _append_query(url, params)
        safe_url = redact_url(full_url)
        request_headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        request_headers.update(dict(headers or {}))
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            try:
                response = self._request(full_url, safe_url, request_headers)
            except (URLError, TimeoutError, socket.timeout, ssl.SSLError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    reason = getattr(exc, "reason", exc)
                    raise HttpTransportError(f"Request failed for {safe_url}: {reason}") from exc
                time.sleep(1.5 * (attempt + 1))
                continue

            if response.status_code in RETRYABLE_STATUS and attempt < self.retries:
                time.sleep(_retry_delay(response, attempt))
                continue
            return response

        raise HttpTransportError(f"Request failed for {safe_url}: {last_error}")

    def _request(self, full_url: str, safe_url: str, headers: Mapping[str, str]) -> HttpResponse:
        request = Request(full_url, headers=dict(headers), method="GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return HttpResponse(
                    status_code=response.status,
                    content=response.read(),
                    headers={key.lower(): value for key, value in response.headers.items()},
                    url=redact_url(response.geturl()),
                )
        except HTTPError as exc:
            return HttpResponse(
                status_code=exc.code,
                content=exc.read(),
                headers={key.lower(): value for key, value in exc.headers.items()},
                url=safe_url,
            )


class HostTlsFallbackTransport:
    """Retry approved hosts through the operating-system curl TLS stack.

    The bridge activates only after the standard-library transport raises a
    connection/TLS error. HTTP responses, including access blocks, are
    returned unchanged and therefore never bypass publisher controls.
    """

    def __init__(
        self,
        inner: HttpTransport,
        allowed_hosts: set[str],
        user_agent: str,
        timeout: float = 30.0,
        curl_path: str | None = None,
    ) -> None:
        self.inner = inner
        self.allowed_hosts = {host.casefold() for host in allowed_hosts}
        self.user_agent = user_agent
        self.timeout = timeout
        self.curl_path = curl_path
        self._fallback_hosts: set[str] = set()

    def get(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        full_url = _append_query(url, params)
        host = (urlsplit(full_url).hostname or "").casefold()
        request_headers = dict(headers or {})
        has_sensitive_headers = any(
            key.casefold() in SENSITIVE_HEADER_KEYS for key in request_headers
        )
        if host in self._fallback_hosts and not has_sensitive_headers:
            return self._curl_get(full_url, request_headers)
        try:
            return self.inner.get(url, params=params, headers=headers)
        except HttpTransportError as primary_error:
            if host not in self.allowed_hosts:
                raise
            if has_sensitive_headers:
                raise
            try:
                response = self._curl_get(full_url, request_headers)
                self._fallback_hosts.add(host)
                return response
            except HttpTransportError as fallback_error:
                raise HttpTransportError(
                    f"Primary and OS TLS transports failed for {redact_url(full_url)}: "
                    f"{primary_error}; {fallback_error}"
                ) from fallback_error

    def _curl_get(self, full_url: str, headers: Mapping[str, str]) -> HttpResponse:
        executable = self.curl_path or shutil.which("curl.exe" if os.name == "nt" else "curl")
        if not executable:
            raise HttpTransportError("OS curl TLS bridge is unavailable")

        with tempfile.TemporaryDirectory(prefix="clean-citaton-tls-") as directory:
            body_path = Path(directory) / "body.bin"
            header_path = Path(directory) / "headers.txt"
            command = [
                executable,
                "--silent",
                "--show-error",
                "--location",
                "--max-time",
                str(max(1.0, self.timeout)),
                "--output",
                str(body_path),
                "--dump-header",
                str(header_path),
                "--write-out",
                "\n%{http_code}\n%{url_effective}",
                "--user-agent",
                self.user_agent,
            ]
            for key, value in headers.items():
                if "\r" in key or "\n" in key or "\r" in value or "\n" in value:
                    raise HttpTransportError("Unsafe HTTP header in OS TLS bridge request")
                command.extend(("--header", f"{key}: {value}"))
            command.append(full_url)

            creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout + 5.0,
                    check=False,
                    creationflags=creation_flags,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise HttpTransportError(f"OS curl TLS bridge failed: {exc}") from exc
            if completed.returncode:
                detail = " ".join(completed.stderr.split())[:300]
                raise HttpTransportError(
                    f"OS curl TLS bridge exited with code {completed.returncode}: {detail}"
                )

            lines = completed.stdout.rstrip("\r\n").splitlines()
            if len(lines) < 2 or not lines[-2].isdigit():
                raise HttpTransportError("OS curl TLS bridge returned malformed status metadata")
            status_code = int(lines[-2])
            effective_url = lines[-1].strip()
            effective_host = (urlsplit(effective_url).hostname or "").casefold()
            if effective_host not in self.allowed_hosts:
                raise HttpTransportError(
                    f"OS curl TLS bridge redirected outside the approved host: {redact_url(effective_url)}"
                )
            try:
                content = body_path.read_bytes()
                response_headers = _last_header_block(header_path.read_text(encoding="iso-8859-1"))
            except OSError as exc:
                raise HttpTransportError("OS curl TLS bridge produced incomplete response files") from exc
            return HttpResponse(
                status_code=status_code,
                content=content,
                headers=response_headers,
                url=redact_url(effective_url),
            )


class CachedTransport:
    """Secret-safe persistent cache with conditional revalidation.

    Cache files are program-owned and made read-only after each atomic write.
    API keys are removed before deriving the key or persisting provenance.
    """

    def __init__(
        self,
        inner: HttpTransport,
        cache_dir: str | Path,
        ttl_seconds: float,
    ) -> None:
        self.inner = inner
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = max(0.0, ttl_seconds)

    def get(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        safe_url, path = self._cache_location(url, params)
        cached = self._load(path)
        now = time.time()
        if cached and now - float(cached.get("stored_at", 0)) <= self.ttl_seconds:
            return _cached_response(cached)

        request_headers = dict(headers or {})
        if cached:
            if cached.get("etag"):
                request_headers["If-None-Match"] = str(cached["etag"])
            if cached.get("last_modified"):
                request_headers["If-Modified-Since"] = str(cached["last_modified"])
        response = self.inner.get(url, params=params, headers=request_headers)
        if response.status_code == 304 and cached:
            cached["stored_at"] = now
            self._save(path, cached)
            return _cached_response(cached)
        if 200 <= response.status_code < 300:
            payload = {
                "schema_version": "1.0",
                "safe_url": safe_url,
                "response_url": response.url,
                "status_code": response.status_code,
                "content_base64": base64.b64encode(response.content).decode("ascii"),
                "headers": {
                    "content-type": response.headers.get("content-type", ""),
                },
                "etag": response.headers.get("etag"),
                "last_modified": response.headers.get("last-modified"),
                "stored_at": now,
                "raw_payload_hash": hashlib.sha256(response.content).hexdigest(),
            }
            self._save(path, payload)
        return response

    def get_cached(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
    ) -> HttpResponse | None:
        """Return a fresh entry without network access or rate-gate delay."""
        _, path = self._cache_location(url, params)
        cached = self._load(path)
        if cached and time.time() - float(cached.get("stored_at", 0)) <= self.ttl_seconds:
            return _cached_response(cached)
        return None

    def _cache_location(
        self,
        url: str,
        params: Mapping[str, Any] | None,
    ) -> tuple[str, Path]:
        safe_url = redact_url(_append_query(url, params))
        cache_key = hashlib.sha256(safe_url.encode("utf-8")).hexdigest()
        return safe_url, self.cache_dir / f"{cache_key}.json"

    @staticmethod
    def _load(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def _save(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _make_writable(path)
        content = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        descriptor, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
            _make_read_only(path)
        finally:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, "***" if key.casefold() in SECRET_QUERY_KEYS else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _append_query(url: str, params: Mapping[str, Any] | None) -> str:
    if not params:
        return url
    parts = urlsplit(url)
    existing = parse_qsl(parts.query, keep_blank_values=True)
    added = [(key, value) for key, value in params.items() if value is not None]
    query = urlencode([*existing, *added], doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _last_header_block(value: str) -> dict[str, str]:
    blocks = re_split_headers(value)
    for block in reversed(blocks):
        lines = [line for line in block.splitlines() if line]
        if lines and lines[0].startswith("HTTP/"):
            headers: dict[str, str] = {}
            for line in lines[1:]:
                if ":" in line:
                    key, item = line.split(":", 1)
                    headers[key.strip().casefold()] = item.strip()
            return headers
    return {}


def re_split_headers(value: str) -> list[str]:
    """Split curl's header dump without importing a full MIME parser."""
    return [part.strip("\r\n") for part in value.replace("\r\n", "\n").split("\n\n")]


def _retry_delay(response: HttpResponse, attempt: int) -> float:
    retry_after = response.headers.get("retry-after") or response.headers.get("ratelimit-reset")
    if retry_after:
        try:
            delay = float(retry_after)
            if response.headers.get("ratelimit-reset") and delay > time.time():
                delay -= time.time()
            return min(MAX_RETRY_DELAY, max(RATE_LIMIT_INITIAL_DELAY, delay))
        except ValueError:
            pass
    if response.status_code == 429:
        return min(MAX_RETRY_DELAY, RATE_LIMIT_INITIAL_DELAY * (2**attempt))
    return 1.5 * (attempt + 1)


def _cached_response(value: Mapping[str, Any]) -> HttpResponse:
    return HttpResponse(
        status_code=int(value.get("status_code") or 200),
        content=base64.b64decode(str(value.get("content_base64") or "")),
        headers={str(k): str(v) for k, v in (value.get("headers") or {}).items()},
        url=str(value.get("response_url") or value.get("safe_url") or "cached-response"),
    )


def _make_writable(path: Path) -> None:
    if not path.exists():
        return
    try:
        path.chmod(path.stat().st_mode | stat.S_IWUSR)
    except OSError:
        pass


def _make_read_only(path: Path) -> None:
    try:
        path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    except OSError:
        pass
