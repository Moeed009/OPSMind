from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger("opsmind.auth.crypto")

_KEY_FILE = Path(__file__).resolve().parent.parent.parent / ".auth_rsa_key.pem"

class PayloadCryptoService:
    """Manages asymmetric RSA-OAEP + AES-256-GCM hybrid encryption for client payloads."""

    def __init__(self) -> None:
        self._private_key: rsa.RSAPrivateKey = self._load_or_generate_key()
        self._public_pem: str = (
            self._private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("utf-8")
        )

    def _load_or_generate_key(self) -> rsa.RSAPrivateKey:
        if _KEY_FILE.exists():
            try:
                pem_data = _KEY_FILE.read_bytes()
                key = serialization.load_pem_private_key(pem_data, password=None)
                if isinstance(key, rsa.RSAPrivateKey):
                    return key
            except Exception as exc:
                logger.warning("Failed to load existing auth RSA key: %s, regenerating", exc)

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        try:
            pem_bytes = key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            _KEY_FILE.write_bytes(pem_bytes)
        except Exception as exc:
            logger.warning("Could not persist auth RSA key to disk: %s", exc)
        return key

    @property
    def public_key_pem(self) -> str:
        return self._public_pem

    def decrypt(self, encrypted_packet_b64: str) -> dict[str, Any]:
        """Decrypts a client-encrypted hybrid packet."""
        try:
            raw_json = base64.b64decode(encrypted_packet_b64).decode("utf-8")
            packet = json.loads(raw_json)

            encrypted_aes_key = base64.b64decode(packet["k"])
            iv = base64.b64decode(packet["iv"])
            ciphertext = base64.b64decode(packet["d"])

            aes_key = self._private_key.decrypt(
                encrypted_aes_key,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )

            decrypted_bytes = AESGCM(aes_key).decrypt(iv, ciphertext, None)
            return json.loads(decrypted_bytes.decode("utf-8"))
        except Exception as exc:
            logger.error("Payload decryption failed: %s", exc)
            raise ValueError("Malformed or invalid encrypted authentication payload") from exc

payload_crypto = PayloadCryptoService()
