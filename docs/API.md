# REST API — kontrakt

Bu hujjat API'ning **to'liq kontrakti**: umumiy qoidalar va barcha endpointlar.

| Qism | § | Nima |
|---|---|---|
| **I. Umumiy qoidalar** | 1–16 | Envelope, xato katalogi, auth, RBAC, ko'p tillilik, idempotentlik, GTS bilan aloqa |
| **II. Public yuza** | 17–26 | Sayt va mobil ilova uchun — `/api/v1/public/` |
| **III. Admin yuza** | 27–39 | Boshqaruv paneli uchun — `/api/v1/admin/` |
| **IV. Webhook'lar** | 40 | To'lov provayderlari callback'lari — `/api/v1/webhooks/` |
| **V. Reliz qamrovi** | 41 | Birinchi relizga kirmaydigan endpointlar |

Loyiha konteksti: [PROJECT.md](PROJECT.md) · Mahsulot manbai: [GTS.md](GTS.md) ·
Backend ichki tuzilishi: [ARCHITECTURE.md](ARCHITECTURE.md).

> Bu hujjat — **dizayn manbai**. FastAPI generatsiya qiladigan OpenAPI (`/api/v1/openapi.json`)
> shu qoidalardan kelib chiqadigan artefakt, aksincha emas. Kontrakt o'zgarsa avval shu hujjat
> yangilanadi.

---
---

# I QISM — UMUMIY QOIDALAR

## 1. Asosiy qoidalar

| Qoida | Qiymat |
|---|---|
| Base path | `/api/v1/` |
| Yuzalar | `/api/v1/public/…` · `/api/v1/admin/…` · `/api/v1/webhooks/…` |
| Trailing slash | **Doim bor**: `/api/v1/admin/content/blogs/` |
| JSON kalitlari | `snake_case` |
| Identifikator | UUID (`id`). Tashqi tizim id'lari alohida maydonda |
| Vaqt | ISO-8601, UTC, `Z` bilan: `2026-08-05T09:41:00Z` |
| Pul | `{"amount": "125000.00", "currency": "UZS"}` — miqdor **string**, float emas |
| Til kodi | ISO 639-1 kichik harf: `uz`, `ru`, `en` |
| Kodlash | UTF-8, `Content-Type: application/json` |

**Versiyalash**: `v1` yo'lda. Buzuvchi o'zgarish — yangi versiya. Yangi ixtiyoriy maydon
qo'shish buzuvchi hisoblanmaydi, shuning uchun klient noma'lum maydonlarni **e'tiborsiz
qoldirishi** shart.

---

## 2. Javob formati (envelope)

Barcha javoblar — muvaffaqiyat ham, xato ham — bir xil strukturada.

**Muvaffaqiyat:**

```json
{
  "status": "success",
  "data": { "id": "9f2c…", "title": "Yangi aksiya" },
  "errors": [],
  "meta": null
}
```

**Ro'yxat (pagination bilan):**

```json
{
  "status": "success",
  "data": [ { "id": "…" }, { "id": "…" } ],
  "errors": [],
  "meta": { "page": 1, "page_size": 20, "total": 137, "total_pages": 7 }
}
```

**Xato:**

```json
{
  "status": "error",
  "data": null,
  "errors": [
    { "code": "validation", "field": "email", "message": "Noto'g'ri format" },
    { "code": "validation", "field": "phone", "message": "Majburiy maydon" }
  ],
  "meta": null
}
```

Qoidalar:

- `status` — faqat `"success"` yoki `"error"`.
- `data` — obyekt, massiv yoki `null`. Xatoda doim `null`.
- `errors` — doim massiv. Muvaffaqiyatda bo'sh.
- `meta` — pagination yoki qo'shimcha kontekst; bo'lmasa `null`.
- HTTP status kodi ham to'g'ri qo'yiladi — envelope uni almashtirmaydi, to'ldiradi.

---

## 3. Xato katalogi

| `code` | HTTP | Qachon |
|---|---|---|
| `validation` | 422 | So'rov tanasi yoki parametrlari noto'g'ri |
| `unauthorized` | 401 | Token yo'q, yaroqsiz yoki muddati tugagan |
| `forbidden` | 403 | Token bor, lekin rol yetarli emas |
| `not_found` | 404 | Resurs topilmadi |
| `conflict` | 409 | Holat ziddiyati (masalan allaqachon bekor qilingan buyurtma) |
| `rate_limited` | 429 | So'rov chegarasi oshib ketdi |
| `upstream_error` | 502 | GTS yoki to'lov provayderi xato qaytardi |
| `upstream_timeout` | 504 | Yuqori oqim javob bermadi |
| `payment_failed` | 400 | To'lov rad etildi (sabab `message` da) |
| `offer_expired` | 409 | Taklif muddati tugadi — qidiruvni qaytadan boshlash kerak |
| `internal` | 500 | Kutilmagan xato |

`field` faqat `validation` da to'ldiriladi. `message` — foydalanuvchiga ko'rsatish uchun
tayyor matn, so'rov tilida (`Accept-Language`).

**Yuqori oqim xatolari**: GTS yoki to'lov provayderining asl xabari `message` da saqlanadi va
`meta.upstream` ichida asl kod/matn beriladi — diagnostika uchun yo'qolmasligi shart.

---

## 4. Autentifikatsiya

**JWT bearer**, access + refresh.

```
Authorization: Bearer <access_token>
```

Ikki xil sub'ekt, ikki xil token — bir-biriga o'tmaydi:

| Sub'ekt | `aud` | Qayerda ishlaydi | Access TTL | Refresh TTL |
|---|---|---|---|---|
| Customer (oxirgi foydalanuvchi) | `public` | `/api/v1/public/*` | 30 daqiqa | 30 kun |
| Staff (xodim) | `admin` | `/api/v1/admin/*` | 15 daqiqa | 12 soat |

Token payload'i: `sub` (foydalanuvchi id), `aud`, `role` (faqat staff uchun), `exp`, `iat`, `jti`.
`role` faqat ikkita qiymat oladi — `owner` yoki `admin` (§5); boshqa qiymatli token qabul
qilinmaydi.

