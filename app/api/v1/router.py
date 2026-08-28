"""Assembles the three surfaces. Thin by design — routers live in modules.

* ``/api/v1/public/*``  — website and Flutter app, subject ``aud: public``
* ``/api/v1/admin/*``   — React panel, subject ``aud: admin``
* ``/api/v1/webhooks/*``— provider callbacks, no auth, signature checked

The first two use ``EnvelopeRoute``; webhooks deliberately do not, because
their response shape is the provider's protocol (API.md §40).

Rate limits are attached per surface here rather than per endpoint, so a new
router cannot forget them (API.md §14). Search raises its own tighter limit
where it is mounted, and each auth module mounts a second router at the same
prefix for the endpoints that hold a session rather than prove one — refresh
and ``auth/me/`` — so the sign-in limit does not fall on them.

Note that ``route_class`` does **not** propagate to included routers — each
module router declares its own via ``enveloped_router``. See that function.
"""

from fastapi import APIRouter, Depends

from app.api.deps import RateLimit
from app.api.envelope import enveloped_router
from app.modules.audit import router_admin as audit_admin
from app.modules.catalog import router_public as catalog_public
from app.modules.cms import router_admin as cms_admin
from app.modules.cms import router_public as cms_public
from app.modules.customers import router_admin as customers_admin
from app.modules.customers import router_profile as customers_profile
from app.modules.customers import router_public as customers_public
from app.modules.integrations import router_admin as integrations_admin
from app.modules.leads import router_admin as leads_admin
from app.modules.leads import router_public as leads_public
from app.modules.orders import router_admin as orders_admin
from app.modules.orders import router_public as orders_public
from app.modules.payments import router_cards as payments_cards
from app.modules.products import router_public as products_public
from app.modules.settings import router_admin as settings_admin
from app.modules.settings import router_public as settings_public
from app.modules.staff import router_admin as staff_admin
from app.modules.system import router_admin as system_admin
from app.modules.uploads import router_admin as uploads_admin

public_router = enveloped_router(
    prefix="/public",
    dependencies=[Depends(RateLimit("public"))],
)

admin_router = enveloped_router(
    prefix="/admin",
    dependencies=[Depends(RateLimit("admin"))],
)

webhooks_router = APIRouter(prefix="/webhooks")

# Empty while the order system is rebuilt. The surface stays declared: a
# provider callback answers in the provider's own shape, not our envelope
# (API.md §40), and that exception is easier to keep than to rediscover.

# --- module routers -----------------------------------------------------------
# One line per module, in the order of API.md's sections. Modules that have not
# been built yet simply are not listed.

public_router.include_router(settings_public.router)
public_router.include_router(customers_public.router)
public_router.include_router(customers_public.session_router)
public_router.include_router(customers_profile.router)
public_router.include_router(customers_profile.passengers_router)
# A profile path served by ``payments`` — the row is an encrypted autofill
# record, not a provider token (ARCHITECTURE.md §5).
public_router.include_router(payments_cards.router)
public_router.include_router(cms_public.faq_router)
public_router.include_router(cms_public.pages_router)
public_router.include_router(leads_public.topics_router)
public_router.include_router(leads_public.support_router)
public_router.include_router(leads_public.router)
public_router.include_router(catalog_public.router)
public_router.include_router(orders_public.router)
# Last on purpose: ``/{product}`` is a catch-all prefix, and mounting it after
# every literal sibling removes even the theoretical chance of shadowing one.
public_router.include_router(products_public.router)

admin_router.include_router(staff_admin.auth_router)
admin_router.include_router(staff_admin.session_router)
admin_router.include_router(settings_admin.router)
admin_router.include_router(integrations_admin.router)
admin_router.include_router(integrations_admin.payments_router)
admin_router.include_router(integrations_admin.social_router)
admin_router.include_router(integrations_admin.notifications_router)
admin_router.include_router(cms_admin.faq_router)
admin_router.include_router(cms_admin.fun_facts_router)
admin_router.include_router(cms_admin.pages_router)
admin_router.include_router(customers_admin.deletion_reasons_router)
admin_router.include_router(leads_admin.topics_router)
admin_router.include_router(leads_admin.support_router)
admin_router.include_router(leads_admin.router)
# ``messages_router`` first: ``/orders/messages/`` must not be read as an id.
admin_router.include_router(orders_admin.messages_router)
admin_router.include_router(orders_admin.router)
admin_router.include_router(staff_admin.router)
admin_router.include_router(system_admin.router)
admin_router.include_router(audit_admin.router)
admin_router.include_router(uploads_admin.router)

# --- assembly ------------------------------------------------------------------

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(public_router)
api_router.include_router(admin_router)
api_router.include_router(webhooks_router)


__all__ = [
    "admin_router",
    "api_router",
    "public_router",
    "webhooks_router",
]
