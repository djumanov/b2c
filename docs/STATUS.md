# Holat va qolgan ish

**Oxirgi yangilanish:** 2026-08-06 · `main` = `491f6fa`

Bu hujjat **avtoritet emas** — kontrakt uchun [API.md](API.md), tuzilma uchun
[ARCHITECTURE.md](ARCHITECTURE.md), qamrov va bosqichlar uchun
[PROJECT.md](PROJECT.md) ustun turadi. Bu yerda faqat **hozir nima qurilgan,
nima qolgan** va yo'lda topilgan, koddan ko'rinmaydigan qarorlar yozilgan.

---

## 1. Bir qarashda

| | |
|---|---|
| Bosqich | **1 — Yadro**, 3 qabul mezonining **uchalasi ham** bajarilgan |
| Endpointlar | 23 ta yo'l / 31 operatsiya (API.md dagi ~150 dan) |
| Jadvallar | 10 ta + `alembic_version` |
| Migratsiyalar | 5 ta, bitta head (`c4adf0023f38`) |
| Testlar | 331 ta — unit 12 fayl · contract 6 · integration 10 |
| Gate'lar | ruff · mypy strict · pytest — hammasi yashil |

**1-bosqich qabul mezonlari** (PROJECT.md §15):

| Mezon | Holat |
|---|---|
| Panelga `owner` sifatida kirish mumkin | ✅ `tests/integration/test_staff_auth.py` |
| Brend rangi o'zgarsa `site-config` da **deploysiz** aks etadi | ✅ `tests/integration/test_site_config.py` |
| `admin` tokeni `owner` talab qiladigan endpointda `403` oladi | ✅ `tests/integration/test_staff_crud.py` |

