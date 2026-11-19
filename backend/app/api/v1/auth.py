import logging
import time
import uuid
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.core.payload_crypto import payload_crypto
from app.core.security import AuthError, decode_token
from app.db.models import User
from app.db.rls import set_rls_context
from app.db.session import get_session_factory
from app.schemas.auth import (
    AuthSessionResponse,
    AuthUserResponse,
    LoginRequest,
    MessageResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    PublicKeyResponse,
    SignupRequest,
    SignupResponse,
)
from app.services.email import send_password_reset_email
from app.services.supabase_auth import SupabaseAuthError, SupabaseAuthService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])

def _provider_error(exc: SupabaseAuthError, *, operation: str) -> HTTPException:
    logger.warning(
        "Supabase auth error during %s: status=%s message=%s",
        operation,
        exc.status_code,
        exc.message,
    )
    message = exc.message.lower()
    if operation == "login" and exc.status_code in (400, 401):
        return HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if operation == "signup" and exc.status_code in (400, 409, 422):
        if "already" in message or "registered" in message or "exists" in message:
            return HTTPException(
                status.HTTP_409_CONFLICT, "An account with this email already exists"
            )
        return HTTPException(
            status.HTTP_400_BAD_REQUEST, "Unable to create account with the supplied details"
        )
    if exc.status_code == 429:
        return HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Please try again later."
        )
    if exc.status_code >= 500 or exc.status_code == 503:
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Authentication provider is temporarily unavailable",
        )
    return HTTPException(
        status.HTTP_400_BAD_REQUEST, "Authentication request could not be completed"
    )

def _extract_user(data: dict[str, Any]) -> dict[str, Any]:
    user = data.get("user") if isinstance(data.get("user"), dict) else data
    user_id = user.get("id")
    email = user.get("email")
    if not user_id or not email:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Authentication provider returned an incomplete user"
        )
    try:
        uuid.UUID(str(user_id))
    except ValueError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Authentication provider returned an invalid user ID"
        ) from None
    metadata = user.get("user_metadata") or user.get("raw_user_meta_data") or {}
    full_name = metadata.get("full_name") or metadata.get("name") if isinstance(metadata, dict) else None
    return {"id": str(user_id), "email": str(email).lower(), "full_name": full_name}

def _sync_local_user(user_data: dict[str, Any]) -> None:
    user_id = uuid.UUID(user_data["id"])
    factory = get_session_factory()
    try:
        with factory() as session, session.begin():
            set_rls_context(session, user_id=user_id)
            user = session.scalar(select(User).where(User.id == user_id))
            if user is None:
                session.add(
                    User(
                        id=user_id,
                        email=user_data["email"],
                        full_name=user_data.get("full_name"),
                        is_active=True,
                    )
                )
            else:
                if not user.is_active:
                    raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled")
                user.email = user_data["email"]
                if user_data.get("full_name"):
                    user.full_name = user_data["full_name"]
    except HTTPException:
        raise
    except (IntegrityError, SQLAlchemyError, RuntimeError) as exc:
        logger.error("User profile sync failed for user %s: %s", user_data.get("email"), exc, exc_info=True)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Your authentication succeeded, but your OpsMind profile could not be "
            "synchronized. Please try logging in again.",
        ) from exc

def _set_session_cookies(response: Response, data: dict[str, Any]) -> None:
    settings = get_settings()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    if not access_token:
        return
    expires_in = int(data.get("expires_in") or 3600)
    cookie_options = {
        "httponly": True,
        "secure": settings.auth_cookie_secure,
        "samesite": settings.auth_cookie_samesite,
        "path": "/",
    }
    response.set_cookie(
        settings.auth_access_cookie_name,
        str(access_token),
        max_age=max(60, expires_in),
        **cookie_options,
    )
    if refresh_token:
        response.set_cookie(
            settings.auth_refresh_cookie_name,
            str(refresh_token),
            max_age=60 * 60 * 24 * 30,
            **cookie_options,
        )

def _clear_session_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.auth_access_cookie_name, path="/")
    response.delete_cookie(settings.auth_refresh_cookie_name, path="/")

def _session_response(data: dict[str, Any], user: dict[str, Any]) -> AuthSessionResponse:
    return AuthSessionResponse(
        user=AuthUserResponse(**user),
        expires_in=int(data.get("expires_in") or 3600),
    )

def _resolve_login_credentials(payload: LoginRequest) -> tuple[str, str]:
    if payload.encrypted:
        try:
            data = payload_crypto.decrypt(payload.encrypted)
            email = str(data.get("email") or "").strip().lower()
            password = str(data.get("password") or "")
            if not email or not password:
                raise ValueError("Email and password are required")
            return email, password
        except Exception as exc:
            logger.warning("Decryption error during login: %s", exc)
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid encrypted authentication payload") from exc
    if not payload.email or not payload.password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email and password are required")
    return str(payload.email).strip().lower(), payload.password

def _resolve_signup_credentials(payload: SignupRequest) -> tuple[str, str, str | None]:
    if payload.encrypted:
        try:
            data = payload_crypto.decrypt(payload.encrypted)
            email = str(data.get("email") or "").strip().lower()
            password = str(data.get("password") or "")
            full_name = data.get("full_name")
            if not email or not password:
                raise ValueError("Email and password are required")
            if len(password) < 12:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Password must be at least 12 characters")
            return email, password, full_name
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Decryption error during signup: %s", exc)
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid encrypted authentication payload") from exc
    if not payload.email or not payload.password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email and password are required")
    if len(payload.password) < 12:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Password must be at least 12 characters")
    return str(payload.email).strip().lower(), payload.password, payload.full_name

