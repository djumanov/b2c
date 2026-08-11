"""``POST /public/leads/`` (API.md §25) — auth optional, ``auth`` rate limit.

Anonymous is welcome; a token, when sent, must be a good one and links the
lead to the customer. The tight per-IP limit is the spam defence — this is
the one public endpoint that writes a row for anybody who asks.
"""

from fastapi import Depends

from app.api.deps import LanguageDep, OptionalCustomer, RateLimit, RequireFeature
from app.api.envelope import enveloped_router
from app.db.session import SessionDep
from app.modules.leads import service
from app.modules.leads.schemas import (
    LeadCreatedOut,
    LeadCreateIn,
    SupportContactPublicOut,
    SupportTopicPublicOut,
)

router = enveloped_router(
    prefix="/leads",
    tags=["leads"],
    dependencies=[Depends(RequireFeature("leads"))],
)


@router.post(
    "/",
    status_code=201,
    dependencies=[Depends(RateLimit("auth"))],
    summary="Leave a message — the operator answers out of band",
)
async def create_lead(
    data: LeadCreateIn, customer: OptionalCustomer, session: SessionDep
) -> LeadCreatedOut:
    return await service.create_lead(
        session, data, customer_id=customer.id if customer else None
    )


# --- support topics (API.md §25) --------------------------------------------------

topics_router = enveloped_router(
    prefix="/leads/topics",
    tags=["leads"],
    dependencies=[Depends(RequireFeature("leads"))],
)


@topics_router.get("/", summary="Topic choices for the lead form")
async def list_topics(
    session: SessionDep, language: LanguageDep
) -> list[SupportTopicPublicOut]:
    return await service.list_topics_public(session, requested=language.requested)


# --- support contact (API.md §25) ---------------------------------------------------

support_router = enveloped_router(
    prefix="/leads/support",
    tags=["leads"],
    dependencies=[Depends(RequireFeature("leads"))],
)


@support_router.get("/", summary="How to reach support directly")
async def get_support_contact(
    session: SessionDep, language: LanguageDep
) -> SupportContactPublicOut:
    return await service.get_support_contact_public(
        session, requested=language.requested
    )


__all__ = ["router", "support_router", "topics_router"]
