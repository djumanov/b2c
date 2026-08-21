"""Payme — the Subscribe API, behind the payment port.

The merchant (the installation's owner) holds a Payme Business cashbox; the
customer never leaves our app. A charge is two steps on our side and five
JSON-RPC calls on Payme's (``POST {base}/api``):

``start``
    ``receipts.create`` — a receipt for the order's amount (tiyin) against the
    cashbox's ``account`` field; it is made **first** because it proves the
    ``merchant_id:key`` pair before the card is touched and before any SMS
    goes out. Then ``cards.create`` with ``save: false`` — the token lives for
    this one attempt (the saved-cards module keeps a PAN, not a Payme token) —
    and ``cards.get_verify_code``, which texts the cardholder.
``confirm``
    ``cards.verify`` with the code, then ``receipts.pay`` with the token — the
    one call that moves money, sent at most once per attempt because the
    orders module commits ``confirming`` before it calls here.
``status``
    ``receipts.check`` — the receipt's state, for a ``receipts.pay`` whose
    answer was lost.
``probe``
    ``receipts.get_all`` over the last day, ``count: 1`` — the panel's test
    button; read-only.

Everything ``confirm`` and ``status`` need — the card token and the receipt
id — travels in the ``reference`` ``start`` hands back, because the orders
module stores exactly that (encrypted) and nothing else.

**Failure is the contract that matters** (``base.py``). Payme answers in one
of three ways, and each lands differently:

* The transport failed, the status was not 200, the body was not JSON, or a
  JSON-RPC error carries a protocol/system code (``<= -32000``: wrong
  credentials, Payme's own fault) — the outcome is **unknown** or the problem
  is ours: ``UpstreamError`` / ``UpstreamTimeout``.
* A JSON-RPC error with a business code (``-31xxx``) is Payme saying **no**:
  at ``cards.*`` during ``start`` that is ``PaymentDeclined`` (the customer
  may try another card); at ``receipts.create`` it is ``UpstreamError`` (the
  amount or the cashbox, never the card — the customer must not be told to
  try another one); at ``confirm`` it is a ``failed`` outcome.
* A receipt state: 4 and 5 are paid, 50 is cancelled, anything else is still
  in progress and the sweep asks again.

Nothing here logs or stores a card number, a code or a token. The settings
come from the panel (``payment_providers.credentials``): ``merchant_id``,
``key``, ``environment`` (``production`` | ``test``), ``account_field``
(default ``order_id``) and, optionally, the fiscal item (``fiscal_title``,
``fiscal_code``, ``fiscal_vat_percent``, ``fiscal_package_code``,
``fiscal_units``) that goes out as ``detail`` when it is filled in.
"""

import itertools
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import httpx

from app.api.errors import UpstreamError, UpstreamTimeout
from app.core.logging import REQUEST_ID_HEADER, get_logger, request_id_var
from app.core.money import Money
from app.providers.payments.base import (
    CardDetails,
    PaymentDeclined,
    PaymentOutcome,
    PaymentStart,
    ProviderField,
)

logger = get_logger(__name__)

PAYME_CODE: Final = "payme"
PAYME_PRODUCTION_URL: Final = "https://checkout.paycom.uz"
PAYME_TEST_URL: Final = "https://checkout.test.paycom.uz"
ENVIRONMENTS: Final[dict[str, str]] = {
    "production": PAYME_PRODUCTION_URL,
    "test": PAYME_TEST_URL,
}

#: The only currency Payme settles; amounts go out in tiyin (1/100).
CURRENCY: Final = "UZS"


class PaymeTimeouts:
    """Both far below the sweep's 120 s ``CONFIRMING_STALE_AFTER``, so a slow
    answer still arrives before the sweep starts asking about it."""

    DEFAULT_SECONDS: float = 15.0
    PAY_SECONDS: float = 30.0


#: Receipt states (Payme's "Состояния чека"). 4 paid, 5 paid and archived;
#: 50 cancelled; everything else — created, checking, debiting, closing,
#: held, paused, queued — is still in progress.
PAID_STATES: Final[frozenset[int]] = frozenset({4, 5})
CANCELLED_STATES: Final[frozenset[int]] = frozenset({50})

