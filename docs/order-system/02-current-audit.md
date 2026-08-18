# 2. Mavjud kod auditi

Holat: `main` (`42ee2f8`), 2026-08-18. Aviachipta oqimi qurilgan, lekin
**buyurtma tizimi qurilmagan** — bor narsa passthrough va uning yonidagi bitta
jadval.

Bu hujjatdagi har bir da'vo `fayl:qator` bilan tasdiqlangan. Baholar:
🔴 pulga yoki bronga zarar · 🟠 ma'lumot yo'qolishi · 🟡 tozalash.

---

## 2.1 Bugun nima bor

| Qism | Fayl | Hajm | Nima qiladi |
|---|---|---|---|
| Order modeli | `app/modules/orders/models.py` | 110 q. | Bitta `orders` jadvali, GTS javobi `JSONB` blob |
| Repository | `app/modules/orders/repository.py` | 52 q. | Uchta o'qish funksiyasi, hammasi egaga bog'langan |
| Service | `app/modules/orders/service.py` | 226 q. | `record_booking:91`, `owned_by_gts_number:143`, `apply_cancel:157`, `list_orders:182`, `get_order:211` |
| Public router | `app/modules/orders/router_public.py` | 80 q. | Faqat ikkita `GET` |
| Oqim routeri | `app/modules/products/router_public.py` | 197 q. | `booking/:166`, `cancel/:180` |
| Oqim service | `app/modules/products/service.py` | 166 q. | `book:92`, `cancel:133` |
| Flight adapteri | `app/providers/products/flight.py` | — | `book:172` → `/v1/content/booking/:187`, `cancel:190` → `/v1/content/cancel/:202` |
| Migratsiyalar | `migrations/versions/20260817_1053_orders.py`, `…_1738_orders_gts_identifiers.py` | — | `orders` jadvali va `gts_order_id` → `gts_order_number` nomlash tuzatishi |

Buyurtma oqimidagi **butun** yozuv yo'li shu:

```
POST /public/flight/booking/          products/router_public.py:166
  → products/service.book             products/service.py:92
      → adapter.book                  flight.py:172   (GTS'ga passthrough)
      → orders_service.record_booking  orders/service.py:91   (try/except ichida)
  → GTS javobi o'zgarmasdan qaytadi
```

---

## 2.2 Nima noto'g'ri qilingan

### A1 🔴 Bron yo'qolishi mumkin va buni hech kim bilmaydi

`app/modules/products/service.py:115-130`:

```python
data = await adapter.book(await _client(session), payload)
try:
    await orders_service.record_booking(...)
except Exception:
    logger.exception("order_not_recorded", ...)
return data
```

