"""``/public/payments/methods/`` — what the checkout may offer (API.md §22).

One route, and it deliberately repeats what ``site-config`` already publishes
(§17). The duplication is the contract's, and it is a reasonable one: the
config document is a page-load concern, and a checkout that has been open for
an hour should be able to ask again without re-reading the whole installation.

No auth. Which methods an installation accepts is not a secret — it is printed
on the checkout page.
"""

from typing import Any

from app.api.envelope import enveloped_router
from app.db.session import SessionDep
from app.modules.integrations import service as integrations_service

router = enveloped_router(prefix="/payments", tags=["payments"])


@router.get("/methods/", summary="Enabled payment methods")
async def methods(session: SessionDep) -> list[dict[str, Any]]:
    """Only the enabled ones, and no ``enabled`` flag.

    A disabled provider in this list would be a button that does nothing.
    """
    return await integrations_service.enabled_payment_methods(session)


__all__ = ["router"]
