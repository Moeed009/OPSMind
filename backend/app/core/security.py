import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient

from app.core.config import get_settings

ASYMMETRIC_ALGORITHMS = frozenset({"ES256", "RS256", "EdDSA"})


class AuthError(Exception):
    """Token missing, invalid, expired ya unsupported."""


@dataclass(frozen=True)
class AuthUser:
    id: uuid.UUID
    email: str | None


@lru_cache
def _jwks_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=3600, timeout=5)


def decode_token(token: str) -> AuthUser:
   
    settings = get_settings()
    base_url = settings.supabase_url.rstrip("/")
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        if alg == "HS256" and settings.supabase_jwt_secret:
            key = settings.supabase_jwt_secret
        elif alg in ASYMMETRIC_ALGORITHMS and base_url:
            jwks_url = f"{base_url}/auth/v1/.well-known/jwks.json"
            key = _jwks_client(jwks_url).get_signing_key_from_jwt(token).key
        else:
            raise AuthError("unsupported token algorithm")

        claims = jwt.decode(
            token,
            key,
            algorithms=[alg],
            audience=settings.supabase_jwt_audience,
            issuer=f"{base_url}/auth/v1" if base_url else None,
            options={"require": ["exp", "sub", "aud"]},
            leeway=10,
        )
        user_id = uuid.UUID(str(claims["sub"]))
    except (jwt.PyJWTError, ValueError) as exc:
        raise AuthError("invalid token") from exc

    email = claims.get("email")
    return AuthUser(id=user_id, email=email.lower() if isinstance(email, str) else None)