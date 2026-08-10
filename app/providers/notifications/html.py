"""The mail shell: one branded HTML message, built from strings.

A code sent as bare text arrives looking like nothing the client sent — the
site is theirs, and the mail that carries a login code is the first thing a new
customer sees of it. So the message goes out twice in one envelope: the plain
text the caller wrote, and this dressed-up version beside it (`smtp.py`).

**Why the brand comes in as an argument.** `providers/` never imports a module
(ARCHITECTURE.md §4), and the colours live in `settings`. So the caller — which
is a module, and may ask another module for them — hands over a `MailBrand`
rather than this file reaching for one. That is the same reason `SmtpNotifier`
is handed a `SmtpConfig`.

**Why the markup looks like 2005.** Mail clients are not browsers. Gmail strips
`<style>` blocks, Outlook renders through Word, and neither can be relied on
for flex or grid. Nested tables with inline styles are what survives all of
them, so that is what this writes.

This is not a template engine and does not want to become one. When
`notifications/` (API.md §36) arrives with panel-edited templates, that module
owns rendering and this stays what it is: the shell transactional mail uses.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

#: Used when the installation has not chosen one, and when what it chose is not
#: a colour — this string is interpolated into a ``style`` attribute.
DEFAULT_PRIMARY = "#0A5CFF"

_HEX_COLOR = re.compile(r"\A#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\Z")

_FONT_STACK = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
    "Helvetica, Arial, sans-serif"
)


@dataclass(frozen=True, slots=True)
class MailBrand:
    """How this installation signs its mail.

    Deliberately three fields. Everything else the panel knows about branding
    — fonts, favicon, app icon — either has no meaning in a mail client or
    cannot be relied on to render, and a field nothing uses is a field somebody
    later has to work out the purpose of.
    """

    name: str
    primary: str = DEFAULT_PRIMARY
    #: Only an absolute ``https`` URL is any use: a mail client has no page to
    #: resolve ``/uploads/…`` against. Anything else is dropped for the name.
    logo_url: str | None = None

    @property
    def color(self) -> str:
        return self.primary if _HEX_COLOR.match(self.primary) else DEFAULT_PRIMARY

    @property
    def logo(self) -> str | None:
        url = (self.logo_url or "").strip()
        return url if url.startswith("https://") else None


def render(
    *,
    brand: MailBrand,
    heading: str,
    paragraphs: Sequence[str],
    code: str | None = None,
    footnotes: Sequence[str] = (),
) -> str:
    """The whole message as one HTML document.

    ``code`` gets a box of its own rather than a line of text: it is the one
    thing the reader came for, and on a phone a four-digit code inside a
    sentence is something to squint at. ``footnotes`` are the lines that only
    make sense *after* it — how long it lasts, what to do if it was not asked
    for — set smaller, below the box.
    """
    color = brand.color
    name = escape(brand.name)
    logo = brand.logo

    # A fresh installation has set neither, and an invented brand name would be
    # worse than none: the message says who it is from in the ``From`` header
    # either way.
    if logo:
        masthead = (
            f'<img src="{escape(logo)}" alt="{name}" height="32" '
            'style="display:block;border:0;max-height:32px;">'
        )
    elif name:
        masthead = (
            f'<span style="font-size:18px;font-weight:700;color:{color};">{name}</span>'
        )
    else:
        masthead = ""

    body = "".join(
        '<p style="margin:0 0 14px;font-size:15px;line-height:1.6;color:#3c4257;">'
        f"{escape(text)}</p>"
        for text in paragraphs
    )

    notice = "Bu avtomatik xabar, unga javob yozmang."
    sign = f"{name} — {notice[0].lower()}{notice[1:]}" if name else notice

    box = ""
    if code is not None:
        # A four-digit code wants to be large and spaced out; a reset token is
        # forty characters and would run off the side of a phone dressed that
        # way. Same box, two sizes, rather than two boxes.
        short = len(code) <= 12
        box = (
            f'<div style="margin:4px 0 20px;padding:18px 12px;border-radius:10px;'
            f"background:#f6f8fc;border:1px solid {color};text-align:center;"
            f"font-weight:700;color:{color};"
            f"font-size:{'30px' if short else '15px'};"
            f"letter-spacing:{'8px' if short else '0'};"
            f'{"" if short else "word-break:break-all;"}">'
            f"{escape(code)}</div>"
        )

    small = "".join(
        '<p style="margin:0 0 10px;font-size:13px;line-height:1.55;color:#6b7280;">'
        f"{escape(text)}</p>"
        for text in footnotes
    )

    return (
        "<!doctype html>"
        '<html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(heading)}</title></head>"
        f'<body style="margin:0;padding:0;background:#f1f3f6;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="background:#f1f3f6;">'
        '<tr><td align="center" style="padding:24px 12px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'width="480" style="width:100%;max-width:480px;background:#ffffff;'
        f'border-radius:14px;font-family:{_FONT_STACK};">'
        f'<tr><td style="padding:26px 28px 0;">{masthead}</td></tr>'
        f'<tr><td style="padding:18px 28px 0;">'
        f'<h1 style="margin:0 0 14px;font-size:20px;line-height:1.35;'
        f'color:#1a1f36;font-weight:600;">{escape(heading)}</h1>'
        f"{body}{box}{small}</td></tr>"
        f'<tr><td style="padding:4px 28px 26px;border-top:1px solid #eceef2;">'
        f'<p style="margin:14px 0 0;font-size:12px;line-height:1.5;color:#8a93a5;">'
        f"{sign}</p>"
        "</td></tr></table></td></tr></table></body></html>"
    )


__all__ = ["DEFAULT_PRIMARY", "MailBrand", "render"]
