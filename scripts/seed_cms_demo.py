"""Seed demo CMS content: FAQ entries and static pages in uz / ru / en.

Operational tooling, not application code — it drives the cms module through
its service functions, the same way a panel operator would, and publishes
everything it creates so the public surface can serve it straight away.

Idempotent: pages are matched by slug, FAQ entries by their Uzbek question;
existing rows are left untouched, so the script is safe to run twice —
locally and on the demo server alike.

    uv run python scripts/seed_cms_demo.py
"""

import asyncio
import sys
from pathlib import Path

# Python puts scripts/ on sys.path, not the repo root where ``app`` lives.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import exists_live
from app.db.session import dispose_engine, get_sessionmaker
from app.modules.cms import service as cms_service
from app.modules.cms.models import ContentStatus, Faq
from app.modules.cms.models import Page as PageModel
from app.modules.cms.schemas import FaqIn, FixedPageIn

# --- pages -----------------------------------------------------------------------

PAGES: list[dict] = [
    {
        "slug": "privacy-policy",
        "title": {
            "uz": "Maxfiylik siyosati",
            "ru": "Политика конфиденциальности",
            "en": "Privacy Policy",
        },
        "body": {
            "uz": (
                "# Maxfiylik siyosati\n\n"
                "Ushbu hujjat xizmatdan foydalanishda shaxsiy "
                "ma'lumotlaringiz qanday to'planishi, saqlanishi va "
                "ishlatilishini tushuntiradi.\n\n"
                "## Qanday ma'lumotlar to'planadi\n\n"
                "- Ism va aloqa ma'lumotlari (telefon, email)\n"
                "- Buyurtma va to'lov tarixi\n"
                "- Qurilma va foydalanish ma'lumotlari\n\n"
                "## Ma'lumotlardan foydalanish\n\n"
                "Ma'lumotlar faqat buyurtmalarni rasmiylashtirish, "
                "xizmat sifatini oshirish va qonun talablarini bajarish "
                "uchun ishlatiladi. Karta raqamlari saqlanmaydi.\n\n"
                "## Bog'lanish\n\n"
                "Savollar bo'lsa, qo'llab-quvvatlash xizmatiga murojaat "
                "qiling."
            ),
            "ru": (
                "# Политика конфиденциальности\n\n"
                "Этот документ описывает, как собираются, хранятся и "
                "используются ваши персональные данные при пользовании "
                "сервисом.\n\n"
                "## Какие данные собираются\n\n"
                "- Имя и контактные данные (телефон, email)\n"
                "- История заказов и платежей\n"
                "- Данные об устройстве и использовании\n\n"
                "## Использование данных\n\n"
                "Данные используются только для оформления заказов, "
                "улучшения качества сервиса и выполнения требований "
                "закона. Номера карт не хранятся.\n\n"
                "## Контакты\n\n"
                "По вопросам обращайтесь в службу поддержки."
            ),
            "en": (
                "# Privacy Policy\n\n"
                "This document explains how your personal data is "
                "collected, stored and used when you use the service.\n\n"
                "## What we collect\n\n"
                "- Name and contact details (phone, email)\n"
                "- Order and payment history\n"
                "- Device and usage data\n\n"
                "## How we use it\n\n"
                "Data is used only to process orders, improve the service "
                "and meet legal requirements. Card numbers are never "
                "stored.\n\n"
                "## Contact\n\n"
                "For any questions, reach out to our support team."
            ),
        },
    },
    {
        "slug": "terms",
        "title": {
            "uz": "Foydalanish shartlari",
            "ru": "Условия использования",
            "en": "Terms of Use",
        },
        "body": {
            "uz": (
                "# Foydalanish shartlari\n\n"
                "Xizmatdan foydalanish orqali siz quyidagi shartlarga "
                "rozilik bildirasiz.\n\n"
                "## Buyurtma va to'lov\n\n"
                "Buyurtma to'lov tasdiqlangandan so'ng kuchga kiradi. "
                "Narxlar buyurtma paytidagi holatda qat'iy belgilanadi.\n\n"
                "## Bekor qilish va qaytarish\n\n"
                "Bekor qilish shartlari har bir mahsulot sahifasida "
                "ko'rsatiladi. Qaytarish ariza asosida ko'rib chiqiladi.\n\n"
                "## Mas'uliyat\n\n"
                "Xizmat uchinchi tomon yetkazib beruvchilarining "
                "o'zgarishlari uchun javobgar emas, lekin muammolarni hal "
                "qilishda yordam beradi."
            ),
            "ru": (
                "# Условия использования\n\n"
                "Пользуясь сервисом, вы соглашаетесь со следующими "
                "условиями.\n\n"
                "## Заказ и оплата\n\n"
                "Заказ вступает в силу после подтверждения оплаты. Цены "
                "фиксируются на момент оформления заказа.\n\n"
                "## Отмена и возврат\n\n"
                "Условия отмены указаны на странице каждого продукта. "
                "Возврат рассматривается по заявлению.\n\n"
                "## Ответственность\n\n"
                "Сервис не отвечает за изменения со стороны сторонних "
                "поставщиков, но помогает в решении проблем."
            ),
            "en": (
                "# Terms of Use\n\n"
                "By using the service you agree to the following terms.\n\n"
                "## Orders and payment\n\n"
                "An order takes effect once payment is confirmed. Prices "
                "are fixed at the moment of ordering.\n\n"
                "## Cancellation and refunds\n\n"
                "Cancellation terms are shown on each product page. "
                "Refunds are reviewed on request.\n\n"
                "## Liability\n\n"
                "The service is not responsible for changes made by "
                "third-party suppliers, but will help resolve any issues."
            ),
        },
    },
    {
        "slug": "about",
        "title": {
            "uz": "Biz haqimizda",
            "ru": "О нас",
            "en": "About Us",
        },
        "body": {
            "uz": (
                "# Biz haqimizda\n\n"
                "Biz sayohat mahsulotlarini onlayn band qilish "
                "xizmatimiz: turlar, mehmonxonalar va transferlar — "
                "hammasi bir joyda.\n\n"
                "Maqsadimiz — sayohatni rejalashtirishni oddiy, tez va "
                "ishonchli qilish."
            ),
            "ru": (
                "# О нас\n\n"
                "Мы — сервис онлайн-бронирования туристических "
                "продуктов: туры, отели и трансферы в одном месте.\n\n"
                "Наша цель — сделать планирование путешествий простым, "
                "быстрым и надёжным."
            ),
            "en": (
                "# About Us\n\n"
                "We are an online booking service for travel products: "
                "tours, hotels and transfers, all in one place.\n\n"
                "Our goal is to make travel planning simple, fast and "
                "reliable."
            ),
        },
    },
]

