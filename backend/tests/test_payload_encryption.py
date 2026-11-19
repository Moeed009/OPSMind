import base64
import json
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

from app.api.v1 import auth as auth_api
from app.main import app

client = TestClient(app)


class FakeSupabaseAuth:
    def signup(self, email: str, password: str, full_name: str | None):
        return {
            "user": {
                "id": "a2e9a1f4-3e0c-4c14-9e5b-2d31c7f4e101",
                "email": email,
                "user_metadata": {"full_name": full_name},
            },
            "access_token": "valid-encrypted-access-token",
            "refresh_token": "valid-encrypted-refresh-token",
            "expires_in": 3600,
        }

    def login(self, email: str, password: str):
        return {
            "user": {
                "id": "a2e9a1f4-3e0c-4c14-9e5b-2d31c7f4e101",
                "email": email,
                "user_metadata": {"full_name": "Encrypted Test User"},
            },
            "access_token": "valid-encrypted-access-token",
            "refresh_token": "valid-encrypted-refresh-token",
            "expires_in": 3600,
        }


def client_encrypt(public_pem: str, data: dict) -> str:
    public_key = serialization.load_pem_public_key(public_pem.encode("utf-8"))
    aes_key = AESGCM.generate_key(bit_length=256)
    iv = os.urandom(12)
    payload_bytes = json.dumps(data).encode("utf-8")
    ciphertext = AESGCM(aes_key).encrypt(iv, payload_bytes, None)

    encrypted_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    packet = {
        "k": base64.b64encode(encrypted_key).decode("utf-8"),
        "iv": base64.b64encode(iv).decode("utf-8"),
        "d": base64.b64encode(ciphertext).decode("utf-8"),
    }
    return base64.b64encode(json.dumps(packet).encode("utf-8")).decode("utf-8")


def test_get_public_key():
    res = client.get("/auth/public-key")
    assert res.status_code == 200
    pem = res.json()["public_key"]
    assert "-----BEGIN PUBLIC KEY-----" in pem
    assert "-----END PUBLIC KEY-----" in pem


def test_login_with_encrypted_payload(monkeypatch):
    monkeypatch.setattr(auth_api, "SupabaseAuthService", FakeSupabaseAuth)
    monkeypatch.setattr(auth_api, "_sync_local_user", lambda user: None)

    # 1. Fetch public key
    res_key = client.get("/auth/public-key")
    pub_pem = res_key.json()["public_key"]

    # 2. Encrypt credentials on client
    encrypted_str = client_encrypt(pub_pem, {
        "email": "encrypted.user@example.com",
        "password": "SecretPassword123@"
    })

    # 3. Post ONLY the encrypted payload (no plain text email or password in network request)
    res = client.post("/auth/login", json={"encrypted": encrypted_str})
    assert res.status_code == 200
    data = res.json()
    assert data["authenticated"] is True
    assert data["user"]["email"] == "encrypted.user@example.com"
    assert "opsmind_access_token" in res.cookies


def test_signup_with_encrypted_payload(monkeypatch):
    monkeypatch.setattr(auth_api, "SupabaseAuthService", FakeSupabaseAuth)
    monkeypatch.setattr(auth_api, "_sync_local_user", lambda user: None)

    res_key = client.get("/auth/public-key")
    pub_pem = res_key.json()["public_key"]

    encrypted_str = client_encrypt(pub_pem, {
        "email": "new.encrypted@example.com",
        "password": "StrongPassword123@",
        "full_name": "Encrypted User"
    })

    res = client.post("/auth/signup", json={"encrypted": encrypted_str})
    assert res.status_code == 201
    data = res.json()
    assert data["user"]["email"] == "new.encrypted@example.com"
    assert "opsmind_access_token" in res.cookies


def test_tampered_encrypted_payload_fails():
    res = client.post("/auth/login", json={"encrypted": "invalid-base64-garbage"})
    assert res.status_code == 400
