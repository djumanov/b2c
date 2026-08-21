# Order tizimi — buyurtma, to'lov, ticketing

Bu hujjat buyurtma (order) hayotiy sikli bo'yicha **manba**. Kod unga
ergashadi; farq topilsa avval shu fayl tuzatiladi, keyin kod.

To'rt bosqichda qurilgan: **1 — lifecycle**, **2 — to'lov** (port + sandbox
provider, `payment_attempts`, sweep), **3 — GTS ticketing**, **4 — support**
(`/admin/orders/`). Hammasi joriy. Keyingi ishlar — §7.

## 1. Uchta lifecycle, bitta qator

Bitta `status` ustuniga hamma holatni tiqish o'rniga order uchta mustaqil
hikoyani alohida ustunda saqlaydi:

| Ustun | Qiymatlar | Ma'nosi |
|---|---|---|
| `status` | `booked` · `cancelled` | Bron tirikmi? |
| `payment_status` | `pending` · `paid` · `failed` · `refunding` · `refunded` · `refund_failed` | Mijozning puli qayerda? |
| `ticketing_status` | `pending` · `processing` · `ticketed` · `failed` | GTS chiptani chiqardimi? |

Bu uchta ustun **ichki** — mijoz API'sida ko'rinmaydi. Mijoz order ustida
bitta `status` ko'radi (§2), u shu uchtadan hisoblanadi. Admin API'da
uchtasi `booking_status` · `payment_status` · `ticketing_status` nomi bilan
chiqadi (DB ustuni `status` bilan adashmasin deb).

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

## 2. `status` — mijoz ko'radigan bitta holat

Mijoz API'sida (`/public/orders/`, `/{id}/`, booking va to'lov javoblari)
order ustida **bitta** `status` bor. U uchta ustundan **serverda**
hisoblanadi, saqlanmaydi (`lifecycle.stage_of`) — har bir klient bir xil
o'qisin deb. Oltita qiymat, chunki ekranga shundan ortig'i kerak emas:

| `status` | DB holati | Ekranda |
|---|---|---|
| `booked` | `booked`, to'lov `pending` yoki `failed` | to'lash kerak; urinish holati `payment.status` da |
| `ticket_waiting` | `booked` + `paid`, ticketing `pending` yoki `processing` | to'landi, GTS javobi kutilmoqda |
| `ticketed` | `booked` + `paid` + `ticketed` | chiptalar tayyor |
| `ticketing_failed` | pul olingan, chipta o'z-o'zidan chiqmaydi: `paid` + ticketing `failed`; yoki `cancelled` + `paid` (staff bekor qilgan); yoki `refunding` / `refund_failed` | supportga murojaat — nima bo'lishini `message` aytadi |
| `refunded` | `payment_status = refunded` | pul qaytarildi — yakuniy |
| `cancelled` | `cancelled`, pul olinmagan (`cancel_reason`: `customer` · `expired` · `staff`) | bekor; qayta qidirish |

Qoidalar:

- **To'lov urinishi order statusini o'zgartirmaydi.** Kod yuborilgani
  (`awaiting_otp`), provider javobi kutilayotgani (`processing`), karta rad
  etilgani (`failed`, `payment.error`) — hammasi `payment` blokida. Order
  `booked` bo'lib qolaveradi; to'lov ekrani `payment.status` ga qaraydi.
- **`refunded` alohida**, chunki `message` status bo'yicha beriladi: pul
  qaytgandan keyin mijoz "supportga murojaat qiling" deb o'qimasligi kerak.
  `refunding` esa hali jarayon — `ticketing_failed` bilan birga, "bog'laning".
