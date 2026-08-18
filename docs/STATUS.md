# Holat va qolgan ish

**Oxirgi yangilanish:** 2026-08-17 · `feat/orders-local-record`

Bu hujjat **avtoritet emas** — kontrakt uchun [API.md](API.md), tuzilma uchun
[ARCHITECTURE.md](ARCHITECTURE.md), qamrov va bosqichlar uchun
[PROJECT.md](PROJECT.md) ustun turadi.

Bu yerda **hozir nima qurilgan** va yo'lda topilgan, koddan ko'rinmaydigan
qarorlar. **Nima qurilishi kerak va qaysi tartibda** — [PHASES.md](PHASES.md);
ikkalasi bir savolga ikki marta javob bermasligi uchun bu hujjat rejani
takrorlamaydi.

---

## 1. Bir qarashda

| | |
|---|---|
| Bosqich | **1 — Yadro** bajarilgan; 4-fazadan FAQ, sahifalar va `leads` oldinga tortilgan ([PHASES.md](PHASES.md) §2.14). 2-fazadan GTS klienti, aviachipta qidiruvi va buyurtma yozuvi boshlab yuborildi (§4 bo'laklari 1, 2, 4, 5, 6 — qisman) |
| Endpointlar | 92 ta yo'l / 129 operatsiya (API.md dagi ~150 dan) |
| Jadvallar | 28 ta + `alembic_version` |
| Migratsiyalar | 29 ta, bitta head (`3c1f9a4d7e02`) |
| Testlar | 842 ta — unit 24 fayl · contract 9 · integration 31 |
| Gate'lar | ruff · mypy strict yashil; pytest'da kartaning Luhn tekshiruvi olib tashlangan `022016f` commit'iga ergashmagan 2 ta eski test qizil (`test_saved_cards`, `test_card_pan_never_stored`) — `main` da ham qizil, bron/bekor qilish ishiga aloqasiz |

**1-bosqich qabul mezonlari** (PROJECT.md §15):

| Mezon | Holat |
|---|---|
| Panelga `owner` sifatida kirish mumkin | ✅ `tests/integration/test_staff_auth.py` |
| Brend rangi o'zgarsa `site-config` da **deploysiz** aks etadi | ✅ `tests/integration/test_site_config.py` |
| `admin` tokeni `owner` talab qiladigan endpointda `403` oladi | ✅ `tests/integration/test_staff_crud.py` |

> Uchala mezon ham texnik jihatdan bajarildi. 1-bosqich qamrovidan **faqat
> e2e qabul testi** qoldi (§3). `customers` to'liq — email+parol va Google;
> `integrations` da GTS, SMTP, to'lov va social sozlamalari bor. Ikkala
> `test/` probe'i 2-fazada, adapterlari bilan birga ([PHASES.md](PHASES.md)
> §2.13) — bu qamrovdan chiqarish emas, adapteri yo'q tugma "sozlangan" deb
> aytardi, "yetib boradi" deb emas.

---

## 2. Nima qurilgan

### Poydevor (dastlabki setup)

`core/` (config, security, roles, crypto, i18n, money, logging) · `api/`
(envelope, errors, deps, idempotency, middleware, openapi) · `db/` (session,
base, mixins, redis) · `providers/` portlari · Celery skeleti · docker stack ·
kontrakt testlari.

### Keyin qo'shilgani

