"""How the receipt leaves us — the part both surfaces answer with.

The customer downloads their own; support downloads anyone's. Everything
after "which order may be asked for" is identical, and identical is what it
has to stay: two routes that describe the same file differently in Swagger,
or send it with different headers, is a bug waiting for the day one of them
is fixed alone. So the response, the query and the documented answers are
written once, here, and both routers use them.

The document itself is GTS's. It renders the itinerary receipt but will not
serve it to a customer — its receipt page wants the agent session's cookies
and answers ``401`` without them — so ``orders.service`` fetches the bytes
with ours and these three pieces hand them on.
"""

from typing import Annotated, Any, Final

from fastapi import Query, Response

from app.api.errors import ErrorCode
from app.api.openapi import error_responses
from app.modules.orders.schemas import ReceiptDocument

#: Sent with every receipt. ``nosniff`` holds GTS's declared type to what it
#: declared, and the sandbox puts a document that *is* markup in an opaque
#: origin of its own, where it can neither read this installation's storage
#: nor call its API — the uploads route's arrangement, for the same reason.
_HEADERS: Final[dict[str, str]] = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}

#: A file, and the three ways asking for one can be refused.
RECEIPT_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "content": {"application/pdf": {}, "text/html": {}},
        "description": "The receipt file.",
    },
    **error_responses(
        ErrorCode.CONFLICT,
        ErrorCode.UPSTREAM_ERROR,
        ErrorCode.UPSTREAM_TIMEOUT,
        conflict=(
            "There is no receipt yet. Either GTS has not issued the ticket — "
            "poll `GET /public/orders/{id}/` until `ticketing.status` is "
            "`ticketed` — or it has issued it and not drawn the document "
            "yet, in which case try again shortly."
        ),
        upstream_error=(
            "GTS could not render the receipt; its words are in "
            "`meta.upstream`. Nothing is wrong with the ticket — try again."
        ),
        upstream_timeout="GTS did not answer in time. Try again.",
    ),
}

PassengerIndex = Annotated[
    int | None,
    Query(
        ge=0,
        description=(
            "One passenger's copy, counted from **zero** in the order's own "
            "passenger order (`order_data.passengers`). Omit for the whole "
            "order."
        ),
    ),
]


def receipt_response(receipt: ReceiptDocument) -> Response:
    """The file as it leaves us — the same body on either surface."""
    return Response(
        content=receipt.content,
        media_type=receipt.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{receipt.filename}"',
            **_HEADERS,
        },
    )


__all__ = ["RECEIPT_RESPONSES", "PassengerIndex", "receipt_response"]
