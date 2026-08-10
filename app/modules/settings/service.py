"""Settings, and the document the whole front end starts from.

API.md §28 for the panel, §17 for ``site-config``. The second is why the first
exists: everything here is a value the client changes without anybody
redeploying anything (PROJECT.md §1, §7).

Every write ends in ``cache.purge()``. That single line is the difference
between the promise and the marketing — and it is applied in ``_save`` rather
than in each endpoint, so an endpoint added later cannot skip it.
"""

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ValidationFailed
from app.core import i18n
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.modules.audit import context as audit_context
from app.modules.integrations import service as integrations_service
from app.modules.settings import cache, defaults, repository
from app.modules.settings.models import Branding, Site
from app.modules.settings.schemas import (
    BrandingIn,
    BrandingOut,
    CurrenciesIn,
    CurrenciesOut,
    FeaturesIn,
    FeaturesOut,
    LanguagesIn,
    LanguagesOut,
    ProductOut,
    SiteConfigOut,
    SiteIn,
    SiteOut,
)
from app.modules.uploads import service as uploads_service
from app.providers.notifications.html import DEFAULT_PRIMARY, MailBrand
from app.providers.storage.base import UploadPurpose

logger = get_logger(__name__)

#: Which purpose each branding slot expects, so a favicon cannot be attached
#: where a logo belongs (``uploads.service.link`` enforces it).
_IMAGE_SLOTS: dict[str, UploadPurpose] = {
    "logo_id": UploadPurpose.LOGO,
    "favicon_id": UploadPurpose.FAVICON,
    "app_icon_id": UploadPurpose.APP_ICON,
}


async def _save(session: AsyncSession) -> None:
    """Commit a settings change and drop the cached document."""
    await session.commit()
    await cache.purge()


# --- branding (API.md §28) ------------------------------------------------------


async def _branding_out(session: AsyncSession, row: Branding) -> BrandingOut:
    return BrandingOut(
        colors=row.colors,
        font_family=row.font_family,
        logo_id=row.logo_id,
        favicon_id=row.favicon_id,
        app_icon_id=row.app_icon_id,
        app_name=row.app_name,
        logo_url=await uploads_service.url_for(session, row.logo_id),
        favicon_url=await uploads_service.url_for(session, row.favicon_id),
        app_icon_url=await uploads_service.url_for(session, row.app_icon_id),
    )


async def get_branding(session: AsyncSession) -> BrandingOut:
    return await _branding_out(session, await repository.branding(session))


async def _attach_image(
    session: AsyncSession, row: Branding, slot: str, new_id: uuid.UUID
) -> None:
    """Point a branding slot at a file, releasing whatever it held before."""
    previous: uuid.UUID | None = getattr(row, slot)
    if previous == new_id:
        return
    await uploads_service.link(session, new_id, purpose=_IMAGE_SLOTS[slot])
    if previous is not None:
        # Released rather than deleted: a client who swaps a logo and changes
        # their mind an hour later still has the old one.
        await uploads_service.unlink(session, previous)
    setattr(row, slot, new_id)


async def update_branding(session: AsyncSession, data: BrandingIn) -> BrandingOut:
    row = await repository.branding(session)
    before = {
        "colors": dict(row.colors),
        "font_family": row.font_family,
        "app_name": row.app_name,
        **{slot: str(getattr(row, slot)) for slot in _IMAGE_SLOTS},
    }

    if data.colors is not None:
        # Merged, not replaced: the panel sends the one swatch somebody moved.
        row.colors = {**row.colors, **data.colors}
    if data.font_family is not None:
        row.font_family = data.font_family
    if data.app_name is not None:
        row.app_name = data.app_name
    for slot in _IMAGE_SLOTS:
        new_id = getattr(data, slot)
        if new_id is not None:
            await _attach_image(session, row, slot, new_id)

    await _save(session)
    await session.refresh(row)

    after = {
        "colors": dict(row.colors),
        "font_family": row.font_family,
        "app_name": row.app_name,
        **{slot: str(getattr(row, slot)) for slot in _IMAGE_SLOTS},
    }
    audit_context.describe(changes=audit_context.diff(before, after))
    return await _branding_out(session, row)


# --- site ------------------------------------------------------------------------


def _site_out(row: Site) -> SiteOut:
    return SiteOut(
        name=row.name,
        domain=row.domain,
        support_phone=row.support_phone,
        support_email=row.support_email,
        social=row.social,
    )


async def get_site(session: AsyncSession) -> SiteOut:
    return _site_out(await repository.site(session))


async def update_site(session: AsyncSession, data: SiteIn) -> SiteOut:
    row = await repository.site(session)
    before = _site_out(row).model_dump()

    if data.name is not None:
        row.name = {**row.name, **data.name}
    if data.domain is not None:
        row.domain = data.domain.strip() or None
    if data.support_phone is not None:
        row.support_phone = data.support_phone.strip() or None
    if data.support_email is not None:
        row.support_email = str(data.support_email)
    if data.social is not None:
        row.social = data.social

    await _save(session)
    await session.refresh(row)
    audit_context.describe(
        changes=audit_context.diff(before, _site_out(row).model_dump())
    )
    return _site_out(row)


