"""What the customer's screen says about an order, per stage, in three languages.

One sentence per ``Stage``, written once here and served by the API as
``order.message`` — so a web client and two mobile clients cannot drift into
three different wordings of "we took your money and the ticket did not come
out". The language is the request's (``LanguageDep``), exactly like every
other translated field (API.md §7); there is no per-customer language yet.

The support contact is interpolated, not hardcoded: it is a panel setting
(``Site.support_phone`` / ``support_email``), and an installation without one
simply gets the sentence without the trailing contact.
"""

from typing import Final

from app.core import i18n
from app.modules.orders.lifecycle import Stage
from app.modules.settings.service import SupportContact

#: ``{support}`` is the contact suffix — ``": +998 …, help@…"`` or nothing.
_MESSAGES: Final[dict[Stage, i18n.Translated]] = {
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
        "support xizmatiga murojaat qiling{support}.",
        "ru": "Оплата прошла успешно, но при оформлении билета произошла "
        "техническая ошибка. Для возврата средств обратитесь в службу "
        "поддержки{support}.",
        "en": "Your payment was successful, but a technical error occurred while "
        "issuing the ticket. Please contact support for a refund{support}.",
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
        "uz": "Mablag'ingiz qaytarilishi kerak. Support xizmatiga murojaat "
        "qiling{support}.",
        "ru": "Вам полагается возврат средств. Обратитесь в службу поддержки{support}.",
        "en": "A refund is due to you. Please contact support{support}.",
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


def message_for(stage: Stage, *, language: str | None, support: SupportContact) -> str:
    """The sentence for ``stage`` in the requested language (default chain)."""
    template = i18n.resolve_value(_MESSAGES[stage], requested=language) or ""
    contact = support.text()
    return template.format(support=f": {contact}" if contact else "")


__all__ = ["message_for"]
