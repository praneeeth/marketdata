"""CredentialVault: AES-GCM round trips, context binding, key rotation, no leaks."""

from __future__ import annotations

import logging

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.platform.security.credential_vault import (
    ENV_VAR,
    CredentialVault,
    VaultDecryptError,
    VaultNotConfigured,
    generate_key_spec,
)

SECRET = "api_secret_NEVER_PRINT_ME"
CTX = "local|conn-1|credentials"


@pytest.fixture
def vault() -> CredentialVault:
    return CredentialVault.from_spec(generate_key_spec("k1"))


def test_round_trip(vault: CredentialVault) -> None:
    token = vault.encrypt({"api_key": "k", "api_secret": SECRET}, context=CTX)
    assert SECRET not in token
    assert token.startswith("v1.k1.")
    assert vault.decrypt(token, context=CTX) == {"api_key": "k", "api_secret": SECRET}


def test_nonces_differ(vault: CredentialVault) -> None:
    assert vault.encrypt({"a": "b"}, context=CTX) != vault.encrypt({"a": "b"}, context=CTX)


@given(st.dictionaries(st.text(), st.text(), max_size=5), st.text(max_size=40))
def test_round_trip_property(values: dict[str, str], context: str) -> None:
    v = CredentialVault.from_spec(generate_key_spec("k1"))
    assert v.decrypt(v.encrypt(values, context=context), context=context) == values


def test_context_binding_blocks_row_swaps(vault: CredentialVault) -> None:
    token = vault.encrypt({"api_secret": SECRET}, context="alice|conn-1|credentials")
    with pytest.raises(VaultDecryptError):
        vault.decrypt(token, context="bob|conn-2|credentials")


def test_tampering_is_detected(vault: CredentialVault) -> None:
    token = vault.encrypt({"api_secret": SECRET}, context=CTX)
    version, kid, nonce, ct = token.split(".")
    flipped = ct[:-2] + ("A" if ct[-2] != "A" else "B") + ct[-1]
    with pytest.raises(VaultDecryptError):
        vault.decrypt(".".join((version, kid, nonce, flipped)), context=CTX)


@pytest.mark.parametrize("token", ["", "v1.k1.x", "v2.k1.a.b", "v1.k1.!!!.b", "plain-text"])
def test_malformed_tokens(vault: CredentialVault, token: str) -> None:
    with pytest.raises(VaultDecryptError):
        vault.decrypt(token, context=CTX)


def test_wrong_payload_shape(vault: CredentialVault) -> None:
    from src.platform.security import credential_vault as cv

    token = vault.encrypt({}, context=CTX)
    # Re-encrypt a JSON list under the same key to reach the shape check.
    nonce = cv.secrets.token_bytes(12)
    aad = vault._aad("k1", CTX)
    ct = cv.AESGCM(vault.keys["k1"]).encrypt(nonce, b"[1, 2]", aad)
    bad = ".".join(("v1", "k1", cv._b64e(nonce), cv._b64e(ct)))
    assert vault.decrypt(token, context=CTX) == {}
    with pytest.raises(VaultDecryptError, match="shape"):
        vault.decrypt(bad, context=CTX)


def test_rotation() -> None:
    old_spec = generate_key_spec("old")
    old = CredentialVault.from_spec(old_spec)
    token = old.encrypt({"api_secret": SECRET}, context=CTX)

    both = CredentialVault.from_spec(f"{generate_key_spec('new')},{old_spec}")
    assert both.needs_rotation(token)
    rotated = both.rotate(token, context=CTX)
    assert rotated.startswith("v1.new.")
    assert not both.needs_rotation(rotated)
    assert both.rotate(rotated, context=CTX) == rotated
    assert both.decrypt(rotated, context=CTX) == {"api_secret": SECRET}

    new_only = CredentialVault.from_spec(both_spec_first(both))
    with pytest.raises(VaultDecryptError, match="rotated out"):
        new_only.decrypt(token, context=CTX)


def both_spec_first(v: CredentialVault) -> str:
    from src.platform.security.credential_vault import _b64e

    return f"{v.active_key_id}:{_b64e(v.keys[v.active_key_id])}"


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        ("", "not set"),
        (" , ", "not set"),
        ("nokey", "<key-id>:<base64>"),
        ("k1:", "<key-id>:<base64>"),
        ("k1:@@@", "base64"),
        ("k1:AAAA", "32 bytes"),
    ],
)
def test_bad_specs(spec: str, match: str) -> None:
    with pytest.raises(VaultNotConfigured, match=match):
        CredentialVault.from_spec(spec)


def test_duplicate_key_ids() -> None:
    spec = generate_key_spec("k1")
    with pytest.raises(VaultNotConfigured, match="duplicate"):
        CredentialVault.from_spec(f"{spec},{spec}")


def test_active_key_must_exist() -> None:
    with pytest.raises(VaultNotConfigured, match="active key"):
        CredentialVault(keys={}, active_key_id="k1")


def test_from_env() -> None:
    assert CredentialVault.from_env({ENV_VAR: generate_key_spec("e1")}).active_key_id == "e1"
    with pytest.raises(VaultNotConfigured):
        CredentialVault.from_env({})


def test_from_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, generate_key_spec("p1"))
    assert CredentialVault.from_env().active_key_id == "p1"


def test_repr_and_errors_leak_nothing(
    vault: CredentialVault, caplog: pytest.LogCaptureFixture
) -> None:
    token = vault.encrypt({"api_secret": SECRET}, context=CTX)
    key_text = both_spec_first(vault).split(":", 1)[1]
    caplog.set_level(logging.DEBUG)
    logging.getLogger("t").info("vault=%r", vault)
    assert key_text not in repr(vault)
    assert key_text not in caplog.text
    with pytest.raises(VaultDecryptError) as exc:
        vault.decrypt(token, context="other")
    assert SECRET not in str(exc.value)
    assert token not in str(exc.value)
    assert exc.value.__suppress_context__


def test_generated_specs_are_unique() -> None:
    assert generate_key_spec() != generate_key_spec()
