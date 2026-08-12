# Admin panel — frontend spetsifikatsiyasi

Bu hujjat React admin panelini quradigan frontend dasturchi uchun yozilgan.
Bu yerda **bugun backend'da haqiqatan mavjud** yuza tasvirlangan — hammasi
ishlaydi va Swagger'da ko'rinadi (`/api/v1/docs`). Kontrakt bo'yicha yakuniy
manba — [API.md](API.md) (III qism); ziddiyat bo'lsa API.md yutadi.
Hali qurilmagan bo'limlar oxirida alohida ro'yxatda ([§12](#12-hali-qurilmagan-bolimlar)).

Baza URL: **`/api/v1/admin/`**. OpenAPI: `/api/v1/openapi.json`.

---

## 1. Umumjahon qoidalar (har bir so'rovga tegishli)

### 1.1 Javob konverti (envelope)

Har bir javob (204 va fayl yo'llaridan tashqari) bitta shaklda keladi:

```json
{ "status": "success", "data": { ... }, "errors": [], "meta": null }
{ "status": "error",   "data": null,   "errors": [ {"code": "...", "field": null, "message": "..."} ], "meta": null }
```

- Ro'yxatlarda `data` — massiv, `meta` — `{page, page_size, total, total_pages}`.
- `errors[].field` faqat `validation` xatolarida to'ldiriladi — formadagi aynan
  qaysi maydon xato ekanini ko'rsatadi (`"email"`, ichma-ich bo'lsa `"a.b"`).
  Formani shu bo'yicha bo'yang.

### 1.2 Xatolar katalogi (yopiq ro'yxat)

| `code` | HTTP | Panel nima qilsin |
|---|---|---|
| `validation` | 422 | Maydon ostida xabar ko'rsat (`field` bor) |
| `unauthorized` | 401 | Access token eskirgan — refresh qil; refresh ham 401 bersa → login sahifasi |
| `forbidden` | 403 | "Ruxsat yo'q" — odatda `owner` roli kerak bo'lgan amal |
| `not_found` | 404 | Resurs yo'q **yoki** bo'lim o'chirilgan (feature flag) — farqlanmaydi |
| `conflict` | 409 | Biznes qoidasi to'sdi (masalan, oxirgi owner'ni o'chirish) — `message` ni ko'rsat |
| `rate_limited` | 429 | `Retry-After` sarlavhasidagi soniyadan keyin qayta urin |
| `upstream_error` | 502 | Tashqi xizmat (GTS/SMTP) xatosi; asl matn `message` da, asl kod `meta.upstream` da |
| `upstream_timeout` | 504 | Tashqi xizmat javob bermadi |
| `internal` | 500 | "Nimadir buzildi" + `X-Request-Id` ni ko'rsat (supportga aytish uchun) |

### 1.3 Boshqa qoidalar

- **Har bir yo'l `/` bilan tugaydi.** Slash'siz chaqiruv 307 emas, **404** —
  API client'ida yo'llarni to'g'ri yozing.
- **Rate limit**: butun admin yuzasi **300 so'rov/daqiqa**, `auth/*` esa
  qo'shimcha **5 so'rov/daqiqa/IP** (login formasida buni hisobga oling —
  noto'g'ri parolni 5 marta terib ko'rish mumkin xolos).
- **`X-Request-Id`** har javobda qaytadi — xato ekranlarida ko'rsating,
  backend logi bilan bog'lash uchun.
- **CORS**: ruxsat etilgan origin panel sozlamasidagi `site.domain`dan
  hisoblanadi (`https://<domain>` va `https://www.<domain>`). Ya'ni panel
  o'sha domenda (yoki dev rejimda `DEBUG=true` bilan har qanday originda)
  ishlashi kerak.
- Sana/vaqt — ISO-8601, UTC (`...Z`). Ko'rsatishda lokal vaqtga o'giring.

---

## 2. Auth va sessiya

Subyekt — **xodim** (staff), token `aud: admin`. Mijoz (customer) tokeni
admin yuzasida **403** oladi — ikkalasi hech qachon aralashmaydi.

| Metod | Yo'l | Auth | Izoh |
|---|---|---|---|
| `POST` | `auth/login/` | — | `{login: email, password}` → `{access_token, refresh_token, expires_in}` |
| `POST` | `auth/refresh/` | — | `{refresh_token}` → yangi juftlik |
| `POST` | `auth/logout/` | ✓ | `{refresh_token}` → 204 |
| `GET` | `auth/me/` | ✓ | `{id, name, email, role}` — menyu shu `role`dan quriladi |
| `POST` | `auth/password/change/` | ✓ | `{current_password, new_password}` → 204 |
| `POST` | `auth/password/reset/request/` | — | `{email}` → **doim 204** (akkaunt bor-yo'qligini oshkor qilmaydi) |
| `POST` | `auth/password/reset/confirm/` | — | `{token, new_password}` → 204; token emaildan, 1 soat, bir martalik |

Muhim mexanika:

- **Access 15 daqiqa, refresh 12 soat.** `expires_in` — access TTL (soniya).
- **Refresh rotatsiyasi**: har `refresh/` eski refresh tokenni bekor qilib
  yangisini beradi. Eski (allaqachon ishlatilgan) refresh bilan kelish —
  **barcha sessiyalar bekor bo'ladi** (o'g'irlik belgisi deb qaraladi).
  Interceptor'da bitta refresh so'rovini navbatlashtiring (parallel refresh
  yubormaslik kerak).
- **`password/change/` dan keyin barcha sessiyalar (shu jumladan joriy)
  bekor bo'ladi** — panel foydalanuvchini login sahifasiga qaytarsin.
- Login xatolari: noto'g'ri email/parol → 401; bloklangan akkaunt → 403.
- Xodim bloklansa/o'chirilsa darhol kuchga kiradi (har so'rovda bazadan
  tekshiriladi) — 401/403 kelganda "sessiya tugadi" oqimiga o'ting.

### 2.1 Rollar

Ikkita rol, kodda qat'iy: **`owner` ⊃ `admin`**.

| Faqat `owner` ko'radigan/bosadigan narsalar |
|---|
| Jamoa bo'limi to'liq (`staff/*` — ro'yxat ham) |
| Sozlamalar → features PATCH (o'qish hammaga) |
| Integratsiyalarning barcha **yozuvlari** (GTS/SMTP/to'lov/social) — o'qish hammaga |

`role === "admin"` bo'lsa bu tugma/bo'limlarni yashiring — backend baribir
403 beradi, lekin UX uchun ko'rsatmaslik to'g'ri.

---

## 3. Ro'yxatlar — umumiy naqsh

Barcha ro'yxat endpointlari bir xil query parametrlarni oladi:

```
?page=1&page_size=20            # 1..100, default 20
&search=...                     # endpointga qarab qaysi ustunlarda izlashi har xil
&ordering=-created_at           # oq ro'yxatdan maydon; "-" = kamayish
&created_from=2026-01-01T00:00:00Z
&created_to=...
```

`meta` dan pagination quriladi: `{page, page_size, total, total_pages}`.
Noto'g'ri `ordering` qiymati → 422 (`field: "ordering"`).

---

## 4. Tarjimali maydonlar (Translated obyekt)

Ko'p kontent maydonlari uch tilli obyekt: `{"uz": "...", "ru": "...", "en": "..."}`.

- Admin yuzada **doim to'liq obyekt** keladi va yuboriladi (public yuzada
  esa bitta tilga yig'ilgan bo'ladi — panelga aloqasi yo'q).
- Yaratishda kamida bitta til bo'sh bo'lmasligi kerak, aks holda 422.
- **PATCH/PUT'da tillar birlashtiriladi (merge)**: faqat `{"ru": "..."}`
  yuborsangiz uz/en o'z joyida qoladi. UI'da har til uchun tab qiling va
  faqat o'zgarganini yuborish mumkin.

---

## 5. Fayl yuklash oqimi

Rasm biriktirish **ikki qadam**: avval yuklash, keyin id'ni egasiga berish.

1. `POST /api/v1/admin/uploads/` — `multipart/form-data`: `file` + `purpose`.
   Javob: `{id, url, mime, size, purpose, filename, ...}` (201).
2. Qaytgan `id` ni tegishli resursga yuboring: `settings/branding/` dagi
   `logo_id | favicon_id | app_icon_id`, yoki `integrations/payments/{code}/`
   dagi `logo_id`.

| `purpose` | Turlar | Limit |
|---|---|---|
| `logo` | png, jpeg, webp, gif, svg | 2 MB |
| `favicon` | + ico | 1 MB |
| `app_icon` | png, jpeg, webp | 2 MB |
| `payment_logo` | rasmlar + svg | 1 MB |
| `blog_cover`, `promo_banner`, `banner` | rasmlar | 5 MB (bo'limlari hali qurilmagan) |
| `document` | rasmlar + pdf | 10 MB (private) |

Eslatmalar:

- Noto'g'ri tur/hajm/purpose → 422 (`field: "file"`), xabari tayyor —
  ko'rsatsangiz bo'ladi. MIME ham, faylning haqiqiy bayti ham tekshiriladi.
- `url` root-relative (`/uploads/...`) — preview uchun bevosita `<img src>`.
- **Bog'lanmagan fayl 24 soatdan keyin o'chiriladi** — yuklab qo'yib formani
  keyinroqqa qoldirmang; saqlashda id'ni darhol biriktiring.
- Noto'g'ri purpose'ni bog'lash (masalan `favicon`ni `logo_id`ga) → 422.

---

## 6. Sozlamalar bo'limi (`settings/*`)

| Metod | Yo'l | Rol | Nima |
|---|---|---|---|
| `GET/PATCH` | `settings/branding/` | admin | Logo, favicon, app icon, ranglar, shrift, app nomi |
| `GET/PATCH` | `settings/site/` | admin | Sayt nomi (tarjimali), domen, support telefon/email, social havolalar |
| `GET/PATCH` | `settings/languages/` | admin | `{default, available}` — faqat `uz/ru/en` |
| `GET/PATCH` | `settings/currencies/` | admin | `{default, available}` — ISO-4217 |
| `GET` | `settings/features/` | admin | Bo'lim bayroqlari |
| `PATCH` | `settings/features/` | **owner** | Bayroqni yoqish/o'chirish |
| `GET` | `settings/products/` | admin | Vertikallar (flight, railway, …) — **faqat o'qish, PATCH yo'q** |
| `POST` | `settings/cache/purge/` | admin | site-config keshini tozalash → 204 |

- **Branding**: `colors` — faqat `{primary, accent, background}` kalitlari,
  `#RGB`/`#RRGGBB` format; `font_family` — `Inter | Roboto | Open Sans |
  Montserrat | Manrope` dan biri. `*_url` maydonlari preview uchun tayyor.
- **Site → `domain` CORS'ni boshqaradi** — noto'g'ri domen kiritilsa panel
  o'zini qulflab qo'yishi mumkin; saqlashdan oldin tasdiq so'rang.
- **Features**: bayroqlar — `blog, promotions, faq, contacts, banners,
  popular_directions, feedbacks, promo_codes, leads, reports, broadcast`
  (default yoniq) va `loyalty` (**qulflangan** — yuborilsa 422; UI'da
  disabled ko'rsating). Bayroq o'chsa, u qo'riqlagan bo'limlar ikkala yuzada
  ham **404** qaytaradi — paneldagi tegishli menyu bandini yashiring
  (bugun qurilganlardan: `faq` → Kontent/FAQ, `leads` → Murojaatlar).
- **Products** — GTS shartnomasidan keladi, panel o'zgartira olmaydi;
  ro'yxatni ko'rsating, switch'siz.
- Har qanday settings yozuvi keshni o'zi tozalaydi; `cache/purge/` — qo'lda
  tozalash tugmasi (kamdan-kam kerak).

---

## 7. Integratsiyalar (`integrations/*`)

O'qish — har qanday admin; **yozish — faqat owner**.

### 7.1 GTS credential'lari

| Metod | Yo'l | Rol |
|---|---|---|
| `GET` | `integrations/gts/credentials/` | admin |
| `POST` | `integrations/gts/credentials/` | owner (201) |
| `GET/PATCH/DELETE` | `integrations/gts/credentials/{id}/` | o'qish admin, yozish owner |
| `POST` | `integrations/gts/credentials/{id}/activate/` | owner |

- Maydonlar: `label` (unikal), `email`, `password`, `base_url`, `agent_uid`.
- **Ro'yxat, bittasi aktiv.** Birinchi yaratilgani avtomatik aktiv bo'ladi.
  `activate/` boshqasini aktivlashtiradi. UI: radio/badge bilan aktivini
  ko'rsating.
- Aktiv qatorni o'chirish (boshqalari bor bo'lsa) → 409 "avval boshqasini
  aktivlashtiring".
- **Sirlar maskalanadi**: o'qishda `password` doim `••••••••`. PATCH'da
  `password` yuborilsa almashadi, yuborilmasa qoladi. **Formadagi maskalangan
  qiymatni qayta yubormang** — parol maydonini bo'sh qoldiring va faqat
  foydalanuvchi kiritsa qo'shing.
- Aviachipta qidiruvi shu aktiv credential orqali ishlaydi — bu bo'lim
  panelning eng muhim joylaridan biri.

### 7.2 SMTP (email) — singleton

| Metod | Yo'l | Rol |
|---|---|---|
| `GET` | `integrations/notifications/` | admin |
| `PATCH` | `integrations/notifications/` | owner |
| `POST` | `integrations/notifications/test/` | owner |

- Maydonlar: `enabled, host, port, tls (starttls|ssl|none), username,
  password, from_address, from_name` + oxirgi sinov natijasi
  (`last_tested_at, last_test_ok, last_test_error`).
- `password: null` = hech narsa saqlanmagan, `••••••••` = saqlangan —
  ikkalasini farqlab ko'rsating.
- `enabled: true` uchun `host` va `from_address` bo'lishi shart (aks holda 422).
- **Test** haqiqiy xat yuboradi: `{to?: email}` (bo'sh qoldirilsa o'zingizga)
  → `{ok, detail, tested_at}`. Relay rad etsa ham **200 + `ok: false`** —
  natijani rangli ko'rsating. SMTP o'chiq bo'lsa → 409.

### 7.3 To'lov provayderlari

| Metod | Yo'l | Rol |
|---|---|---|
| `GET` | `integrations/payments/` | admin |
| `GET/PATCH` | `integrations/payments/{code}/` | o'qish admin, yozish owner |

- `{code}` — `payme` yoki `click`. POST/DELETE **yo'q** — qatorlar o'zi
  paydo bo'ladi.
- Maydonlar: `enabled, title, logo_id, sort_order, credentials: {kalit: qiymat}`.
- **`credentials` merge bo'ladi**: o'qishda qiymatlar maskalangan; ichida `•`
  bo'lgan qiymat "o'zgarmagan" deb tashlab yuboriladi (formani qayta yuborish
  xavfsiz); `null` — kalitni o'chiradi; yangi qiymat — almashtiradi.
- `enabled: true` credential'siz → 422.

### 7.4 Social kirish

- `GET integrations/social/` (admin), `PATCH integrations/social/google/` (owner).
- `{enabled, client_id, client_secret}` — `client_id` ochiq, `client_secret`
  maskalanadi. `enabled: true` uchun `client_id` shart.

---

## 8. Kontent (`content/*`)

### 8.1 FAQ — `faq` bayrog'i ostida

| Metod | Yo'l | Izoh |
|---|---|---|
| `GET` | `content/faq/` | `?status=draft|published`, `?category=`, `search` (faqat category bo'yicha) |
| `POST` | `content/faq/` | 201, **draft** bo'lib yaratiladi |
| `GET/PATCH/DELETE` | `content/faq/{id}/` | |
| `POST` | `content/faq/{id}/publish/` \| `unpublish/` | idempotent |
| `POST` | `content/faq/reorder/` | tana: `[{id, order}, ...]` → 204 |

- `question`, `answer` — tarjimali obyektlar (majburiy); `category` — erkin kod.
- UI: draft/published badge, drag-and-drop tartiblash (`reorder/` bitta
  so'rovda hammasini oladi).

### 8.2 Qat'iy sahifalar — bayroqsiz (doim bor)

Uchta slug: `privacy-policy`, `terms`, `about`. Har birida:

| Metod | Yo'l |
|---|---|
| `GET` | `content/{slug}/` |
| `PUT` | `content/{slug}/` (upsert — birinchi PUT yaratadi) |
| `POST` | `content/{slug}/publish/` \| `unpublish/` |

- `{title, body}` — tarjimali; **`body` — har til uchun markdown** (editor
  markdown bo'lsin). PUT tillarni birlashtiradi.
- Hali yozilmagan sahifada GET → 404 — bo'sh editor ko'rsating.

---

## 9. Murojaatlar (`leads/*`) — `leads` bayrog'i ostida

| Metod | Yo'l | Izoh |
|---|---|---|
| `GET` | `leads/` | `?status=new|in_progress|done`, `search` (topic/name/contact) |
| `GET/PATCH` | `leads/{id}/` | PATCH: `{status?, note?}` — `note` ichki izoh, `""` tozalaydi |
| `GET/POST` | `leads/topics/` | Mavzular lug'ati |
| `GET/PATCH/DELETE` | `leads/topics/{id}/` | `name` tarjimali, `sort_order` |
| `GET/PATCH` | `leads/support/` | Support kontakt (singleton): username, telefon, email, ish vaqti (tarjimali) |

Lead maydonlari: `{id, topic, name, contact, message, status, note,
customer_id, created_at}`. Mas'ul biriktirish (assignee) ataylab yo'q.

---

## 10. Jamoa (`staff/*`) — **butun bo'lim faqat owner**

| Metod | Yo'l | Izoh |
|---|---|---|
| `GET` | `staff/` | `search` (name/email), `ordering`: name/email/role/created_at/last_login_at |
| `POST` | `staff/` | `{email, name, role, password}` → 201 |
| `GET/PATCH/DELETE` | `staff/{id}/` | PATCH: `{email?, name?, role?}` |
| `POST` | `staff/{id}/block/` | Bloklash (sessiyalari darhol o'ladi) |
| `POST` | `staff/{id}/reset-password/` | Emailga reset havola → 204 |

Qo'riqlash qoidalari (hammasi **409** qaytaradi — xabarini ko'rsating):

- O'zingizni o'chirish/bloklash mumkin emas.
- Oxirgi aktiv owner'ni o'chirish, bloklash yoki admin'ga tushirish mumkin emas.

⚠ **Blokdan chiqarish endpointi hozircha yo'q** — bloklash bir tomonlama.
UI'da block tugmasiga jiddiy tasdiq oynasi qo'ying.

---

## 11. Tizim (`system/*`)

| Metod | Yo'l | Izoh |
|---|---|---|
| `GET` | `system/health/` | `{status, components: {database, redis, gts, payments}}` — har birida `status (ok|failing|not_configured)`, `latency_ms?`, `detail?` |
| `GET` | `system/version/` | `{backend, panel}` |
| `GET` | `system/audit/` | Audit jurnali (faqat o'qish) |

- Health'ni dashboard'da kartochkalar qilib ko'rsating; `not_configured`
  bo'lsa `detail` ichida qaysi sahifada sozlash kerakligi yozilgan.
- **Audit**: har bir muvaffaqiyatli admin yozuvi avtomatik jurnalga tushadi
  (kim, qachon, nimani, qanday o'zgartirdi — `changes` diff'i bilan, sirlar
  yashirilgan) + login/logout hodisalari. Filtrlar: `?actor=<uuid>`,
  `?resource=<prefix>` (masalan `settings` hammasini oladi), `?action=`,
  `search` (email/path), sanalar. Buni alohida "Jurnal" sahifasi qiling.

---

## 12. Hali qurilmagan bo'limlar

Bular API.md kontraktida bor, lekin backend'da hali yo'q — chaqirilsa 404.
Panelda menyuda ko'rsatmang yoki "tez orada" deb belgilang:

- **Dashboard/statistika** — hech qanday endpoint yo'q (hozircha bosh sahifa
  sifatida `system/health/` + oxirgi audit yozuvlarini ko'rsatish mumkin).
- **Buyurtmalar** (`admin/orders/*`), **to'lov operatsiyalari va qaytarishlar**
  (`admin/payments/*`) — aviachipta bron oqimi bilan birga keladi.
- **Promokodlar**, **obunalar**, **bildirishnoma shablonlari/broadcast**,
  **hisobotlar/eksport**.
- **Mijozlar ro'yxati/kartochkasi** — hozircha faqat
  `customers/deletion-reasons/` lug'ati bor (CRUD, tarjimali `text` +
  `sort_order` — kichik sozlama sahifasi).
- **Blog, aksiyalar, bannerlar, ommabop yo'nalishlar, sharhlar** — bayroqlari
  bor, admin CRUD'lari hali yo'q.
- **GTS va to'lov provayderining "test" tugmasi** — faqat SMTP'da test bor;
  GTS/payments test endpointlari keyin qo'shiladi.
- **Staff unblock** — yuqoridagi ogohlantirishga qarang.

---

## 13. Panel skeleti — tavsiya etiladigan sahifalar

```
/login                          — kirish (+ parol tiklash oqimi)
/                               — bosh sahifa: health kartalari + oxirgi audit
/settings/branding              — logo, ranglar, shrift (jonli preview)
/settings/site                  — nom, domen, kontaktlar, social
/settings/localization          — tillar + valyutalar (bitta sahifada mumkin)
/settings/features              — bayroq switch'lari (owner'da yozish)
/settings/products              — vertikallar (faqat ko'rish)
/integrations/gts               — credential ro'yxati + aktivlashtirish
/integrations/smtp              — singleton forma + test tugmasi
/integrations/payments          — payme/click kartochkalari
/integrations/social            — google
/content/faq                    — ro'yxat + tartiblash + editor
/content/pages/:slug            — privacy-policy | terms | about (markdown editor, 3 til)
/leads                          — ro'yxat + status boshqaruvi
/leads/topics                   — mavzular lug'ati
/leads/support                  — support kontakt
/customers/deletion-reasons     — lug'at
/staff                          — jamoa (faqat owner ko'radi)
/audit                          — jurnal
/profile                        — o'z ma'lumotlari + parol o'zgartirish
```

Texnik tavsiyalar:

- **API client**: bitta axios/fetch qatlam — envelope'ni yechadi, 401'da
  bitta navbatlashgan refresh, 429'da `Retry-After`, xatolarni `{code,
  field, message}` shaklida formaga uzatadi.
- **Token saqlash**: access — xotirada; refresh — httpOnly cookie imkoni
  yo'q (token JSON'da keladi), shuning uchun refresh'ni ehtiyotkorlik bilan
  saqlang va logout'da bekor qiling.
- **Rol guard'lari**: `auth/me/` dagi `role` bo'yicha route va tugmalarni
  yashirish; backend baribir tekshiradi.
- **Maskalangan sirlar**: `••••••••` ko'rinishidagi qiymatni hech qachon
  qayta yubormang; parol maydonlari doim bo'sh boshlanadi.
- **Tarjimali maydonlar**: uz/ru/en tablari; saqlashda faqat to'ldirilgan
  tillarni yuborish kifoya (merge).
- Swagger (`/api/v1/docs`) va Postman kolleksiyasi
  (`postman/b2c-admin.postman_collection.json`) — jonli ma'lumotnoma.
