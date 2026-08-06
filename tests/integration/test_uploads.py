"""Uploading a file and getting it back — API.md §11.

The branding module is about to reference these by id, so what matters here is
that a file survives the round trip, that a file which is not what it claims to
be does not, and that one nobody ever attached goes away on its own.
"""

from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.mixins import utcnow
from app.modules.staff.models import Staff
from app.modules.uploads import service
from app.modules.uploads.models import Upload
from app.providers.storage.base import UploadPurpose
from app.providers.storage.local import LocalStorage
from tests.integration.conftest import headers_for

UPLOADS = "/api/v1/admin/uploads/"

#: A real 1×1 PNG. The signature check is one of the things under test, so a
#: string of `b"fake"` would not do.
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


async def _upload(
    api: AsyncClient,
    staff: Staff,
    *,
    content: bytes = PNG,
    filename: str = "logo.png",
    mime: str = "image/png",
    purpose: str = "logo",
) -> tuple[int, dict[str, object]]:
    response = await api.post(
        UPLOADS,
        headers=headers_for(staff),
        files={"file": (filename, content, mime)},
        data={"purpose": purpose},
    )
    body = response.json()
    return response.status_code, body


# --- the round trip -----------------------------------------------------------------


async def test_a_logo_can_be_uploaded_and_read_back(
    api: AsyncClient, owner: Staff
) -> None:
    status, body = await _upload(api, owner)
    assert status == 201, body

    data = body["data"]
    assert data["mime"] == "image/png"
    assert data["size"] == len(PNG)
    assert data["purpose"] == "logo"
    assert data["filename"] == "logo.png"
    # The contract's example returns a `url`; the key never contains the
    # client's own filename.
    assert data["url"].startswith("/uploads/logo/")
    assert "logo.png" not in data["url"]

    fetched = await api.get(data["url"])
    assert fetched.status_code == 200
    assert fetched.content == PNG
    # Loaded by an `<img>` tag, so it must not be enveloped.
    assert fetched.headers["content-type"] == "image/png"


async def test_the_served_type_comes_from_the_bytes_not_the_filename(
    api: AsyncClient, owner: Staff
) -> None:
    """A "PNG" called `x.html` must not come back as HTML.

    The extension decides the `Content-Type`, and served from this
    installation's own origin `text/html` is stored XSS: the file is public,
    needs no token, and shares the panel's cookies.
    """
    status, body = await _upload(
        api,
        owner,
        content=PNG + b"<script>alert(1)</script>",
        filename="x.html",
        mime="image/png",
    )
    assert status == 201, body

    url = str(body["data"]["url"])
    assert url.endswith(".png")
    assert ".html" not in url

    fetched = await api.get(url)
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/png"


async def test_a_served_file_forbids_sniffing_and_scripting(
    api: AsyncClient, owner: Staff
) -> None:
    """An SVG is XML, and XML can carry a `<script>`.

    The signature check cannot see that — a script-bearing SVG still starts
    with `<svg`. The sandbox is what makes it harmless: opened directly it
    lands in an origin of its own, with nothing of this installation in reach.
    """
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    status, body = await _upload(
        api, owner, content=svg, filename="logo.svg", mime="image/svg+xml"
    )
    assert status == 201, body

    fetched = await api.get(str(body["data"]["url"]))
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/svg+xml"
    assert fetched.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in fetched.headers["content-security-policy"]


async def test_a_public_file_needs_no_token(api: AsyncClient, owner: Staff) -> None:
    """The website is anonymous; its logo cannot require a login."""
    _, body = await _upload(api, owner)

    response = await api.get(str(body["data"]["url"]))

    assert response.status_code == 200


async def test_a_document_is_not_public(api: AsyncClient, owner: Staff) -> None:
    _, body = await _upload(
        api,
        owner,
        content=PDF,
        filename="passport.pdf",
        mime="application/pdf",
        purpose="document",
    )
    url = str(body["data"]["url"])
    assert url.startswith("/uploads/private/document/")

    anonymous = await api.get(url)
    assert anonymous.status_code == 401

    with_token = await api.get(url, headers=headers_for(owner))
    assert with_token.status_code == 200
    assert with_token.headers["cache-control"] == "private, no-store"


async def test_a_private_file_is_not_reachable_on_the_public_path(
    api: AsyncClient, owner: Staff
) -> None:
    _, body = await _upload(
        api,
        owner,
        content=PDF,
        filename="p.pdf",
        mime="application/pdf",
        purpose="document",
    )
    private_url = str(body["data"]["url"])

    response = await api.get(private_url.replace("/uploads/private/", "/uploads/"))

    assert response.status_code == 404


async def test_uploading_requires_a_token(api: AsyncClient) -> None:
    response = await api.post(
        UPLOADS,
        files={"file": ("logo.png", PNG, "image/png")},
        data={"purpose": "logo"},
    )

    assert response.status_code == 401


# --- what is refused ----------------------------------------------------------------


async def test_a_file_that_is_not_what_it_claims_is_refused(
    api: AsyncClient, owner: Staff
) -> None:
    """It would be served back to somebody's browser later."""
    status, body = await _upload(
        api, owner, content=b"<script>alert(1)</script>", filename="logo.png"
    )

    assert status == 422
    assert body["errors"][0]["field"] == "file"