@router.get("/public-key", response_model=PublicKeyResponse)
def get_auth_public_key() -> PublicKeyResponse:
    """Returns RSA-OAEP public key for client-side zero-knowledge payload encryption."""
    return PublicKeyResponse(public_key=payload_crypto.public_key_pem)

@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, response: Response) -> SignupResponse:
    email, password, full_name = _resolve_signup_credentials(payload)
    try:
        data = SupabaseAuthService().signup(email, password, full_name)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except SupabaseAuthError as exc:
        raise _provider_error(exc, operation="signup") from None

    user = _extract_user(data)
    _sync_local_user(user)
    has_session = bool(data.get("access_token") and data.get("refresh_token"))
    if has_session:
        _set_session_cookies(response, data)
    return SignupResponse(
        message="Account created successfully"
        if has_session
        else "Account created. Please check your email to confirm your account.",
        confirmation_required=not has_session,
        user=AuthUserResponse(**user),
    )

@router.post("/login", response_model=AuthSessionResponse)
def login(payload: LoginRequest, response: Response) -> AuthSessionResponse:
    email, password = _resolve_login_credentials(payload)
    try:
        data = SupabaseAuthService().login(email, password)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except SupabaseAuthError as exc:
        raise _provider_error(exc, operation="login") from None

    if not data.get("access_token") or not data.get("refresh_token"):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Login did not return an authenticated session"
        )
    user = _extract_user(data)
    _sync_local_user(user)
    _set_session_cookies(response, data)
    return _session_response(data, user)

@router.post("/refresh", response_model=AuthSessionResponse)
def refresh(request: Request, response: Response) -> AuthSessionResponse:
    settings = get_settings()
    refresh_token = request.cookies.get(settings.auth_refresh_cookie_name)
    if not refresh_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh session not found")
    try:
        data = SupabaseAuthService().refresh(refresh_token)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except SupabaseAuthError:
        _clear_session_cookies(response)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Refresh session is invalid or expired"
        ) from None

    user = _extract_user(data)
    _sync_local_user(user)
    _set_session_cookies(response, data)
    return _session_response(data, user)

@router.post(
    "/password/reset-request", response_model=MessageResponse, status_code=status.HTTP_202_ACCEPTED
)
def request_password_reset(
    payload: PasswordResetRequest,
    db: Annotated[Session, Depends(get_db)],
) -> MessageResponse:
    email = str(payload.email).lower()
    settings = get_settings()

    user_row = db.execute(
        text("SELECT id, email, is_active FROM app_find_user_by_email(:email)"),
        {"email": email},
    ).mappings().first()

    email_dispatched = False
    from app.services.email import get_effective_email_config
    config = get_effective_email_config(db=db)

    if config.is_configured and user_row and user_row.get("is_active") and settings.supabase_jwt_secret:
        try:
            now = int(time.time())
            token_payload = {
                "sub": str(user_row["id"]),
                "aud": settings.supabase_jwt_audience or "authenticated",
                "role": "authenticated",
                "email": user_row["email"],
                "exp": now + 900,              
                "iat": now,
                "iss": f"{settings.supabase_url.rstrip('/')}/auth/v1" if settings.supabase_url else None,
            }
            token = jwt.encode(token_payload, settings.supabase_jwt_secret, algorithm="HS256")
            reset_url = f"{settings.frontend_url.rstrip('/')}/reset-password#access_token={token}&type=recovery"
            success, err = send_password_reset_email(user_row["email"], reset_url, db=db)
            if success and "simulated" not in err.lower():
                email_dispatched = True
            else:
                logger.info("Custom SMTP not active or simulated (%s), delegating to Supabase recovery mailer", err)
        except Exception as exc:
            logger.warning("Failed during SMTP reset preparation: %s", exc)

    if not email_dispatched:
        try:
            SupabaseAuthService().request_password_reset(email)
        except RuntimeError as exc:
            logger.error("Supabase reset service unavailable: %s", exc)
        except SupabaseAuthError as exc:
            logger.warning("Supabase reset request returned status %s: %s", exc.status_code, exc.message)
            if exc.status_code == 429:
                raise _provider_error(exc, operation="reset") from None

    return MessageResponse(
        message="If an account exists for this email, password reset instructions will be sent."
    )

@router.post("/password/reset", response_model=MessageResponse)
def confirm_password_reset(
    payload: PasswordResetConfirmRequest, response: Response
) -> MessageResponse:
    try:
        decode_token(payload.access_token)
    except AuthError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Password reset link is invalid or expired"
        ) from None

    try:
        service = SupabaseAuthService()
        service.update_password(payload.access_token, payload.new_password)
        try:
            service.logout(payload.access_token)
        except SupabaseAuthError:
            pass
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except SupabaseAuthError as exc:
        if exc.status_code in (400, 401, 403):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Password could not be updated. Request a new reset link.",
            ) from None
        raise _provider_error(exc, operation="reset") from None

    _clear_session_cookies(response)
    return MessageResponse(
        message="Password updated successfully. Please log in with your new password."
    )

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> Response:
    settings = get_settings()
    access_token = request.cookies.get(settings.auth_access_cookie_name)
    if access_token:
        try:
            SupabaseAuthService().logout(access_token)
        except (RuntimeError, SupabaseAuthError):
            pass
    _clear_session_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
