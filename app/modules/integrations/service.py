"""GTS credentials: several stored, exactly one in use — API.md §29.

This is the first caller of ``app/core/crypto.py``. Passwords are sealed on
the way in and masked on the way out; the only function that hands back a
readable password is ``active_credential``, and nothing routes to it.

``active_credential`` is also the module's whole outward surface. Everything
that talks to GTS reaches its account through that one call rather than
through the model, which is what keeps ``providers/gts`` from importing this
module's ``models.py`` (ARCHITECTURE.md §4).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import Conflict, NotFound, ValidationFailed
from app.core.crypto import decrypt, encrypt, mask_secret, needs_reencryption
from app.core.logging import get_logger
from app.modules.audit import context as audit_context
from app.modules.integrations import repository
from app.modules.integrations.models import GtsCredential, SmtpSettings
from app.modules.integrations.schemas import (
    CredentialCreateIn,
    CredentialOut,
    CredentialUpdateIn,
    SmtpIn,
    SmtpOut,
)

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
    agent_uid: str | None
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
        "agent_uid": row.agent_uid,
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
        agent_uid=mask_secret(row.agent_uid) if row.agent_uid else None,
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
        agent_uid=data.agent_uid or None,
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
    if data.agent_uid is not None:
        row.agent_uid = data.agent_uid or None
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
        agent_uid=row.agent_uid,
        updated_at=row.updated_at,
    )


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


__all__ = [
    "ActiveGtsCredential",
    "activate_credential",
    "active_credential",
    "create_credential",
    "delete_credential",
    "get_credential",
    "get_smtp",
    "list_credentials",
    "update_credential",
    "update_smtp",
]