> Uchala mezon ham texnik jihatdan bajarildi, lekin 1-bosqichning **qamrovi**
> hali to'liq emas: `integrations` va `customers` modullari yo'q (§3).

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
| `system` | health, version (setup'dan) | 2 |

> Ustundagi son — **yo'llar** soni (11+1+1+7+1+2 = 23). Operatsiyalar ko'proq:
> `settings` ning yettita yo'lida 12 ta bor, chunki beshtasi `GET`+`PATCH`
> juftligi (`products/` faqat `GET`, `cache/purge/` faqat `POST`).

**Jadvallar:** `staff`, `staff_refresh_tokens`, `audit_log`, `uploads`,
`branding`, `site`, `languages`, `currencies`, `features`, `product_settings`.

**Beat jadvali:** `heartbeat` (5 daq) · `sweep_unlinked_uploads` (soatlik).

---

## 3. Qolgan ish — 1-bosqich

### 5-bo'lak: `integrations`

> Bu **2-bosqichni to'sib turibdi**: GTS credential'lari shusiz saqlanmaydi.

- Jadvallar: `integration_configs` (`code`, `enabled`, `settings` JSONB,
  `last_tested_at`, `last_test_ok`) va `integration_credentials` (shifrlangan
  qiymat + **`key_version`** — kalit rotatsiyasi credential'larni qayta
  kiritishsiz o'tishi uchun, ARCHITECTURE.md §10).
- `app/core/crypto.py` (`encrypt`/`decrypt`/`needs_reencryption`) shu yerda
  **birinchi marta** ishlatiladi — hozircha faqat testlari bor.
- Endpointlar (API.md §29, 9 ta): `GET` — `admin`, `PATCH` va `test/` —
  **`owner`**. `gts/`, `payments/{code}` (`payme`, `click`), `notifications/`.
- Sirlar `core.crypto.mask_secret` bilan maskalanadi, hech qachon to'liq
  qaytmaydi.
- `providers/notifications/smtp.py` — hozirgi `log.py` adapterining o'rniga
  (`providers/notifications/__init__.py` dagi `get_notifier()` seam'i orqali).
- **`system/health/` haqiqiy bo'ladi:** `app/modules/system/service.py:47` da
  `gts` va `payments` hozir qattiq `NOT_CONFIGURED`.
- **`site-config`ning `payment_methods` bloki to'ladi** — hozir bo'sh ro'yxat
  (`settings/service.py::_assemble`).

### 6-bo'lak: `customers`

- Jadvallar: `customers`, `customer_refresh_tokens`, `passengers`,
  `email_otps`.
- API.md §18–19: `register/` + `confirm/` + `resend/`, `login/`, `refresh/`,
  `logout/`, `password/reset/{request,verify,confirm}/`; `GET`/`PATCH`/`DELETE
  /public/profile/`, `password/`, `CRUD passengers/`.
- `avatar/` uchun mavjud `uploads` ishlatiladi.
- `api/deps.py::current_customer` hozir **qatorni yuklamaydi** — `current_staff`
  kabi `customers.service.get_active()` ga o'tkaziladi.
- 1-relizga kirmaydi (API.md §41): telefon+SMS OTP, `devices/`,
  `social/apple/`. `social/google/` credential'lari `integrations` da.

### Bo'laklardan keyin

`tests/e2e/test_phase1_acceptance.py` — uchala mezon toza baza ustida,
`docker compose up` bilan uchidan-uchiga.

---

## 4. 2–7 bosqichlar

To'liq yo'l xaritasi — PROJECT.md §15 va ARCHITECTURE.md §15. Qisqacha:

| Bosqich | Asosiy ish | Diqqat |
|---|---|---|
| **2. GTS + aviachipta** | `providers/gts/` ACL, `catalog`, `products` (holatsiz), `orders`, `payments` + Payme/Click, **`booking` sagasi**, `jobs` | ⚠ GTS mashina akkaunti uchun **2FA o'chirilishi** kerak (D1) — bizga bog'liq emas, oldindan hal qilinsin |
| **3. Qolgan vertikallar** | `railway`, `insurance`, `esim`, `transfer` — har biri bitta adapter + registry yozuvi | Qabul mezoni: **oqim va saga kodi o'zgarmagan** |
| **4. Panel** | `cms`, `feedback`, `promo`, `leads`, `notifications`, `reports`, `customers`ning admin tomoni | 3 bilan parallel bo'lishi mumkin |
| **5. Sayt** | Public kontent yuzasi; `settings/menu/` va `content/pages/` §41 dan chiqadi | Menyu modeli shu paytga qadar aniqlanishi kerak |
| **6. Mobil ilova** | Push, `devices/`, **Sign in with Apple** (D5) | |
| **7. Yetuklik** | Eksport, ommaviy yuborish, **o'rnatish/yangilash/zaxira hujjati** | |

**Qolgan beat vazifalari** (`app/tasks/celery_app.py` dagi izohda): GTS'dan
buyurtma statuslarini sync · idempotency kalitlarini tozalash · kataloglarni
yangilash · valyuta kurslarini yangilash.

---

## 5. Yo'lda qabul qilingan qarorlar

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
| 12 | Saqlash kaliti kengaytmasi **tasdiqlangan MIME'dan** olinadi, client fayl nomidan emas; fayl route'i `media_type` ni o'sha jadvaldan beradi | Kengaytma javobning `Content-Type` ini belgilaydi — ya'ni client tanlagan kengaytma client tanlagan content type demakdir (§6.5) |
| 13 | Har bir fayl javobida `nosniff` + `Content-Security-Policy: default-src 'none'; sandbox` | SVG — XML, ichida `<script>` bo'lishi mumkin va imzo tekshiruvi buni ko'rmaydi. Sandbox uni alohida origin'ga tushiradi; `<img>` ga ta'sir qilmaydi |
| 14 | `Idempotency-Key` da in-flight belgisi **60 s**, saqlangan natija **24 soat** | Xato bergan handler kalitni bir kunga qulflab qo'ymasligi kerak — aks holda caller to'lovni umuman qila olmaydi |
| 15 | `.env.sample` dagi placeholder qiymatlar bilan **ko'tarilmaydi** (`DEBUG=true` dan tashqari) | `ENCRYPTION_KEYS` allaqachon shunday qilardi (base64 emas). Qolganlari uchun ham ataylab: `JWT_SECRET_KEY` — bu owner tokenini yasash imkoni |
| 16 | CORS ro'yxati **har so'rovda** `site.domain` dan olinadi, faqat `https` | Starlette uni bir marta, start'da o'qiydi — bu paneldan tahrirlanadigan qiymat uchun noto'g'ri umr. `http://` domenni qabul qilish PROJECT.md §13 dan paneldan chiqib ketish yo'li bo'lardi |
| 17 | `revoked_before` belgisi: sub'ekt bo'yicha "shu vaqtdan oldingi hamma narsa yaroqsiz". Xuddi **shu soniyada** berilgan token omon qoladi | Access token saqlanmaydi, demak `jti` bilan nomlab bo'lmaydi. Soniya oynasi ataylab: `iat` soniya aniqligida, aks holda odam o'z parolini o'zgartirgach darhol qilgan login'idan chiqib ketardi |
| 18 | Orphan grace `updated_at` dan hisoblanadi | Fayl ikki marta orphan bo'lishi mumkin; `created_at` almashtirilgan logoni darhol o'chirardi (§6.6) |

---

## 6. Topilgan va tuzatilgan tuzoqlar

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
   olmasdi, chunki u faqat baytlarning boshiga qaraydi. Tuzatish — §5.12–13.

6. 🔴 **`Idempotency-Key` da'vosi atomik emas edi.** `SET … NX` chaqirilardi,
   lekin **javobi tashlab yuborilardi** — qaror hamon undan oldingi `GET` da
   qolardi, ya'ni ikkita bir xil so'rov ikkalasi ham "yozuv yo'q" deb o'tib,
   **ikki marta yechardi**. Modul aynan shu holat uchun bor. Endi `NX` ning
   javobi hal qiladi.

7. 🟠 **`JWT_SECRET_KEY` ning ishlaydigan default'i bor edi** va aynan o'sha
   satr `.env.sample` da chop etilgan. O'zgaruvchini unutgan o'rnatma
   ko'tarilardi va o'sha kalit bilan imzolardi — istalgan kishi `aud: admin`,
   `role: owner` token yasay olardi. §5.15.

8. 🟠 **Prod'da CORS `[]` edi** ("settings moduli paydo bo'lguncha" izohi bilan),
   ya'ni panel o'z API'siga murojaat qila olmasdi; `debug` da esa `["*"]` +
   `allow_credentials=True` — brauzer rad etadigan juftlik. §5.16.

9. 🟠 **Parol o'zgartirilganda access token tirik qolardi.** Denylist'ga faqat
   **refresh** jti yozilardi, `change_password` esa "har bir sessiya o'ladi"
   deb yozilgan — sizib chiqqan access token yana 15 daqiqa ishlardi. §5.17.

10. 🟠 **Faylni uzish uni darhol supurilishga ochardi.** `sweep_orphans` cutoff'ni
    `created_at` bo'yicha hisoblardi, `unlink` esa uni o'zgartirmasdi — olti oy
    tirik turgan logo almashtirilsa keyingi soatlik supurish baytlarni
    o'chirardi. Va'da qilingan 24 soat aynan o'zi uchun yozilgan holatda
    ishlamasdi. §5.18.

---

## 7. Lokal muhit

- **`.env` dagi `POSTGRES_USER=postgres` lokal Postgres'ga mos emas** — mavjud
  rol `djumanov`. Buyruqlar hozircha shunday ishlatilmoqda:
  ```bash
  env POSTGRES_USER=djumanov POSTGRES_PASSWORD= uv run pytest
  ```
  Yo `.env` tuzatilsin, yo `postgres` roli yaratilsin.
- **Lokal `.env` da `DEBUG=true` bo'lishi kerak.** U hozircha `.env.sample`
  nusxasi, ya'ni `DEBUG=false` va placeholder parollar bilan — endi bu
  kombinatsiya ataylab ko'tarilmaydi (§5.15):
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

## 8. Ochiq savollar

PROJECT.md §16 dagi oltitasi. Qaysi biri qachon to'sadi:

| Savol | Qachon kerak |
|---|---|
| Qisman qaytarish siyosati | 2-bosqich oxiri, `payments` |
| Buyurtmani paneldan tahrirlash | 4-bosqich, `orders` |
| Dashboard ko'rsatkichlari | 4-bosqich, `reports` |
| Menyu va sahifalar modeli | 5-bosqich |
| Saqlash muddatlari | 7-bosqich |
| Kutilayotgan yuk | server o'lchami; D2 sababli har `offers/` GTS'ga boradi |
| **GTS 2FA (D1)** | **2-bosqichni to'sadi** |

---

## 9. Ma'lum kamchiliklar

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
11. `core/crypto.py` AAD ishlatmaydi — shifrmatnni bir ustundan boshqasiga
    ko'chirishni aniqlab bo'lmaydi. `integrations` yozilayotganda ko'rib
    chiqilsin.
