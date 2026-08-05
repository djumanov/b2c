# Backend arxitekturasi

Bu hujjat backend'ning **ichki tuzilishi**ni belgilaydi: modullar, qatlamlar, ma'lumotlar bazasi,
fon vazifalari va tashqi tizimlar bilan ishlash tartibi.

- Loyiha konteksti: [PROJECT.md](PROJECT.md) · Mahsulot manbai: [GTS.md](GTS.md)
- API kontrakti: [API.md](API.md)

> [API.md](API.md) — **kontrakt manbai**: tashqaridan nima ko'rinishini u belgilaydi.
> Bu hujjat esa shu kontrakt **ichkarida qanday amalga oshirilishini** tasvirlaydi.
> Ziddiyat chiqsa API.md ustun turadi.

---

## 1. Asosiy talab

[PROJECT.md](PROJECT.md) §1 va §7 dan kelib chiqadigan yagona bosh qoida:

> **Clientga xos farq kodda emas, DB'dagi sozlamada bo'ladi.**

Brending, sayt sozlamalari, to'lov va GTS credential'lari — hammasi DB'da, shifrlangan, paneldan
boshqariladi va qayta deploy talab qilmaydi. Env'da faqat infratuzilma qoladi: DB ulanishi, Redis,
shifrlash kaliti, log darajasi.

Bu qoida buzilsa o'rnatmalar bir-biridan uzoqlashadi va bitta clientga qilingan tuzatish
boshqalariga yetib bormaydi. **Quyidagi har bir qaror shu mezon bo'yicha tekshirilgan.**

Ikkinchi tayanch: bitta backend **ikkita yuza**ga xizmat qiladi (`/api/v1/public/*` va
`/api/v1/admin/*`), bitta domen model ustida, lekin **ikkita bir-biriga o'tmaydigan token
sub'ekti** bilan.

---

## 2. Qabul qilingan qarorlar

| # | Qaror | Natijasi |
|---|---|---|
| **D1** | GTS bilan mashina-mashina aloqasi — **o'rnatmaning o'z GTS agent akkaunti** | Backend DB'dagi shifrlangan credential bilan kiradi, sessiyani ushlab turadi, muddati tugaganda qayta kiradi. **Ochiq bog'liqlik:** [GTS.md](GTS.md) §3 dagi ikki bosqichli tasdiq mashina akkauntlari uchun o'chirilishi kerak — GTS jamoasi bilan tasdiqlansin |
| **D2** | Takliflar **keshlanmaydi**; qidiruv **to'liq stateless** | GTS `request_id` bo'yicha o'z keshini yuritadi ([GTS.md](GTS.md) §6) — biz uni takrorlamaymiz. `offers/` **passthrough**: `sort`, `limit`, `next_token`, `currency` GTS parametrlariga o'giriladi. Biz na Redis'ga, na Postgres'ga taklif yozmaymiz. **Evaziga:** saralash va filtr GTS imkoniyati bilan chegaralanadi |
| **D3** | **Bron → to'lov → avtomatik chipta**; chipta chiqmasa **avtomatik qaytarish** | GTS va to'lov provayderi bo'ylab tarqoq tranzaksiya. Restart'dan omon qoladigan **saga** bo'lishi shart, oddiy so'rov ichida bajarilmaydi |
| **D4** | Xarid uchun **akkaunt majburiy** | Har bir buyurtmada `customer_id` bo'sh bo'lmaydi. Mehmon sifatida xarid yo'q |
| **D5** | Auth — **email + parol**, qo'shimcha **Google** | Apple/Facebook/VK yo'q. ⚠ Apple qoidalari bo'yicha iOS ilovada uchinchi tomon social login bo'lsa **Sign in with Apple majburiy** — ya'ni Google 6-bosqichda Apple'ni ham talab qiladi. Shuning uchun provayderlar registry sifatida quriladi |
| **D6** | MVP'da OTP va parol tiklash — **faqat email/SMTP** | Telefon + SMS keyingi bosqichda; `login` maydoni relizda faqat email qabul qiladi ([API.md](API.md) §41) |
| **D7** | To'lov provayderlari — **Payme + Click** | Ikkalasi ham redirect + webhook. **Karta+OTP oqimi yo'q → karta raqami serverimizdan o'tmaydi → PCI qamrovi yo'q.** `transactions/{id}/card\|confirm\|resend-otp/` kontraktda qoladi, lekin ulanmaydi |
| **D8** | Tillar — **uz + ru + en** | Bo'sh qolgan tarjima [API.md](API.md) §7 fallback zanjiriga tushadi. Tarjima jadvali emas, `JSONB` (§10) |
| **D9** | **Beshta vertikal ham birinchi relizda** | `ProductAdapter` porti (§6) spekulyativ emas — birinchi kundanoq beshta turli oqim bilan sinovdan o'tadi |
| **D10** | O'rnatish, yangilash va zaxira — **clientning zimmasida** | Migratsiya biz nazorat qilmaydigan vaqtda ishga tushadi → **oldinga mos** bo'lishi va bir necha versiya sakrashni ko'tarishi shart (§12) |

