"""The Payme adapter on its own: the wire, the error table, the state table.

Every test here talks to a fake Payme mounted by ``mock_payme`` and drives
``PaymeProvider`` directly — no database, no API. What the orders module
does with each answer is pinned in ``test_payment.py``.
"""

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from app.api.errors import UpstreamError, UpstreamTimeout
from app.core.logging import request_id_var
from app.core.money import Money
from app.providers.payments.base import CardDetails, PaymentDeclined
from app.providers.payments.payme import (
    FIELDS,
    PAYME_PRODUCTION_URL,
    PAYME_TEST_URL,
    PaymeProvider,
    PaymeSettings,
)
from tests.conftest import (
    PAYME_CREDENTIALS,
    PAYME_RECEIPT,
    PAYME_TOKEN,
    PAYME_URL,
    RpcError,
    mock_payme,
    payme_receipt,
)

PAN = "8600069195406311"
CARD = CardDetails(number=PAN, expire="0399")
AMOUNT = Money(amount=Decimal("287500.00"), currency="UZS")
ORDER_REF = "7d1f8a4e-0b3c-4f0a-9c1e-2a6b8d9e0f11"


def _provider(**overrides: str) -> PaymeProvider:
    return PaymeProvider.from_credentials({**PAYME_CREDENTIALS, **overrides})


def _reference() -> str:
    return json.dumps({"v": 1, "token": PAYME_TOKEN, "receipt": PAYME_RECEIPT})


# --- start ---------------------------------------------------------------------------


@respx.mock
async def test_start_creates_receipt_then_card_then_code() -> None:
    payme = mock_payme()
    request_id_var.set("req-payme-1")

    started = await _provider().start(card=CARD, amount=AMOUNT, order_ref=ORDER_REF)

    assert payme.methods == ["receipts.create", "cards.create", "cards.get_verify_code"]
    # The cashbox id alone for ``cards.*``, id:key for ``receipts.*``.
    assert payme.auth("receipts.create") == "m-1234abcd:k-secret-9f"
    assert payme.auth("cards.create") == "m-1234abcd"
    assert payme.auth("cards.get_verify_code") == "m-1234abcd"
    # Tiyin, against the cashbox's account field, with a description.
    receipt = payme.params("receipts.create")
    assert receipt["amount"] == 28750000
    assert receipt["account"] == {"order_id": ORDER_REF}
    assert receipt["description"] == f"Order {ORDER_REF}"
    assert "detail" not in receipt
    # A one-shot token: the saved-cards module keeps a PAN, not a Payme token.
    assert payme.params("cards.create") == {
        "card": {"number": PAN, "expire": "0399"},
        "save": False,
    }
    assert payme.params("cards.get_verify_code") == {"token": PAYME_TOKEN}
    # The reference carries what ``confirm`` and ``status`` will need.
    assert json.loads(started.reference) == {
        "v": 1,
        "token": PAYME_TOKEN,
        "receipt": PAYME_RECEIPT,
    }
    assert started.phone_hint == "99890*****31"
    assert started.raw == {
        "receipt": PAYME_RECEIPT,
        "card": "860006******6311",
        "wait": 60000,
    }
    # Nothing that can charge, and no card number, in what gets kept or shown.
    assert PAYME_TOKEN not in json.dumps(started.raw)
    assert PAN not in json.dumps(started.raw)
    assert PAYME_TOKEN not in repr(started)
    assert PAN not in repr(started)
    # Our request id travels with the call; the body is JSON-RPC.
    request = respx.calls.last.request
    assert request.headers["X-Request-Id"] == "req-payme-1"
    body = json.loads(request.content)
    assert set(body) == {"id", "method", "params"}
    assert isinstance(body["id"], int)


@respx.mock
async def test_start_sends_fiscal_detail_only_when_configured() -> None:
    payme = mock_payme()
    provider = _provider(
        fiscal_title="Aviachipta",
        fiscal_code="10202001001000000",
        fiscal_vat_percent="12",
        fiscal_package_code="1500123",
        fiscal_units="1",
    )

    await provider.start(card=CARD, amount=AMOUNT, order_ref=ORDER_REF)

    assert payme.params("receipts.create")["detail"] == {
        "receipt_type": 0,
        "items": [
            {
                "title": "Aviachipta",
                "price": 28750000,
                "count": 1,
                "code": "10202001001000000",
                "vat_percent": 12,
                "package_code": "1500123",
                "units": 1,
            }
        ],
    }