- **`expired` alohida status emas** — `cancelled` + `cancel_reason = expired`.
- Ro'yxat va detail **bir xil** `status` ko'rsatadi.
- Frontend noma'lum qiymatni umumiy holat sifatida ko'rsatsin (`default:`
  tarmog'i) — keyingi relizlar qiymat qo'shishi mumkin.

Har `status` ga mos `message` — **admin panel matni**
(`/admin/orders/messages/`, uz/ru/en, har qanday staff tahrirlaydi); admin
yozmagan til/status uchun `orders/messages.py::DEFAULTS` ko'rsatiladi. Matn
**aynan yozilganidek** chiqadi — placeholder yo'q; support kontakti kerak
bo'lsa matnning o'ziga yoziladi. `ticketing_failed` default matni hech narsa
va'da qilmaydi ("to'lov qabul qilindi, chipta chiqarilmadi, supportga
murojaat qiling") — refund yoki qayta chiqarish haqida admin yozadi. Til —
so'rovniki (`?lang=` yoki `Accept-Language`), fallback zanjiri — sayt
sozlamasidagi `languages.default/available`.

Saqlash: `order_messages` (`key` = status, `text` JSONB — faqat admin yozgan
tillar; `{}` = hammasi default). Qatorlar **o'qishda** enum bilan
tenglashtiriladi: yangi status o'z qatorini oladi, olib tashlangan status
qatori (matni bilan) o'chiriladi — migratsiya kerak emas. PATCH tillar
bo'yicha merge qiladi; bo'sh satr o'sha tilni default'ga qaytaradi.

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
  (bekor qilingan orderda ham yoziladi; mijoz `status = ticketing_failed`
  ko'radi, admin `attention` inbox'ida chiqadi).
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
   `payment.status=processing` (order `status` esa `booked` bo'lib
   qolaveradi — §2).

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
joyida, refund — support ishi (mijoz `status=ticketing_failed` va admin
yozgan `message` ni ko'radi).

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
    "id": "…", "status": "booked", "message": "Bron qilindi. …", "cancel_reason": null,
    "gts_status": "BO", "gts_order_number": 61453, "pnr": "UBPLKW", "amount": {"amount": "287500.00", "currency": "UZS"},
    "ticket_time_limit_at": "…", "paid_at": null, "ticketed_at": null, "cancelled_at": null, "...": "…"
  },
  "payment":   { "status": "pending", "amount": {…}, "pay_before": "…",
                 "payment_id": null, "provider": null, "card_last4": null, "phone_hint": null, "paid_at": null, "error": null },
  "ticketing": { "status": "pending", "requested_at": null, "ticketed_at": null, "tickets": [], "error": null },
  "order_data": { "…GTS javobi, commission maydonlarisiz…" }
}
```

`order.status` — §2 dagi oltita qiymat; `payment_status`, `ticketing_status`
va DB `status` ustuni mijoz javobida **yo'q**. To'lov ekrani
`payment.status` ga qaraydi — bu `payment_status` + urinishdan o'qiladigan
ikki aniqlik: `awaiting_otp` (kod kiritilmoqda), `processing` (provider
javobi noma'lum); `failed` bo'lsa sabab `payment.error` da; to'lanmasdan
bekor bo'lgan order uchun `cancelled`. Admin javobida `order` bloki
qo'shimcha `booking_status`, `payment_status`, `ticketing_status` olib yuradi.

| Method | Path | Kim | Bosqich |
|---|---|---|---|
| POST | `/public/{product}/booking/` | customer | 1 — **idempotent**: bir xil so'rov ikkinchi marta o'sha orderni **hozirgi holatida** qaytaradi; GTS xatosi claim'ni bo'shatadi, GTS timeout — **bo'shatmaydi** (60 s) |
| GET | `/public/orders/` | customer | 1 — ro'yxat: `status` (§2, detail bilan bir xil), `routes`, yo'lovchi ismlari |
| GET | `/public/orders/{id}/` | customer | 1 — **yozmaydi**; "chipta tayyormi?" ekrani shuni poll qiladi |
| POST | `/public/orders/{id}/payment/` | customer | 2 — kodni yuboradi; 200, `payment.status=awaiting_otp`, `payment_id`, `phone_hint` |
| POST | `/public/orders/{id}/payment/confirm/` | customer | 2/3 — `{payment_id, otp}`; 200; `paid` bo'lsa o'sha so'rovda ticketing: `ticketing.status` `ticketed` · `processing` · `failed` |
| GET | `/admin/orders/` | staff | qatorlar mijoz `status` + xom `booking_status`, `payment_status`, `ticketing_status`; filtrlar shu uchta xom ustun bo'yicha; `attention=true` — support inbox (ticketing failed · refund_failed · bekor qilingan, lekin to'langan); `search` — PNR yoki GTS raqami |
| GET | `/admin/orders/{id}/` | staff | mijoz ko'rinishi (`order` da xom uchta ustun ham) + `customer_id`, `ticketing_attempts`, `events[]` (tarix), `payments[]` (urinishlar, reference'siz) |
| POST | `/admin/orders/{id}/refund/` | staff | `{status: refunding \| refunded \| refund_failed, note}` — pul provider kabinetida qaytariladi, bu yozuv; `ticketed` orderga — 409 |
| POST | `/admin/orders/{id}/sync/` | staff | GTS (va `confirming` urinish bo'lsa provider) bilan hozir solishtirish: yo'qolgan to'lov javobi, kech chiqqan chipta (`failed → ticketed`, faqat `paid`+`booked`), GTS qo'yib yuborgan bron |
| GET / PATCH | `/admin/orders/messages/` · `/{status}/` | staff | mijoz xabarlari, §2 dagi oltita status uchun: `{status, default, custom, text}`; PATCH `{text: {uz, ru, en}}` — merge, `""` → default; noma'lum til tashlanadi; noma'lum status — 422; 1000 belgi |
| POST | `/admin/orders/{id}/ticketing/retry/` | staff | avval sync; GTS allaqachon `TI` desa — POST yo'q; `paid`+`booked` bo'lmasa yoki GTS bronni qo'yib yuborgan bo'lsa — 409; aks holda `ticket()` (staff sweep chegarasiga bo'ysunmaydi) |

Xatolar faqat katalogdan: `conflict` (noto'g'ri o'tish), `offer_expired`
(GTS broni muddati o'tgan), `upstream_error` / `upstream_timeout`,
`not_found` (begona order), `validation`.

Har admin amali `order_events` ga `staff:<uuid>` bilan yoziladi; audit
middleware HTTP chaqiruvini jurnalga oladi (`resource_id` = order, `changes` =
status o'zgarishi va izoh).

## 6. Qoidalar

- Har yozuv: `lock → qayta o'qish → tekshirish → o'zgartirish → commit`.
  Tarmoq chaqiruvi qulfdan oldin yoki ikki qulf orasida; faqat 15 s bilan
  chegaralangan GTS GET qulf ostida bo'lishi mumkin.
- GTS'ga POST (booking, ticketing) **ko'r-ko'rona qayta yuborilmaydi**;
  natija noma'lum bo'lsa GET bilan o'qiladi.
- GTS bizga hech qachon qo'ng'iroq qilmaydi — `processing` holatlarni
  Celery beat sweep (30 s) `GET /v1/orders/{n}/` bilan yakunlaydi.
- Idempotency (Redis) — qulaylik qatlami; qulf va commit qilingan holat
  yolg'iz ushlab turishi shart.

## 7. Keyingi ishlar (bu to'rt bosqichdan tashqarida)

- `POST /public/orders/{id}/cancel/` — mijoz to'lanmagan bronni bekor qiladi
  (`POST /v1/content/cancel/`, `cancelled/customer`); guard'lar tayyor.
- Email xabarnomalar (`ticket_waiting`, `ticketed`, `ticketing_failed`) —
  `customers.service._send` namunasi; commit'dan keyin, faqat event qaytgan
  yo'l yuboradi. Hozir mijoz xabarni ilovada (`order.message`) ko'radi.
- `providers/payments/payme.py`, `click.py` — `start/confirm/status`;
  `ADAPTERS` jadvaliga bir qator.
- Provider refund API (`refund()` porti) — hozir support provider kabinetida
  qaytaradi va `refund/` bilan belgilaydi.
- `reprice_check` — ticketing o'zi reprice qiladimi, GTS bilan aniqlash kerak.
- Contract test sweep (trailing slash, envelope) ni qayta tiklash; CLAUDE.md
  hujjat jadvalini tozalash (`docs/API.md` va boshqalar yo'q).
