"""Assembles the three surfaces. Thin by design — routers live in modules.

* ``/api/v1/public/*``  — website and Flutter app, subject ``aud: public``
* ``/api/v1/admin/*``   — React panel, subject ``aud: admin``
* ``/api/v1/webhooks/*``— payment provider callbacks, no auth, signature checked

The first two use ``EnvelopeRoute``; webhooks deliberately do not, because
their response shape is the provider's protocol (API.md §40).

Rate limits are attached per surface here rather than per endpoint, so a new
router cannot forget them (API.md §14). Search raises its own tighter limit
where it is mounted.

Note that ``route_class`` does **not** propagate to included routers — each
module router declares its own via ``enveloped_router``. See that function.
"""

from fastapi import APIRouter, Depends

from app.api.deps import RateLimit
from app.api.envelope import enveloped_router
from app.modules.staff import router_admin as staff_admin
from app.modules.system import router_admin as system_admin

public_router = enveloped_router(
    prefix="/public",
    dependencies=[Depends(RateLimit("public"))],
)

admin_router = enveloped_router(
    prefix="/admin",
    dependencies=[Depends(RateLimit("admin"))],
)

webhooks_router = APIRouter(prefix="/webhooks")

# --- module routers -----------------------------------------------------------
# One line per module, in the order of API.md's sections. Modules that have not
# been built yet simply are not listed.

admin_router.include_router(staff_admin.auth_router)
admin_router.include_router(staff_admin.router)
admin_router.include_router(system_admin.router)

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
