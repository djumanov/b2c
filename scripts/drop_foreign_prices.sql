-- Drop prices this installation can never charge — the one-off data decision
-- behind migration ``20260828_1030_single_currency``.
--
-- That migration refuses to run while an order is priced in another currency,
-- because restating what somebody was charged is not a migration's decision.
-- This is the decision, made once, by hand: **an order quoted in a currency we
-- cannot charge has no price.** It is exactly what ``orders.service`` now does
-- with such a booking on the way in — the order is recorded, the figure is not.
--
-- Safe by construction:
--
--   * It **refuses** if money moved. A foreign-currency attempt that is ``paid``
--     or still ``confirming``, or an order whose ``payment_status`` has left
--     ``pending``/``failed``, is a real amount somebody owes or was charged.
--     Those need a person and a rate, not this script.
--   * It records **why** on the order's own timeline (``price.dropped``), so the
--     history and the row still agree — the rule the orders module is built on.
--   * It removes the attempts that quoted the same unusable price. Step 1 has
--     already proved they are ``started``/``failed``/``abandoned``, so nothing
--     was ever charged through them; their count rides along in the event.
--   * One transaction. Either all of it happened or none of it did.
--
-- Run it with the API stopped, then start the API and let the entrypoint's
-- ``alembic upgrade head`` go through:
--
--     docker compose stop api
--     docker compose exec -T postgres \
--         psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
--         < scripts/drop_foreign_prices.sql
--     docker compose start api
--
-- Running it twice is a no-op: after the first pass nothing matches.

\set ON_ERROR_STOP on

BEGIN;

\echo '--- foreign-currency rows before ---'

SELECT o.id, o.status, o.payment_status, o.ticketing_status,
       o.amount, o.currency, o.gts_order_number, o.created_at
  FROM orders o
 WHERE o.currency IS NOT NULL AND o.currency <> 'UZS'
 ORDER BY o.created_at;

SELECT a.id, a.order_id, a.status, a.amount, a.currency, a.created_at
  FROM payment_attempts a
 WHERE a.currency <> 'UZS'
 ORDER BY a.created_at;

-- 1. Refuse if money moved. -----------------------------------------------

DO $$
DECLARE
    charged int;
BEGIN
    SELECT count(*) INTO charged
      FROM payment_attempts
     WHERE currency <> 'UZS' AND status IN ('paid', 'confirming');
    IF charged > 0 THEN
        RAISE EXCEPTION
            'refusing: % foreign-currency payment attempt(s) are paid or still '
            'in flight. Money may have moved — settle what each is worth before '
            'dropping any price.', charged;
    END IF;

    SELECT count(*) INTO charged
      FROM orders
     WHERE currency IS NOT NULL AND currency <> 'UZS'
       AND payment_status NOT IN ('pending', 'failed');
    IF charged > 0 THEN
        RAISE EXCEPTION
            'refusing: % foreign-currency order(s) have taken money '
            '(payment_status is past pending/failed). Those need a rate and a '
            'person, not this script.', charged;
    END IF;
END $$;

-- 2. Say why, on the order's own timeline. --------------------------------

INSERT INTO order_events (id, order_id, event, actor, note, data)
SELECT gen_random_uuid(),
       o.id,
       'price.dropped',
       'system',
       'single-currency migration: quoted in ' || o.currency
           || ', which this installation cannot charge',
       jsonb_build_object(
           'amount', o.amount::text,
           'currency', o.currency,
           'void_attempts', (SELECT count(*)
                               FROM payment_attempts a
                              WHERE a.order_id = o.id AND a.currency <> 'UZS')
       )
  FROM orders o
 WHERE o.currency IS NOT NULL AND o.currency <> 'UZS';

-- 3. The attempts that quoted the same unusable price are void. -----------

DELETE FROM payment_attempts WHERE currency <> 'UZS';

-- 4. Drop the price. The order stays. -------------------------------------

UPDATE orders
   SET amount = NULL, currency = NULL
 WHERE currency IS NOT NULL AND currency <> 'UZS';

\echo '--- foreign-currency rows after (both should be empty) ---'

SELECT id, currency FROM orders
 WHERE currency IS NOT NULL AND currency <> 'UZS';
SELECT id, currency FROM payment_attempts WHERE currency <> 'UZS';

\echo '--- what was recorded ---'

SELECT order_id, event, note, data
  FROM order_events
 WHERE event = 'price.dropped'
 ORDER BY created_at;

COMMIT;
