"""When a profile counts as finished — API.md §19.

The rule is five fields wide and lives on the model so that the app and the
site cannot disagree about it. That makes it worth testing here rather than
only through the endpoint: every way of being incomplete gets named, which a
round trip through ``GET /public/profile/`` would not do.
"""

from datetime import date

import pytest

from app.modules.customers.models import Customer

FILLED: dict[str, object] = {
    "first_name": "Aziz",
    "last_name": "Karimov",
    "middle_name": "Baxtiyorovich",
    "phone": "+998901234567",
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
    partial answer has to read the same as no answer."""
    assert _customer(**{missing: None}).is_profile_complete is False


def test_a_freshly_registered_row_is_incomplete() -> None:
    """Registration asks for the address and the password only (API.md §18),
    so this is the normal state of a brand-new account, not a broken one."""
    account = Customer(email="buyer@example.uz", password_hash="x")

    assert account.is_profile_complete is False


@pytest.mark.parametrize("blank", ["", " ", "\t\n"])
def test_whitespace_is_not_an_answer(blank: str) -> None:
    """``PersonName`` only asks for one character and ``phone`` has no floor at
    all, so both a space and an empty string reach the column. Neither is
    somebody having filled the field in, and counting them would leave the
    customer looking at a profile the server calls finished and they do not.
    """
    assert _customer(middle_name=blank).is_profile_complete is False
    assert _customer(phone=blank).is_profile_complete is False
