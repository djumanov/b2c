"""Whether the bytes are what the upload says they are — API.md §11.

The type a browser declares comes from the file extension, so it is a hint, not
a fact. These are the checks that turn it into one.
"""

import pytest

from app.modules.uploads import rules
from app.providers.storage.base import UploadPurpose

PNG_HEAD = b"\x89PNG\r\n\x1a\n"
JPEG_HEAD = b"\xff\xd8\xff\xe0"


@pytest.mark.parametrize(
    ("content_type", "content", "expected"),
    [
        (rules.PNG, PNG_HEAD + b"rest", True),
        (rules.PNG, b"GIF89a", False),
        (rules.JPEG, JPEG_HEAD, True),
        (rules.JPEG, PNG_HEAD, False),
        (rules.GIF, b"GIF89a...", True),
        (rules.GIF, b"GIF80a...", False),
        (rules.PDF, b"%PDF-1.7", True),
        (rules.PDF, b"<html>", False),
        (rules.ICO, b"\x00\x00\x01\x00", True),
        # WEBP is a RIFF container: the tag sits after the length.
        (rules.WEBP, b"RIFF\x00\x00\x00\x00WEBPVP8 ", True),
        (rules.WEBP, b"RIFF\x00\x00\x00\x00WAVEfmt ", False),
        # SVG is XML, so it has no signature — it is sniffed instead.
        (rules.SVG, b'<svg xmlns="http://www.w3.org/2000/svg"/>', True),
        (rules.SVG, b'  <?xml version="1.0"?><svg/>', True),
        (rules.SVG, b"<script>alert(1)</script>", False),
    ],
)
def test_content_matches(content_type: str, content: bytes, expected: bool) -> None:
    assert rules.content_matches(content_type, content) is expected


def test_every_purpose_has_a_rule() -> None:
    """A new purpose without one would raise a KeyError on first use."""
    assert set(rules.RULES) == set(UploadPurpose)


def test_exports_are_produced_not_uploaded() -> None:
    assert rules.rule_for(UploadPurpose.EXPORT).uploadable is False


@pytest.mark.parametrize(
    "purpose",
    [
        UploadPurpose.LOGO,
        UploadPurpose.FAVICON,
        UploadPurpose.APP_ICON,
        UploadPurpose.BLOG_COVER,
        UploadPurpose.PROMO_BANNER,
        UploadPurpose.BANNER,
    ],
)
def test_branding_and_content_images_are_public(purpose: UploadPurpose) -> None:
    """An anonymous visitor of the site loads these by definition."""
    assert rules.rule_for(purpose).public is True


@pytest.mark.parametrize("purpose", [UploadPurpose.DOCUMENT, UploadPurpose.EXPORT])
def test_documents_and_exports_are_not_public(purpose: UploadPurpose) -> None:
    assert rules.rule_for(purpose).public is False


def test_the_overall_cap_matches_the_proxy() -> None:
    """``client_max_body_size 25m`` in docker/nginx.conf.

    If they drift apart, nginx starts rejecting bodies the API would have
    accepted — with a bare 413 that is not in the error catalogue.
    """
    assert rules.MAX_UPLOAD_BYTES == 25 * rules.MEGABYTE
