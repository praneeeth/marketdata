"""Encrypt users' broker credentials at rest (AES-256-GCM with key IDs for rotation).

``CREDENTIALS_MASTER_KEY`` holds one or more keys as ``<key-id>:<base64url 32 bytes>``,
comma-separated. The first key encrypts; every listed key can decrypt. To rotate: put
the new key first and restart, run ``python -m src.modules.market.brokers rotate-keys``
to re-encrypt every stored value, then remove the old key. Key ids may contain only
letters, digits, ``-`` and ``_``.

Each ciphertext is bound to a *context* (e.g. user, connection and field) through
AES-GCM associated data. Moving a stored value to another row or user makes it fail to
decrypt instead of silently handing one user's key to another.

Generate a key with ``python -m src.platform.security.credential_vault``.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENV_VAR = "CREDENTIALS_MASTER_KEY"
_VERSION = "v1"
_NONCE_BYTES = 12
_KEY_BYTES = 32
# Tokens are "."-joined, so key ids must not contain "." (or anything else unusual).
_KEY_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class VaultError(Exception):
    """Base error. Messages never include plaintext, keys or ciphertext."""


class VaultNotConfigured(VaultError):
    pass


class VaultDecryptError(VaultError):
    pass


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    # validate=True: reject stray characters instead of silently dropping them.
    return base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)


def generate_key_spec(key_id: str | None = None) -> str:
    kid = key_id or f"k{secrets.token_hex(4)}"
    return f"{kid}:{_b64e(secrets.token_bytes(_KEY_BYTES))}"


@dataclass(frozen=True, repr=False)
class CredentialVault:
    keys: Mapping[str, bytes]
    active_key_id: str

    def __post_init__(self) -> None:
        if self.active_key_id not in self.keys:
            raise VaultNotConfigured("active key is not in the key set")
        for kid, key in self.keys.items():
            if len(key) != _KEY_BYTES:
                raise VaultNotConfigured(f"key {kid!r} must be {_KEY_BYTES} bytes")

    def __repr__(self) -> str:
        return f"CredentialVault(key_ids={sorted(self.keys)!r}, active={self.active_key_id!r})"

    @classmethod
    def from_spec(cls, spec: str) -> CredentialVault:
        keys: dict[str, bytes] = {}
        order: list[str] = []
        for part in (p.strip() for p in spec.split(",")):
            if not part:
                continue
            kid, sep, encoded = part.partition(":")
            if not sep or not kid or not encoded:
                raise VaultNotConfigured(f"{ENV_VAR} entries must look like <key-id>:<base64>")
            if not _KEY_ID.match(kid):
                raise VaultNotConfigured(
                    f"key id {kid!r} may only contain letters, digits, '-' and '_'"
                )
            if kid in keys:
                raise VaultNotConfigured(f"duplicate key id {kid!r} in {ENV_VAR}")
            try:
                keys[kid] = _b64d(encoded)
            except (binascii.Error, ValueError):
                raise VaultNotConfigured(f"key {kid!r} is not valid base64") from None
            order.append(kid)
        if not order:
            raise VaultNotConfigured(f"{ENV_VAR} is not set; broker connections are disabled")
        return cls(keys=keys, active_key_id=order[0])

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CredentialVault:
        source = os.environ if env is None else env
        return cls.from_spec(source.get(ENV_VAR, ""))

    def encrypt(self, values: Mapping[str, str], *, context: str) -> str:
        nonce = secrets.token_bytes(_NONCE_BYTES)
        plaintext = json.dumps(dict(values), sort_keys=True).encode()
        aad = self._aad(self.active_key_id, context)
        ciphertext = AESGCM(self.keys[self.active_key_id]).encrypt(nonce, plaintext, aad)
        return ".".join((_VERSION, self.active_key_id, _b64e(nonce), _b64e(ciphertext)))

    def decrypt(self, token: str, *, context: str) -> dict[str, str]:
        _version, kid, nonce, ciphertext = self._split(token)
        key = self.keys.get(kid)
        if key is None:
            raise VaultDecryptError(f"no key with id {kid!r}; was it rotated out too early?")
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, self._aad(kid, context))
            values = json.loads(plaintext)
        except (InvalidTag, ValueError):
            raise VaultDecryptError("credential could not be decrypted") from None
        if not isinstance(values, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in values.items()
        ):
            raise VaultDecryptError("credential payload has the wrong shape")
        return values

    def needs_rotation(self, token: str) -> bool:
        return self._split(token)[1] != self.active_key_id

    def rotate(self, token: str, *, context: str) -> str:
        """Re-encrypt under the active key (no-op result if already current)."""
        if not self.needs_rotation(token):
            return token
        return self.encrypt(self.decrypt(token, context=context), context=context)

    @staticmethod
    def _aad(kid: str, context: str) -> bytes:
        return f"{_VERSION}|{kid}|{context}".encode()

    @staticmethod
    def _split(token: str) -> tuple[str, str, bytes, bytes]:
        parts = token.split(".") if isinstance(token, str) else []
        if len(parts) != 4 or parts[0] != _VERSION:
            raise VaultDecryptError("not a vault token")
        try:
            return parts[0], parts[1], _b64d(parts[2]), _b64d(parts[3])
        except (binascii.Error, ValueError):
            raise VaultDecryptError("not a vault token") from None


if __name__ == "__main__":  # pragma: no cover
    print(f"{ENV_VAR}={generate_key_spec()}")
