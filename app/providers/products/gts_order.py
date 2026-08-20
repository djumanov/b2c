"""Facts read out of a GTS order body — the few that are needed twice.

The body is GTS's own, and GTS spells it differently between installations.
The flight adapter reads a **fresh** one the moment a booking is confirmed;
the orders list reads a **stored** one every time a customer opens their
orders. Both need to find the same two things, so the knowledge of where GTS
hides them lives here, once. Written twice it would drift, and the drift is
invisible: a list that silently comes back empty.

The one that bites today is the journey. The gateway collection puts it beside
the order as ``routes[]``; live GTS nests it under ``offer.routes[]`` — the
same class of mismatch that made two real bookings unreadable on 2026-08-19.
So both places are looked in, in the order the docs settled on: **live answer
first, collection second**.

Every reader here answers an empty list rather than raising. An order booked
while the read-back of ``GET /v1/orders/{n}/`` failed carries no routes at
all, and that is a thin card, not a 500.
"""

from typing import Any, Final

# --- GTS's status codes, the two vocabularies it uses ---------------------------
# ``BO``/``PW``/``TI``/``TE``/``CB``/``VO`` is the documented set; live
# ``/v1/orders/`` has also answered ``STATUS_BOOK`` and ``STATUS_VOID``. Both
# are read. A code in none of the sets is simply unknown — never assumed.

#: The hold is alive and unticketed (``PW`` = GTS is issuing, still alive).
HELD_CODES: Final[frozenset[str]] = frozenset({"BO", "STATUS_BOOK", "PW"})
#: GTS is working on the ticket.
WAITING_CODES: Final[frozenset[str]] = frozenset({"PW"})
#: The ticket is issued.
TICKETED_CODES: Final[frozenset[str]] = frozenset({"TI"})
#: The hold or the ticket is gone — nothing more will come of this order.
#: ``TE`` (ticketing error) is deliberately not here: it is a verdict on the
#: ticket, not on the hold, and the ticketing step reads it itself.
RELEASED_CODES: Final[frozenset[str]] = frozenset({"CB", "VO", "STATUS_VOID"})


def is_held(status: str | None) -> bool:
    return status in HELD_CODES


def is_released(status: str | None) -> bool:
    return status in RELEASED_CODES


def _text(value: Any) -> str | None:
    """A non-empty string, or nothing. GTS sends ``""`` where it means null."""
    return value if isinstance(value, str) and value else None


def routes(body: dict[str, Any]) -> list[dict[str, Any]]:
    """GTS's route objects — beside the order first, under ``offer`` second.

    Returned verbatim: the segments underneath carry the flight number, the
    airline, the airports and the times a list card is drawn from, and which
    key holds which is GTS's business, not ours (API.md §20).
    """
    for source in (body, body.get("offer")):
        if not isinstance(source, dict):
            continue
        found = source.get("routes")
        if isinstance(found, list):
            return [route for route in found if isinstance(route, dict)]
    return []


def passenger_names(body: dict[str, Any]) -> list[dict[str, str | None]]:
    """Who is flying — name only, never a document.

    Built key by key rather than by copying GTS's passenger and trimming it.
    A blocklist fails open the day GTS adds a field, and the field it adds is
    a passport number riding on every list request (PROJECT.md §13). Adding a
    name to a card is a deliberate act; leaking a document must not be an
    accidental one.

    Both spellings are read: the collection sends ``first_name``, live GTS
    sends ``firstname``.
    """
    people = body.get("passengers")
    if not isinstance(people, list):
        return []
    return [
        {
            "first_name": _text(person.get("first_name") or person.get("firstname")),
            "last_name": _text(person.get("last_name") or person.get("lastname")),
            "middle_name": _text(person.get("middle_name") or person.get("middlename")),
        }
        for person in people
        if isinstance(person, dict)
    ]


def tickets(body: dict[str, Any]) -> list[dict[str, str]]:
    """Issued tickets — ``passengers[].ticket_number`` where GTS filled it in.

    Named by passenger (the card's "Azimjon Yusufov — 7653081297644"), using
    the same two spellings ``passenger_names`` reads. A passenger without a
    number is simply absent: before ticketing the list is empty, after a
    partial issue it is short, and both are facts rather than errors.
    """
    found: list[dict[str, str]] = []
    people = body.get("passengers")
    if not isinstance(people, list):
        return found
    for person in people:
        if not isinstance(person, dict):
            continue
        number = person.get("ticket_number")
        if isinstance(number, int) and not isinstance(number, bool):
            number = str(number)
        if not isinstance(number, str) or not number:
            continue
        names = (
            _text(person.get("first_name") or person.get("firstname")),
            _text(person.get("last_name") or person.get("lastname")),
        )
        found.append(
            {
                "passenger": " ".join(part for part in names if part),
                "ticket_number": number,
            }
        )
    return found


__all__ = [
    "HELD_CODES",
    "RELEASED_CODES",
    "TICKETED_CODES",
    "WAITING_CODES",
    "is_held",
    "is_released",
    "passenger_names",
    "routes",
    "tickets",
]