---

## 3. Umumiy shakl — modulli monolit

**Bitta FastAPI ilovasi**, ichida **funksional modullar**, har bir modul ichida qatlamlar
(`router → service → repository/model`), tashqi tizimlar esa **adapterlar ortida**.

**Nega `Modules/railways` dagi qat'iy gorizontal qatlamlash emas?**
U qatlamlash (`api → services → domain → integrations → clients → infra`) bitta vazifali servis
uchun to'g'ri. Bu ilovada esa ~18 ta funksional soha bor va gorizontal qatlamlashda "promokod"
o'zgarishi bir-biridan uzoq beshta papkaga tegadi. Funksiya bo'yicha bo'lish o'zgarishni bitta
joyda ushlab turadi va daraxtni [PROJECT.md](PROJECT.md) §10 hamda [API.md](API.md) bo'limlariga
**1:1 moslashtiradi** — hujjatdagi o'zgarish aniq bitta papkani ko'rsatadi.
Qatlamlash **modul ichida** saqlanadi, ya'ni intizom foyda beradigan joyda qoladi.

**Nega mikroservis emas?**
Mahsulot bir clientga tegishli va client o'z serverida ishlatadi — ba'zan kuchli ops tajribasisiz.
Har bir qo'shimcha harakatlanuvchi qism qo'llab-quvvatlash yukini o'rnatmalar soniga ko'paytiradi.
Mikroservis muhiti allaqachon GTS tomonda; biz esa uning iste'molchisimiz.

---

## 4. Papka tuzilmasi

```
app/
  main.py                  # ilova factory, routerlarni ulash, middleware, lifespan
  api/
    v1/router.py           # public / admin / webhooks routerlarini yig'ish
    envelope.py            # {status, data, errors, meta} javob o'rami
    errors.py              # istisno → xato katalogi
    deps.py                # CurrentCustomer, CurrentStaff, require_owner, Pagination
    idempotency.py         # Idempotency-Key dependency
    listing.py             # search / ordering / created_from-to + Page yig'ish (§6)
    middleware.py          # X-Request-Id, so'rov jurnali, CORS manbalari
    openapi.py             # OpenAPI artefaktini envelope bo'yicha qayta yig'ish
  core/
    config.py              # FAQAT env: DB, Redis, shifrlash kaliti, log darajasi
    security.py            # JWT, argon2, refresh rotatsiyasi, jti qora ro'yxati
    roles.py               # ikkita rol va ular orasidagi ierarxiya (owner ⊃ admin)
    crypto.py              # DB'dagi sirlar uchun AES-GCM shifrlash
    i18n.py                # tarjima obyektlari + fallback zanjiri
    money.py               # Decimal va valyuta
    logging.py             # structlog, X-Request-Id
  db/
    session.py  base.py  mixins.py     # UUID pk, created_at/updated_at/deleted_at
    redis.py                           # jarayon bo'yicha yagona Redis klienti
    repository.py                      # soft delete'ni hisobga oluvchi umumiy o'qishlar
  modules/                 # funksional bo'laklar; har birida: router_public.py,
    settings/              #   router_admin.py, models.py, schemas.py, service.py
    integrations/          # gts / payments / notifications sozlamalari va sirlari
    cms/                   # blog, aksiya, faq, sahifa, kontakt, banner, yo'nalishlar
    feedback/              # sharhlar va moderatsiya
    catalog/               # shahar, stansiya, davlat, aviakompaniya, valyuta
    customers/             # akkaunt, profil, yo'lovchilar, kartalar, qurilmalar
    staff/                 # xodimlar va ularning roli
    products/              # qidiruv oqimi routerlari + ProductAdapter registry (stateless)
    booking/               # verify → bron → to'lov/chipta sagasi
    orders/                # buyurtmalar, status xaritasi, sync, available_actions
    payments/              # to'lovlar, tranzaksiyalar, qaytarishlar, webhook
    promo/  leads/  notifications/  reports/  audit/  jobs/  uploads/
  providers/               # portlar va adapterlar — tashqi dunyoga yagona chiqish
    gts/                   # klient, sessiya auth, xato+status xaritasi, vertikal adapterlar
    payments/              # base.py, payme.py, click.py
    notifications/         # base.py, smtp.py
    storage/               # base.py, local.py
  tasks/                   # celery ilovasi, beat jadvali
migrations/                # alembic, bitta head
tests/                     # unit / integration / e2e
```

