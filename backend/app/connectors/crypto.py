
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import CryptoError

VERSION_V1 = b"\x01"
NONCE_SIZE = 12
KEY_SIZE = 32

def _aad(tenant_id: UUID | str, connection_id: UUID | str) -> bytes:
    return f"{tenant_id}:{connection_id}".encode()

@dataclass(frozen=True)
class KeyRing:
    keys: dict[str, bytes]
    current_key_id: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> KeyRing:
        env = os.environ if env is None else env
        raw = env.get("CONNECTOR_ENCRYPTION_KEYS")
        current = env.get("CONNECTOR_ENCRYPTION_CURRENT_KEY_ID")
        if not raw or not current:
            raise CryptoError("encryption keys are not configured")
        try:
            decoded = {kid: base64.b64decode(v) for kid, v in json.loads(raw).items()}
        except Exception as exc:  # noqa: BLE001 - never leak key material in message
            raise CryptoError("CONNECTOR_ENCRYPTION_KEYS is malformed") from exc
        return cls.from_dict(decoded, current)

    @classmethod
    def from_dict(cls, keys: dict[str, bytes], current_key_id: str) -> KeyRing:
        for kid, key in keys.items():
            if len(key) != KEY_SIZE:
                raise CryptoError(f"key '{kid}' must be {KEY_SIZE} bytes")
        if current_key_id not in keys:
            raise CryptoError("current key id not found in key ring")
        return cls(dict(keys), current_key_id)

    def get(self, key_id: str) -> bytes:
        try:
            return self.keys[key_id]
        except KeyError:
            raise CryptoError(f"unknown key id '{key_id}'") from None

def generate_key() -> str:
    """Helper for ops: returns a new base64 32-byte key."""
    return base64.b64encode(os.urandom(KEY_SIZE)).decode()

def encrypt(
    payload: dict | bytes | str,
    *,
    tenant_id: UUID | str,
    connection_id: UUID | str,
    keyring: KeyRing,
) -> tuple[str, str]:
    """Returns (ciphertext_b64, key_id). `connection_id` must be generated before calling."""
    if isinstance(payload, dict):
        data = json.dumps(payload, separators=(",", ":")).encode()
    elif isinstance(payload, str):
        data = payload.encode()
    else:
        data = payload
    key_id = keyring.current_key_id
    nonce = os.urandom(NONCE_SIZE)
    ct = AESGCM(keyring.get(key_id)).encrypt(nonce, data, _aad(tenant_id, connection_id))
    return base64.b64encode(VERSION_V1 + nonce + ct).decode(), key_id

def decrypt_bytes(
    ciphertext_b64: str,
    key_id: str,
    *,
    tenant_id: UUID | str,
    connection_id: UUID | str,
    keyring: KeyRing,
) -> bytes:
    try:
        blob = base64.b64decode(ciphertext_b64)
    except Exception as exc:  # noqa: BLE001
        raise CryptoError("ciphertext is not valid base64") from exc
    if len(blob) < 1 + NONCE_SIZE + 16 or blob[:1] != VERSION_V1:
        raise CryptoError("unsupported or corrupt ciphertext")
    nonce, ct = blob[1 : 1 + NONCE_SIZE], blob[1 + NONCE_SIZE :]
    try:
        return AESGCM(keyring.get(key_id)).decrypt(nonce, ct, _aad(tenant_id, connection_id))
    except InvalidTag:
        raise CryptoError("decryption failed (wrong key, tampered data, or wrong tenant/connection)") from None

def decrypt_json(ciphertext_b64: str, key_id: str, **kw) -> dict:
    return json.loads(decrypt_bytes(ciphertext_b64, key_id, **kw))

def needs_rotation(key_id: str, keyring: KeyRing) -> bool:
    """True if a row was encrypted with a non-current key (lazy re-encrypt on read)."""
    return key_id != keyring.current_key_id

def rotate(
    ciphertext_b64: str,
    key_id: str,
    *,
    tenant_id: UUID | str,
    connection_id: UUID | str,
    keyring: KeyRing,
) -> tuple[str, str]:
    """Re-encrypt with the current key. Used by lazy-on-read and the offline migration script."""
    plain = decrypt_bytes(
        ciphertext_b64, key_id, tenant_id=tenant_id, connection_id=connection_id, keyring=keyring
    )
    return encrypt(plain, tenant_id=tenant_id, connection_id=connection_id, keyring=keyring)
