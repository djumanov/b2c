# 4. Implementation reja

[`03-design.md`](03-design.md) ni qurish tartibi. Har bir bo'lak:

* **mustaqil test qilinadi** va o'zidan keyin uchala gate'ni yashil qoldiradi
  (`ruff` · `mypy --strict` · `pytest`);
* alohida shoxda bajariladi: `<type>/<short-kebab-desc>` (`CLAUDE.md` §Workflow);
* `main` ga to'g'ridan-to'g'ri commit qilinmaydi, `push` va PR — faqat ruxsat bilan.

Bo'laklar ketma-ket: har biri o'zidan oldingisining jadvalini yoki portini
ishlatadi, va pul yo'lida "yarim ulangan" holat eng qimmat xato.

---

## Umumiy ko'rinish

| # | Bo'lak | Migratsiya | Tashqi chaqiruv | Bog'liq |
|---|---|---|---|---|
| S1 | Holat mashinasi | `orders` qayta yoziladi, `order_events` | yo'q | — |
| S2 | `OrderOperations` porti + flight adapteri | yo'q | yo'q (fixture) | S1 |
| S3 | Bron niyati + `Idempotency-Key` | yo'q | GTS booking | S1, S2 |
| S4 | To'lov: `order_payments` + Payme/Click + webhooklar | `order_payments` | provayder | S3 |
| S5 | Ticketing + poller + reprice | yo'q | GTS ticketing | S4 |
| S6 | Kompensatsiya va qaytarish | `order_refunds` | GTS + provayder | S5 |
| S7 | Bekor qilish, sinxronizatsiya, admin yuzasi, maxfiylik | yo'q | GTS cancel/retrieve | S6 |

Oldingi qoralamada 11 ta bo'lak bor edi; outbox, snapshot va yo'lovchilar
jadvallari tashlangach ular ettitaga siqildi.

---

## S1 — Holat mashinasi

**Shox:** `feat/order-state-machine`

Buyurtma tizimining yuragi, hech qanday tashqi chaqiruvsiz. Shu sababdan
birinchi: uni to'liq test qilish uchun na GTS, na to'lov provayderi kerak.
**Bron javobining sim ustidagi shakli o'zgarmaydi** — `booking/` hamon GTS'ning
`data` sini qaytaradi, ya'ni `test_search_passthrough.py` yashil qoladi.

**Quriladi**

* `app/modules/orders/states.py` — `OrderStatus` (12 qiymat), `StatusClass`,
  `OrderAction`, `Actor`, va **o'tishlar jadvali** `TRANSITIONS` (T1…T18)
  deklarativ struktura sifatida; `can()`, `is_closed()`;
* `orders/models.py` qayta yoziladi — `03-design.md` §3.4 dagi ustunlar;
  `OrderEvent` modeli;
* `orders/service.transition(session, order_id, to, *, actor, reason, meta)` —
  `FOR UPDATE`, jadval tekshiruvi, guard, vaqt tamg'asi, `order_events`,
  `next_attempt_at`;
* mavjud `record_booking` va `apply_cancel` shu funksiya orqali o'tadi;
* `app/providers/gts/base.py` dagi `OrderStatus` **olib tashlanadi** — lug'at
  endi `orders` moduliniki (u GTS emas, biznikidir), va bugun o'lik kod.

