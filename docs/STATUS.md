# Holat va qolgan ish

**Oxirgi yangilanish:** 2026-08-05 · `main` = `5707216`

Bu hujjat **avtoritet emas** — kontrakt uchun [API.md](API.md), tuzilma uchun
[ARCHITECTURE.md](ARCHITECTURE.md), qamrov va bosqichlar uchun
[PROJECT.md](PROJECT.md) ustun turadi. Bu yerda faqat **hozir nima qurilgan,
nima qolgan** va yo'lda topilgan, koddan ko'rinmaydigan qarorlar yozilgan.

---

## 1. Bir qarashda

| | |
|---|---|
| Bosqich | **1 — Yadro**, 3 qabul mezonidan **2 tasi** bajarilgan |
| Endpointlar | 23 ta yo'l (API.md dagi ~150 dan) |
| Jadvallar | 10 ta + `alembic_version` |
| Migratsiyalar | 5 ta, bitta head (`c4adf0023f38`) |
| Testlar | 278 ta — unit 10 fayl · contract 6 · integration 9 |
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
| `settings` | Brending, sayt, tillar, valyutalar, features, mahsulotlar, kesh | 9 |
| `settings` (public) | `GET /public/site-config/` — kesh + ETag + i18n | 1 |
| `system` | health, version (setup'dan) | 2 |

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

---

## 7. Lokal muhit

- **`.env` dagi `POSTGRES_USER=postgres` lokal Postgres'ga mos emas** — mavjud
  rol `djumanov`. Buyruqlar hozircha shunday ishlatilmoqda:
  ```bash
  env POSTGRES_USER=djumanov POSTGRES_PASSWORD= uv run pytest
  ```
  Yo `.env` tuzatilsin, yo `postgres` roli yaratilsin.
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
