"""Ending every session at once — the half a `jti` denylist cannot do.

A refresh token is a stored row, so it can be revoked by name. An access token
is not stored anywhere, so the only handle left on it is what it says about
itself: when it was issued. `revoked_before` is that handle.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.core.security import (
    TOKEN_TTL,
    Audience,
    TokenClaims,
    TokenType,
    create_token,
    is_revoked_before,
    revoked_before_key,
    revoked_before_ttl,
    revoked_before_value,
)

NOW = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)


def _claims_issued_at(moment: datetime) -> TokenClaims:
    _, claims = create_token(
        subject_id=uuid.uuid4(),
        audience=Audience.ADMIN,
        token_type=TokenType.ACCESS,
        role="owner",
        now=moment,
    )
    return claims


def test_a_token_from_before_the_mark_is_dead() -> None:
    claims = _claims_issued_at(NOW - timedelta(minutes=5))

    assert is_revoked_before(claims, revoked_before_value(NOW)) is True


def test_a_token_from_after_the_mark_is_alive() -> None:
    """The mark ends what came before it; the login that follows is not that."""
    claims = _claims_issued_at(NOW + timedelta(seconds=1))

    assert is_revoked_before(claims, revoked_before_value(NOW)) is False


def test_the_same_second_survives() -> None:
    """`iat` is only accurate to the second.

    Killing that second too would log somebody out of the login they make
    immediately after changing their own password. The cost is a window no
    wider than one second.
    """
    claims = _claims_issued_at(NOW)

    assert is_revoked_before(claims, revoked_before_value(NOW)) is False


def test_no_mark_revokes_nothing() -> None:
    claims = _claims_issued_at(NOW)

    assert is_revoked_before(claims, None) is False


def test_an_unreadable_mark_counts_as_revoked() -> None:
    """Nothing but this process writes it. If it reads back wrong, say no."""
    claims = _claims_issued_at(NOW)

    assert is_revoked_before(claims, "not-a-timestamp") is True


def test_the_mark_outlives_the_tokens_it_invalidates() -> None:
    """Forgetting it early would bring every one of them back."""
    access_ttl = TOKEN_TTL[(Audience.ADMIN, TokenType.ACCESS)].total_seconds()

    assert revoked_before_ttl(Audience.ADMIN) > access_ttl


def test_the_key_is_per_subject() -> None:
    """One employee's revocation must not end another's day."""
    assert revoked_before_key(uuid.uuid4()) != revoked_before_key(uuid.uuid4())