@respx.mock
async def test_start_refuses_wrong_currency_and_zero_before_any_call() -> None:
    mock_payme()
    route = respx.routes[0]

    with pytest.raises(UpstreamError, match="charges in UZS"):
        await _provider().start(
            card=CARD,
            amount=Money(amount=Decimal("10.00"), currency="USD"),
            order_ref=ORDER_REF,
        )
    with pytest.raises(UpstreamError, match="no price"):
        await _provider().start(
            card=CARD,
            amount=Money(amount=Decimal("0.00"), currency="UZS"),
            order_ref=ORDER_REF,
        )
    assert route.call_count == 0


@respx.mock
async def test_start_receipt_refusal_is_ours_not_the_cards() -> None:
    """A refused receipt is the amount or the cashbox — never "try another
    card", so it is an upstream error with Payme's words, not a decline."""
    payme = mock_payme(
        {"receipts.create": RpcError(-31001, "Недопустимая сумма платежа")}
    )

    with pytest.raises(UpstreamError) as failure:
        await _provider().start(card=CARD, amount=AMOUNT, order_ref=ORDER_REF)

    assert failure.value.meta == {
        "upstream": {"code": -31001, "message": "Недопустимая сумма платежа"}
    }
    assert payme.methods == ["receipts.create"]


@respx.mock
async def test_start_card_refusal_is_declined_in_the_customers_language() -> None:
    payme = mock_payme(
        {
            "cards.create": RpcError(
                -31300,
                {"ru": "Неверный номер карты", "uz": "Karta raqami noto'g'ri"},
            )
        }
    )

    with pytest.raises(PaymentDeclined, match="Karta raqami noto'g'ri"):
        await _provider().start(card=CARD, amount=AMOUNT, order_ref=ORDER_REF)

    assert payme.methods == ["receipts.create", "cards.create"]


@respx.mock
async def test_start_code_not_sent_is_declined() -> None:
    refused = mock_payme({"cards.get_verify_code": RpcError(-31304, "SMS недоступен")})
    with pytest.raises(PaymentDeclined, match="SMS недоступен"):
        await _provider().start(card=CARD, amount=AMOUNT, order_ref=ORDER_REF)
    assert refused.count("cards.get_verify_code") == 1

    respx.reset()
    mock_payme({"cards.get_verify_code": {"sent": False, "phone": None, "wait": 0}})
    with pytest.raises(PaymentDeclined, match="could not send the code"):
        await _provider().start(card=CARD, amount=AMOUNT, order_ref=ORDER_REF)


@respx.mock
async def test_start_system_error_is_upstream_not_declined() -> None:
    mock_payme({"cards.create": RpcError(-32504, "Insufficient privileges")})

    with pytest.raises(UpstreamError, match="Insufficient privileges"):
        await _provider().start(card=CARD, amount=AMOUNT, order_ref=ORDER_REF)


@respx.mock
async def test_start_unexpected_shapes_are_upstream_errors() -> None:
    mock_payme({"cards.create": {"card": {"number": "8600******"}}})
    with pytest.raises(UpstreamError, match="unexpected shape"):
        await _provider().start(card=CARD, amount=AMOUNT, order_ref=ORDER_REF)

    respx.reset()
    mock_payme({"receipts.create": {"receipt": "not-an-object"}})
    with pytest.raises(UpstreamError, match="unexpected shape"):
        await _provider().start(card=CARD, amount=AMOUNT, order_ref=ORDER_REF)


# --- confirm -------------------------------------------------------------------------


@respx.mock
async def test_confirm_verifies_then_pays_once() -> None:
    payme = mock_payme()

    outcome = await _provider().confirm(reference=_reference(), otp="666666")

    assert payme.methods == ["cards.verify", "receipts.pay"]
    assert payme.params("cards.verify") == {"token": PAYME_TOKEN, "code": "666666"}
    assert payme.auth("cards.verify") == "m-1234abcd"
    assert payme.params("receipts.pay") == {"id": PAYME_RECEIPT, "token": PAYME_TOKEN}
    assert payme.auth("receipts.pay") == "m-1234abcd:k-secret-9f"
    assert outcome.status == "paid"
    assert outcome.raw == {"receipt": PAYME_RECEIPT, "state": 4}
    assert PAYME_TOKEN not in repr(outcome)
    assert PAYME_TOKEN not in json.dumps(outcome.raw)