# --- faq -------------------------------------------------------------------------

FAQS: list[dict] = [
    {
        "category": "booking",
        "question": {
            "uz": "Buyurtmani qanday rasmiylashtiraman?",
            "ru": "Как оформить заказ?",
            "en": "How do I place an order?",
        },
        "answer": {
            "uz": (
                "Mahsulotni tanlang, sana va ishtirokchilarni belgilang, "
                "so'ng to'lovni amalga oshiring. Tasdiqlash email orqali "
                "yuboriladi."
            ),
            "ru": (
                "Выберите продукт, укажите дату и участников, затем "
                "оплатите. Подтверждение придёт на email."
            ),
            "en": (
                "Choose a product, set the date and participants, then "
                "pay. A confirmation is sent by email."
            ),
        },
    },
    {
        "category": "booking",
        "question": {
            "uz": "Buyurtmani bekor qilsam bo'ladimi?",
            "ru": "Могу ли я отменить заказ?",
            "en": "Can I cancel my order?",
        },
        "answer": {
            "uz": (
                "Ha, bekor qilish shartlari mahsulot sahifasida "
                "ko'rsatilgan. Muddatga qarab to'lov to'liq yoki qisman "
                "qaytarilishi mumkin."
            ),
            "ru": (
                "Да, условия отмены указаны на странице продукта. В "
                "зависимости от срока оплата возвращается полностью или "
                "частично."
            ),
            "en": (
                "Yes — cancellation terms are shown on the product page. "
                "Depending on the timing, the payment is refunded fully "
                "or partially."
            ),
        },
    },
    {
        "category": "payment",
        "question": {
            "uz": "Qanday to'lov usullari mavjud?",
            "ru": "Какие способы оплаты доступны?",
            "en": "What payment methods are available?",
        },
        "answer": {
            "uz": (
                "Bank kartalari orqali onlayn to'lov qabul qilinadi. "
                "Karta ma'lumotlaringiz bizda saqlanmaydi."
            ),
            "ru": (
                "Принимается онлайн-оплата банковскими картами. Данные "
                "вашей карты у нас не хранятся."
            ),
            "en": (
                "Online payment by bank card is accepted. Your card "
                "details are never stored on our side."
            ),
        },
    },
    {
        "category": "payment",
        "question": {
            "uz": "To'lov o'tmadi, pul yechildi — nima qilay?",
            "ru": "Оплата не прошла, но деньги списались — что делать?",
            "en": "Payment failed but money was charged — what now?",
        },
        "answer": {
            "uz": (
                "Odatda bunday mablag' bank tomonidan 1–3 ish kunida "
                "avtomatik qaytariladi. Qaytmasa, qo'llab-quvvatlashga "
                "buyurtma raqami bilan murojaat qiling."
            ),
            "ru": (
                "Обычно банк автоматически возвращает такие средства в "
                "течение 1–3 рабочих дней. Если возврата нет, обратитесь "
                "в поддержку с номером заказа."
            ),
            "en": (
                "Such funds are usually returned automatically by the "
                "bank within 1–3 business days. If not, contact support "
                "with your order number."
            ),
        },
    },
    {
        "category": "general",
        "question": {
            "uz": "Qo'llab-quvvatlash bilan qanday bog'lanaman?",
            "ru": "Как связаться с поддержкой?",
            "en": "How do I contact support?",
        },
        "answer": {
            "uz": (
                "Saytdagi aloqa formasi yoki ko'rsatilgan telefon raqami "
                "orqali murojaat qilishingiz mumkin."
            ),
            "ru": (
                "Вы можете обратиться через форму обратной связи на "
                "сайте или по указанному номеру телефона."
            ),
            "en": (
                "You can reach us through the contact form on the site "
                "or by the phone number listed there."
            ),
        },
    },
    {
        "category": "general",
        "question": {
            "uz": "Sayt qaysi tillarda ishlaydi?",
            "ru": "На каких языках работает сайт?",
            "en": "What languages does the site support?",
        },
        "answer": {
            "uz": (
                "Sayt o'zbek, rus va ingliz tillarida ishlaydi. Til "
                "so'rov parametri orqali tanlanadi, matn topilmasa asosiy "
                "tilga qaytadi."
            ),
            "ru": (
                "Сайт работает на узбекском, русском и английском. Язык "
                "выбирается параметром запроса; если текста нет, "
                "возвращается основной язык."
            ),
            "en": (
                "The site works in Uzbek, Russian and English. The "
                "language is chosen via a query parameter, falling back "
                "to the default when a text is missing."
            ),
        },
    },
]