#: A sentence, not a rendered error page — the same bound the GTS client uses.
_MAX_ERROR_CHARS: Final = 300
_DECLINED: Final = "The card was declined"
_UNEXPECTED_SHAPE: Final = "Payme answered in an unexpected shape"
_NOT_OURS: Final = "this payment was not started with Payme"
_REFERENCE_VERSION: Final = 1

#: What the panel asks for. ``key`` is the one secret; ``merchant_id`` is the
#: cashbox id Payme's own front-end integration sends in the clear.
FIELDS: Final[tuple[ProviderField, ...]] = (
    ProviderField("merchant_id", required=True),
    ProviderField("key", kind="secret", required=True),
    ProviderField(
        "environment",
        kind="choice",
        choices=tuple(ENVIRONMENTS),
        default="production",
    ),
    ProviderField("account_field", default="order_id"),
    ProviderField("fiscal_title"),
    ProviderField("fiscal_code"),
    ProviderField("fiscal_vat_percent", kind="int"),
    ProviderField("fiscal_package_code"),
    ProviderField("fiscal_units", kind="int"),
)

#: JSON-RPC request ids. Payme only echoes them; a per-process counter is all
#: that is needed to match an answer to its question in a log.
_ids = itertools.count(1)


# --- settings ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FiscalItem:
    """The one line of the fiscal receipt — a ticket, priced at the order."""

    title: str
    #: ИКПУ — the national product classification code.
    code: str
    vat_percent: int
    package_code: str | None = None
    units: int | None = None

    def detail(self, *, tiyin: int) -> dict[str, Any]:
        item: dict[str, Any] = {
            "title": self.title,
            "price": tiyin,
            "count": 1,
            "code": self.code,
            "vat_percent": self.vat_percent,
        }
        if self.package_code:
            item["package_code"] = self.package_code
        if self.units is not None:
            item["units"] = self.units
        return {"receipt_type": 0, "items": [item]}