# --- languages and currencies -------------------------------------------------------


async def get_languages(session: AsyncSession) -> LanguagesOut:
    row = await repository.languages(session)
    return LanguagesOut(default=row.default, available=row.available)


async def update_languages(session: AsyncSession, data: LanguagesIn) -> LanguagesOut:
    row = await repository.languages(session)
    before = LanguagesOut(default=row.default, available=row.available).model_dump()

    available = data.available if data.available is not None else row.available
    default = data.default if data.default is not None else row.default
    # The default has to be one of the languages on offer, or the fallback
    # chain in API.md §7 starts at a language nothing is written in.
    if default not in available:
        raise ValidationFailed(
            f"The default language {default!r} must be one of the available "
            f"languages ({', '.join(available)})",
            field="default",
        )
    row.available, row.default = available, default

    await _save(session)
    after = LanguagesOut(default=row.default, available=row.available)
    audit_context.describe(changes=audit_context.diff(before, after.model_dump()))
    return after


async def get_currencies(session: AsyncSession) -> CurrenciesOut:
    row = await repository.currencies(session)
    return CurrenciesOut(default=row.default, available=row.available)


async def update_currencies(session: AsyncSession, data: CurrenciesIn) -> CurrenciesOut:
    row = await repository.currencies(session)
    before = CurrenciesOut(default=row.default, available=row.available).model_dump()

    available = data.available if data.available is not None else row.available
    default = data.default if data.default is not None else row.default
    if default not in available:
        raise ValidationFailed(
            f"The default currency {default!r} must be one of the available "
            f"currencies ({', '.join(available)})",
            field="default",
        )
    row.available, row.default = available, default

    await _save(session)
    after = CurrenciesOut(default=row.default, available=row.available)
    audit_context.describe(changes=audit_context.diff(before, after.model_dump()))
    return after


# --- features and products -----------------------------------------------------------


def _all_flags(stored: dict[str, bool]) -> dict[str, bool]:
    """Defaults first, so a flag added in a new version appears immediately."""
    return {**defaults.FEATURE_DEFAULTS, **stored}


async def get_features(session: AsyncSession) -> FeaturesOut:
    row = await repository.features(session)
    return FeaturesOut(flags=_all_flags(row.flags))


async def update_features(session: AsyncSession, data: FeaturesIn) -> FeaturesOut:
    row = await repository.features(session)
    unknown = sorted(set(data.flags) - set(defaults.FEATURE_DEFAULTS))
    if unknown:
        raise ValidationFailed(
            f"unknown feature(s): {', '.join(unknown)}", field="flags"
        )
    before = _all_flags(row.flags)
    row.flags = {**row.flags, **data.flags}

    await _save(session)
    after = _all_flags(row.flags)
    audit_context.describe(changes=audit_context.diff(before, after))
    return FeaturesOut(flags=after)


async def get_products(session: AsyncSession) -> list[ProductOut]:
    rows = await repository.products(session)
    if session.in_transaction():
        await session.commit()
    return [ProductOut(code=row.code, enabled=row.enabled) for row in rows]


# --- the public document (API.md §17) -----------------------------------------------


async def _assemble(session: AsyncSession) -> dict[str, Any]:
    """Build the document with its translations still intact.

    One cache entry then serves every language; collapsing happens per request.
    """
    branding = await repository.branding(session)
    site = await repository.site(session)
    languages = await repository.languages(session)
    currencies = await repository.currencies(session)
    features = await repository.features(session)
    products = await repository.products(session)
    # First read of a fresh installation creates the rows above.
    if session.in_transaction():
        await session.commit()

    return {
        "site": {
            "name": site.name,
            "domain": site.domain,
            "support_phone": site.support_phone,
            "support_email": site.support_email,
            "social": site.social,
        },
        "branding": {
            "logo_url": await uploads_service.url_for(session, branding.logo_id),
            "favicon_url": await uploads_service.url_for(session, branding.favicon_id),
            "colors": branding.colors,
            "font_family": branding.font_family,
            "app_name": branding.app_name,
        },
        "languages": {"default": languages.default, "available": languages.available},
        "currencies": {
            "default": currencies.default,
            "available": currencies.available,
        },
        "products": [{"code": row.code, "enabled": row.enabled} for row in products],
        # Asked of the module that owns the answer (API.md §29). Only the
        # enabled ones and no ``enabled`` field, unlike ``products`` above:
        # the site turns this into buttons, and a switched-off provider would
        # be a button that does nothing.
        "payment_methods": await integrations_service.enabled_payment_methods(session),
        # The menu builder is not in the first release (API.md §41).
        "menu": [],
        "features": _all_flags(features.flags),
    }


