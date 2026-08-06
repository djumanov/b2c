"""Which browser origins may call this installation — API.md §15.

The domain is a setting, so this is the same promise as the colour in
`test_site_config.py`: a client changes it in the panel and it is true on the
next request, not after somebody restarts the container (PROJECT.md §7).
"""

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.modules.staff.models import Staff
from tests.integration.conftest import headers_for

CONFIG = "/api/v1/public/site-config/"
SITE = "/api/v1/admin/settings/site/"


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client's server, where the origin list is the one that matters."""
    monkeypatch.setattr(settings, "debug", False)


async def _set_domain(api: AsyncClient, owner: Staff, domain: str) -> None:
    response = await api.patch(
        SITE, headers=headers_for(owner), json={"domain": domain}
    )
    assert response.status_code == 200, response.text
    # The write purged the cache; this fills it again through the test's own
    # transaction, which is what any real request would do a moment later.
    await api.get(CONFIG)


async def test_the_site_domain_is_allowed_without_a_restart(
    api: AsyncClient, owner: Staff, production: None
) -> None:
    """The process answering the second request is the one that answered the first."""
    await _set_domain(api, owner, "brand-a.uz")

    response = await api.get(CONFIG, headers={"Origin": "https://brand-a.uz"})

    assert response.headers["access-control-allow-origin"] == "https://brand-a.uz"
    assert response.headers["access-control-allow-credentials"] == "true"


async def test_changing_the_domain_changes_who_may_call(
    api: AsyncClient, owner: Staff, production: None
) -> None:
    await _set_domain(api, owner, "brand-a.uz")
    await _set_domain(api, owner, "brand-b.com")

    old = await api.get(CONFIG, headers={"Origin": "https://brand-a.uz"})
    new = await api.get(CONFIG, headers={"Origin": "https://brand-b.com"})

    assert "access-control-allow-origin" not in old.headers
    assert new.headers["access-control-allow-origin"] == "https://brand-b.com"


async def test_www_counts_as_the_same_site(
    api: AsyncClient, owner: Staff, production: None
) -> None:
    """A client types their domain, not a list of origins."""
    await _set_domain(api, owner, "brand-a.uz")

    response = await api.get(CONFIG, headers={"Origin": "https://www.brand-a.uz"})

    assert response.headers["access-control-allow-origin"] == "https://www.brand-a.uz"


async def test_a_stranger_gets_no_header(
    api: AsyncClient, owner: Staff, production: None
) -> None:
    await _set_domain(api, owner, "brand-a.uz")

    response = await api.get(CONFIG, headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in response.headers


async def test_a_preflight_is_answered_for_the_site_domain(
    api: AsyncClient, owner: Staff, production: None
) -> None:
    """The panel sends `Authorization`, so every call it makes is preflighted."""
    await _set_domain(api, owner, "brand-a.uz")

    response = await api.request(
        "OPTIONS",
        SITE,
        headers={
            "Origin": "https://brand-a.uz",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://brand-a.uz"


async def test_development_reflects_the_caller(api: AsyncClient) -> None:
    """A checkout runs on localhost against whatever port the panel picked.

    Reflected, never the literal `*`: a wildcard is invalid alongside
    `Access-Control-Allow-Credentials`, and browsers refuse the pair.
    """
    response = await api.get(CONFIG, headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_a_request_without_an_origin_is_untouched(api: AsyncClient) -> None:
    """Server-to-server calls and the app are not browsers; nothing to resolve."""
    response = await api.get(CONFIG)

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
