"""Personal access token (PAT) utilities.

A separate auth system just for the MCP endpoint, kept apart from the login JWT (modelled on BeeCount-Cloud):

- plaintext prefix ``pwmcp_`` (easy for users and secret scanners to recognise, like GitHub's ``ghp_``);
- the plaintext is returned once at creation; only sha256 (``token_hash``) is stored;
- validation uses ``hmac.compare_digest`` for a constant-time comparison, against timing attacks;
- no bcrypt/PBKDF2: the token already has 256 bits of random entropy, so unlike a password it needn't resist brute force,
  and MCP validates on every tool call, so sha256 + constant-time comparison is fast and secure enough.

Separation guarantees (a PAT only opens the MCP endpoint):
- the regular API uses JWT (auth.get_current_user); a PAT (pwmcp_ prefix) isn't a valid JWT -> rejected;
- the MCP endpoint validates PATs; a JWT without the pwmcp_ prefix -> rejected.
"""

import hashlib
import hmac
import secrets

PAT_PREFIX = "pwmcp_"
PAT_RANDOM_BYTES = 32          # about 43 characters after token_urlsafe, 256 bits of entropy
PAT_DISPLAY_PREFIX_LEN = 14    # first 14 plaintext characters, e.g. pwmcp_a1b2c3d4

# MCP scope (read-only)
SCOPE_MCP_READ = "mcp:read"


def hash_token(token: str) -> str:
    """sha256 hex digest."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_pat() -> tuple[str, str, str]:
    """Generate a PAT.

    Returns:
        (plaintext, token_hash, display_prefix); plaintext is returned only at creation.
    """
    raw = secrets.token_urlsafe(PAT_RANDOM_BYTES)
    plaintext = f"{PAT_PREFIX}{raw}"
    return plaintext, hash_token(plaintext), plaintext[:PAT_DISPLAY_PREFIX_LEN]


def looks_like_pat(token: str) -> bool:
    """Whether a token is a PAT, by prefix (routes auth without decoding every request twice)."""
    return bool(token) and token.startswith(PAT_PREFIX)


def verify_pat_hash(provided_token: str, stored_hash: str) -> bool:
    """Constant-time comparison of a PAT's sha256, against timing attacks."""
    return hmac.compare_digest(hash_token(provided_token), stored_hash)