| Modul | Nima bor | Endpoint |
|---|---|---|
| `api/listing.py`, `db/repository.py` | API.md §6 (search / ordering / sana filtri / `paginate`), soft-delete o'qishlari, `db.Entity` | — |
| `staff` | Admin auth, rotatsiyali refresh, jamoa resursi, birinchi owner bootstrap | 11 |
| `audit` | Har bir admin mutatsiyasi + auth hodisalari jurnali | 1 |
| `uploads` | Local disk storage, purpose bo'yicha limitlar, orphan sweep | 1 + `/uploads/*` |
| `settings` | Brending, sayt, tillar, valyutalar, features, mahsulotlar, kesh, CORS origin'lari | 7 |
| `settings` (public) | `GET /public/site-config/` — kesh + ETag + i18n | 1 |
| `integrations` | GTS credential'lari: ro'yxat, bittasi aktiv, shifrlangan parol | 3 |
| `integrations` (SMTP) | Singleton sozlama, shifrlangan parol, haqiqiy sinov xabari, `SMTP_*` dan birinchi ko'tarilish urug'i | 2 |
| `providers/notifications/html.py` | Brendlangan xat qobig'i: matn va HTML bitta konvertda (`multipart/alternative`), rang va nom `settings.service.mail_brand()` dan | — |
| `integrations` (to'lov) | Har bir provayder uchun qator, shifrlangan credential obyekti, `site-config` va `health` ga ulanish | 2 |
| `integrations` (social) | Google OAuth client'i; `client_id` ochiq, `client_secret` shifrlangan | 1 |
| `system` | health, version (setup'dan) | 2 |
| `customers` (auth) | Ro'yxatdan o'tish + email OTP, kirish, rotatsiyali refresh, uch qadamli parol tiklash | 9 |
| `customers` (profil) | Profil o'qish/tahrir (avatar kodi shu yerda), parol, akkauntni o'chirish (sabablar bilan, arxivga nusxa), sabablar ro'yxati, saqlangan yo'lovchilar CRUD | 5 |
| `customers` (sabablar, admin) | `deletion_reasons` lug'ati CRUD — `text` JSONB, `sort_order`, holatsiz | 2 |
| `customers` (social) | `social/{provider}/` — Google ID token tekshiruvi, topiladi yoki yaratiladi | 1 |
| `catalog` (public) | `document-types/` va `countries/` — GTS static servisiga keshlangan proxy; `airports/?q=` — keshsiz qidiruv passthrough'i; auth yo'q, jadval yo'q | 3 |
| `providers/gts/static.py` | GTS `/static/*` uchun adapter: sessiyasiz, xato xaritasi bilan, envelope shu yerda to'xtaydi | — |
| `providers/gts/client.py` | Autentifikatsiyalangan GTS klienti: sessiya Redis'da (`gts:session:{id}:{updated_at}`), qayta kirish **qulf ostida**, 401/403 da bitta avtomatik takror, xato xaritasi (HTTP 200 ichidagi xato ham). Sessiya `sessionid` cookie sifatida yuboriladi — **taxmin**, jonli GTS'da tekshirilsin | — |
| `providers/products/flight.py` | `flight` adapteri: **butun oqim** — `search`, `offers`, `upsell`, `verify`, `booking`, `cancel`; sof passthrough — tana yengil tekshiruvdan so'ng GTS'ga aynan, javob `data` si kelganicha (API.md §20, 2026-08-12 va 2026-08-14 qarorlari). `booking`/`cancel` timeout'i 15 s (§12), javobga hech narsa qo'shilmaydi; `cancel` da shakl tekshiruvi umuman yo'q | — |
| `products` (public) | `POST /public/{product}/` oqimining oltita qadami — generik router, adapter registry orqali; gate `product_settings` (bayroq emas), 30/daq `search` limiti, qidiruv qadamlarida auth ixtiyoriy, **`booking/` va `cancel/` da majburiy** (D4); credential yo'q → `502`. Qidiruv qadamlari hech narsa saqlamaydi (D2), `test_search_passthrough.py` qo'riqlaydi; `booking/` esa buyurtma qatorini yozadi va `cancel/` uni egalik uchun o'qiydi (quyidagi qator) | 6 |
| `orders` (public) | `GET /public/orders/` va `/{id}/` — mijozning o'z buyurtmalari, `?product=`/`?status=` filtri bilan. Qator `booking/` javobidan tug'iladi: GTS javobi `JSONB` bo'lib kelganicha saqlanadi, undan javobning **ichki `data`** sidan `order_number`, `order_uid` va `status`, so'rovdan `request_id` va `offer_id` ustunga ajratiladi. **Yo'lovchilar bloki saqlanmaydi** (PROJECT.md §13). Status — GTS kodi, kanonik enum emas (API.md §21) | 2 |
| `api/multipart` | Yuklanadigan tanani chegara bilan o'qish — hozir yagona yuza, `/admin/uploads/`, uchun | — |
| `api/deps` | `RequireFeature` — o'chirilgan bo'lim ikkala yuzada `404`; o'n bitta bayroq. `current_customer` endi **qatorni yuklaydi** | — |
| `cms` (FAQ) | Savol/javob obyektlari, erkin kategoriya kodi, publish/unpublish, `reorder/`; public ro'yxat bitta tilda, `faq` bayrog'i ostida | 6 |
| `cms` (qiziqarli faktlar) | `fun_facts` — `text` JSONB, publish/unpublish, bayroqsiz va tartibsiz; `flight` qidiruv javobidagi `fun_fact` maydonini boqadi (API.md §20). Admin CRUD `/admin/content/fun-facts/` | 4 |
| `cms` (sahifalar) | Qat'iy uchlik — `privacy-policy`, `terms`, `about` — har biri o'z endpointi bilan (Swagger'da frontend/mobil ko'radi): admin `GET`/`PUT` (upsert, tillarni birlashtiradi) + publish/unpublish, public `GET`; draft ham yozilmagan ham bir xil `404`. Umumiy `pages/{slug}` CRUD olib tashlandi. Yadro — bayroqsiz (API.md §28) | 12 |
| `leads` | Sodda murojaat: mavzu + xabar + aloqa, token ixtiyoriy (`current_customer_optional` — sarlavha yo'q → anonim, yaroqsiz token → `401`); panelda ro'yxat, status va izoh | 3 |
| `leads` (mavzular) | `support_topics` lug'ati — `name` JSONB + `sort_order`, holatsiz, `leads` bayrog'i ostida; admin CRUD, public ro'yxat bitta tilda | 3 |
| `payments` (kartalar) | `/public/profile/cards/` — saqlangan kartalar **oddiy CRUD** (list/qo'shish/ko'rish/o'chirish): raqam faqat AES-GCM shifrlangan holda, provayder va OTP qatnashmaydi ([API.md](API.md) §19). Akkaunt o'chirilganda `forget_cards()` chaqiriladi | 2 |

> Ustundagi son — **yo'llar** soni (jami — §1 dagi 80).
> Operatsiyalar ko'proq: `settings` ning yettita yo'lida 12 ta bor, chunki
> beshtasi `GET`+`PATCH` juftligi (`products/` faqat `GET`, `cache/purge/`
> faqat `POST`); `integrations` ning to'qqizta yo'lida 14 ta. `customers` ning
> auth qismida 10 yo'l = 10 operatsiya (hammasi `POST`), profil qismida esa
> 5 yo'lda 11 ta. `catalog` ning uchtasida uchta.

**Jadvallar:** `staff`, `staff_refresh_tokens`, `audit_log`, `uploads`,
`branding`, `site`, `languages`, `currencies`, `features`, `product_settings`,
`gts_credentials`, `smtp_settings`, `customers`, `customer_refresh_tokens`,
`email_otps`, `passengers`, `payment_providers`, `social_credentials`,
`customer_cards`, `deleted_customers`, `deletion_reasons`, `faqs`,
`fun_facts`, `pages`, `leads`, `leads_support_contact`, `support_topics`,
`orders`.

**Beat jadvali:** `heartbeat` (5 daq) · `sweep_unlinked_uploads` (soatlik).

---

## 3. Keyingi ish

> **Keyingi ish — buyurtma tizimi.** Reja va bo'laklar:
> [`order-system/04-plan.md`](order-system/04-plan.md) (S1…S7).
> **S1 (holat mashinasi) bajarildi 2026-08-18:** `orders` jadvali qayta
> yozildi, `order_events` qo'shildi, kanonik status enumi va 18 ta o'tishdan
> iborat jadval `modules/orders/states.py` da, `service.transition()` —
> `FOR UPDATE` ostida statusni o'zgartiradigan yagona yo'l. `modules/booking/`
> o'chirildi (`O1`), `providers/gts/base.OrderStatus` olib tashlandi.
> **S2 (adapter porti) bajarildi 2026-08-18:** `providers/products/orders.py`
> — `OrderOperations`, `BookingResult`, `CancelResult`, `TravelerRef`,
> `UnreadableAnswer`. `book`/`cancel` `ProductAdapter` dan chiqib shu portga
> o'tdi va endi GTS `dict` i emas, bizning turlarimizni qaytaradi. Flight
> adapteri narxni (`price + fee_amount`, `float` → `str` → `Decimal`),
> `ticket_time_limit` ning uch xil formatini, marshrutni va yo'lovchilarni
> normallashtiradi; yo'lovchilar endi `orders.travelers` da **bizning
> shaklimizda** (§8.17 shu bilan yopilishga tayyor — tozalash S7 da).
> **S3 (bron niyati) bajarildi 2026-08-18:** buyurtma qatori endi GTS
> chaqiruvidan **oldin** yoziladi — A1 (yo'qoladigan bron) yopildi. `booking/`
> `Idempotency-Key` talab qiladi va 10/daq to'lov chelagida — A2 yopildi;
> haqiqiy qo'riqchi bazadagi `(customer_id, idempotency_key)` unique indeksi,
> Redis esa tez yo'l. Javob `{order, payment, data}` shakliga o'tdi. Rad etilgan
> bron kalitni bo'shatadi, javobsiz qolgani esa saqlaydi va `created` da
> qoladi. Keyingisi — S4, to'lov (`order_payments` + Payme/Click + webhooklar).

To'liq reja — [PHASES.md](PHASES.md).

1-bosqichdan **bitta bo'lak** qoldi:

| # | Bo'lak | Nima |
|---|---|---|
| 7 | `tests/e2e/test_phase1_acceptance.py` | Uchala qabul mezoni toza baza ustida, uchidan-uchiga |

**2-fazadan boshlab yuborilgani (2026-08-12):** GTS klienti + sessiya menejeri
(`providers/gts/client.py`), `flight` adapteri va `products` moduli —
`search/`, `offers/`, `upsell/` va `verify/` jonli passthrough sifatida ishlaydi. **Jonli GTS'da
tekshirildi (2026-08-12)**:

> **2026-08-17 da qo'shilgani:** `orders` moduli — `booking/` javobi mijoz
> nomiga `JSONB` bo'lib saqlanadi, `GET /public/orders/` va `/{id}/` uni
> qaytaradi, `cancel/` esa egalikni shu yozuvdan tekshiradi. **Jonli GTS'da
> tekshirilmagan**: `order_id` va `status` maydon nomlari hujjatdan olingan
> taxmin, va bekor qilish qattiq bo'lgani uchun bu §8.15 dagi 🔴 xavf.

- sessiya **ikkita cookie** bilan yuriladi: `esession={session_key}` va
  `token={token}` — ikkalasi ham signin javobining `data` sida keladi,
  bittasi yolg'iz `401` oladi. Kolleksiyadagi "cookie jar" taxmini
  (`sessionid`) noto'g'ri chiqdi va tuzatildi;
- o'lgan sessiya jonli GTS'da HTTP `401` qaytardi (kod `403` ni ham shunday
  qabul qilaveradi); `timeout_minutes` **float** (`360.0`) keladi. GTS
  akkauntida `white_list` (server IP) va `is_single` (bitta sessiya —
  panelga kirish backend sessiyasini o'ldiradi) bayroqlari ishlab
  chiqarishda muhim.

**2-fazaga qoldirilgan, ya'ni bugun `404` qaytaradigan yo'llar:**

- `POST /admin/integrations/gts/test/` — probe `providers/gts/` ga tushadi
  (2-fazaning 1-bo'lagi qurildi, probe'ning o'zi hali yo'q) va 2FA holatini
  alohida ko'rsatishi kerak (D1).
- Boshqa vertikallarning barcha qadamlari — adapter e'lon qilmagan qadam
  `404` (registry gate orqali). Idempotent `GET` uchun ikki takror
  (API.md §12) birinchi `GET` iste'molchisi bilan keladi.
  (`POST /public/flight/booking/` **endi ishlaydi** — 2026-08-14, quyida.)
- `POST /admin/integrations/payments/{code}/test/` — Payme va Click
  adapterlari bilan birga (2-fazaning 7-bo'lagi). Bugungi `PaymentProvider`
  portida sinash uchun chaqiriladigan metod yo'q: har biri haqiqiy to'lovni
  boshlaydi. Adapter kelganda portga `verify()` qo'shilishi kerak — `Notifier`
  da shunday.
- `POST /public/auth/devices/` — API.md §41, push bilan birga.

**Seam'lar ishladi**: `providers/gts/client.py` qo'shildi va `integrations`
ga **tegilmadi** — mezon bajarildi (`active_credential()` orqali o'qiydi,
xolos). `providers/payments/{payme,click}.py` uchun ham mezon o'sha.
`gts_base_url()` ni 2-faza emas, `catalog` (1-faza, [PHASES.md](PHASES.md)
§2.6) qo'shgan edi.

| Seam | Nima qaytaradi |
|---|---|
| `integrations.service.active_credential(session)` | Shifri ochilgan GTS akkaunti yoki `None` |
| `integrations.service.gts_base_url(session)` | Aktiv qatorning `base_url` i, aktiv qator bo'lmasa `DEFAULT_BASE_URL`. Hech qachon shifrni ochmaydi — `catalog` uchun, akkaunt kerak emas |
| `integrations.service.payment_providers(session)` | Yoqilgan va credential'i bor provayderlar |
| `integrations.service.notifier(session)` | SMTP yoki log adapteri |
| `settings.service.mail_brand()` | Xat uchun brend: nom, `primary` rang, absolyut logo URL'i. Sessiyasiz — `cors_origins()` bilan bir xil, `site-config` keshi orqali |
| `integrations.service.social_verifier(session, provider)` | Google verifier yoki `None` |

Boshqa **koddagi aniq nuqtalar**:

- `passengers.document_type` va `citizenship` endi **JSONB katalog obyekti** —
  klient §26 dan tanlagan obyekt aynan kelganicha saqlanadi, server faqat
  `"code"`/`"type"` kalitini tekshiradi (§4.75). **2-faza kuzatuvi:** bron oqimi
  GTS'ga yo'lovchi yuborganda saqlangan obyektdan aynan nima olinishi (kodmi,
  boshqa maydonmi) o'sha kontraktdan ko'rinadi — qat'iyroq tekshiruv kerak
  bo'lsa o'shanda qo'shiladi.
- **Bron GTS'ga yo'lovchi yuborganda** §13 dagi maydonlar yetmasligi mumkin
  (jins, fuqarolik, hujjat amal muddati — GTS'ning DOCS/DOCO/DOCA oilasi).
  Ular qo'shilishi kerak bo'lsa **avval `PROJECT.md` §13** tahrirlanadi:
  saqlanadigan shaxsiy ma'lumot ro'yxati e'lon qilingan va modul uni o'zi
  kengaytirmaydi.

**Qolgan beat vazifalari** (`app/tasks/celery_app.py` dagi izohda): GTS'dan
buyurtma statuslarini sync · idempotency kalitlarini tozalash · kataloglarni
yangilash · valyuta kurslarini yangilash. Hammasi 2-fazada.

---

## 4. Yo'lda qabul qilingan qarorlar

Kontraktda yo'q, lekin kodda bor — kelajakda "nega bunday?" degan savol
tug'ilmasligi uchun.

| # | Qaror | Sabab |
|---|---|---|
| 1 | Oxirgi `owner` ni o'chirish / bloklash / `admin` ga tushirish → **`409`**. O'zini o'chirish/bloklash ham | Aks holda client o'z panelidan chiqib qoladi va DB'ni qo'lda tahrirlashdan boshqa yo'l qolmaydi |
| 2 | Bekor qilingan refresh token qayta kelsa — **o'sha xodimning barcha sessiyalari** o'chadi | Qayta yuborish yo o'g'irlik signali; xato bo'lsa narxi bitta qayta login |
| 3 | `current_staff` **qatorni yuklaydi** | Bloklash/o'chirish darhol kuchga kiradi, token muddati kutilmaydi |
| 4 | `audit_log` da `staff` ga **FK yo'q** | Jurnal xodim ketgach ham o'qilishi kerak; boshqa modul jadvaliga FK — bu `models.py` ni import qilishning DB darajasidagi ko'rinishi |
| 5 | Audit **middleware**, dependency emas | Dependency javob statusidan oldin tugaydi; `route_class` merosga o'tmaydi va unutilishi mumkin |
| 6 | Audit yozuvi **alohida tranzaksiyada** | Yozuv bajarilmasa muvaffaqiyatli mutatsiya orqaga qaytmaydi — takroriy to'lovdan yo'qolgan jurnal qatori afzal |
| 7 | Yuklangan fayl baytlari **imzo bo'yicha** tekshiriladi | Brauzer MIME'ni kengaytmadan oladi; `<script>` li "PNG" keyin birovning brauzeriga qaytariladi |
| 8 | Singleton jadvallar: `UNIQUE(singleton) + CHECK(singleton IS TRUE)` | Ikkita worker birinchi so'rovda poyga qilsa ham bitta qator |
| 9 | Settings qatorlari **birinchi o'qishda** yaratiladi, migratsiyada emas | Default'lar tegishli maydon yonida turadi; migratsiya chiqqach muzlaydi |
| 10 | `settings/menu/` va `features.loyalty` — **yozilmagan / yoqib bo'lmaydi** | API.md §41, PROJECT.md §3 |
| 11 | `document`/`export` fayllari `/uploads/private/` ortida, staff token bilan | Eksport uchun keyinchalik imzolangan URL kerak (7-bosqich) |
| 12 | Saqlash kaliti kengaytmasi **tasdiqlangan MIME'dan** olinadi, client fayl nomidan emas; fayl route'i `media_type` ni o'sha jadvaldan beradi | Kengaytma javobning `Content-Type` ini belgilaydi — ya'ni client tanlagan kengaytma client tanlagan content type demakdir (§5.5) |
| 13 | Har bir fayl javobida `nosniff` + `Content-Security-Policy: default-src 'none'; sandbox` | SVG — XML, ichida `<script>` bo'lishi mumkin va imzo tekshiruvi buni ko'rmaydi. Sandbox uni alohida origin'ga tushiradi; `<img>` ga ta'sir qilmaydi |
| 14 | `Idempotency-Key` da in-flight belgisi **60 s**, saqlangan natija **24 soat** | Xato bergan handler kalitni bir kunga qulflab qo'ymasligi kerak — aks holda caller to'lovni umuman qila olmaydi |
| 15 | `.env.sample` dagi placeholder qiymatlar bilan **ko'tarilmaydi** (`DEBUG=true` dan tashqari) | `ENCRYPTION_KEYS` allaqachon shunday qilardi (base64 emas). Qolganlari uchun ham ataylab: `JWT_SECRET_KEY` — bu owner tokenini yasash imkoni |
| 16 | CORS ro'yxati **har so'rovda** `site.domain` dan olinadi, faqat `https` | Starlette uni bir marta, start'da o'qiydi — bu paneldan tahrirlanadigan qiymat uchun noto'g'ri umr. `http://` domenni qabul qilish PROJECT.md §13 dan paneldan chiqib ketish yo'li bo'lardi |
| 17 | `revoked_before` belgisi: sub'ekt bo'yicha "shu vaqtdan oldingi hamma narsa yaroqsiz". Xuddi **shu soniyada** berilgan token omon qoladi | Access token saqlanmaydi, demak `jti` bilan nomlab bo'lmaydi. Soniya oynasi ataylab: `iat` soniya aniqligida, aks holda odam o'z parolini o'zgartirgach darhol qilgan login'idan chiqib ketardi |
| 18 | Orphan grace `updated_at` dan hisoblanadi | Fayl ikki marta orphan bo'lishi mumkin; `created_at` almashtirilgan logoni darhol o'chirardi (§5.6) |
| 19 | `gts_credentials` da **soft delete yo'q** | O'chirilgan credential — keraksiz saqlanib qolgan client paroli. Unga hech qanday FK ishora qilmaydi, kim qachon o'chirgani audit jurnalida qoladi — ya'ni saqlashga arziydigan tarix allaqachon boshqa joyda |
| 20 | Birinchi qo'shilgan credential **o'zi aktiv** bo'ladi | Nol qatorda "qaysi biri ishlatiladi?" degan savolning to'g'ri javobi yo'q; usiz panel saqlangan, lekin hech narsa ishlatmaydigan credential ko'rsatardi |
| 21 | Aktiv credential'ni o'chirish — boshqasi bo'lsa `409`, yagonasi bo'lsa ruxsat | Taqiqlansa, bitta akkaunti bor client uni umuman o'chira olmaydi. Boshqasi turganda esa bu deyarli har doim noto'g'ri bosilgan tugma |
| 22 | GTS sessiya kaliti `{credential_id}:{updated_at}` dan yasaladi | Shunda aktivni almashtirish Redis'da hech narsa qilmaydi — muqobil yechim worker'lar aro invalidatsiya masalasini tug'dirardi (ARCHITECTURE.md §7) |
| 23 | Parol javobda **qat'iy uzunlikdagi** maska bilan qaytadi | API.md §29 API *kaliti*ning oxirgi belgilarini ko'rsatishga ruxsat beradi — ikkitasini farqlash uchun. Parolda bunday ehtiyoj yo'q, ko'rsatilgan har bir belgi esa taxmin qilinadiganidan bittaga kam |
| 24 | SMTP — **singleton**, GTS'dan farqli | O'rnatma pochtasini bitta relay orqali yuboradi; kontrakt ham shuni aytadi — `/integrations/notifications/` `{id}` olmaydi. `Entity` emas: soft delete qilingan singleton yagona o'rinni abadiy band qiladi (§8.3) |
| 25 | Qaysi notifier ishlatilishini **modul** hal qiladi, provayder emas | Argumentsiz modul-global ikki jihatdan noto'g'ri edi: bitta worker o'rnatgan qiymatni qolganlari ko'rmaydi, va u paneldan hozirgina o'zgartirilgan sozlamani qayta o'qiy olmaydi. Chuqurrog'i — bu sozlama savoli, javob berish uchun provayder `modules` ni import qilishi kerak bo'lardi (ARCHITECTURE.md §4 ruxsat bermaydi) |
| 26 | `enabled: true` faqat `host` va `from_address` bo'lganda | Aks holda panel "pochta yoqilgan" deydi-yu, hech narsa yetib bormaydi — bu hech kim xabar bermaydigan nosozlik, chunki tashqaridan hammasi joyida ko'rinadi |
| 27 | Relay rad etsa `test/` **`200`** qaytaradi, `502` emas | `502` owner'ga "nimadir buzildi" deydi, lekin nimasi buzilganini aytmaydi — bu tugma esa aynan shuning uchun bor. Sabab `detail` da va qatorda saqlanadi |
| 28 | Bo'lim bayrog'i sweep testi **teskari** yozilgan: "har bir route yo yadro, yo bayroq ostida" | To'g'ridan-to'g'ri "bayroqli route'da gate bormi" bugun **vakuum** bo'lardi — to'siladigan modul hali yo'q. Teskarisi esa hozirdan tishlaydi: 40 ta route aniq tasniflandi va 4-fazadagi birinchi kontent routeri ham majburan tasniflanadi |
| 29 | `CORE_PREFIXES` **testda**, ilova kodida emas | Bu sozlama emas — qabul qilingan qarorning yozuvi. Unga e'tiroz bildiriladigan joy — diff |
| 30 | `providers/gts/static.py` — `client.py` dan **alohida fayl** | GTS `/static/*` sessiya, cookie va `agent-uid` talab qilmaydi. Bittasiga qo'shib qo'yish fuqarolik dropdown'ini 2-fazaning sessiya menejeriga bog'lab qo'yardi, va GTS credential'i hali kiritilmagan o'rnatmada forma bo'sh qolardi |
| 31 | Static chaqiruvlarda **retry yo'q** (API.md §12 ikkitasiga ruxsat bersa ham) | Retry siyosati `client.py` ga tegishli — u yerda umumiy backoff va 401-relogin turadi. Oldida 24 soatlik kesh borligi uchun bir martalik nosozlik mingta so'rovdan bittasiga tegadi |
| 32 | Static so'rovda `follow_redirects=True` — paketning boshqa joyida **yo'q** | So'rovda credential ham, cookie ham yo'q, ya'ni redirect'da oqadigan narsa yo'q. Foydasi bor: DB'da `http://` bo'lib qolgan `base_url` 301 oladi va javobsiz qolmaydi. `client.py` buni ko'chirmasligi kerak |
| 33 | Static yo'llarda **trailing slash yo'q** | Bizning har bir yo'limiz slash bilan tugaydi (API.md §1), GTS static servisi esa aynan shunda `404` beradi. Kod noto'g'ridek ko'rinadi, shuning uchun izoh ham, test ham bor |
| 34 | `catalog` da `purge()` yo'q — TTL yagona invalidatsiya | `site-config` da TTL yozuvga qo'yilgan tozalashning orqasidagi to'r, chunki ma'lumot client'niki. Bu yerda uni bizda hech kim yozmaydi, tozalash uchun hodisa yo'q. Narxi — GTS tomonidagi tuzatish bir kungacha ko'rinmasligi |
| 30 | `RequireFeature` — class, `RateLimit`/`Audited` esa funksiya-fabrika | Sweep uni dependency daraxtidan `isinstance` bilan topishi kerak; closure'ga atribut osish ishlardi-yu, birinchi o'quvchidan omon qolmasdi |
| 31 | Noma'lum bayroq **import vaqtida** yiqiladi | `RATE_LIMITS[kind]` bilan bir xil: xato yozilgan bayroq jarayonni boot'da to'xtatadi, aks holda hech kim o'chira olmaydigan bo'lim jimgina xizmat qilaverardi |
| 32 | `register/` akkaunt qatorini **darhol**, tasdiqlanmagan holda yaratadi — kutayotgan ro'yxatlar uchun alohida joy yo'q | Bitta manzil bitta joyda turadi. Muqobili — tasdiqlanmagan yozuvlar do'koni, keyin uni `customers` bilan sinxron saqlash. `is_active` `email_verified_at` ni talab qilgani uchun bunday qator hech narsa bermaydi: u akkaunt emas, band qilingan joy |
| 33 | Band manzilga ham **`204`**, xabar esa **o'sha manzilning o'ziga** ketadi | `409` bo'lsa `register/` "bu odamning bizda akkaunti bormi?" degan savolga javob beradigan vositaga aylanardi, va u autentifikatsiyasiz. Kimga bilish kerak — manzil egasiga — o'ziga boradi |
| 34 | Kodlar **Postgres'da**, parol tiklash tokeni esa Redis'da | To'rt raqam faqat urinishlar shifti bilan yetarli, shift esa kesh tozalanganda nolga qaytmasligi kerak. Token boshqa narsa: uzun, bir martalik va qisqa umrli — `getdel` unga aynan mos |
| 35 | Qayta yuborish oynasi **jim** ishlaydi: `204`, lekin xat ketmaydi | Ko'rinadigan `429` "bu manzilda kutayotgan kod bormi?" deb aytardi — ya'ni §4.33 dagi `204` ning ma'nosini yo'q qilardi. Ko'rinadigan chegara API.md §14 niki bo'lib qoladi, u manzilni emas, IP'ni sanaydi |
| 36 | Mijoz auth hodisalari `audit_log` ga **yozilmaydi** | PROJECT.md §13 jurnalni **paneldagi** mutatsiyalar bilan chegaralaydi va `tests/contract/test_audit_coverage.py` faqat `/admin/*` ni supuradi. Mijoz login'lari `/admin/system/audit/` ni ko'mib tashlardi — u aynan xodimlar faoliyatini ko'rsatish uchun bor |
| 37 | Parol pastki chegarasi `customers` da **qayta yozilgan**, `staff` dan import qilinmagan | Bugun ikkalasi 8, lekin ular boshqa-boshqa narsa: biri client o'z xodimlariga qo'ygan siyosat, ikkinchisi ommaga qo'yilgani. Umumiy konstanta bo'lsa bittasini o'zgartirish ikkalasini o'zgartirgandek ko'rinardi |
| 38 | Yo'lovchi maydonlari **`PROJECT.md` §13 ro'yxatidan** olingan va undan oshmaydi | Saqlanadigan shaxsiy ma'lumot ro'yxati e'lon qilingan hujjat. Modul o'z ehtiyoji uchun unga maydon qo'shsa, ro'yxat hujjatda emas, kodda bo'lib qoladi — va PII inventarini kod bo'ylab yig'ib chiqish kerak bo'lardi |
| 39 | ~~`document_type` — **cheklanmagan satr**~~ — **№75 bilan qisman bekor** (endi JSONB obyekt); enum/CHECK yo'qligi kuchda | Katalog GTS tomonda. §26 `document-types/` endi uni klientga beradi, lekin ustunni cheklamaydi: lokal enum GTS ro'yxati o'zgarganda unga zid bo'lib chiqadi, CHECK esa bu xatoni tuzatishni migratsiyaga aylantiradi |
| 40 | Yo'lovchida **bitta hujjat**, ichma-ich ro'yxat emas | "Yo'lovchilar va hujjatlari" ikki xil o'qiladi. Saqlangan yo'lovchi bron uchun bitta hujjat bilan ishlatiladi; ko'plik kerak bo'lsa bu jadval qatorlar bilan kengayadi, sxema bilan emas |
| 41 | Avatar — **fayl emas, kod**: rasmlar to'plami klientda, server tanlangan variantning kodini saqlaydi (`customers.avatar_id`, `VARCHAR(64)`) | **2026-08-10 da avvalgi uchta qarorning o'rniga keldi** — ular avatarni yuklanadigan fayl deb qurgandi. Fayl bo'lmasa: yuklash yuzasi ham, notanish odamdan kelgan baytlar ham, imzo tekshiruvi ham, supurish ham, `avatar_url` ham kerak emas. Kod boshqa maydonlar qatori `PATCH /public/profile/` bilan yoziladi, `null` uni tozalaydi |
| 42 | `UploadPurpose.AVATAR` va `POST`/`DELETE` `/public/profile/avatar/` **olib tashlandi**, ishlatilmay qoldirilmadi | §4.41 dan keyin ular hech kim chaqirmaydigan yuklash yuzasi bo'lib qolardi — public yuzada esa endi umuman fayl qabul qilinmaydi. Migratsiya CHECK'ni toraytirishdan oldin `avatar` qatorlarini o'chiradi, baytlarni esa orphan sweep oladi: migratsiyada storage adapteri yo'q |
| 43 | Kod serverda **tekshirilmaydi** — ruxsat etilgan qiymatlar ro'yxati yo'q, faqat 64 belgi chegarasi | Ro'yxatni chiqaradigan tomon — klient, demak uni bilgan tomon ham o'sha. Serverda tursa har bir yangi rasm backend deployiga aylanardi, ustiga ilova va sayt bir xil to'plam chiqarishi shart emas. Noma'lum kod shundayligicha saqlanadi va qaytariladi; uni chiza olmaslik — yuborgan klientning ishi |
| 44 | Akkaunt o'chirilganda qator **bo'shatiladi**, faqat soft delete emas | `PROJECT.md` §13 "tozalanadi" deydi; `deleted_at` qatorni yashiradi, bo'shatmaydi. Manzilni ham bo'shatish — o'sha odam qaytadan ro'yxatdan o'ta olishining sharti, chunki unique indeks faqat tirik qatorlarni qamraydi |
| 45 | ~~`DELETE /public/profile/` **parol so'raydi**~~ — **№71 bilan bekor qilindi** | Mijoz tokeni bajara oladigan yagona qaytarib bo'lmaydigan amal. Narxi — `DELETE` da tana, va ba'zi klientlar (`httpx`) uni qisqartmada yubora olmaydi; kontraktda ogohlantirish bor |
| 46 | Yo'lovchi so'rovlari **har doim** egasi bo'yicha cheklangan, boshqa mijozniki `404` | `403` "bunday id bor" deb aytardi. Egasiz chaqirilishi mumkin bo'lgan yordamchi — bu oxir-oqibat egasiz chaqiriladigan yordamchi, shuning uchun `owned_passengers(customer_id)` dan boshqa kirish nuqtasi yo'q |
| 47 | Tanani chegara bilan o'qish `app/api/multipart.py` da, `uploads` ichida emas | Ikkinchi yuza (avatar yuklash) paydo bo'lganda ko'chirilgandi; §4.41 uni olib tashlagach ham qaytarilmadi. Muqobili — bir modul routerining boshqa modul routeridan yordamchi import qilishi, ya'ni ARCHITECTURE.md §4 to'sib turgan bog'liqlikning aynan o'zi. Yordamchi so'rov tanasi haqida, faylning maqsadi haqida emas |
| 48 | To'lov credential'lari — **bitta shifrlangan JSON obyekt**, kalit boshiga ustun emas | Payme va Click turli kalitlar so'raydi va ro'yxat har birining o'z hujjatidan keladi. Ustunlar bilan bo'lsa, kontraktda adapterlar keyin rad etadigan sxema paydo bo'lardi. Shu sababli `enabled: true` uchun ham faqat "bo'sh emas" tekshiriladi — aniq kalitlarni adapter biladi |
| 49 | `PATCH` credential'larni **birlashtiradi**, almashtirmaydi | Panel qiymatlarni faqat maskalangan holda oldi, ya'ni bitta kalitni tahrirlab qolganini qayta yubora olmaydi. Almashtirish rejimida bitta maydonni o'zgartirish qolganini o'chirardi |
| 50 | Maska ichida **bittagina** nuqta bo'lsa ham qiymat e'tiborsiz qoldiriladi | Birinchi versiya "hammasi nuqta" deb tekshirardi va haqiqiy maskani o'tkazib yuborardi: `mask_secret` oxirgi belgilarni ataylab ko'rsatadi. Provayder beradigan hech bir qiymatda `•` yo'q |
| 51 | Provayder qatorlari **birinchi o'qishda** yaratiladi, migratsiyada emas | Migratsiya faqat yozilgan paytdagi provayderlarni qamrardi. Bu yo'l bilan keyingi reliz qo'shgan provayder yangilangan o'rnatmada o'zi paydo bo'ladi — o'chirilgan holda |
| 52 | Standart `sort_order` — enum e'lon tartibi (o'nlab qadam bilan) | Aks holda tartib `code` bo'yicha alifbo bo'lardi, ya'ni hech kim so'ramagan savolga tasodifiy javob. O'nlab qadam keyingi provayderni ikkitasining orasiga qo'yish imkonini beradi |
| 53 | `health/` **tarmoqqa chiqmaydi**: `ok` "sinab ko'rishga narsa bor" degani | Bu endpoint pollanadi; har pollda GTS'ga kirish client'ning mashina akkauntini hech kim so'ramagan savolga sarflaydi. Da'vo `database`/`redis` nikidan torroq va buni docstring aytadi. Tiriklik — `test/` ning ishi, uni odam bosadi |
| 54 | `payment_logo` — alohida purpose, `logo` qayta ishlatilmadi | Ikkalasini boshqa-boshqa resurs biriktiradi, va `uploads.service.link` aynan shuni ajratish uchun `purpose` oladi. SVG bu yerda ruxsat: to'lov brendlari SVG chiqaradi va faylni xodim yuklaydi, do'kon ikonkasidan farqli |
| 55 | Social credential'da `client_id` **ochiq**, faqat `client_secret` shifrlanadi | `client_id` tugmani chizadigan har bir brauzerga baribir beriladi. Uni panelda yashirish operatorni Google konsolidagi qiymat bilan solishtira oladigan yagona qiymatdan mahrum qilardi |
| 56 | Google tasdiqlagan manzil **bu yerda ham tasdiqlangan** | Email OTP manzilni kimdir boshqarishini isbotlash uchun bor; `email_verified` aynan shuni isbotlaydi. Ustiga kod so'rash bir xil isbotni ikki marta so'rash bo'lardi. `email_verified: false` esa rad etiladi — bu istalgan odam boshqasining pochtasi haqida qila oladigan da'vo |
| 57 | Social oqim akkauntni **topadi yoki yaratadi**, tasdiqlanmaganini esa tasdiqlaydi | Bitta manzilda ikkita akkaunt — bitta odamning ikkita buyurtma tarixi. Yaratilgan qatorda parol xeshi bo'sh: uni taxmin qilib bo'lmaydi, kerak bo'lsa parol tiklash orqali qo'yiladi |
| 58 | `social/{provider}/` yo'l parametri **enum emas, satr** | Enum bo'lsa noma'lum provayder `422` bo'lardi va anonim chaqiruvchiga bizning ro'yxatimizni tasvirlardi. Tashqaridan "biz buni qo'llamaymiz" va "client buni o'chirgan" farq qilmasligi kerak — ikkalasi ham `404` |
| 59 | ID token **lokal tekshiriladi**, Google'dan so'ralmaydi | `tokeninfo` chaqiruvi uchinchi tomonni har bir kirishning kritik yo'liga qo'yardi va ularning uzilishi bizniki bo'lardi. Google kalitlarni o'zi chop etadi va shu yo'lni tavsiya qiladi; kalitlar Redis'da keshlanadi |
| 60 | Verifier override'i **faqat o'z provayderiga** javob beradi | Aks holda pinlangan Google verifier'i qo'llanmaydigan provayderni ishlata boshlardi va testlar hech kim yeta olmaydigan route haqida o'zlari bilan kelishib qolardi |
| 61 | `register/` da majburiy maydon **faqat ikkitasi**: `email` va `password`; `customers.first_name` `NULL` qabul qiladi | Akkaunt — manzil va uni tasdiqlagan narsa; ism esa profil ekranining ishi (§19). Ustun `NOT NULL` qolganda ro'yxatdan o'tish yo ismni majburlardi, yo o'ylab topilgan qiymat yozardi. Shu sabab social oqim ham endi manzil boshidan ism yasamaydi: mijozga o'zi tanlamagan ism o'ziniki bo'lib ko'rinardi |
| 62 | OTP **to'rt raqam** (`OTP_LENGTH`), generator kenglikni o'sha konstantadan oladi | Maydon million emas, o'n ming — demak `OTP_MAX_ATTEMPTS` endi haqiqiy himoya: shiftsiz kodni terib chiqish soniyalar ishi bo'lardi. Uch to'siq o'z joyida: beshta urinishda kod kuyadi, qayta yuborish orasi 60 s, §14 esa IP'ni 5/daqiqa bilan cheklaydi. Generator `f"…{10**OTP_LENGTH}…"` bilan yozilgan — uzunlikni ikkinchi joyda takrorlash kontrakt qabul qilmaydigan kod chiqarish demakdir |
| 63 | `is_profile_complete` — **modelda `@property`**, sxemada `computed_field` emas | Bu qator haqidagi fakt, sim shakli haqidagi emas: `is_verified` va `is_active` xuddi shu yerda turadi va `tests/unit` ularni sessiyasiz sinaydi. `ProfileOut` da bo'lganda shartni sinash uchun butun javobni yig'ish kerak bo'lardi. §4.41 `avatar_url` ni olib tashlagach `ProfileOut` boshqa modulga umuman murojaat qilmaydi, lekin sinash joyi o'zgarmaydi: shart qatorniki |
| 64 | To'lganlik sharti — `bool(value and value.strip())`, ya'ni **faqat bo'sh joy** to'lgan hisoblanmaydi | `PersonName` bir belgi so'raydi, demak `" "` ustungacha yetadi; `phone` da quyi chegara umuman yo'q, demak `""` ham yetadi. Ikkalasi ham "to'ldirdim" degan javob emas, va ularni sanash mijozga server "tugadi" deydigan, o'zi esa bo'sh ko'radigan profil qoldirardi. `[deleted]` sentineliga alohida shox yo'q: u faqat soft-delete qilingan qatorda paydo bo'ladi, u qator esa `get_active` dan o'tmaydi |
| 65 | `middle_name` `register/` da **yo'q**, faqat profilda | §4.61 ro'yxatdan o'tishni manzil va parolgacha qisqartirgandi; uchinchi ixtiyoriy shaxsiy maydon o'sha qarorni orqaga qaytarardi. Ota ismi profil ekranida so'raladi, u esa tasdiqdan keyingi birinchi ekran |
| 66 | Yo'lovchi `PATCH` ida `null` ni **`field_validator`** to'xtatadi, `model_validator` emas | `errors._field_from_location` maydon nomini `loc` dan oladi, model darajasidagi validatorda esa `loc` `("body",)` da to'xtaydi — 422 "nimadir xato" der edi, nimasi xatoligini emas (§3). `field_validator` default'lar ustida ishlamaydi, ya'ni u faqat kalit haqiqatan yuborilganda o'q otadi: "yo'q" bilan "`null`" farqli so'rov bo'lib qoladi. Faqat uchta majburiy maydon sanalgan — `staff` dagi "har qanday `None` ni tashlab ket" odati bu yerda hujjat raqamini o'chirishning yagona yo'lini yopib qo'yardi |
| 67 | `passengers.birth_date` `NOT NULL` ga o'tdi, **backfill'siz** | Tug'ilgan sana uchun halol placeholder yo'q — o'ylab topilgani chiptaga chiqadi. Migratsiya `NULL` qator ustida to'xtaydi va ustun nomini aytadi. §4.61 dagi `first_name` bilan farq ataylab: ismni manzildan yasash mumkin edi va uni mijoz tuzatardi, sanani esa yo'q |
| 68 | ~~`passengers.citizenship` — **cheklanmagan satr**~~ — **№75 bilan qisman bekor** (endi JSONB obyekt); enum/ISO tanlamaslik kuchda | `document_type` bilan bir sabab: davlatlar katalogi GTS tomonda va §26 `countries/` uni faqat **ko'rsatadi**, cheklamaydi — lokal ro'yxat GTS ro'yxati o'zgarganda unga zid bo'lib chiqardi. Ustiga kod tanlash standartni ham tanlash demak — "UZ" mi, "UZB" mi, kontrakt hali aytmagan. Satrni keyin kengaytirish arzon, upstream bilan kelishmaydigan CHECK'ni yechish esa yo'q |
| 69 | `SMTP_*` env'ga qo'shildi — lekin **urug' sifatida**: faqat `host` bo'sh qatorda o'qiladi | Bo'sh baza pochta yubora olmaydi, pochtasiz esa parol tiklash kodi ham ketmaydi — ya'ni paneldan sozlash uchun avval panelga kirish kerak, kirish uchun esa pochta. `FIRST_OWNER_*` shu tugunni xodim tomonidan yechgandi, bu — relay tomonidan. Sozlama baribir DB'da: qator to'lgach env boshqa o'qilmaydi, shuning uchun keyin tahrirlangan `.env` client tanlaganini almashtira olmaydi. `tests/unit/test_config.py` dagi tripwire saqlanib qoldi — urug'lar `SEED_FIELDS` da nomma-nom sanaladi, ya'ni yangi `smtp_*` maydon baribir testni yiqitadi |
| 70 | Xat **matn va HTML** bo'lib bitta konvertda ketadi; HTML `providers/notifications/html.py` da, jadval va inline CSS bilan | Kod ko'taradigan xat — yangi mijoz brendni ko'radigan birinchi joy, va yalang'och matn hech kimnikiga o'xshamaydi. Matn qismi qoladi: uni ko'rsatolmaydigan mijoz ham kodni oladi. Markup ataylab eskicha — Gmail `<style>` blokini olib tashlaydi, Outlook esa Word orqali chizadi, flex/grid ikkalasida ham ishonchsiz. Brend qiymatlari argument bo'lib kiradi, chunki provayder `settings` ni import qila olmaydi (ARCHITECTURE.md §4) |
| 71 | `DELETE /public/profile/` **parol emas, sabab so'raydi** — №45 bekor: token o'chirishga yetarli, tana `{reasons: [matn…]}` va u **majburiy** | Mahsulot qarori: ketayotgan mijozdan parol emas, sabab qimmat. Matnlar mijoz ko'rgan tilda **aynan kelganicha** saqlanadi, lug'atga solishtirilmaydi — shuning uchun sabab keyin tahrirlangan yoki o'chirilgani tarixni buzmaydi. `DELETE` tanasi haqidagi `httpx` ogohlantirishi kuchda qoladi |
| 72 | `deleted_customers` — o'chirishdan **oldingi to'liq shaxsiy nusxa** + sabablar, FK'siz, faqat qo'shiladigan jadval | Jonli qator baribir tozalanadi (PROJECT.md §13 saqlanib qoldi), lekin biznes kimlar va nima uchun ketganini ko'rishi kerak. FK yo'q: arxiv hech nimaga bog'lanmaydi va hech nima uni kaskadda o'chira olmaydi. Hech bir API qaytarmaydi; saqlash muddati — §16 dagi ochiq savollar qatorida |
| 73 | `deletion_reasons` lug'ati — `text` JSONB + `sort_order`, **holatsiz va bayroqsiz** | FAQ'dagi draft/publish tahririy ehtiyoj uchun edi; besh qatorlik lug'atga yashirish = soft delete yetadi. O'chirish oqimini o'chirib bo'lmagani uchun uning lug'atiga `RequireFeature` ham ma'nosiz |
| 74 | `support_topics` — murojaat formasining mavzu lug'ati, №73 naqshida (`name` JSONB + `sort_order`, holatsiz); murojaat **matnni** saqlaydi, `topic_id` emas | Klient tanlangan mavzuning siqilgan matnini mavjud `topic` maydonida yuboradi — lead kontrakti o'zgarmadi, №71 dagi verbatim tamoyili takrorlanadi. Bu G1 ni bekor qilmaydi: sxema emas, tanlov ro'yxati xolos. `leads` moduli ichida, `leads` bayrog'i ostida |
| 75 | `passengers.citizenship` va `document_type` — §26 katalog obyekti **aynan kelganicha, JSONB**; tekshiruv faqat `"code"`/`"type"` bo'sh bo'lmagan satr ekani. №39 va №68 ni **qisman bekor qiladi** — enum/CHECK hali ham yo'q, eski satr qiymatlar migratsiyada NULL | UI yo'lovchini nomi tarjimalari va bayrog'i bilan ko'rsatadi — kodning o'zi (`"UZ"`, `"PSP"`) buni bermaydi, ikkinchi lug'atni qo'lda yuritish esa §26 rad etgan yo'l. Obyekt verbatim saqlanadi: GTS katalogni o'zgartirsa ham saqlangan nusxa o'z holicha o'qilaveradi. Identifikator kaliti tekshiriladi, chunki bron oqimi ertaga GTS'ga kod yuborishi kerak bo'ladi |
| 76 | `fun_fact` (2026-08-13): maydon `search/` javobida **doim bor**, fakt yo'q bo'lsa `null` — bayroq yo'q; tanlash SQL'da `ORDER BY random()`; o'qish `products/service.py` da `adapter.code == FLIGHT` sharti bilan, adapterda emas | Bo'sh jadval allaqachon o'chirish tugmasi — bayroq o'sha simga ikkinchi kalit bo'lardi. Adapter sessiyani olmaydi (`providers/products/base.py`), shuning uchun o'qish service qatlamida; boshqa vertikallar tegilmaydi. Fakt hech qayerda keshlanmaydi — passthrough testining Redis supurgisi shunday talab qiladi |
| 77 | **`booking/` sagasiz, sof passthrough** (2026-08-14): buyurtma qatori yo'q, `payment_id` yo'q, outbox yo'q — API.md §20 dagi "bron → buyurtma va `payment_id`" tavsifi shunga ko'ra tahrirlandi. PHASES.md 9-bo'lagi (saga) o'z o'rnida qoladi va shu endpoint **ustiga** quriladi | Klientga bugun kerak bo'lgan narsa — GTS oqimini uchidan-uchiga aylanib chiqish; `orders`+`payments`+saga esa uchta modul va eng yuqori xavfli bo'lak. Ularni kutish butun vertikalni to'sib turardi. So'rov shakli saga kelganda ham o'zgarmaydi — faqat javobga bizning maydonlarimiz qo'shiladi, ya'ni bu tashlab yuboriladigan ish emas |
| 78 | **`cancel/` — oqim qadami** (`POST /public/flight/cancel/`), API.md §21 dagi `orders/{id}/cancel/` emas; so'rov tanasi **umuman tekshirilmaydi** | Lokal buyurtma yozuvi yo'q ekan, `orders/{id}/` yo'lining `{id}` si bizda mavjud emas — bekor qilishni buyurtma resursiga osib bo'lmaydi. Tana tekshirilmasligi ataylab: GTS bronni qaysi maydon bilan nomlashi hujjatlarda yozilmagan, noto'g'ri taxmin esa haqiqiy bekor qilishni GTS ko'rmasdan rad etardi — narxi haqiqiy o'rin |
| 79 | `booking/` va `cancel/` da **`Idempotency-Key` talab qilinmaydi**, qo'shimcha rate limit ham yo'q — `api/idempotency.py` hamon iste'molchisiz | Bu qadamlar hozir hech qanday holatni o'zgartirmaydi: takroriy so'rov GTS'da ikkinchi bron ochadi, lekin bizda tuzatiladigan yozuv yo'q, ya'ni kalit himoya qiladigan narsaning o'zi yo'q. API.md §10 talabi **pul yo'li** uchun — u saga bilan keladi va o'shanda kalit shu endpointga qo'yiladi. §14 bo'yicha bron "boshqa public" (120/daq), ya'ni public yuzasining limiti yetadi |
| 80 | **Buyurtma qatori sagadan oldin** (2026-08-17): `booking/` javobi `orders` ga `JSONB` bo'lib yoziladi, `cancel/` esa `order_id` bo'yicha egalikni tekshiradi — `payment_id` ham, outbox ham, holat mashinasi ham yo'q. №77 ni bekor qilmaydi: u saga haqida edi, bu yozuv haqida | Ikkita muammoni bitta jadval yopadi — mijoz o'z buyurtmalarini topa olmasligi va №78 qoldirgan egalik tuynugi (§8.14, 🔴). Ikkalasi ham sagani kutishi shart emas edi: egalik uchun **yozuvning o'zi** yetarli, holat mashinasi esa pul yo'li uchun kerak. Javob shakli o'zgarmadi, ya'ni saga kelganda bu ish tashlanmaydi — ustiga qo'yiladi |
| 81 | **Status GTS kodi bo'lib saqlanadi** (`BO`/`TI`/`CB`/…), kanonik enum emas; xarita 6-bo'lakdan **9-bo'lakka ko'chdi** | Kanonik enum'ning birinchi haqiqiy iste'molchisi — bekor qilish/qaytarish qoidalari va `available_actions`, ular esa saga bilan keladi. Bugun xarita qurilsa u iste'molchisiz taxmin bo'lardi, ustiga har vertikal uchun alohida yoziladi va faqat bittasi mavjud. GTS kodini kelganicha saqlash esa hech narsani yo'qotmaydi — o'girish keyin, saqlangan qiymat ustida bajariladi |
| 82 | **Buyurtma ichi ochilmaydi**: `gts_response` blob, undan faqat `order_id`, `status`, `request_id`, `offer_id` ustunga ajratiladi; bron **so'rovidagi `passengers` bloki saqlanmaydi** | Ustunga chiqarilganlar — indeks kerak bo'lganlar (egalik, filtr, saralash), boshqasi emas. Yo'lovchilar esa pasport raqami: PROJECT.md §13 buyurtmani anonimlashtirishni va'da qiladi, ichini bilmaydigan blobni esa anonimlashtirib bo'lmaydi — ya'ni to'liq so'rovni saqlash bajarib bo'lmaydigan va'da yaratardi. `save_passenger` orqali yo'lovchi `passengers` jadvaliga baribir tushadi |
| 84 | **GTS bron/bekor qilish shakli kolleksiyadan olindi** (2026-08-17): `EASY_GATEWAY` kolleksiyasining `/content/Booking` va `/content/Cancel` yozuvlari topilib, kod ularga moslandi. Uchta o'zgarish: (a) javob **ikki qavatli** — buyurtma maydonlari ichki `data` da, biz esa yuqori qavatdan o'qiyotgan edik; (b) bekor qilish handle'i `order_id` emas, **`order_number`** (butun son), shunga ko'ra ustun `gts_order_number` ga qayta nomlandi va `gts_order_uid` qo'shildi; (c) yo'lovchi shakli §19 dan farq qiladi — `citizenship` bare kod, hujjat maydonlari `document` ichida, ustiga `gender`/`issue_date`/`email`/`phone` bizda umuman saqlanmaydi | Avvalgi shakl `products/openapi.py` dagi hujjat taxminidan olingan edi va **noto'g'ri chiqdi**: u bilan har bir `gts_order_id` `NULL` bo'lardi, bekor qilish esa qattiq bo'lgani uchun **har doim `404`** berardi — ya'ni bron ishlab, bekor qilish umuman ishlamasdi. Kolleksiya — yozib olingan haqiqiy chaqiruv, hujjatdan ustun manba |
| 83 | `booking/` da **yozuv xatosi so'rovni yiqitmaydi** — log'ga yoziladi va GTS javobi qaytadi | GTS o'rinni allaqachon ushlab turibdi. `500` mijozni qayta urinishga, u esa **ikkinchi bronga** olib borardi — haqiqiy o'rin va keyinroq haqiqiy pul. Javobdagi `order_id`/`pnr` mijoz qo'lida qoladi. To'g'ri yechimi outbox (ARCHITECTURE.md §8), u 9-bo'lakda; §8.16 shu oraliqni ochiq kamchilik sifatida yuritadi |
| 85 | **Profil telefoni obyektga aylandi** (2026-08-18): yassi `phone VARCHAR(32)` o'rniga `phone_code` + `phone_number` + `phone_mask` uchta ustun; `deleted_customers` arxivi ham shunday. `is_profile_complete` uchun `phone_code` va `phone_number` ikkalasi ham kerak, `phone_mask` sanalmaydi | GTS bron tanasi telefonni `{phone_code, phone_number}` shaklida kutadi (№84), ya'ni yassi satr saqlangani bilan bron uni **ishlata olmasdi** — shakl mijozdan telefonni qayta so'rashga majbur edi. `phone_mask` yoniga qo'shildi, chunki §26 katalogidan olingan maska bo'lmasa klient saqlangan raqamni ko'rsatish uchun katalogga ikkinchi marta boradi; u `avatar_id` kabi server uchun shaffof matn. Eski qiymatlar migratsiyada `+998XXXXXXXXX` namunasi bo'yicha ajratildi, mos kelmaganlari `phone_number` da kodsiz qoldi |

---

## 5. Topilgan va tuzatilgan tuzoqlar

Bular yana takrorlanishi mumkin — shuning uchun yozib qo'yilgan.

1. **`docker/bootstrap.py` xatoni yashirardi.** `except ImportError` bloki
   modul paydo bo'lgach ham ushlab turardi: `python /app/docker/bootstrap.py`
   da `sys.path[0]` `docker/` bo'ladi, ya'ni **konteynerdagi birinchi boot'da
   ham** owner yaratilmasdan jimgina o'tib ketardi. Endi `sys.path` aniq
   to'g'rilanadi va entrypoint xatoda to'xtaydi.

2. **Alembic har autogenerate'da `ck_staff_staff_role` ni o'chirmoqchi bo'lardi.**
   SQLAlchemy enum CHECK'ini POSTCOMPILE parametr bilan renderlaydi, Alembic
   uni serverdagi bilan solishtira olmaydi. `migrations/env.py` dagi
   `_include_object` CHECK'larni autogenerate'dan chiqaradi —
   **CHECK'lar qo'lda yoziladi** (`create_table` ichidagilari saqlanadi).

3. **`.gitignore` / `.dockerignore` da `uploads/`** naqshi
   `app/modules/uploads/` ni ham yutardi — modul commit'ga va image'ga
   tushmasdi. Ikkalasi ham repo ildiziga bog'landi (`/uploads/`).

4. **Kontrakt oqishi:** framework `HTTPException` uchun handler katalog
   **kodini**, lekin Starlette **statusini** qaytarardi — noto'g'ri metod
   `405 not_found` bo'lardi, API.md §3 da esa `405` yo'q. Endi katalog statusi
   qaytadi.

### 2026-08-06 — 1-bosqich kodini qayta ko'rib chiqishda topilganlar

Oltitasi ham tuzatildi. Hujjatning o'zi to'g'ri edi (faqat ikkita raqam xato:
1-bo'limdagi "2 mezon" va 2-bo'limdagi `settings` = 9) — muammo kodda edi.

5. 🔴 **Yuklangan fayl o'z origin'imizdan `text/html` bo'lib qaytardi.**
   Kalit client bergan kengaytmani saqlardi
   (`storage/local.py::build_key`), fayl route'i esa `FileResponse` ga
   `media_type` bermasdi — Starlette uni `guess_type(path)` bilan topardi.
   PNG imzosi + `filename="x.html"` → `logo/ab/<hex>.html` → autentifikatsiyasiz
   **stored XSS**, panel bilan bitta origin'da. Imzo tekshiruvi buni ko'ra
   olmasdi, chunki u faqat baytlarning boshiga qaraydi. Tuzatish — §4.12–13.

6. 🔴 **`Idempotency-Key` da'vosi atomik emas edi.** `SET … NX` chaqirilardi,
   lekin **javobi tashlab yuborilardi** — qaror hamon undan oldingi `GET` da
   qolardi, ya'ni ikkita bir xil so'rov ikkalasi ham "yozuv yo'q" deb o'tib,
   **ikki marta yechardi**. Modul aynan shu holat uchun bor. Endi `NX` ning
   javobi hal qiladi.

7. 🟠 **`JWT_SECRET_KEY` ning ishlaydigan default'i bor edi** va aynan o'sha
   satr `.env.sample` da chop etilgan. O'zgaruvchini unutgan o'rnatma
   ko'tarilardi va o'sha kalit bilan imzolardi — istalgan kishi `aud: admin`,
   `role: owner` token yasay olardi. §4.15.

8. 🟠 **Prod'da CORS `[]` edi** ("settings moduli paydo bo'lguncha" izohi bilan),
   ya'ni panel o'z API'siga murojaat qila olmasdi; `debug` da esa `["*"]` +
   `allow_credentials=True` — brauzer rad etadigan juftlik. §4.16.

9. 🟠 **Parol o'zgartirilganda access token tirik qolardi.** Denylist'ga faqat
   **refresh** jti yozilardi, `change_password` esa "har bir sessiya o'ladi"
   deb yozilgan — sizib chiqqan access token yana 15 daqiqa ishlardi. §4.17.

10. 🟠 **Faylni uzish uni darhol supurilishga ochardi.** `sweep_orphans` cutoff'ni
    `created_at` bo'yicha hisoblardi, `unlink` esa uni o'zgartirmasdi — olti oy
    tirik turgan logo almashtirilsa keyingi soatlik supurish baytlarni
    o'chirardi. Va'da qilingan 24 soat aynan o'zi uchun yozilgan holatda
    ishlamasdi. §4.18.

### 2026-08-06 — enum CHECK'ini birinchi marta qayta yozishda

`UploadPurpose` ga `avatar` qo'shish uchun `ck_uploads_upload_purpose` qo'lda
qayta yozildi. (O'sha purpose 2026-08-10 da olib tashlandi — §4.42; bu yerdagi
ikki tuzoq esa har bir enum-CHECK migratsiyasiga tegishli bo'lib qoldi.) Zanjirdagi **birinchi shunday migratsiya**, va
ikkita tuzoq ketma-ket chiqdi — keyingisi shu yerdan nusxa olsin:

11. **Nomi ikki marta prefikslanadi.** `op.drop_constraint('ck_uploads_upload_purpose', …)`
    ga metadata nomlash konvensiyasi (`ck_%(table_name)s_%(constraint_name)s`)
    **ustidan** qo'llanadi va natija `ck_uploads_ck_uploads_upload_purpose`
    bo'ladi — `UndefinedObjectError`. Nom `op.f(...)` bilan "tayyor" deb
    belgilanishi kerak; mavjud migratsiyalar `op.f('ck_..._singleton')` ni
    aynan shuning uchun ishlatadi.
12. **`op.f(...)` modul darajasida chaqirilmaydi.** `op` — migratsiya
    ishlayotgandagina mavjud proxy, shuning uchun konstanta sifatida yozilsa
    skriptni **shunchaki yuklaydigan** har bir buyruq yiqiladi: `alembic heads`
    ham. Nom oddiy satr bo'lib qoladi, `op.f(...)` esa har bir ishlatish
    joyida chaqiriladi.

    Uchinchisi tuzoq emas, lekin yodda tutilsin: CHECK'ni toraytiradigan tomon
    undan tushib qoladigan qatorlarni **oldin** o'chiradi, aks holda mavjud
    qator yangi constraint'ni buzadi.

---

## 6. Lokal muhit

- **`.env` dagi `POSTGRES_USER=postgres` lokal Postgres'ga mos emas** — mavjud
  rol `djumanov`. Buyruqlar hozircha shunday ishlatilmoqda:
  ```bash
  env POSTGRES_USER=djumanov POSTGRES_PASSWORD= uv run pytest
  ```
  Yo `.env` tuzatilsin, yo `postgres` roli yaratilsin.
- **Lokal `.env` da `DEBUG=true` bo'lishi kerak.** U hozircha `.env.sample`
  nusxasi, ya'ni `DEBUG=false` va placeholder parollar bilan — endi bu
  kombinatsiya ataylab ko'tarilmaydi (§4.15):
  ```
  Value error, FIRST_OWNER_PASSWORD, POSTGRES_PASSWORD still holds the value
  from .env.sample …
  ```
  Yo `DEBUG=true` qo'yilsin, yo uchala qiymat almashtirilsin.
- Test to'plami buni **o'zi hal qiladi**: `tests/conftest.py` `app` import
  qilinishidan oldin `DEBUG=true` ni `setdefault` qiladi, shuning uchun run
  mashinadagi `.env` ga bog'liq emas.
- Integratsiya testlari `b2c_test` bazasini o'zi yaratadi va har run'da
  sxemasini migratsiya zanjiri bilan qayta quradi. Boshqa joyga yo'naltirish:
  `TEST_DATABASE_URL`.

---

## 7. Ochiq savollar

PROJECT.md §16 dagi oltitasi. Qaysi biri qachon to'sadi:

| Savol | Qachon kerak |
|---|---|
| Qisman qaytarish siyosati | 2-bosqich oxiri, `payments` |
| Buyurtmani paneldan tahrirlash | 4-bosqich, `orders` |
| Dashboard ko'rsatkichlari | 4-bosqich, `reports` |
| Menyu modeli (sahifa yarmi yechildi — markdown, [PHASES.md](PHASES.md) §2.14) | 5-bosqich |
| Saqlash muddatlari | 7-bosqich |
| Kutilayotgan yuk | server o'lchami; D2 sababli har `offers/` GTS'ga boradi |
| **Buyurtmani anonimlashtirish** (yangi, PROJECT.md §13) | Jonli bron ko'rilganda. GTS javobi yo'lovchi qaytarsa blob ichida pasport raqami qoladi va akkaunt o'chirilganda uni tozalab bo'lmaydi — o'shanda avval §13, keyin kod (§8.17) |
| **GTS 2FA (D1)** | 2-bosqich. Kolleksiyadan aniqlandi: bu **akkaunt bayrog'i** (`two_factory`), ya'ni GTS tomonda o'chiriladi — kod masalasi emas ([GTS.md](GTS.md) §3). Yoniga ikkita band qo'shildi: `white_list` (client IP'si) va `is_single` |

---

## 8. Ma'lum kamchiliklar

> **Buyurtma tizimi qayta loyihalandi (2026-08-18).** Quyidagi 🔴/🟠 bandlarning
> aksariyati [`order-system/02-current-audit.md`](order-system/02-current-audit.md) da
> `A1…A11` sifatida qayta ta'riflangan va yechimi
> [`order-system/03-design.md`](order-system/03-design.md) da belgilangan.

2026-08-06 ko'rigida topilgan, lekin **hali tuzatilmagan**. Hech biri to'smaydi;
yo'qolib ketmasligi uchun yozilgan. Tartib — jiddiyligi bo'yicha.

1. **Qo'lda yig'ilgan envelope'ni ushlaydigan test yo'q.**
   `api/envelope.py:123-125` "buni kontrakt testi flag qiladi" deydi, lekin
   bunday test mavjud emas — `tests/contract/test_routes.py` faqat
   `route_class` ni tekshiradi. Va `settings/router_public.py` allaqachon shu
   yo'ldan ketgan (`json_response(success_envelope(...))`, ETag uchun; `_wrap`
   endpoint sarlavhalarini saqlagani uchun kerak ham emas). Ya'ni himoya emas,
   ochiq eshik.
2. **500 javobida `X-Request-Id` yo'q.** `api/middleware.py:51-62` xatoni
   qayta ko'taradi, javobni esa `ServerErrorMiddleware` yasaydi — bu
   middleware'dan tashqarida, ya'ni sarlavha qo'yilmaydi. Support suhbati aynan
   shu javobdan boshlanadi (API.md §13). Shu sababdan 500 da CORS sarlavhalari
   ham yo'q.
3. **Singleton o'qishi `deleted_at` ni ko'rmaydi.**
   `settings/repository.py:36` — `select(model).limit(1)`, `UNIQUE(singleton)`
   esa soft-delete qilingan qatorni o'rnini abadiy band qiladi. Hozir hech narsa
   ularni o'chirmaydi, shuning uchun latent — lekin `SingletonMixin` va
   `SoftDeleteMixin` juftligi ziddiyatli.
4. `settings/service.py` da noto'g'ri `logo_id` → **`404`**; maydon qiymati
   xato bo'lgani uchun `422` to'g'riroq (noto'g'ri `purpose` shoxi aynan shunday
   qiladi).
5. `staff/service.py::confirm_password_reset` da `uuid.UUID(str(raw))` — buzuq
   Redis qiymatida `500`.
6. `staff/service.py::send_reset_password_link` (owner boshlaydigan) da
   `is_blocked` tekshiruvi yo'q; `request_password_reset` da bor.
7. `uploads/service.py::count_orphans` barcha id'ni yuklab `len()` qiladi —
   `select(func.count())` bo'lishi kerak.
8. `audit/middleware.py` jurnal yozuvini so'rov yo'lida `await` qiladi.
9. `api/envelope.py` sarlavhalarni **almashtiradi**, qo'shmaydi — bugun cookie
   yo'q, `Set-Cookie` paydo bo'lsa muhim bo'ladi.
10. `api/deps.py::_rate_limit_subject` refresh tokenni ham autentifikatsiyalangan
    sub'ekt deb sanaydi.
11. **Muddati o'tgan sessiya va kod qatorlarini hech kim tozalamaydi.**
    `customer_refresh_tokens`, `email_otps` va `staff_refresh_tokens` faqat
    o'sadi. Hech biri noto'g'ri javob bermaydi — o'qishlar `expires_at` va
    `revoked_at` ni tekshiradi — lekin bu uchtasi bitta beat vazifasi bilan
    hal bo'ladigan **bitta** ish, shuning uchun `customers` bo'lagining ichiga
    yashirilmadi. Qolgan beat vazifalari bilan birga (§3).
12. `core/crypto.py` AAD ishlatmaydi — shifrmatnni bir ustundan boshqasiga
    ko'chirishni aniqlab bo'lmaydi. **Endi haqiqiy**: `smtp_settings.password`
    bilan shifrmatn saqlaydigan ikkinchi ustun paydo bo'ldi, ya'ni "ko'chirish"
    nazariy bo'lmay qoldi. SMTP bo'lagida **ataylab qilinmadi**, chunki AAD
    qo'shish mavjud ikkala jadvaldagi qatorlarni qayta shifrlaydigan **ma'lumot
    migratsiyasi**ni talab qiladi — bu alohida bo'lak, SMTP sozlamasining
    ichiga yashiriladigan narsa emas.
    ⚠ Narxi vaqt o'tgani sari oshadi: hozircha qatorlar faqat dev bazasida.

### 2026-08-17 — buyurtma qatori qo'shilganda

15. 🟠 **GTS javobining maydonlari kolleksiyadan olindi, jonli emas.**
    2026-08-17 da `EASY_GATEWAY` kolleksiyasidagi `/content/Booking` va
    `/content/Cancel` yozuvlari topildi va kod ularga moslandi (№84). Bu
    avvalgi 🔴 ni tushirdi: endi maydon nomlari taxmin emas, **yozib
    olingan haqiqiy chaqiruv**. Lekin ular hamon *shu o'rnatmaning* jonli
    GTS'ida ko'rilmagan. Yozuv o'qilmasa qator baribir yoziladi va
    `order_recorded_without_gts_number` ogohlantirishi ikkala qavatning
    maydon ro'yxati bilan log'ga tushadi.

15a. 🔴 **Bekor qilish javobining shakli mos kelmayapti.** Kolleksiyada
    saqlangan yagona `cancel` javobi eski shaklda — `{status, code, order}`,
    ya'ni unda **`data` kaliti yo'q**. Bizning `providers/gts/client.py`
    `_translate` esa envelope'siz rejimda `data` ni talab qiladi va bo'lmasa
    `502 upstream_error` beradi. Agar jonli GTS ham shu shaklni qaytarsa,
    **bekor qilish har doim 502 bo'ladi** — bron esa ishlaydi. Bu jonli
    tekshiruvda **birinchi ko'riladigan narsa**. Tuzatish arzon (`cancel`
    uchun `post_envelope` yoki alohida shakl), lekin qaysi biri kerakligini
    haqiqiy javobsiz tanlab bo'lmaydi.

16. 🟠 **Bron yozilmay qolsa buyurtma yo'qoladi.** `booking/` da GTS javob
    bergandan keyingi `INSERT` xato bersa, so'rov baribir `200` qaytaradi
    (izohi API.md §20 da: `500` qaytarish mijozni qayta urinishga va
    **ikkinchi bronga** olib borardi — haqiqiy o'rin). Oqibati: o'sha bron
    bizda ko'rinmaydi va u orqali bekor qilib bo'lmaydi; mijoz qo'lida faqat
    javobdagi `order_id`/`pnr` qoladi. To'g'ri yechimi — holat o'zgarishi
    bilan bitta tranzaksiyadagi outbox (ARCHITECTURE.md §8), u **9-bo'lakda**.
    Hozircha `order_not_recorded` log'i.

17. 🔴 **Buyurtma anonimlashtirilmaydi, va endi bu tasdiqlangan.**
    Kolleksiyadagi bron javobida `data.passengers[]` bor va uning ichida
    `firstname`, `lastname`, `birth_date`, `gender`, `email_address`,
    `phone_number` hamda `document.passport_number` / `passport_issuance` /
    `passport_expiry` turibdi. Ya'ni biz opaque blob sifatida saqlayotgan
    javob **pasport raqamini o'z ichiga oladi**. So'rovdagi yo'lovchi bloki
    saqlanmasligi (№82) bu teshikni yopmaydi — ma'lumot javob orqali
    baribir kiradi.
    Oqibati: akkaunt o'chirilganda PROJECT.md §13 va'da qilgan
    anonimlashtirishni bajarib bo'lmaydi. Bu **qaror talab qiladi** va
    hujjat tartibi bo'yicha avval §13 tahrirlanadi, keyin kod. Variantlar:
    blobdan `passengers` ni chiqarib tashlash · uni alohida shifrlangan
    ustunga ajratish · yoki §13 ni "buyurtma javobi to'liq saqlanadi" deb
    qayta yozish.

18. 🟡 **`orders` da soft delete ishlatilmaydi.** Jadval `Entity` dan meros
    olgani uchun `deleted_at` bor va barcha o'qishlar `live()` orqali ketadi,
    lekin uni yozadigan yo'l yo'q. Buyurtma moliyaviy hujjat (PROJECT.md
    §13), ya'ni o'chirilmaydi — ustun kelajakdagi arxivlash uchun turibdi va
    unikal indeks uni allaqachon hisobga oladi.

### 2026-08-14 — bron va bekor qilish passthrough sifatida qo'shilganda

14. ✅ **`POST /public/flight/cancel/` da egalik tekshiruvi yo'q edi** —
    **2026-08-17 da yopildi** (№80). Buyurtma qatori paydo bo'ldi va u
    egalikning manbai: `order_id` bo'yicha shu mijozning yozuvi topilmasa
    `404`, GTS'ga umuman borilmaydi
    (`tests/integration/test_orders.py`).

### 2026-08-06 — GTS credential'lari yozilayotganda topilgani

13. 🟠 **`mask_secret(value, visible=0)` sirni to'liq qaytarardi.**
    `value[-0:]` — bu `value[0:]`, ya'ni butun satr; umumiy ifoda uni o'z
    maskasiga qo'shib qo'yardi. Hech kim hali bunday chaqirmagan edi, lekin
    "hech narsa ko'rinmasin" degan so'rov sirni chop etishi jimgina kutib
    turgan tuzoq. Endi alohida shox.
