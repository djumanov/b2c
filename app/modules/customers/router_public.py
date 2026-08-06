"""``/public/auth/*`` — the customer half of API.md §18.

Handlers stay thin: parse, call the service, return a model. The envelope comes
from the route class and errors from the exception handlers.

``social/{provider}/`` is mounted here but configured in ``integrations``: the
OAuth client belongs with the other credentials (API.md §29, PHASES.md §2.11),
while the sign-in itself is an account question and so belongs to this module.

``devices/`` is absent — API.md §41 defers it to the push work, so it answers
404.
"""

from fastapi import Depends, Request, Response

from app.api.deps import RateLimit, client_ip, current_customer
from app.api.envelope import enveloped_router
from app.db.session import SessionDep
from app.modules.customers import service
from app.modules.customers.schemas import (
    LoginIn,
    PasswordResetConfirmIn,
    PasswordResetRequestIn,
    PasswordResetVerifyIn,
    RefreshIn,
    RegisterConfirmIn,
    RegisterIn,
    ResendCodeIn,
    ResetTokenOut,
    SocialLoginIn,
    TokenPairOut,
)

router = enveloped_router(
    prefix="/auth",
    tags=["public-auth"],
    # API.md §14: five a minute per IP. The contract names ``login`` and
    # ``password/reset``; registration and resend sit under the same limit
    # because they send mail to an address the caller chose, which is the one
    # thing on this router that costs somebody else something. Attached to the
    # router so an endpoint added later cannot be left off it.
    dependencies=[Depends(RateLimit("auth"))],
)


# --- registration -------------------------------------------------------------------


@router.post("/register/", status_code=204, summary="Register — sends a code")
async def register(data: RegisterIn, session: SessionDep) -> Response:
    # 204 whether or not the address is already taken — see the service.
    await service.register(session, data)
    return Response(status_code=204)


@router.post(
    "/register/confirm/", summary="Confirm the emailed code — access + refresh"
)
async def confirm_registration(
    data: RegisterConfirmIn, request: Request, session: SessionDep
) -> TokenPairOut:
    return await service.confirm_registration(
        session,
        data,
        user_agent=request.headers.get("user-agent"),
        ip=client_ip(request),
    )


@router.post("/register/resend/", status_code=204, summary="Send the code again")
async def resend_registration_code(data: ResendCodeIn, session: SessionDep) -> Response:
    await service.resend_registration_code(session, data.email)
    return Response(status_code=204)


# --- sessions -----------------------------------------------------------------------


@router.post("/login/", summary="Sign in — access + refresh")
async def login(data: LoginIn, request: Request, session: SessionDep) -> TokenPairOut:
    return await service.authenticate(
        session,
        data,
        user_agent=request.headers.get("user-agent"),
        ip=client_ip(request),
    )


@router.post("/refresh/", summary="Rotate the refresh token for a new pair")
async def refresh(
    data: RefreshIn, request: Request, session: SessionDep
) -> TokenPairOut:
    return await service.refresh_session(
        session,
        data.refresh_token,
        user_agent=request.headers.get("user-agent"),
        ip=client_ip(request),
    )


@router.post("/social/{provider}/", summary="Sign in with a social provider")
async def social_login(
    # A plain string, not the enum: a name this release does not know has to
    # read the same as one the client switched off — both are 404, "there is no
    # such sign-in here" (API.md §18). Typing it would make an unknown provider
    # a 422 instead, which describes our enum to an anonymous caller.
    provider: str,
    data: SocialLoginIn,
    request: Request,
    session: SessionDep,
) -> TokenPairOut:
    return await service.authenticate_social(
        session,
        provider,
        data.id_token,
        user_agent=request.headers.get("user-agent"),
        ip=client_ip(request),
    )


@router.post(
    "/logout/",
    status_code=204,
    # API.md §18 marks this one ✓: the caller proves the session is theirs
    # before it is ended, so a leaked refresh token is not also a way to sign
    # somebody out.
    dependencies=[Depends(current_customer)],
    summary="Revoke this refresh token",
)
async def logout(data: RefreshIn, session: SessionDep) -> Response:
    await service.logout(session, data.refresh_token)
    return Response(status_code=204)


# --- password reset -------------------------------------------------------------------


@router.post(
    "/password/reset/request/", status_code=204, summary="Send a reset code by email"
)
async def request_password_reset(
    data: PasswordResetRequestIn, session: SessionDep
) -> Response:
    # 204 whether or not the address is known — see the service.
    await service.request_password_reset(session, data.email)
    return Response(status_code=204)


@router.post("/password/reset/verify/", summary="Exchange the code for a reset token")
async def verify_password_reset(
    data: PasswordResetVerifyIn, session: SessionDep
) -> ResetTokenOut:
    return await service.verify_password_reset(session, data)


@router.post(
    "/password/reset/confirm/", status_code=204, summary="Set the new password"
)
async def confirm_password_reset(
    data: PasswordResetConfirmIn, session: SessionDep
) -> Response:
    await service.confirm_password_reset(session, data.reset_token, data.new_password)
    return Response(status_code=204)


__all__ = ["router"]
