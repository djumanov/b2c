"""What the customer's screen says about an order, per stage, in three languages.

One sentence per ``Stage``, served by the API as ``order.message`` — so a web
client and two mobile clients cannot drift into three different wordings of
"we took your money and the ticket did not come out".

**The text is the panel's, the defaults are ours.** The sentences below are
what an installation starts with; ``/admin/orders/messages/`` lets staff
rewrite any of them in any language, and what they write is shown **as
written** — no placeholders, no interpolation. A support phone number
belongs in the sentence itself, typed by the people who answer it.

The language is the request's (``LanguageDep``) and the fallback chain is the
installation's (``Languages.default`` / ``available``), exactly like every
other translated field (API.md §7).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from app.core import i18n
from app.modules.orders.lifecycle import Stage

DEFAULTS: Final[dict[Stage, i18n.Translated]] = {
    Stage.AWAITING_PAYMENT: {
        "uz": "Bron qilindi. Chiptani olish uchun to'lovni belgilangan muddatgacha "
        "amalga oshiring.",
        "ru": "Бронирование создано. Оплатите заказ до истечения срока, чтобы "
        "получить билет.",
        "en": "Your booking is held. Pay before the deadline to have the ticket "
        "issued.",
    },
    Stage.PAYMENT_PROCESSING: {
        "uz": "To'lov tekshirilmoqda. Iltimos, biroz kuting.",
        "ru": "Платёж проверяется. Пожалуйста, подождите.",
        "en": "Your payment is being confirmed. Please wait a moment.",
    },
    Stage.PAYMENT_FAILED: {
        "uz": "To'lov amalga oshmadi. Qayta urinib ko'ring yoki boshqa kartadan "
        "foydalaning.",
        "ru": "Оплата не прошла. Попробуйте ещё раз или используйте другую карту.",
        "en": "The payment did not go through. Please try again or use another card.",
    },
    Stage.TICKETING: {
        "uz": "To'lovingiz muvaffaqiyatli amalga oshirildi. Ticketing jarayoni "
        "davom etmoqda. Iltimos, biroz kuting.",
        "ru": "Оплата прошла успешно. Идёт оформление билета. Пожалуйста, подождите.",
        "en": "Your payment was successful. Your ticket is being issued — please wait.",
    },
    Stage.TICKETED: {
        "uz": "Chiptalaringiz tayyor.",
        "ru": "Ваши билеты готовы.",
        "en": "Your tickets are ready.",
    },
    Stage.TICKETING_FAILED: {
        "uz": "To'lovingiz muvaffaqiyatli amalga oshirildi, ammo ticket chiqarish "
        "jarayonida texnik xatolik yuz berdi. Mablag'ingizni qaytarish uchun "
        "support xizmatiga murojaat qiling.",
        "ru": "Оплата прошла успешно, но при оформлении билета произошла "
        "техническая ошибка. Для возврата средств обратитесь в службу "
        "поддержки.",
        "en": "Your payment was successful, but a technical error occurred while "
        "issuing the ticket. Please contact support for a refund.",
    },
    Stage.CANCELLED: {
        "uz": "Buyurtma bekor qilindi.",
        "ru": "Заказ отменён.",
        "en": "The order was cancelled.",
    },
    Stage.EXPIRED: {
        "uz": "To'lov muddati o'tib ketdi va bron bekor qilindi. Qidiruvni "
        "qaytadan boshlang.",
        "ru": "Срок оплаты истёк, бронирование отменено. Начните поиск заново.",
        "en": "The payment deadline passed and the booking was released. Please "
        "search again.",
    },
    Stage.REFUND_DUE: {
        "uz": "Mablag'ingiz qaytarilishi kerak. Support xizmatiga murojaat qiling.",
        "ru": "Вам полагается возврат средств. Обратитесь в службу поддержки.",
        "en": "A refund is due to you. Please contact support.",
    },
    Stage.REFUNDING: {
        "uz": "Mablag'ingiz qaytarilmoqda.",
        "ru": "Возврат средств выполняется.",
        "en": "Your refund is being processed.",
    },
    Stage.REFUNDED: {
        "uz": "Mablag'ingiz qaytarildi.",
        "ru": "Средства возвращены.",
        "en": "Your refund has been completed.",
    },
}


@dataclass(frozen=True, slots=True)
class MessageCatalogue:
    """The sentences of one installation: the panel's words over our defaults.

    ``overrides`` holds only what staff wrote (``{stage: {lang: text}}``);
    everything else reads from ``DEFAULTS``, so a stage or a language added
    in a new release has a sentence the moment the release lands. Built by
    ``service.message_catalogue`` and handed to the schemas, which stay pure.
    """

    overrides: Mapping[str, i18n.Translated] = field(default_factory=dict)
    default_language: str = i18n.DEFAULT_LANGUAGE
    available: tuple[str, ...] = i18n.SUPPORTED_LANGUAGES

    def text(self, stage: Stage) -> i18n.Translated:
        """Every language of one stage, the panel's word winning per language."""
        custom = self.overrides.get(stage.value, {})
        merged = {**DEFAULTS[stage], **custom}
        return {
            lang: value for lang, value in merged.items() if value and value.strip()
        }

    def render(self, stage: Stage, *, language: str | None) -> str:
        """The sentence for ``stage`` in the requested language — verbatim."""
        return (
            i18n.resolve_value(
                self.text(stage),
                requested=language,
                default=self.default_language,
                available=self.available,
            )
            or ""
        )


__all__ = ["DEFAULTS", "MessageCatalogue"]
