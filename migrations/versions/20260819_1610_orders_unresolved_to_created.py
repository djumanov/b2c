"""orders unresolved to created

Revision ID: 4a1d7e0c93b2
Revises: 7c4e2b915da8
Create Date: 2026-08-19 16:10:04.118273

The rows this fixes were made by a bug, and they are in a state the code can
no longer produce.

A booking answer the adapter could not read used to send the order to
``needs_attention`` — a state defined as *money moved and the automatic
compensation failed*, which is nothing like what happened. No money had moved,
the seat was almost certainly held, and ``needs_attention`` is a state only a
person leaves (T18) at a time when there is no admin surface for orders. With
``provider_order_number`` never written, the customer's cancel answered
``409``; with the status not ``booked``, ``expire_unpaid`` never looked. So
each of those rows was a live reservation at GTS that nothing in this system
could name, release, or close.

The code now leaves such an order in ``created`` (T2x) and ``orders.sync_open``
settles it with GTS. This revision brings the rows written before that back
onto the same road.

**Data only, and deliberately blunt.** It writes a status directly rather than
going through ``transition``, because the machine has no move out of
``needs_attention`` that is not a person's — which is precisely the trap being
undone. The move is recorded as an ``order_events`` row so the history does not
have a silent step in it.

Nothing is re-read here. The provider's answer stays exactly as it was stored
and ``sync_open`` asks GTS what the order really is; a migration that learned
GTS's shape would freeze today's spelling into the schema history, which is the
mistake that produced these rows in the first place (ARCHITECTURE.md §4).

Reversible in the only sense that matters: ``downgrade`` puts back the status
and the reason for exactly the rows this touched, found by the event it wrote.
"""

import sqlalchemy as sa
from alembic import op

revision = "4a1d7e0c93b2"
down_revision = "7c4e2b915da8"
branch_labels = None
depends_on = None

#: The one reason that was ever written on this path.
_REASON = "unreadable_booking_answer"

#: What the event says, so ``downgrade`` can find the same rows again.
_NOTE = "Recorded as needs_attention before an unreadable answer stayed created"


def upgrade() -> None:
    moved = (
        op.get_bind()
        .execute(
            sa.text(
                """
            UPDATE orders
               SET status = 'created', attention_reason = NULL, attempts = 0
             WHERE status = 'needs_attention'
               AND attention_reason = :reason
            RETURNING id
            """
            ),
            {"reason": _REASON},
        )
        .fetchall()
    )
    if not moved:
        return
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO order_events (
                id, order_id, from_status, to_status, action,
                actor_type, actor_label, reason, attempt
            )
            SELECT gen_random_uuid(), id, 'needs_attention', 'created',
                   'booking.unresolved', 'system', 'migration.4a1d7e0c93b2',
                   :note, 0
              FROM orders
             WHERE id IN :ids
            """
        ).bindparams(sa.bindparam("ids", expanding=True)),
        {"note": _NOTE, "ids": [row[0] for row in moved]},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE orders
               SET status = 'needs_attention', attention_reason = :reason
             WHERE status = 'created'
               AND id IN (
                     SELECT order_id FROM order_events
                      WHERE actor_label = 'migration.4a1d7e0c93b2'
                   )
            """
        ),
        {"reason": _REASON},
    )
    op.get_bind().execute(
        sa.text("DELETE FROM order_events WHERE actor_label = 'migration.4a1d7e0c93b2'")
    )
