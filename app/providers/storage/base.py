"""The ``Storage`` port. Local disk in the first release (PROJECT.md A8).

A single-server installation on a Docker volume, which doubles as the backup
unit. A client who wants S3 gets a different adapter, not different callers
(ARCHITECTURE.md §12).
"""

from enum import StrEnum
from typing import Protocol


class UploadPurpose(StrEnum):
    """What a file is for — it decides the allowed MIME types and size limits.

    Files that never get attached to anything are swept after 24 hours
    (API.md §11).
    """

    LOGO = "logo"
    FAVICON = "favicon"
    APP_ICON = "app_icon"
    BLOG_COVER = "blog_cover"
    PROMO_BANNER = "promo_banner"
    BANNER = "banner"
    DOCUMENT = "document"
    EXPORT = "export"
    #: A payment method's mark, shown on the site next to its button
    #: (API.md §17, §29). Its own purpose rather than ``LOGO``: the two are
    #: attached by different resources, and ``uploads.service.link`` takes a
    #: purpose precisely so a brand logo cannot end up on a payment button.
    PAYMENT_LOGO = "payment_logo"


class Storage(Protocol):
    async def save(
        self, *, content: bytes, extension: str, purpose: UploadPurpose
    ) -> str:
        """Store the file; returns the key it can be fetched and deleted by.

        ``extension`` comes from the **validated content type**, never from the
        name the client sent (``modules.uploads.rules.extension_for``). It is
        what decides the ``Content-Type`` the bytes are served back with, so an
        attacker-chosen one would be an attacker-chosen content type.
        """
        ...

    async def url(self, key: str, *, private: bool = False) -> str:
        """A URL the client can load the file from.

        ``private`` is a property of the stored object, not of the caller: a
        logo is fetched by anonymous visitors, an export never is. Each adapter
        honours it its own way — the local one points at a route that requires
        a staff token, an S3 one would sign the URL instead.
        """
        ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...


__all__ = ["Storage", "UploadPurpose"]
