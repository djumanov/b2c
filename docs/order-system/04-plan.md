# 4. Implementation reja

[`03-design.md`](03-design.md) ni qurish tartibi. Har bir bo'lak:

* **mustaqil test qilinadi** va o'zidan keyin uchala gate'ni yashil qoldiradi
  (`ruff` · `mypy --strict` · `pytest`);
* alohida shoxda bajariladi: `<type>/<short-kebab-desc>` (`CLAUDE.md` §Workflow);
* `main` ga to'g'ridan-to'g'ri commit qilinmaydi, `push` va PR — faqat ruxsat bilan.

Bo'laklar ketma-ket. Sabab: har biri o'zidan oldingisining jadvalini yoki
portini ishlatadi, va pul yo'lida "yarim ulangan" holat eng qimmat xato.

---

## Umumiy ko'rinish

| # | Bo'lak | Migratsiya | Tashqi chaqiruv | Bog'liq |
|---|---|---|---|---|
| S1 | Holat mashinasi | `orders` qayta yoziladi, `order_events` | yo'q | — |
| S2 | `OrderOperations` porti + flight adapteri | yo'q | yo'q (fixture) | S1 |
| S3 | Bron niyati + yo'lovchilar + snapshot | `order_travelers`, `order_snapshots` | GTS booking | S1, S2 |
| S4 | Outbox + dispatcher | `outbox` | yo'q | S1 |
| S5 | `payments` yadrosi | `payments`, `payment_transactions` | yo'q | S3 |
| S6 | Payme/Click + webhooklar | yo'q | provayder | S5 |
| S7 | Ticketing sagasi | yo'q | GTS ticketing | S4, S6 |
| S8 | Kompensatsiya va qaytarish | `refunds` | GTS + provayder | S7 |
| S9 | Bekor qilish va muddat | yo'q | GTS cancel | S7 |
| S10 | Sinxronizatsiya va admin yuzasi | yo'q | GTS retrieve | S9 |
| S11 | Kvitansiya, tarix, anonimlashtirish | yo'q | yo'q | S10 |

---

## S1 — Holat mashinasi

**Shox:** `feat/order-state-machine`

Buyurtma tizimining yuragi, hech qanday tashqi chaqiruvsiz. Shu sababdan
birinchi: uni to'liq test qilish uchun na GTS, na provayder kerak.

**Quriladi**

* `app/modules/orders/states.py` — `OrderStatus` (12 qiymat), `StatusClass`,
  `OrderAction`, `Actor`, va **o'tishlar jadvali** `TRANSITIONS` (T1…T18)
  deklarativ struktura sifatida;
* `orders/models.py` qayta yoziladi — `03-design.md` §3.4 dagi ustunlar;
  `OrderEvent` modeli;
* `orders/service.transition(session, order_id, to, *, actor, reason, meta)` —
  `FOR UPDATE`, jadval tekshiruvi, guard, `order_events`, (S4 dan keyin)
  outbox;
* `app/providers/gts/base.py` dagi `OrderStatus` **olib tashlanadi** —
  lug'at endi `orders` moduliniki (u GTS emas, biznikidir).