INSERT yiqilsa (baza tushdi, unique buzildi, ustun sig'madi) — GTS o'rinni
ushlab turadi, bizda yozuv yo'q. Mijoz uni ro'yxatda ko'rmaydi, bekor
qilolmaydi, biz esa uning mavjudligini faqat log qatoridan bilamiz.

Kodning o'zi buni tan oladi (`service.py:106-113`): `500` qaytarish mijozni
qayta urinishga va **ikkinchi haqiqiy o'rin** ochishga majbur qilardi, shuning
uchun kichikroq yomonlik tanlangan. To'g'ri yechim — buyurtmani **GTS
chaqiruvidan oldin** yozish; u hali yo'q.

### A2 🔴 Idempotentlik nol iste'molchi bilan

`app/api/idempotency.py` to'liq yozilgan: Redis `SET NX` bilan da'vo,
24 soatlik replay, barmoq izi mos kelmasa `422`, poyga bo'lsa `409`. Butun
`app/` bo'ylab `Idempotency` so'zi shu fayldan tashqarida **umuman uchramaydi**.

`POST /{product}/booking/` (`router_public.py:166-177`) header so'ramaydi.
Ya'ni ikki marta bosilgan tugma — ikkita bron. `STATUS.md` №79 buni ataylab
kechiktirilgan deb yozadi ("pul yo'li saga bilan keladi"), lekin bron **hozir
ham** haqiqiy o'rin band qiladi.

Alohida rate limit ham yo'q (`router_public.py:161-164`): bron umumiy 120/daq
chelagida. `RateLimit("payment")` (10/daq) `app/api/deps.py:251` da bor va
ishlatilmaydi.

### A3 🔴 Holat mashinasi yo'q, kanonik enum esa o'lik kod

`app/modules/orders/models.py:90-93`:

```python
#: GTS's status code, verbatim. **No CHECK constraint on purpose**
status: Mapped[str | None] = mapped_column(String(16), index=True)
```

Qiymat — GTS lug'ati (`BO/PW/TI/TE/CB/VO/RF/PRF`). Bizning kanonik enum
`OrderStatus` `app/providers/gts/base.py:34-52` da e'lon qilingan va **hech
qayerda import qilinmagan**.

Natijada:

* `apply_cancel` (`orders/service.py:157-176`) istalgan statusni istalganiga
  almashtiradi — chiptalangan (`TI`) buyurtmani ham "bekor qilindi" deb
  yozaveradi;
* `products/service.cancel` (`service.py:133-163`) buyurtma holatini umuman
  tekshirmaydi: allaqachon bekor qilingani ham GTS'ga yuboriladi;
* `available_actions` yo'q — mijoz nima qila olishini server aytmaydi.

### A4 🔴 Ticketing umuman yo'q

`/v1/content/ticketing/` `app/` bo'ylab hech qayerda chaqirilmaydi. Ya'ni
hozirgi tizim bron qiladi va **hech qachon chipta chiqarmaydi**.

`ticket_time_limit` GTS javobida bor va `gts_response` blobida saqlanadi, lekin
hech kim uni o'qimaydi. Bron muddati jimgina o'tadi; GTS o'z tomonida
avtomatik bekor qiladi ([`../GTS.md`](../GTS.md) §11) va biz bundan xabar
topmaymiz.

`void`, `refund-check`, `refund-commit`, `reprice_check`, `retrieve` —
hech biri chaqirilmaydi.

### A5 🔴 To'lov bilan bog'lanish yo'q

`orders` jadvalida `payment_id` ham, `amount` ham, `currency` ham yo'q
(`models.py:70-107`). Narx faqat blob ichida.

`app/modules/payments/` da faqat `CustomerCard` (`models.py:43`) — saqlangan
karta. `payments`, `transactions`, `refunds` jadvallari yo'q.
`webhooks_router` (`app/api/v1/router.py:51`) e'lon qilingan va unga
**bironta router qo'shilmagan**.

`app/core/money.py` to'liq yozilgan (`Money`, `money_column`,
`currency_column`) va **bironta jadval uni ishlatmaydi**.

### A6 🟠 Status tarixi ham, outbox ham yo'q

`apply_cancel` statusni joyida almashtiradi va oldingi qiymat hech qayerda
qolmaydi. `order_events` yoki shunga o'xshash jadval yo'q.

Audit middleware faqat `/api/v1/admin/` ni qamraydi
(`app/modules/audit/middleware.py:36`), ya'ni mijozning bron va bekor qilishi
audit jurnaliga ham tushmaydi.

Outbox jadvali yo'q. Celery'da order bilan bog'liq **bironta task yo'q**:
`app/tasks/celery_app.py:40` faqat `heartbeat` va `uploads` ni yuklaydi,
beat jadvalida (`:66-77`) ikkita yozuv bor. `:57-65` dagi izoh kelajakdagi
`sync open orders from GTS` yozuvini **band qilib qo'ygan**, lekin task yo'q.

Diqqatga sazovor: `task_acks_late=True` va `task_reject_on_worker_lost=True`
(`celery_app.py:49-51`) allaqachon sozlangan — aynan saga uchun.

### A7 🔴 Bekor qilish jonli GTS'da doim `502` berishi mumkin

`app/providers/gts/client.py:318-320`:

```python
data = payload.get("data")
if not isinstance(data, dict):
    raise UpstreamError("GTS returned an unexpected shape")
```

