"""What the customer's screen says about an order, per status, in three languages.

One sentence per public ``status`` (``lifecycle.Stage``), served by the API
as ``order.message`` — so a web client and two mobile clients cannot drift
into three different wordings of "we took your money and the ticket did not
come out".

**The text is the panel's, the defaults are ours.** The sentences below are
what an installation starts with; ``/admin/orders/messages/`` lets staff
rewrite any of them in any language, and what they write is shown **as
written** — no placeholders, no interpolation. A support phone number
belongs in the sentence itself, typed by the people who answer it.

``ticketing_failed`` covers every "money taken, no ticket coming" case — a
GTS refusal, a paid order staff cancelled, a refund under way — so its
default promises nothing; the panel says what happens next.

The language is the request's (``LanguageDep``) and the fallback chain is the
installation's (``Languages.default`` / ``available``), exactly like every
other translated field.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from app.core import i18n
from app.modules.orders.lifecycle import Stage

DEFAULTS: Final[dict[Stage, i18n.Translated]] = {
    Stage.BOOKED: {
        "uz": "Bron qilindi. Chiptani olish uchun to'lovni belgilangan muddatgacha "
        "amalga oshiring.",
        "ru": "Бронирование создано. Оплатите заказ до истечения срока, чтобы "
        "получить билет.",
        "en": "Your booking is held. Pay before the deadline to have the ticket "
        "issued.",
    },
    Stage.TICKET_WAITING: {
        "uz": "To'lov qabul qilindi. Chipta rasmiylashtirilmoqda, bu biroz vaqt "
        "olishi mumkin.",
        "ru": "Оплата принята. Билет оформляется, это может занять некоторое время.",
        "en": "Your payment was received. Your ticket is being issued — this may "
        "take a little while.",
    },
    Stage.TICKETED: {
        "uz": "Chiptalaringiz tayyor.",
        "ru": "Ваши билеты готовы.",
        "en": "Your tickets are ready.",
    },
    Stage.TICKETING_FAILED: {
        "uz": "To'lov qabul qilindi, ammo chipta chiqarilmadi. Iltimos, "
        "qo'llab-quvvatlash xizmatiga murojaat qiling.",
        "ru": "Оплата принята, но билет не был оформлен. Пожалуйста, обратитесь в "
        "службу поддержки.",
        "en": "Your payment was received, but the ticket was not issued. Please "
        "contact support.",
    },
    Stage.REFUNDED: {
        "uz": "Mablag'ingiz qaytarildi.",
        "ru": "Средства возвращены.",
        "en": "Your refund has been completed.",
    },
    Stage.CANCELLED: {
        "uz": "Buyurtma bekor qilindi. Yangi qidiruv qiling.",
        "ru": "Заказ отменён. Начните поиск заново.",
        "en": "The order was cancelled. Please search again.",
    },
}


@dataclass(frozen=True, slots=True)
class MessageCatalogue:
    """The sentences of one installation: the panel's words over our defaults.

    ``overrides`` holds only what staff wrote (``{status: {lang: text}}``);
    everything else reads from ``DEFAULTS``, so a status or a language added
    in a new release has a sentence the moment the release lands. Built by
    ``service.message_catalogue`` and handed to the schemas, which stay pure.
    """

    overrides: Mapping[str, i18n.Translated] = field(default_factory=dict)
    default_language: str = i18n.DEFAULT_LANGUAGE
    available: tuple[str, ...] = i18n.SUPPORTED_LANGUAGES

    def text(self, status: Stage) -> i18n.Translated:
        """Every language of one status, the panel's word winning per language."""
        custom = self.overrides.get(status.value, {})
        merged = {**DEFAULTS[status], **custom}
        return {
            lang: value for lang, value in merged.items() if value and value.strip()
        }

    def render(self, status: Stage, *, language: str | None) -> str:
        """The sentence for ``status`` in the requested language — verbatim."""
        return (
            i18n.resolve_value(
                self.text(status),
                requested=language,
                default=self.default_language,
                available=self.available,
            )
            or ""
        )


__all__ = ["DEFAULTS", "MessageCatalogue"]