Routerlar **modul ichida** turadi (bir joyda to'planganlik uchun); `api/v1/router.py` — faqat
yupqa yig'uvchi.

> **Modullar bir-biri bilan faqat `service` funksiyalari orqali gaplashadi** — hech qachon boshqa
> modulning `models.py` yoki repository'siga murojaat qilmaydi. Aynan shu bitta qoida bu
> tuzilmani modulli monolit holida saqlaydi va "katta chalkashlik"ka aylanishiga yo'l qo'ymaydi.

---

## 5. Modullar va javobgarliklari

| Modul | Nimaga egalik qiladi | Izoh |
|---|---|---|
| `settings` | Brending, sayt, tillar, valyutalar, menyu, `features`, mahsulot ro'yxati, `site-config` yig'ilishi | Redis read-through kesh; **har qanday yozuv `site-config` keshini tozalaydi** — "logoni almashtir, deploy shart emas" shu bilan haqiqatga aylanadi |
| `integrations` | GTS, to'lov va bildirishnoma xizmatlari sozlamasi va shifrlangan credential'lari, `test/` tekshiruvlari | Sirlar o'qishda maskalanadi, hech qachon to'liq qaytarilmaydi |
| `cms` | 7 ta kontent resursi + publish/unpublish/reorder | Tarjimali maydonlar JSONB; public o'qish alohida "yassilovchi" serializer orqali |
| `feedback` | Sharhlar va moderatsiya holati | `pending → accepted \| rejected` |
| `catalog` | Shaharlar, stansiyalar, davlatlar, aviakompaniyalar, valyutalar | GTS static servisidan beat vazifa bilan sinxronlanadi, uzoq Redis TTL, ikkala yuzaga ham faqat o'qish |
| `customers` | Akkaunt, auth, profil, saqlangan yo'lovchi/karta, qurilma, ichki bildirishnomalar | `aud: public` tokenlari |
| `staff` | Xodimlar va ularning roli — `owner` yoki `admin`, kodda qat'iy belgilangan | `aud: admin` tokenlari |
| `products` | Qidiruv oqimi routerlari (`search`/`offers`/`verify`/`upsell`) va `ProductAdapter` registry | **Holatsiz** (D2): hech narsa saqlamaydi, GTS'ga uzatadi va javobni normallashtiradi |
| `booking` | `verify` dan keyingi bron va buyurtmani chiptagacha yoki qaytarishgacha olib boruvchi **saga** | D3 bo'yicha — **eng yuqori xavfli modul** |
| `orders` | Lokal buyurtma yozuvlari, GTS↔kanonik status xaritasi, `sync`, `available_actions` | Lokal yozuv **egalik** uchun, GTS **status va chipta** uchun manba |
| `payments` | To'lovlar, tranzaksiyalar, qaytarishlar, provayder webhook'lari | D7 bo'yicha |
| `promo` | Kodlar, qoidalar, to'lovga qo'llash, statistika | Chegirma **client marjasidan** ketadi, GTS to'liq tarifni oladi (§14 A4) |
| `leads` | Lead'lar, **lead manbalari sxemasi**, obunalar | Manba sxemasi uchun admin endpoint qo'shildi — §14 G1 |
| `notifications` | Shablonlar, yuborish, ommaviy yuborish, qurilma reyestri | MVP'da faqat SMTP adapteri (D6) |
| `reports` | Dashboard, sotuv agregatsiyasi, maydonlar katalogi, saqlangan ko'rinishlar, eksport | Eksport `jobs` orqali |
| `audit` | Har bir admin mutatsiyasi va auth hodisalarining o'zgarmas jurnali | Har bir handler emas, **dependency** yozadi |
| `jobs` | Foydalanuvchiga ko'rinadigan async ishlar reyestri | `GET /admin/jobs/{id}/` ortida |
| `uploads` | Fayl yozuvlari, `purpose` tekshiruvi, bog'lanmaganlarini tozalash | Storage porti ortida |
| `system` | Holat (`health`), versiya — [API.md](API.md) §39 | Egalik qiladigan jadvali yo'q: boshqa modullardan va infratuzilmadan holat yig'adi |

---

## 6. Mahsulot vertikallari — bitta oqim, ko'p mahsulot

[API.md](API.md) §20 barcha vertikallar uchun bir xil `{product}` naqshini talab qiladi, lekin
chetlanishlar bor: `railway` da `offers/` o'rniga `trains/` va `train-details/`, `esim` va
`transfer` da `verify/` o'rniga `offer/`.

Beshta deyarli bir xil routerni qo'lda yozish ularning vaqt o'tib bir-biridan uzoqlashishini
**kafolatlaydi** — bu [PROJECT.md](PROJECT.md) §17 dagi aynan o'sha risk.

**Yechim:** har bir vertikal uchun **`ProductAdapter` porti**, mahsulot kodi bo'yicha registry'da
saqlanadi. Adapter umumiy oqimni amalga oshiradi va **o'z imkoniyatlarini e'lon qiladi**.
Public router `{product}` ni adapterga bog'laydi, umumiy qadamlarni generik tarzda beradi,
vertikalga xos qo'shimcha yo'llarni uning e'lonidan oladi, qo'llab-quvvatlanmagan qadamga esa
`404 not_found` qaytaradi.

Natija: 3-bosqichda poyezd/sug'urta/eSIM/transfer qo'shish — **bitta adapter fayli va registry
yozuvi**. Oqim, saga va panel o'zgarmaydi. Bu shart 3-bosqichning **qabul mezoniga** kiritilgan
([PROJECT.md](PROJECT.md) §15): oqim kodiga o'zgarish kiritishga to'g'ri kelsa, demak port
noto'g'ri loyihalangan.

---

## 7. GTS anti-corruption qatlami

GTS kontrakti bizning kontraktimizdan ataylab farq qiladi va **ichkariga o'tmasligi shart**.

| GTS tomonda | Bizda | Kim o'giradi |
|---|---|---|
| `{status, message, id, time, total, data}` | `{status, data, errors, meta}` | javob xaritasi |
| **Xatoda ham HTTP 200**, manfiy kodlar bilan | To'g'ri HTTP status + xato katalogi | xato xaritasi: default `502 upstream_error`; alohida kodlar `offer_expired`, `payment_failed` ga; **asl matn `message` da, asl kod `meta.upstream` da** ([API.md](API.md) §3 talabi) |
| `BO/PW/TI/TE/CB/VO/RF/PRF` | `booked/pending/ticketed/failed/cancelled/voided/refunded/partially_refunded` | status xaritasi, har vertikal uchun alohida |
| Cookie sessiya, muddati tugaydi | — | sessiya menejeri: credential DB'dan deshifrlanadi, sessiya Redis'da, **qulf ostida — faqat bitta worker qayta kiradi**, 401 da bitta avtomatik takror |
| Taklif va narx tuzilmasi | Bizning `offer` sxemamiz | maydon xaritasi + pul formati (`{amount, currency}`, string) |

Timeout va retry [API.md](API.md) §12 bo'yicha: qidiruv 40 s, qolgani 15 s; retry **faqat `GET`**,
2 marta, eksponensial kechikish bilan; **bron va to'lovda retry yo'q**. `X-Request-Id` tashqariga
uzatiladi.

> `available_actions` **server tomonda** hisoblanadi — kanonik status va vertikal qoidalaridan.
> Shunda panel ham, ilova ham biznes qoidasini UI ichida qattiq kodlamaydi.

---

## 8. Bron → to'lov → chipta sagasi

```
verify → bron (GTS hold, buyurtma=booked) → to'lov yaratiladi → mijoz to'laydi (Payme/Click)
       → provayder webhook → to'lov=paid → chipta vazifasi → buyurtma=ticketed
                                                          ↘ xato → avto-qaytarish → refunded
                                                                 ↘ qaytarish ham xato →
                                                                   needs_attention
```

**Transactional outbox + Celery vazifalari** ustida quriladi, saga framework'isiz:

- Holat mashinasi **buyurtma qatorida**; o'tishlar `SELECT … FOR UPDATE` bilan.
- Har bir qadam — **buyurtma id'si bo'yicha idempotent vazifa**: qayta urinish xavfsiz, takroriy
  webhook xavfsiz (provayderlar qayta yuboradi, Payme protokoli buni nazarda tutadi).
- Nojo'ya ta'sirlar holat o'zgarishi bilan **bitta tranzaksiyada** outbox'ga yoziladi, keyin
  yuboriladi — "to'lov yozildi, lekin chipta navbatga tushmadi" oralig'ida server o'chsa ham
  chipta yo'qolmaydi.
- Chipta chiqmasa — **avtomatik qaytarish** (D3). Agar qaytarishning **o'zi** ham urinishlardan
  keyin bajarilmasa, buyurtma **`needs_attention`** terminal holatiga tushadi va panelda
  ko'rinadi. **Pul hech qachon jimgina yo'qolmaydi.**
- `Idempotency-Key` ([API.md](API.md) §10) Redis'da 24 soat: so'rov barmoq izi → keshlangan javob.
  Pul endpointida kalitsiz so'rov — `422`.

> Saga **bron qilingandan keyin** boshlanadi. Undan oldingi qism (qidiruv va takliflar) holatsiz
> (§9), shuning uchun tiklash mantiqi faqat pul yo'lida kerak — u yerda esa majburiy.

---

## 9. Qidiruv oqimi — holatsiz passthrough

Qidiruv **to'liq stateless** (D2): biz na taklifni, na qidiruv holatini saqlaymiz.

| Qadam | Nima bo'ladi |
|---|---|
| `search/` | So'rov GTS'ga uzatiladi; GTS bergan `request_id` javobda qaytariladi. Biz hech qayerga yozmaymiz |
| `offers/` | `request_id`, `next_token`, `limit`, `sort`, `currency` GTS parametrlariga o'giriladi; GTS javobi normallashtiriladi (envelope, pul formati, maydon nomlari) va qaytariladi |
| `search_state`, qisman natija | GTS'dan kelganicha uzatiladi — bizda progress hisoblagichi yo'q |
| Taklif muddati | **GTS tomonda** tugaydi; uning xatosi `409 offer_expired` ga o'giriladi ([API.md](API.md) §3) |

**Nega kesh qurmaymiz.** GTS `request_id` va `offer_id` bo'yicha o'z keshini yuritadi
([GTS.md](GTS.md) §6). Uni ikkinchi marta qurish uchta muammo keltiradi: ma'lumot ikki joyda
turadi, ikkita TTL'ni moslashtirish kerak bo'ladi va "GTS'da bor-u bizda yo'q" (yoki teskarisi)
holati paydo bo'ladi. Passthrough bularning uchalasini ham yo'q qiladi va butun quyi tizimni —
kollektor vazifasi, offer ombori, TTL siyosati — olib tashlaydi.

> **Evazi.** `offers/` dagi saralash va filtr **GTS imkoniyati bilan chegaralangan**: GTS
> qo'llamaydigan saralash tartibi bizda ham bo'lmaydi. Bu cheklov kontraktga ochiq yozilgan
> ([API.md](API.md) §20) — frontend undan tashqariga chiqmasligi kerak.

**Redis nima uchun qoladi:** `site-config` keshi · statik kataloglar · `Idempotency-Key` ·
GTS sessiyasi · rate limit hisoblagichlari · Celery brokeri. Qidiruv uchun **ishlatilmaydi**.

---

## 10. Ma'lumotlar bazasi

Bitta PostgreSQL bazasi, bitta schema, Alembic **bitta head** bilan; migratsiya konteyner
ko'tarilganda ishga tushadi.

- **Tenant ustuni yo'q.** Mahsulot ataylab bir clientga mo'ljallangan; "ehtimol keraksa" deb
  qo'shilgan tenant ustuni bu yerdagi asosiy ortiqcha murakkablik tuzog'i bo'lardi va abadiy
  sizib chiquvchi xatolar manbaiga aylanardi.
- UUID birlamchi kalitlar; `created_at` / `updated_at` / `deleted_at` mixin'i;
  **default soft delete** ([API.md](API.md) §8), o'chirilgan qatorlarni chiqarib tashlovchi
  **partial unique index** bilan — shunda `slug` qayta ishlatilishi mumkin.
- **Tarjimali maydonlar — `JSONB`** `{til: qiymat}`. Bu aynan API kontrakti talab qiladigan shakl,
  shuning uchun har o'qishda tarjima jadvaliga join qilinmaydi; qidiriladigan joyda GIN indeks.
- **Pul — `NUMERIC(18,2)` va alohida `currency CHAR(3)`**, hech qachon float; javobda string.
- **Singleton sozlama jadvallari** (brending, sayt, tillar, valyutalar, `features`) — bittadan
  qator, `CHECK` bilan kafolatlanadi. Menyu — o'ziga havola qiluvchi daraxt.
- **Sirlar** `integration_credentials` da: shifrlangan qiymat + `key_version` ustuni. Shunda
  shifrlash kalitini **almashtirish** uchun barcha credential'larni qayta kiritish shart emas
  ([PROJECT.md](PROJECT.md) §17 dagi risk).
- **Buyurtma va to'lov keshlanmaydi** ([API.md](API.md) §12). **Takliflar esa umuman hech qayerda
  saqlanmaydi** — na Postgres'da, na Redis'da (D2, §9).
- Audit jurnali — faqat qo'shiladigan, `(actor, resource, created_at)` bo'yicha indeksli.
  Partitsiyalash hozircha yo'q: hajm talab qilganda qo'shiladi, oldindan emas.
- **Migratsiyalar oldinga mos** (D10): client bir necha versiya oshirib sakrashi mumkin, shuning
  uchun har bir migratsiya mustaqil va qaytarib bo'ladigan bo'lishi kerak.

**Jadval guruhlari:** *identity* (customers, staff, refresh tokens) · *config* (sozlama
singletonlari, menu, integration configs) · *cms* (7 ta kontent jadvali, feedbacks) ·
*commerce* (orders, order passengers, payments, transactions, refunds, promo codes, promo usages) ·
*engagement* (leads, lead sources, subscriptions, templates, broadcasts, devices, notifications) ·
*ops* (jobs, audit log, uploads).

> Qidiruv uchun jadval **yo'q** — bu D2 ning bevosita natijasi.

---

## 11. API tashkil etilishi

Uchta router ulanadi: `/api/v1/public`, `/api/v1/admin`, `/api/v1/webhooks`.

- **Envelope markazda qo'llanadi** — javob o'ramchisi va istisno handler'lari orqali. Endpoint'lar
  oddiy model qaytaradi va envelope'ni **qo'lda yasamaydi**; ~150 ta endpoint bo'ylab bir xillikni
  saqlashning yagona yo'li shu. Istisno — webhook'lar ([API.md](API.md) §40): u yerda javob shakli
  provayder protokoli talab qilganicha bo'ladi.
- **Xato katalogi bir joyda**: domen istisnolari → `(code, http_status)`, ya'ni [API.md](API.md) §3
  bir marta amalga oshiriladi.
- **Barcha yo'llarda trailing slash** ([API.md](API.md) §1).
- **Kesishuvchi vazifalarni dependency'lar bajaradi**: `CurrentCustomer` / `CurrentStaff`
  (`aud` tekshiriladi — shuning uchun customer tokeni `/admin/*` da `401` emas, **`403`** oladi),
  `require_owner`, `Pagination`, `IdempotencyKey`, `AuditContext`.
- **RBAC — ikki pog'onali rol tekshiruvi.** Rollar ikkita va kodda qat'iy belgilangan:
  `owner ⊃ admin` ([API.md](API.md) §5). Shuning uchun ruxsat satrlari katalogi yo'q va
  endpoint ikki holatdan birida bo'ladi: `CurrentStaff` yetarli (ikkala rol ham o'tadi), yoki
  ustiga `require_owner` qo'shiladi (`admin` → **`403`**). Bitta dependency, ikkita holat —
  ~150 endpoint bo'ylab yodda tutish kerak bo'lgan yagona narsa shu.