@respx.mock
async def test_confirm_wrong_code_is_failed_and_nothing_is_paid() -> None:
    payme = mock_payme({"cards.verify": RpcError(-31103, "Неверный код подтверждения")})

    outcome = await _provider().confirm(reference=_reference(), otp="000000")

    assert outcome.status == "failed"
    assert outcome.error == "Неверный код подтверждения"
    assert outcome.raw["error"] == {
        "code": -31103,
        "message": "Неверный код подтверждения",
    }
    assert payme.count("receipts.pay") == 0


@respx.mock
async def test_confirm_system_error_at_verify_is_failed_with_a_neutral_sentence() -> (
    None
):
    """Nothing is charged by ``cards.verify``, so the attempt may end; Payme's
    system wording is not for a customer to read."""
    payme = mock_payme({"cards.verify": RpcError(-32400, "System error")})

    outcome = await _provider().confirm(reference=_reference(), otp="666666")

    assert outcome.status == "failed"
    assert outcome.error == "The card was declined"
    assert outcome.raw["error"]["code"] == -32400
    assert payme.count("receipts.pay") == 0


@respx.mock
async def test_confirm_pay_refusal_is_failed_with_paymes_words() -> None:
    mock_payme({"receipts.pay": RpcError(-31611, "Недостаточно средств")})

    outcome = await _provider().confirm(reference=_reference(), otp="666666")

    assert outcome.status == "failed"
    assert outcome.error == "Недостаточно средств"


@respx.mock
async def test_confirm_pay_system_error_is_unknown() -> None:
    """Payme may have debited before its own machinery failed — the attempt
    stays open and ``status`` will ask."""
    mock_payme({"receipts.pay": RpcError(-32400, "System error")})

    with pytest.raises(UpstreamError) as failure:
        await _provider().confirm(reference=_reference(), otp="666666")

    assert failure.value.meta == {
        "upstream": {"code": -32400, "message": "System error"}
    }


@pytest.mark.parametrize(
    ("state", "status"),
    [(0, "pending"), (2, "pending"), (21, "pending"), (4, "paid"), (5, "paid")],
)
@respx.mock
async def test_confirm_reads_the_receipt_state(state: int, status: str) -> None:
    mock_payme({"receipts.pay": {"receipt": payme_receipt(state=state)}})

    outcome = await _provider().confirm(reference=_reference(), otp="666666")

    assert outcome.status == status
    assert outcome.raw["state"] == state


@respx.mock
async def test_confirm_cancelled_receipt_is_failed_with_its_reason() -> None:
    mock_payme(
        {
            "receipts.pay": {
                "receipt": payme_receipt(
                    state=50,
                    error={"code": -31630, "message": {"ru": "Карта заблокирована"}},
                )
            }
        }
    )

    outcome = await _provider().confirm(reference=_reference(), otp="666666")

    assert outcome.status == "failed"
    assert outcome.error == "Карта заблокирована"

    respx.reset()
    mock_payme({"receipts.pay": {"receipt": payme_receipt(state=50)}})
    outcome = await _provider().confirm(reference=_reference(), otp="666666")
    assert outcome.error == "The payment was cancelled"


@pytest.mark.parametrize(
    ("answer", "expected", "match"),
    [
        (httpx.ConnectTimeout("slow"), UpstreamTimeout, "did not answer"),
        (httpx.ConnectError("down"), UpstreamError, "unreachable"),
        (httpx.Response(502, text="bad gateway"), UpstreamError, "unexpected status"),
        (
            httpx.Response(200, text="<html>maintenance</html>"),
            UpstreamError,
            "cannot read",
        ),
        (
            httpx.Response(200, json={"result": "yes"}),
            UpstreamError,
            "unexpected shape",
        ),
        (httpx.Response(200, json=[1, 2]), UpstreamError, "unexpected shape"),
    ],
)
@respx.mock
async def test_confirm_non_answers_are_unknown_outcomes(
    answer: Any, expected: type[Exception], match: str
) -> None:
    mock_payme({"receipts.pay": answer})

    with pytest.raises(expected, match=match):
        await _provider().confirm(reference=_reference(), otp="666666")


@respx.mock
async def test_http_status_travels_as_the_upstream_code() -> None:
    mock_payme({"receipts.pay": httpx.Response(503)})

    with pytest.raises(UpstreamError) as failure:
        await _provider().confirm(reference=_reference(), otp="666666")

    assert failure.value.meta == {"upstream": {"code": 503}}


