# Order tizimi — buyurtma, to'lov, ticketing

Bu hujjat buyurtma (order) hayotiy sikli bo'yicha **manba**. Kod unga
ergashadi; farq topilsa avval shu fayl tuzatiladi, keyin kod.

Bosqichlar: **1 — lifecycle**, **2 — to'lov** (port + sandbox provider,
`payment_attempts`, sweep), **3 — GTS ticketing** — joriy; 4 — admin/support.
Keyingi bosqichga tegishli qismlar shunday belgilangan.

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
(GTS'dan chipta so'ralgan vaqt), `gts_checked_at` (sweep oxirgi marta
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

## 4a. To'lov — `payment_attempts` va ikki qadam

Provider bilan har suhbat — `payment_attempts` da bir qator: `order_id,
customer_id, provider, status` (`started` · `confirming` · `paid` · `failed` ·
`abandoned`)`, amount, currency, card_id, card_last4, provider_reference`
(**shifrlangan** — Payme'da bu kartadan yana yechadigan token)`, phone_hint,
provider_data, error, paid_at`.

**Strukturaviy kafolat:** partial unique index `uq_payment_attempts_open` —
bir order uchun `started | confirming | paid` holatida **bitta** qator. Ikki
parallel start indeksda to'qnashadi, ikki marta pul yechish mumkin emas —
kod ustida nima bo'lishidan qat'i nazar.

Qadamlar (`orders/service.py`):

1. **`POST /public/orders/{id}/payment/`** — body `{card_id}` yoki
   `{card: {number, expire}}` (aynan bittasi). Tartib: provider va GTS client
   **qulfdan oldin** olinadi (ular sessiyani commit qiladi); `GET
   /v1/orders/{n}/` — bron tirikmi, narx qancha (GTS narxi bizning narxdan
   ustun); GTS `CB/VO/STATUS_VOID` desa → `cancelled/expired` + **409
   `offer_expired`**. Qulf ostida: tekshiruvlar, eski `started` urinish →
   `abandoned`, yangi `started` qatori (**claim**, provider'dan oldin),
   commit. So'ng `provider.start()` → reference (shifrlab) va `phone_hint`
   yoziladi. Provider rad etsa (`PaymentDeclined`) → urinish `failed`,
   `payment_status=failed`, javob 200 (`payment.status=failed`, `error`);
   provider javob bermasa → xuddi shu + 502/504 (hali pul yechilmagan).
2. **`POST /public/orders/{id}/payment/confirm/`** — body `{payment_id, otp}`.
   Qulf ostida: urinish ochiq va `payment_id` mos; `confirming` bo'lsa —
   provider chaqirilmaydi, joriy holat qaytadi (o'qish); muddat o'tgan bo'lsa
   → `abandoned` + `cancelled/expired` + 409. Urinish `confirming`, **commit**
   — charge provider'ga hech qachon ikki marta ketmaydi. So'ng
   `provider.confirm()` qulfsiz; natija `settle_attempt` bilan **qayta qulf +
   qayta o'qish** ostida qo'llanadi (sweep bilan poyga): `paid` → urinish
   `paid`, `payment=paid`, karta `last_used_at`; `failed` → `payment=failed`,
   javob 200; exception (javob noma'lum) → `confirming` qoladi, javob 200
   `stage=payment_processing`.

Provider tanlovi (`payments.service.payment_provider`): test override →
panelda yoqilgan provider adapteri (Payme/Click kelgunicha "bu relizda
yo'q" → 502) → `DEBUG=true` va hech biri yoqilmagan → **sandbox** → aks
holda 502. Sandbox kodlari: `000000` paid · `111111` declined · `222222`
timeout (noma'lum) · `333333` pending · boshqasi — noto'g'ri kod.

Sweep (`app/tasks/orders.py::reconcile_orders`, beat 30 s, har qator o'z
tranzaksiyasida, `SKIP LOCKED`):

- `confirming` va 120 s dan eski → `provider.status()`; `paid/failed` →
  qo'llanadi; `pending` → kutiladi; 15 daqiqadan keyin ham `pending` →
  `failed` "the provider never confirmed this charge" + `ERROR
  payment_unconfirmed` (support provider panelini tekshiradi).
- To'lanmagan, muddati 10 daqiqadan ko'p o'tgan (yoki muddatsiz va 24 soatdan
  eski) orderlar → `GET /v1/orders/{n}/`: GTS `CB/VO/STATUS_VOID` →
  `cancelled/expired`; hali `BO` → muddat yangilanadi, `gts_checked_at` 10
  daqiqa throttle; `confirming` urinish bor → o'tkazib yuboriladi; 10
  daqiqadan eski `started` → `abandoned`.

## 4b. Ticketing — bitta POST, o'qish bilan yakunlanadi

To'lov `paid` bo'lgan zahoti (o'sha so'rov ichida) `orders/service.py::ticket()`
chaqiriladi — ticketing POST **faqat shu yerdan** ketadi:

1. Qulf ostida `ticketing=processing`, `ticketing_attempts += 1`,
   `ticketing_requested_at = now`, event `ticketing.requested`; **commit**.
   Shundan keyin nima bo'lmasin (crash, timeout) so'rov tasodifan qayta
   yuborilmaydi — sweep holatni GET bilan aniqlaydi.
2. `POST /v1/content/ticketing/ {"order_number": n, "payment_method": "deposit"}`
   (GTS **bizning depozit**dan yechadi). Javobdagi order `order` → `data` →
   flat tartibida o'qiladi.
3. GTS rad etsa (`status: "error"`) — xulosadan oldin **`GET /v1/orders/{n}/`**:
   qayta yuborilgan so'rovni "already ticketed" deb rad etishi ticketed
   orderni failed qilmasligi kerak. Timeout → `processing` qoladi.
4. Natija qulf ostida, faqat order hali `processing` bo'lsa qo'llanadi
   (`_apply_ticketing`). `paid` commit bo'lgach handler hech qachon raise
   qilmaydi — ticketing xatosi log'ga, order `paid/pending` da qoladi, sweep
   ko'taradi.

Qaror jadvali (`_decide`) — POST javobi va har GET read-back uchun bir xil:

| GTS `status` | Qaror |
|---|---|
| `TI` | `ticketed` (snapshot yangilanadi, chipta raqamlari `order_data` va `ticketing.tickets` da) |
| `CB` · `VO` · `STATUS_VOID` · `TE` | `failed` — sabab: GTS matni yoki `GTS status X` |
| `PW` | kutish; `now - ticketing_requested_at > 30 min` → `failed` |
| POST rad etilgan (status `TI`/`PW` emas) | `failed`, GTS matni; `"enough credits"` → `ERROR gts_deposit_empty` (bizning balans — support to'ldirib retry qiladi) |
| `BO` / `STATUS_BOOK` | `< 5 min` → kutish (javob yo'lda bo'lishi mumkin); `ticketing_attempts < 2` va muddat o'tmagan → **bir marta qayta yuboriladi**; aks holda `failed` "not confirmed by GTS" |
| noma'lum / o'qib bo'lmadi | kutish; 30 min → `failed` |

Konstantalar: `TICKETING_MAX_WAIT = 30 min`, `TICKETING_POST_GRACE = 5 min`,
`TICKETING_MAX_SENDS = 2` (1 qilinsa avtomatik qayta yuborish yo'q) — GTS
xususiyati, klient sozlamasi emas. Staff retry (4-bosqich) chegaraga
bo'ysunmaydi.

`ticketing_failed` **hech qachon avtomatik `cancelled` bo'lmaydi**: bron va pul
joyida, refund — support ishi (`stage=ticketing_failed`, xabar support
kontakt bilan).

Sweep qismlari (30 s): `ticket_paid_pending` (to'langan, lekin chipta
so'ralmagan — crash xavfsizlik to'ri) → `recheck_processing` (har `processing`
order `GET` bilan, `gts_checked_at` tartibida, 20 tadan) → to'lov va muddat
qismlari (4a).

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
| POST | `/public/orders/{id}/payment/` | customer | 2 — kodni yuboradi; 200, `payment.status=awaiting_otp`, `payment_id`, `phone_hint` |
| POST | `/public/orders/{id}/payment/confirm/` | customer | 2/3 — `{payment_id, otp}`; 200; `paid` bo'lsa o'sha so'rovda ticketing: `ticketing.status` `ticketed` · `processing` · `failed` |
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
  Celery beat sweep (30 s) `GET /v1/orders/{n}/` bilan yakunlaydi.
- Idempotency (Redis) — qulaylik qatlami; qulf va commit qilingan holat
  yolg'iz ushlab turishi shart.
