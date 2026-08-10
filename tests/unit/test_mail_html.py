"""The branded mail shell — ``providers/notifications/html.py``.

What is pinned down is what the reader ends up looking at: the code is in the
message, the installation's own colour is on it, and nothing the panel typed
can climb out of an attribute and become markup.
"""

from app.providers.notifications.html import DEFAULT_PRIMARY, MailBrand, render

BRAND = MailBrand(name="Brand Travel", primary="#123456")


def test_the_code_is_in_the_message() -> None:
    page = render(
        brand=BRAND,
        heading="Ro'yxatdan o'tishni tasdiqlang",
        paragraphs=("Quyidagi kodni kiriting.",),
        code="0421",
        footnotes=("Kod 10 daqiqa amal qiladi.",),
    )

    assert "0421" in page
    assert "Quyidagi kodni kiriting." in page
    assert "Kod 10 daqiqa amal qiladi." in page
    assert "Brand Travel" in page
    # The installation's colour, not the default one.
    assert "#123456" in page
    assert page.startswith("<!doctype html>")


def test_a_message_without_a_code_has_no_box() -> None:
    page = render(brand=BRAND, heading="Sizda akkaunt bor", paragraphs=("Salom.",))
    assert "letter-spacing" not in page


def test_a_long_token_is_not_set_like_a_four_digit_code() -> None:
    """A reset token in 30px with 8px of tracking runs off a phone."""
    token = "x" * 43
    page = render(brand=BRAND, heading="Password reset", paragraphs=(), code=token)

    assert token in page
    assert "letter-spacing:0" in page
    assert "word-break:break-all" in page


def test_a_colour_that_is_not_a_colour_falls_back() -> None:
    """The value is interpolated into a ``style`` attribute, so it is checked."""
    page = render(
        brand=MailBrand(name="Brand", primary='red;" onload="steal()'),
        heading="Salom",
        paragraphs=(),
    )

    assert "onload" not in page
    assert DEFAULT_PRIMARY in page


def test_panel_text_is_escaped_rather_than_rendered() -> None:
    page = render(
        brand=MailBrand(name="<script>alert(1)</script>"),
        heading="Salom & xayr",
        paragraphs=("<b>qalin</b>",),
    )

    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "&lt;b&gt;qalin&lt;/b&gt;" in page
    assert "Salom &amp; xayr" in page


def test_a_relative_logo_is_dropped_for_the_name() -> None:
    """A mail client has no page to resolve ``/uploads/…`` against."""
    page = render(
        brand=MailBrand(name="Brand", logo_url="/uploads/logo.png"),
        heading="Salom",
        paragraphs=(),
    )

    assert "<img" not in page
    assert "Brand" in page


def test_an_absolute_logo_is_shown() -> None:
    page = render(
        brand=MailBrand(name="Brand", logo_url="https://brand.uz/uploads/logo.png"),
        heading="Salom",
        paragraphs=(),
    )

    assert '<img src="https://brand.uz/uploads/logo.png"' in page


def test_a_fresh_installation_invents_no_brand() -> None:
    """Nothing configured yet: the ``From`` header is who it is from."""
    page = render(brand=MailBrand(name=""), heading="Salom", paragraphs=())

    assert "<img" not in page
    assert "Bu avtomatik xabar" in page