Kolleksiyada yozib olingan GTS `cancel` javobi esa `{status, code, order}` —
`data` kaliti **yo'q**. Jonli javob ham shunday bo'lsa, bekor qilish har doim
`502`. Bron ishlaydi, bekor qilish ishlamaydi ([`../STATUS.md`](../STATUS.md)
§8.15a).

### A8 🔴 Blob ichidagi pasport anonimlashtirishni imkonsiz qiladi

`orders.gts_response` — GTS javobi to'liq, ichida `data.passengers[]` va u
yerda `passport_number`, `passport_issuance`, `passport_expiry`, `birth_date`,
`email_address`, `phone_number`.

`app/modules/customers/service.py` da `orders` so'zi **umuman yo'q** — akkaunt
o'chirilganda mijoz tozalanadi, yo'lovchilar soft-delete qilinadi, kartalar
unutiladi, `orders` esa qo'l tegmasdan qoladi.

`PROJECT.md` §13 buyurtmani anonimlashtirishni va'da qiladi. Ichini
bilmaydigan blobni anonimlashtirib bo'lmaydi — va'da bugun bajarilmaydi.

### A9 🟠 Konkurentlik nazorati yo'q

`owned_by_gts_number` (`orders/repository.py:32`) oddiy `SELECT`. Ikkita
parallel bekor qilish ikkalasi ham egalik tekshiruvidan o'tadi va ikkalasi ham
GTS'ga boradi. Hech qayerda `with_for_update` yo'q.

Yagona haqiqiy dublikat qo'riqchisi — `uq_orders_gts_order_number_live`
partial unique indeksi (`models.py:56-63`), va u chiqargan `IntegrityError`
A1 dagi `except Exception` tomonidan yutiladi.

### A10 🟠 Xato va status xaritalari yarim

Xato xaritasi bor (`client.py:279-323`): GTS'ning HTTP 200 + `status: "error"`
konvensiyasi `UpstreamError` ga o'giriladi, asl matn `message` da, asl kod
`meta.upstream` da. Bu qism yaxshi.

Yo'q qismi: `409 offer_expired` va `400 payment_failed` katalogda bor
(`app/api/errors.py:151,157`) va GTS kodlaridan **hech qachon** hosil
qilinmaydi. Muddati o'tgan taklif bugun `502` bo'lib qaytadi.

Retry siyosati faqat bitta holatga: 401/403 da bir marta qayta kirish
(`client.py:216,243`). 5xx va timeout uchun retry, backoff, circuit breaker
yo'q. Har chaqiruvda yangi `httpx.AsyncClient` (`client.py:225`) — ulanish
qayta ishlatilmaydi.

### A11 🟡 Ishlatilmaydigan qismlar

| Nima | Qayerda | Holat |
|---|---|---|
| `orders.deleted_at` | `models.py` (`Entity` dan) | Har o'qishda hisobga olinadi, **hech kim yozmaydi** |
| `save_passenger` | `products/openapi.py:566` | Kontraktda hujjatlashtirilgan, kodda **o'qilmaydi** — GTS'ga uzatiladi va shu yerda tugaydi |
| `OrderStatus` | `providers/gts/base.py:34` | O'lik kod |
| `core/money.py` | butun modul | Hech bir jadval ishlatmaydi |
| `RateLimit("payment")` | `api/deps.py:251` | Chelak bor, iste'molchi yo'q |
| `app/modules/booking/` | bo'sh `__init__.py` | Placeholder |

---

## 2.3 Umuman hisobga olinmagan holatlar

`01-research.md` §1.6 jadvalidagi 12 ssenariydan bugun **bittasi ham**
qoplanmagan:

| # | Ssenariy | Bugun nima bo'ladi |
|---|---|---|
| 1 | Bron timeout | `504` qaytadi, buyurtma yozilmaydi, bron GTS'da qoladi — abadiy ko'rinmas |
| 2 | To'lov o'tdi, ticketing yiqildi | To'lov ham, ticketing ham yo'q |
| 3 | Refund ham yiqildi | Refund yo'q |
| 4 | Takroriy webhook | Webhook yo'q |
| 5 | Webhook kelmadi | — |
| 6 | Ikki marta bosildi | Ikkita haqiqiy bron |
| 7 | Parallel bekor qilish | Ikkalasi ham GTS'ga boradi |
| 8 | Bron muddati o'tdi | Hech nima; GTS o'zi bekor qiladi, biz bilmaymiz |
| 9 | Narx o'zgardi | Reprice chaqirilmaydi |
| 10 | GTS balansimiz bo'sh | Ticketing yo'q, shuning uchun bilinmaydi |
| 11 | Worker o'ldi | Fon ishi yo'q |
| 12 | GTS holatni o'zgartirdi | Sinxronizatsiya yo'q |

