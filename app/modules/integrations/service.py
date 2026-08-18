"""GTS credentials: several stored, exactly one in use — API.md §29.

This is the first caller of ``app/core/crypto.py``. Passwords are sealed on
the way in and masked on the way out; the only function that hands back a
readable password is ``active_credential``, and nothing routes to it.

``active_credential`` and ``gts_base_url`` are the module's whole outward
surface for GTS. Everything that talks to GTS reaches its account — or merely
its address, which is all the static catalogues need — through those two calls
rather than through the model, which is what keeps ``providers/gts`` and
``modules/catalog`` from importing this module's ``models.py``
(ARCHITECTURE.md §4).
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import Conflict, NotFound, UpstreamError, ValidationFailed
from app.core.config import settings
from app.core.crypto import decrypt, encrypt, mask_secret, needs_reencryption
from app.core.logging import get_logger
from app.db.mixins import utcnow
from app.modules.audit import context as audit_context
from app.modules.integrations import repository
from app.modules.integrations.models import (
    DEFAULT_BASE_URL,
    GtsCredential,
    PaymentProvider,
    SmtpSettings,
    SocialCredential,
    TlsMode,
)
from app.modules.integrations.schemas import (
    CredentialCreateIn,
    CredentialOut,
    CredentialUpdateIn,
    PaymentProviderIn,
    PaymentProviderOut,
    SmtpIn,
    SmtpOut,
    SmtpTestOut,
    SocialCredentialIn,
    SocialCredentialOut,
)
from app.modules.uploads import service as uploads_service
from app.providers import notifications, social
from app.providers import payments as payments_provider
from app.providers.gts.base import GtsClient
from app.providers.notifications.base import Notifier
from app.providers.notifications.log import LogNotifier
from app.providers.notifications.smtp import SmtpConfig, SmtpNotifier
from app.providers.payments.base import PaymentProvider as PaymentProviderPort
from app.providers.payments.base import PaymentProviderCode
from app.providers.payments.base import registry as payments_registry
from app.providers.social.base import SocialProviderCode, SocialVerifier
from app.providers.social.google import GoogleVerifier
from app.providers.storage.base import UploadPurpose

logger = get_logger(__name__)

#: What a password looks like on the way out. A fixed mask, not a suffix of the
#: real value: API.md §29 lets the last characters of an API *key* show so the
#: owner can tell two of them apart, but a password has no such need and every
#: character shown is one fewer to guess.
MASKED_PASSWORD = "•" * 8


@dataclass(frozen=True, slots=True, repr=False)
class ActiveGtsCredential:
    """The account GTS calls sign in with. ``password`` is in the clear.

    ``repr`` is written by hand rather than generated. A dataclass carrying a
    secret ends up inside a structlog ``exc_info``, an f-string or a failing
    assertion sooner or later, and the default ``repr`` would print the
    password there (PROJECT.md §13).
    """

    id: uuid.UUID
    label: str
    base_url: str
    email: str
    password: str
    updated_at: datetime

    def __repr__(self) -> str:
        return (
            f"ActiveGtsCredential(id={self.id}, label={self.label!r}, "
            f"email={self.email!r})"
        )


def _aud(row: GtsCredential) -> dict[str, Any]:
    """The fields a reader of the audit journal would care about.

    The password is not among them, and not as a masked string either: that it
    changed is visible from ``updated_at``, and the value has no business being
    in a diff. ``audit.context.redact`` would catch it anyway — this is the
    belt to that pair of braces.
    """
    return {
        "label": row.label,
        "email": row.email,
        "base_url": row.base_url,
        "is_active": row.is_active,
    }


def _out(row: GtsCredential) -> CredentialOut:
    return CredentialOut(
        id=row.id,
        label=row.label,
        base_url=row.base_url,
        email=row.email,
        # This path never decrypts, so it cannot leak the password by accident.
        password=MASKED_PASSWORD,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _require(session: AsyncSession, credential_id: uuid.UUID) -> GtsCredential:
    row = await repository.by_id(session, credential_id)
    if row is None:
        raise NotFound("GTS credential not found")
    return row


async def _check_label(
    session: AsyncSession, label: str, *, excluding: uuid.UUID | None = None
) -> str:
    label = label.strip()
    if await repository.label_taken(session, label, excluding=excluding):
        raise ValidationFailed(
            "Another GTS credential already uses this name", field="label"
        )
    return label


# --- reads -----------------------------------------------------------------------


async def list_credentials(session: AsyncSession) -> list[CredentialOut]:
    return [_out(row) for row in await repository.all_credentials(session)]


async def get_credential(
    session: AsyncSession, credential_id: uuid.UUID
) -> CredentialOut:
    return _out(await _require(session, credential_id))


# --- writes ----------------------------------------------------------------------


async def create_credential(
    session: AsyncSession, data: CredentialCreateIn
) -> CredentialOut:
    """Add an account. The first one added becomes the active one.

    With no rows there is no answer to "which account is in use", so the panel
    would show a credential that is stored and still not used by anything.
    """
    label = await _check_label(session, data.label)
    ciphertext, key_version = encrypt(data.password)

    row = GtsCredential(
        label=label,
        base_url=data.base_url,
        email=str(data.email),
        password=ciphertext,
        key_version=key_version,
        is_active=await repository.count(session) == 0,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    audit_context.describe(resource_id=row.id, changes={"created": _aud(row)})
    logger.info(
        "gts_credential_created", credential_id=str(row.id), active=row.is_active
    )
    return _out(row)


async def update_credential(
    session: AsyncSession, credential_id: uuid.UUID, data: CredentialUpdateIn
) -> CredentialOut:
    row = await _require(session, credential_id)
    before = _aud(row)

    if data.label is not None:
        row.label = await _check_label(session, data.label, excluding=row.id)
    if data.email is not None:
        row.email = str(data.email)
    if data.base_url is not None:
        row.base_url = data.base_url
    if data.password is not None:
        row.password, row.key_version = encrypt(data.password)
    elif needs_reencryption(row.key_version):
        # Rotation happens while the client is asleep: rows move to the new key
        # the next time somebody touches them, without anybody retyping a
        # password (PROJECT.md §13). Free here — the row is being written anyway.
        row.password, row.key_version = encrypt(decrypt(row.password, row.key_version))

    await session.commit()
    await session.refresh(row)

    audit_context.describe(changes=audit_context.diff(before, _aud(row)))
    return _out(row)


async def delete_credential(session: AsyncSession, credential_id: uuid.UUID) -> None:
    """Remove an account, unless it is the one in use and another could be.

    Deleting the active credential while an alternative exists is almost
    always a misclick: the installation stops selling for no reason a reader
    of the journal could reconstruct. Deleting the last one is allowed —
    otherwise a client with a single account could never remove it.
    """
    row = await _require(session, credential_id)
    if row.is_active and await repository.count(session) > 1:
        raise Conflict("This credential is the one in use. Activate another one first.")

    await session.delete(row)
    await session.commit()
    logger.info("gts_credential_deleted", credential_id=str(credential_id))


async def activate_credential(
    session: AsyncSession, credential_id: uuid.UUID
) -> CredentialOut:
    """Make this the account every GTS call uses.

    Deliberately unconditional: a credential that has never been tested, or
    whose last test failed, can still be activated. ``test/`` can fail because
    GTS is down or because two-factor confirmation is still on for the machine
    account (PROJECT.md D1) — neither is a reason to lock the owner out of
    switching their own account.
    """
    row = await _require(session, credential_id)
    if row.is_active:
        return _out(row)

    await repository.deactivate_all(session)
    # Without this the unit of work may issue the two UPDATEs in either order,
    # and "set the new one active" landing first trips the partial unique index
    # on a change that is perfectly valid.
    await session.flush()
    row.is_active = True

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict(
            "Another credential was activated at the same moment. Try again."
        ) from exc

    await session.refresh(row)
    audit_context.describe(changes={"is_active": {"from": False, "to": True}})
    logger.info("gts_credential_activated", credential_id=str(row.id))
    return _out(row)


# --- what the rest of the process calls ------------------------------------------


async def active_credential(session: AsyncSession) -> ActiveGtsCredential | None:
    """The account GTS traffic signs in with, decrypted. ``None`` if unset.

    The one function outside this module's own endpoints that anybody should
    call. ``providers/gts`` reads its credentials here (ARCHITECTURE.md §7),
    and it deliberately does **not** re-encrypt a row left on an older key:
    this is the hot path of every GTS request, and a read that writes would
    take the caller's transaction with it.
    """
    row = await repository.active(session)
    if row is None:
        return None
    return ActiveGtsCredential(
        id=row.id,
        label=row.label,
        base_url=row.base_url,
        email=row.email,
        password=decrypt(row.password, row.key_version),
        updated_at=row.updated_at,
    )


async def gts_base_url(session: AsyncSession) -> str:
    """Where GTS lives for this installation — with no account involved.

    Separate from ``active_credential`` for two reasons, both of which matter
    to the one caller that needs it: ``catalog`` reads GTS's static service,
    which takes no credentials at all.

    It **never decrypts.** A public, unauthenticated endpoint has no business
    unsealing a password it will not send (PROJECT.md §13).

    And it answers ``str``, not ``str | None``. The static service works
    without an account, so a citizenship dropdown must not go dark on an
    installation where nobody has saved a GTS credential yet. The URL still is
    not hardcoded in the calling module (PROJECT.md §7) — if a row is active,
    it is that row's.
    """
    row = await repository.active(session)
    return row.base_url if row is not None else DEFAULT_BASE_URL


# --- SMTP (API.md §29) ------------------------------------------------------------


def _smtp_aud(row: SmtpSettings) -> dict[str, Any]:
    """The audit-visible projection. The password is not in it, at all."""
    return {
        "enabled": row.enabled,
        "host": row.host,
        "port": row.port,
        "tls": row.tls.value,
        "username": row.username,
        "from_address": row.from_address,
        "from_name": row.from_name,
    }


def _smtp_out(row: SmtpSettings) -> SmtpOut:
    return SmtpOut(
        enabled=row.enabled,
        host=row.host,
        port=row.port,
        tls=row.tls,
        username=row.username,
        # ``None`` and a mask mean different things to the panel: nothing
        # stored versus stored and hidden. This path never decrypts.
        password=MASKED_PASSWORD if row.password else None,
        from_address=row.from_address,
        from_name=row.from_name,
        last_tested_at=row.last_tested_at,
        last_test_ok=row.last_test_ok,
        last_test_error=row.last_test_error,
    )


async def get_smtp(session: AsyncSession) -> SmtpOut:
    row = await repository.smtp(session)
    # First read of a fresh installation creates the row; without the commit it
    # is rolled back and the next read makes it again.
    if session.in_transaction():
        await session.commit()
    return _smtp_out(row)


async def update_smtp(session: AsyncSession, data: SmtpIn) -> SmtpOut:
    row = await repository.smtp(session)
    before = _smtp_aud(row)

    if data.host is not None:
        row.host = data.host.strip()
    if data.port is not None:
        row.port = data.port
    if data.tls is not None:
        row.tls = data.tls
    if data.username is not None:
        # An empty string clears it — a relay that wants no authentication is a
        # real configuration and has to be reachable from one that did.
        row.username = data.username.strip() or None
    if data.from_address is not None:
        row.from_address = str(data.from_address)
    if data.from_name is not None:
        row.from_name = data.from_name.strip() or None
    if data.password is not None:
        row.password, row.key_version = encrypt(data.password)
    elif row.password is not None and needs_reencryption(row.key_version or 0):
        # Lazy rotation, free here because the row is being written anyway.
        row.password, row.key_version = encrypt(
            decrypt(row.password, row.key_version or 0)
        )

    if data.enabled is not None:
        if data.enabled and not _is_usable(row):
            raise ValidationFailed(
                "Set at least a host and a from-address before switching "
                "email on — otherwise nothing would be sent",
                field="enabled",
            )
        row.enabled = data.enabled

    await session.commit()
    await session.refresh(row)

    audit_context.describe(changes=audit_context.diff(before, _smtp_aud(row)))
    return _smtp_out(row)


def _is_usable(row: SmtpSettings) -> bool:
    """Enough filled in to have a chance of delivering a message."""
    return bool(row.host and row.from_address)


async def gts_client(session: AsyncSession) -> GtsClient:
    """A ready GTS client, built from the active credential.

    The counterpart of ``payment_provider_adapter``, and here for the same
    reason: the credential is a settings question and settings belong to a
    module. It lives in ``integrations`` rather than in ``products`` because
    both the search flow and the order steps need one, and putting it in either
    of them would make the other import it — a cycle for a two-line function.

    **No active credential is a 502, not a 404.** The product *is* enabled on
    this installation, so pretending the resource does not exist would lie; the
    thing behind us cannot serve, which is exactly what ``upstream_error``
    means. A ``CryptoError`` from decryption propagates to the generic 500
    instead — that is our misconfiguration, not GTS's, and the message must not
    describe the key ring.

    ``client_for`` is imported here rather than at the top, and that is a knot
    rather than a style choice: ``providers/gts/client.py`` reads
    ``ActiveGtsCredential`` out of this very module, so importing it at module
    level would close a cycle. The alternative — a module that exists only to
    hold these two lines — costs more than the import does.
    """
    from app.providers.gts.client import client_for

    credential = await active_credential(session)
    if credential is None:
        raise UpstreamError(
            "GTS is not configured on this installation: no active credential"
        )
    return client_for(credential)


async def notifier(session: AsyncSession) -> Notifier:
    """Which notifier this installation sends through, right now.

    The only accessor. Built per call rather than cached in a module global:
    the settings behind it are edited from the panel and there are several
    worker processes, so a value computed once at import would be both stale
    and inconsistent between them. Sending is rare enough that reading one row
    first costs nothing worth saving.
    """
    override = notifications.get_override()
    if override is not None:
        return override

    row = await repository.smtp(session)
    if session.in_transaction():
        await session.commit()
    if not row.enabled or not _is_usable(row):
        return LogNotifier()

    assert row.host is not None and row.from_address is not None  # _is_usable
    return SmtpNotifier(
        SmtpConfig(
            host=row.host,
            port=row.port,
            tls=row.tls.value,
            from_address=row.from_address,
            from_name=row.from_name,
            username=row.username,
            password=decrypt(row.password, row.key_version or 0)
            if row.password
            else None,
        )
    )


async def test_smtp(
    session: AsyncSession, *, recipient: str, subject: str, body: str
) -> SmtpTestOut:
    """Send a real message and record what happened (API.md §29).

    A relay that refuses the password is **not** an error of this endpoint: it
    answers `200` with ``ok: false`` and the reason. Returning `502` here would
    tell the owner that something went wrong without telling them what, which
    is the one thing this button exists to do.
    """
    row = await repository.smtp(session)
    if not row.enabled or not _is_usable(row):
        raise Conflict("Configure the SMTP settings and switch email on first")

    detail: str | None = None
    try:
        await (await notifier(session)).send(
            recipient=recipient, subject=subject, body=body
        )
        ok = True
    except Exception as exc:  # noqa: BLE001 - the reason is the whole answer
        ok = False
        detail = f"{type(exc).__name__}: {exc}"[:500]
        logger.warning("smtp_test_failed", error=detail)

    row.last_tested_at = utcnow()
    row.last_test_ok = ok
    row.last_test_error = detail
    await session.commit()
    await session.refresh(row)

    audit_context.describe(changes={"last_test_ok": ok})
    return SmtpTestOut(ok=ok, detail=detail, tested_at=row.last_tested_at)


async def bootstrap_smtp(session: AsyncSession) -> SmtpSettings | None:
    """Fill the relay in from the environment — first boot only (PROJECT.md §14).

    ``None`` when it was a no-op, which is the usual case: the same rule as
    ``staff.create_first_owner``, and for the same reason. An installation with
    an empty database cannot send the code its own first sign-in needs, and
    there is nobody signed in yet to type one into the panel — so the very
    first configuration comes from ``.env``, once.

    **Once** is the whole design. As soon as the row has a host, somebody has
    configured this installation and the environment is never consulted again:
    a ``.env`` edited months later must not quietly replace what the client
    chose from the panel. The setting still lives in the database (PROJECT.md
    §7); this only decides what is in it on the first morning.
    """
    if not settings.smtp_host or not settings.smtp_from:
        logger.info("smtp_seed_skipped", reason="SMTP_HOST/SMTP_FROM not set")
        return None

    row = await repository.smtp(session)
    if row.host:
        logger.info("smtp_seed_skipped", reason="already configured")
        return None

    try:
        tls = TlsMode(settings.smtp_tls.strip().lower())
    except ValueError:
        # Not fatal: mail with the wrong TLS mode fails at the relay, mail with
        # none of it configured fails silently, and the second is worse.
        logger.warning("smtp_seed_bad_tls", value=settings.smtp_tls)
        tls = TlsMode.STARTTLS

    row.host = settings.smtp_host.strip()
    row.port = settings.smtp_port
    row.tls = tls
    row.username = settings.smtp_username.strip() or None
    row.from_address = settings.smtp_from.strip()
    row.from_name = settings.smtp_from_name.strip() or None
    if settings.smtp_password:
        row.password, row.key_version = encrypt(settings.smtp_password)
    row.enabled = True

    await session.commit()
    await session.refresh(row)
    logger.info("smtp_seeded", host=row.host, from_address=row.from_address)
    return row


# --- payment providers (API.md §29) ---------------------------------------------------


@dataclass(frozen=True, slots=True, repr=False)
class ConfiguredPaymentProvider:
    """What an adapter needs, and nothing the panel needs.

    The seam phase 2 builds against: `providers/payments/{payme,click}.py`
    reads its keys here and never imports this module's ``models``
    (ARCHITECTURE.md §4). Same shape and the same hand-written ``__repr__`` as
    ``ActiveGtsCredential``, for the same reason — a dataclass carrying secrets
    ends up inside a traceback sooner or later.
    """

    code: PaymentProviderCode
    title: str
    sort_order: int
    credentials: dict[str, str]

    def __repr__(self) -> str:
        return (
            f"ConfiguredPaymentProvider(code={self.code.value!r}, "
            f"keys={sorted(self.credentials)!r})"
        )


def _decode_credentials(row: PaymentProvider) -> dict[str, str]:
    if not row.credentials:
        return {}
    decoded: dict[str, str] = json.loads(decrypt(row.credentials, row.key_version or 0))
    return decoded


def _payments_aud(row: PaymentProvider) -> dict[str, Any]:
    """What the journal records. The credentials are **not** among them.

    ``core.logging.redact`` would blank the key anyway; this is the belt to
    that pair of braces. Which keys are set is worth knowing, so their names
    are recorded — the names are not the secret.
    """
    return {
        "enabled": row.enabled,
        "title": row.title,
        "logo_id": str(row.logo_id) if row.logo_id else None,
        "sort_order": row.sort_order,
        "credential_keys": sorted(_decode_credentials(row)),
    }


async def _payments_out(
    session: AsyncSession, row: PaymentProvider
) -> PaymentProviderOut:
    return PaymentProviderOut(
        code=row.code,
        enabled=row.enabled,
        title=row.title,
        logo_id=row.logo_id,
        logo_url=await uploads_service.url_for(session, row.logo_id),
        sort_order=row.sort_order,
        # Masked one value at a time rather than replaced wholesale: the panel
        # has to show *which* keys are set, and an operator recognises their
        # own merchant id by its last characters.
        credentials={
            key: mask_secret(value) for key, value in _decode_credentials(row).items()
        },
        last_tested_at=row.last_tested_at,
        last_test_ok=row.last_test_ok,
        last_test_error=row.last_test_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _is_masked(value: str) -> bool:
    """A value the panel echoed back unchanged, rather than a new secret.

    Without this a panel that renders its own ``GET`` and submits the form
    untouched would overwrite every credential with bullet characters — and the
    client would only find out when a payment failed.

    The test is "contains a bullet", not "is all bullets": ``mask_secret``
    leaves the last few characters visible so an operator can recognise their
    own key, so a mask is mostly bullets with a real tail. Nothing a provider
    issues contains one.
    """
    return "•" in value


def _merge_credentials(
    stored: dict[str, str], patch: dict[str, str | None]
) -> dict[str, str]:
    merged = dict(stored)
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        elif not _is_masked(value):
            merged[key] = value
    return merged


async def list_payment_providers(session: AsyncSession) -> list[PaymentProviderOut]:
    rows = await repository.payment_providers(session)
    # The first read of a fresh installation creates the rows; without the
    # commit they are rolled back and the next read makes them again.
    if session.in_transaction():
        await session.commit()
    return [await _payments_out(session, row) for row in rows]


async def _require_provider(
    session: AsyncSession, code: PaymentProviderCode
) -> PaymentProvider:
    await repository.payment_providers(session)
    row = await repository.payment_provider(session, code)
    if row is None:  # pragma: no cover - the seeding read above just made it
        raise NotFound("Payment provider not found")
    return row


async def get_payment_provider(
    session: AsyncSession, code: PaymentProviderCode
) -> PaymentProviderOut:
    row = await _require_provider(session, code)
    if session.in_transaction():
        await session.commit()
    return await _payments_out(session, row)


async def update_payment_provider(
    session: AsyncSession, code: PaymentProviderCode, data: PaymentProviderIn
) -> PaymentProviderOut:
    row = await _require_provider(session, code)
    before = _payments_aud(row)
    credentials = _decode_credentials(row)

    if data.title is not None:
        row.title = data.title
    if data.sort_order is not None:
        row.sort_order = data.sort_order
    if data.logo_id is not None:
        await _attach_logo(session, row, data.logo_id)
    if data.credentials is not None:
        credentials = _merge_credentials(credentials, data.credentials)
        row.credentials, row.key_version = (
            encrypt(json.dumps(credentials)) if credentials else (None, None)
        )
    elif row.credentials is not None and needs_reencryption(row.key_version or 0):
        # Lazy rotation, free here because the row is being written anyway.
        row.credentials, row.key_version = encrypt(
            decrypt(row.credentials, row.key_version or 0)
        )

    if data.enabled is not None:
        if data.enabled and not credentials:
            raise ValidationFailed(
                "Add the provider's credentials before switching it on",
                field="enabled",
            )
        row.enabled = data.enabled

    await session.commit()
    await session.refresh(row)
    await _purge_site_config()
    audit_context.describe(changes=audit_context.diff(before, _payments_aud(row)))
    logger.info("payment_provider_updated", code=row.code.value, enabled=row.enabled)
    return await _payments_out(session, row)


async def _attach_logo(
    session: AsyncSession, row: PaymentProvider, new_id: uuid.UUID
) -> None:
    """Point the row at a file, releasing whatever it held before.

    ``settings/service.py::_attach_image`` does this for branding and the order
    matters the same way: link first, because that is the step that can still
    refuse, and nothing has been mutated yet.
    """
    if row.logo_id == new_id:
        return
    await uploads_service.link(session, new_id, purpose=UploadPurpose.PAYMENT_LOGO)
    if row.logo_id is not None:
        await uploads_service.unlink(session, row.logo_id)
    row.logo_id = new_id


async def _purge_site_config() -> None:
    """Drop the cached ``site-config`` after a change the site can see.

    Imported here rather than at the top because ``settings`` reads this module
    to fill ``payment_methods``, so a module-level import would close the loop —
    the same deferral ``api/deps.py`` uses for ``current_staff``.
    """
    from app.modules.settings import service as settings_service

    await settings_service.purge_cache()


async def enabled_payment_methods(session: AsyncSession) -> list[dict[str, Any]]:
    """What ``site-config`` shows (API.md §17).

    Only the enabled ones, and no ``enabled`` field: the site turns this list
    into buttons, and a disabled provider would be a button that does nothing.
    """
    return [
        {
            "code": row.code.value,
            "title": row.title,
            "logo_url": await uploads_service.url_for(session, row.logo_id),
        }
        for row in await repository.payment_providers(session)
        if row.enabled
    ]


async def payment_providers(
    session: AsyncSession,
) -> list[ConfiguredPaymentProvider]:
    """Every provider an adapter could actually charge through.

    Like ``active_credential``, this is the only path that decrypts, and it
    deliberately does **not** re-encrypt a row left on an older key: a read
    that writes would take the caller's transaction with it.
    """
    return [
        ConfiguredPaymentProvider(
            code=row.code,
            title=row.title,
            sort_order=row.sort_order,
            credentials=_decode_credentials(row),
        )
        for row in await repository.payment_providers(session)
        if row.enabled and row.credentials
    ]


async def payment_provider_adapter(
    session: AsyncSession, code: PaymentProviderCode
) -> PaymentProviderPort | None:
    """The configured adapter for one provider, or ``None``.

    The payment counterpart of ``notifier()``, and built per call for the same
    reason: the credentials behind it are edited from the panel and there are
    several worker processes, so an adapter cached at import would be both stale
    and inconsistent between them.

    ``None`` covers three cases the caller must not tell apart — no adapter
    registered for the code, the provider switched off, or no credentials
    entered. All three mean "this installation cannot charge through it", and
    which one it is says something about the installation that a customer has
    no business learning.
    """
    override = payments_provider.get_override(code)
    if override is not None:
        return override

    row = await repository.payment_provider(session, code)
    if session.in_transaction():
        await session.commit()
    if row is None or not row.enabled or not row.credentials:
        return None
    return payments_registry.build(code.value, _decode_credentials(row))


async def any_payment_provider_ready(session: AsyncSession) -> bool:
    """Whether ``system/health/`` may call payments configured (API.md §39).

    A database question on purpose — health must not reach the network, or
    every poll would spend a provider's rate limit for nothing.
    """
    rows = await repository.payment_providers(session)
    if session.in_transaction():
        await session.commit()
    return any(row.enabled and row.credentials for row in rows)


# --- social sign-in (API.md §29) ------------------------------------------------------


def _social_aud(row: SocialCredential) -> dict[str, Any]:
    """The secret is not among them; that it changed shows in ``updated_at``."""
    return {"enabled": row.enabled, "client_id": row.client_id}


def _social_out(row: SocialCredential) -> SocialCredentialOut:
    return SocialCredentialOut(
        provider=row.provider,
        enabled=row.enabled,
        # In the clear on purpose — this value is published to every browser
        # that draws the sign-in button.
        client_id=row.client_id,
        client_secret=MASKED_PASSWORD if row.client_secret else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def list_social_credentials(session: AsyncSession) -> list[SocialCredentialOut]:
    rows = await repository.social_credentials(session)
    if session.in_transaction():
        await session.commit()
    return [_social_out(row) for row in rows]


async def update_social_credential(
    session: AsyncSession, provider: SocialProviderCode, data: SocialCredentialIn
) -> SocialCredentialOut:
    await repository.social_credentials(session)
    row = await repository.social_credential(session, provider)
    if row is None:  # pragma: no cover - the seeding read above just made it
        raise NotFound("Social provider not found")
    before = _social_aud(row)

    if data.client_id is not None:
        row.client_id = data.client_id.strip() or None
    if data.client_secret is not None:
        if data.client_secret:
            row.client_secret, row.key_version = encrypt(data.client_secret)
        else:
            row.client_secret, row.key_version = None, None
    elif row.client_secret is not None and needs_reencryption(row.key_version or 0):
        # Lazy rotation, free here because the row is being written anyway.
        row.client_secret, row.key_version = encrypt(
            decrypt(row.client_secret, row.key_version or 0)
        )

    if data.enabled is not None:
        if data.enabled and not row.client_id:
            # The client id is what a token is checked against, so without it
            # there is nothing to verify against and the button would fail on
            # the first press.
            raise ValidationFailed(
                "Add the provider's client id before switching it on",
                field="enabled",
            )
        row.enabled = data.enabled

    await session.commit()
    await session.refresh(row)
    audit_context.describe(changes=audit_context.diff(before, _social_aud(row)))
    logger.info(
        "social_credential_updated", provider=row.provider.value, enabled=row.enabled
    )
    return _social_out(row)


async def social_verifier(
    session: AsyncSession, provider: str
) -> SocialVerifier | None:
    """Which verifier this installation signs people in with, right now.

    ``None`` means the provider is switched off or unconfigured, and the caller
    turns that into a `404` — "there is no such sign-in here", the same shape a
    switched-off section uses (API.md §28).

    Built per call rather than cached in a module global, for the reason
    ``notifier`` gives: the settings behind it are edited from the panel and
    there are several worker processes.
    """
    # Scoped to the provider it stands for, not to every name: a pinned Google
    # verifier must not make an unsupported provider start working, or the
    # tests would agree with themselves about a route nobody can reach.
    override = social.get_override()
    if override is not None and override.code == provider:
        return override

    try:
        code = SocialProviderCode(provider)
    except ValueError:
        # A name this release has no adapter for. Indistinguishable from a
        # switched-off one to the caller, and deliberately so.
        return None

    row = await repository.social_credential(session, code)
    if row is None or not row.enabled or not row.client_id:
        return None
    return GoogleVerifier(client_id=row.client_id)


__all__ = [
    "ActiveGtsCredential",
    "ConfiguredPaymentProvider",
    "activate_credential",
    "active_credential",
    "any_payment_provider_ready",
    "bootstrap_smtp",
    "create_credential",
    "delete_credential",
    "enabled_payment_methods",
    "get_credential",
    "get_payment_provider",
    "get_smtp",
    "gts_base_url",
    "gts_client",
    "list_credentials",
    "list_payment_providers",
    "list_social_credentials",
    "notifier",
    "payment_providers",
    "social_verifier",
    "test_smtp",
    "update_credential",
    "update_payment_provider",
    "update_smtp",
    "update_social_credential",
]