- `GET /admin/auth/me/` xodimning `role` qiymatini qaytaradi va panel menyuni **shu qiymat
  bo'yicha** yig'adi ([API.md](API.md) §27).
- **Audit — `/admin/*` mutatsiyalari uchun middleware, hodisa uchun service.** Yozuv shakli o'sha:
  kim, qaysi resurs, qanday amal, maydon darajasidagi farq (sirlar berkitilgan), request id, IP.
  Lekin uni **dependency yoza olmaydi**: dependency javob statusidan oldin tugaydi, `422` bilan
  tugagan so'rov haqidagi yozuv esa yolg'on bo'lardi. `route_class` ham yaramaydi — u
  `include_router` orqali merosga o'tmaydi, ya'ni har modul uni takrorlashi va **unutishi** mumkin
  bo'lardi. Shuning uchun: middleware `/api/v1/admin/*` dagi `POST`/`PATCH`/`DELETE` ni **2xx**
  bo'lganda yozadi, resurs va amalni route shablonidan oladi (`staff/{id}/block/` → `staff` +
  `block`); shablon noto'g'ri o'qiladigan kam sonli yo'l buni `Depends(Audited(...))` bilan o'zi
  aytadi; farqni esa `audit.context.describe()` orqali service qo'shadi.
  **`/admin/auth/*` bundan mustasno** — uning hodisalarida tizimga kirgan aktyor yo'q va eng
  muhimi (`login_failed`) `401` qaytaradi, ya'ni middleware qoidasiga umuman tushmaydi; ularni
  `staff` moduli o'zi yozadi ([API.md](API.md) §13 ham ularni alohida ajratadi).
  Middleware yozuvi **alohida tranzaksiyada** ketadi: so'rov sessiyasi javob statusi ma'lum
  bo'lgunga qadar yopiladi. Demak audit yozuvi bajarilmasa, muvaffaqiyatli mutatsiya orqaga
  qaytmaydi — xato `exception` darajasida logga tushadi. Teskarisi (yozuv uchun `500` qaytarish)
  clientni allaqachon bajarilgan amalni takrorlashga undagan bo'lardi.
- OpenAPI — shu qoidalarning **artefakti**, aksincha emas ([API.md](API.md) muqaddimasi).

---

## 12. Integratsiyalar va infratuzilma

| Soha | Tanlov | Nega |
|---|---|---|
| Stek | Python 3.13, FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic, Pydantic v2, httpx, argon2, structlog, `uv`, ruff + mypy strict | [GTS.md](GTS.md) §11 dagi tashkilot standartiga mos — jamoa buni allaqachon ishlatadi |
| Fon vazifalari | **Celery + Redis** (worker + beat) | Tashkilot standarti; buyurtma sinxronizatsiyasi, tozalash va katalog yangilash uchun beat kerak |
| Kesh / broker | Redis | `site-config`, statik kataloglar, idempotency, GTS sessiyasi, rate limit, Celery brokeri. **Qidiruv uchun emas** (D2, §9) |
| To'lov | `PaymentProvider` porti + Payme, Click adapterlari | Payme'ning provayder boshqaradigan JSON-RPC protokoli webhook endpoint'i orqali; Click — redirect + callback. Keyinchalik Paygine = bitta adapter va karta/OTP yo'llarini ulash |
| Bildirishnoma | `Notifier` porti + SMTP adapteri | D6; SMS/push adapterlari chaqiruvchi kodga tegmasdan qo'shiladi |
| Fayl saqlash | `Storage` porti + lokal disk adapteri | Bitta serverli o'rnatma; Docker volume — zaxira birligi. Client xohlasa S3 shunchaki adapter almashtirish |
| **Olinmadi** | Kafka, mikroservis, event sourcing, CQRS, GraphQL, o'z rules/narx mexanizmimiz, rol konstruktori, Kubernetes | Har biri — client boshqarishi kerak bo'lgan haqiqiy infratuzilma. Narx GTS'ga tegishli ([PROJECT.md](PROJECT.md) §5), rollar esa ikkita va [API.md](API.md) §5 da qat'iy belgilangan |

**Yetkazib berish:** Docker Compose — `api`, `worker`, `beat`, `postgres`, `redis`, reverse proxy.
Entrypoint `alembic upgrade head` ni bajaradi, so'ng **faqat birinchi ko'tarilishda** env'dan
birinchi `owner` ni yaratadi. Postgres va yuklangan fayllar uchun volume.

**Beat jadvali:** ochiq buyurtmalar statusini GTS'dan sinxronlash · bog'lanmagan fayllarni tozalash
(24 soat, [API.md](API.md) §11) · idempotency kalitlarini tozalash · katalog yangilash ·
valyuta kurslarini yangilash.

> Buyurtma sinxronizatsiyasi ataylab **polling** sifatida quriladi: [GTS.md](GTS.md) §12 faqat
> B2C→GTS yo'nalishini hujjatlashtirgan, ya'ni GTS bizga o'zi qo'ng'iroq qilishiga tayanib
> bo'lmaydi. Agar keyinchalik ma'lum bo'lsaki qiladi — webhook **optimallashtirish** bo'ladi,
> qayta yozish emas.

---

## 13. Dizayn tamoyillari

1. **Sozlama, kod emas, env ham emas.** Client farq qilishi mumkin bo'lgan hamma narsa panel
   ortidagi DB'da. Env'da faqat infratuzilma. Bu — loyihaning bosh talabi va har bir kelajakdagi
   o'zgarish shu mezon bilan tekshirilishi kerak.
2. **Tashqi dunyo faqat `providers/` orqali kiradi.** GTS'ning envelope'i, "xatoda ham 200"
   konvensiyasi va status kodlari adapter chegarasida to'xtaydi.
3. **Yuqori oqimda bor narsani takrorlamaymiz.** GTS keshini ikkinchi marta qurmaymiz (D2),
   narx mexanizmini yozmaymiz, provayderlarga o'zimiz ulanmaymiz. Har bir takrorlash — sinxrondan
   chiqadigan yana bitta joy.
4. **Kesishuvchi vazifalar — dependency, handler kodi emas**: envelope, xatolar, auth, RBAC,
   sahifalash, idempotentlik, audit. Aks holda ikki yuza bo'ylab bir xillikka erishib bo'lmaydi.
5. **Modullar `service` orqali gaplashadi.** Modullararo model yoki repository import qilinmaydi.
6. **Pul yo'llari — aniq holat mashinalari**, chidamli qayta urinishlar va `needs_attention`
   terminal holati bilan. Jimgina yo'qotish yo'q.
7. **Avval kontrakt, keyin kod**: [API.md](API.md) — haqiqat manbai, OpenAPI — uning artefakti.
8. **Ikkinchi vertikal abstraksiyasi birinchisi ishlagach quriladi.** `ProductAdapter` porti
   hozir loyihalanadi (chunki kontrakt beshta vertikalni talab qiladi), lekin 2-bosqichda faqat
   `flight` amalga oshiriladi; qolgan to'rttasi 3-bosqichda portni sinovdan o'tkazadi.

---

## 14. Qabul qilingan taxminlar

Bular ishni to'xtatmaydi, lekin tasdiqlanishi kerak.

| | Taxmin | Qanday bekor qilinadi |
|---|---|---|
| A1 | Lokal buyurtma yozuvi **mijoz↔buyurtma egaligi** uchun manba; GTS — **status va chipta** uchun. `sync/` ularni moslashtiradi | GTS mijoz bo'yicha buyurtma so'rovini qo'llab-quvvatlasa |
| A2 | Kanonik status enum va vertikal xaritasi **biz belgilaymiz** (hujjatlarda ziddiyat: `BO/TI/…` va `"ticketed"`) | GTS'ning B2C uchun mo'ljallangan lug'ati berilsa |
| A3 | Valyuta konvertatsiyasi **GTS tomonda**: `currency` parametr sifatida uzatiladi va narx o'sha valyutada qaytadi | GTS buni qo'llamasa — u holda D2 passthrough'i ham qayta ko'riladi |
| A4 | Promokod chegirmasi **client marjasidan** ketadi, GTS to'liq tarifni oladi | Tijorat tomoni tasdiqlasa |
| A5 | Yoqilgan mahsulotlar ro'yxati GTS shartnomasidan o'qiladi va beat vazifa bilan keshlanadi | Aniq GTS endpoint'i ko'rsatilsa |
| A6 | Hisobotlarda kun bo'yicha guruhlash uchun o'rnatma darajasidagi vaqt mintaqasi (default `Asia/Tashkent`); saqlash UTC'da qoladi | — |
| A7 | Qisman qaytarishni `admin` boshlaydi, summa GTS `refund-check` dan olinadi | Siyosat aniqlansa |
| A8 | Yuklamalar va eksportlar lokal diskda, storage porti ortida | S3 talab qilinsa |
| A9 | GTS `offers/` da sahifalash (`next_token`) va saralashni qo'llaydi | Qo'llamasa — [API.md](API.md) §20 kontrakti qisqartiriladi (`sort` olib tashlanadi) |

