import time
import uuid
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser
from app.core import security
from app.core.config import Settings

SECRET = "test-secret-" + "a" * 32
SUPABASE_URL = "https://example-ref.supabase.co"

app = FastAPI()


@app.get("/whoami")
def whoami(user: CurrentUser) -> dict:
    return {"id": str(user.id), "email": user.email}


client = TestClient(app)


def make_token(
    key=SECRET,
    algorithm="HS256",
    sub: str | None = None,
    aud="authenticated",
    exp_in=3600,
    iss=None,
    drop=(),
    email="Alice@Example.com",
):
    payload = {
        "sub": sub or str(uuid.uuid4()),
        "aud": aud,
        "exp": int(time.time()) + exp_in,
        "email": email,
    }
    if iss:
        payload["iss"] = iss
    for name in drop:
        payload.pop(name, None)
    return jwt.encode(payload, key, algorithm=algorithm)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def hs256_mode(monkeypatch: pytest.MonkeyPatch):
    settings = Settings(supabase_url="", supabase_jwt_secret=SECRET)
    monkeypatch.setattr(security, "get_settings", lambda: settings)


@pytest.fixture
def jwks_mode(monkeypatch: pytest.MonkeyPatch):
    private_key = ec.generate_private_key(ec.SECP256R1())
    settings = Settings(supabase_url=SUPABASE_URL, supabase_jwt_secret="")
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    stub = SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private_key.public_key())
    )
    monkeypatch.setattr(security, "_jwks_client", lambda url: stub)
    return private_key


def test_valid_hs256_token(hs256_mode):
    user_id = str(uuid.uuid4())
    response = client.get("/whoami", headers=auth(make_token(sub=user_id)))
    assert response.status_code == 200
    assert response.json() == {"id": user_id, "email": "alice@example.com"}


def test_missing_header_is_401(hs256_mode):
    response = client.get("/whoami")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_expired_token_is_401(hs256_mode):
    assert client.get("/whoami", headers=auth(make_token(exp_in=-3600))).status_code == 401


def test_wrong_secret_is_401(hs256_mode):
    token = make_token(key="another-secret-" + "b" * 32)
    assert client.get("/whoami", headers=auth(token)).status_code == 401


def test_wrong_audience_is_401(hs256_mode):
    assert client.get("/whoami", headers=auth(make_token(aud="anon"))).status_code == 401


def test_missing_sub_is_401(hs256_mode):
    assert client.get("/whoami", headers=auth(make_token(drop=("sub",)))).status_code == 401


def test_non_uuid_sub_is_401(hs256_mode):
    assert client.get("/whoami", headers=auth(make_token(sub="not-a-uuid"))).status_code == 401


def test_alg_none_is_rejected(hs256_mode):
    token = make_token(key=None, algorithm="none")
    assert client.get("/whoami", headers=auth(token)).status_code == 401


def test_hs256_rejected_when_no_secret_configured(monkeypatch: pytest.MonkeyPatch):
    settings = Settings(supabase_url=SUPABASE_URL, supabase_jwt_secret="")
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    assert client.get("/whoami", headers=auth(make_token())).status_code == 401


def test_valid_asymmetric_token_via_jwks(jwks_mode):
    token = make_token(key=jwks_mode, algorithm="ES256", iss=f"{SUPABASE_URL}/auth/v1")
    assert client.get("/whoami", headers=auth(token)).status_code == 200


def test_asymmetric_token_with_wrong_issuer_is_401(jwks_mode):
    token = make_token(key=jwks_mode, algorithm="ES256", iss="https://evil.example/auth/v1")
    assert client.get("/whoami", headers=auth(token)).status_code == 401