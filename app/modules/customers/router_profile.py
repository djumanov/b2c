"""``/public/profile/*`` — API.md §19.

Every row of §19 is marked ``✓``, so the token check sits on the **router**
rather than on each handler, the way ``staff/router_admin.py`` puts
``require_owner`` on the team router. There is no endpoint here that could
correctly be left off it.

``current_customer`` loads the row, so a blocked, unconfirmed or deleted
account is refused before any handler runs — which is also why every handler
below takes the principal and immediately asks the service for the live row.

**Not here:** ``cards/`` (phase 2 — a card arrives as a provider token and
there is nothing to write yet, PHASES.md §2.7) and ``notifications/``
(phase 4 — the ``notifications`` module owns them, §2.8). Both answer 404
until then.
"""

import uuid
from typing import Annotated

from fastapi import Depends, File, Response, UploadFile

from app.api.deps import CurrentCustomer, PaginationDep, current_customer
from app.api.envelope import Page, enveloped_router
from app.api.listing import ListQueryDep
from app.api.multipart import read_limited
from app.db.session import SessionDep
from app.modules.customers import service
from app.modules.customers.schemas import (
    AccountDeleteIn,
    PassengerCreateIn,
    PassengerOut,
    PassengerUpdateIn,
    PasswordChangeIn,
    ProfileOut,
    ProfileUpdateIn,
)

#: One byte over what an avatar may be. Tighter than ``/admin/uploads/``'s cap
#: because this route accepts exactly one purpose, so a 20 MB body is refused
#: after a megabyte rather than after twenty-five.
_READ_LIMIT = service.AVATAR_MAX_BYTES + 1

router = enveloped_router(
    prefix="/profile",
    tags=["public-profile"],
    dependencies=[Depends(current_customer)],
)


# --- personal details ---------------------------------------------------------------


@router.get("/", summary="The signed-in customer's own details")
async def get_profile(customer: CurrentCustomer, session: SessionDep) -> ProfileOut:
    return await service.to_profile(
        session, await service.get_active(session, customer.id)
    )


@router.patch("/", summary="Change name, phone or birth date")
async def update_profile(
    data: ProfileUpdateIn, customer: CurrentCustomer, session: SessionDep
) -> ProfileOut:
    return await service.update_profile(
        session, await service.get_active(session, customer.id), data
    )


@router.delete("/", status_code=204, summary="Delete the account")
async def delete_account(
    data: AccountDeleteIn, customer: CurrentCustomer, session: SessionDep
) -> Response:
    await service.delete_account(
        session, await service.get_active(session, customer.id), data
    )
    return Response(status_code=204)


@router.post("/password/", status_code=204, summary="Change own password")
async def change_password(
    data: PasswordChangeIn, customer: CurrentCustomer, session: SessionDep
) -> Response:
    await service.change_password(
        session, await service.get_active(session, customer.id), data
    )
    return Response(status_code=204)


# --- avatar -------------------------------------------------------------------------


@router.post("/avatar/", summary="Upload an avatar")
async def set_avatar(
    customer: CurrentCustomer,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
) -> ProfileOut:
    # No ``purpose`` form field, unlike ``/admin/uploads/``: on this route it
    # could only ever be ``avatar`` (API.md §11, §19).
    return await service.set_avatar(
        session,
        await service.get_active(session, customer.id),
        content=await read_limited(file, limit=_READ_LIMIT),
        filename=file.filename or "avatar",
        content_type=(file.content_type or "").split(";")[0].strip().lower(),
    )


@router.delete("/avatar/", status_code=204, summary="Remove the avatar")
async def clear_avatar(customer: CurrentCustomer, session: SessionDep) -> Response:
    await service.clear_avatar(session, await service.get_active(session, customer.id))
    return Response(status_code=204)


# --- saved passengers (API.md §8's CRUD pattern) ------------------------------------

passengers_router = enveloped_router(
    prefix="/profile/passengers",
    tags=["public-profile"],
    dependencies=[Depends(current_customer)],
)


@passengers_router.get("/", summary="Saved passengers")
async def list_passengers(
    customer: CurrentCustomer,
    session: SessionDep,
    pagination: PaginationDep,
    query: ListQueryDep,
) -> Page[PassengerOut]:
    return await service.list_passengers(
        session, await service.get_active(session, customer.id), pagination, query
    )


@passengers_router.post("/", status_code=201, summary="Save a passenger")
async def create_passenger(
    data: PassengerCreateIn, customer: CurrentCustomer, session: SessionDep
) -> PassengerOut:
    return await service.create_passenger(
        session, await service.get_active(session, customer.id), data
    )


@passengers_router.get("/{id}/", summary="One saved passenger")
async def get_passenger(
    id: uuid.UUID, customer: CurrentCustomer, session: SessionDep
) -> PassengerOut:
    return await service.get_passenger(
        session, await service.get_active(session, customer.id), id
    )


@passengers_router.patch("/{id}/", summary="Change a saved passenger")
async def update_passenger(
    id: uuid.UUID,
    data: PassengerUpdateIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> PassengerOut:
    return await service.update_passenger(
        session, await service.get_active(session, customer.id), id, data
    )


@passengers_router.delete("/{id}/", status_code=204, summary="Forget a passenger")
async def delete_passenger(
    id: uuid.UUID, customer: CurrentCustomer, session: SessionDep
) -> Response:
    await service.delete_passenger(
        session, await service.get_active(session, customer.id), id
    )
    return Response(status_code=204)


__all__ = ["passengers_router", "router"]
