"""``/webhooks/payments/{provider}/`` — the providers calling us back.

Three rules, and they are the whole module (API.md §40):

* **The envelope does not apply.** What goes back is whatever the provider's
  protocol expects, built by its adapter. A JSON-RPC caller handed our
  ``{status, data, errors, meta}`` would treat every answer as malformed.
* **A bad signature changes nothing.** The check runs before any state is read
  or written, and its answer is the adapter's — ``401`` for most, ``200`` with
  a JSON-RPC error for Payme, which reads ``401`` as a reason to retry blindly.
* **A repeat is not an error.** Providers resend, and a callback that arrives
  twice must settle once and answer success both times; "this is already paid"
  is a fact, not a failure.

No authentication and no rate limit: a provider has no token, and a limit here
would drop the one message that says money moved.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request, Response

from app.api.envelope import json_response
from app.api.errors import NotFound
from app.core.logging import get_logger
from app.db.session import SessionDep
from app.modules.orders import service as orders_service
from app.modules.payments import service
from app.providers.payments.base import PaymentProviderCode

logger = get_logger(__name__)

router = APIRouter(prefix="/payments", tags=["webhooks"])

ProviderParam = Annotated[
    str,
    Path(description="Payment provider code — `payme` or `click`."),
]


@router.post("/{provider}/", summary="Payment provider callback")
async def callback(
    provider: ProviderParam, request: Request, session: SessionDep
) -> Response:
    """Take one callback and, if it says money moved, make it so.

    The settlement runs **after** the adapter has read the message, so a
    provider whose protocol needs several exchanges before the money is real
    can say so with ``settled=False`` and be answered without anything moving.

    ``order_id`` and ``provider_ref`` both have to be there before anything is
    settled: without the first there is no order to credit, and without the
    second the same message could be applied twice.
    """
    try:
        code = PaymentProviderCode(provider)
    except ValueError as unknown:
        raise NotFound("No such payment provider") from unknown

    body = await request.body()
    result = await service.callback(
        session, code, headers=dict(request.headers), body=body
    )

    if result.settled and result.provider_ref and result.order_id:
        await orders_service.settle_attempt(
            session,
            provider=code.value,
            provider_ref=result.provider_ref,
            order_id=_as_uuid(result.order_id),
            provider_state=_state(result.body),
        )

    return json_response(result.body, status_code=result.status_code)


def _as_uuid(value: str) -> Any:
    """The order id the provider quoted back, as ours.

    A provider that quotes something that is not one of our ids is not talking
    about an order of ours, and a 404 says so without touching anything.
    """
    import uuid

    try:
        return uuid.UUID(value)
    except ValueError as bad:
        logger.warning("payment_callback_unknown_order", reference=value)
        raise NotFound("No such order") from bad


def _state(body: dict[str, Any]) -> dict[str, Any]:
    """What to keep on the attempt for diagnosis — the answer we sent back."""
    return {"callback": body}


__all__ = ["router"]
