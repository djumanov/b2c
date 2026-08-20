"""Payment providers — the port, the adapters, and the seam tests hang from.

Which provider charges is a settings question and settings belong to a
module, so the choice lives in ``payments.service.payment_provider`` (the
``notifications`` arrangement). What stays here is the two things a provider
package can own without importing ``modules``: the table of adapters this
release ships, and the override a test uses to pin a scripted provider.
"""

from collections.abc import Callable
from typing import Final

from app.providers.payments.base import PaymentProvider, PaymentProviderCode

_override: PaymentProvider | None = None


def set_provider(provider: PaymentProvider | None) -> None:
    """Pin the provider, or pass ``None`` to go back to the configured one."""
    global _override
    _override = provider


def get_override() -> PaymentProvider | None:
    return _override


#: code → factory, for the providers this release can actually talk to. Payme
#: and Click arrive in their own iterations; until then an enabled row for
#: either is "configured, but not available in this release".
ADAPTERS: Final[
    dict[PaymentProviderCode, Callable[[dict[str, str]], PaymentProvider]]
] = {}


__all__ = ["ADAPTERS", "get_override", "set_provider"]
