"""Turning a domain a client typed into browser origins — API.md §15."""

import pytest

from app.modules.settings.service import _origins_for


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("brand-a.uz", ["https://brand-a.uz", "https://www.brand-a.uz"]),
        # The client thinks of their site with a scheme in front, or with a
        # trailing slash, or in capitals. All of them are the same site.
        ("https://brand-a.uz", ["https://brand-a.uz", "https://www.brand-a.uz"]),
        ("https://brand-a.uz/", ["https://brand-a.uz", "https://www.brand-a.uz"]),
        ("  Brand-A.UZ  ", ["https://brand-a.uz", "https://www.brand-a.uz"]),
        # Already a www host: adding another would be `www.www.`.
        ("www.brand-a.uz", ["https://www.brand-a.uz"]),
        # A port is part of the origin and has to survive.
        ("brand-a.uz:8443", ["https://brand-a.uz:8443", "https://www.brand-a.uz:8443"]),
    ],
)
def test_a_domain_becomes_its_origins(domain: str, expected: list[str]) -> None:
    assert _origins_for(domain) == expected


@pytest.mark.parametrize("domain", [None, "", "   ", "https://"])
def test_an_unset_domain_allows_nobody(domain: str | None) -> None:
    """A fresh installation has no domain yet. Closed is the safe answer."""
    assert _origins_for(domain) == []


def test_http_is_never_produced() -> None:
    """HTTPS is required everywhere (PROJECT.md §13).

    Honouring an `http://` domain here would be a way to opt back out of that
    from the panel.
    """
    assert _origins_for("http://brand-a.uz") == [
        "https://brand-a.uz",
        "https://www.brand-a.uz",
    ]