def _whole_number(credentials: Mapping[str, str], key: str) -> int | None:
    value = (credentials.get(key) or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise UpstreamError(f"Payme setting {key} must be a whole number") from None


@dataclass(frozen=True, slots=True, repr=False)
class PaymeSettings:
    """The cashbox as the panel described it. ``key`` never prints."""

    merchant_id: str
    key: str
    base_url: str
    account_field: str
    fiscal: FiscalItem | None

    @classmethod
    def from_credentials(cls, credentials: Mapping[str, str]) -> "PaymeSettings":
        """Read the panel's keys; refuse an incomplete or contradictory set.

        Raised as ``UpstreamError`` on purpose: this runs inside
        ``payments.service.payment_provider`` — before any lock and before any
        attempt row — so an unfinished panel is a 502 with nothing written,
        and the same sentence is what the panel's test button shows.
        """
        missing = [
            field.key
            for field in FIELDS
            if field.required and not (credentials.get(field.key) or "").strip()
        ]
        if missing:
            raise UpstreamError(f"Payme settings are incomplete: {', '.join(missing)}")
        environment = (credentials.get("environment") or "production").strip()
        base_url = ENVIRONMENTS.get(environment)
        if base_url is None:
            raise UpstreamError(
                f"Payme environment must be one of {', '.join(ENVIRONMENTS)}"
            )
        title = (credentials.get("fiscal_title") or "").strip()
        code = (credentials.get("fiscal_code") or "").strip()
        vat_percent = _whole_number(credentials, "fiscal_vat_percent")
        fiscal: FiscalItem | None = None
        if title or code or vat_percent is not None:
            if not (title and code and vat_percent is not None):
                raise UpstreamError(
                    "Payme fiscal settings need fiscal_title, fiscal_code and "
                    "fiscal_vat_percent together"
                )
            fiscal = FiscalItem(
                title=title,
                code=code,
                vat_percent=vat_percent,
                package_code=(credentials.get("fiscal_package_code") or "").strip()
                or None,
                units=_whole_number(credentials, "fiscal_units"),
            )
        return cls(
            merchant_id=credentials["merchant_id"].strip(),
            key=credentials["key"].strip(),
            base_url=base_url,
            account_field=(credentials.get("account_field") or "order_id").strip(),
            fiscal=fiscal,
        )

    def __repr__(self) -> str:
        return (
            f"PaymeSettings(merchant_id={self.merchant_id!r}, "
            f"base_url={self.base_url!r})"
        )


# --- Payme's answers ------------------------------------------------------------------


def _text(message: Any) -> str | None:
    """Payme's words, in one language, bounded — or nothing.

    ``message`` is a string on the Subscribe API and a ``{ru, uz, en}`` object
    on the Merchant API; both are read, in the order the customers here speak.
    """
    if isinstance(message, Mapping):
        for language in ("uz", "ru", "en"):
            value = message.get(language)
            if isinstance(value, str) and value.strip():
                return value.strip()[:_MAX_ERROR_CHARS]
        return None
    if isinstance(message, str) and message.strip():
        return message.strip()[:_MAX_ERROR_CHARS]
    return None


class _Refusal(Exception):
    """A JSON-RPC ``error``: Payme answered, and the answer is no.

    Internal to this module — every call site translates it into what the
    port allows there: ``PaymentDeclined``, a ``failed`` outcome, or
    ``UpstreamError``. ``is_system`` separates Payme's protocol and system
    codes (``<= -32000``: bad credentials, an outage on their side) from the
    business codes (``-31xxx``) that are a verdict on the card or the code.
    """

    def __init__(self, code: int | None, text: str | None) -> None:
        super().__init__(text or f"Payme error {code}")
        self.code = code
        self.text = text

    @classmethod
    def from_payload(cls, error: Mapping[str, Any]) -> "_Refusal":
        code = error.get("code")
        if isinstance(code, bool) or not isinstance(code, int):
            code = None
        return cls(code, _text(error.get("message")))

    @property
    def is_system(self) -> bool:
        return self.code is None or self.code <= -32000

    @property
    def raw(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.text}

    def upstream(self) -> UpstreamError:
        return UpstreamError(
            f"Payme refused the request: {self.text or f'error {self.code}'}",
            upstream_code=self.code,
            upstream_message=self.text,
        )


def _tiyin(amount: Money) -> int:
    if amount.currency != CURRENCY:
        raise UpstreamError(
            f"Payme charges in {CURRENCY}; this order is priced in {amount.currency}"
        )
    # Money is quantized to 0.01, so this is exact.
    tiyin = int((amount.amount * 100).to_integral_value())
    if tiyin <= 0:
        raise UpstreamError("the order has no price to charge")
    return tiyin


def _pack(token: str, receipt: str) -> str:
    return json.dumps({"v": _REFERENCE_VERSION, "token": token, "receipt": receipt})


def _unpack(reference: str) -> tuple[str, str]:
    """The token and the receipt id ``start`` put into the reference.

    A reference another provider wrote (the sandbox's ``sbx-…``, say) is
    refused as an unknown outcome rather than guessed at: only the provider
    that started an attempt may settle it, and the orders module keeps the
    two apart before it gets here.
    """
    try:
        data = json.loads(reference)
    except ValueError as exc:
        raise UpstreamError(_NOT_OURS) from exc
    if (
        not isinstance(data, dict)
        or data.get("v") != _REFERENCE_VERSION
        or not isinstance(data.get("token"), str)
        or not isinstance(data.get("receipt"), str)
    ):
        raise UpstreamError(_NOT_OURS)
    return data["token"], data["receipt"]


def _state(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise UpstreamError(_UNEXPECTED_SHAPE)
    return value


def _receipt_of(result: Mapping[str, Any]) -> dict[str, Any]:
    receipt = result.get("receipt")
    if not isinstance(receipt, dict):
        raise UpstreamError(_UNEXPECTED_SHAPE)
    return receipt


def _outcome(
    reference: str, receipt: str, state: int, error: str | None
) -> PaymentOutcome:
    raw = {"receipt": receipt, "state": state}
    if state in PAID_STATES:
        return PaymentOutcome("paid", reference=reference, raw=raw)
    if state in CANCELLED_STATES:
        return PaymentOutcome(
            "failed",
            reference=reference,
            error=error or "The payment was cancelled",
            raw=raw,
        )
    return PaymentOutcome("pending", reference=reference, raw=raw)


# --- the adapter ----------------------------------------------------------------------


class PaymeProvider:
    """Implements ``PaymentProvider`` over Payme's Subscribe API."""

    code = PAYME_CODE

    def __init__(self, settings: PaymeSettings) -> None:
        self._settings = settings

    @classmethod
    def from_credentials(cls, credentials: dict[str, str]) -> "PaymeProvider":
        """The ``ADAPTERS`` factory — the panel's keys in, a provider out."""
        return cls(PaymeSettings.from_credentials(credentials))

    async def start(
        self, *, card: CardDetails, amount: Money, order_ref: str
    ) -> PaymentStart:
        tiyin = _tiyin(amount)
        receipt = await self._create_receipt(tiyin, order_ref)
        token, masked = await self._create_card(card)
        phone, wait = await self._send_code(token)
        return PaymentStart(
            reference=_pack(token, receipt),
            phone_hint=phone,
            raw={"receipt": receipt, "card": masked, "wait": wait},
        )

    async def confirm(self, *, reference: str, otp: str) -> PaymentOutcome:
        token, receipt = _unpack(reference)
        try:
            await self._rpc(
                "cards.verify",
                {"token": token, "code": otp},
                with_key=False,
                timeout=PaymeTimeouts.DEFAULT_SECONDS,
            )
        except _Refusal as refusal:
            # Nothing has been charged yet, whatever the code: the attempt is
            # over and the customer may start again. A system code gets a
            # neutral sentence — Payme's own is not written for a customer.
            text = _DECLINED if refusal.is_system else (refusal.text or _DECLINED)
            if refusal.is_system:
                logger.warning("payme_verify_failed", upstream_code=refusal.code)
            return PaymentOutcome(
                "failed",
                reference=reference,
                error=text,
                raw={"receipt": receipt, "state": None, "error": refusal.raw},
            )
        try:
            result = await self._rpc(
                "receipts.pay",
                {"id": receipt, "token": token},
                with_key=True,
                timeout=PaymeTimeouts.PAY_SECONDS,
            )
        except _Refusal as refusal:
            if refusal.is_system:
                # Payme may have debited before its own machinery failed:
                # unknown, so the attempt stays open and ``status`` asks.
                raise refusal.upstream() from refusal
            return PaymentOutcome(
                "failed",
                reference=reference,
                error=refusal.text or _DECLINED,
                raw={"receipt": receipt, "state": None, "error": refusal.raw},
            )
        paid = _receipt_of(result)
        state = _state(paid.get("state"))
        error = paid.get("error")
        reason = _text(error.get("message")) if isinstance(error, Mapping) else None
        logger.info("payme_paid", receipt=receipt, state=state)
        return _outcome(reference, receipt, state, reason)

    async def status(self, *, reference: str) -> PaymentOutcome:
        _, receipt = _unpack(reference)
        try:
            result = await self._rpc(
                "receipts.check",
                {"id": receipt},
                with_key=True,
                timeout=PaymeTimeouts.DEFAULT_SECONDS,
            )
        except _Refusal as refusal:
            # Whatever the code, the receipt's fate is still unknown to us.
            raise refusal.upstream() from refusal
        state = _state(result.get("state"))
        logger.info("payme_checked", receipt=receipt, state=state)
        return _outcome(reference, receipt, state, None)

    async def probe(self) -> None:
        now_ms = int(time.time() * 1000)
        try:
            await self._rpc(
                "receipts.get_all",
                {
                    "from": now_ms - 24 * 60 * 60 * 1000,
                    "to": now_ms,
                    "count": 1,
                    "offset": 0,
                },
                with_key=True,
                timeout=PaymeTimeouts.DEFAULT_SECONDS,
            )
        except _Refusal as refusal:
            raise refusal.upstream() from refusal

    # --- the three calls of ``start`` ----------------------------------------------

    async def _create_receipt(self, tiyin: int, order_ref: str) -> str:
        params: dict[str, Any] = {
            "amount": tiyin,
            "account": {self._settings.account_field: order_ref},
            "description": f"Order {order_ref}",
        }
        if self._settings.fiscal is not None:
            params["detail"] = self._settings.fiscal.detail(tiyin=tiyin)
        try:
            result = await self._rpc(
                "receipts.create",
                params,
                with_key=True,
                timeout=PaymeTimeouts.DEFAULT_SECONDS,
            )
        except _Refusal as refusal:
            # The amount, the account field, the cashbox — never the card.
            raise refusal.upstream() from refusal
        receipt = _receipt_of(result).get("_id")
        if not isinstance(receipt, str) or not receipt:
            raise UpstreamError(_UNEXPECTED_SHAPE)
        logger.info("payme_receipt_created", receipt=receipt, amount=tiyin)
        return receipt

    async def _create_card(self, card: CardDetails) -> tuple[str, str | None]:
        try:
            result = await self._rpc(
                "cards.create",
                {"card": {"number": card.number, "expire": card.expire}, "save": False},
                with_key=False,
                timeout=PaymeTimeouts.DEFAULT_SECONDS,
            )
        except _Refusal as refusal:
            if refusal.is_system:
                raise refusal.upstream() from refusal
            raise PaymentDeclined(refusal.text or _DECLINED) from refusal
        found = result.get("card")
        if not isinstance(found, dict):
            raise UpstreamError(_UNEXPECTED_SHAPE)
        token = found.get("token")
        if not isinstance(token, str) or not token:
            raise UpstreamError(_UNEXPECTED_SHAPE)
        number = found.get("number")
        return token, number if isinstance(number, str) else None

    async def _send_code(self, token: str) -> tuple[str | None, int | None]:
        try:
            result = await self._rpc(
                "cards.get_verify_code",
                {"token": token},
                with_key=False,
                timeout=PaymeTimeouts.DEFAULT_SECONDS,
            )
        except _Refusal as refusal:
            if refusal.is_system:
                raise refusal.upstream() from refusal
            raise PaymentDeclined(refusal.text or _DECLINED) from refusal
        if result.get("sent") is False:
            raise PaymentDeclined("Payme could not send the code to this card's phone")
        phone = result.get("phone")
        wait = result.get("wait")
        return (
            phone if isinstance(phone, str) and phone else None,
            wait if isinstance(wait, int) and not isinstance(wait, bool) else None,
        )

    # --- the wire -------------------------------------------------------------------

    async def _rpc(
        self,
        method: str,
        params: dict[str, Any],
        *,
        with_key: bool,
        timeout: float,
    ) -> dict[str, Any]:
        """One JSON-RPC call. Returns ``result``; raises ``_Refusal`` for a
        JSON-RPC ``error`` and the port's ``Upstream*`` for everything that is
        not an answer at all.

        ``cards.*`` authenticate with the cashbox id alone — the form Payme
        documents for them, and the one its front-end integrations use —
        ``receipts.*`` with ``id:key``. ``params`` are never logged: they
        carry the card, the code or the token.
        """
        auth = self._settings.merchant_id
        if with_key:
            auth = f"{auth}:{self._settings.key}"
        headers = {"X-Auth": auth, "Content-Type": "application/json"}
        request_id = request_id_var.get()
        if request_id:
            headers[REQUEST_ID_HEADER] = request_id
        body = {"id": next(_ids), "method": method, "params": params}
        began = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=False
            ) as http:
                response = await http.post(
                    f"{self._settings.base_url}/api", json=body, headers=headers
                )
        except httpx.TimeoutException as exc:
            logger.warning("payme_timeout", method=method)
            raise UpstreamTimeout("Payme did not answer in time") from exc
        except httpx.HTTPError as exc:
            logger.warning("payme_unreachable", method=method, error=str(exc))
            raise UpstreamError(f"Payme is unreachable: {exc}") from exc
        elapsed_ms = round((time.monotonic() - began) * 1000, 1)

        if response.status_code != 200:
            logger.warning(
                "payme_http_error",
                method=method,
                status=response.status_code,
                ms=elapsed_ms,
            )
            raise UpstreamError(
                "Payme returned an unexpected status",
                upstream_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("payme_unparsable", method=method, ms=elapsed_ms)
            raise UpstreamError("Payme returned a body we cannot read") from exc
        if not isinstance(payload, dict):
            raise UpstreamError(_UNEXPECTED_SHAPE)

        error = payload.get("error")
        if isinstance(error, dict):
            refusal = _Refusal.from_payload(error)
            logger.warning(
                "payme_refused",
                method=method,
                upstream_code=refusal.code,
                ms=elapsed_ms,
            )
            raise refusal
        result = payload.get("result")
        if not isinstance(result, dict):
            raise UpstreamError(_UNEXPECTED_SHAPE)
        logger.info("payme_rpc", method=method, ms=elapsed_ms)
        return result


__all__ = [
    "CANCELLED_STATES",
    "CURRENCY",
    "ENVIRONMENTS",
    "FIELDS",
    "PAID_STATES",
    "PAYME_CODE",
    "PAYME_PRODUCTION_URL",
    "PAYME_TEST_URL",
    "FiscalItem",
    "PaymeProvider",
    "PaymeSettings",
    "PaymeTimeouts",
]
