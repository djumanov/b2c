"""``/admin/auth/*`` (API.md §27) and ``/admin/staff/*`` (API.md §38).

Two routers, because the two paths answer to different rules. Auth is mostly
unauthenticated and carries the tight ``auth`` rate limit; the team resource is
**entirely** ``owner`` — API.md §5 puts *Jamoa* out of ``admin``'s reach
altogether, so the guard sits on the router and no handler can forget it.

Handlers stay thin: parse, call the service, return a model. The envelope comes
from the route class and errors from the exception handlers.
"""

import uuid

from fastapi import Depends, Request, Response

from app.api.deps import (
    CurrentStaff,
    PaginationDep,
    RateLimit,
    client_ip,
    current_staff,
    require_owner,
)
from app.api.envelope import Page, enveloped_router
from app.api.listing import ListQueryDep
from app.db.session import SessionDep
from app.modules.staff import service
from app.modules.staff.schemas import (
    LoginIn,
    PasswordChangeIn,
    PasswordResetConfirmIn,
    PasswordResetRequestIn,
    RefreshIn,
    StaffCreateIn,
    StaffMeOut,
    StaffOut,
    StaffUpdateIn,
    TokenPairOut,
)

# --- authentication ---------------------------------------------------------------

auth_router = enveloped_router(
    prefix="/auth",
    tags=["admin-auth"],
    # API.md §14: five attempts a minute. Attached to the router so a password
    # endpoint added later cannot be left off the limit.
    dependencies=[Depends(RateLimit("auth"))],
)


@auth_router.post("/login/", summary="Sign in — access + refresh")
async def login(data: LoginIn, request: Request, session: SessionDep) -> TokenPairOut:
    return await service.authenticate(
        session,
        data,
        user_agent=request.headers.get("user-agent"),
        ip=client_ip(request),
    )


@auth_router.post("/refresh/", summary="Rotate the refresh token for a new pair")
async def refresh(
    data: RefreshIn, request: Request, session: SessionDep
) -> TokenPairOut:
    return await service.refresh_session(
        session,
        data.refresh_token,
        user_agent=request.headers.get("user-agent"),
        ip=client_ip(request),
    )


@auth_router.post(
    "/logout/",
    status_code=204,
    dependencies=[Depends(current_staff)],
    summary="Revoke this session's refresh token",
)
async def logout(data: RefreshIn, session: SessionDep) -> Response:
    await service.logout(session, data.refresh_token)
    return Response(status_code=204)


@auth_router.get("/me/", summary="The signed-in employee and their role")
async def me(staff: CurrentStaff, session: SessionDep) -> StaffMeOut:
    return service.to_me(await service.get_active(session, staff.id))


@auth_router.post("/password/change/", status_code=204, summary="Change own password")
async def change_password(
    data: PasswordChangeIn, staff: CurrentStaff, session: SessionDep
) -> Response:
    await service.change_password(
        session, await service.get_active(session, staff.id), data
    )
    return Response(status_code=204)


@auth_router.post(
    "/password/reset/request/",
    status_code=204,
    summary="Send a reset link by email",
)
async def request_password_reset(
    data: PasswordResetRequestIn, session: SessionDep
) -> Response:
    # 204 whether or not the address is known — see the service.
    await service.request_password_reset(session, data.email)
    return Response(status_code=204)


@auth_router.post(
    "/password/reset/confirm/", status_code=204, summary="Set the new password"
)
async def confirm_password_reset(
    data: PasswordResetConfirmIn, session: SessionDep
) -> Response:
    await service.confirm_password_reset(session, data.token, data.new_password)
    return Response(status_code=204)


# --- the team (API.md §38) ----------------------------------------------------------

router = enveloped_router(
    prefix="/staff",
    tags=["staff"],
    # The whole section is owner-only: `admin` does not appear in the *Jamoa*
    # row of API.md §5 at all, so it gets 403 here, not a filtered view.
    dependencies=[Depends(require_owner)],
)


@router.get("/", summary="List employees")
async def list_staff(
    session: SessionDep, pagination: PaginationDep, query: ListQueryDep
) -> Page[StaffOut]:
    return await service.list_staff(session, pagination, query)


@router.post("/", status_code=201, summary="Add an employee")
async def create_staff(data: StaffCreateIn, session: SessionDep) -> StaffOut:
    return await service.create_staff(session, data)


@router.get("/{id}/", summary="One employee")
async def get_staff(id: uuid.UUID, session: SessionDep) -> StaffOut:
    return await service.get_staff(session, id)


@router.patch("/{id}/", summary="Change name, email or role")
async def update_staff(
    id: uuid.UUID, data: StaffUpdateIn, session: SessionDep
) -> StaffOut:
    return await service.update_staff(session, id, data)


@router.delete("/{id}/", status_code=204, summary="Remove an employee")
async def delete_staff(
    id: uuid.UUID, staff: CurrentStaff, session: SessionDep
) -> Response:
    await service.delete_staff(session, id, actor_id=staff.id)
    return Response(status_code=204)


@router.post("/{id}/block/", summary="Block an employee")
async def block_staff(
    id: uuid.UUID, staff: CurrentStaff, session: SessionDep
) -> StaffOut:
    return await service.block_staff(session, id, actor_id=staff.id)


@router.post(
    "/{id}/reset-password/",
    status_code=204,
    summary="Email this employee a reset link",
)
async def reset_staff_password(id: uuid.UUID, session: SessionDep) -> Response:
    await service.send_reset_password_link(session, id)
    return Response(status_code=204)


__all__ = ["auth_router", "router"]
