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
from app.api.errors import ErrorCode
from app.api.openapi import error_responses
from app.db.session import SessionDep
from app.modules.audit.deps import Audited
from app.modules.integrations import service
from app.modules.integrations.schemas import (
    CredentialCreateIn,
    CredentialOut,
    CredentialUpdateIn,
    PaymentProviderIn,
    PaymentProviderOut,
    PaymentTestOut,
    SmtpIn,
    SmtpOut,
    SmtpTestIn,
    SmtpTestOut,
    SocialCredentialIn,
    SocialCredentialOut,
)
from app.providers.payments.base import PaymentProviderCode
from app.providers.social.base import SocialProviderCode

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


# --- payment providers (API.md §29) --------------------------------------------------
#
# A registry: addressed by ``code``, and every code the release supports has a
# row whether the client uses it or not. There is no POST and no DELETE —
# adding a provider means shipping an adapter, not filling in a form.

payments_router = enveloped_router(
    prefix="/integrations/payments",
    tags=["integrations"],
    dependencies=[Depends(current_staff)],
)


@payments_router.get(
    "/",
    summary="Payment providers — credentials masked",
    description=(
        "Every provider this release knows, whether the installation uses it "
        "or not. `fields` is the form the adapter asks for; `credentials` is "
        "what is stored, secret values masked. Several may be `enabled` at "
        "once — the customer picks one by `method` when paying — and "
        "site-config `payment_methods` lists exactly those."
    ),
    response_description="One row per provider code.",
)
async def list_payment_providers(session: SessionDep) -> list[PaymentProviderOut]:
    return await service.list_payment_providers(session)


@payments_router.get(
    "/{code}/",
    summary="One provider",
    description="The same row as in the list, for one `code`.",
)
async def get_payment_provider(
    code: PaymentProviderCode, session: SessionDep
) -> PaymentProviderOut:
    return await service.get_payment_provider(session, code)


@payments_router.patch(
    "/{code}/",
    dependencies=[Depends(require_owner)],
    summary="Switch on or off, set credentials, title, logo and order",
    description=(
        "Partial update. `credentials` **merge** into what is stored — send "
        "one key to change one, `null` to delete one; a masked value echoed "
        "back from `GET` is ignored, so a form can resend what it received. "
        "For a provider with `fields`, an unknown key, a `choice` outside its "
        "choices or a non-numeric `int` is a `422` naming the key.\n\n"
        "`enabled: true` needs every `required` field stored, and a code "
        "this release ships no adapter for cannot be switched on — nothing "
        "else is switched off. Press `test/` first."
    ),
    response_description="The row after the change, secrets masked.",
)
async def update_payment_provider(
    code: PaymentProviderCode, data: PaymentProviderIn, session: SessionDep
) -> PaymentProviderOut:
    return await service.update_payment_provider(session, code, data)


@payments_router.post(
    "/{code}/test/",
    # The verb follows the ``{code}`` here, so the audit middleware reads
    # ``integrations.payments`` / ``test`` from the path by itself.
    dependencies=[Depends(require_owner)],
    summary="Check the stored settings against the provider — moves no money",
    description=(
        "Asks the provider, with the stored settings, whether they work — a "
        "read-only call, no charge, no code sent. Works before the provider "
        "is switched on; that is what it is for. A refusal is **not an "
        "error**: `200` with `ok: false` and the provider's words in `detail`. "
        "The result is kept on the row as `last_tested_at` / `last_test_ok` / "
        "`last_test_error`."
    ),
    response_description="What the provider said.",
    responses=error_responses(
        ErrorCode.CONFLICT,
        conflict=(
            "No settings are stored yet, or this release has no adapter for "
            "the provider."
        ),
    ),
)
async def test_payment_provider(
    code: PaymentProviderCode, session: SessionDep
) -> PaymentTestOut:
    return await service.test_payment_provider(session, code)


# --- social sign-in (API.md §29) -----------------------------------------------------

social_router = enveloped_router(
    prefix="/integrations/social",
    tags=["integrations"],
    dependencies=[Depends(current_staff)],
)


@social_router.get("/", summary="Social sign-in providers — secret masked")
async def list_social_credentials(session: SessionDep) -> list[SocialCredentialOut]:
    return await service.list_social_credentials(session)


@social_router.patch(
    "/{provider}/",
    dependencies=[Depends(require_owner)],
    summary="Switch on or off and set the OAuth client",
)
async def update_social_credential(
    provider: SocialProviderCode, data: SocialCredentialIn, session: SessionDep
) -> SocialCredentialOut:
    return await service.update_social_credential(session, provider, data)


__all__ = [
    "notifications_router",
    "payments_router",
    "router",
    "social_router",
]
