"""Payment providers — the port, the adapters, and the seam tests hang from.

Which provider charges is a settings question and settings belong to a
module, so the choice lives in ``payments.service.payment_provider`` (the
``notifications`` arrangement). What stays here is what a provider package
can own without importing ``modules``: the table of adapters this release
ships, what each asks the panel for, and the override a test uses to pin a
scripted provider.
"""

from collections.abc import Callable
from typing import Final

from app.providers.payments import payme
from app.providers.payments.base import (
    PaymentProvider,
    PaymentProviderCode,
    ProviderField,
)

_override: PaymentProvider | None = None


def set_provider(provider: PaymentProvider | None) -> None:
    """Pin the provider, or pass ``None`` to go back to the configured one."""
    global _override
    _override = provider


def get_override() -> PaymentProvider | None:
    return _override


#: code → factory, for the providers this release can actually talk to. Click
#: arrives in its own iteration; until then an enabled row for it is
#: "configured, but not available in this release".
ADAPTERS: Final[
    dict[PaymentProviderCode, Callable[[dict[str, str]], PaymentProvider]]
] = {
    PaymentProviderCode.PAYME: payme.PaymeProvider.from_credentials,
}

#: code → the settings its adapter reads from the panel. A code without an
#: adapter declares nothing, and the panel takes any key for it.
FIELDS: Final[dict[PaymentProviderCode, tuple[ProviderField, ...]]] = {
    PaymentProviderCode.PAYME: payme.FIELDS,
}


def fields(code: PaymentProviderCode) -> tuple[ProviderField, ...]:
    return FIELDS.get(code, ())


def secret_keys(code: PaymentProviderCode) -> frozenset[str] | None:
    """Which keys to mask on the way out — ``None`` when the code declares no
    fields, which the panel reads as "mask everything"."""
    declared = FIELDS.get(code)
    if declared is None:
        return None
    return frozenset(field.key for field in declared if field.secret)


__all__ = [
    "ADAPTERS",
    "FIELDS",
    "fields",
    "get_override",
    "secret_keys",
    "set_provider",
]
