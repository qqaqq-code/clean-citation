from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from ..models import PaperHint, PaperRecord
from ..transport import HttpTransport, UrllibTransport


DEFAULT_USER_AGENT = "clean-citaton/1.0 (official-source citation verification)"


class SourceLookupError(RuntimeError):
    """Base class for a source failure that must remain distinguishable."""


class SourceBlockedError(SourceLookupError):
    pass


class UnsupportedLookupError(SourceLookupError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compact(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


class MetadataSource(ABC):
    name: str

    def __init__(
        self,
        transport: HttpTransport | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.transport = transport or UrllibTransport(user_agent=user_agent)

    def close(self) -> None:
        return None

    @abstractmethod
    def search(self, hint: PaperHint, limit: int = 5) -> list[PaperRecord]:
        raise NotImplementedError
