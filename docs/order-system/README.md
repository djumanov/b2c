# Order tizimi — buyurtma, to'lov, ticketing

Bu hujjat buyurtma (order) hayotiy sikli bo'yicha **manba**. Kod unga
ergashadi; farq topilsa avval shu fayl tuzatiladi, keyin kod.

To'rt bosqichda qurilgan: **1 — lifecycle**, **2 — to'lov** (port, sandbox,
**Payme Subscribe API** adapteri, `payment_attempts`, sweep), **3 — GTS
ticketing**, **4 — support** (`/admin/orders/`). Hammasi joriy. Keyingi
ishlar — §7.

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
`paid_at`, `ticketed_at`, `cancelled_at`, `price_confirmed_at` (mijoz
narxni qabul qilib GTS tasdiqlagan vaqt — `reprice_confirm`; to'lov shusiz
rad etiladi; `reprice_check` iz qoldirmaydi), `price_response` (oxirgi
`reprice_confirm` javobi — `price_info`, `price_details` — aynan o'zi),
`ticketing_requested_at`
(GTS'dan chipta so'ralgan vaqt), `gts_checked_at` (sweep oxirgi marta
GTS'dan o'qigan vaqt), `ticketing_attempts` (so'rov necha marta yuborilgan),
`ticketing_error` (GTS'ning xato matni). `gts_status` va `gts_response` —
GTS'ning o'z kodi va to'liq javobi, har o'qishda yangilanadi. `amount` esa
faqat **narxi hali tasdiqlanmagan** orderda o'qishdan yangilanadi:
`price_confirmed_at` bosilgach `price_response` — GTS'ning keyingi so'zi,
order yozuvining o'z `price_info`si esa bron paytidagi narx; o'qish farq
qilsa log'ga yoziladi (`gts_price_differs_from_confirmed`), `amount`
o'zgarmaydi. Mijozga
`order_data` ham shu asosda ketadi: `gts_response` ustiga `price_response`dagi
`price_info` (va bo'sh bo'lmasa `price_details`) qo'yiladi — `order.amount`,
`payment.amount` va `order_data.price_info` hech qachon har xil narx aytmaydi.

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
  ko'radi, admin `status=ticketing_failed` ro'yxatida chiqadi).
- `payment → refunding | refunded | refund_failed`: faqat staff; ticketing
  `ticketed` bo'lmasa.
- `ticketing → processing | ticketed`: `payment = paid` va `status = booked`.

Kim (`order_events.actor`): `customer`, `system` (sweep), `staff:<uuid>`.

## 4. Tarix — `order_events`

`order_id, created_at, seq, event` (`payment.paid`, `ticketing.failed`,
`order.created`…)`, from_value, to_value, actor, note, data, request_id`.
`data` — faqat kod va id'lar (`gts_order_number`, urinish id); xom GTS javobi
`orders.gts_response` da, karta haqida hech narsa hech qayerda.

`created_at` — `now()`, ya'ni tranzaksiya boshlangan vaqt: bitta commit
yozgan qatorlar (`ticketing.processing` + `ticketing.requested`) bir xil
tamg'a oladi. `seq` — bazaning o'z hisoblagichi (identity), yozilish tartibi;
tarix `created_at, seq` bo'yicha o'qiladi, shuning uchun tartib hech qachon
tasodifiy emas.

## 4a. To'lov — `payment_attempts` va uch qadam

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

0. **Narx — `POST /public/orders/{id}/reprice/` va `POST
   /public/orders/{id}/reprice/confirm/`** (body yo'q). GTS'ning o'z
   hayot sikli (`docs/gts-api-v1.4.pdf`, 4-bet): `booking → reprice_check →
   reprice_confirm → ticketing`; jonli server check'siz ticketing'ni rad
   etadi ("Перед выпиской билета выполните reprice_check", 2026-08-24), lekin
   **confirm'ni faqat narx o'zgarganda qabul qiladi** (aks holda `400803`).
   Shuning uchun **mijoz ilovasi** to'lov ekranidan oldin `reprice/`ni
   chaqiradi va faqat `changed: true` bo'lsa `reprice/confirm/`ni:
   - `reprice/` → `POST /v1/content/reprice_check/ {order_number}`
     (`FlightAdapter.reprice`) — **sof so'rov, passthrough**: javob (`RepriceOut`)
     = `changed` (narx o'zgardimi), `old_price` (orderda saqlangan — mijoz
     ko'rib turgan narx: bron narxi yoki oxirgi tasdiqlangani), `new_price`
     (GTS'ning bugungi narxi) + GTS'ning `data`si (`price_info`,
     `price_details`) komissiya maydonlarisiz aynan. `changed` — **GTS'ning
     o'z hukmi**: javobdagi `price_changed`. GTS uni yubormasa (hujjatdagi
     shakl) summa/valyuta solishtiriladi (yoki orderda narx bo'lmasa
     `true`). Narx **hech qachon bu yerda o'zgarmaydi** — `amount`, ochiq
     urinish tegilmaydi.
     - `changed: false` → **narx shu qadamning o'zida yopiladi**
       (`_settle_unmoved_price`): `price_confirmed_at = now`, event
       `price.confirmed`, `payment/` ochiladi. Sababi — GTS o'zgarmagan narxni
       confirm qilishni rad etadi (`400803`), ya'ni kutadigan ikkinchi qadam
       yo'q; GTS'ning talabi esa faqat "ticketing'dan oldin check bo'lsin", u
       hozir bajarildi. Faqat to'lash mumkin bo'lgan order uchun (to'langan,
       ticketing'dagi yoki bekor qilingan orderga savol berish mumkin, lekin
       u hech narsa yozmaydi).
     - `changed: true` → hech narsa yozilmaydi; mijoz yangi narxni ko'rsatib
       rozilik oladi va `reprice/confirm/`ni chaqiradi.

     Faqat egasi (404), bizdan 409 yo'q; GTS rad etsa 502. Takrorlash bepul.
   - `reprice/confirm/` → **narx o'zgargan holat uchun**. Avval yana
     `reprice_check`, va faqat u "o'zgardi" desa `POST
     /v1/content/reprice_confirm/ {order_number}`
     (`FlightAdapter.confirm_price`). Nega avval check: narx o'zgarmagan
     bo'lsa GTS'da tasdiqlaydigan qayta narxlangan taklif qolmaydi va confirm
     `400803` bilan rad etiladi ("Срок действия предложения после перерасчёта
     истёк", jonli 2026-08-25) — o'sha 502 `payment/`ni butunlay bloklab
     qo'ygan edi. Narx o'zgarmagan bo'lsa bu chaqiruv **zararsiz, lekin
     keraksiz**: confirm yuborilmaydi, order o'z narxida tasdiqlanadi.
     Narx saqlangandan **farq qilsa** → `amount/currency` yangilanadi,
     event `price.repriced {from, to}`, ochiq `started` urinish → `abandoned`
     (eski summa uchun yuborilgan kod yangi summani tasdiqlay olmasligi
     kerak). So'ng **`GET /v1/orders/{n}/`** — GTS o'z yozuvini
     qayta narxlab bo'ldi, order to'liq qayta o'qiladi (best-effort: o'qib
     bo'lmasa WARNING `gts_read_after_confirm_failed`, tasdiq davom etadi).
     Qulf ostida, tartib: GET `CB/VO/STATUS_VOID` desa → `cancelled/expired`
     + **409 `offer_expired`** (`payment/` topadigan xuddi shu yo'l; tasdiq
     yozilmaydi); aks holda narx yoziladi, `price_confirmed_at = now`, **keyin**
     `apply_snapshot` (status, muddat, `gts_response`; `amount` emas — §1).
     GTS javobidagi narx — **yakuniy** (ticketing shuni yechadi): check'dan
     farq qilsa ham shu saqlanadi (WARNING `gts_confirmed_other_price`),
     event `price.confirmed {amount, currency}`. Javob — order **to'liq yangilangan narx bilan**:
     `payment.amount`, `order.amount`, `order_data.price_info/price_details`,
     yangi `pay_before`; mijoz to'lov ekranida shuni ko'radi.
   - **Narx o'zgarmaganda (jonli, 2026-08-25 — oddiy holat).** `reprice_check`
     `status: success`, `data.price_changed: false` va **provayderning o'z
     valyutasidagi narxi** bilan javob beradi: 343.04 USD ga bron qilingan
     order uchun `294.00 EUR`, GTS'ning o'z order yozuvi esa 343.04 USD
     bo'lib turadi. Ya'ni raqam bu orderning narxi emas — hukm `price_changed`.
     Ba'zi javoblarda narx umuman bo'lmaydi (`data: null`, `{}` yoki
     `price_info`siz — shuning uchun adapter butun envelope'ni
     `post_envelope` bilan o'qiydi: `post` `data` obyektini talab qilib,
     oddiy holatning o'zini 502 ga aylantirardi). Ikkalasi ham **javob**,
     xato emas:
     - `reprice/` → `changed: false`, `new_price = old_price` (orderdagi narx
       — bugungi narx), GTS bergan `price_info` esa baribir passthrough
       bo'lib o'tadi (narx bermagan bo'lsa `{}`). Agar orderda ham narx
       bo'lmasa (`amount = NULL`) → 502 "carried no price": ikkala tomon ham
       raqam aytmadi, savol javobsiz qoldi.
     - `reprice/confirm/` (chaqirilsa) → GTS'ga confirm **yuborilmaydi** (u
       400803 bilan rad etadi), tasdiq **o'z kuchida**:
       `price_confirmed_at = now`, `amount`/`price_response` tegilmaydi,
       `price.repriced` eventi yo'q, ochiq urinish bekor qilinmaydi (summa
       o'zgarmadi), valyuta tekshiruvi ham o'tkazilmaydi (tekshiradigan raqam
       yo'q). Lekin bu qadam endi shart emas — `reprice/`ning o'zi yopadi.
     - Jonli tekshirilgan zanjir (2026-08-25, order 4903): `search → offers →
       verify → booking (343.04 USD) → reprice (changed:false) →
       reprice/confirm (200) → payment → payment/confirm → ticketing` — GTS
       `PW` (chipta chiqarilmoqda), `ticket_date` qo'yilgan. `verify` ba'zi
       offerlarda GTS'ning o'zidan `100500` ("Неизвестная ошибка со стороны
       поставщика") qaytaradi — boshqa offerni sinash kerak, bu bizning
       tomondan emas.
   - GTS rad etsa (`status: error`) → 502, GTS matni `meta.upstream`da, hech
     narsa yozilmaydi. **Confirm'da valyuta orderning valyutasidan farq
     qilsa** (hujjat 12–13-betlarda check'ni `UZS`, confirm'ni `USD` bilan
     chizadi — xato bosmami, yo'qmi, noma'lum) → 502
     "answered in USD; this order is priced in UZS", ERROR
     `gts_reprice_currency_mismatch`, **hech narsa yozilmaydi** — boshqa
     valyutadagi raqam yangi narx emas, kartaga ham depozitga ham yetmasligi
     kerak. Ikkalasi `RateLimit("payment")` ostida, idempotency kaliti yo'q
     (takrorlanadigan).
   - Hujjat aytmaydigan, jonli serverda tekshirilmagani: `price`
     ("Итоговая цена", 16-bet) `fee_amount`/`service_fee_amount`ni o'z ichiga
     oladimi — bron paytidagidek yakuniy deb olinadi.
   - **`payment/` `price_confirmed_at` bo'sh orderni 409 bilan rad etadi**
     ("The price has not been checked with GTS") — qulfdan oldin ham, qulf
     ostida ham. Ya'ni `reprice/` (va narx o'zgargan bo'lsa `reprice/confirm/`)
     hech bo'lmasa bir marta chaqirilgan bo'lishi shart.
   - Ticketing (`§4b`) o'zgarmagan: narx qadamlari to'lovdan oldin bo'lib
     o'tgan, `ticket()` faqat `POST /v1/content/ticketing/` yuboradi.
1. **`POST /public/orders/{id}/payment/`** — body `{method, card_id}` yoki
   `{method, card: {number, expire}}` (karta — aynan bittasi). `method` —
   **majburiy**, site-config `payment_methods` dagi `code` (masalan `payme`);
   default yo'q, fallback yo'q: yoqilmagan yoki notanish kod → **422**
   (`field=method`), hech biri yoqilmagan bo'lsa → 502. `card` bilan
   `save: true` —
   provider kartani qabul qilgach (`start` muvaffaqiyatli) u `customer_cards`
   ga yoziladi, urinish unga bog'lanadi (`card_id`), to'lov `paid` bo'lganda
   `last_used_at` bosiladi; rad etilgan karta saqlanmaydi; allaqachon bor
   karta — xato emas, o'sha qator ishlatiladi; `card_id` bilan `save` → 422. Tartib: provider va GTS client
   **qulfdan oldin** olinadi (ular sessiyani commit qiladi); `GET
   /v1/orders/{n}/` — bron tirikmi (narx `price_response`dan — §1; o'qish
   `amount`ni faqat narxi tasdiqlanmagan orderda yangilaydi); GTS
   `CB/VO/STATUS_VOID` desa → `cancelled/expired` + **409
   `offer_expired`**. Qulf ostida: tekshiruvlar, eski `started` urinish →
   `abandoned`, yangi `started` qatori (**claim**, provider'dan oldin),
   commit. So'ng `provider.start()` → reference (shifrlab) va `phone_hint`
   yoziladi. Provider rad etsa (`PaymentDeclined`) → urinish `failed`,
   `payment_status=failed`, javob 200 (`payment.status=failed`, `error`);
   provider javob bermasa → xuddi shu + 502/504 (hali pul yechilmagan).
2. **`POST /public/orders/{id}/payment/resend/`** — body `{payment_id}`. Xuddi
   shu ochiq urinishga (`payment_id` mos kelishi shart) kodni **qayta**
   yuboradi — yangi karta ro'yxatga olinmaydi, yangi urinish ochilmaydi, pul
   yechilmaydi. Provider urinishning o'z `provider` kodi bo'yicha topiladi
   (`provider_for_attempt`), xuddi confirm kabi qulfdan **oldin** — resolve
   commit qiladi. Qulf ostida: urinish ochiq va `payment_id` mos; `confirming`
   bo'lsa → 409 (kod chiqib bo'lgan bo'lishi mumkin, qayta yuborilmaydi);
   method panel tomonidan o'chirilgan bo'lsa → xuddi confirm bilan bir xil
   `abandoned` + 409. Payme uchun `cards.get_verify_code` yana chaqiriladi
   (`start`dagi xuddi shu yordamchi — yangi chek yoki karta yo'q); Demo va
   Sandbox — no-op, chunki ularning kodi statik/deterministik va qayta
   yuborishga hojat yo'q. Provider rad etsa (`PaymentDeclined`) → urinish
   `failed`, javob 200 (`start`dagi bilan bir xil qoida); provider javob
   bermasa (`UpstreamError`/`UpstreamTimeout`) → urinish **o'zgarishsiz
   qoladi** (`start`dan farqli — eski kod hali ham ishlashi mumkin) va
   502/504 qaytadi. Alohida "kutish" holati yo'q — yagona cheklov
   `RateLimit("payment")` (daqiqasiga 10 so'rov, `payment/` va
   `payment/confirm/` bilan bir xil).
3. **`POST /public/orders/{id}/payment/confirm/`** — body `{payment_id, otp}`.
   Provider urinishning o'z `provider` kodi bo'yicha topiladi
   (`provider_for_attempt`), qulfdan **oldin** — resolve commit qiladi. Qulf
   ostida: urinish ochiq va `payment_id` mos; `confirming` bo'lsa — provider
   chaqirilmaydi, joriy holat qaytadi (o'qish); urinish boshlangan method
   panel tomonidan o'chirilgan bo'lsa → `abandoned` + 409 "start again" —
   `start` pul yechmaydi, yangi urinish yoqiq method bilan ochiladi. Bizdagi
   muddat o'tgan bo'lsa order **bekor qilinmaydi** — order allaqachon bor,
   muddat esa GTS uch xil yozadigan maydonning taxminiy o'qilishi: qulfdan
   **oldin** `GET /v1/orders/{n}/`; GTS `CB/VO/STATUS_VOID` desa →
   `abandoned` + `cancelled/expired` + 409 `offer_expired`; hali `BO` bo'lsa
   muddat yangilanadi va to'lov davom etadi (sweep qoidasi bilan bir xil);
   o'qilmasa → 502/504, pul yechilmaydi. Urinish `confirming`, **commit**
   — charge provider'ga hech qachon ikki marta ketmaydi. So'ng
   `provider.confirm()` qulfsiz; natija `settle_attempt` bilan **qayta qulf +
   qayta o'qish** ostida qo'llanadi (sweep bilan poyga): `paid` → urinish
   `paid`, `payment=paid`, karta `last_used_at`; `failed` → `payment=failed`,
   javob 200; exception (javob noma'lum) → `confirming` qoladi, javob 200
   `payment.status=processing` (order `status` esa `booked` bo'lib
   qolaveradi — §2).

Provider tanlovi — **mijozniki**: panel bir nechta providerni birga yoqadi
(adaptersiz kodni yoqish → 422 "not available in this release"), site-config
`payment_methods` yoqiqlarini ko'rsatadi (`code, title, logo_url`,
`sort_order` tartibida), mijoz 1-qadamda `method` bilan bittasini nomlaydi.
`payments.service.payment_provider(method)`: test override (kodi mos
bo'lmasa 422) → yoqilgan qatorlar ichidan `method` bo'yicha (`ADAPTERS`:
hozir **Payme** va **Demo**) → hech biri yoqilmagan bo'lsa: `DEBUG=true` va
`method=sandbox` → **sandbox** (site-config ham shu holatda ro'yxatga faqat
sandbox'ni chiqaradi), aks holda 502. Urinishni yakunlash tomonida
`provider_for_attempt(code)` — urinish qatoridagi kod bo'yicha; topilmasa
`None`, chaqiruvchi o'zi hal qiladi. Sandbox kodlari: `000000` paid ·
`111111` declined · `222222` timeout (noma'lum) · `333333` pending ·
boshqasi — noto'g'ri kod. Sandbox'da ham `resend` — no-op (kodlar
deterministik, qayta yuborishga hojat yo'q).

**Payme** (`providers/payments/payme.py`, Subscribe API, JSON-RPC
`POST {base}/api`). Merchant — loyiha egasi, Payme Business kassasi bilan;
mijoz ilovadan chiqmaydi. Port qadamlari Payme metodlariga shunday tushadi:

| Port | Payme | Izoh |
|---|---|---|
| `start` | `receipts.create` (tiyin, `account: {account_field: order.id}`, ixtiyoriy `detail`) → `cards.create {save: false}` → `cards.get_verify_code` | chek **birinchi**: karta tegilmasdan va SMS ketmasdan `merchant_id:key` tekshiriladi. Token bitta urinish uchun — saqlangan karta bizda PAN (shifrlangan), Payme tokeni emas. `reference` = `{v, token, receipt}` JSON (orders shifrlab saqlaydi); `phone_hint` = Payme'ning masklangan raqami |
| `resend` | `cards.get_verify_code` (yana, xuddi shu token bilan) | faqat kodni qayta yuboradi — yangi chek yoki karta yo'q; `reference` o'zgarmaydi. Rad javobi (`sent:false` yoki biznes xato) `start`dagidek `PaymentDeclined` |
| `confirm` | `cards.verify {token, code}` → `receipts.pay {id, token}` | `receipts.pay` urinish boshiga **bir marta** (`confirming` commit'i kafolatlaydi). Chek holati 4/5 → `paid`; 50 → `failed`; boshqasi → `pending` |
| `status` | `receipts.check {id}` | yo'qolgan `receipts.pay` javobi uchun |
| `probe` | `receipts.get_all` (oxirgi 24 soat, `count: 1`) | panel "test" tugmasi; pul ko'chirmaydi |

`X-Auth`: `cards.*` → `{merchant_id}`, `receipts.*` → `{merchant_id}:{key}`.
Faqat `UZS`; boshqa valyuta yoki `0` narx — hech narsa chaqirilmasdan 502.

**Demo** (`providers/payments/demo.py`) — pul yechmaydigan namoyish metodi.
Sandbox'dan farqi: `DEBUG`ga bog'liq emas, panelda xuddi Payme kabi yoqiladi
va boshqa providerlar bilan yonma-yon site-config ro'yxatida chiqadi.
Bitta sozlama (`fields`): `otp` — to'laydigan yagona statik kod, default
`123456`, panelda ochiq ko'rinadi (sir emas — operator namoyishda o'qiydi).
`start` har qanday kartani oladi, hech narsa yechmaydi; `resend` — no-op:
kod statik bo'lgani uchun hech narsa qilinmaydi, xuddi shu `phone_hint`
qaytariladi; `confirm` da kod mos → `paid`, aks holda `failed` "wrong code";
`status` → `pending` (stateless); `probe` doim ok. To'lovdan keyin ticketing
odatdagidek GTS'ga boradi (demo
server GTS test bilan). Kod kengaytmasi migratsiya bilan:
`payment_providers.code` CHECK'iga `demo` qo'shildi
(`20260824_1500_payment_provider_demo_code.py`); qatorni birinchi o'qish
o'zi yaratadi.

Xato qoidasi (port: **faqat natija noma'lum bo'lsa raise**): tarmoq/timeout,
HTTP ≠ 200, JSON emas, kutilmagan shakl, JSON-RPC kod `≤ -32000` (auth,
tizim) → `UpstreamError`/`UpstreamTimeout`. JSON-RPC `-31xxx` (biznes rad):
`receipts.create` da → `UpstreamError` (summa yoki kassa — karta emas, mijozga
"boshqa karta" deyilmaydi); `cards.create`/`get_verify_code` da →
`PaymentDeclined` (200, `payment.error` = Payme matni); `confirm` da →
`failed` natija. `cards.verify` dagi tizim xatosi ham `failed` (hali hech
narsa yechilmagan), `receipts.pay` dagi tizim xatosi — noma'lum → raise,
sweep chekni o'qiydi. Matn: `message` satr yoki `{uz, ru, en}` → uz, ru, en
tartibida; 300 belgi. Log'ga token, karta, kod hech qachon tushmaydi.

Panel sozlamalari (`/admin/integrations/payments/payme/`, `fields` bilan
e'lon qilinadi, noto'g'ri kalit 422): `merchant_id`, `key` (yagona sir,
maskalanadi), `environment` (`production` | `test`), `account_field`
(default `order_id`), ixtiyoriy fiskal `fiscal_title`, `fiscal_code` (ИКПУ),
`fiscal_vat_percent`, `fiscal_package_code`, `fiscal_units` — to'ldirilsa
`detail` yuboriladi. `POST /admin/integrations/payments/{code}/test/` —
saqlangan sozlamalar bilan `probe`; `ok: false` + sabab javob, 502 emas;
`last_tested_at/ok/error` yoziladi. Yoqish uchun `merchant_id`, `key` va shu
relizda adapter shart; bir vaqtda bir nechta provider yoqiq turishi mumkin.
Demo uchun yoqish sharti — `otp` saqlangan bo'lishi.

Sweep (`app/tasks/orders.py::reconcile_orders`, beat 30 s, har qator o'z
tranzaksiyasida, `SKIP LOCKED`):

- `confirming` va 120 s dan eski → `provider.status()`; `paid/failed` →
  qo'llanadi; `pending` → kutiladi; 15 daqiqadan keyin ham `pending` →
  `failed` "the provider never confirmed this charge" + `ERROR
  payment_unconfirmed` (support provider panelini tekshiradi). Urinishni
  **faqat uni boshlagan provider** yakunlaydi: adapter `attempt.provider`
  kodi bo'yicha topiladi (`provider_for_attempt`); topilmasa (method
  o'chirilgan, adapter bu relizda yo'q, sozlama chala) qator o'tkazib
  yuboriladi (`ERROR payment_provider_unavailable`) — u provider pul yechgan
  bo'lishi mumkin, uni odam hal qiladi; qolgan savollar (ticketing, muddat)
  ishlayveradi. `status()` 15 daqiqadan keyin ham o'qilmasa log WARNING'dan
  ERROR'ga ko'tariladi, holat o'zgarmaydi.
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

## 4c. Bekor qilish — avval GTS, keyin biz

`POST /public/orders/{id}/cancel/` (body yo'q) — mijoz **to'lanmagan** bronni
qo'yib yuboradi. GTS hayot siklida bu `cancel`: "отмена брони до выписки"
(`docs/gts-api-v1.4.pdf`, 12-bet). Chiqarilgan chipta bu yerda qaytarilmaydi —
u `void` yoki `refund`, ikkalasi ham bu relizda yo'q.

Tartib **GTS avval**: joyni ushlab turgan GTS, bizdagi qator esa uning yozuvi.
Teskarisi bekor qilingan orderni GTS hali ushlab turgan joyga qarshi qo'yardi —
muddat o'tguncha.

1. Egasi (`404`), so'ng `_require_payable`: pul qabul qila oladigan order —
   broni hali bizniki bo'lgan order, predikat aynan bir xil. Ochiq urinish
   `confirming` bo'lsa **409**, GTS'ga borilmasdan.
2. `POST /v1/content/cancel/ {order_number}` (`FlightAdapter.cancel`). Javob
   **o'qilmaydi**: GTS uni uchta konvertining qaysi biridir bilan qaytaradi va
   u **status olib yurmaydi** — faqat `order_number`, `cancel_booking_date`,
   `ticket_date`.
3. **`GET /v1/orders/{n}/` — har holatda.** GTS hozir nima ushlab turganini
   faqat shu aytadi, va u rad javobini ham hal qiladi: allaqachon qo'yib
   yuborilgan orderni GTS ikkinchi marta bekor qilishdan bosh tortadi, bu esa
   **bekor qilingan order**, xato emas. Rad javobi (yoki timeout) + bron tirik
   → 502/504, hech narsa yozilmaydi.
4. Qulf ostida: `transition(status=cancelled, cancel_reason=customer)`, ochiq
   `started` urinish → `abandoned`, `apply_snapshot` (`gts_status = CB`), event
   `order.cancelled {gts_status}`, commit.

Chekka holatlar:

- **Allaqachon `cancelled`** → GTS'ga umuman borilmaydi, order shundayligicha
  qaytadi (200). Endpoint shu bilan idempotent — `Idempotency-Key` kerak emas.
- **GTS `success` dedi-yu, o'qiganda hali `BO`** → POST — harakat, o'qish —
  faqat tasdiq: bekor qilish o'z kuchida qoladi, farq WARNING
  `gts_still_holds_after_cancel` bilan yoziladi.
- **O'qib bo'lmadi** (cancel muvaffaqiyatli bo'lsa) → bekor qilish baribir
  yoziladi, `gts_status` eski holida qoladi, WARNING
  `gts_read_after_cancel_failed`.
- **Poyga.** GTS'ga cancel ketgandan keyin, qulfgacha bo'lgan oniyda to'lov o'tib
  ketsa — `transition` 409 beradi, ERROR `cancel_raced_payment`. Order
  `paid + booked` bo'lib qoladi; `ticket_paid_pending` chipta so'raydi, GTS `CB`
  order uchun rad etadi → `ticketing_failed` → support. `sync/` buni hoziroq
  ko'rsatadi.
- **Commit yiqilsa** (GTS bekor qildi, biz yozolmadik): keyingi `payment/` GTS'ni
  o'qib `offer_expired` beradi va orderni `cancelled/expired` qiladi; sweep
  muddat o'tgach o'zi topadi; `sync/` esa darhol.
- Booking **idempotency** 24 soat yashaydi: bekor qilib, **aynan o'sha** booking
  body'si qayta yuborilsa yangi bron emas, bekor qilingan order qaytadi. Yangi
  qidiruvdan keyin `offer_id` boshqa bo'ladi, shuning uchun amalda uchramaydi.

Staff cancel'i yo'q — `lifecycle` unga ruxsat bersa ham, to'langan bronni bekor
qilish refund savoli va u 4-bosqichda qo'lda hal qilinadi.

## 4d. Marshrut kvitansiyasi — link orderning o'zida

Chipta chiqqach yo'lovchi o'zi bilan olib yuradigan hujjatni **GTS chizadi**
(`GET /v1/receipt/pattern/view/?order_number={n}&product=flight`,
`docs/gts-api-v1.4.pdf`, 14-bet). Mijoz ilovasi yoki sayt uni **o'zi** yuklab
oladi, shuning uchun order javobi tayyor linkni olib yuradi:

```
ticketing.receipt_url =
  {gts_credentials.base_url}/v1/receipt/pattern/view/?order_number={n}&product=flight
```

- **To'liq URL**, chala yo'l emas: ilova order javobidan boshqa hech narsasiz
  ocha oladi. Host — **bazadagi faol GTS credential'ining `base_url`** i
  (`integrations.service.gts_base_url`, parol ochilmaydi), demak sandbox va
  prod har biri o'z linkini beradi va kodda hech qanday domen yozilmaydi
  (PROJECT.md §7). Yo'lning o'zi vertikalniki —
  `ProductAdapter.receipt_url()`, flight uchun yuqoridagi manzil.
- **Faqat `ticketing_status = ticketed`.** Undan oldin chizadigan narsa yo'q,
  shuning uchun `receipt_url = null` — tugma shu maydonni kutadi. Link
  saqlanmaydi: har javobda qaytadan yig'iladi.
- `&passenger_index=` — bitta yo'lovchi nusxasi; GTS **noldan** sanaydi,
  ko'rsatilmasa hujjat butun order uchun. Ilova uni o'zi qo'shadi.
- Admin javobida ham xuddi shu link chiqadi (`OrderAdminOut` mijoz shaklidan
  meros oladi) — support GTS kabinetiga kirgan brauzerda ocha oladi.

**Muhim:** GTS hujjati (5-bet) "token'ni **barcha** API metodlariga cookie
bilan uzatish kerak" deydi. Agar `receipt/pattern/view/` sessiyasiz ochilmasa,
link brauzerda ishlamaydi — o'shanda quyidagi marshrut ishlatiladi; u aynan
shu hujjatni **bizning** sessiyamiz bilan olib beradi.

### `GET /public/orders/{id}/receipt/` — o'sha hujjat, biz olib beramiz

- **Saqlanmaydi** — na Postgres'da, na Redis'da, na diskda. GTS hujjatni
  o'zida turgan orderdan chizadi: hozir so'ralgan nusxa — chiptaning hozirgi
  holati, bizdagi nusxa esa GTS orderga tegishi bilan eskiradi. Qidiruv
  natijalari qoidasi, faylga qo'llangani.
- **Faqat `ticketing_status = ticketed`.** Undan oldin 409, GTS'ga umuman
  borilmasdan — javobni o'z ustunimiz allaqachon biladi.
- **Javob — fayl**, envelope emas: `api/envelope.py` JSON bo'lmagan javobni
  o'ramaydi. GTS'ning `Content-Type` i o'z holicha, `Content-Disposition` da
  `receipt-{PNR}.pdf` (PNR — GTS matni, shuning uchun faqat harf va raqamlari
  o'tadi). Kutilmagan tur — WARNING `gts_receipt_unexpected_type`, fayl
  `application/octet-stream` bo'lib saqlash uchun beriladi; brauzer uni
  bizning origin'imizda ochmaydi (`nosniff` + `sandbox` CSP, `uploads`
  marshrutidagi kelishuv).
- `?passenger_index=` — linkdagi bilan bir xil, faqat query emas, bizning
  parametrimiz (manfiy son — 422, GTS'ga borilmasdan).
- GTS rad etsa yoki javob bermasa — 502/504, GTS matni `meta.upstream` da.
  Hech narsa o'zgarmaydi: bu sof o'qish, sweep ham, `transition` ham yo'q.

Portda bu `ProductAdapter.receipt()`, u esa `GtsClient.download()` ustida —
GTS konverti to'xtamaydigan yagona chaqiruv, chunki muvaffaqiyat konvert emas,
bayt. Rad javobi esa baribir JSON konvert bo'lib keladi (ko'pincha HTTP 200
ostida), shuning uchun JSON kelsa u hujjat emas: `_translate` uni odatdagi
`upstream_error` ga aylantiradi. Bo'sh javob ham xato — baytsiz kvitansiya
kvitansiya emas.

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
  "ticketing": { "status": "pending", "requested_at": null, "ticketed_at": null, "tickets": [], "error": null,
                 "receipt_url": null },
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
| POST | `/public/orders/{id}/payment/` | customer | 2 — `{method, card_id \| card}`; kodni yuboradi; 200, `payment.status=awaiting_otp`, `payment_id`, `phone_hint`; yoqilmagan `method` → 422 |
| POST | `/public/orders/{id}/payment/confirm/` | customer | 2/3 — `{payment_id, otp}`; 200; `paid` bo'lsa o'sha so'rovda ticketing: `ticketing.status` `ticketed` · `processing` · `failed` |
| GET | `/public/orders/{id}/receipt/` | customer | 3 — МК'ni **biz** olib beramiz (§4d): javob envelope emas, faylning o'zi; `?passenger_index=` (noldan); ticketing tugamagan order → 409. Odatda kerak emas — `ticketing.receipt_url` GTS'ning to'liq linki |
| POST | `/public/orders/{id}/cancel/` | customer | 1 — body yo'q (§4c): avval `POST /v1/content/cancel/`, keyin `cancelled/customer`; allaqachon bekor qilingan order → 200 va GTS'ga borilmaydi; to'langan, ticketing'dagi yoki `confirming` urinishli order → 409 |
| GET | `/admin/orders/` | staff | qatorlar mijoz `status` + xom `booking_status`, `payment_status`, `ticketing_status`, sabab uchun `cancel_reason`, `ticketing_error`, `updated_at`; filtrlar: `status` (§2 dagi oltita so'z — SQL `stage_of` ning o'zidan hosil qilinadi, `lifecycle.stage_filter`) va uchta xom ustun, birga ishlaydi; **support inbox = `status=ticketing_failed`** — ekrani "supportga murojaat qiling" deydigan har bir order, puli qaytgani emas; `ordering` — `created_at` yoki `updated_at` (`-updated_at` — eng so'nggi o'zgargan birinchi); `search` — PNR yoki GTS raqami |
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

- `GET /admin/orders/{id}/receipt/` — МК'ni staff token bilan yuklab olish.
  Hozir admin javobidagi `ticketing.receipt_url` GTS'ning linki (brauzerda GTS
  sessiyasi bo'lsa ochiladi), mijoz marshruti esa staff token bilan 403 beradi.
  Kerak bo'lsa xizmat funksiyasi tayyor — `service.order_receipt()` dan egalik
  tekshiruvini olib tashlagan varianti yetadi.
- ~~`POST /public/orders/{id}/cancel/`~~ — §4c: mijoz to'lanmagan bronni avval
  GTS'da, keyin bizda bekor qiladi (`cancelled/customer`).
- Email xabarnomalar (`ticket_waiting`, `ticketed`, `ticketing_failed`) —
  `customers.service._send` namunasi; commit'dan keyin, faqat event qaytgan
  yo'l yuboradi. Hozir mijoz xabarni ilovada (`order.message`) ko'radi.
- `providers/payments/click.py` — `start/confirm/status/probe`; `ADAPTERS`
  va `FIELDS` jadvallariga bir qator (Payme namuna).
- Provider refund API (`refund()` porti) — hozir support provider kabinetida
  qaytaradi va `refund/` bilan belgilaydi.
- ~~`reprice_check`~~ — §4a 0-qadam: `reprice/` + `reprice/confirm/`, mijoz
  ilovasi to'lovdan oldin chaqiradi; `payment/` tasdiqlanmagan narxni rad etadi.
- ~~Contract test sweep~~ — `tests/test_openapi.py` (trailing slash, envelope,
  xato shakllari, ikki token sxemasi, har endpoint tavsifi); CLAUDE.md jadvali
  tozalandi.
