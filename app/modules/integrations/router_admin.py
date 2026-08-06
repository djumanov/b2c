"""``/admin/integrations/gts/*`` — API.md §29.

The guard sits per route rather than on the router: API.md §5 gives
*Integratsiya kalitlari* `owner ✎ / admin 👁`, so reads and writes share a
prefix but not a rule. A router-level `require_owner` would take the reads
away from `admin`; a router-level `current_staff` alone would hand `admin` the
client's GTS password. Both halves have to be said out loud.

The audit middleware reads these templates correctly on its own —
``integrations/gts/credentials/{id}/activate/`` gives the resource
``integrations.gts.credentials`` and the action ``activate`` — so no route
here needs an ``Audited`` override.
"""

import uuid

from fastapi import Depends, Response

from app.api.deps import CurrentStaff, current_staff, require_owner
from app.api.envelope import enveloped_router
from app.db.session import SessionDep
from app.modules.audit.deps import Audited
from app.modules.integrations import service
from app.modules.integrations.schemas import (
    CredentialCreateIn,
    CredentialOut,
    CredentialUpdateIn,
    SmtpIn,
    SmtpOut,
    SmtpTestIn,
    SmtpTestOut,
)

router = enveloped_router(
    prefix="/integrations/gts",
    tags=["integrations"],
    dependencies=[Depends(current_staff)],
)


@router.get("/credentials/", summary="Stored GTS accounts — passwords masked")
async def list_credentials(session: SessionDep) -> list[CredentialOut]:
    return await service.list_credentials(session)


@router.post(
    "/credentials/",
    status_code=201,
    dependencies=[Depends(require_owner)],
    summary="Add a GTS account — the first one becomes the active one",
)
async def create_credential(
    data: CredentialCreateIn, session: SessionDep
) -> CredentialOut:
    return await service.create_credential(session, data)


@router.get("/credentials/{id}/", summary="One GTS account")
async def get_credential(id: uuid.UUID, session: SessionDep) -> CredentialOut:
    return await service.get_credential(session, id)


@router.patch(
    "/credentials/{id}/",
    dependencies=[Depends(require_owner)],
    summary="Change an account; sending a password replaces it",
)
async def update_credential(
    id: uuid.UUID, data: CredentialUpdateIn, session: SessionDep
) -> CredentialOut:
    return await service.update_credential(session, id, data)


@router.delete(
    "/credentials/{id}/",
    status_code=204,
    dependencies=[Depends(require_owner)],
    summary="Remove an account",
)
async def delete_credential(id: uuid.UUID, session: SessionDep) -> Response:
    await service.delete_credential(session, id)
    return Response(status_code=204)


@router.post(
    "/credentials/{id}/activate/",
    dependencies=[Depends(require_owner)],
    summary="Use this account for every GTS call from now on",
)
async def activate_credential(id: uuid.UUID, session: SessionDep) -> CredentialOut:
    return await service.activate_credential(session, id)


# --- SMTP (API.md §29) ------------------------------------------------------------
#
# A singleton, so no ``{id}`` and no collection — the contract addresses it as
# one resource because an installation sends its mail through one relay.

notifications_router = enveloped_router(
    prefix="/integrations/notifications",
    tags=["integrations"],
    dependencies=[Depends(current_staff)],
)


@notifications_router.get("/", summary="SMTP settings — password masked")
async def get_smtp(session: SessionDep) -> SmtpOut:
    return await service.get_smtp(session)


@notifications_router.patch(
    "/",
    dependencies=[Depends(require_owner)],
    summary="Change the SMTP settings; sending a password replaces it",
)
async def update_smtp(data: SmtpIn, session: SessionDep) -> SmtpOut:
    return await service.update_smtp(session, data)


@notifications_router.post(
    "/test/",
    dependencies=[
        Depends(require_owner),
        # The verb comes before any identifier, which is the one shape the
        # audit middleware reads wrongly — so the route says so itself.
        Depends(Audited("integrations.notifications", "test")),
    ],
    summary="Send a test message with the stored settings",
)
async def test_smtp(
    data: SmtpTestIn, staff: CurrentStaff, session: SessionDep
) -> SmtpTestOut:
    return await service.test_smtp(
        session,
        # Whoever pressed the button, unless they named somebody else. Their
        # own address is the one they can check.
        recipient=str(data.to) if data.to else staff.email,
        subject="Test message",
        body=(
            "This is a test message from your travel platform's admin panel.\n"
            "If it reached you, the email settings are working."
        ),
    )


__all__ = ["notifications_router", "router"]
