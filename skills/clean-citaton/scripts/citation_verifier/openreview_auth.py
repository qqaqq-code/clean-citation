from __future__ import annotations

import getpass
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_BASE_URL = "https://api2.openreview.net"
TOKEN_NAME = "OPENREVIEW_ACCESS_TOKEN"
TOKEN_LIFETIME_SECONDS = 7 * 24 * 60 * 60


class OpenReviewLoginError(RuntimeError):
    """A login failure that never contains a password or session token."""


def configure_openreview_token(
    credentials_path: str | Path,
    *,
    timeout: float = 30.0,
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = getpass.getpass,
    post_json: Callable[[str, dict[str, Any], float], dict[str, Any]] | None = None,
) -> Path:
    """Log in through OpenReview's official API and save only its short-lived token."""
    email = input_fn("OpenReview confirmed email: ").strip()
    if not email:
        raise OpenReviewLoginError("OpenReview email cannot be empty")
    password = secret_fn("OpenReview password (not stored): ")
    if not password:
        raise OpenReviewLoginError("OpenReview password cannot be empty")
    sender = post_json or _post_json
    response = sender(
        f"{API_BASE_URL}/login",
        {"id": email, "password": password, "expiresIn": TOKEN_LIFETIME_SECONDS},
        timeout,
    )
    password = ""
    if response.get("mfaPending"):
        response = _complete_mfa(response, timeout, secret_fn, sender)
    token = str(response.get("token") or "").strip()
    if not token:
        raise OpenReviewLoginError("OpenReview login succeeded without returning a session token")
    path = Path(credentials_path).expanduser().resolve()
    _upsert_credential(path, TOKEN_NAME, token)
    token = ""
    return path


def _complete_mfa(
    response: dict[str, Any],
    timeout: float,
    secret_fn: Callable[[str], str],
    sender: Callable[[str, dict[str, Any], float], dict[str, Any]],
) -> dict[str, Any]:
    pending_token = str(response.get("mfaPendingToken") or "")
    methods = [str(value) for value in (response.get("mfaMethods") or [])]
    preferred = str(response.get("preferredMethod") or "")
    supported = [method for method in methods if method in {"totp", "emailOtp"}]
    method = preferred if preferred in supported else (supported[0] if supported else "")
    if not pending_token or not method:
        raise OpenReviewLoginError(
            "This account requires an unsupported MFA method; use the official openreview-py client"
        )
    if method == "emailOtp":
        sender(
            f"{API_BASE_URL}/mfa/challenge",
            {"mfaPendingToken": pending_token, "method": method},
            timeout,
        )
        prompt = "OpenReview email verification code: "
    else:
        prompt = "OpenReview authenticator code: "
    code = secret_fn(prompt).strip()
    if not code:
        raise OpenReviewLoginError("OpenReview MFA code cannot be empty")
    return sender(
        f"{API_BASE_URL}/mfa/verify",
        {"mfaPendingToken": pending_token, "method": method, "code": code},
        timeout,
    )


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "clean-citaton/1.0 (OpenReview official API login)",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8-sig"))
    except HTTPError as exc:
        result = _error_payload(exc.read())
        name = str(result.get("name") or f"HTTP {exc.code}")
        message = str(result.get("message") or "OpenReview login failed")
        raise OpenReviewLoginError(f"{name}: {message}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise OpenReviewLoginError(f"OpenReview login request failed: {reason}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenReviewLoginError("OpenReview login returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise OpenReviewLoginError("OpenReview login returned an invalid response")
    return result


def _error_payload(content: bytes) -> dict[str, Any]:
    try:
        result = json.loads(content.decode("utf-8-sig"))
        return result if isinstance(result, dict) else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _upsert_credential(path: Path, name: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    output: list[str] = []
    replaced = False
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key == name:
            if not replaced:
                output.append(f"{name}={value}")
                replaced = True
            continue
        output.append(line)
    if not replaced:
        if output and output[-1].strip():
            output.append("")
        output.append(f"{name}={value}")
    descriptor, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(output) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp_name, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass
