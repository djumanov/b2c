# GTS (Global Travel Space) — B2B platforma haqida umumiy ma'lumot

Bu hujjat `EASY_GATEWAY_V5_5_16.postman_collection.json` (GTS gateway API, 393 ta request) va
`/Users/djumanov/GTS/` workspace'idagi repo'lar tahlili asosida yozilgan.

EasyBooking B2C API'si haqida alohida hujjat: [OVERVIEW.md](OVERVIEW.md).

---

## 1. GTS nima?

**GTS (Global Travel Space)** — sayohat mahsulotlarini sotish uchun **B2B platforma**.
U o'zi chipta sotmaydi — u **yetkazib beruvchilar (GDS, aviakompaniya, temir yo'l, sug'urta,
ekskursiya provayderlari) bilan chipta sotuvchilar o'rtasidagi qatlam**.

GTS quyidagilarni beradi:

- **Yagona API** — o'nlab turli provayder API'lari bitta kontrakt ortiga yashiriladi;
- **Agentlik ierarxiyasi** — turagentliklar bir-biriga shartnoma (agreement) asosida qayta sotadi,
  narx shu zanjir bo'ylab markup bilan shakllanadi;
- **Qoidalar (rules) mexanizmi** — kim qaysi provayderni ko'radi, qanday ustama qo'yiladi;
- **Buyurtma, balans, hisobot, kvitansiya** — sotuvdan keyingi butun operatsion qism.

GTS'ning mijozlari — turagentliklar (agent kabineti orqali), korporativ mijozlar, va
**B2C loyihalari**. **EasyBooking** ana shunday B2C loyihalardan biri: u GTS'ning iste'molchisi,
GTS kontraktini o'z foydalanuvchilariga o'rab ko'rsatadi. Shunga o'xshash boshqa loyihalar ham
xuddi shu API'ga ulanadi.

```
Turagentlik kabineti ─┐
Korporativ mijoz ─────┼──► GTS ──► Provayderlar (GDS, aviakompaniya, RZD, sug'urta, ...)
EasyBooking (B2C) ────┤
Boshqa B2C loyihalar ─┘
```

---

## 2. Arxitektura

Integratsiya repo'laridagi rasmiy zanjir:

```
Ticket Company        GTS Core / Gateway       Module                Integration          Provider
(B2C sayt,      ──►   (auth, agent,      ──►   (mahsulot       ──►   (bitta         ──►   (Amadeus,
 agent kabineti)       shartnoma,              vertikali:            supplier'ga           Kiwi, DRCT,
                       qoidalar, buyurtma)     flight, railway,      moslashuv)            UzRailways, ...)
                                               excursion, ...)
```

| Qatlam | Javobgarligi |
|---|---|
| **Client** | B2C sayt yoki agent kabineti. Faqat gateway bilan gaplashadi |
| **Gateway / Core** | Autentifikatsiya, agent va shartnoma modeli, qoidalar, buyurtmalar, balans, hisobotlar. Mahsulot so'rovlarini tegishli modulga uzatadi |
| **Module** (content service) | Bitta mahsulot vertikali. Ko'p supplier'ni parallel so'raydi, natijalarni keshlaydi, agent zanjiri bo'yicha narxlaydi, buyurtma hayotiy siklini boshqaradi |
| **Integration** | Bitta supplier API'siga moslashtiruvchi mikroservis. Provayder javobini GTS sxemasiga o'giradi |
| **Provider** | Tashqi supplier API'si |

---

## 3. Gateway API (`EASY_GATEWAY`)

- **Host**: `https://api2.globaltravel.space` (collection'dagi `{{host}}`).
  Boshqa muhitlar: `https://api.eb-system.uz/v2` (live), `.../supplier`, `.../order`,
  `{{dev}}`, `{{localhost}}`, charter uchun `http://api.globaltravel.space:8032`.
- Barcha yo'llar **`/v1/…`** ostida.
- **Javob envelope'i**:

  ```json
  { "status": "success", "message": "Все ок.", "id": "<uuid>", "time": "...", "total": 2, "data": {} }
  ```

- **Autentifikatsiya**: `POST /v1/auth/signin/` (email + parol). Ikki bosqichli tasdiq
  **yoqilgan bo'lsa** — `PUT /v1/auth/signin/confirm/` (email + kod). Ikkalasi ham bir xil
  javob beradi:

  ```json
  { "data": { "session_key": "f771d913342bec7e9d6572ef9c8783",
              "expired_time": "2023-07-06T15:55:06.736536",
              "timeout_minutes": 360 } }
  ```

  Ya'ni **sessiya muddati aniq ma'lum** (odatda 6 soat) — sessiya menejeri 401 ni kutmasdan,
  muddat bo'yicha oldindan qayta kirishi mumkin. Chiqish — `POST /v1/auth/logout/`.
  Ba'zi so'rovlarda qo'shimcha `agent-uid` header'i uzatiladi.
- **Ikki bosqichli tasdiq — akkaunt bayrog'i**, protokol talabi emas: akkaunt
  `two_factory: false` / `type_factory: null` bilan yaratiladi yoki shunday o'zgartiriladi
  (`/v1/base/root/`, `/v1/users/law/`; turlari `/v1/users/settings/type-factory/` da).
  Mashina akkaunti uchun kerak bo'lgan narsa aynan shu ([PROJECT.md](PROJECT.md) D1) —
  demak to'siq **GTS tomondagi sozlama**, bizning kodimizdagi ish emas.
- Xuddi shu akkaunt sozlamasida yana ikkitasi bor va ikkalasi ham o'rnatmaga ta'sir qiladi:
  **`white_list`** — IP oq ro'yxati (client serverining IP'si kiritilishi kerak) va
  **`is_single`** ("Разовая авторизация") — yoqilgan bo'lsa bir vaqtda faqat bitta sessiya,
  ya'ni o'sha akkaunt bilan GTS panelига brauzerdan kirilsa backend sessiyasi uziladi.

### Bo'limlar

| Bo'lim | Prefiks | Nima |
|---|---|---|
| **users** | `/v1/auth/`, `/v1/users/`, `/v1/base/` | Ro'yxatdan o'tish + tasdiq, 2FA turlari, biznes-akkauntlar (yuridik/jismoniy), korporativ mijozlar (xodimlar, cost-center, guruhlar), xodim rollari, root/admin akkaunt |
| **agreements** | `/v1/contract/` | Shartnomalar: kiruvchi/chiquvchi, qabul/rad etish, balans, to'ldirish, tranzaksiya tarixi, **freeze** (summa muzlatish + chat), provayderlarni shartnomaga bog'lash, arxiv |
| **general** | `/v1/any/upload/` | Fayl yuklash (shartnoma hujjatlari va h.k.) |
| **supplier** | `/v1/suppliers/`, `/v1/providers/`, `/v1/product/`, `/v1/booking-system/` | Yetkazib beruvchilar, booking system'lar va ularning turlari, mahsulotlar, provayderlar (balans, topup/expend, tarix, agent biriktirish) |
| **orders** | `/v1/orders/` | Buyurtma yaratish/tahrirlash, tarix, shartnoma bo'yicha buyurtmalar, yo'lovchi ma'lumotlari (SSR, OSI, DOCO, DOCA, mile card) |
| **content** | `/v1/content/` | Aviachipta to'liq hayotiy sikli — 25 ta metod |
| **rules** | `/v1/rules/` | Qoidalar konstruktori (pastda alohida bo'lim) |
| **reports** | `/v1/reports/main/` | Asosiy ko'rsatkichlar, sozlanadigan maydonlar, xls eksport, analitik hisobot |
| **currency / rates** | `/v1/exchange/`, `/v1/rate/` | Kurs integratsiyalari, agent kurslari, provayder kurslari |
| **community** | `/v1/community/` | B2B e'lonlar taxtasi: guruhlar, e'lonlar, shikoyatlar, chat |
| **chat-partner** | `/v1/partner/query/` | Agentlararo so'rovlar, chat, baholash (estimation) |
| **receipt** | `/v1/receipt/` | Kvitansiya shablonlari va sozlamalari (logo, IRC) |
| **charter** | `/v1/charter/` | Charter reyslar: ro'yxat/guruh, pattern (sana, tarif, qoida), charter content |
| **esim** | `/v1/esim/` | search / offers / offer, buyurtmalar, hisobotlar |
| **insurance** | `/v1/insurance/` | search / offers / upsell / get-offer / calculate / ticketing / void, buyurtmalar, hisobotlar |
| **railways** | `/v1/railway/` | search / obtain-trains / train-details / booking / ticketing, buyurtmalar, kvitansiya, hisobotlar |

---

## 4. Aviachipta oqimi (`/v1/content/`)

Eng to'liq ishlangan vertikal — 25 ta metod:

```
search → offers → rules → upsell → verify → seatmap → additional-services
       → select-services → booking → retrieve
       → reprice_check → reprice_confirm
       → ticketing → seatorder → split
       → void │ cancel │ refund-check → refund-commit
       → buy-services │ update-order │ update-ocn
```

Buyurtma statuslari:

| Kod | Ma'nosi |
|---|---|
| `BO` | Booked — bron qilingan, chipta chiqarilmagan |
| `PW` | Pending / ticketing kutilmoqda |
| `TI` | Ticketed — chipta chiqarildi |
| `TE` | Ticketing error |
| `CB` | Cancelled — bron bekor qilindi |
| `VO` | Void — chipta bekor qilindi |
| `RF` / `PRF` | Refund / partial refund |

Boshqa kodlar: trip type `OW` (bir tomonlama) / `RT` (borish-qaytish) / `MT` (multi-city);
klass `E` (ekonom) / `B` (biznes) / `F` (birinchi).

**Qidiruv tanasi** (`POST /v1/content/search/`):

```json
{
  "directions": [{ "departure": "TAS", "arrival": "VKO", "departure_date": "2026-03-29" }],
  "adt": 1, "chd": 0, "inf": 0, "ins": 0,
  "class": "E", "flexible": true, "direct": false,
  "airlines": [], "passengers_ids": []
}
```

Javobda `request_id` qaytadi; keyingi barcha qadamlar shu `request_id` + `offer_id` bilan ishlaydi.

---

## 5. Boshqa mahsulot vertikallari

| Vertikal | Gateway prefiksi | Modul (repo) | Oqim / holat |
|---|---|---|---|
| Aviachipta | `/v1/content/` | `Modules/flight-content` | To'liq hayotiy sikl, ~50 supplier |
| Temir yo'l | `/v1/railway/` | `Modules/railways` | search → obtain-trains → train-details → booking → ticketing → reconcile → void/refund |
| Sug'urta | `/v1/insurance/` | — | search → offers → upsell → get-offer → calculate → ticketing → void |
| eSIM | `/v1/esim/` | — | search → offers → offer → order |
| Charter | `/v1/charter/` | `Modules/metacharter` | Charter reyslar boshqaruvi + pattern + o'z content oqimi |
| Ekskursiya | — | `Modules/excursion`, `excursion_extranet` | Sputnik8, Extranet provayderlari |
| Kruiz | — | `Modules/cruise` | Boshlang'ich holatda |

---

## 6. Agent, Provider va Shartnoma modeli

Bu GTS'ning eng o'ziga xos qismi — narx shu model orqali shakllanadi.

| Tushuncha | Ma'nosi |
|---|---|
| **Agent** | GTS'da chipta sotuvchi (turagentlik). Agentlar bir-biriga qayta sotadi |
| **Booking system** | Supplier API'si. Uning nomi integratsiyani tanlash kaliti |
| **Provider** | Agentning bitta booking system uchun sotuv kanali (`puid`). O'ziniki (`index=None`) yoki shartnoma orqali (`index` to'ldirilgan) |
| **Agreement (shartnoma)** | Ikki agent orasidagi qayta sotish kelishuvi: balans, valyuta, hisob-kitob davri, provayderlar ro'yxati |
| **Flow / chain** | Offer qayta sotiladigan agentlar zanjiri: ildiz agent supplier'dan sotib oladi, keyingi har bir agent oldingisidan shartnoma asosida oladi. Narx shu zanjir bo'ylab bosqichma-bosqich hisoblanadi |
| **`request_id` / `offer_id`** | Har bir qidiruv va har bir taklif uchun UUID; keshdagi barcha kalitlar shular bilan bog'langan |

**Balans mexanizmi**: shartnomaga summa qo'shiladi (`topup`), bron qilinganda tekshiriladi
(`provider/balance/check/`), chipta chiqarilganda hisobdan yechiladi (`provider/balance/expense/`).
Alohida **freeze** mexanizmi bor — summani muzlatish, uni tasdiqlash/tahrirlash va muzlatish
bo'yicha chat.

**Foydalanuvchi kodlari** (collection izohlaridan):

| Guruh | Kodlar |
|---|---|
| Status | `A` Active · `W` Wait · `C` Closed · `D` Deleted |
| Kabinet turi | `T` Technical · `B` Business |
| Foydalanuvchi turi | `R` Root · `A` Admin · `O` Agent · `S` Employee |
| Obyekt turi | `LEGAL` (yuridik) · `PHYSICAL` (jismoniy) |

---

## 7. Rules engine (qoidalar mexanizmi)

Har bir agent uchun **kim nimani ko'radi va narx qanday o'zgaradi** shu mexanizm orqali sozlanadi.
Gateway'da bu to'liq konstruktor sifatida berilgan:

```
collections  →  types  →  folders          (tashkiliy iyerarxiya)
events · actions · conditions · operations  (qurilish bloklari)
                        ↓
                     rules                  (status, tartib, tarix)
```

- Qoida turlari: **`search`** (qidiruv bosqichida) va **`fare`** (narx bosqichida);
  `product_uri` bo'yicha ajratiladi (`flights`, `railway`, ...).
- Qidiruv bosqichida **turn-off** qoidalari bitta provayderni o'chirishi (`PROVIDER_OFF`)
  yoki butun qidiruvni bloklashi (`ALL_OFF`) mumkin. Faqat aktiv (`status == "A"`) qoidalar
  qo'llanadi, oxirgi mos kelgan qoida g'olib bo'ladi, xato yozilgan qoida qidiruvni bloklamaydi.
- Modullar qoidalarni RULES mikroservisidan oladi va offer'larga chain bo'yicha qo'llaydi
  (`flight-content` da `rules_matcher/v3`, `railways` da `domain/` qatlami).

---

## 8. Repo xaritasi

GitLab: `git.globaltravel.space`, group **`backend-gts`**.

### Modules — mahsulot vertikallari

| Repo | Lokal papka | Nima |
|---|---|---|
| `modules/flights/content` | `Modules/flight-content` | ~50 airline/GDS supplier agregatori. FastAPI, `/content/`. Supplier'lar `importlib` bilan avtomatik topiladi. Redis kesh (provayder bo'yicha alohida — qisman natija berish uchun), PostgreSQL (auto-cancel, ticket-waiting navbatlari), Celery, Kafka logging |
| `modules/railways` (lokal: `railway-content`) | `Modules/railways` | Rail-ticketing gateway, `/railway/`. Qat'iy qatlamli: `api → services → domain → integrations → clients → infra`, mypy strict. Supplier'lar plagin, `DEMO` — offline test tayanchi |
| `integrations/excursion/content` | `Modules/excursion`, `Modules/content` | Ekskursiya agregatori (Sputnik8, Extranet), `/excursion/` |
| `modules/excursion_extranet` | `Modules/excursion_extranet` | Ekskursiya yetkazib beruvchilari uchun extranet |
| `metacharter` | `Modules/metacharter` | Charter offer'lar uchun tez storage/kesh qatlami: GTS javoblarini PostgreSQL'ga upsert bilan saqlaydi, soft delete orqali invalidatsiya qiladi |
| `modules/cruise` | `Modules/cruise` | Kruiz vertikali (boshlang'ich) |

### Micro — umumiy mikroservislar

| Repo | Lokal papka | Nima |
|---|---|---|
| `micro/orders` | `Micro/orders` | **easyorders** — Django, `/order/`. Buyurtmalarning markaziy ombori: yo'lovchilar, tarix, statuslar, hisobotlar, FTP eksport, dublikat tekshiruvi |
| `micro/static` | `Micro/static` | **Easystatistical** — kataloglar: aeroportlar, aviakompaniyalar, shaharlar, davlatlar, valyutalar, vaqt mintaqalari, hujjat turlari |
| `micro/route_receipt_generator` | `Micro/route_receipt_generator` | `/itineraryreceipt/` — kvitansiya generatori. Vertikallar: flights, insurance, railway, esim, excursions (transfers/cruises hali stub). PDF — Playwright + Chromium |
| — | `Micro/airports-json-maker` | Aeroport JSON'ini yig'uvchi yordamchi skript |

### Integrations — supplier moslashtiruvchilari

`backend-gts/integrations/flights/content/microsuppliers/` ostida:
`etm-system` (etalon shablon), `drct` (NDC), `kiwi` (Kiwi.com Tequila API), `integration-template`.

---

## 9. Ichki mikroservislar

Modullar gateway va bir-biriga HTTP orqali murojaat qiladi. `Modules/railways/app/clients/` dagi
tipizatsiyalangan klientlardan aniqlangan ro'yxat:

| Servis | Vazifasi |
|---|---|
| **USERS** | Agent ma'lumoti: uid, nomi, turi, roli, zanjirdagi parent |
| **RULES** | Agent uchun `search` + `fare` qoidalari |
| **RATES / CURRENCY** | Agent va provayder bo'yicha valyuta kurslari |
| **AGREEMENTS** | Shartnoma balansi: tekshirish va hisobdan yechish |
| **SETTINGS** | Agentning FTP eksport sozlamalari |
| **SUPPLIERS** | Provayder credential'lari, supplier va booking system nomlari |
| **ORDERS (booking system)** | Buyurtmalar ombori |

Klientlar **lazy** — bo'sh URL bilan ham ilova ko'tariladi, xato faqat servis haqiqatan
chaqirilganda chiqadi (lokal ishlash uchun qulay).

---

## 10. Yangi integratsiya yozish standarti

`Integrations/integration-template` — yangi supplier ulash uchun tayyor shablon.

Qoida: **`app/utils/` dan tashqari hamma narsa o'zgartirilmaydi** — `router.py`, `schemas/`,
`core/`, `deps.py`, `config.py`, `main.py` barcha integratsiyalarda bir xil. Supplier'ga xos
kod (so'rov quruvchi, konverter, sozlamalar) faqat `app/utils/` ichida yashaydi.

Standart endpoint to'plami (`/api/v1`):

```
/login  /search  /rules  /verify  /reprice-check  /reprice-confirm  /booking  /ticketing
/cancel  /refund  /void  /upsell  /seat-map  /additional-services  /select-service  /split
```

**Xato konvensiyasi**: zanjir `GTS Core → Integration → Provider`. Provayder xatosining asl matni
Core'ga aynan yetib borishi shart, shuning uchun xatolar HTTP 5xx emas, `status="error"` bilan
**200** javobga aylanadi:

```json
{ "status": "error", "code": -104,
  "message": "BOOKING: save_booking 403: user don't have enough credits on account",
  "data": null }
```

Supplier qo'llab-quvvatlamaydigan operatsiya ham xuddi shunday — sabab bilan `status="error"`.

---

## 11. Texnologik stek

| | |
|---|---|
| Til | Python 3.11–3.13 |
| Framework | FastAPI (modullar, integratsiyalar), Django + DRF (orders, static, receipt) |
| Paket menejeri | `uv` (`uv.lock`) |
| Kesh / broker | Redis (qidiruv va offer'lar hot path'i, Celery broker) |
| DB | PostgreSQL (asyncpg + SQLAlchemy 2.0 async, Alembic migratsiyalari) |
| Fon vazifalari | Celery (worker + beat): auto-cancel, ticket status tekshiruvi, FTP eksport |
| Logging | Kafka — supplier so'rov/javob juftliklari |
| PDF | Playwright + Chromium |

---

## 12. EasyBooking (B2C) qayerda turadi

EasyBooking — GTS'ning **iste'molchisi**. B2C API o'z foydalanuvchilariga soddalashtirilgan
kontrakt beradi, ortida esa GTS bilan gaplashadi.

Buning eng aniq isboti — qidiruv tanalari deyarli bir xil:

| GTS gateway | EasyBooking B2C |
|---|---|
| `POST /v1/content/search/` | `POST /flight/search/` |
| `directions`, `adt`, `chd`, `inf`, `ins`, `class`, `flexible`, `direct`, `airlines` | aynan shu maydonlar |

B2C collection'idagi `/gts/` bo'limi — B2C tomonidan GTS'ga buyurtma hodisalarini uzatish:

```
POST /gts/event/    { "order_number": "1250", "product_type": "flight" }
GET  /gts/
```

Farqlar: B2C'da qo'shimcha ravishda oxirgi foydalanuvchi uchun mo'ljallangan qismlar bor —
CMS (blog, aksiyalar, FAQ), to'lov tizimlari (Payme, Paygine, Uzum Nasiya), promokodlar,
Pocket Book. GTS tomonda esa agentlik ierarxiyasi, shartnomalar, qoidalar va hisobotlar bor.

Batafsil: [OVERVIEW.md](OVERVIEW.md).

---

## 13. Eslatmalar

- **GTS Core kodi bu workspace'da yo'q** — u faqat gateway collection'i va modullar kontraktlari
  orqali tasvirlangan. Modullar va integratsiyalar kodi mavjud.
- Gateway collection'ida host'lar aralash ishlatilgan: ko'p so'rovlar `{{host}}`, lekin ba'zilari
  `{{dev}}`, `{{local_user}}`, `{{localhost}}`, `{{global}}`, `{{live}}` da qolib ketgan — ishlatishdan
  oldin host'ni tekshirish kerak.
- `currency` va `rates` bo'limlari deyarli bir xil metodlarni takrorlaydi
  (`/v1/exchange/…` va `/v1/rate/…`) — qaysi biri joriy ekanini backend'dan aniqlash kerak.
- Ba'zi repo'lar (`cruise`, `excursion_extranet`, `orders`, `static`) hali GitLab shablon README'si
  bilan turibdi — ularning haqiqiy tavsifi kodda.
- `Modules/railways` — eski `railways` servisining toza qayta yozilgani; ikkalasini adashtirmaslik kerak.
