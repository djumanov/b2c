"""The parts of customer auth that are decisions, not database work.

The expiry, the attempt ceiling and the resend cooldown all need rows, so they
are tested end to end in ``tests/integration/test_customer_auth.py``. What is
left here is small and easy to get wrong in a way no integration test would
name clearly.
"""

from datetime import UTC, datetime, timedelta

from app.modules.customers.models import Customer
from app.modules.customers.schemas import OTP_LENGTH
from app.modules.customers.service import _generate_code, _hash_code

VERIFIED = datetime(2026, 8, 6, 9, 0, tzinfo=UTC)


def _customer(
    *,
    verified: datetime | None = VERIFIED,
    blocked: bool = False,
    deleted: datetime | None = None,
) -> Customer:
    return Customer(
        email="buyer@example.uz",
        password_hash="x",
        first_name="Buyer",
        email_verified_at=verified,
        is_blocked=blocked,
        deleted_at=deleted,
    )


def test_a_code_is_always_six_characters() -> None:
    """Zero padding is not cosmetic.

    ``str(randbelow(1_000_000))`` gives ``"42"`` about one time in ten
    thousand, and the schema requires exactly six characters — so an unpadded
    code would be a registration that cannot be confirmed, for a fraction of
    users, with nothing in the logs to say why.
    """
    codes = [_generate_code() for _ in range(2000)]

    assert all(len(code) == OTP_LENGTH for code in codes)
    assert all(code.isdigit() for code in codes)


def test_codes_are_not_all_the_same() -> None:
    assert len({_generate_code() for _ in range(200)}) > 1


def test_a_code_is_hashed_before_it_is_stored() -> None:
    """The column is 64 characters wide because the digest is."""
    digest = _hash_code("482913")

    assert digest != "482913"
    assert len(digest) == 64
    assert _hash_code("482913") == digest


def test_an_account_is_active_only_when_confirmed() -> None:
    assert _customer().is_active is True
    # The one that is specific to this surface: the row exists from the moment
    # somebody types the address, and grants nothing until they prove it.
    assert _customer(verified=None).is_active is False


def test_blocked_and_deleted_accounts_are_not_active() -> None:
    assert _customer(blocked=True).is_active is False
    assert _customer(deleted=VERIFIED + timedelta(days=1)).is_active is False
