"""Encryption for credentials at rest (NFR-DP-1, FR-AC-1).

`cryptography` has been a declared dependency since the first commit and was never
used: `ChannelConfig.encrypted_credentials` holds plaintext JSON, and
`AgentWorkerAccount.password_encrypted` holds a plaintext password. The column names
described an intention, not a behaviour.

This matters more here than in a typical application. The machine holds platform
session cookies for the monitoring accounts and evidence about people who are already
targets of violence — and it is a PC on a residential connection, not a hardened
server. Theft is a realistic threat, not a compliance checkbox.

Fernet: AES-128-CBC with an HMAC, authenticated so tampering is detected rather than
silently decrypting to garbage.
"""
from __future__ import annotations

import base64
import hashlib

import structlog
from cryptography.fernet import Fernet, InvalidToken

from src.core.settings import get_settings

log = structlog.get_logger()

_PREFIX = "enc:v1:"


class CryptoError(RuntimeError):
    """Encryption is unavailable or a value could not be decrypted."""


def _fernet() -> Fernet:
    settings = get_settings()
    secret = settings.secret_key
    if not secret:
        raise CryptoError(
            "SECRET_KEY is not set — credentials cannot be encrypted. Run `ankedo setup`."
        )
    if len(secret) < 32:
        raise CryptoError(
            f"SECRET_KEY is {len(secret)} characters; at least 32 are required."
        )
    # Fernet needs exactly 32 url-safe base64 bytes; the configured secret is a
    # passphrase, so derive rather than requiring the operator to paste a key.
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt(plaintext: str) -> str:
    """Encrypt a value for storage. Already-encrypted input passes through."""
    if not plaintext or is_encrypted(plaintext):
        return plaintext
    return _PREFIX + _fernet().encrypt(plaintext.encode()).decode()


def decrypt(value: str) -> str:
    """Decrypt a stored value.

    Values written before encryption existed are returned unchanged, so an upgrade
    does not lose access to existing credentials. `ankedo db encrypt-credentials`
    migrates them.
    """
    if not value:
        return value
    if not is_encrypted(value):
        log.warning("Reading a credential that is still stored in plaintext")
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode()).decode()
    except InvalidToken as exc:
        # Wrong key, or the ciphertext was altered. Both need a human, and neither
        # should be papered over by returning something that looks like data.
        raise CryptoError(
            "could not decrypt — SECRET_KEY may have changed since this was written"
        ) from exc


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)
