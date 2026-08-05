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


class Storage(Protocol):
    async def save(
        self, *, content: bytes, filename: str, purpose: UploadPurpose
    ) -> str:
        """Store the file; returns the key it can be fetched and deleted by."""
        ...

    async def url(self, key: str) -> str:
        """A URL the client can load the file from."""
        ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...


__all__ = ["Storage", "UploadPurpose"]
