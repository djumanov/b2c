"""Seed demo content: CMS pages/FAQ/fun facts, profile deletion reasons, the
site's company contacts, leads support contact, available currencies and the
order status messages customers see while paying and ticketing, in uz / ru / en.

Operational tooling, not application code — it drives the cms, customers and
settings modules through their service functions, the same way a panel
operator would, and publishes everything it creates so the public surface
can serve it straight away.

Safe to run twice — locally and on the demo server alike. Pages are matched
by slug and always **updated** to match ``PAGES`` below (this is demo
content, meant to be kept current, not an admin's own edit). FAQ entries,
fun facts and deletion reasons are matched by their Uzbek text; site
settings, currencies and order messages by still being at their untouched
defaults — those are left alone once set, since there is no seed-vs-edited
distinction to draw for them.

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
from app.modules.cms.models import ContentStatus, Faq, FunFact
from app.modules.cms.models import Page as PageModel
from app.modules.cms.schemas import FaqIn, FixedPageIn, FunFactIn
from app.modules.customers import service as customers_service
from app.modules.customers.models import DeletionReason
from app.modules.customers.schemas import DeletionReasonIn
from app.modules.leads import service as leads_service
from app.modules.leads.schemas import SupportContactIn
from app.modules.orders import service as orders_service
from app.modules.orders.lifecycle import Stage
from app.modules.orders.schemas import OrderMessageIn
from app.modules.settings import service as settings_service
from app.modules.settings.schemas import CurrenciesIn, SiteIn

# --- pages -----------------------------------------------------------------------

PAGES: list[dict] = [
    {
        "slug": "privacy-policy",
        "title": {
            "uz": "Maxfiylik siyosati",
            "ru": "Политика конфиденциальности",
            "en": "Privacy Policy",
        },
        # Deliberately long and rich in markdown elements — headings, lists,
        # a table, a blockquote, a code block, bold/italic, a link, a rule —
        # so the mobile client's scroll and every markdown tag get exercised
        # by demo data, not just a paragraph or two.
        "body": {
            "uz": (
                "# Maxfiylik siyosati\n\n"
                "Ushbu hujjat xizmatdan foydalanishda shaxsiy "
                "ma'lumotlaringiz qanday to'planishi, saqlanishi, qayta "
                "ishlanishi va himoyalanishini tushuntiradi. Xizmatdan "
                "foydalanish orqali siz ushbu siyosatga rozilik "
                "bildirasiz.\n\n"
                "> **Muhim:** karta ma'lumotlaringiz (raqam, CVV, amal "
                "muddati) bizning serverlarimizda **hech qachon** "
                "saqlanmaydi va loglarga yozilmaydi.\n\n"
                "## 1. Qanday ma'lumotlar to'planadi\n\n"
                "Ro'yxatdan o'tish va buyurtma berish jarayonida quyidagi "
                "ma'lumotlar to'planishi mumkin:\n\n"
                "- Ism va familiya\n"
                "- Telefon raqami va email manzili\n"
                "- Tug'ilgan sana (chipta uchun majburiy bo'lsa)\n"
                "- Pasport yoki boshqa hujjat ma'lumotlari (xalqaro "
                "reyslar uchun)\n"
                "- Buyurtma va to'lov tarixi\n"
                "- Qurilma turi, IP manzil va foydalanish statistikasi\n"
                "- Ilova sozlamalari: til, valyuta, bildirishnoma "
                "tanlovlari\n\n"
                "### 1.1. Avtomatik to'planadigan ma'lumotlar\n\n"
                "Ilova va sayt quyidagilarni avtomatik qayd etadi: "
                "sessiya davomiyligi, bosilgan tugmalar, xatolik loglar. "
                "Bu ma'lumotlar shaxsni aniqlash uchun emas, xizmat "
                "sifatini oshirish uchun ishlatiladi.\n\n"
                "## 2. Ma'lumotlardan qanday foydalanamiz\n\n"
                "Ma'lumotlaringiz quyidagi maqsadlarda ishlatiladi:\n\n"
                "1. Buyurtmani rasmiylashtirish va yetkazib berish\n"
                "2. To'lovni amalga oshirish va tasdiqlash\n"
                "3. Mijozlar bilan aloqa — eslatmalar, bildirishnomalar\n"
                "4. Xizmat sifatini yaxshilash va statistik tahlil\n"
                "5. Qonun talablarini bajarish (soliq, hisobot)\n\n"
                "Ma'lumotlar **hech qachon** uchinchi tomonlarga "
                "sotilmaydi. Faqat buyurtmani bajarish uchun zarur "
                "bo'lgan hamkorlar (aviakompaniya, mehmonxona, to'lov "
                "provayderi) bilan zarur hajmda almashinadi.\n\n"
                "## 3. Ma'lumotlarni saqlash muddati\n\n"
                "| Ma'lumot turi | Saqlash muddati | Izoh |\n"
                "|---|---|---|\n"
                "| Buyurtma tarixi | 5 yil | Soliq hisoboti uchun |\n"
                "| Aloqa ma'lumotlari | Akkaunt o'chirilgunicha | Profil "
                "sozlamalarida |\n"
                "| Karta ma'lumotlari | Saqlanmaydi | To'lov provayderi "
                "tomonida |\n"
                "| Loglar (texnik) | 90 kun | Xatoliklarni tahlil "
                "qilish |\n\n"
                "## 4. Xavfsizlik choralari\n\n"
                "Ma'lumotlaringiz `TLS 1.2+` orqali uzatiladi, "
                "ma'lumotlar bazasida shifrlangan holda saqlanadi. "
                "Xodimlarning kirish huquqi rol asosida cheklangan — har "
                "bir kirish audit jurnaliga yoziladi.\n\n"
                "```\n"
                "To'lov so'rovi hech qachon karta raqamini to'liq "
                "qaytarmaydi,\n"
                "faqat oxirgi 4 raqam ko'rsatiladi: **** **** **** 1234\n"
                "```\n\n"
                "## 5. Cookie va shunga o'xshash texnologiyalar\n\n"
                "Sayt versiyasi sessiya va sozlamalarni saqlash uchun "
                "cookie'lardan foydalanadi. Brauzer sozlamalaridan "
                "cookie'larni o'chirish mumkin, biroq bu holda ba'zi "
                "funksiyalar ishlamasligi mumkin.\n\n"
                "## 6. Sizning huquqlaringiz\n\n"
                "- O'z ma'lumotlaringizni ko'rish va tahrirlash\n"
                "- Ma'lumotlarning nusxasini so'rash\n"
                "- Akkauntni va unga tegishli ma'lumotlarni o'chirishni "
                "so'rash\n"
                "- Marketing xabarlaridan voz kechish\n\n"
                "Bu huquqlardan foydalanish uchun profil bo'limiga yoki "
                "qo'llab-quvvatlash xizmatiga murojaat qiling.\n\n"
                "## 7. Bolalar maxfiyligi\n\n"
                "Xizmat 18 yoshdan katta shaxslar uchun mo'ljallangan. "
                "Agar 18 yoshga to'lmagan shaxsning ma'lumotlari "
                "beixtiyor to'plangani aniqlansa, ular darhol "
                "o'chiriladi.\n\n"
                "## 8. Siyosatga o'zgartirishlar\n\n"
                "Ushbu siyosat vaqti-vaqti bilan yangilanishi mumkin. "
                "Muhim o'zgarishlar haqida email yoki ilova ichidagi "
                "bildirishnoma orqali xabar beramiz.\n\n"
                "---\n\n"
                "## 9. Bog'lanish\n\n"
                "Savollar yoki so'rovlar bo'lsa, *qo'llab-quvvatlash "
                "xizmatiga* murojaat qiling — [aloqa formasi]"
                "(https://gts.uz/support) orqali yoki quyidagi "
                "kontaktlar orqali:\n\n"
                "- Telefon: `+998 71 200 11 22`\n"
                "- Email: `info@gts.uz`\n"
                "- Telegram: `@gts_support`\n"
            ),
            "ru": (
                "# Политика конфиденциальности\n\n"
                "Этот документ описывает, как собираются, хранятся, "
                "обрабатываются и защищаются ваши персональные данные "
                "при пользовании сервисом. Используя сервис, вы "
                "соглашаетесь с этой политикой.\n\n"
                "> **Важно:** данные вашей карты (номер, CVV, срок "
                "действия) **никогда** не хранятся на наших серверах и "
                "не попадают в логи.\n\n"
                "## 1. Какие данные собираются\n\n"
                "При регистрации и оформлении заказа могут собираться "
                "следующие данные:\n\n"
                "- Имя и фамилия\n"
                "- Телефон и email\n"
                "- Дата рождения (если обязательна для билета)\n"
                "- Данные паспорта или другого документа (для "
                "международных рейсов)\n"
                "- История заказов и платежей\n"
                "- Тип устройства, IP-адрес и статистика использования\n"
                "- Настройки приложения: язык, валюта, уведомления\n\n"
                "### 1.1. Данные, собираемые автоматически\n\n"
                "Приложение и сайт автоматически фиксируют: "
                "длительность сессии, нажатые кнопки, журналы ошибок. "
                "Эти данные не используются для идентификации личности, "
                "только для улучшения качества сервиса.\n\n"
                "## 2. Как мы используем данные\n\n"
                "Ваши данные используются в следующих целях:\n\n"
                "1. Оформление и доставка заказа\n"
                "2. Проведение и подтверждение оплаты\n"
                "3. Связь с клиентом — напоминания, уведомления\n"
                "4. Улучшение сервиса и статистический анализ\n"
                "5. Выполнение требований закона (налоги, отчётность)\n\n"
                "Данные **никогда** не продаются третьим лицам. Ими "
                "делятся только с партнёрами, необходимыми для "
                "выполнения заказа (авиакомпания, отель, платёжный "
                "провайдер), и только в нужном объёме.\n\n"
                "## 3. Срок хранения данных\n\n"
                "| Тип данных | Срок хранения | Комментарий |\n"
                "|---|---|---|\n"
                "| История заказов | 5 лет | Для налоговой отчётности |\n"
                "| Контактные данные | До удаления аккаунта | В "
                "настройках профиля |\n"
                "| Данные карты | Не хранятся | На стороне платёжного "
                "провайдера |\n"
                "| Технические логи | 90 дней | Для анализа сбоев |\n\n"
                "## 4. Меры безопасности\n\n"
                "Ваши данные передаются по `TLS 1.2+` и хранятся в базе "
                "данных в зашифрованном виде. Доступ сотрудников "
                "ограничен по ролям — каждый доступ фиксируется в "
                "журнале аудита.\n\n"
                "```\n"
                "Запрос на оплату никогда не возвращает полный номер "
                "карты,\n"
                "показываются только последние 4 цифры: **** **** **** "
                "1234\n"
                "```\n\n"
                "## 5. Cookie и похожие технологии\n\n"
                "Веб-версия сайта использует cookie для хранения сессии "
                "и настроек. Их можно отключить в настройках браузера, "
                "но часть функций может перестать работать.\n\n"
                "## 6. Ваши права\n\n"
                "- Просматривать и редактировать свои данные\n"
                "- Запросить копию своих данных\n"
                "- Запросить удаление аккаунта и связанных данных\n"
                "- Отказаться от маркетинговых рассылок\n\n"
                "Чтобы воспользоваться этими правами, перейдите в "
                "профиль или обратитесь в службу поддержки.\n\n"
                "## 7. Конфиденциальность несовершеннолетних\n\n"
                "Сервис предназначен для лиц старше 18 лет. Если "
                "выяснится, что данные лица младше 18 лет были собраны "
                "непреднамеренно, они незамедлительно удаляются.\n\n"
                "## 8. Изменения политики\n\n"
                "Эта политика может периодически обновляться. О "
                "существенных изменениях мы сообщим по email или "
                "уведомлением в приложении.\n\n"
                "---\n\n"
                "## 9. Контакты\n\n"
                "По вопросам обращайтесь в *службу поддержки* — через "
                "[форму обратной связи](https://gts.uz/support) или по "
                "контактам ниже:\n\n"
                "- Телефон: `+998 71 200 11 22`\n"
                "- Email: `info@gts.uz`\n"
                "- Telegram: `@gts_support`\n"
            ),
            "en": (
                "# Privacy Policy\n\n"
                "This document explains how your personal data is "
                "collected, stored, processed and protected when you "
                "use the service. By using the service you agree to "
                "this policy.\n\n"
                "> **Important:** your card details (number, CVV, "
                "expiry) are **never** stored on our servers and never "
                "written to logs.\n\n"
                "## 1. What data we collect\n\n"
                "During registration and checkout the following data "
                "may be collected:\n\n"
                "- First and last name\n"
                "- Phone number and email address\n"
                "- Date of birth (when required for a ticket)\n"
                "- Passport or other document details (for "
                "international flights)\n"
                "- Order and payment history\n"
                "- Device type, IP address and usage statistics\n"
                "- App settings: language, currency, notification "
                "preferences\n\n"
                "### 1.1. Data collected automatically\n\n"
                "The app and site automatically record session length, "
                "taps and error logs. This data is not used to identify "
                "you personally — only to improve the quality of the "
                "service.\n\n"
                "## 2. How we use your data\n\n"
                "Your data is used for the following purposes:\n\n"
                "1. Processing and delivering your order\n"
                "2. Taking and confirming payment\n"
                "3. Communicating with you — reminders, notifications\n"
                "4. Improving the service and statistical analysis\n"
                "5. Meeting legal requirements (tax, reporting)\n\n"
                "Your data is **never** sold to third parties. It is "
                "only shared, to the extent necessary, with partners "
                "required to fulfil your order — an airline, a hotel, a "
                "payment provider.\n\n"
                "## 3. How long we keep it\n\n"
                "| Data type | Retention | Note |\n"
                "|---|---|---|\n"
                "| Order history | 5 years | For tax reporting |\n"
                "| Contact details | Until account deletion | In "
                "profile settings |\n"
                "| Card details | Never stored | Held by the payment "
                "provider |\n"
                "| Technical logs | 90 days | For debugging |\n\n"
                "## 4. Security measures\n\n"
                "Your data travels over `TLS 1.2+` and is stored "
                "encrypted at rest. Staff access is role-based and "
                "every access is written to an audit log.\n\n"
                "```\n"
                "A payment request never returns the full card number,\n"
                "only the last 4 digits are shown: **** **** **** 1234\n"
                "```\n\n"
                "## 5. Cookies and similar technologies\n\n"
                "The web build uses cookies to keep your session and "
                "preferences. You can disable them in your browser "
                "settings, though some features may then stop "
                "working.\n\n"
                "## 6. Your rights\n\n"
                "- View and edit your own data\n"
                "- Request a copy of your data\n"
                "- Request deletion of your account and its data\n"
                "- Opt out of marketing messages\n\n"
                "To exercise these rights, use your profile settings or "
                "contact support.\n\n"
                "## 7. Children's privacy\n\n"
                "The service is intended for people 18 and over. If we "
                "learn that data from someone under 18 was collected "
                "unintentionally, it is deleted immediately.\n\n"
                "## 8. Changes to this policy\n\n"
                "This policy may be updated from time to time. We will "
                "announce material changes by email or an in-app "
                "notification.\n\n"
                "---\n\n"
                "## 9. Contact\n\n"
                "Questions or requests? Reach out to *our support "
                "team* — via the [contact form]"
                "(https://gts.uz/support) or the details below:\n\n"
                "- Phone: `+998 71 200 11 22`\n"
                "- Email: `info@gts.uz`\n"
                "- Telegram: `@gts_support`\n"
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
                "to'liq rozilik bildirasiz. Iltimos, buyurtma "
                "berishdan oldin ularni diqqat bilan o'qib chiqing.\n\n"
                "> Ushbu shartlar [Maxfiylik siyosati]"
                "(/privacy-policy) bilan birgalikda o'qilishi kerak.\n\n"
                "## 1. Umumiy qoidalar\n\n"
                "1. Xizmatdan faqat 18 yoshga to'lgan shaxslar "
                "foydalanishi mumkin.\n"
                "2. Ro'yxatdan o'tishda kiritilgan ma'lumotlar **haqiqiy** "
                "bo'lishi shart.\n"
                "3. Bitta akkaunt bitta shaxsga tegishli bo'ladi.\n"
                "4. Xizmat *xohlagan vaqtda* funksionallikni "
                "o'zgartirish huquqini saqlab qoladi.\n\n"
                "## 2. Buyurtma va to'lov\n\n"
                "Buyurtma to'lov tasdiqlangandan so'ng kuchga kiradi. "
                "Narxlar buyurtma paytidagi holatda qat'iy "
                "belgilanadi va keyinchalik o'zgartirilmaydi.\n\n"
                "Qabul qilinadigan to'lov usullari:\n\n"
                "- Bank kartalari (Visa, Mastercard, UzCard, Humo)\n"
                "- Ilova ichidagi balans (mavjud bo'lsa)\n"
                "- Promo-kodlar va chegirmalar\n\n"
                "## 3. Bekor qilish va qaytarish\n\n"
                "Bekor qilish shartlari har bir mahsulot sahifasida "
                "ko'rsatiladi. Umumiy jadval:\n\n"
                "| Bekor qilish vaqti | Qaytariladigan summa |\n"
                "|---|---|\n"
                "| 24 soatdan oldin | 100% |\n"
                "| 24–12 soat oldin | 50% |\n"
                "| 12 soatdan kam | Qaytarilmaydi |\n\n"
                "Qaytarish ariza asosida ko'rib chiqiladi va odatda "
                "`3–5 ish kuni` ichida bank kartangizga qaytariladi.\n\n"
                "## 4. Foydalanuvchi majburiyatlari\n\n"
                "- To'g'ri va yangilangan ma'lumot kiritish\n"
                "- Akkaunt maxfiyligini (parol, kirish kodi) saqlash\n"
                "- Xizmatni noqonuniy maqsadlarda ishlatmaslik\n"
                "- Boshqa foydalanuvchilarga yoki xizmatga zarar "
                "yetkazadigan harakatlardan tiyilish\n\n"
                "## 5. Intellektual mulk\n\n"
                "Sayt va ilovadagi barcha matn, logotip, dizayn "
                "elementlari xizmatga tegishli va ruxsatsiz "
                "nusxalanishi yoki tarqatilishi mumkin emas.\n\n"
                "## 6. Mas'uliyat cheklovi\n\n"
                "Xizmat uchinchi tomon yetkazib beruvchilarining "
                "(aviakompaniya, mehmonxona) o'z ichki qoidalari "
                "tufayli yuzaga kelgan o'zgarishlar yoki bekor "
                "qilishlar uchun javobgar emas, lekin muammolarni hal "
                "qilishda mijozga yordam beradi.\n\n"
                "```\n"
                "Misol: reys kechiktirilsa yoki bekor qilinsa,\n"
                "aviakompaniyaning o'z siyosati amal qiladi.\n"
                "```\n\n"
                "## 7. Nizolarni hal qilish\n\n"
                "Kelishmovchiliklar avval muzokara yo'li bilan hal "
                "qilinishga harakat qilinadi. Kelishuvga erishilmasa, "
                "O'zbekiston Respublikasi qonunchiligiga muvofiq hal "
                "qilinadi.\n\n"
                "## 8. Shartlarga o'zgartirishlar\n\n"
                "Ushbu shartlar vaqti-vaqti bilan yangilanishi mumkin. "
                "Yangilangan versiya nashr etilgan kundan boshlab "
                "kuchga kiradi.\n\n"
                "---\n\n"
                "## 9. Bog'lanish\n\n"
                "Savollaringiz bo'lsa, [qo'llab-quvvatlash xizmatiga]"
                "(https://gts.uz/support) murojaat qiling:\n\n"
                "- Telefon: `+998 71 200 11 22`\n"
                "- Email: `info@gts.uz`\n"
            ),
            "ru": (
                "# Условия использования\n\n"
                "Пользуясь сервисом, вы полностью соглашаетесь со "
                "следующими условиями. Пожалуйста, внимательно "
                "прочитайте их перед оформлением заказа.\n\n"
                "> Эти условия следует читать вместе с [Политикой "
                "конфиденциальности](/privacy-policy).\n\n"
                "## 1. Общие положения\n\n"
                "1. Сервисом могут пользоваться только лица старше 18 "
                "лет.\n"
                "2. Данные, указанные при регистрации, должны быть "
                "**достоверными**.\n"
                "3. Один аккаунт принадлежит одному человеку.\n"
                "4. Сервис оставляет за собой право *в любое время* "
                "изменять функциональность.\n\n"
                "## 2. Заказ и оплата\n\n"
                "Заказ вступает в силу после подтверждения оплаты. "
                "Цены фиксируются на момент оформления заказа и в "
                "дальнейшем не меняются.\n\n"
                "Принимаемые способы оплаты:\n\n"
                "- Банковские карты (Visa, Mastercard, UzCard, Humo)\n"
                "- Баланс внутри приложения (если доступен)\n"
                "- Промокоды и скидки\n\n"
                "## 3. Отмена и возврат\n\n"
                "Условия отмены указаны на странице каждого продукта. "
                "Общая таблица:\n\n"
                "| Время отмены | Возвращаемая сумма |\n"
                "|---|---|\n"
                "| Более чем за 24 часа | 100% |\n"
                "| За 24–12 часов | 50% |\n"
                "| Менее чем за 12 часов | Не возвращается |\n\n"
                "Возврат рассматривается по заявлению и обычно "
                "поступает на карту в течение `3–5 рабочих дней`.\n\n"
                "## 4. Обязанности пользователя\n\n"
                "- Указывать точные и актуальные данные\n"
                "- Хранить в тайне данные аккаунта (пароль, код "
                "входа)\n"
                "- Не использовать сервис в незаконных целях\n"
                "- Не совершать действий, вредящих другим "
                "пользователям или сервису\n\n"
                "## 5. Интеллектуальная собственность\n\n"
                "Весь текст, логотипы и элементы дизайна на сайте и в "
                "приложении принадлежат сервису и не могут "
                "копироваться или распространяться без разрешения.\n\n"
                "## 6. Ограничение ответственности\n\n"
                "Сервис не отвечает за изменения или отмены, "
                "произошедшие по внутренним правилам сторонних "
                "поставщиков (авиакомпания, отель), но помогает "
                "клиенту в решении возникших проблем.\n\n"
                "```\n"
                "Пример: если рейс задерживается или отменяется,\n"
                "действуют правила самой авиакомпании.\n"
                "```\n\n"
                "## 7. Разрешение споров\n\n"
                "Разногласия сначала пытаются урегулировать путём "
                "переговоров. Если согласие не достигнуто, спор "
                "решается в соответствии с законодательством "
                "Республики Узбекистан.\n\n"
                "## 8. Изменения условий\n\n"
                "Эти условия могут периодически обновляться. Новая "
                "версия вступает в силу со дня публикации.\n\n"
                "---\n\n"
                "## 9. Контакты\n\n"
                "По вопросам обращайтесь в [службу поддержки]"
                "(https://gts.uz/support):\n\n"
                "- Телефон: `+998 71 200 11 22`\n"
                "- Email: `info@gts.uz`\n"
            ),
            "en": (
                "# Terms of Use\n\n"
                "By using the service you fully agree to the following "
                "terms. Please read them carefully before placing an "
                "order.\n\n"
                "> These terms should be read together with the "
                "[Privacy Policy](/privacy-policy).\n\n"
                "## 1. General provisions\n\n"
                "1. Only people aged 18 or over may use the service.\n"
                "2. The data provided at registration must be "
                "**accurate**.\n"
                "3. One account belongs to one person.\n"
                "4. The service reserves the right to change its "
                "functionality *at any time*.\n\n"
                "## 2. Orders and payment\n\n"
                "An order takes effect once payment is confirmed. "
                "Prices are fixed at the moment of ordering and do not "
                "change afterwards.\n\n"
                "Accepted payment methods:\n\n"
                "- Bank cards (Visa, Mastercard, UzCard, Humo)\n"
                "- In-app balance (where available)\n"
                "- Promo codes and discounts\n\n"
                "## 3. Cancellation and refunds\n\n"
                "Cancellation terms are shown on each product page. "
                "General table:\n\n"
                "| Cancelled | Amount refunded |\n"
                "|---|---|\n"
                "| More than 24 hours before | 100% |\n"
                "| 24–12 hours before | 50% |\n"
                "| Less than 12 hours before | Not refunded |\n\n"
                "Refunds are reviewed on request and usually reach your "
                "card within `3–5 business days`.\n\n"
                "## 4. User obligations\n\n"
                "- Provide accurate, up-to-date information\n"
                "- Keep account credentials (password, login code) "
                "confidential\n"
                "- Not use the service for unlawful purposes\n"
                "- Not act in a way that harms other users or the "
                "service\n\n"
                "## 5. Intellectual property\n\n"
                "All text, logos and design elements on the site and "
                "in the app belong to the service and may not be "
                "copied or distributed without permission.\n\n"
                "## 6. Limitation of liability\n\n"
                "The service is not responsible for changes or "
                "cancellations arising from a third-party supplier's "
                "own rules (an airline, a hotel), but will help the "
                "customer resolve any resulting issue.\n\n"
                "```\n"
                "Example: if a flight is delayed or cancelled,\n"
                "the airline's own policy applies.\n"
                "```\n\n"
                "## 7. Dispute resolution\n\n"
                "Disagreements are first addressed through "
                "negotiation. If no agreement is reached, the dispute "
                "is resolved under the laws of the Republic of "
                "Uzbekistan.\n\n"
                "## 8. Changes to these terms\n\n"
                "These terms may be updated from time to time. The "
                "updated version takes effect on the day it is "
                "published.\n\n"
                "---\n\n"
                "## 9. Contact\n\n"
                "Questions? Reach out to [support]"
                "(https://gts.uz/support):\n\n"
                "- Phone: `+998 71 200 11 22`\n"
                "- Email: `info@gts.uz`\n"
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


# --- fun facts (the flight search response's fun_fact field, API.md §20) ----------

FUN_FACTS: list[dict[str, str]] = [
    {
        "uz": "Boeing 747 taxminan 6 million detaldan yig'iladi.",
        "ru": "Boeing 747 собирается примерно из 6 миллионов деталей.",
        "en": "A Boeing 747 is assembled from about 6 million parts.",
    },
    {
        "uz": (
            "Har lahzada havoda o'rtacha bir millionga yaqin odam parvoz qilib yuradi."
        ),
        "ru": ("В каждый момент времени в воздухе находится около миллиона человек."),
        "en": ("At any given moment, roughly a million people are up in the air."),
    },
    {
        "uz": (
            "Dunyodagi eng qisqa muntazam reys Shotlandiyada — parvoz "
            "atigi 90 soniyacha davom etadi."
        ),
        "ru": (
            "Самый короткий регулярный рейс в мире — в Шотландии: полёт "
            "длится всего около 90 секунд."
        ),
        "en": (
            "The world's shortest scheduled flight is in Scotland — it "
            "lasts only about 90 seconds."
        ),
    },
    {
        "uz": (
            "Parvoz balandligida samolyot tashqarisidagi havo harorati "
            "taxminan −55 °C bo'ladi."
        ),
        "ru": ("На крейсерской высоте температура воздуха за бортом — около −55 °C."),
        "en": ("At cruising altitude, the air outside the plane is about −55 °C."),
    },
    {
        "uz": (
            "Uchuvchi va yordamchi uchuvchi odatda har xil taom yeydi — "
            "ikkalasi birdan zaharlanib qolmasligi uchun."
        ),
        "ru": (
            "Пилот и второй пилот обычно едят разные блюда — чтобы оба "
            "не отравились одновременно."
        ),
        "en": (
            "The pilot and co-pilot usually eat different meals, so "
            "they can't both get food poisoning at once."
        ),
    },
    {
        "uz": (
            '"Qora quti" aslida qora emas — topish oson bo\'lishi uchun '
            "to'q sariq rangga bo'yaladi."
        ),
        "ru": (
            "«Чёрный ящик» на самом деле не чёрный — его красят в "
            "ярко-оранжевый, чтобы легче найти."
        ),
        "en": (
            "The \"black box\" isn't black at all — it's painted bright "
            "orange so it's easier to find."
        ),
    },
    {
        "uz": (
            "Airbus A380 ichidagi elektr simlarining umumiy uzunligi "
            "500 kilometrdan oshadi."
        ),
        "ru": ("Общая длина электропроводки в Airbus A380 превышает 500 километров."),
        "en": ("The wiring inside an Airbus A380 runs to more than 500 kilometres."),
    },
    {
        "uz": ("Samolyot salonidagi havo har 2–3 daqiqada to'liq yangilanib turadi."),
        "ru": ("Воздух в салоне самолёта полностью обновляется каждые 2–3 минуты."),
        "en": ("The cabin air on a plane is completely refreshed every 2–3 minutes."),
    },
]


async def seed_fun_facts(session: AsyncSession) -> None:
    for spec in FUN_FACTS:
        text_uz = spec["uz"]
        if await exists_live(session, FunFact, FunFact.text["uz"].astext == text_uz):
            print(f"fun fact {text_uz!r}: already exists, skipped")
            continue
        created = await cms_service.create_fun_fact(session, FunFactIn(text=spec))
        await cms_service.set_fun_fact_status(
            session, created.id, ContentStatus.PUBLISHED
        )
        print(f"fun fact {text_uz!r}: created and published")


# --- site settings (company contacts for /public/content/about/) ----------------

SITE_SETTINGS = {
    "name": {
        "uz": "GTS Sayohat",
        "ru": "GTS Путешествия",
        "en": "GTS Travel",
    },
    "domain": "gts.uz",
    "support_phone": "+998712001122",
    "support_email": "info@gts.uz",
    "social": {
        "instagram": "https://instagram.com/gts.uz",
        "telegram": "https://t.me/gts_uz",
        "facebook": "https://facebook.com/gts.uz",
    },
}


# --- currencies --------------------------------------------------------------------

#: UZS stays default; USD/EUR/RUB are added so the demo has more than one
#: currency to switch between.
DEMO_CURRENCIES = ["UZS", "USD", "EUR", "RUB"]


async def seed_currencies(session: AsyncSession) -> None:
    current = await settings_service.get_currencies(session)
    # Skip once the list has grown past the untouched single-currency default:
    # an admin's real configuration must never be clobbered by a rerun.
    if len(current.available) > 1:
        print("currencies: already configured, skipped")
        return
    await settings_service.update_currencies(
        session, CurrenciesIn(available=DEMO_CURRENCIES)
    )
    print(f"currencies: {', '.join(DEMO_CURRENCIES)} seeded")


async def seed_site_settings(session: AsyncSession) -> None:
    # Skip once any field is set: an admin's real entry must never be
    # clobbered by a rerun, unlike the pages/FAQ above there's no per-field
    # matching key to check against, just the untouched-defaults state.
    current = await settings_service.get_site(session)
    if current.name or current.domain or current.support_email:
        print("site settings: already configured, skipped")
        return
    await settings_service.update_site(session, SiteIn(**SITE_SETTINGS))
    print("site settings: company contacts seeded")


# --- leads support contact (§25, §35) ---------------------------------------------

SUPPORT_CONTACT = {
    "support_username": "@gts_support",
    "support_phone": "+998712001133",
    "support_email": "support@gts.uz",
    "working_hours": {
        "uz": "Dush-Juma 09:00-18:00",
        "ru": "Пн-Пт 09:00-18:00",
        "en": "Mon-Fri 09:00-18:00",
    },
}


async def seed_support_contact(session: AsyncSession) -> None:
    # Same skip rule as site settings: no per-field matching key, just the
    # untouched-defaults state, so a real entry is never clobbered by a rerun.
    current = await leads_service.get_support_contact_admin(session)
    if current.support_username or current.support_phone or current.support_email:
        print("leads support contact: already configured, skipped")
        return
    await leads_service.update_support_contact(
        session, SupportContactIn(**SUPPORT_CONTACT)
    )
    print("leads support contact: seeded")


# --- order status messages (``/admin/orders/messages/``) ----------------------------

#: What the customer's screen says per public order ``status`` — the panel's
#: wording over ``orders.messages.DEFAULTS``. Written the way an operator
#: would: concrete, with the support line typed into the sentence itself
#: (the message is shown verbatim, nothing is interpolated).
ORDER_MESSAGES: dict[Stage, dict[str, str]] = {
    Stage.BOOKED: {
        "uz": "Joyingiz band qilindi. Chipta chiqarilishi uchun to'lovni "
        "ko'rsatilgan muddatgacha amalga oshiring — muddat o'tsa bron "
        "avtomatik bekor bo'ladi.",
        "ru": "Место забронировано. Оплатите заказ до указанного срока, чтобы "
        "билет был выписан — по истечении срока бронь снимается автоматически.",
        "en": "Your seat is held. Pay before the deadline shown to have the "
        "ticket issued — once it passes, the booking is released automatically.",
    },
    Stage.TICKET_WAITING: {
        "uz": "To'lovingiz qabul qilindi, rahmat! Chipta hozir "
        "rasmiylashtirilmoqda — odatda bu 1-2 daqiqa oladi. Tayyor bo'lishi "
        "bilan SMS va ilovada xabar beramiz.",
        "ru": "Оплата получена, спасибо! Билет оформляется — обычно это занимает "
        "1-2 минуты. Как только он будет готов, мы сообщим по SMS и в приложении.",
        "en": "Payment received, thank you! Your ticket is being issued — this "
        "usually takes 1-2 minutes. We will notify you by SMS and in the app "
        "as soon as it is ready.",
    },
    Stage.TICKETED: {
        "uz": 'Chiptalaringiz tayyor! Ularni "Chiptalar" bo\'limidan yuklab '
        "olishingiz yoki elektron pochtangizdan topishingiz mumkin. Oq yo'l!",
        "ru": "Ваши билеты готовы! Скачайте их в разделе «Билеты» или найдите в "
        "электронной почте. Счастливого пути!",
        "en": "Your tickets are ready! Download them from the Tickets section or "
        "find them in your email. Have a great trip!",
    },
    Stage.TICKETING_FAILED: {
        "uz": "To'lov qabul qilindi, ammo aviakompaniya chiptani tasdiqlamadi. "
        "Xavotir olmang — mablag' 3-5 ish kuni ichida kartangizga qaytariladi. "
        "Savollar bo'lsa: +998 71 200 11 33 yoki @gts_support.",
        "ru": "Оплата прошла, но авиакомпания не подтвердила билет. Не "
        "волнуйтесь — средства вернутся на карту в течение 3-5 рабочих дней. "
        "Вопросы: +998 71 200 11 33 или @gts_support.",
        "en": "Your payment went through, but the airline did not confirm the "
        "ticket. Don't worry — the money will be back on your card within 3-5 "
        "business days. Questions: +998 71 200 11 33 or @gts_support.",
    },
    Stage.REFUNDED: {
        "uz": "Mablag' kartangizga qaytarildi. Bank tomonidan aks etishi 3-5 ish "
        "kunigacha vaqt olishi mumkin.",
        "ru": "Средства возвращены на вашу карту. Зачисление банком может занять "
        "до 3-5 рабочих дней.",
        "en": "Your refund has been sent to your card. It may take your bank up "
        "to 3-5 business days to show it.",
    },
    Stage.CANCELLED: {
        "uz": "Buyurtma bekor qilindi, hech qanday to'lov olinmadi. Yangi "
        "qidiruv qilib, qayta bron qilishingiz mumkin.",
        "ru": "Заказ отменён, оплата не взималась. Вы можете выполнить новый "
        "поиск и забронировать заново.",
        "en": "The order was cancelled and nothing was charged. Search again to "
        "make a new booking.",
    },
}


async def seed_order_messages(session: AsyncSession) -> None:
    # Same skip rule as site settings, per status: a sentence staff already
    # rewrote in any language is theirs, and a rerun must not clobber it.
    existing = {row.status: row for row in await orders_service.list_messages(session)}
    for status, text in ORDER_MESSAGES.items():
        if existing[status].custom:
            print(f"order message {status.value!r}: already customised, skipped")
            continue
        await orders_service.update_message(session, status, OrderMessageIn(text=text))
        print(f"order message {status.value!r}: seeded")


# --- deletion reasons -------------------------------------------------------------

DELETION_REASONS: list[dict] = [
    {
        "uz": "Endi xizmatdan foydalanmayman",
        "ru": "Больше не пользуюсь сервисом",
        "en": "I no longer use the service",
    },
    {
        "uz": "Maxfiylik bilan bog'liq tashvishlar",
        "ru": "Опасения по поводу конфиденциальности",
        "en": "Privacy concerns",
    },
    {
        "uz": "Boshqa xizmatdan foydalanaman",
        "ru": "Пользуюсь другим сервисом",
        "en": "I use a different service",
    },
    {
        "uz": "Xizmat sifatidan norozi bo'ldim",
        "ru": "Не устроило качество сервиса",
        "en": "Unsatisfied with the service quality",
    },
    {
        "uz": "Boshqa sabab",
        "ru": "Другая причина",
        "en": "Other reason",
    },
]


async def seed_deletion_reasons(session: AsyncSession) -> None:
    for spec in DELETION_REASONS:
        question_uz = spec["uz"]
        if await exists_live(
            session, DeletionReason, DeletionReason.text["uz"].astext == question_uz
        ):
            print(f"deletion reason {question_uz!r}: already exists, skipped")
            continue
        await customers_service.create_deletion_reason(
            session, DeletionReasonIn(text=spec)
        )
        print(f"deletion reason {question_uz!r}: created")


async def seed_pages(session: AsyncSession) -> None:
    for spec in PAGES:
        slug = spec["slug"]
        # Upsert rather than skip: this is demo content, not an admin's own
        # edit, so a rerun is meant to bring it up to date with PAGES below —
        # ``upsert_fixed_page`` merges per language, so every seeded language
        # here fully replaces what it held.
        existed = await exists_live(session, PageModel, PageModel.slug == slug)
        await cms_service.upsert_fixed_page(
            session, slug, FixedPageIn(title=spec["title"], body=spec["body"])
        )
        await cms_service.set_fixed_page_status(session, slug, ContentStatus.PUBLISHED)
        print(f"page {slug!r}: {'updated' if existed else 'created'} and published")


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
        await seed_fun_facts(session)
        await seed_deletion_reasons(session)
        await seed_site_settings(session)
        await seed_support_contact(session)
        await seed_currencies(session)
        await seed_order_messages(session)
    await dispose_engine()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:  # noqa: BLE001 — a CLI entry point reports and exits
        print(f"seed failed: {error}", file=sys.stderr)
        sys.exit(1)
