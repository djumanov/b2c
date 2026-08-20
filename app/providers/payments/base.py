"""The payment provider codes — Payme and Click in the first release (D7).

Only the code enum lives here now. The port itself, its result types and its
registry went with the order system they served, and the flow that replaces
them will bring its own.

The enum stays in ``providers/`` rather than inside a module because two
modules read it: ``integrations`` stores a credential row per code, and
``payments`` stamps a saved card with the code it belongs to. Moving it into
either one would make the other import a module's models, which is the one
thing modules may not do to each other (ARCHITECTURE.md §4).
"""

from enum import StrEnum


class PaymentProviderCode(StrEnum):
    PAYME = "payme"
    CLICK = "click"


__all__ = ["PaymentProviderCode"]
