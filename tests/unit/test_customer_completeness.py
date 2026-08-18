"""When a profile counts as finished — API.md §19.

The rule is five fields wide and lives on the model so that the app and the
site cannot disagree about it. That makes it worth testing here rather than
only through the endpoint: every way of being incomplete gets named, which a
round trip through ``GET /public/profile/`` would not do.

Five fields, six columns: the phone is one field to the customer and
``phone_code`` + ``phone_number`` to the database, and both halves have to be
there — a number without its country code cannot be handed to GTS, so a
profile holding one is not finished.
"""

from datetime import date

import pytest

from app.modules.customers.models import Customer

#: Every column the rule counts. ``phone_mask`` is deliberately absent — it is
#: how a number is shown, not whether there is one.
FILLED: dict[str, object] = {
    "first_name": "Aziz",
    "last_name": "Karimov",
    "middle_name": "Baxtiyorovich",
    "phone_code": "998",
    "phone_number": "901234567",
    "birth_date": date(1995, 4, 17),
}


def _customer(**overrides: object) -> Customer:
    return Customer(
        email="buyer@example.uz", password_hash="x", **{**FILLED, **overrides}
    )


def test_all_five_filled_is_complete() -> None:
    assert _customer().is_profile_complete is True


@pytest.mark.parametrize("missing", sorted(FILLED))
def test_any_one_missing_is_incomplete(missing: str) -> None:
    """All five or nothing. The frontend shows one banner, not five, so a
    partial answer has to read the same as no answer.

    Parametrised over columns rather than fields, which is what puts each half
    of the phone under the rule on its own.
    """
    assert _customer(**{missing: None}).is_profile_complete is False


def test_the_mask_is_not_part_of_the_answer() -> None:
    """``phone_mask`` is presentation. A customer who saved a code and a number
    has given us a usable phone whether or not their client sent a mask, and
    counting it would leave them staring at a banner they cannot clear."""
    assert _customer(phone_mask=None).is_profile_complete is True


def test_a_freshly_registered_row_is_incomplete() -> None:
    """Registration asks for the address and the password only (API.md §18),
    so this is the normal state of a brand-new account, not a broken one."""
    account = Customer(email="buyer@example.uz", password_hash="x")

    assert account.is_profile_complete is False


@pytest.mark.parametrize("blank", ["", " ", "\t\n"])
def test_whitespace_is_not_an_answer(blank: str) -> None:
    """``PersonName`` only asks for one character and ``phone_number`` has no
    floor at all, so both a space and an empty string reach the column. Neither
    is somebody having filled the field in, and counting them would leave the
    customer looking at a profile the server calls finished and they do not.
    """
    assert _customer(middle_name=blank).is_profile_complete is False
    assert _customer(phone_number=blank).is_profile_complete is False
    assert _customer(phone_code=blank).is_profile_complete is False