async def test_a_type_the_purpose_does_not_allow_is_refused(
    api: AsyncClient, owner: Staff
) -> None:
    status, body = await _upload(
        api, owner, content=PDF, filename="logo.pdf", mime="application/pdf"
    )

    assert status == 422
    assert body["errors"][0]["field"] == "file"


async def test_an_oversized_file_is_refused(api: AsyncClient, owner: Staff) -> None:
    # A favicon is capped at 1 MB.
    oversized = PNG + b"\x00" * (2 * 1024 * 1024)
    status, body = await _upload(
        api, owner, content=oversized, filename="favicon.png", purpose="favicon"
    )

    assert status == 422
    assert "MB" in body["errors"][0]["message"]


async def test_an_empty_file_is_refused(api: AsyncClient, owner: Staff) -> None:
    status, _ = await _upload(api, owner, content=b"")

    assert status == 422


async def test_an_export_cannot_be_uploaded(api: AsyncClient, owner: Staff) -> None:
    """Exports are produced by a report job, not handed in by a person."""
    status, body = await _upload(api, owner, purpose="export")

    assert status == 422
    assert body["errors"][0]["field"] == "purpose"


async def test_an_unknown_purpose_is_refused(api: AsyncClient, owner: Staff) -> None:
    status, body = await _upload(api, owner, purpose="whatever")

    assert status == 422
    assert body["errors"][0]["field"] == "purpose"


async def test_traversal_out_of_the_storage_root_is_refused(
    api: AsyncClient, owner: Staff
) -> None:
    response = await api.get("/uploads/%2e%2e%2f%2e%2e%2fetc%2fpasswd")

    assert response.status_code == 404


async def test_an_unknown_key_is_a_404(api: AsyncClient) -> None:
    assert (await api.get("/uploads/logo/ab/nothing.png")).status_code == 404


# --- the journal --------------------------------------------------------------------


async def test_an_upload_is_audited(api: AsyncClient, owner: Staff) -> None:
    await _upload(api, owner)

    entries = await api.get(
        "/api/v1/admin/system/audit/?resource=uploads", headers=headers_for(owner)
    )
    entry = entries.json()["data"][0]
    assert entry["action"] == "create"
    assert entry["actor"]["id"] == str(owner.id)


# --- attachment and the sweep (API.md §11) ------------------------------------------


async def _row(session: AsyncSession, upload_id: str) -> Upload:
    row = await session.scalar(select(Upload).where(Upload.id == upload_id))
    assert row is not None
    return row


async def test_a_file_nobody_attached_is_swept(
    api: AsyncClient, session: AsyncSession, owner: Staff, storage: LocalStorage
) -> None:
    _, body = await _upload(api, owner)
    upload = await _row(session, str(body["data"]["id"]))
    key = upload.storage_key
    assert await storage.exists(key)

    # Older than the 24-hour grace period.
    upload.created_at = utcnow() - service.ORPHAN_GRACE - timedelta(minutes=1)
    await session.commit()

    assert await service.sweep_orphans(session) == 1
    # The row survives as a record; the bytes do not.
    assert not await storage.exists(key)
    await session.refresh(upload)
    assert upload.is_deleted


async def test_a_file_something_uses_is_left_alone(
    api: AsyncClient, session: AsyncSession, owner: Staff, storage: LocalStorage
) -> None:
    _, body = await _upload(api, owner)
    upload = await _row(session, str(body["data"]["id"]))

    await service.link(session, upload.id, purpose=UploadPurpose.LOGO)
    upload.created_at = utcnow() - service.ORPHAN_GRACE - timedelta(minutes=1)
    await session.commit()

    assert await service.sweep_orphans(session) == 0
    assert await storage.exists(upload.storage_key)


async def test_a_recent_orphan_is_left_alone(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    """Twenty-four hours is a grace period, not a formality."""
    await _upload(api, owner)

    assert await service.sweep_orphans(session) == 0


async def test_linking_the_wrong_purpose_is_refused(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    """A document cannot become a logo by being pointed at from one."""
    _, body = await _upload(
        api,
        owner,
        content=PDF,
        filename="p.pdf",
        mime="application/pdf",
        purpose="document",
    )
    upload = await _row(session, str(body["data"]["id"]))

    from app.api.errors import ValidationFailed

    try:
        await service.link(session, upload.id, purpose=UploadPurpose.LOGO)
    except ValidationFailed as exc:
        assert "document" in str(exc)
    else:  # pragma: no cover - the assertion above is the point
        raise AssertionError("a document was accepted as a logo")


async def test_unlinking_makes_a_file_sweepable_again(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    _, body = await _upload(api, owner)
    upload = await _row(session, str(body["data"]["id"]))
    await service.link(session, upload.id)
    await session.commit()

    await service.unlink(session, upload.id)
    upload.created_at = utcnow() - service.ORPHAN_GRACE - timedelta(minutes=1)
    await session.commit()

    assert await service.sweep_orphans(session) == 1