**G1 (hujjatdagi bo'shliq, to'ldirildi):** lead `source` qiymatlari va ularning `fields` sxemasi
paneldan sozlanishi aytilgan edi ([API.md](API.md) §25), lekin uni sozlaydigan endpoint yo'q edi.
Shu sababli `/admin/leads/sources/` (CRUD) qo'shildi ([API.md](API.md) §35).

**Keyinga qoldirilgan** — [PROJECT.md](PROJECT.md) §16 dagi ochiq savollar. Ularning hech biri
hozir to'sqinlik qilmaydi.

---

## 15. Bosqichlar

To'liq jadval va qabul mezonlari — [PROJECT.md](PROJECT.md) §15. Bu yerda backend nuqtai
nazaridan qisqacha:

| Bosqich | Backend ishi |
|---|---|
| **1. Yadro** | Ilova skeleti, envelope + xato katalogi, ikki sub'ektli auth, ikki rolli RBAC, sozlamalar + shifrlangan credential'lar, migratsiyalar, audit, `site-config`, health |
| **2. GTS ulanishi va birinchi vertikal** | GTS sessiya klienti va ACL (§7), `ProductAdapter` porti (§6), `flight` adapteri, holatsiz qidiruv oqimi (§9), verify/bron, Payme + Click adapterlari, **saga** (§8), buyurtmalar |
| **3. Qolgan vertikallar** | `railway`, `insurance`, `esim`, `transfer` adapterlari. **Oqim va saga kodi o'zgarmasligi shart** — port shu bilan sinovdan o'tadi |
| **4. Panel** | Admin yuzasining qolgan qismi: kontent, mijozlar, promokodlar, murojaatlar, hisobot asoslari |
| **5. Sayt** | Backend tomonda yangi ish kam; `site-config` va public kontent yuzasini yakunlash |
| **6. Mobil ilova** | Push infratuzilmasi, qurilma reyestri, **Sign in with Apple** (D5) |
| **7. Yetuklik** | Hisobot eksporti, ommaviy yuborish, o'rnatish/yangilash hujjati |

---

## 16. Tekshirish

- **Kontrakt testlari**: envelope shakli, 11 ta xato kodi, trailing slash va `meta` sahifalash —
  har bir routerdan namuna olib. Ikki yuzaning bir xilligini aynan shular ushlab turadi.
- **Soxta GTS adapteri va soxta to'lov provayderlari** (`Modules/railways` dagi `DEMO` supplier
  ana shu yondashuvning namunasi) — butun oqim CI'da oflayn ishlaydi.
- **Passthrough testi** (§9): `offers/` parametrlari GTS chaqiruviga to'g'ri o'girilgani, javob
  normallashtirilgani va **hech qayerda saqlanmagani**. Oxirgisi D2 ning regressiya qo'riqchisi.
- **Saga testlari sun'iy xatolar bilan**: chipta xatosi → avto-qaytarish · qaytarish xatosi →
  `needs_attention` · takroriy webhook → ikki marta yechilmaydi · to'lov va chipta oralig'ida
  server o'chishi → qayta ko'tarilganda tiklanish. **Test eng ko'p foyda beradigan modul shu.**
- **Adapter porti testi** (3-bosqich qabul mezoni): to'rtta yangi vertikal qo'shilganda oqim va
  saga kodi o'zgarmaganini diff bilan tasdiqlash.
- **Ikki rolli kirish testi** route jadvali bo'ylab sidirg'a o'tadi: har bir `/admin/*` yo'l
  yo `admin`, yo `owner` talab qilishini e'lon qiladi, keyin `admin` tokeni `owner`-only
  yo'llarda **`403`** olishi, customer tokeni esa ikkalasida ham **`403`** olishi tekshiriladi
  ([API.md](API.md) §5). Rolsiz yoki noma'lum rolli token ham `403`.
- **i18n fallback testi**: so'ralgan til → asosiy til → mavjud birinchi til zanjiri va qaytarilgan
  `lang` qiymati.
- **Qo'lda e2e** compose stack'da: ko'tarilish → migratsiya → `owner` yaratish → panelga kirish →
  brend rangini o'zgartirish → `GET /public/site-config/` da **deploysiz** aks etishini tasdiqlash
  (mahsulotning bosh va'dasi) → aviachipta qidiruvi → bron → provayder sandbox'ida to'lov →
  chipta → qaytarish.