- Customer tokeni bilan `/admin/*` ga urinish → `403 forbidden`.
- Refresh **rotatsiya bilan**: har `refresh/` chaqiruvida eski refresh bekor qilinadi.
- `logout/` refresh tokenni bekor qiladi (`jti` qora ro'yxatga tushadi).

Auth talab qilmaydigan endpointlar aniq belgilanadi (`site-config`, kontent o'qish, ro'yxatdan
o'tish, login, webhook'lar).

---

## 5. Rollar va kirish

Rollar **ikkita**, ular [PROJECT.md](PROJECT.md) §9 da tasvirlangan. Resurs guruhlari bo'yicha
matritsa:

| Resurs guruhi | `owner` | `admin` |
|---|---|---|
| Sozlamalar (brending, domen, menyu) | ✎ | ✎ |
| Integratsiya kalitlari | ✎ | 👁 |
| Kontent va sharhlar | ✎ | ✎ |
| Buyurtmalar | ✎ | ✎ |
| To'lovlar va qaytarishlar | ✎ | ✎ |
| Promokodlar | ✎ | ✎ |
| Mijozlar | ✎ | ✎ |
| Hisobotlar | ✎ | ✎ |
| Jamoa | ✎ | — |
| Tizim va audit | ✎ | 👁 |

✎ o'qish + yozish · 👁 faqat o'qish · — kirish yo'q

**Ierarxiya: `owner ⊃ admin`.** `admin` hech bir joyda `owner` dan ko'proq huquqqa ega emas,
shuning uchun tekshiruv **ikki pog'onali**: endpoint yo `admin` talab qiladi (ikkala rol ham
o'tadi), yo `owner` (faqat `owner`). Ruxsat satrlari katalogi yo'q — kirish rol nomi bo'yicha
hal qilinadi.

`admin` `owner` talab qiladigan endpointga urinsa → **`403 forbidden`**.

`GET /admin/auth/me/` xodimning `role` qiymatini qaytaradi va panel menyuni **shu qiymat
bo'yicha** yig'adi (§27).

> **Alohida amallar guruhdan qattiqroq bo'lishi mumkin.** Qaytarib bo'lmaydigan yoki
> o'rnatmaning qamrovini o'zgartiradigan bir nechta endpoint guruh darajasi `✎` bo'lsa ham
> `owner` da qoladi — hozircha ikkitasi:
> `DELETE /admin/customers/{id}/` (shaxsiy ma'lumotni tozalash, §34) va
> `PATCH /admin/settings/features/` (butun bo'limni o'chirish, §28). Buning teskarisi
> bo'lmaydi: guruhda `—` yoki `👁` turgan joyda endpoint hech qachon kengroq ruxsat bermaydi.

---

## 6. Ro'yxat, filtr va tartib

| Parametr | Ma'nosi | Default |
|---|---|---|
| `page` | Sahifa raqami (1 dan) | `1` |
| `page_size` | Sahifadagi yozuvlar soni (maks. 100) | `20` |
| `search` | Matnli qidiruv (resursga xos maydonlar bo'yicha) | — |
| `ordering` | Tartib maydoni; `-` bilan teskari: `-created_at` | resursga xos |
| `created_from` / `created_to` | Sana oralig'i (ISO-8601) | — |

Resursga xos filtrlar har bir endpoint tavsifida ko'rsatiladi (masalan buyurtmalarda
`?product=`, `?status=`).

---

## 7. Ko'p tillilik

Sayt uchta tilni qo'llab-quvvatlaydi: `uz`, `ru`, `en` ([PROJECT.md](PROJECT.md) D8).

Tarjima qilinadigan maydonlar **obyekt** sifatida saqlanadi va admin API'da shunday qaytadi:

```json
{ "title": { "uz": "Chegirma", "ru": "Скидка", "en": "Discount" } }
```

Public API'da esa bitta tilga siqiladi — `?lang=` yoki `Accept-Language` bo'yicha:

```json
{ "title": "Chegirma", "lang": "uz" }
```

**Fallback zanjiri**: so'ralgan til → saytning asosiy tili → mavjud birinchi til.
Fallback ishlatilganda javobda `"lang"` haqiqatda qaytarilgan tilni ko'rsatadi — klient
buni bilishi kerak.

Sayt qaysi tillarni qo'llab-quvvatlashi `settings/languages/` da belgilanadi; ro'yxatda
bo'lmagan til so'ralsa asosiy tilga tushadi. **Barcha tillar to'ldirilishi shart emas** —
bo'sh til shu zanjir bo'yicha almashtiriladi.

**Yagona istisno — §26 kataloglari.** Ular GTS'dan kelgan shaklda qaytadi: `translations`
obyekti butunligicha qoladi va `lang` qo'shilmaydi. Sabab — bu tarjima qilingan bizning
kontentimiz emas, tashqi ma'lumotnoma; uni siqish GTS ro'yxatiga parallel ikkinchi
lug'atni yuritishni anglatardi, va GTS bizda yo'q tillarni ham beradi (`az`).

---

## 8. Standart CRUD naqshi

Ko'p resurslar bir xil naqshda ishlaydi. Har birini alohida sanab chiqmaslik uchun naqsh
shu yerda bir marta belgilanadi — endpoint jadvallarida faqat **CRUD** deb belgilanadi.

| Metod | Yo'l | Javob |
|---|---|---|
| `GET` | `/{resource}/` | `200` — ro'yxat + `meta` pagination |
| `POST` | `/{resource}/` | `201` — yaratilgan obyekt |
| `GET` | `/{resource}/{id}/` | `200` — bitta obyekt |
| `PATCH` | `/{resource}/{id}/` | `200` — yangilangan obyekt |
| `DELETE` | `/{resource}/{id}/` | `204` — tana yo'q |

Qoidalar:

- `PATCH` — qisman yangilash; faqat berilgan maydonlar o'zgaradi. `PUT` ishlatilmaydi.
- `DELETE` — soft delete (`deleted_at` qo'yiladi), agar resurs tavsifida boshqacha aytilmasa.
- Har bir obyektda `id`, `created_at`, `updated_at` bor.

---

## 9. Uzoq davom etadigan amallar

Hisobot eksporti, ommaviy yuborish kabi amallar darhol tugamaydi:

```
POST /api/v1/admin/reports/export/     →  202 Accepted
{ "status": "success", "data": { "job_id": "…", "state": "pending" } }

GET  /api/v1/admin/jobs/{job_id}/      →  200
{ "status": "success", "data": { "job_id": "…", "state": "done",
                                 "result": { "file_url": "…" } } }
```

`state`: `pending` → `running` → `done` | `failed`. `failed` bo'lsa sabab `errors` da.

---

## 10. Idempotentlik

Pul yoki tashqi tizimga ta'sir qiluvchi `POST` so'rovlarida (bron, to'lov, qaytarish)
klient `Idempotency-Key` header'ini yuboradi:

```
Idempotency-Key: 6f1a9c2e-…
```

Bir xil kalit bilan takroriy so'rov **yangi amal bajarmaydi**, birinchi natijani qaytaradi.
Kalit 24 soat saqlanadi. Kalitsiz yuborilgan bunday so'rov `422 validation` bilan rad etiladi.

---

## 11. Fayl yuklash

Fayl alohida yuklanadi, keyin uning `id` si boshqa resursga bog'lanadi:

```
POST /api/v1/admin/uploads/        (multipart/form-data: file, purpose)
→ 201 { "id": "…", "url": "https://…", "mime": "image/png", "size": 20481 }

PATCH /api/v1/admin/content/blogs/{id}/
{ "cover_image_id": "…" }
```

`purpose` — `logo`, `favicon`, `blog_cover`, `promo_banner`, `document` va h.k.; ruxsat
etilgan MIME va o'lcham shunga qarab tekshiriladi. Bog'lanmagan fayllar 24 soatdan keyin
tozalanadi.

> **Bu naqsh — admin yuzasiniki.** Public yuzada `uploads/` resursi yo'q va mijoz tokeni
> `/admin/*` ga kira olmaydi (§4). Mijoz **hech qanday fayl yuklamaydi**: avatar ham
> fayl emas, klientning o'z rasmlar to'plamidan tanlangan kod (§19).

---

## 12. GTS bilan aloqa

Mahsulot endpointlari (qidiruv, bron, chipta) GTS'ga chiqadi. Qoidalar:

| Parametr | Qiymat |
|---|---|
| Qidiruv timeout | 40 s (GTS provayderlarni parallel so'raydi) |
| Boshqa amallar timeout | 15 s |
| Retry | Faqat idempotent `GET` uchun, 2 marta, eksponensial kechikish bilan |
| Bron/to'lov | **Retry yo'q** — takrorlash `Idempotency-Key` orqali klient tomonidan |

GTS xatosi hech qachon yashirilmaydi: `502 upstream_error` bilan qaytadi, asl matn
`message` da, asl kod `meta.upstream` da.

**Kesh**: faqat statik kataloglar (shaharlar, aeroportlar, aviakompaniyalar) Redis'da keshlanadi.
**Qidiruv natijalari bizda keshlanmaydi** — GTS `request_id` bo'yicha o'z keshini yuritadi va
`offers/` unga passthrough qilinadi (§20, [ARCHITECTURE.md](ARCHITECTURE.md) §9).
Buyurtma va to'lov ma'lumoti ham keshlanmaydi.

GTS javoblarini o'girish va status xaritasi — [ARCHITECTURE.md](ARCHITECTURE.md) §7.

---

## 13. Kuzatuv va audit

- Har so'rovda `X-Request-Id` header'i — klient yuborsa saqlanadi, bo'lmasa generatsiya
  qilinadi va javobda qaytariladi. Barcha loglar shu id bilan bog'lanadi.
- **Har bir mutatsiya** (`POST`/`PATCH`/`DELETE`) admin yuzasida audit log'ga tushadi:
  kim, qachon, qaysi resurs, qanday o'zgarish. `GET /api/v1/admin/system/audit/`.
- Autentifikatsiya hodisalari (kirish, muvaffaqiyatsiz urinish) alohida belgilanadi.

---

## 14. Chegaralar (rate limit)

| Endpoint turi | Chegara |
|---|---|
| Auth (`login`, `password/reset`) | 5 / daqiqa / IP |
| Qidiruv | 30 / daqiqa / foydalanuvchi |
| **To'lov va karta** (tranzaksiya boshlash, karta qo'shish va tasdiqlash) | **10 / daqiqa / foydalanuvchi** |
| Boshqa public | 120 / daqiqa / foydalanuvchi |
| Admin | 300 / daqiqa / xodim |

Oshib ketganda `429 rate_limited` va `Retry-After` header'i.
Tizimga kirmagan foydalanuvchi uchun chegara **IP bo'yicha** hisoblanadi.

> To'lov chegarasi qolganlaridan qattiqroq, chunki bu yerdagi har bir so'rov o'rnatmaning
> **merchant credential'i bilan** provayderga chiqadi: chegara foydalanuvchini emas,
> o'rnatmaning provayderdagi hisobini himoya qiladi. Kodni qayta yuborish (`resend-otp/`)
> esa auth chegarasida — u email OTP qayta yuborish bilan bir xil shakldagi suiiste'mol.

---

## 15. CORS va xavfsizlik

- Public API — sayt domeni va Flutter app uchun ochiq; ruxsat etilgan domenlar sozlamada.
- Admin API — faqat panel domeni.
- Barcha muhitlarda HTTPS majburiy.
- Parollar — `argon2`; integratsiya kalitlari DB'da shifrlangan (kalit env'da).
- Karta ma'lumoti **hech qachon saqlanmaydi va log'ga tushmaydi** — to'lov provayderi tokeni
  bilan ishlanadi.

---

## 16. Nomlash qoidalari

- Resurs nomi — **ko'plikda va tire bilan**: `popular-directions/`, `payment-methods/`.
- Amal (CRUD emas) — resursning quyi yo'li sifatida fe'l: `orders/{id}/cancel/`,
  `feedbacks/{id}/accept/`.
- Filtr uchun alohida endpoint yaratilmaydi — query parametr ishlatiladi
  (`orders/?status=paid`, `orders/paid/` emas).
- Bo'lim prefikslari: `content/`, `settings/`, `integrations/`, `payments/`, `system/`.

---
---

# II QISM — PUBLIC YUZA

Prefiks: **`/api/v1/public/`**. Foydalanuvchi: sayt (web build) va Flutter ilova.
Sub'ekt — **customer**, token `aud: public`.

Jadvallardagi **Auth** ustuni: `—` auth talab qilinmaydi · `✓` access token majburiy ·
`(✓)` ixtiyoriy (tizimga kirgan bo'lsa qo'shimcha ma'lumot qaytadi).

---

## 17. Sayt konfiguratsiyasi

Sayt va ilova ishga tushganda **birinchi** shu endpointni chaqiradi. Brending, yoqilgan
mahsulotlar, tillar va menyu shundan keladi — shuning uchun rang yoki logo o'zgarishi
qayta build talab qilmaydi ([PROJECT.md](PROJECT.md) §7).

| Metod | Yo'l | Auth | Izoh |
|---|---|---|---|
| `GET` | `/public/site-config/` | — | To'liq konfiguratsiya |

```json
{
  "status": "success",
  "data": {
    "site": { "name": "Brand Travel", "domain": "brand.uz", "support_phone": "+998…" },
    "branding": {
      "logo_url": "https://…", "favicon_url": "https://…",
      "colors": { "primary": "#0A5CFF", "accent": "#FF7A00", "background": "#FFFFFF" },
      "font_family": "Inter"
    },
    "languages": { "default": "uz", "available": ["uz", "ru", "en"] },
    "currencies": { "default": "UZS", "available": ["UZS", "USD"] },
    "products": [
      { "code": "flight",    "enabled": true },
      { "code": "railway",   "enabled": true },
      { "code": "insurance", "enabled": true },
      { "code": "esim",      "enabled": true },
      { "code": "transfer",  "enabled": true }
    ],
    "payment_methods": [
      { "code": "payme", "title": "Payme", "logo_url": "…", "supports_saved_cards": true },
      { "code": "click", "title": "Click", "logo_url": "…", "supports_saved_cards": false }
    ],
    "menu": [ { "title": "Aksiyalar", "url": "/promotions", "children": [] } ],
    "features": { "blog": true, "loyalty": false }
  }
}
```

Javob keshlanadi (`Cache-Control` + `ETag`); paneldan o'zgarish kiritilganda kesh tozalanadi.

`payment_methods` — `products` dan farqli o'laroq **`enabled` maydoni yo'q**, va bu ataylab:
bu yerda faqat **yoqilgan** provayderlar bo'ladi. Sayt bu ro'yxatni tugmalarga aylantiradi,
o'chirilgan provayder esa bosilmaydigan tugma bo'lardi. Tartib ham shu massivning o'zida —
paneldagi `sort_order` bo'yicha, teng bo'lsa `code` bo'yicha (§29).

`supports_saved_cards` — shu provayderda karta saqlash mumkinmi. **Sozlama emas, adapter
imkoniyati**: provayderning karta-token API'si bor-yo'qligini kod biladi, panel emas.
Sayt shundan "kartani saqlab qo'yish" tugmasini ko'rsatish-ko'rsatmaslikni hal qiladi;
`false` bo'lgan provayderga karta so'rovi `404` qaytaradi (§19).

`features` — sayt va ilova qaysi bo'limlarni ko'rsatishini shundan biladi: bayroq `false`
bo'lsa menyu elementi ham, sahifa ham chizilmaydi. Bu **yagona himoya emas** — backend ham
o'sha bayroqni majburlaydi va o'chirilgan bo'lim `404 not_found` qaytaradi (§28). Ikki
tomonlama, chunki eski build yoki to'g'ridan-to'g'ri chaqiruv bayroqni hisobga olmasligi
mumkin.

---

## 18. Autentifikatsiya

| Metod | Yo'l | Auth | Izoh |
|---|---|---|---|
| `POST` | `/public/auth/register/` | — | Ro'yxatdan o'tish — tasdiqlash so'rovi yuboriladi |
| `POST` | `/public/auth/register/confirm/` | — | OTP kod bilan tasdiqlash → tokenlar |
| `POST` | `/public/auth/register/resend/` | — | Kodni qayta yuborish |
| `POST` | `/public/auth/login/` | — | Kirish → access + refresh |
| `POST` | `/public/auth/refresh/` | — | Token yangilash (rotatsiya bilan) |
| `POST` | `/public/auth/logout/` | ✓ | Refresh tokenni bekor qilish |
| `POST` | `/public/auth/password/reset/request/` | — | Tiklash so'rovi |
| `POST` | `/public/auth/password/reset/verify/` | — | OTP tekshirish |
| `POST` | `/public/auth/password/reset/confirm/` | — | Yangi parol o'rnatish |
| `POST` | `/public/auth/social/{provider}/` | — | Social orqali kirish |
| `POST` | `/public/auth/devices/` | ✓ | Push uchun qurilmani ro'yxatga olish |
| `DELETE` | `/public/auth/devices/{id}/` | ✓ | Qurilmani o'chirish |

### Ro'yxatdan o'tish

```json
POST /public/auth/register/
{ "email": "user@mail.uz", "password": "…",
  "first_name": "Aziz", "last_name": "Karimov", "phone": "+998901234567" }

→ 204
```

Majburiy maydonlar — faqat `email` va `password`; `first_name`, `last_name` va
`phone` ixtiyoriy. Akkaunt — bu manzil va uni tasdiqlagan narsa; ism kim
ekanligining tafsiloti va uni profil ekrani so'raydi (§19). Parol uzunligi —
kamida **8** belgi, aks holda `422 validation`.

`middle_name` bu yerda **yo'q** — u faqat profilda to'ldiriladi (§19). Ro'yxatdan
o'tish yangi shaxsiy maydon qabul qilmaydi: qancha kam so'ralsa, shuncha ko'p odam
oxirigacha yetadi, qolgani esa profil ekranining ishi.

Javob **har doim `204`**, hatto manzil allaqachon band bo'lsa ham. Aks holda bu
endpoint kimning bizda akkaunti borligini tekshiradigan vositaga aylanardi. Band
manzil o'rniga **o'sha akkauntga** "sizda allaqachon akkaunt bor, parolni tiklang"
degan xabar ketadi — ya'ni ma'lumot manzil egasiga boradi, so'rov yuborganga emas.

Akkaunt qatori **darhol**, tasdiqlanmagan holda yaratiladi. Tasdiqlanmagan
akkaunt **hech narsa bermaydi**: `login/` unga `403` qaytaradi va tokeni bo'lmaydi.
Shu manzil bilan qayta ro'yxatdan o'tilsa kutayotgan kod almashtiriladi va qaytadan
yuboriladi — ya'ni noto'g'ri terilgan manzil abadiy band bo'lib qolmaydi.

**SMTP yiqilsa javob o'zgarmaydi** — baribir `204`. Xat ketmagani `502` bo'lib
qaytsa, bu endpoint yana o'sha "bu manzilda akkaunt bormi?" savoliga javob
beradigan vositaga aylanardi: mavjud manzil bir xil, yo'q manzil boshqa status
olardi. Buning o'rniga xat ketmagani **kodni yozib qo'ymaydi** — ya'ni qayta
yuborish taymeri boshlanmaydi va keyingi urinish darhol qayta harakat qiladi,
oldingi kod (agar bo'lsa) esa amalda qoladi. Sabab `error` darajasida logga
yoziladi (`mail_send_failed`) va `notifications/test/` (§29) orqali ko'rinadi —
buni bilishi kerak odam owner, so'rov yuborgan mijoz emas.

Xuddi shu qoida `register/resend/` va `password/reset/request/` uchun ham.

```json
POST /public/auth/register/confirm/
{ "email": "user@mail.uz", "code": "4829" }

→ { "status": "success",
    "data": { "access_token": "…", "refresh_token": "…", "expires_in": 1800 } }
```

```json
POST /public/auth/register/resend/
{ "email": "user@mail.uz" }

→ 204
```

### Kirish va sessiya

```json
POST /public/auth/login/
{ "login": "user@mail.uz", "password": "…" }

→ { "status": "success",
    "data": { "access_token": "…", "refresh_token": "…", "expires_in": 1800 } }
```

`refresh/` va `logout/` — `{ "refresh_token": "…" }`. Birinchisi o'sha juftlikni
qaytaradi va **eskisini bekor qiladi**, ikkinchisi `204`.

Bekor qilingan refresh token qayta kelsa — bu o'g'irlik signali va **o'sha
foydalanuvchining barcha sessiyalari** o'chadi (§4).

### Social orqali kirish

```json
POST /public/auth/social/google/
{ "id_token": "eyJhbGciOiJSUzI1NiIs…" }

→ { "status": "success",
    "data": { "access_token": "…", "refresh_token": "…", "expires_in": 1800 } }
```

`id_token` — provayder brauzerda bergan token; server uni provayderning o'z kaliti bilan
tekshiradi.

Manzil bo'yicha akkaunt topilsa **o'shanga kiriladi**, topilmasa yaratiladi. Yangi akkaunt
**darhol tasdiqlangan** bo'ladi: manzilni provayder allaqachon tasdiqlagan, ya'ni email OTP
isbotlaydigan narsa bu yerda boshqa yo'l bilan isbotlangan. Shu manzilda tasdiqlanmagan
qator turgan bo'lsa u **tasdiqlanadi**, ikkinchi akkaunt yaratilmaydi — aks holda bitta
odamning bitta manzilida ikkita akkaunti bo'lardi.

Provayder o'chirilgan, sozlanmagan yoki noma'lum bo'lsa — `404` (§29).

### Parolni tiklash

Uch qadam: kod so'raladi, kod tekshiriladi, yangi parol o'rnatiladi.

```json
POST /public/auth/password/reset/request/
{ "email": "user@mail.uz" }

→ 204

POST /public/auth/password/reset/verify/
{ "email": "user@mail.uz", "code": "4829" }

→ { "status": "success",
    "data": { "reset_token": "…", "expires_in": 900 } }

POST /public/auth/password/reset/confirm/
{ "reset_token": "…", "new_password": "…" }

→ 204
```

`request/` **har doim `204`** — noma'lum yoki bloklangan manzil ham xuddi shunday
javob oladi (§18 dagi ro'yxatdan o'tish bilan bir sabab). `verify/` kodni
**ishlatib yuboradi** va o'rniga bir martalik `reset_token` beradi; parol aynan shu
token bilan almashtiriladi. Parol o'zgargach **barcha sessiyalar** o'chadi.

### OTP kodlari

Ro'yxatdan o'tish va parol tiklash bitta mexanizmni ishlatadi:

- kod — **to'rt raqam**, amal muddati **10 daqiqa**;
- **beshta** noto'g'ri urinishdan keyin kod kuyadi va yangisini so'rash kerak.
  To'rt raqamda bu shift oldingidan ham muhimroq: maydon o'n ming, ya'ni
  shiftsiz kodni terib chiqish soniyalar ishi bo'lardi. Beshta urinishdan keyin
  kod kuyadi va keyingisi boshqa son;
- yangi kod berilganda oldingisi **darhol kuyadi** — bitta manzilda bir vaqtda
  ikkita ishlaydigan kod bo'lmaydi;
- qayta yuborish orasida **60 soniya**. Bu chegara **jim** ishlaydi: javob
  baribir `204`, shunchaki xat ketmaydi. Ko'rinadigan bo'lsa `register/resend/`
  "bu manzilda kutayotgan kod bormi?" degan savolga javob beradigan vositaga
  aylanardi va yuqoridagi `204` qoidasidan ma'no qolmasdi.

Ko'rinadigan chegara — §14 niki: bu bo'limning **barcha** yo'llari auth guruhida,
ya'ni **5/daqiqa/IP**. U manzilni emas, chaqiruvchini sanaydi, shuning uchun hech
narsani oshkor qilmaydi.

### Xatolar

§3 katalogi yopiq — bu bo'limning har bir nosozligi o'sha o'n bitta koddan biriga
tushadi:

| Holat | `code` | HTTP |
|---|---|---|
| Noto'g'ri email yoki parol | `unauthorized` | 401 |
| Tasdiqlanmagan akkaunt — parol to'g'ri bo'lgach | `forbidden` | 403 |
| Bloklangan akkaunt — parol to'g'ri bo'lgach | `forbidden` | 403 |
| Noto'g'ri, muddati o'tgan yoki ishlatilgan kod | `validation` (`field: "code"`) | 422 |
| Yaroqsiz yoki ishlatilgan `reset_token` | `validation` (`field: "reset_token"`) | 422 |
| Noma'lum, muddati o'tgan yoki bekor qilingan refresh token | `unauthorized` | 401 |
| Yaroqsiz `id_token` (social) | `unauthorized` | 401 |
| Sozlanmagan, o'chirilgan yoki noma'lum social provayder | `not_found` | 404 |
| So'rov chegarasi oshib ketdi (§14 — 5/daq/IP) | `rate_limited` | 429 |

**Noto'g'ri parol va yo'q akkaunt bir xil javob beradi** — matni ham, kodi ham.
Ikkalasini farqlash mumkin bo'lsa, bu endpoint manzillarni tekshirish vositasi
bo'lardi. Shu sababdan akkaunt topilmaganda ham parol xeshi baribir tekshiriladi:
javob vaqti ham farq qilmasligi kerak.

Tasdiqlanmagan va bloklangan holat esa **parol tekshirilgandan keyin** aytiladi —
to'g'ri parolni bilgan kishiga o'z akkaunti nima uchun ishlamayotganini aytish
hech narsani oshkor qilmaydi.

> **Birinchi reliz chegarasi.** `login` maydoni **email** qabul qiladi; telefon + SMS OTP
> keyingi bosqichda ([PROJECT.md](PROJECT.md) D6), shuning uchun OTP va parol tiklash kodlari
> **email orqali** yuboriladi. `{provider}` — hozircha faqat **`google`**; `apple` mobil ilova
> bosqichida qo'shiladi (D5). Qurilma ro'yxati (`devices/`) push bilan birga keladi (§41).


---

## 19. Profil

| Metod | Yo'l | Auth | Izoh |
|---|---|---|---|
| `GET` `PATCH` | `/public/profile/` | ✓ | Shaxsiy ma'lumot va avatar kodi |
| `POST` | `/public/profile/password/` | ✓ | Parolni o'zgartirish |
| `DELETE` | `/public/profile/` | ✓ | Akkauntni o'chirish |
| CRUD | `/public/profile/passengers/` | ✓ | Saqlangan yo'lovchilar va hujjatlari |
| `GET` `POST` | `/public/profile/cards/` | ✓ | Saqlangan to'lov kartalari · yangi karta qo'shish |
| `GET` `PATCH` `DELETE` | `/public/profile/cards/{id}/` | ✓ | Bitta karta · nomini o'zgartirish · o'chirish |
| `POST` | `/public/profile/cards/{id}/verify/` | ✓ | Kartani SMS kodi bilan tasdiqlash |
| `POST` | `/public/profile/cards/{id}/resend-otp/` | ✓ | Kodni qayta yuborish |
| `POST` | `/public/profile/cards/{id}/default/` | ✓ | Asosiy karta qilib belgilash |
| `GET` | `/public/profile/notifications/` | ✓ | Bildirishnomalar |
| `POST` | `/public/profile/notifications/read-all/` | ✓ | Hammasini o'qilgan deb belgilash |
| `POST` | `/public/profile/notifications/{id}/read/` | ✓ | Bittasini o'qilgan deb belgilash |
| `DELETE` | `/public/profile/notifications/{id}/` | ✓ | O'chirish |

### Shaxsiy ma'lumot

```json
GET /public/profile/
→ { "status": "success",
    "data": { "id": "9f2c…", "email": "user@mail.uz",
              "first_name": "Aziz", "last_name": "Karimov",
              "middle_name": "Baxtiyorovich",
              "phone": "+998901234567", "birth_date": "1995-04-17",
              "avatar_id": "avatar-07",
              "created_at": "…", "is_profile_complete": true } }
```

```json
PATCH /public/profile/
{ "first_name": "Aziz", "last_name": "Karimov", "middle_name": "Baxtiyorovich",
  "phone": "+998901234567", "birth_date": "1995-04-17", "avatar_id": "avatar-07" }
```

Ro'yxatdan o'tishda ism so'ralmagani uchun (§18) `first_name` ham `null` bo'lishi
mumkin — mijoz uni shu yerda to'ldiradi.

`is_profile_complete` — **faqat o'qish uchun**, ustun emas: `first_name`, `last_name`,
`middle_name`, `phone` va `birth_date` ning **beshalasi ham** to'ldirilgan bo'lsa `true`.
Faqat bo'sh joydan iborat qiymat to'ldirilgan hisoblanmaydi. Shart serverda turadi,
chunki har bir klient uni o'zicha hisoblasa, "profilni to'ldiring" ekrani ilovada
saytdagidan boshqacha chiqadi — va bu farqni hech kim xato deb bildirmaydi. `PATCH` da
yuborilsa u noma'lum maydon, ya'ni `422 validation`.

`PATCH` **faqat shu oltita maydonni** oladi. Boshqa nom yuborilsa `422 validation` —
jimgina e'tiborsiz qoldirilmaydi, aks holda panel o'zgarmagan qiymatni o'zgargandek
ko'rsatardi.

**`email` bu yerda o'zgarmaydi.** U — kirish identifikatori: OTP aynan shu manzilni
tasdiqlagan va parol tiklash aynan shunga ishonadi. Manzilni almashtirish yangisini
tasdiqlaydigan alohida oqimni talab qiladi; u oqim belgilangunga qadar `email` yuborilsa
`422 validation` qaytadi.

### Avatar

**Avatar — fayl emas.** Rasmlar to'plami klientning o'zida (ilova va sayt o'z
rasmlarini o'zi yuklab beradi), foydalanuvchi shundan bittasini tanlaydi va server
faqat tanlangan variant **kodini** saqlaydi. Shuning uchun yuklash endpointi ham,
`avatar_url` ham yo'q — `avatar_id` boshqa maydonlar qatori `PATCH /public/profile/`
orqali yoziladi va `null` yuborilsa tozalanadi.

```json
PATCH /public/profile/
{ "avatar_id": "avatar-07" }

PATCH /public/profile/
{ "avatar_id": null }
```

Kod — server uchun **shaffof matn**, uzunligi 64 belgigacha. Ruxsat etilgan qiymatlar
ro'yxati serverda **yo'q** va tekshirilmaydi: to'plamni klient chiqaradi, demak uni
klient biladi. Ro'yxat serverda turganda har bir yangi rasm backend deployini talab
qilardi, ustiga rasmlar to'plami ilovada va saytda bir xil bo'lishi shart ham emas.
Server bilmagan kod kelsa u shundayligicha saqlanadi va shundayligicha qaytariladi;
bunday kodni ko'rsata olmaslik — uni yuborgan klientning ishi.

### Parolni o'zgartirish va akkauntni o'chirish

```json
POST /public/profile/password/
{ "current_password": "…", "new_password": "…" }
→ 204

DELETE /public/profile/
{ "password": "…" }
→ 204
```

Parol o'zgargach **barcha sessiyalar** o'chadi, jumladan so'rov yuborgani ham.

`DELETE` parol so'raydi, garchi chaqiruvchi allaqachon tokenga ega bo'lsa ham: bu mijoz
tokeni bajara oladigan **yagona qaytarib bo'lmaydigan** amal, va o'g'irlangan access token
uni bajarishga yetarli bo'lmasligi kerak.

> ⚠ Demak `DELETE` **tanaga ega**. Ba'zi HTTP klientlari `delete()` qisqartmasida tana
> yubora olmaydi (masalan `httpx`) — u holda umumiy `request("DELETE", …)` shakli
> ishlatiladi.

**Akkaunt o'chirilganda** shaxsiy ma'lumot tozalanadi — ism va ota ismi, telefon, tug'ilgan
sana va manzil qatordan olib tashlanadi, saqlangan yo'lovchilar ham. Saqlangan kartalar
**provayder tomonda ham** o'chiriladi va token qatordan olib tashlanadi. Buyurtma va to'lov
yozuvlari esa moliyaviy hujjat sifatida anonimlashtirilib saqlanadi
([PROJECT.md](PROJECT.md) §13) — tranzaksiyadagi maskalangan karta nusxasi ular bilan birga
qoladi, chunki kvitansiya karta unutilgandan keyin ham o'qilishi kerak. Manzil bo'shaydi,
ya'ni o'sha email bilan qaytadan ro'yxatdan o'tish mumkin.

### Saqlangan yo'lovchilar

Bron qilishda qayta terishni oldini oladi. Bron so'rovida `"save_passenger": true`
berilsa yangi yozuv qo'shiladi. Resurs §8 dagi standart CRUD naqshida ishlaydi.

```json
GET /public/profile/passengers/
→ { "status": "success",
    "data": [
      { "id": "3c1d…", "first_name": "Aziz", "last_name": "Karimov",
        "middle_name": "Baxtiyorovich", "birth_date": "1995-04-17",
        "citizenship": "Uzbekistan",
        "document_type": "passport", "document_number": "AA1234567",
        "document_expiry_date": "2030-01-01",
        "created_at": "…", "updated_at": "…" }
    ],
    "errors": [], "meta": { "page": 1, "page_size": 20, "total": 1, "total_pages": 1 } }
```

- Maydonlar to'plami [PROJECT.md](PROJECT.md) §13 dagi saqlanadigan shaxsiy ma'lumot
  ro'yxatidan olingan va undan oshmaydi. Jins **yo'q** — u o'sha ro'yxatda yo'q, ya'ni
  qo'shilishi kerak bo'lsa avval `PROJECT.md` §13 tahrirlanadi.
- `birth_date` — **majburiy**. Saqlangan yo'lovchi qayta terishni oldini olish uchun bor,
  tug'ilgan sanasiz yozuv esa bron uchun baribir yaramaydi: u yarim to'ldirilgan shakl
  bo'lib qolardi va buni saqlagan paytda emas, bron paytida bilib qolinardi.
- `middle_name`, `citizenship` va `document_expiry_date` — **ixtiyoriy**. Ota ismi hujjatda
  har doim ham bo'lmaydi (chet el pasporti), amal muddati esa hujjatning har bir turida
  bo'lmaydi.
- `citizenship` — `document_type` kabi **cheklanmagan satr**, enum ham, ISO kodi ham emas.
  Davlatlar ro'yxati GTS tomonda ([GTS.md](GTS.md) §9) va klientga `/public/catalog/countries/`
  (§26) orqali beriladi, lekin ustunning o'zi cheklanmaydi: kod tanlash qaysi standart
  ekanini ("UZ" yoki "UZB") kontrakt aytmaguncha erta bo'lardi, ro'yxatni esa GTS
  o'zgartirsa saqlangan yo'lovchi yaroqsiz bo'lib qolardi.
- **Bitta yo'lovchida bitta hujjat.** Ichma-ich ro'yxat emas: saqlangan yo'lovchi bron
  uchun bitta hujjat bilan ishlatiladi.
- `document_type` — **cheklanmagan satr**. Hujjat turlari katalogi GTS tomonda
  ([GTS.md](GTS.md) §9) va klientga `/public/catalog/document-types/` (§26) orqali
  beriladi — forma shundan to'ldiriladi. Ustun baribir cheklanmaydi: lokal enum GTS
  ro'yxati o'zgarganda unga zid bo'lib chiqardi, va buni saqlash paytida emas, bron
  paytida bilib qolinardi.
- Takrorlanish taqiqlanmaydi — bitta odam eski va yangi pasporti bilan ikki marta
  saqlanishi mumkin.
- `search` — ism va familiya bo'yicha; `ordering` default `-created_at`.

### Saqlangan kartalar

Mijoz bir nechta kartani saqlab qo'yadi va to'lovda bittasini tanlaydi (§22, `flow: card_token`).
Karta **provayder tokeni** sifatida saqlanadi — raqamning o'zi hech qachon yozilmaydi
([PROJECT.md](PROJECT.md) §13, D7).

```json
GET /public/profile/cards/
→ { "status": "success",
    "data": [
      { "id": "7c1a…", "provider": "payme", "status": "active",
        "masked_pan": "860049******6604", "last4": "6604",
        "brand": "uzcard", "expiry_month": 3, "expiry_year": 2029,
        "label": "Asosiy karta", "is_default": true,
        "otp_sent_to": null, "otp_expires_at": null,
        "last_used_at": "…", "created_at": "…", "updated_at": "…" }
    ],
    "errors": [], "meta": { "page": 1, "page_size": 20, "total": 1, "total_pages": 1 } }
```

**Qo'shish ikki qadam.** Avval karta ro'yxatdan o'tkaziladi, so'ng provayder yuborgan SMS
kodi bilan tasdiqlanadi:

```json
POST /public/profile/cards/
{ "provider": "payme", "number": "8600…", "expire": "0329", "label": "Asosiy karta" }
Idempotency-Key: …

→ 201 { "data": { "id": "7c1a…", "status": "pending_verification",
                  "masked_pan": "860049******6604", "is_default": false,
                  "otp_sent_to": "+9989**1234", "otp_expires_at": "…", … } }

POST /public/profile/cards/{id}/verify/   { "code": "123456" }
→ { "data": { "id": "7c1a…", "status": "active", "otp_sent_to": null, … } }
```

- `number` va `expire` — **faqat yozish uchun**. Ular hech qachon javobda qaytmaydi va
  hech qanday `GET` da ko'rinmaydi. `expire` — `MMYY` (ikkala provayder ham shu shaklni
  so'raydi). CVV **so'ralmaydi**.
- `status`: `pending_verification` → `active`. Tasdiqlanmagan karta bilan to'lab
  bo'lmaydi (§22 da `409`).
- `otp_sent_to` va `otp_expires_at` **faqat** `pending_verification` da to'ldiriladi;
  `active` kartada ikkalasi ham `null`.
- **Kodni biz tekshirmaymiz** — uni provayder yuboradi va provayder tekshiradi. Bizdagi
  hisoblagich faqat klient provayder endpointini o'rnatmaning merchant credential'i bilan
  urib turmasligi uchun: **3 ta noto'g'ri urinish** dan keyin karta o'chiriladi va qaytadan
  qo'shish kerak. Kod **5 daqiqa** yashaydi, qayta yuborish orasida **60 soniya**.
- Noto'g'ri kod, muddati o'tgan kod va urinishlar tugagani — **bir xil xabar**. Ularni
  ajratib ko'rsatish taxmin qilayotgan odamga qayerga yetganini aytib qo'yardi.
- Tasdiqlanmay 30 daqiqa turgan karta o'chadi — bizdan ham, provayder tomondan ham.
- **Birinchi tasdiqlangan karta avtomatik `is_default`** bo'ladi. Keyingilari uchun
  `default/` chaqiriladi; asosiy karta **aynan bitta** bo'ladi, va buni baza kafolatlaydi
  ([ARCHITECTURE.md](ARCHITECTURE.md) §10). `PATCH {"is_default": true}` emas, alohida
  endpoint — bu maydonga qiymat berish emas, o'tish (§16).
- `PATCH` faqat `label` ni oladi. Boshqa hamma narsa provayderniki.
- `DELETE` — §8 dagi soft delete, **ustiga** provayder tomonda ham o'chiradi. Provayder
  javob bermasa ham mijozning ro'yxatidan yo'qoladi; qolgani fon vazifasining ishi.
- Bir xil karta ikki marta qo'shilmaydi (`422`). Ikki **turli provayderda** esa ikkita
  qator — tokenlar merchant'ga bog'langan va almashtirib bo'lmaydi.
- `search` — `label` va `last4` bo'yicha; `ordering` default `-created_at`.

### Xatolar

| Holat | `code` | HTTP |
|---|---|---|
| Token yo'q yoki muddati tugagan | `unauthorized` | 401 |
| Tasdiqlanmagan yoki bloklangan akkaunt | `forbidden` | 403 |
| `PATCH` da noma'lum maydon (jumladan `email` va `is_profile_complete`) | `validation` | 422 |
| Yo'lovchida `birth_date` yo'q | `validation` (`field: "birth_date"`) | 422 |
| Yo'lovchi `PATCH` ida majburiy maydonga `null` (`first_name`, `last_name`, `birth_date`) | `validation` (`field`: o'sha maydon) | 422 |
| Noto'g'ri joriy parol | `validation` (`field: "current_password"`) | 422 |
| `DELETE /public/profile/` da noto'g'ri parol | `validation` (`field: "password"`) | 422 |
| Fayl turi yoki hajmi mos emas | `validation` (`field: "file"`) | 422 |
| Karta raqami yaroqsiz (uzunlik yoki Luhn) | `validation` (`field: "number"`) | 422 |
| Amal muddati yaroqsiz yoki o'tgan | `validation` (`field: "expire"`) | 422 |
| Kod noto'g'ri, muddati o'tgan yoki urinishlar tugagan | `validation` (`field: "code"`) | 422 |
| Shu karta allaqachon saqlangan | `validation` (`field: "number"`) | 422 |
| Allaqachon tasdiqlangan kartani `verify/` qilish | `conflict` | 409 |
| Kodni juda tez qayta so'rash | `rate_limited` | 429 |
| Provayderda karta API'si yo'q yoki u yoqilmagan | `not_found` | 404 |
| Provayder javob bermadi | `upstream_error` · `upstream_timeout` | 502 · 504 |
| Boshqa mijozning yo'lovchisi yoki kartasi | `not_found` | 404 |

Oxirgi qator ataylab `404`, `403` emas: yozuv faqat o'z egasi orqali ko'rinadi, ya'ni
"bunday yozuv yo'q" va "bu sizniki emas" — tashqaridan bir xil javob bo'lishi kerak.

Provayderda karta API'si yo'qligi ham `404`: bu yo'l o'sha provayder uchun **mavjud emas**,
va sabab §41 dagi "relizga kirmagan" bilan bir xil shaklda ifodalanadi.

---

## 20. Mahsulotlar

Barcha vertikallar **bir xil naqshda** ishlaydi. `{product}` o'rniga: `flight`, `railway`,
`insurance`, `esim`, `transfer` — **beshtasi ham birinchi relizda**
([PROJECT.md](PROJECT.md) §8).

```
search  →  offers  →  verify  →  booking  →  payment  →  order
```

| Metod | Yo'l | Auth | Izoh |
|---|---|---|---|
| `POST` | `/public/{product}/search/` | (✓) | Qidiruvni boshlaydi → `request_id` |
| `POST` | `/public/{product}/offers/` | (✓) | Takliflar sahifasi (`request_id` + `next_token`) |
| `POST` | `/public/{product}/verify/` | (✓) | Tanlangan taklifni tasdiqlash (narx/mavjudlik) |
| `POST` | `/public/{product}/upsell/` | (✓) | Qo'shimcha xizmatlar (mavjud vertikallarda) |
| `POST` | `/public/{product}/booking/` | ✓ | Bron → buyurtma va `payment_id` |

**Vertikalga xos qo'shimcha qadamlar:**

| Vertikal | Qo'shimcha |
|---|---|
| `flight` | `/seat-map/`, `/additional-services/` |
| `railway` | `/trains/` (poyezdlar), `/train-details/` (vagon va o'rinlar) — `offers/` o'rniga |
| `insurance` | `/calculate/`, `/upsell/` |
| `esim` | `/offer/` (taklif tafsiloti) — `verify/` o'rniga |
| `transfer` | `/offer/`, `/recommended-time/` |

**Qidiruv → takliflar oqimi asinxron.** `search/` darhol `request_id` qaytaradi; provayderlar
fon rejimida javob beradi. `offers/` sahifalab so'raladi va **qisman natija** qaytishi mumkin:

```json
POST /public/flight/search/
{ "directions": [ { "from": "TAS", "to": "IST", "date": "2026-09-14" } ],
  "passengers": { "adt": 1, "chd": 0, "inf": 0 },
  "cabin": "E", "direct": false }

→ { "status": "success", "data": { "request_id": "…", "search_state": "in_progress" } }
```

```json
POST /public/flight/offers/
{ "request_id": "…", "next_token": null, "limit": 20, "sort": "price", "currency": "UZS" }

→ { "status": "success",
    "data": { "search_state": "in_progress", "next_token": "…", "offers": [ … ] } }
```

`search_state`: `in_progress` → `completed` | `failed`. Klient `completed` bo'lguncha yoki
`next_token` tugaguncha so'rashda davom etadi.

> **Qidiruv holatsiz.** `request_id` — **GTS'niki**; takliflar bizda saqlanmaydi va
> keshlanmaydi. `sort`, `limit`, `next_token` va `currency` GTS parametrlariga o'giriladi
> ([PROJECT.md](PROJECT.md) D2). Amaliy oqibati: **saralash va filtr GTS qo'llaydigan
> variantlar bilan chegaralangan** — frontend undan tashqariga chiqmasligi kerak.
> Qo'llanadigan aniq ro'yxat GTS kontrakti bo'yicha aniqlanadi
> ([ARCHITECTURE.md](ARCHITECTURE.md) §14, A9).

**Muddat tugashi**: taklif va `request_id` ning amal muddati cheklangan. Muddati o'tgan
taklif bilan `verify/` yoki `booking/` chaqirilsa → `409 offer_expired`, klient qidiruvni
qaytadan boshlaydi.

> `booking/` **auth talab qiladi** — mehmon sifatida xarid yo'q ([PROJECT.md](PROJECT.md) D4).

---

## 21. Buyurtmalar

| Metod | Yo'l | Auth | Izoh |
|---|---|---|---|
| `GET` | `/public/orders/` | ✓ | Barcha vertikal bo'yicha; `?product=`, `?status=` |
| `GET` | `/public/orders/{id}/` | ✓ | Tafsilot |
| `GET` | `/public/orders/{id}/receipt/` | ✓ | Kvitansiya (PDF yoki HTML) |
| `POST` | `/public/orders/{id}/cancel/` | ✓ | Bekor qilish (qoidalar ruxsat bersa) |

Buyurtma statuslari (kanonik): `booked` · `pending` · `ticketed` · `failed` · `cancelled` ·
`voided` · `refunded` · `partially_refunded` · `needs_attention`.
GTS kodlaridan o'girish — [ARCHITECTURE.md](ARCHITECTURE.md) §7.

---

## 22. To'lov

| Metod | Yo'l | Auth | Izoh |
|---|---|---|---|
| `GET` | `/public/payments/{payment_id}/` | ✓ | To'lov holati va summasi |
| `GET` | `/public/payments/methods/` | — | Yoqilgan to'lov usullari |
| `POST` | `/public/payments/{payment_id}/transactions/` | ✓ | Tranzaksiya boshlash (`method`, ixtiyoriy `card_id`) |
| `GET` | `/public/transactions/{id}/` | ✓ | Tranzaksiya holati |
| `POST` | `/public/transactions/{id}/card/` | ✓ | Karta ma'lumotini yuborish |
| `POST` | `/public/transactions/{id}/confirm/` | ✓ | OTP bilan tasdiqlash |
| `POST` | `/public/transactions/{id}/resend-otp/` | ✓ | Kodni qayta yuborish |

Tranzaksiya **uchta oqimdan** birida ketadi. Qaysi biri — javobdagi `flow` aytadi, va
klient boshqa hech narsaga qarab qaror qilmasligi kerak:

| `flow` | Qachon | Keyin nima bo'ladi |
|---|---|---|
| `redirect` | `card_id` berilmagan va usul hosted | `redirect_url` ga o'tiladi; yakun webhook orqali keladi |
| `card_token` | `card_id` berilgan (saqlangan karta) | **Javob yakuniy** — `status` darhol `paid` yoki `failed`; hech qayerga o'tilmaydi |
| `card` | Yangi karta bilan, saqlamasdan to'lash | `card/` → `confirm/` qadamlari shu tranzaksiya ustida bajariladi |

**Redirect** — hosted usul, karta/OTP qadamlari o'tkazib yuboriladi:

```json
POST /public/payments/{payment_id}/transactions/
{ "method": "payme" }
Idempotency-Key: …

→ { "status": "success",
    "data": { "transaction_id": "…", "flow": "redirect", "redirect_url": "https://…" } }
```

**Saqlangan karta** — bitta so'rov, yakuniy javob:

```json
POST /public/payments/{payment_id}/transactions/
{ "method": "payme", "card_id": "7c1a…" }
Idempotency-Key: …

→ { "status": "success",
    "data": { "transaction_id": "…", "flow": "card_token", "status": "paid",
              "card_masked": "860049******6604", "paid_at": "…" } }
```

Karta boshqa mijoznikiga tegishli bo'lsa yoki umuman bo'lmasa — `404` (§19 dagi qoida).
Karta hali tasdiqlanmagan bo'lsa — `409 conflict`. Provayder rad etsa — `400 payment_failed`,
to'lov esa `pending` holida qoladi va boshqa usul bilan qayta urinish mumkin.

**Yangi karta bilan, saqlamasdan** — uch qadam, `flow: card`:

```json
POST /public/transactions/{id}/card/
{ "number": "8600…", "expire": "0329", "save": false }
Idempotency-Key: …

→ { "data": { "transaction_id": "…", "flow": "card", "status": "awaiting_otp",
              "otp_sent_to": "+9989**1234", "otp_expires_at": "…" } }

POST /public/transactions/{id}/confirm/   { "code": "123456" }
→ { "data": { "transaction_id": "…", "status": "paid", … } }
```

`save: true` bo'lsa muvaffaqiyatli to'lovdan keyin karta profilga ham qo'shiladi (§19).

**Bo'lib to'lash** (`type: installment`) uchun qo'shimcha qadamlar:

| Metod | Yo'l | Izoh |
|---|---|---|
| `GET` | `/public/payments/{payment_id}/installment/calculate/` | Oylik to'lov jadvalini hisoblash — **§41** |
| `POST` | `/public/payments/{payment_id}/installment/apply/` | Ariza yuborish — **§41** |

> **Karta raqami serverda saqlanmaydi va log'ga tushmaydi.** Karta-token oqimida
> ([PROJECT.md](PROJECT.md) D7) raqam so'rov tanasidan adapterga, adapterdan provayderga
> o'tadi va shu yerda tugaydi — hech bir ustunda, hech bir log qatorida yo'q. Qaytib
> keladigan yagona narsa — provayder **tokeni**, u shifrlangan holda saqlanadi.
> **CVV umuman so'ralmaydi.** Javobda karta hech qachon to'liq ko'rinmaydi: faqat
> `masked_pan` va oxirgi to'rt raqam.

> **Valyuta.** Karta yo'llari (`card_token` va `card`) faqat **UZS** ni qabul qiladi —
> ikkala provayder ham karta API'sida boshqa valyuta bermaydi. Boshqa valyutadagi to'lovga
> `card_id` berilsa `422 validation` qaytadi va klient redirect oqimiga tushadi.

To'lovdan keyingi oqim (chipta chiqarish, xato bo'lsa avtomatik qaytarish) —
[ARCHITECTURE.md](ARCHITECTURE.md) §8.

---

## 23. Promokod

| Metod | Yo'l | Auth | Izoh |
|---|---|---|---|
| `POST` | `/public/promo/apply/` | ✓ | To'lovga promokod qo'llash |
| `POST` | `/public/promo/discard/` | ✓ | Qo'llangan promokodni olib tashlash |

```json
POST /public/promo/apply/
{ "code": "SUMMER25", "payment_id": "…" }

→ { "status": "success",
    "data": { "discount": { "amount": "50000.00", "currency": "UZS" },
              "total": { "amount": "1200000.00", "currency": "UZS" } } }
```

---

## 24. Kontent (faqat o'qish)

Hammasi auth talab qilmaydi. `?lang=` bilan til tanlanadi (§7).

| Metod | Yo'l | Izoh |
|---|---|---|
| `GET` | `/public/content/blogs/` | Ro'yxat; `?category=`, `?search=` |
| `GET` | `/public/content/blogs/{slug}/` | Maqola |
| `GET` | `/public/content/promotions/` | Aksiyalar; `?placement=carousel\|card` |
| `GET` | `/public/content/promotions/{slug}/` | Aksiya tafsiloti |
| `GET` | `/public/content/faq/` | `?category=` |
| `GET` | `/public/content/contacts/` | Ofis va kontakt nuqtalari |
| `GET` | `/public/content/pages/{slug}/` | Statik sahifa — **§41** |
| `GET` | `/public/content/banners/` | `?placement=` |
| `GET` | `/public/content/popular-directions/` | Bosh sahifadagi yo'nalishlar |
| `GET` | `/public/content/feedbacks/` | Chop etilgan sharhlar |

---

## 25. Murojaat va aloqa

| Metod | Yo'l | Auth | Izoh |
|---|---|---|---|
| `POST` | `/public/leads/` | (✓) | Ariza qoldirish (`source` bo'yicha turli maydonlar) |
| `POST` | `/public/feedbacks/` | ✓ | Sharh qoldirish — moderatsiyaga tushadi |
| `GET` | `/public/feedbacks/my/` | ✓ | O'z sharhlarim va ularning holati |
| `POST` | `/public/subscriptions/` | — | Yangiliklarga obuna |
| `DELETE` | `/public/subscriptions/` | — | Obunani bekor qilish |

```json
POST /public/leads/
{ "source": "corporate", "name": "Muzaffar",
  "fields": { "phone": "998901234567", "company": "…" },
  "comment": "…" }
```

`source` qiymatlari va har biriga tegishli `fields` sxemasi paneldan sozlanadi
(§35, `/admin/leads/sources/`). Kelgan `fields` shu sxema bo'yicha tekshiriladi.

---

## 26. Kataloglar

Qidiruv va yo'lovchi formalari uchun statik ma'lumot. Auth talab qilinmaydi, uzoq
keshlanadi.

| Metod | Yo'l | Izoh |
|---|---|---|
| `GET` | `/public/catalog/places/` | Shahar/aeroport avtoto'ldirish; `?q=`, `?type=` |
| `GET` | `/public/catalog/stations/` | Temir yo'l stansiyalari; `?q=` |
| `GET` | `/public/catalog/document-types/` | Hujjat turlari; `?country=` (ISO 3166-1 alpha-2) |
| `GET` | `/public/catalog/countries/` | Davlatlar — kod, telefon kodi va maskasi, bayroq |
| `GET` | `/public/catalog/airlines/` | Aviakompaniyalar |
| `GET` | `/public/catalog/currencies/` | Valyutalar va joriy kurs |

Kataloglar GTS static servisidan olinadi — [ARCHITECTURE.md](ARCHITECTURE.md) §5.
Elementlar **upstream shaklida** qaytadi: tarjimalar siqilmaydi va `lang` qo'shilmaydi
(§7). Bular bizning kontentimiz emas, GTS ma'lumotnomasi — o'z shaklini berish ikkinchi
lug'atni qo'lda yuritishni anglatardi.

`document-types/` va `countries/` **jonli proxy** sifatida ishlaydi: GTS static servisi
auth talab qilmaydi, javob Redis'da 24 soat keshlanadi va `Cache-Control:
public, max-age=86400` bilan qaytadi. Beat bilan sinxronizatsiya va qolgan uchta
katalog — [PHASES.md](PHASES.md) §2.6.

Ular ishlashi uchun GTS credential'i kiritilgan bo'lishi **shart emas**; aktiv
credential bo'lsa (§29), uning `base_url` i ishlatiladi.

---
---

# III QISM — ADMIN YUZA

Prefiks: **`/api/v1/admin/`**. Foydalanuvchi: React admin panel.
Sub'ekt — **staff**, token `aud: admin`. Barcha endpointlar auth talab qiladi
(`auth/login/` va `auth/refresh/` dan tashqari).

Kirish §5 dagi matritsa bo'yicha. Jadvallardagi **Rol** ustuni — **talab qilinadigan rol**:
`admin` (ikkala rol ham o'tadi) yoki `owner` (faqat `owner`).

**CRUD** deb belgilangan resurslar §8 dagi standart naqshda ishlaydi.

---

## 27. Autentifikatsiya

| Metod | Yo'l | Izoh |
|---|---|---|
| `POST` | `/admin/auth/login/` | Kirish → access + refresh |
| `POST` | `/admin/auth/refresh/` | Token yangilash |
| `POST` | `/admin/auth/logout/` | Sessiyani yopish |
| `GET` | `/admin/auth/me/` | Joriy xodim: profil va rol |
| `POST` | `/admin/auth/password/change/` | Parolni o'zgartirish |
| `POST` | `/admin/auth/password/reset/request/` | Tiklash so'rovi (email orqali) |
| `POST` | `/admin/auth/password/reset/confirm/` | Yangi parol |

```json
GET /admin/auth/me/
→ { "status": "success",
    "data": { "id": "…", "name": "Aziz", "email": "…",
              "role": "admin" } }
```

`role` — `owner` yoki `admin`. Frontend menyuni shu qiymat bo'yicha yig'adi: `admin` da jamoa
bo'limi ko'rinmaydi, integratsiya kalitlari va tizim ekranlari faqat o'qish rejimida ochiladi
(§5).

---

## 28. Sayt sozlamalari

| Metod | Yo'l | Rol | Izoh |
|---|---|---|---|
| `GET` `PATCH` | `/admin/settings/branding/` | `admin` | Logo, favicon, ranglar, shrift, app ikonka va nomi |
| `GET` `PATCH` | `/admin/settings/site/` | `admin` | Sayt nomi, domen, kontakt, ijtimoiy tarmoqlar |
| `GET` `PATCH` | `/admin/settings/languages/` | `admin` | Asosiy til va mavjud tillar |
| `GET` `PATCH` | `/admin/settings/currencies/` | `admin` | Asosiy valyuta va ko'rsatiladigan valyutalar |
| CRUD | `/admin/settings/menu/` | `admin` | Menyu elementlari (ierarxik) — **§41** |
| `GET` `PATCH` | `/admin/settings/features/` | `GET` `admin` · `PATCH` **`owner`** | Bo'limlarni yoqish/o'chirish (blog, sharhlar, …) |
| `GET` | `/admin/settings/products/` | — | Yoqilgan mahsulot vertikallari — **faqat o'qish** |
| `POST` | `/admin/settings/cache/purge/` | `admin` | `site-config` keshini tozalash |

```json
PATCH /admin/settings/branding/
{ "colors": { "primary": "#0A5CFF" }, "logo_id": "…" }
```

O'zgarish saqlanganda `public/site-config/` keshi avtomatik tozalanadi.

> `settings/products/` faqat o'qish uchun: qaysi vertikallar sotilishi GTS shartnomasi bilan
> belgilanadi. Panel ularni ko'rsatadi, o'zgartira olmaydi — sabab
> [PROJECT.md](PROJECT.md) §5 da.

### `features` — bo'lim o'chirilsa u yo'q bo'ladi

Mahsulot ko'p clientga o'rnatiladi va ba'zi clientga ayrim bo'limlar kerak emas
([PROJECT.md](PROJECT.md) §1). Farq **kodda emas, shu bayroqlarda**: bitta image, ko'p
o'rnatma.

Bayroq `false` bo'lsa tegishli yo'llar **ikkala yuzada ham `404 not_found`** qaytaradi —
`/public/…` da ham, `/admin/…` da ham. Panel ham bo'limni ko'rsatmaydi. Sabab: modul
"o'chiq" bo'lib panelda turaversa, xodim unga kontent yozadi va u hech qayerda ko'rinmaydi.

**Ma'lumot o'chirilmaydi.** Blogni o'chirish bloglarni o'chirmaydi — qayta yoqilsa
hammasi joyida. Bu tugma, mina emas.

`403` emas, `404`: `403` "sizga ruxsat yo'q" degani, holbuki hech kimga ruxsat yo'q —
bu o'rnatmada bunday resurs umuman mavjud emas.

| Bayroq | Nimani o'chiradi |
|---|---|
| `blog` | `/admin/content/blogs/` · `/public/content/blogs/` |
| `promotions` | `…/content/promotions/` — aksiyalar |
| `faq` | `…/content/faq/` |
| `contacts` | `…/content/contacts/` |
| `banners` | `…/content/banners/` |
| `popular_directions` | `…/content/popular-directions/` |
| `feedbacks` | `/admin/content/feedbacks/` · `/public/content/feedbacks/` · `/public/feedbacks/` |
| `promo_codes` | `/admin/promos/` · `/public/promo/apply\|discard/` |
| `leads` | `/admin/leads/` · `/admin/subscriptions/` va public juftliklari |
| `reports` | `/admin/reports/` |
| `broadcast` | `/admin/notifications/` — shablonlar va ommaviy yuborish (§36) |
| `loyalty` | Qamrovga kirmaydi va yoqib bo'lmaydi — **§41** |

`promo_codes` ataylab `promo` deb atalmagan: `promotions` (CMS aksiyalari) yonida turadi
va bir harfli farq noto'g'ri tugmani bosishga olib keladi.

**Vertikallar bu ro'yxatga kirmaydi.** Aviachipta, poyezd, sug'urta, eSIM va transfer
`settings/products/` bilan boshqariladi va u paneldan o'zgartirilmaydi — client nima
sotishi GTS shartnomasidan keladi, bayroqdan emas.

Tranzaksion pochta (OTP, parol tiklash) `broadcast` ga **bog'liq emas** — u yadro va
`integrations` orqali ketadi (§29).

> Brendingning qaysi qismi runtime'da, qaysi biri build vaqtida qotib qolishi —
> [PROJECT.md](PROJECT.md) §7 dagi jadval. App ikonkasi va nomi bu yerda saqlanadi, lekin
> ular **keyingi build'da** kuchga kiradi.

---

## 29. Integratsiyalar

| Metod | Yo'l | Rol | Izoh |
|---|---|---|---|
| `GET` | `/admin/integrations/gts/credentials/` | `admin` | Saqlangan GTS credential'lari; parol maskalangan |
| `POST` | `/admin/integrations/gts/credentials/` | `owner` | Yangi credential qo'shish |
| `GET` | `/admin/integrations/gts/credentials/{id}/` | `admin` | Bittasi |
| `PATCH` | `/admin/integrations/gts/credentials/{id}/` | `owner` | O'zgartirish |
| `DELETE` | `/admin/integrations/gts/credentials/{id}/` | `owner` | O'chirish |
| `POST` | `/admin/integrations/gts/credentials/{id}/activate/` | `owner` | **Shu credential'ni tanlash** |
| `POST` | `/admin/integrations/gts/test/` | `owner` | Tanlangan credential bilan ulanishni tekshirish |
| `GET` | `/admin/integrations/payments/` | `admin` | To'lov provayderlari ro'yxati va holati |
| `GET` | `/admin/integrations/payments/{code}/` | `admin` | Bittasi |
| `PATCH` | `/admin/integrations/payments/{code}/` | `owner` | Yoqish/o'chirish, kalitlar, tartib |
| `POST` | `/admin/integrations/payments/{code}/test/` | `owner` | Provayderni tekshirish |
| `GET` | `/admin/integrations/social/` | `admin` | Social kirish provayderlari; sir maskalangan |
| `PATCH` | `/admin/integrations/social/{provider}/` | `owner` | Yoqish/o'chirish, `client_id`, `client_secret` |
| `GET` | `/admin/integrations/notifications/` | `admin` | Email (SMTP) sozlamasi; SMS/push — **§41** |
| `PATCH` | `/admin/integrations/notifications/` | `owner` | Sozlamani o'zgartirish — **§41** |
| `POST` | `/admin/integrations/notifications/test/` | `owner` | Sinov xabari yuborish |

> **Ikkita `test/` hozircha ulanmagan va `404` qaytaradi** — `gts/test/` va
> `payments/{code}/test/`. Sinov *haqiqiy* ulanish demakdir, ulanishni esa
> adapter biladi: GTS klienti va Payme/Click adapterlari
> [PROJECT.md](PROJECT.md) §15 bo'yicha **2-bosqichda** quriladi. Ulargacha
> yozilgan tugma "sozlangan" deb aytardi, "yetib boradi" deb emas — ya'ni tugma
> mavjud bo'lishining yagona sababiga javob bermasdi. Sozlama va credential'lar
> shu bosqichda saqlanadi, sinov adapteri bilan birga keladi
> ([PHASES.md](PHASES.md) §2.13). `notifications/test/` ishlaydi: SMTP adapteri
> bor.

### GTS: bir nechta credential, bittasi tanlangan

O'rnatma GTS'ga o'z agent akkaunti bilan ulanadi ([PROJECT.md](PROJECT.md) D1). Akkaunt
bittadan ko'p bo'lishi mumkin — prod va sinov muhiti, almashtiriladigan eski va yangi
akkaunt — shuning uchun credential **ro'yxat** sifatida saqlanadi va ulardan **aynan
bittasi** `is_active` bo'ladi. **GTS'ga ketadigan har qanday so'rov o'sha tanlangani
bo'yicha ulanadi.**

```json
GET /admin/integrations/gts/credentials/
→ { "status": "success",
    "data": [
      { "id": "9f2c…", "label": "Prod agent", "is_active": true,
        "base_url": "https://api2.globaltravel.space",
        "email": "agent@brand.uz", "password": "••••••••",
        "agent_uid": "17c4…5695",
        "created_at": "…", "updated_at": "…" },
      { "id": "1a7b…", "label": "Zaxira", "is_active": false, … }
    ],
    "errors": [], "meta": null }
```

- `label` — owner beradigan nom, ro'yxat ichida takrorlanmaydi.
- `base_url` — har bir credential o'ziniki bilan turadi, shunda prod va sinov muhiti
  yonma-yon saqlanadi va bitta amal bilan almashadi.
- `agent_uid` — GTS ba'zi endpointlarda `agent-uid` sarlavhasi sifatida so'raydi;
  majburiy emas.
- **Birinchi qo'shilgan credential o'zi tanlangan bo'ladi** — nol yozuvda "qaysi biri
  ishlatiladi?" degan savol umuman tug'ilmasligi uchun.
- `activate/` **sinovdan o'tgani shart emas**: `test/` yiqilishi GTS tomondagi sababdan
  ham bo'lishi mumkin va o'rnatmani o'z panelida qulflab qo'ymasligi kerak.
- Tanlangan credential'ni **o'chirish** — boshqasi bo'lsa `409 conflict` (avval boshqasi
  tanlansin); yagonasi bo'lsa ruxsat, shundan keyin o'rnatma GTS'ga ulana olmaydi.
- Takroriy `label` — `422 validation`.

**Sirlar qaytarilmaydi.** Javobda kalit maskalangan holda keladi, faqat oxirgi belgilar
ko'rinadi; yangi qiymat yuborilsa almashtiriladi:

```json
GET /admin/integrations/payments/payme/
→ { "status": "success",
    "data": { "code": "payme", "enabled": true,
              "title": "Payme", "logo_id": "1a7b…", "logo_url": "/uploads/payment_logo/…svg",
              "sort_order": 10,
              "credentials": { "merchant_id": "…3f2a", "secret_key": "••••••7c" },
              "last_tested_at": "2026-08-05T09:00:00Z",
              "last_test_ok": true, "last_test_error": null } }
```

Maskadagi ko'rinadigan belgilar soni kontraktning bir qismi emas.

### To'lov provayderlari

`{code}` — birinchi relizda `payme` va `click` ([PROJECT.md](PROJECT.md) D7). Ro'yxat
**yopiq**: provayder qo'shish adapter yozishni talab qiladi, ya'ni bu paneldan emas,
relizdan keladigan o'zgarish.

Shu sababli qatorlar **birinchi o'qishda** yaratiladi — har bir kod uchun bittadan,
o'chirilgan holatda. Bo'sh ro'yxat noto'g'ri bo'lardi: panel yoqadigan hech narsa
ko'rmasdi. Bu SMTP bilan bir naqsh, faqat singleton emas.

- `title` va `logo_id` — sayt to'lov usulini qanday ko'rsatishini belgilaydi;
  ikkalasi ham §17 ga tushadi. `logo_id` — `payment_logo` purpose'i bilan
  yuklangan fayl (§11).
- `sort_order` — §29 jadvalidagi "tartib". §17 dagi massiv shu bo'yicha
  saralanadi (teng bo'lsa `code` bo'yicha), ya'ni saytdagi tugmalar tartibi
  paneldan boshqariladi.
- **`enabled: true`** qilish uchun `credentials` bo'sh bo'lmasligi shart, aks
  holda `422 validation` — SMTP dagi bir xil qoida (`host` + `from_address`).
  Qaysi kalitlar aynan kerakligini **adapter biladi**, u esa 2-bosqichda
  keladi; shu sababli hozircha faqat "bo'sh emas" tekshiriladi.
- `credentials` — **erkin obyekt**, sxemasi qat'iy emas. Payme va Click turli
  kalitlar so'raydi va ularning ro'yxati provayder hujjatidan keladi, kontrakt
  esa uni belgilamaydi. Adapter kutadigan kalitlar (panel ularni placeholder
  sifatida ko'rsatadi):

  | Provayder | Kalitlar |
  |---|---|
  | `payme` | `merchant_id`, `key` · ixtiyoriy `checkout_url` (test yoki prod) |
  | `click` | `service_id`, `merchant_id`, `merchant_user_id`, `secret_key` |

- ⚠ **`merchant_id` almashtirilsa o'sha provayderning saqlangan kartalari o'ladi** —
  tokenlar merchant'ga bog'langan va yangi identlik bilan ishlamaydi. Shuning uchun
  merchant identligi o'zgarganda o'sha provayderdagi barcha kartalar `removed` deb
  belgilanadi va mijoz kartani qaytadan qo'shadi. Bu jimgina bo'lmaydi: o'zgarish
  audit jurnaliga tushadi.
- `POST /{code}/test/` credential'ni tekshiradi va natijani `last_tested_at` /
  `last_test_ok` / `last_test_error` ga yozadi:

  ```json
  POST /admin/integrations/payments/payme/test/
  → { "data": { "ok": true, "detail": null, "tested_at": "…" } }
  ```

  `ok: false` ham **`200`** — SMTP `test/` bilan bir xil semantika. `502` qaytarish
  egaga nimadir noto'g'ri ekanini aytardi, lekin nimasi noto'g'ri ekanini aytmasdi,
  holbuki tugma aynan shuning uchun bor. `ok` "credential autentifikatsiyadan o'tdi"
  degani, "to'lov ishlaydi" degani emas: tekshiruv pul harakatlantirmaydi.

`PATCH` da `credentials` **birlashtiriladi**, almashtirilmaydi:

- yuborilgan kalit saqlangan qiymatni almashtiradi;
- qiymat `null` bo'lsa kalit o'chiriladi;
- qiymat **butunlay maska belgilaridan iborat bo'lsa e'tiborsiz qoldiriladi**.
  Aks holda `GET` javobini o'zgartirmasdan qaytarib yuborgan panel hamma sirni
  nuqtalar bilan ustidan yozardi.

`last_tested_at`, `last_test_ok` va `last_test_error` — `test/` ulangunga qadar
`null` (yuqoridagi ogohlantirish).

### Social kirish

Mijoz Google orqali kiradi ([PROJECT.md](PROJECT.md) D5), demak o'rnatmaning o'z
Google client'i bo'lishi kerak. Bu ham **registry**: `apple` mobil ilova bosqichida
qo'shilganda yangi qator bo'ladi, yangi oqim emas.

```json
GET /admin/integrations/social/
→ { "status": "success",
    "data": [
      { "provider": "google", "enabled": true,
        "client_id": "1234…apps.googleusercontent.com",
        "client_secret": "••••••7c",
        "created_at": "…", "updated_at": "…" }
    ],
    "errors": [], "meta": null }
```

- Qatorlar to'lov provayderlari kabi **birinchi o'qishda** yaratiladi.
- `client_id` sir emas — u brauzerga baribir ko'rinadi, shuning uchun to'liq
  qaytadi. `client_secret` esa maskalanadi va shifrlangan holda saqlanadi.
- **`enabled: true`** uchun `client_id` bo'lishi shart, aks holda
  `422 validation`.
- Provayder o'chirilgan yoki sozlanmagan bo'lsa `POST /public/auth/social/{provider}/`
  **`404`** qaytaradi — "bunday narsa yo'q", §28 dagi o'chirilgan bo'lim bilan
  bir shakl. Noma'lum `{provider}` ham `404`.

### Bildirishnomalar: SMTP

O'rnatma pochtasini bitta relay orqali yuboradi, shuning uchun bu resurs —
**singleton**, `{id}` yo'q. Yangi o'rnatmada qator birinchi `GET` da default'lar bilan
yaratiladi, `404` qaytmaydi.

```json
GET /admin/integrations/notifications/
→ { "status": "success",
    "data": { "enabled": true,
              "host": "smtp.brand.uz", "port": 587, "tls": "starttls",
              "username": "no-reply@brand.uz", "password": "••••••••",
              "from_address": "no-reply@brand.uz", "from_name": "Brand Travel",
              "last_tested_at": "2026-08-06T09:00:00Z",
              "last_test_ok": true, "last_test_error": null } }
```

- `tls` — `starttls` (587), `ssl` (465) yoki `none`.
- `password` — `null` bo'lsa **hech narsa saqlanmagan**; maska bo'lsa saqlangan-u
  ko'rsatilmayapti. Panel ikkalasini boshqacha ko'rsatadi.
- `username` bo'sh satr yuborilsa tozalanadi — auth so'ramaydigan ichki relay ham
  haqiqiy sozlama.
- **`enabled: true`** qilish uchun kamida `host` va `from_address` bo'lishi shart,
  aks holda `422 validation`. Aks holda panel "pochta yoqilgan" deydi-yu, hech narsa
  yetib bormaydi.
- `test/` **haqiqiy xabar yuboradi**: `{"to": "..."}` ixtiyoriy, berilmasa tugmani
  bosgan xodimning o'z manziliga ketadi. Pochta sozlanmagan yoki o'chiq bo'lsa —
  `409 conflict`.
- Relay qabul qilmasa bu **`test/` ning xatosi emas**: `200` va
  `{"ok": false, "detail": "<sabab>", "tested_at": …}`. Natija saqlanadi va oddiy
  `GET` da ko'rinadi — ya'ni holatni bilish uchun yana xabar yuborish shart emas.

SMS va push shu resursning qismi bo'ladi, lekin birinchi relizga kirmaydi (§41).

---

## 30. Kontent

| Resurs | Yo'l | Rol | Izoh |
|---|---|---|---|
| Blog | `/admin/content/blogs/` | `admin` | CRUD; `?status=draft\|published`, `?category=` |
| Aksiyalar | `/admin/content/promotions/` | `admin` | CRUD; `placement`, `starts_at`, `ends_at` |
| FAQ | `/admin/content/faq/` | `admin` | CRUD; `?category=` |
| Kontaktlar | `/admin/content/contacts/` | `admin` | CRUD — ofis nuqtalari, koordinatalar |
| Sahifalar | `/admin/content/pages/` | `admin` | CRUD — statik sahifalar — **§41** |
| Bannerlar | `/admin/content/banners/` | `admin` | CRUD; `?placement=` |
| Mashhur yo'nalishlar | `/admin/content/popular-directions/` | `admin` | CRUD |

**Qo'shimcha amallar:**

| Metod | Yo'l | Izoh |
|---|---|---|
| `POST` | `/admin/content/{resource}/{id}/publish/` | Chop etish |
| `POST` | `/admin/content/{resource}/{id}/unpublish/` | Chop etishni to'xtatish |
| `POST` | `/admin/content/{resource}/reorder/` | Tartibni o'zgartirish (`[{id, order}]`) |

Tarjima qilinadigan maydonlar obyekt sifatida keladi va shunday yuboriladi:

```json
PATCH /admin/content/blogs/{id}/
{ "title": { "uz": "Sarlavha", "ru": "Заголовок" },
  "body":  { "uz": "…", "ru": "…" },
  "status": "published" }
```

Barcha tillar to'ldirilishi shart emas — bo'sh til uchun public API fallback qiladi (§7).

### Sharh moderatsiyasi

| Metod | Yo'l | Rol | Izoh |
|---|---|---|---|
| `GET` | `/admin/content/feedbacks/` | `admin` | `?status=pending\|accepted\|rejected` |
| `GET` | `/admin/content/feedbacks/{id}/` | `admin` | Tafsilot |
| `POST` | `/admin/content/feedbacks/{id}/accept/` | `admin` | Chop etish |
| `POST` | `/admin/content/feedbacks/{id}/reject/` | `admin` | Rad etish (`reason` bilan) |
| `DELETE` | `/admin/content/feedbacks/{id}/` | `admin` | O'chirish |

---

## 31. Buyurtmalar

| Metod | Yo'l | Rol | Izoh |
|---|---|---|---|
| `GET` | `/admin/orders/` | `admin` | Barcha vertikal; filtrlar quyida |
| `GET` | `/admin/orders/{id}/` | `admin` | To'liq tafsilot: yo'lovchilar, narx, to'lov, tarix |
| `GET` | `/admin/orders/{id}/receipt/` | `admin` | Kvitansiya |
| `POST` | `/admin/orders/{id}/cancel/` | `admin` | Bekor qilish |
| `POST` | `/admin/orders/{id}/push/` | `admin` | Mijozga push xabar yuborish — **§41** |
| `POST` | `/admin/orders/{id}/note/` | `admin` | Ichki izoh qo'shish |
| `POST` | `/admin/orders/{id}/sync/` | `admin` | GTS'dan holatni qayta olish |

**Filtrlar**: `?product=`, `?status=`, `?payment_status=`, `?customer_id=`, `?search=`
(buyurtma raqami, PNR, yo'lovchi ismi, telefon), `?created_from=`, `?created_to=`.

Buyurtma holati GTS'dan keladi — panel uni **o'zgartirmaydi**, faqat ko'rsatadi va
ruxsat etilgan amallarni (bekor qilish) uzatadi. Har bir buyurtmada `available_actions`
massivi bor, frontend tugmalarni shunga qarab ko'rsatadi:

```json
{ "id": "…", "product": "flight", "status": "ticketed",
  "available_actions": ["cancel", "push", "receipt"] }
```

> `needs_attention` holatidagi buyurtmalar alohida ajratiladi: bu — to'lov o'tib, chipta
> chiqmagan va avtomatik qaytarish ham bajarilmagan holat ([ARCHITECTURE.md](ARCHITECTURE.md) §8).
> Ular qo'lda hal qilinishi kerak.

---

## 32. To'lovlar

| Metod | Yo'l | Rol | Izoh |
|---|---|---|---|
| `GET` | `/admin/payments/` | `admin` | To'lovlar; `?status=`, `?method=` |
| `GET` | `/admin/payments/{id}/` | `admin` | Tafsilot va tranzaksiyalar |
| `GET` | `/admin/payments/transactions/` | `admin` | Barcha tranzaksiyalar |
| `GET` | `/admin/payments/transactions/{id}/` | `admin` | Tranzaksiya tafsiloti |
| `POST` | `/admin/payments/{id}/refund/` | `admin` | Qaytarish (to'liq yoki qisman) |
| `POST` | `/admin/payments/{id}/sync/` | `admin` | Provayderdan holatni qayta olish |

`refund/` — `Idempotency-Key` majburiy (§10).

---

## 33. Promokodlar

| Metod | Yo'l | Rol | Izoh |
|---|---|---|---|
| CRUD | `/admin/promos/` | `admin` | Kod, chegirma turi, chegaralar, amal muddati |
| `POST` | `/admin/promos/{id}/activate/` | `admin` | Faollashtirish |
| `POST` | `/admin/promos/{id}/deactivate/` | `admin` | To'xtatish |
| `GET` | `/admin/promos/{id}/stats/` | `admin` | Ishlatilish statistikasi |
| `GET` | `/admin/promos/{id}/usages/` | `admin` | Kim, qachon, qaysi buyurtmada ishlatgan |

---

## 34. Mijozlar

| Metod | Yo'l | Rol | Izoh |
|---|---|---|---|
| `GET` | `/admin/customers/` | `admin` | `?search=` (ism, email, telefon) |
| `GET` | `/admin/customers/{id}/` | `admin` | Profil va statistika |
| `GET` | `/admin/customers/{id}/orders/` | `admin` | Buyurtmalari |
| `POST` | `/admin/customers/{id}/block/` | `admin` | Bloklash |
| `POST` | `/admin/customers/{id}/unblock/` | `admin` | Blokdan chiqarish |
| `DELETE` | `/admin/customers/{id}/` | `owner` | O'chirish (shaxsiy ma'lumotni tozalash) |

---

## 35. Murojaatlar

| Metod | Yo'l | Rol | Izoh |
|---|---|---|---|
| `GET` | `/admin/leads/` | `admin` | `?source=`, `?status=` |
| `GET` | `/admin/leads/{id}/` | `admin` | Tafsilot |
| `PATCH` | `/admin/leads/{id}/` | `admin` | Holat, mas'ul, izoh |
| CRUD | `/admin/leads/sources/` | `admin` | Lead manbalari: `code`, nomi va `fields` sxemasi |
| `GET` | `/admin/subscriptions/` | `admin` | Obunachi'lar ro'yxati |
| `GET` | `/admin/subscriptions/export/` | `admin` | CSV eksport (async) |

`leads/sources/` — §25 dagi `source` qiymatlari va har biriga tegishli `fields` sxemasi shu
yerdan boshqariladi. Public `POST /public/leads/` kelgan `fields` ni shu sxema bo'yicha tekshiradi.

---

## 36. Bildirishnomalar

| Metod | Yo'l | Rol | Izoh |
|---|---|---|---|
| CRUD | `/admin/notifications/templates/` | `admin` | Xabar shablonlari |
| `POST` | `/admin/notifications/broadcast/` | `admin` | Ommaviy yuborish (async job) |
| `GET` | `/admin/notifications/broadcasts/` | `admin` | Yuborishlar tarixi va natijasi |

Birinchi relizda kanal — **email**. SMS va push keyingi bosqichda (§41).

---

## 37. Hisobotlar

| Metod | Yo'l | Rol | Izoh |
|---|---|---|---|
| `GET` | `/admin/reports/dashboard/` | `admin` | Bosh sahifadagi ko'rsatkichlar |
| `GET` | `/admin/reports/sales/` | `admin` | Sotuv; `?group_by=day\|product\|method` |
| `GET` | `/admin/reports/fields/` | `admin` | Eksport uchun mavjud maydonlar katalogi |
| CRUD | `/admin/reports/views/` | `admin` | Saqlangan hisobot ko'rinishlari |
| `POST` | `/admin/reports/export/` | `admin` | Eksport (async job → `xlsx`/`csv`) |

```json
POST /admin/reports/export/
{ "type": "sales", "format": "xlsx",
  "date_from": "2026-07-01", "date_to": "2026-07-31",
  "fields": ["order_number", "product", "total", "status"] }

→ 202 { "status": "success", "data": { "job_id": "…", "state": "pending" } }
```

Kun bo'yicha guruhlash o'rnatma vaqt mintaqasida hisoblanadi (saqlash UTC'da).

---

## 38. Jamoa

| Metod | Yo'l | Rol | Izoh |
|---|---|---|---|
| CRUD | `/admin/staff/` | `owner` | Xodimlar |
| `POST` | `/admin/staff/{id}/block/` | `owner` | Bloklash |
| `POST` | `/admin/staff/{id}/reset-password/` | `owner` | Parol tiklash havolasini yuborish |

Rollar ikkita va **kodda qat'iy belgilangan** ([PROJECT.md](PROJECT.md) §9) — panel orqali na
yangi rol yaratiladi, na mavjudining ruxsati o'zgartiriladi. Xodim yaratilganda yoki
tahrirlanganda unga `owner` yoki `admin` biriktiriladi, boshqa qiymat `422 validation_error`
beradi.

`reset-password/` — owner havolani yuboradi, parolni **xodimning o'zi** tanlaydi;
boshqa odam uchun parol o'rnatilib, keyin unga aytilmaydi. SMTP xatni qabul qilmasa
bu endpoint `502 upstream_error` qaytaradi — §18 dagi ochiq endpointlardan farqli,
chunki bu yerda tugmani bosgan owner javob kutib turadi va jim `204` uni ketmagan
xatni kutishga qoldirardi. Yashiradigan narsa ham yo'q: so'rov allaqachon
autentifikatsiyalangan.

Bu bo'lim butunlay `owner` da: `admin` §5 matritsasidagi **Jamoa** guruhiga umuman kirmaydi,
shuning uchun bu yo'llarga urinsa `403 forbidden` oladi.

---

## 39. Tizim

| Metod | Yo'l | Rol | Izoh |
|---|---|---|---|
| `GET` | `/admin/system/health/` | `admin` | DB, Redis, GTS, to'lov provayderlari holati |
| `GET` | `/admin/system/version/` | `admin` | Backend va panel versiyasi |
| `GET` | `/admin/system/audit/` | `admin` | Audit log; `?actor=`, `?resource=`, `?action=` |
| `GET` | `/admin/jobs/{id}/` | `admin` | Async ish holati (§9) |
| `POST` | `/admin/uploads/` | `admin` | Fayl yuklash (§11) |

`system/health/` va `system/version/` — muammo qaysi tomonda ekanini ajratish uchun birinchi
murojaat qilinadigan endpointlar ([PROJECT.md](PROJECT.md) §4, §14).

Bu yo'llarning hammasi `admin` uchun **faqat o'qish** (§5, *Tizim va audit* satri) —
`/admin/uploads/` bundan mustasno, u kontent guruhiga tegishli.

---
---

# IV QISM — WEBHOOK'LAR

## 40. To'lov provayderi callback'lari

| Metod | Yo'l | Auth | Izoh |
|---|---|---|---|
| `POST` | `/api/v1/webhooks/payments/{provider}/` | — | Provayder callback'i |

- `{provider}` — `payme`, `click`.
- Auth **yo'q**, lekin har bir so'rov **imzo bo'yicha tekshiriladi**. Imzo noto'g'ri bo'lsa
  **hech qanday holat o'zgarmaydi** — bu shartsiz qoida. Javob shakli esa provayderniki:
  odatda `401`, lekin **Payme'da `200` va JSON-RPC xatosi `-32504`**, chunki uning protokoli
  shuni kutadi va `401` ni ko'r-ko'rona qayta urinish sababi deb biladi. Qaysi biri
  bo'lishidan qat'i nazar, tekshirilishi kerak bo'lgan narsa bitta: holat o'zgarmaganligi.
- Callback **takroriy kelishi mumkin** — provayderlar qayta yuboradi. Ishlov idempotent:
  bir xil hodisa ikki marta yechmaydi ([ARCHITECTURE.md](ARCHITECTURE.md) §8).
- Payme'ning merchant protokoli provayder tomonidan boshqariladigan JSON-RPC — uning oltita
  metodi (`CheckPerformTransaction`, `CreateTransaction`, `PerformTransaction`,
  `CancelTransaction`, `CheckTransaction`, `GetStatement`) shu endpoint ortida amalga
  oshiriladi. Autentifikatsiya — HTTP Basic, foydalanuvchi `Paycom`, parol merchant kaliti.
- Click esa `application/x-www-form-urlencoded` yuboradi va ikkita amali bor: `action=0`
  (Prepare) va `action=1` (Complete). Imzo — so'rov maydonlaridan yig'ilgan `md5`.
- **Saqlangan karta bilan to'langan tranzaksiya ham callback oladi.** To'lov so'rov ichida
  yakunlangan bo'lsa ham Payme protokoli chekni o'z yo'li bilan yopadi — shuning uchun
  callback "bu allaqachon to'langan" holatini xatoga aylantirmasligi kerak.
- Envelope qoidasi bu yerda **qo'llanmaydi**: javob shakli provayder protokoli talab qilgan
  ko'rinishda bo'ladi.

---
---

# V QISM — RELIZ QAMROVI

## 41. Birinchi relizga kirmaydigan endpointlar

Qamrovning yakuniy manbai — [PROJECT.md](PROJECT.md) §10 (modullar) va §15 (bosqichlar).
Bu yerda faqat **endpoint darajasidagi** ro'yxat.

| Endpoint / imkoniyat | Nega keyinroq | Qachon |
|---|---|---|
| `/public/payments/{payment_id}/installment/calculate/`, `/apply/` | D7 relizni **Payme va Click** bilan cheklaydi va ikkalasi ham bo'lib to'lash bermaydi — bu qadamlar uchun provayder umuman nomlanmagan | Bo'lib to'lash provayderi belgilanganda |
| `/public/auth/social/apple/` | iOS ilovada Google bo'lgani uchun Apple qoidalari talab qiladi (D5) | 6-bosqich |
| Telefon + SMS OTP (`login`, `register/confirm`, `password/reset`) | MVP'da faqat email/SMTP (D6) | SMS xizmati ulanganda |
| `/public/auth/devices/`, `/admin/orders/{id}/push/`, push broadcast | Push xizmati birinchi relizga kirmaydi | 6–7-bosqich |
| `/admin/settings/menu/` | Menyu konstruktori modeli hali aniqlanmagan ([PROJECT.md](PROJECT.md) §16, 1-savol) | 5-bosqich |
| `/admin/content/pages/`, `/public/content/pages/{slug}/` | Sahifa tanasi sxemasi hali aniqlanmagan (shu savol) | 5-bosqich |
| `/admin/integrations/notifications/` — SMS va push qismi | Email/SMTP ✓, qolgani keyinroq | SMS/push ulanganda |
| `features.loyalty` | Loyalty dasturi qamrovga kirmaydi ([PROJECT.md](PROJECT.md) §3) | — |

Kontrakt **hozirdan** to'liq belgilangan: bu endpointlar API'da mavjud, lekin birinchi relizda
ulanmaydi. Chaqirilganda `404 not_found` qaytaradi.

> **Ro'yxatdan chiqqan:** `/public/transactions/{id}/card/`, `/confirm/` va `/resend-otp/`
> endi ulanadi. D7 qayta ko'rib chiqildi — saqlangan kartalar talab qilingani uchun Payme va
> Click'ning karta-token API'lari ishlatiladi ([PROJECT.md](PROJECT.md) §11, §13). Bu
> yo'llar 2-fazada, to'lov moduli bilan birga keladi.

> `404` ning **ikkita sababi** bor va ular boshqa-boshqa. Bu yerdagisi — relizga
> kirmagan, ya'ni **har bir o'rnatmada** bir xil. Ikkinchisi — client bo'limni
> paneldan o'chirgan (§28), ya'ni **shu o'rnatmaga xos** va qaytarib yoqilishi mumkin.
> Javob tanasi ikkalasida bir xil: mijozga ikkalasi ham "bunday narsa yo'q" degani, va
> qaysi biri ekanini aytish o'rnatma haqida keraksiz ma'lumot berardi.
