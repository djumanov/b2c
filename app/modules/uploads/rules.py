"""What each ``purpose`` accepts — API.md §11.

These are code, not settings. "Could two clients want different values?"
(PROJECT.md §7) — no: these are the limits that keep an installation safe and
its disk finite, not a preference. A client who wants a bigger logo is asking
for a different number here, not a different product.

The declared content type is checked **and** the bytes are. A browser will send
whatever the file extension suggests, and an attacker will send whatever they
like; a file that claims to be a PNG and starts with ``<script`` is served back
to somebody's browser later.
"""

from dataclasses import dataclass
from typing import Final

from app.providers.storage.base import UploadPurpose

MEGABYTE: Final = 1024 * 1024

PNG: Final = "image/png"
JPEG: Final = "image/jpeg"
WEBP: Final = "image/webp"
GIF: Final = "image/gif"
SVG: Final = "image/svg+xml"
ICO: Final = "image/x-icon"
PDF: Final = "application/pdf"

_RASTER: Final = frozenset({PNG, JPEG, WEBP})
_IMAGES: Final = frozenset({PNG, JPEG, WEBP, GIF, SVG})


@dataclass(frozen=True, slots=True)
class PurposeRule:
    types: frozenset[str]
    max_bytes: int
    #: Whether the file may be fetched without a token. Branding and content
    #: images are loaded by anonymous visitors of the site, by definition;
    #: documents and exports are not.
    public: bool
    #: Files the panel may upload. Exports are produced by a report job, so the
    #: upload endpoint refuses that purpose outright (API.md §37).
    uploadable: bool = True


RULES: Final[dict[UploadPurpose, PurposeRule]] = {
    UploadPurpose.LOGO: PurposeRule(_IMAGES, 2 * MEGABYTE, public=True),
    UploadPurpose.FAVICON: PurposeRule(_IMAGES | {ICO}, 1 * MEGABYTE, public=True),
    # Store icons are raster only — neither store accepts an SVG.
    UploadPurpose.APP_ICON: PurposeRule(_RASTER, 2 * MEGABYTE, public=True),
    UploadPurpose.BLOG_COVER: PurposeRule(_IMAGES, 5 * MEGABYTE, public=True),
    UploadPurpose.PROMO_BANNER: PurposeRule(_IMAGES, 5 * MEGABYTE, public=True),
    UploadPurpose.BANNER: PurposeRule(_IMAGES, 5 * MEGABYTE, public=True),
    # SVG is allowed here where it is not on a store icon: payment brands
    # publish their marks as SVG, and this file comes from the panel — a staff
    # member the installation already trusts.
    UploadPurpose.PAYMENT_LOGO: PurposeRule(_IMAGES, 1 * MEGABYTE, public=True),
    UploadPurpose.DOCUMENT: PurposeRule(_IMAGES | {PDF}, 10 * MEGABYTE, public=False),
    UploadPurpose.EXPORT: PurposeRule(
        frozenset(), 25 * MEGABYTE, public=False, uploadable=False
    ),
}

#: The extension a stored file gets, chosen from the type we **validated** and
#: never from the name the client sent. The name is attacker-controlled, and the
#: extension is what decides the ``Content-Type`` the bytes come back with: a
#: "PNG" called ``x.html`` would otherwise be served as HTML from this
#: application's own origin.
EXTENSION_FOR: Final[dict[str, str]] = {
    PNG: ".png",
    JPEG: ".jpg",
    WEBP: ".webp",
    GIF: ".gif",
    SVG: ".svg",
    ICO: ".ico",
    PDF: ".pdf",
}

#: The other direction, for the route that serves the bytes. It is the exact
#: inverse of ``EXTENSION_FOR`` plus the aliases a key written before that map
#: existed may carry; anything else is not a type this application ever stored
#: and is served as an opaque download.
MIME_FOR_EXTENSION: Final[dict[str, str]] = {
    **{ext: mime for mime, ext in EXTENSION_FOR.items()},
    ".jpeg": JPEG,
}

#: What an unrecognised extension is served as. Not a type any browser will
#: execute or render in place.
FALLBACK_MIME: Final = "application/octet-stream"

#: Leading bytes that must match the declared type. SVG is absent on purpose —
#: it is XML, so it has no fixed signature and is checked separately.
_SIGNATURES: Final[dict[str, tuple[bytes, ...]]] = {
    PNG: (b"\x89PNG\r\n\x1a\n",),
    JPEG: (b"\xff\xd8\xff",),
    GIF: (b"GIF87a", b"GIF89a"),
    ICO: (b"\x00\x00\x01\x00",),
    PDF: (b"%PDF-",),
}


def rule_for(purpose: UploadPurpose) -> PurposeRule:
    return RULES[purpose]


def extension_for(content_type: str) -> str:
    """The extension to store a validated type under. Empty if unknown.

    Unknown cannot happen for an uploaded file — the type whitelist runs first
    — but an extensionless key is still safe: it comes back as an opaque
    download rather than as something a browser will run.
    """
    return EXTENSION_FOR.get(content_type, "")


def mime_for_key(key: str) -> str:
    """What to serve a stored key as, without asking the database.

    The extension is trustworthy because this application writes it from the
    validated content type (``extension_for``), so the map is exact rather than
    a guess. ``mimetypes.guess_type`` is deliberately not used: it would happily
    return ``text/html``.
    """
    _, dot, suffix = key.rpartition(".")
    if not dot:
        return FALLBACK_MIME
    return MIME_FOR_EXTENSION.get(f".{suffix.lower()}", FALLBACK_MIME)


def content_matches(content_type: str, content: bytes) -> bool:
    """Do the bytes look like what the upload claims to be?"""
    if content_type == WEBP:
        # RIFF container: "RIFF" + four size bytes + "WEBP".
        return content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    if content_type == SVG:
        head = content[:512].lstrip().lower()
        return head.startswith((b"<?xml", b"<svg", b"<!doctype svg"))
    signatures = _SIGNATURES.get(content_type)
    if signatures is None:
        # Nothing to check against; the type whitelist already did its work.
        return True
    return content.startswith(signatures)


#: The largest anything may be, whatever the purpose. Mirrors the proxy's
#: ``client_max_body_size`` (docker/nginx.conf) so the two cannot drift into a
#: state where nginx rejects what the API would have accepted.
MAX_UPLOAD_BYTES: Final = max(rule.max_bytes for rule in RULES.values())


__all__ = [
    "EXTENSION_FOR",
    "FALLBACK_MIME",
    "MAX_UPLOAD_BYTES",
    "MEGABYTE",
    "MIME_FOR_EXTENSION",
    "RULES",
    "PurposeRule",
    "content_matches",
    "extension_for",
    "mime_for_key",
    "rule_for",
]
