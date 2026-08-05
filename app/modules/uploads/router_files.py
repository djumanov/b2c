"""Serving the bytes back — the other end of the ``url`` in API.md §11.

Mounted **outside** ``/api/v1``, like ``/healthz``, and deliberately not part
of the contract: API.md promises a ``url`` field, not a particular path behind
it. A client who would rather have nginx serve the volume directly adds a
``location`` and nothing in the API changes.

No envelope here either. The response is a file; wrapping it in
``{status, data, …}`` would make it unloadable by an ``<img>`` tag.

**Who may read what** comes from the purpose, which is the first segment of the
key. Branding and content images are loaded by anonymous visitors of the site
by definition; documents and exports are not, so they live behind
``/uploads/private/`` and need a staff token. That the purpose is trustworthy
is a property of the key format — this application generates every key
(``providers/storage/local.build_key``) and refuses traversal before touching
the path — and the private route is registered first, so the public catch-all
cannot swallow it.

A soft-deleted upload cannot be served, without this route needing a database
query per image: the only thing that soft-deletes one is the sweep, and the
sweep deletes the bytes in the same breath.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.api.deps import current_staff
from app.api.errors import NotFound
from app.modules.uploads import rules
from app.providers.storage import get_storage
from app.providers.storage.base import UploadPurpose
from app.providers.storage.local import PRIVATE_SEGMENT, URL_PREFIX, LocalStorage

router = APIRouter(prefix=URL_PREFIX, include_in_schema=False)

#: An hour. Long enough that a page of blog covers is not re-fetched on every
#: navigation, short enough that a replaced logo appears without a hard reload.
_PUBLIC_CACHE = "public, max-age=3600"


def _resolved(key: str, *, want_public: bool) -> FileResponse:
    """The file, or a 404 that says nothing about why."""
    head, _, _ = key.partition("/")
    try:
        purpose = UploadPurpose(head)
    except ValueError:
        raise NotFound("File not found") from None

    if rules.rule_for(purpose).public is not want_public:
        # A public URL for a document, or a private URL for a logo. Neither is
        # a URL this application ever produced.
        raise NotFound("File not found")

    storage = get_storage()
    if not isinstance(storage, LocalStorage):
        # Another adapter serves its own bytes; its ``url`` points at itself.
        raise NotFound("File not found")

    path = storage.resolve(key)
    if path is None:
        raise NotFound("File not found")

    return FileResponse(
        path,
        headers={
            "Cache-Control": _PUBLIC_CACHE if want_public else "private, no-store"
        },
    )


# Registered before the catch-all below, which would otherwise match
# ``private/…`` as a key of its own.
@router.get(f"/{PRIVATE_SEGMENT}/{{key:path}}", dependencies=[Depends(current_staff)])
async def serve_private_file(key: str) -> FileResponse:
    """Documents and exports — the panel fetches these with its token."""
    return _resolved(key, want_public=False)


@router.get("/{key:path}")
async def serve_file(key: str) -> FileResponse:
    """Logos, favicons, covers and banners — loaded by anonymous visitors."""
    return _resolved(key, want_public=True)


__all__ = ["router"]