---

## 2.4 Nimani qayta ishlatamiz

Bu — auditning eng muhim qismi: poydevorning katta qismi **tayyor va sifatli**.

| Qism | Fayl | Nega yaraydi |
|---|---|---|
| Envelope | `app/api/envelope.py` | `enveloped_router`, `Page[T]`, `PageMeta`. Handler model qaytaradi, o'ram markazda quriladi |
| Xato katalogi | `app/api/errors.py` | 11 kod, HTTP xaritasi, `UpstreamError(upstream_code=…)`. Order yo'liga aynan mos |
| Idempotentlik | `app/api/idempotency.py` | To'liq va to'g'ri yozilgan; faqat ulash qoldi |
| Ro'yxat yordamchilari | `app/api/listing.py` | `apply_search`, `apply_created_range`, `apply_ordering`, `paginate`, `page` |
| Auth/RBAC/limit | `app/api/deps.py` | `CurrentCustomer`, `CurrentStaff`, `require_owner`, `RateLimit`, `Pagination` |
| Pul turi | `app/core/money.py` | `Money`, `money_column`, `currency_column` — `NUMERIC(18,2)` + `CHAR(3)` |
| Model asoslari | `app/db/mixins.py` | UUID PK **ilova tomonda** (`:30-32`) — outbox qatorini bitta tranzaksiyada yozish uchun ataylab shunday |
| GTS klienti | `app/providers/gts/client.py` | Sessiya menejeri (Redis + qulf), xato xaritasi, `X-Request-Id` uzatish |
| Mahsulot porti | `app/providers/products/base.py` | `ProductCode`, `FlowStep`, `ProductAdapter`, registry |
| To'lov porti | `app/providers/payments/base.py` | `PaymentProvider`, natija dataclass'lari, **factory** registry |
| Audit | `app/modules/audit/` | Middleware + `audit_context.describe/diff` |
| Sozlama-DB'da | `app/modules/settings/` | `notice` matnlari va tolerantliklar shu yerda yashaydi |
| Test infratuzilmasi | `tests/conftest.py`, `tests/integration/conftest.py` | Haqiqiy Postgres, Alembic zanjiri, tranzaksiya izolyatsiyasi, `fakeredis` |

**Qayta ishlatilmaydigan (qayta yoziladigan) qism:** `orders` modulining o'zi
(model, service, repository) va `products/service.py` dagi `book`/`cancel`.
Ular passthrough uchun to'g'ri yozilgan, buyurtma tizimi uchun emas.

---

## 2.5 Yangi dizayn buzadigan testlar

Bular kutilgan o'zgarishlar — hujjatga ro'yxatga olinadi, "singan test" emas.

| Test | Nima kutadi | Nega o'zgaradi |
|---|---|---|
| `tests/contract/test_search_passthrough.py` (~`:280`) | "Booking adds nothing of ours: no `payment_id`, no order id" | Endi `{order, payment, data}` qaytaradi. Testning o'z docstring'i bu paytni oldindan aytgan |
| `tests/integration/test_orders.py` | `POST /public/flight/cancel/` keyslari | Bekor qilish `POST /public/orders/{id}/cancel/` ga ko'chadi |
| `tests/unit/test_flight_adapter.py` | `cancel` tanasini ko'rmasdan uzatadi | Adapter endi normallashtirilgan natija qaytaradi |
| `tests/integration/test_flight_search.py` | "bir bron aynan bitta qator yozadi" | Endi order + travelers + snapshot + payment |
| `tests/contract/test_products_openapi.py` | `FLIGHT_CANCEL` bloki | Blok o'chiriladi |
