"""Per-user broker sessions that cannot leak their credentials by accident.

A ``ProviderSession`` holds decrypted credentials for a short time. The host's credential
vault creates it; nothing in this package stores it. ``repr``/``str``/``format`` and
pickling never reveal a secret, so a stray log line or exception cannot leak one.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import NoReturn

_MASK = "***"


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return f"Secret({_MASK!r})"

    def __str__(self) -> str:
        return _MASK

    def __format__(self, spec: str) -> str:
        return _MASK

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self._value.encode(), other._value.encode())

    def __hash__(self) -> int:
        return hash(Secret)

    def __reduce__(self) -> NoReturn:
        raise TypeError("Secret values cannot be pickled")


@dataclass(frozen=True, repr=False)
class ProviderSession:
    """Credentials for one user's connection to one provider.

    ``credential_id`` identifies the stored connection. Caches and throttles are keyed by it
    so data fetched with one user's credential is never served to another user.
    """

    provider: str
    credential_id: str
    user_id: str
    api_key: Secret = field(default_factory=lambda: Secret(""))
    access_token: Secret = field(default_factory=lambda: Secret(""))
    extra: Mapping[str, Secret] = field(default_factory=dict)
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.credential_id:
            raise ValueError("ProviderSession needs a credential_id")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("ProviderSession.expires_at must be timezone-aware")

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at

    def __repr__(self) -> str:
        return (
            f"ProviderSession(provider={self.provider!r}, credential_id={self.credential_id!r}, "
            f"user_id={self.user_id!r})"
        )

    __str__ = __repr__
