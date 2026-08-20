# Order tizimi — buyurtma, to'lov, ticketing

Bu hujjat buyurtma (order) hayotiy sikli bo'yicha **manba**. Kod unga
ergashadi; farq topilsa avval shu fayl tuzatiladi, keyin kod.

Bosqichlar: **1 — lifecycle** (shu hujjat tasvirlagan model, joriy),
2 — to'lov (port + sandbox provider), 3 — GTS ticketing va sweep,
4 — admin/support. Keyingi bosqichlarga tegishli qismlar shunday belgilangan.

## 1. Uchta lifecycle, bitta qator

Bitta `status` ustuniga hamma holatni tiqish o'rniga order uchta mustaqil
hikoyani alohida ustunda saqlaydi:

| Ustun | Qiymatlar | Ma'nosi |
|---|---|---|
| `status` | `booked` · `cancelled` | Bron tirikmi? |
| `payment_status` | `pending` · `paid` · `failed` · `refunding` · `refunded` · `refund_failed` | Mijozning puli qayerda? |
| `ticketing_status` | `pending` · `processing` · `ticketed` · `failed` | GTS chiptani chiqardimi? |

Misollar:

```
status=booked  payment=paid  ticketing=processing   → bron bor, pul yechilgan, chipta chiqarilmoqda
status=booked  payment=paid  ticketing=failed       → bron bor, pul yechilgan, chipta chiqmadi (support, refund)
status=cancelled payment=pending                     → to'lanmasdan bekor bo'ldi (cancel_reason aytadi nega)
```