**Migratsiya:** `orders` qayta yaratiladi (DB bo'sh), `order_events` qo'shiladi,
`order_no` uchun sequence. CHECK cheklovlari **qo'lda** yoziladi.

**Testlar**

* `tests/unit/test_order_states.py` — jadvaldagi har bir o'tish ishlaydi;
  jadvalda **yo'q** har bir juftlik rad etiladi (12 × 12 supurgi); har bir
  status aynan bitta sinfga tegishli; *yopiq* holatlardan chiqish yo'q;
* `tests/integration/test_order_transitions.py` — har o'tish aynan bitta
  `order_events` qatori qoldiradi; noqonuniy o'tish `409`; ikkita parallel
  o'tishdan bittasi o'tadi (`FOR UPDATE`); vaqt tamg'alari bosiladi;
* `tests/integration/test_orders.py` yangilanadi — `status` endi kanonik
  (`booked`/`cancelled`), GTS kodi `provider_status` da.

---

## S2 — `OrderOperations` porti va flight adapteri

**Shox:** `feat/order-operations-port`

**Quriladi**

* `app/providers/products/orders.py` — port va natija dataclass'lari
  (`BookingResult`, `TicketingResult`, `CancelResult`, `RetrieveResult`,
  `RepriceResult`, `RefundQuote`, `RefundCommitResult`, `TravelerRef`,
  `FailureClass`);
* `providers/products/flight.py` — port amalga oshiriladi: GTS javobini
  normallashtirish, `travelers` massivini **bizning shaklga** o'girish,
  `status_map()`, `classify()`, `available_actions()`;
* `float` → `Decimal` (`str()` orqali) va `ticket_time_limit` ning uch xil
  formatini o'qish (§3.5).

**Tashqi chaqiruv yo'q:** testlar yozib olingan haqiqiy GTS javoblarini fixture
sifatida ishlatadi (`EASY_GATEWAY` kolleksiyasi, `drct-error*.json`).

**Testlar** — `tests/unit/test_flight_order_ops.py`: yozib olingan bron
javobidan `BookingResult` to'g'ri chiqadi · `46.89` `float` `Decimal("46.89")`
ga aylanadi · `ticket_time_limit` uchala formatda ham to'g'ri, noaniqda zaxira
va `WARNING` · `BO/PW/TI/TE/CB/VO/RF/PRF` **to'liq** xaritalanadi (enum
bo'ylab supurgi) · `classify()`: `not enough credits` → `retryable`, tanimagan
xato → `terminal`.

---

## S3 — Bron niyati va idempotentlik

**Shox:** `feat/order-booking-intent`

A1 (yo'qoladigan bron), A2 (idempotentlik) va A8 (blobdagi pasport) shu yerda
yopiladi.

**Quriladi**

* `orders/service.start_order(...)` — §3.8 dagi ketma-ketlik:
  **INSERT avval, GTS keyin**;
* `products/router_public.booking` bir qatorga aylanadi va `IdempotencyKey`
  hamda `RateLimit("payment")` ga bog'lanadi;
* `products/service.book` va `orders/service.record_booking` **o'chiriladi**;
* javob `{order, payment, data}` shakliga o'tadi (`payment` — S4 gacha
  `null`), `API.md` §20/§21 shu commitda tahrirlanadi.

**Testlar** — `tests/integration/test_order_booking.py`: GTS javob bermasdan
turib ham `created` qator mavjud · GTS xato bersa `failed` · bir xil
`Idempotency-Key` bilan ikkinchi so'rov **o'sha** buyurtmani qaytaradi va
GTS'ga bormaydi · Redis tozalangan holatda ham takroriy kalit ikkinchi bron
ochmaydi (bazadagi `UNIQUE`) · kalitsiz so'rov `422` · yo'lovchilar
`orders.travelers` ga **bizning shaklda** tushadi.
`tests/contract/test_search_passthrough.py` yangilanadi: booking endi `order`
qaytaradi, lekin Redis'ga hamon hech nima yozilmaydi va qidiruv qadamlari
hech qayerga tegmaydi (D2 saqlanadi).

---

## S4 — To'lov

**Shox:** `feat/order-payments`

**Quriladi**

* `order_payments` modeli va migratsiyasi;
* `POST /public/payments/{id}/transactions/`, `GET /public/payments/{id}/`,
  `GET /public/payments/methods/`;
* `providers/payments/payme.py`, `click.py` — mavjud port bo'yicha;
* `POST /api/v1/webhooks/payments/{provider}/` — envelope'siz (`API.md` §40),
  imzo tekshiruvi, `T5` ni chaqirish;
* `POST /admin/integrations/payments/{code}/test/` (`verify()`).

**Testlar** — `tests/integration/test_payments.py`, `test_webhooks.py`:
takroriy callback ikki marta yechmaydi (`uq_order_payments_provider_ref`) ·
imzo yomon bo'lsa **hech qanday holat o'zgarmaydi** · Payme yomon imzoga `200`
+ JSON-RPC `-32504` oladi · pul birligi: Payme **tiyin**, Click **so'm** —
pinlangan test · "allaqachon to'langan" callback xato emas · summa mos
kelmasa `T5` `needs_attention` ga tushadi.

---

## S5 — Ticketing va poller

**Shox:** `feat/order-ticketing`

Bu yerda birinchi marta **pul va chipta bir zanjirda** bog'lanadi.

**Quriladi**

* `app/tasks/orders.py` — `run_due` (poller, `FOR UPDATE SKIP LOCKED`) va
  `ticket` (§3.7 dagi 6 qadam);
* `celery_app.py`: `money` navbati, `task_routes`, beat yozuvi — **task bilan
  bitta commitda**;
* `reprice` (O6) va tolerantlik sozlamasi;
* `classify()` bo'yicha retry/backoff, `ticket_deadline`;
* `notice` matnlari `settings` ga (uch tilda);
* `gts_deposit_low` hisoblagichi va alert.

**Testlar** — `tests/integration/test_saga_ticketing.py`: happy path
`paid → ticketing → ticketed`, chipta raqamlari `travelers` ga tushadi ·
`retryable` xato — `ticketing` da qoladi, `next_attempt_at` suriladi ·
**deposit xatosi refundga olib kelmaydi**, alohida hisoblagichni oshiradi ·
`ticket_deadline` o'tsa `refunding` · narx tolerantlikdan oshsa chipta
chiqarilmaydi · qisman chipta `needs_attention` · takroriy task ikkinchi
ticketing chaqirmaydi · poller yuborilmay qolgan qadamni topadi.

---

## S6 — Kompensatsiya va qaytarish

**Shox:** `feat/order-refunds`

**Quriladi**

* `order_refunds` modeli va migratsiyasi;
* `refunds.commit` task: **chiptalanmagan** buyurtmada GTS `cancel`,
  **chiptalangan** buyurtmada `refund-commit`; ikkalasida ham provayder
  refund'i;
* `POST /public/orders/{id}/refund/quote/` (faqat o'qish) va
  `POST /public/orders/{id}/refund/`;
* `GET /admin/refunds/`, `approve/`, `reject/`;
* `needs_attention` va `POST /admin/orders/{id}/resolve/`.

**Testlar** — `tests/integration/test_saga_refund.py`: ticketing yiqilsa
avtomatik to'liq qaytarish ishlaydi · qaytarish ham yiqilsa `needs_attention`
va panelda ko'rinadi · mijoz so'rovi admin tasdig'isiz **bajarilmaydi** ·
takroriy `refunds.commit` ikki marta qaytarmaydi · `resolve/` audit yozuvi
qoldiradi va sabab majburiy.

---

## S7 — Bekor qilish, sinxronizatsiya, admin yuzasi, maxfiylik

**Shox:** `feat/order-cancel-admin`

**Quriladi**

* `POST /public/orders/{id}/cancel/` (Idempotency-Key);
  `POST /public/{product}/cancel/`, `FlowStep.CANCEL`, `FLIGHT_CANCEL`
  **olib tashlanadi**;
* `orders.expire_unpaid`, `orders.sync_open`, `orders.reconcile_orphans`,
  `orders.detect_stuck`, `payments.reconcile`;
* `available_actions`; `/admin/orders/` ro'yxat, tafsilot, `sync/`, `note/`,
  `?search=` (buyurtma raqami, PNR, yo'lovchi ismi);
* `GET /public/orders/{id}/history/` va `/receipt/`;
* `orders/service.anonymize(...)` — `customers.delete_account` uchun eshik.

**Testlar**: to'langan buyurtmani bekor qilishga urinish `409` · muddat o'tgan
bron GTS'da bo'shatiladi · GTS o'z tomonida bekor qilgani sinxronizatsiyada
ko'rinadi · `created` da qolgani GTS ro'yxatidan topiladi, bir nechta nomzod
bo'lsa `needs_attention` · akkaunt o'chirilgach `travelers` da ism/hujjat yo'q,
chipta raqami bor · **har bir jadvalning har bir matnli ustuni supuriladi** —
pasport ham, karta raqami ham ochiq matnda yo'q.

Shu bo'lakda `STATUS.md` §8.15a (A7) va §8.17 (A8) yopiladi.

---

## Uchidan-uchiga qabul mezoni

`PROJECT.md` §15 ning 2-faza mezonlari:

| Mezon | Qayerda |
|---|---|
| Qidiruv → bron → to'lov → chipta uchidan-uchiga | `tests/e2e/test_flight_purchase.py` |
| Chipta xatosida avtomatik qaytarish | `tests/integration/test_saga_refund.py` |
| Qaytarish ham xato → `needs_attention` | shu yerda |
| Takroriy webhook ikki marta yechmaydi | `tests/integration/test_webhooks.py` |
| Takliflar hech qayerda saqlanmaydi (D2) | `tests/contract/test_search_passthrough.py` |
| Karta raqami ochiq matnda hech bir jadvalda yo'q | `tests/integration/test_order_privacy.py` |
| **Pul jimgina yo'qolmaydi** | Yuqoridagi uchtasi birgalikda |

---

## Bo'laklardan tashqari

| Ish | Qachon |
|---|---|
| `API.md` §20/§21/§22 ni yangi kontraktga moslash | S3 va S7 bilan |
| `ARCHITECTURE.md` §5 dan `booking` modulini olib tashlash (O1) | S1 bilan |
| `ARCHITECTURE.md` §8 ga outbox o'rniga poller (O13) | S5 bilan |
| `PROJECT.md` D3 ni aniqlashtirish (xato sinflari, O5) | S5 bilan |
| Jonli GTS'da `Q1`…`Q3` savollarini tekshirish (§3.10) | S2 dan oldin |