**Migratsiya:** `orders` qayta yoziladi (DB bo'sh — `drop` + `create`).
CHECK cheklovlari **qo'lda** yoziladi.

**Testlar** — `tests/unit/test_order_states.py`,
`tests/integration/test_order_transitions.py`:

* jadvaldagi **har bir** o'tish ishlaydi;
* jadvalda yo'q har bir juftlik `409` beradi (kombinatorik supurgi);
* guard buzilganda ko'rsatilgan holatga tushadi;
* har bir o'tish aynan bitta `order_events` qatori qoldiradi;
* ikkita parallel o'tishdan bittasi o'tadi (`FOR UPDATE`);
* terminal (`yopiq`) holatdan chiqish yo'q.

**Qabul mezoni:** o'tishlar jadvalini o'qib, `03-design.md` §3.3 bilan qatorma-qator
solishtirish mumkin.

---

## S2 — `OrderOperations` porti va flight adapteri

**Shox:** `feat/order-operations-port`

**Quriladi**

* `app/providers/products/orders.py` — port va natija dataclass'lari
  (`BookingResult`, `TicketingResult`, `CancelResult`, `RetrieveResult`,
  `RepriceResult`, `RefundQuote`, `RefundCommitResult`, `TravelerRef`,
  `FailureClass`);
* `providers/products/flight.py` — port amalga oshiriladi: GTS javobini
  normallashtirish, `status_map()`, `classify()`, `available_actions()`;
* `float` → `Decimal` o'girish (`str()` orqali) va `ticket_time_limit`
  ning uch xil formatini o'qish (§3.5).

**Tashqi chaqiruv yo'q:** testlar yozib olingan haqiqiy GTS javoblarini
fixture sifatida ishlatadi (`EASY_GATEWAY` kolleksiyasi va `drct-error*.json`).

**Testlar** — `tests/unit/test_flight_order_ops.py`:

* yozib olingan bron javobidan `BookingResult` to'g'ri chiqadi;
* `46.89` `float` `Decimal("46.89")` ga aylanadi va `Money` uni qabul qiladi;
* `ticket_time_limit` uchala formatda ham to'g'ri `datetime` beradi,
  noaniq qiymatda zaxira ishlaydi va `WARNING` chiqadi;
* `BO/PW/TI/TE/CB/VO/RF/PRF` kanonikka to'liq xaritalanadi (**hech biri
  tushib qolmaydi** — enum bo'ylab supurgi);
* `classify()`: `not enough credits` → `retryable`, tanimagan xato →
  `terminal`.

---

## S3 — Bron niyati, yo'lovchilar, snapshot

**Shox:** `feat/order-booking-intent`

A1 (yo'qoladigan bron), A2 (idempotentlik) va A8 (blobdagi pasport) shu yerda
yopiladi.

**Quriladi**

* `order_travelers`, `order_snapshots` modellari va migratsiyasi;
* `orders/service.start_order(...)` — §3.8 dagi 5 qadamli ketma-ketlik:
  **INSERT avval, GTS keyin**;
* `products/router_public.booking` bir qatorga aylanadi va
  `IdempotencyKey` hamda `RateLimit("payment")` ga bog'lanadi;
* `products/service.book` va `orders/service.record_booking` **o'chiriladi**;
* javob `{order, payment_placeholder, data}` shaklida (to'lov qatori S5 da
  haqiqiy bo'ladi).

**Testlar** — `tests/integration/test_order_booking.py`:

* GTS javob bermasdan turib ham `created` qator mavjud;
* GTS xato bersa — `failed`, snapshot bilan;
* bir xil `Idempotency-Key` bilan ikkinchi so'rov **o'sha** buyurtmani
  qaytaradi va GTS'ga **ikkinchi marta bormaydi**;
* Redis tozalangan holatda ham takroriy kalit ikkinchi bron ochmaydi
  (bazadagi `UNIQUE` ushlaydi);
* kalitsiz so'rov `422`;
* yo'lovchilar `order_travelers` ga normallashib tushadi;
* `order_snapshots` da xom javob bor, `orders` da blob **yo'q**;
* `tests/contract/test_search_passthrough.py` yangilanadi: booking endi
  `order` qaytaradi, lekin **Redis'ga hamon hech nima yozilmaydi** va
  `search`/`offers`/`verify` hech qayerga tegmaydi (D2 saqlanadi).

---

## S4 — Outbox va dispatcher

**Shox:** `feat/order-outbox`

**Quriladi**

* `outbox` modeli va migratsiyasi;
* `orders/service.transition` endi yon ta'sirlarni **o'sha tranzaksiyada**
  outbox'ga yozadi;
* `app/tasks/outbox.py` — `dispatch` (`FOR UPDATE SKIP LOCKED`, backoff,
  `attempts`, `last_error`);
* `celery_app.py`: `money` navbati, `task_routes`, beat yozuvi
  (task bilan **bitta commitda**).

**Testlar** — `tests/integration/test_outbox.py`:

* o'tish va outbox qatori bitta tranzaksiyada — o'tish rollback bo'lsa
  outbox ham yo'q;
* dispatcher yuborgandan keyin `dispatched` bo'ladi;
* yuborish bilan belgilash orasidagi "o'lim" — task ikki marta ketadi va
  qabul qiluvchi buni bemalol ko'taradi;
* ikkita parallel dispatcher bir qatorni ikki marta yubormaydi.

---

## S5 — `payments` yadrosi

**Shox:** `feat/payments-core`

Provayderga bironta chaqiruvsiz: jadvallar, holatlar, o'qish endpointlari.

**Quriladi**

* `payments`, `payment_transactions` modellari va migratsiyasi;
* `payments/service.create_payment(...)` — `orders` uchun eshik;
* `GET /public/payments/{id}/`, `GET /public/payments/methods/`,
  `GET /public/transactions/{id}/`;
* `POST /public/payments/{id}/transactions/` — tranzaksiya qatorini yozadi va
  **nol adapter bo'lsa** `502` qaytaradi;
* `T5` (`booked → paid`) ni chaqiradigan `payments_service` → `orders_service`
  eshigi.

**Testlar:** nol adapter bilan hamma narsa yashil; summa/valyuta mos
kelmasa `T5` `needs_attention` ga tushadi.

---

## S6 — Payme va Click adapterlari, webhooklar

**Shox:** `feat/payment-providers`

**Quriladi**

* `providers/payments/payme.py`, `click.py` — mavjud port bo'yicha;
* `POST /api/v1/webhooks/payments/{provider}/` — envelope'siz
  (`API.md` §40), imzo tekshiruvi;
* `POST /admin/integrations/payments/{code}/test/` (`verify()`).

**Testlar** — `tests/integration/test_webhooks.py`:

* takroriy callback ikki marta yechmaydi (`uq_..._provider_ref`);
* imzo yomon bo'lsa **hech qanday holat o'zgarmaydi**;
* Payme yomon imzoga `200` + JSON-RPC `-32504` oladi, `401` emas;
* pul birligi: Payme **tiyin**, Click **so'm** — pinlangan test;
* "allaqachon to'langan" callback xato emas, muvaffaqiyat.

---

## S7 — Ticketing sagasi

**Shox:** `feat/order-ticketing`

Bu yerda birinchi marta **pul va chipta bir zanjirda** bog'lanadi.

**Quriladi**

* `orders/tasks.ticket` — §3.7 dagi 6 qadam;
* `reprice` (O6) va tolerantlik sozlamasi;
* `classify()` bo'yicha retry/backoff, `ticket_deadline`;
* `notice` matnlari `settings` ga (uch tilda);
* `gts_deposit_low` hisoblagichi va alert.

**Testlar** — `tests/integration/test_saga_ticketing.py`:

* happy path: `paid → ticketing → ticketed`, chipta raqamlari
  `order_travelers` ga tushadi;
* `retryable` xato — `ticketing` da qoladi va qayta rejalashtiriladi;
* deposit xatosi `retryable` sinfida va alohida hisoblagichni oshiradi
  (**refundga olib kelmaydi**);
* `ticket_deadline` o'tsa — `refunding`;
* narx tolerantlikdan oshsa — chipta chiqarilmaydi, `refunding`;
* qisman chipta — `needs_attention`;
* takroriy task ikkinchi ticketing chaqirmaydi.

---

## S8 — Kompensatsiya va qaytarish

**Shox:** `feat/order-refunds`

**Quriladi**

* `refunds` modeli va migratsiyasi;
* `refunds.commit` task: **chiptalanmagan** buyurtmada GTS `cancel`,
  **chiptalangan** buyurtmada `refund-commit`; ikkalasida ham provayder
  refund'i;
* `POST /public/orders/{id}/refund/quote/` (faqat o'qish) va
  `POST /public/orders/{id}/refund/`;
* `GET /admin/refunds/`, `approve/`, `reject/`;
* `needs_attention` va `POST /admin/orders/{id}/resolve/`.

**Testlar** — `tests/integration/test_saga_refund.py`:

* ticketing yiqilsa avtomatik to'liq qaytarish ishlaydi;
* qaytarish ham yiqilsa `needs_attention` va panelda ko'rinadi;
* mijoz so'rovi admin tasdig'isiz **bajarilmaydi**;
* takroriy `refunds.commit` ikki marta qaytarmaydi;
* `resolve/` audit yozuvi qoldiradi va sabab majburiy.

---

## S9 — Bekor qilish va bron muddati

**Shox:** `feat/order-cancel`

**Quriladi**

* `POST /public/orders/{id}/cancel/` (Idempotency-Key);
* `POST /public/{product}/cancel/`, `FlowStep.CANCEL`, `FLIGHT_CANCEL`
  **olib tashlanadi**; `API.md` §20 tegishli qismi tahrirlanadi;
* `orders.expire_unpaid` beat;
* S2 dagi `cancel` javobining `data` siz shakli (A7) shu yerda jonli
  tekshiriladi.

**Testlar:** to'langan buyurtmani bekor qilishga urinish `409` va qaytarish
yo'lini taklif qiladi · boshqa mijozning buyurtmasi `404` va GTS'ga
borilmaydi · muddat o'tgan bron GTS'da bo'shatiladi va `cancelled` bo'ladi ·
ikkita parallel bekor qilishdan bittasi `409`.

---

## S10 — Sinxronizatsiya, solishtirish, admin yuzasi

**Shox:** `feat/order-sync-admin`

**Quriladi**

* `orders.sync_open`, `orders.reconcile_orphans`, `orders.detect_stuck`,
  `payments.reconcile`;
* `available_actions` hisoblagichi;
* `/admin/orders/` ro'yxat, tafsilot, `sync/`, `note/`;
* `?search=` — buyurtma raqami, **PNR**, yo'lovchi ismi, telefon.

**Testlar** — `tests/integration/test_reconciliation.py`:

* GTS o'z tomonida bekor qilgan bron sinxronizatsiyada `cancelled` bo'ladi;
* `created` da qolgan buyurtma GTS ro'yxatidan topiladi va bog'lanadi;
* bir nechta nomzod bo'lsa `needs_attention`;
* SLA oshgan buyurtma aniqlanadi.

---

## S11 — Kvitansiya, tarix, anonimlashtirish

**Shox:** `feat/order-receipt-privacy`

**Quriladi**

* `GET /public/orders/{id}/history/` va `/receipt/`;
* `orders/service.anonymize(session, customer_id)` — `customers` moduli
  uchun eshik; `customers/service.delete_account` uni chaqiradi;
* `order_snapshots` dagi PII tozalash (`pii_purged_at`).

**Testlar** — `tests/integration/test_order_privacy.py`:

* akkaunt o'chirilgach `order_travelers` da ism/hujjat/kontakt yo'q, chipta
  raqami va summalar bor;
* snapshot bloblarida pasport raqami qolmaydi;
* **har bir jadvalning har bir matnli ustuni supuriladi** — pasport ham,
  karta raqami ham hech qayerda ochiq matnda yo'q.

Shu bo'lakda `STATUS.md` §8.17 (🔴) yopiladi.

---

## Uchidan-uchiga qabul mezoni

`PROJECT.md` §15 ning 2-faza mezonlari, `tests/e2e/test_flight_purchase.py`
(soxta GTS + soxta provayder):

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

Bular kod bo'laklari emas, lekin bajarilishi shart:

| Ish | Qachon |
|---|---|
| `API.md` §20/§21/§22 ni yangi kontraktga moslash | S3, S9 bilan birga |
| `ARCHITECTURE.md` §5 dan `booking` modulini olib tashlash (O1) | S1 bilan |
| `PROJECT.md` D3 ni aniqlashtirish (xato sinflari, O5) | S7 bilan |
| `STATUS.md` §8 dagi 🔴 bandlarni yopish | S9 (A7), S11 (A8) |
| Jonli GTS'da S1…S3 savollarini tekshirish (§3.10) | S2 dan oldin |