async def seed_pages(session: AsyncSession) -> None:
    for spec in PAGES:
        slug = spec["slug"]
        # Skip rather than upsert: a rerun must never clobber edited text.
        if await exists_live(session, PageModel, PageModel.slug == slug):
            print(f"page {slug!r}: already exists, skipped")
            continue
        await cms_service.upsert_fixed_page(
            session, slug, FixedPageIn(title=spec["title"], body=spec["body"])
        )
        await cms_service.set_fixed_page_status(session, slug, ContentStatus.PUBLISHED)
        print(f"page {slug!r}: created and published")


async def seed_faqs(session: AsyncSession) -> None:
    for spec in FAQS:
        question_uz = spec["question"]["uz"]
        if await exists_live(session, Faq, Faq.question["uz"].astext == question_uz):
            print(f"faq {question_uz!r}: already exists, skipped")
            continue
        created = await cms_service.create_faq(session, FaqIn(**spec))
        await cms_service.set_faq_status(session, created.id, ContentStatus.PUBLISHED)
        print(f"faq {question_uz!r}: created and published")


async def main() -> None:
    async with get_sessionmaker()() as session:
        await seed_pages(session)
        await seed_faqs(session)
    await dispose_engine()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:  # noqa: BLE001 — a CLI entry point reports and exits
        print(f"seed failed: {error}", file=sys.stderr)
        sys.exit(1)
