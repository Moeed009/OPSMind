import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.config import get_settings
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
    SignupRequest,
    SignupResponse,
)
from app.services.supabase_auth import SupabaseAuthError, SupabaseAuthService

router = APIRouter(prefix="/auth", tags=["authentication"])


def _provider_error(exc: SupabaseAuthError, *, operation: str) -> HTTPException:
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
    full_name = metadata.get("full_name") if isinstance(metadata, dict) else None
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
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Your authentication succeeded, but your OpsMind profile could not be "
            "synchronized. Please try logging in again.",
        ) from exc


def _set_session_cookies(response: Response, data: dict[str, Any]) -> None:
    settings = get_settings()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    if not access_token or not refresh_token:
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


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, response: Response) -> SignupResponse:
    email = str(payload.email).lower()
    try:
        data = SupabaseAuthService().signup(email, payload.password, payload.full_name)
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
    try:
        data = SupabaseAuthService().login(str(payload.email).lower(), payload.password)
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
def request_password_reset(payload: PasswordResetRequest) -> MessageResponse:
    try:
        SupabaseAuthService().request_password_reset(str(payload.email).lower())
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except SupabaseAuthError as exc:
        if exc.status_code == 429:
            raise _provider_error(exc, operation="reset") from None
        if exc.status_code >= 500:
            raise _provider_error(exc, operation="reset") from None
        # Do not disclose whether an email address exists in Supabase Auth.
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
            # Always clear the local browser session, even if the provider is unreachable.
            pass
    _clear_session_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