def _flatten(document: dict[str, Any], *, requested: str | None) -> SiteConfigOut:
    """Collapse translated fields to one language (API.md §7)."""
    languages = document["languages"]
    resolved = i18n.resolve(
        document["site"]["name"],
        requested=requested,
        default=languages["default"],
        available=languages["available"],
    )
    site = {**document["site"], "name": resolved.value}
    return SiteConfigOut(
        site=site,
        branding=document["branding"],
        languages=LanguagesOut(**languages),
        currencies=CurrenciesOut(**document["currencies"]),
        products=[ProductOut(**item) for item in document["products"]],
        payment_methods=document["payment_methods"],
        menu=document["menu"],
        features=document["features"],
        lang=resolved.lang or languages["default"],
    )


def etag_for(config: SiteConfigOut) -> str:
    """A strong validator over what the client is actually about to receive.

    Computed after flattening, because two languages produce two documents from
    one cache entry and an ETag that ignored that would let a browser keep the
    wrong one.
    """
    payload = json.dumps(config.model_dump(), sort_keys=True, default=str)
    return f'"{hashlib.sha256(payload.encode()).hexdigest()[:32]}"'


async def site_config(
    session: AsyncSession, *, requested: str | None = None
) -> SiteConfigOut:
    document = await cache.read()
    if document is None:
        document = await _assemble(session)
        await cache.write(document)
    return _flatten(document, requested=requested)


# --- who may call this installation from a browser (API.md §15) ---------------------


def _origins_for(domain: str | None) -> list[str]:
    """The browser origins a site domain implies.

    A client types their domain, not a list of origins — ``brand-a.uz``, or
    with a scheme in front if that is how they think of it. Both spellings mean
    the same installation, and so does the ``www`` in front of it, so all of
    them are accepted. HTTPS only: PROJECT.md §13 requires it everywhere, and an
    ``http`` origin here would be a way to opt back out of that from the panel.
    """
    if not domain:
        return []
    # Scheme first, then path: stripping slashes off "https://" the other way
    # round leaves "https:" behind and calls it a host.
    host = domain.strip().split("://", 1)[-1].split("/", 1)[0].strip().lower()
    if not host:
        return []
    origins = [f"https://{host}"]
    if not host.startswith("www."):
        origins.append(f"https://www.{host}")
    return origins


async def _document() -> dict[str, Any]:
    """The ``site-config`` document, without a session to read it with.

    For callers that are not handling a request of their own — a dependency, a
    mail being sent — and so have no session to pass. One Redis GET on the
    common path; on a miss it opens a session, assembles and caches. Every
    settings write purges the same key, so a change lands on the next read
    rather than on the next restart.
    """
    document = await cache.read()
    if document is None:
        async with get_sessionmaker()() as session:
            document = await _assemble(session)
        await cache.write(document)
    return document


async def cors_origins() -> list[str]:
    """Which origins the browser surfaces may be called from.

    A **database setting**, not an environment variable: the client owns their
    domain and changes it from the panel, with no deploy (PROJECT.md §7).
    """
    domain: str | None = (await _document())["site"]["domain"]
    return _origins_for(domain)


async def feature_enabled(flag: str) -> bool:
    """Is this section switched on for this installation? (API.md §28)

    An unknown flag reads as its default rather than raising: the caller is
    ``RequireFeature``, which already refused unknown flags at import.
    """
    flags: dict[str, bool] = (await _document())["features"]
    return bool(flags.get(flag, defaults.FEATURE_DEFAULTS.get(flag, False)))


async def mail_brand() -> MailBrand:
    """How this installation signs the mail it sends (`providers.notifications`).

    Here rather than in the sending modules because this is the module that
    owns branding: `customers` and `staff` ask for the answer instead of
    reading `Branding` themselves (ARCHITECTURE.md §4). Session-less like the
    two above — a code is mailed from inside a transaction that is about
    something else, and this must not join it.

    The logo is made absolute here for the same reason the storage adapter
    leaves it relative: the domain is a setting, and this is where settings
    live. Without a domain there is no logo to send, and the name stands in.
    """
    document = await _document()
    languages = document["languages"]
    name = i18n.resolve_value(
        document["site"]["name"],
        requested=None,
        default=languages["default"],
        available=languages["available"],
    )
    branding = document["branding"]
    colors: dict[str, str] = branding["colors"] or {}
    origins = _origins_for(document["site"]["domain"])
    logo: str | None = branding["logo_url"]
    return MailBrand(
        name=name or "",
        primary=colors.get("primary") or DEFAULT_PRIMARY,
        logo_url=f"{origins[0]}{logo}" if logo and origins else None,
    )


async def purge_cache() -> None:
    """``POST /admin/settings/cache/purge/`` (API.md §28).

    Writes purge on their own; this is the button for the case where somebody
    changed a row by hand, or a purge was lost while Redis was restarting.
    """
    await cache.purge()


__all__ = [
    "cors_origins",
    "etag_for",
    "feature_enabled",
    "get_branding",
    "get_currencies",
    "get_features",
    "get_languages",
    "get_products",
    "get_site",
    "mail_brand",
    "purge_cache",
    "site_config",
    "update_branding",
    "update_currencies",
    "update_features",
    "update_languages",
    "update_site",
]