Yordamchi ustunlar: `cancel_reason` (`customer` · `expired` · `staff`),
`paid_at`, `ticketed_at`, `cancelled_at`, `ticketing_requested_at`
(GTS'dan chipta so'ralgan vaqt), `ticketing_checked_at` (sweep oxirgi marta
GTS'dan o'qigan vaqt), `ticketing_attempts` (so'rov necha marta yuborilgan),
`ticketing_error` (GTS'ning xato matni). `gts_status` va `gts_response` —
GTS'ning o'z kodi va to'liq javobi, har o'qishda yangilanadi.

`failed` (payment) "oxirgi urinish muvaffaqiyatsiz" degani — yangi urinish
ochilishi mumkin. Refund holatlarini **support qo'lda** belgilaydi; pul
provider kabineti orqali qaytariladi (4-bosqich).

## 2. `stage` — ekran uchun bitta yorliq

Uchta ustundan **serverda** hisoblanadi, saqlanmaydi (`lifecycle.stage_of`).
Har bir klient bir xil o'qisin deb. Har `stage` ga mos `message` (uz/ru/en,
so'rov tilidan — `?lang=` yoki `Accept-Language`) `orders/messages.py` da.

| `stage` | Qachon |
|---|---|
| `awaiting_payment` | booked, to'lov `pending` (yoki `failed` + ochiq urinish bor) |
| `payment_processing` | ochiq urinish `confirming` — provider javobi kutilmoqda |
| `payment_failed` | to'lov `failed`, ochiq urinish yo'q |
| `ticketing` | `paid`, ticketing `pending` yoki `processing` |
| `ticketed` | `paid`, ticketing `ticketed` |
| `ticketing_failed` | `paid`, ticketing `failed` — "pul yechildi, supportga murojaat qiling" |
| `cancelled` / `expired` | `cancelled`, to'lanmagan (`expired` — GTS muddati o'tgan) |
| `refund_due` | pul olingan, lekin order bekor yoki `refund_failed` — kimdir qaytarishi kerak |
| `refunding` / `refunded` | refund jarayonda / yakunlangan |

`ticketing_failed` va `refund_due` matnlariga `Site` sozlamalaridagi
`support_phone` / `support_email` qo'shiladi.

## 3. O'tishlar (`orders/lifecycle.py`)

Har status o'zgarishi **faqat** `transition()` orqali. U jadvalga kirmagan
yoki guard rad etgan o'tishni `409 conflict` bilan qaytaradi, ruxsat
etilganini qo'llaydi va har o'zgarish uchun `order_events` qatori beradi —
chaqiruvchi uni **o'sha tranzaksiyada** commit qiladi.

```
status:    booked → cancelled
payment:   pending → paid | failed ;  failed → paid ;  paid → refunding | refunded ;
           refunding → refunded | refund_failed ;  refund_failed → refunding | refunded
ticketing: pending → processing ;  processing → ticketed | failed ;
           failed → processing (staff retry) | ticketed (GTS kech chiqargan)
```

Guard'lar (natijaviy order ustida tekshiriladi; bir chaqiruvda
`payment=paid, ticketing=processing` bo'lishi mumkin):

- `→ cancelled`: ticketing `processing`/`ticketed` emas; ochiq urinish
  `confirming` emas; mijoz faqat to'lanmagan orderni bekor qila oladi,
  to'langanini — faqat staff.
- `payment → paid | failed`: **har doim ruxsat** — pul fakti rad etilmaydi
  (bekor qilingan orderda ham yoziladi; keyin `stage = refund_due`).
- `payment → refunding | refunded | refund_failed`: faqat staff; ticketing
  `ticketed` bo'lmasa.
- `ticketing → processing | ticketed`: `payment = paid` va `status = booked`.

Kim (`order_events.actor`): `customer`, `system` (sweep), `staff:<uuid>`.

## 4. Tarix — `order_events`

`order_id, created_at, event` (`payment.paid`, `ticketing.failed`,
`order.created`…)`, from_value, to_value, actor, note, data, request_id`.
`data` — faqat kod va id'lar (`gts_order_number`, urinish id); xom GTS javobi
`orders.gts_response` da, karta haqida hech narsa hech qayerda.

## 5. API

Javob shakli hamma joyda bir xil — `BookingResultOut`:

```jsonc
{
  "product": "flight",
  "order": {
    "id": "…", "status": "booked", "payment_status": "pending", "ticketing_status": "pending",
    "stage": "awaiting_payment", "message": "Bron qilindi. …", "cancel_reason": null,
    "gts_status": "BO", "gts_order_number": 61453, "pnr": "UBPLKW", "amount": {"amount": "287500.00", "currency": "UZS"},
    "ticket_time_limit_at": "…", "paid_at": null, "ticketed_at": null, "cancelled_at": null, "...": "…"
  },
  "payment":   { "status": "pending", "amount": {…}, "pay_before": "…",
                 "payment_id": null, "provider": null, "card_last4": null, "phone_hint": null, "paid_at": null, "error": null },
  "ticketing": { "status": "pending", "requested_at": null, "ticketed_at": null, "tickets": [], "error": null },
  "order_data": { "…GTS javobi, commission maydonlarisiz…" }
}
```

`payment.status` — `payment_status` + urinishdan o'qiladigan ikki aniqlik:
`awaiting_otp` (kod kiritilmoqda), `processing` (provider javobi noma'lum);
to'lanmasdan bekor bo'lgan order uchun `cancelled`.

| Method | Path | Kim | Bosqich |
|---|---|---|---|
| POST | `/public/{product}/booking/` | customer | 1 — **idempotent**: bir xil so'rov ikkinchi marta o'sha orderni **hozirgi holatida** qaytaradi; GTS xatosi claim'ni bo'shatadi, GTS timeout — **bo'shatmaydi** (60 s) |
| GET | `/public/orders/` | customer | 1 — ro'yxat: `status`, `payment_status`, `ticketing_status`, `stage`, `routes`, yo'lovchi ismlari |
| GET | `/public/orders/{id}/` | customer | 1 — **yozmaydi**; "chipta tayyormi?" ekrani shuni poll qiladi |
| POST | `/public/orders/{id}/payment/` | customer | 2 — OTP yuboradi, 201 |
| POST | `/public/orders/{id}/payment/confirm/` | customer | 2 — pul yechadi, so'ng ticketing (3) |
| GET/POST | `/admin/orders/…` | staff | 4 — ro'yxat, detal, `refund/`, `ticketing/retry/` |

Xatolar faqat katalogdan: `conflict` (noto'g'ri o'tish), `offer_expired`
(GTS broni muddati o'tgan), `upstream_error` / `upstream_timeout`,
`not_found` (begona order), `validation`.

## 6. Qoidalar (keyingi bosqichlar uchun ham)

- Har yozuv: `lock → qayta o'qish → tekshirish → o'zgartirish → commit`.
  Tarmoq chaqiruvi qulfdan oldin yoki ikki qulf orasida; faqat 15 s bilan
  chegaralangan GTS GET qulf ostida bo'lishi mumkin.
- GTS'ga POST (booking, ticketing) **ko'r-ko'rona qayta yuborilmaydi**;
  natija noma'lum bo'lsa GET bilan o'qiladi.
- GTS bizga hech qachon qo'ng'iroq qilmaydi — `processing` holatlarni
  Celery beat sweep (30 s) `GET /v1/orders/{n}/` bilan yakunlaydi (3-bosqich).
- Idempotency (Redis) — qulaylik qatlami; qulf va commit qilingan holat
  yolg'iz ushlab turishi shart.