# --- status --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "status"), [(0, "pending"), (3, "pending"), (4, "paid"), (50, "failed")]
)
@respx.mock
async def test_status_checks_the_receipt(state: int, status: str) -> None:
    payme = mock_payme({"receipts.check": {"state": state}})

    outcome = await _provider().status(reference=_reference())

    assert payme.methods == ["receipts.check"]
    assert payme.params("receipts.check") == {"id": PAYME_RECEIPT}
    assert payme.auth("receipts.check") == "m-1234abcd:k-secret-9f"
    assert outcome.status == status
    assert outcome.raw == {"receipt": PAYME_RECEIPT, "state": state}


@respx.mock
async def test_status_refusal_of_any_code_is_unknown() -> None:
    mock_payme({"receipts.check": RpcError(-31602, "Чек не найден")})

    with pytest.raises(UpstreamError, match="Чек не найден"):
        await _provider().status(reference=_reference())


@respx.mock
async def test_a_foreign_reference_is_unknown_and_never_sent() -> None:
    """The sandbox's ``sbx-…`` or a test's ``ref-1`` must not reach Payme."""
    mock_payme()
    route = respx.routes[0]

    with pytest.raises(UpstreamError, match="not started with Payme"):
        await _provider().confirm(reference="sbx-0123456789abcdef", otp="666666")
    with pytest.raises(UpstreamError, match="not started with Payme"):
        await _provider().status(reference=json.dumps({"v": 2, "token": "t"}))
    assert route.call_count == 0


# --- probe ---------------------------------------------------------------------------


@respx.mock
async def test_probe_reads_receipts_with_both_halves_of_the_credentials() -> None:
    payme = mock_payme()

    await _provider().probe()

    assert payme.methods == ["receipts.get_all"]
    assert payme.auth("receipts.get_all") == "m-1234abcd:k-secret-9f"
    params = payme.params("receipts.get_all")
    assert params["count"] == 1
    assert params["to"] - params["from"] == 24 * 60 * 60 * 1000


@respx.mock
async def test_probe_refusal_carries_paymes_words() -> None:
    mock_payme({"receipts.get_all": RpcError(-32504, "Insufficient privileges")})

    with pytest.raises(UpstreamError, match="Insufficient privileges"):
        await _provider().probe()


# --- settings ------------------------------------------------------------------------


def test_settings_read_the_panels_keys_and_default_the_rest() -> None:
    settings = PaymeSettings.from_credentials({"merchant_id": " m-1 ", "key": "k-1"})
    assert settings.merchant_id == "m-1"
    assert settings.base_url == PAYME_PRODUCTION_URL
    assert settings.account_field == "order_id"
    assert settings.fiscal is None
    assert "k-1" not in repr(settings)

    test = PaymeSettings.from_credentials(
        {**PAYME_CREDENTIALS, "account_field": "booking"}
    )
    assert test.base_url == PAYME_TEST_URL
    assert test.account_field == "booking"
    assert PAYME_URL.startswith(test.base_url)


@pytest.mark.parametrize(
    ("credentials", "match"),
    [
        ({"merchant_id": "m-1"}, "incomplete: key"),
        ({"key": "k-1", "merchant_id": "  "}, "incomplete: merchant_id"),
        ({"merchant_id": "m-1", "key": "k-1", "environment": "staging"}, "one of"),
        (
            {"merchant_id": "m-1", "key": "k-1", "fiscal_title": "Aviachipta"},
            "together",
        ),
        (
            {
                "merchant_id": "m-1",
                "key": "k-1",
                "fiscal_title": "Aviachipta",
                "fiscal_code": "102",
                "fiscal_vat_percent": "twelve",
            },
            "whole number",
        ),
    ],
)
def test_settings_refuse_an_unfinished_panel(
    credentials: dict[str, str], match: str
) -> None:
    with pytest.raises(UpstreamError, match=match):
        PaymeSettings.from_credentials(credentials)


def test_fields_declare_one_secret_and_two_required_keys() -> None:
    assert [field.key for field in FIELDS if field.required] == ["merchant_id", "key"]
    assert [field.key for field in FIELDS if field.secret] == ["key"]
    environment = next(field for field in FIELDS if field.key == "environment")
    assert environment.choices == ("production", "test")
    assert environment.default == "production"
