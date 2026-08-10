"""The payment provider override — the seam tests hang from.

Same shape and same reasoning as ``providers/notifications/__init__.py`` and
``providers/social/__init__.py``: **which provider to use is a settings
question, and settings belong to a module.** The credentials live in the
database and are edited from the panel, so the answer is
``integrations.service.payment_provider_adapter(session, code)``. Answering it
here would mean a provider importing ``modules``, the one direction
ARCHITECTURE.md §4 does not allow.

What stays here is the override — the only way a test can register and charge a
card without a real merchant account.

**The override is scoped per provider code**, unlike the notifier's single slot.
The reason is the one ``social`` gives: pinning Payme must not make an
unconfigured Click quietly start working, or the tests would agree with
themselves about a route no real installation can reach.
"""

from app.providers.payments.base import PaymentProvider, PaymentProviderCode

_overrides: dict[PaymentProviderCode, PaymentProvider] = {}


def set_provider(code: PaymentProviderCode, provider: PaymentProvider | None) -> None:
    """Pin one provider, or pass ``None`` to go back to the configured one."""
    if provider is None:
        _overrides.pop(code, None)
    else:
        _overrides[code] = provider


def get_override(code: PaymentProviderCode) -> PaymentProvider | None:
    """The pinned provider, if there is one. ``None`` means "use the settings"."""
    return _overrides.get(code)


def clear_overrides() -> None:
    """Drop every pin — the fixture teardown, so one test cannot leak into the next."""
    _overrides.clear()


__all__ = ["clear_overrides", "get_override", "set_provider"]
