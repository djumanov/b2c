# Fazalar — ijro rejasi

**Oxirgi yangilanish:** 2026-08-06

[PROJECT.md](PROJECT.md) §15 yettita bosqichni bittadan qator bilan belgilaydi.
Bu **qamrov** uchun yetarli, **ijro** uchun emas: [API.md](API.md) da 24 ta
endpoint bo'limi bor va ularning qaysi biri qaysi bosqichda ekani hech qayerda
yozilmagan. Fazasi yo'q bo'lim yo unutiladi, yo noto'g'ri fazada shoshib
quriladi — ikkalasi ham [PROJECT.md](PROJECT.md) §17 dagi "spekulyativ" va
"tarqalib ketish" xavflarining amaliy ko'rinishi.

Shu hujjat o'sha bo'shliqni to'ldiradi: **`API.md` bo'limlarini fazalarga
to'liq va bir martadan taqsimlaydi**, so'ng har bir fazani bir xil skelet bilan
tasvirlaydi.

---

## 0. Bu hujjat nima emas

**Avtoritet emas.** [STATUS.md](STATUS.md) bilan bir darajada turadi.

| Hujjat | Daraja |
|---|---|
| [API.md](API.md) | Kontrakt — tashqaridan nima ko'rinishi |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Ichki tuzilma |
| [PROJECT.md](PROJECT.md) | Mahsulot, qamrov va **bosqichlar** |
| **PHASES.md** (shu hujjat) | Bosqichlarning **ijro rejasi** — avtoritet emas |
| [STATUS.md](STATUS.md) | Hozir qayerdamiz — avtoritet emas |

Uchta qat'iy qoida:

1. **Qamrov qo'shmaydi.** [PROJECT.md](PROJECT.md) §3 chiqarib tashlagan narsa
   bu yerda hech qanday fazada paydo bo'la olmaydi.
2. **Kontraktga zid bo'lmaydi.** Bu hujjat `API.md` dagi bo'limlarni faqat
   **taqsimlaydi**; endpoint qo'shmaydi, o'chirmaydi, shaklini o'zgartirmaydi.
3. **Faza chegarasini ko'chira olmaydi.** [PROJECT.md](PROJECT.md) §15 qo'ygan
   chegara o'zgarishi kerak bo'lsa — **avval `PROJECT.md` tahrirlanadi**, keyin
   bu hujjat unga ergashadi. Ziddiyat bo'lsa `PROJECT.md` §15 ustun turadi.

Shu sababli §1 dagi xaritaning har bir qatori **manbasini ko'rsatadi**.

> **Ikki hujjat, ikki savol.** `STATUS.md` — "hozir nima qurilgan", PHASES.md —
> "nima qurilishi kerak va qaysi tartibda". Biror ish bajarilganda `STATUS.md`
> yangilanadi; bu hujjat faqat reja o'zgarganda.

---

## 1. `API.md` bo'limlari → fazalar

Konvensiya bo'limlari (§1–§16) fazaga tegishli emas — ular kesishuvchi
infratuzilma. Ikkitasi bundan mustasno va ular alohida ko'rsatilgan.

**Holat:** ✅ qurilgan · ◐ qisman · — hali yo'q.

### II qism — public yuza

| § | Bo'lim | Faza | Holat | Izoh va manba |
|---|---|---|---|---|
| 17 | Sayt konfiguratsiyasi | **1** | ◐ | Yo'l va kesh bor; `payment_methods` `integrations` bilan to'ladi |
| 18 | Autentifikatsiya | **1** | ✅ | `PROJECT.md` §15: "auth (customer + staff)". Email+parol 6a da, Google 5c da. Chetlanishlar: `devices/` → **6**, `social/apple/` → **6**, telefon+SMS → §41 |
| 19 | Profil | **1** | ◐ | `profile/` (avatar kodi shu yerda), `password/`, `passengers/` — 6b-bo'lakda qurildi. `cards/` — oddiy CRUD sifatida qurildi (§2.7 qayta ko'rildi). Chetlanish: `notifications/` → **4** (§2.8) |
| 20 | Mahsulotlar | **2** + **3** | — | `flight` → 2-faza; `railway`, `insurance`, `esim`, `transfer` → 3-faza (`PROJECT.md` §15) |
| 21 | Buyurtmalar | **2** | ◐ | `ARCHITECTURE.md` §15: `orders` 2-fazada. Ro'yxat va tafsilot 6-bo'lakda qurildi; `receipt/`, `{id}/cancel/`, kanonik status va `available_actions` → **9-bo'lak** |
| 22 | To'lov | **2** | — | **Karta oqimi** — `transactions/`, `card/`, `confirm/`, `resend-otp/`: hammasi shu fazada (§2.7). Redirect oqimi **umuman qurilmaydi** (`O14`). Faqat `installment/…` → §41 |
| 23 | Promokod | **2** | — | Shu hujjatdagi qaror (§2.3), `PROJECT.md` §15 ga yozib qo'yilgan |
| 24 | Kontent (o'qish) | **4** | ◐ | `cms` moduli bilan birga; 5-faza uni **iste'mol qiladi**, qayta qurmaydi. `faq/` va statik sahifalar (`privacy-policy/`, `terms/`, `about/`) oldinga tortildi (§2.14) |
| 25 | Murojaat va aloqa | **4** | ◐ | `leads` va `feedback` modullari bilan (§2.5). `leads` soddalashib oldinga tortildi (§2.14) |
| 26 | Kataloglar | **1** + **2** + **3** | ◐ | `document-types`, `countries` → 1 (proxy, §2.6); `places`, `airlines`, `currencies` → 2; `stations/` poyezd bilan → 3 |

### III qism — admin yuza

| § | Bo'lim | Faza | Holat | Izoh va manba |
|---|---|---|---|---|
| 27 | Autentifikatsiya | **1** | ✅ | 7/7 |
| 28 | Sayt sozlamalari | **1** | ✅ | 7/7. `settings/menu/` → **5** (§41) |
| 29 | Integratsiyalar | **1** + **2** | ◐ | `PROJECT.md` §15 1-faza: "sozlamalar + shifrlangan credential'lar" (§2.1). GTS, SMTP, to'lov va social sozlamalari 1-fazada. Chetlanishlar: `gts/test/` va `payments/{code}/test/` → **2** (§2.13). SMS/push qismi → §41 |
| 30 | Kontent | **4** | ◐ | `PROJECT.md` §15 4-faza. `faq/` va `pages/` oldinga tortildi (§2.14) |
| 31 | Buyurtmalar | **2** | — | `orders/{id}/push/` → **6** (§41) |
| 32 | To'lovlar | **2** | — | `refund/` — `PROJECT.md` §16 3-savoli 2-faza oxirida kerak |
| 33 | Promokodlar | **4** | — | CRUD, statistika, panel. Minimal model 2-fazada (§2.3) |
| 34 | Mijozlar | **4** | — | `PROJECT.md` §15 4-faza |
| 35 | Murojaatlar | **4** | ◐ | `leads/` oldinga tortildi (§2.14); `subscriptions/export/` → **7** (eksport mexanizmi bilan) |
| 36 | Bildirishnomalar | **4** + **7** | — | Shablon CRUD va tarix → 4; `broadcast/` **ijrosi** → 7 (§2.9) |
| 37 | Hisobotlar | **4** + **7** | — | `dashboard`, `sales`, `fields`, `views` → 4; `export/` → 7 (§2.9) |
| 38 | Jamoa | **1** | ✅ | 4/4, hammasi `owner` |
| 39 | Tizim | **1** + **2** | ◐ | `health`, `version`, `audit`, `uploads` → 1 (qurilgan); `jobs/{id}/` → **2** (§2.10) |

### IV qism — webhook'lar

| § | Bo'lim | Faza | Holat | Izoh |
|---|---|---|---|---|
| 40 | To'lov provayderi callback'lari | **2** | — | Router ulangan, lekin bo'sh. Idempotentlik 2-fazaning qabul mezoni |

### Fazaga bog'liq ikkita konvensiya bo'limi

| § | Bo'lim | Faza | Nega |
|---|---|---|---|
| 9 | Uzoq davom etadigan amallar | **2** | `jobs` moduli — birinchi async ish saga bilan keladi |
| 12 | GTS bilan aloqa | **2** | Timeout, retry va xato xaritasi GTS klienti bilan |

Qolgan konvensiyalar (§1–8, §10, §11, §13–§16) 1-fazada qurilgan; §4 va §5
customer tomoni uchun 1-fazada yakunlanadi (`current_customer` hozir qatorni
yuklamaydi — [STATUS.md](STATUS.md) §3).

---

## 2. Hal qilingan chegaralar

Quyidagilar `PROJECT.md` §15 da aniq belgilanmagan edi. Har biri uchun qaror va
uning asosi. **Bular qamrovni o'zgartirmaydi** — faqat mavjud ishni fazalarga
taqsimlaydi.

**2.1 · §29 Integratsiyalar → 1-faza.** `PROJECT.md` §15 ning 4-faza qatorida
"integratsiyalar" bor, lekin o'sha qator **panel ekranlari** haqida; 1-faza
qatorida esa "sozlamalar + shifrlangan credential'lar" allaqachon yozilgan.
Ziddiyat yo'q edi, faqat aytilmagan edi — endi `PROJECT.md` §15 da aniqlashtirildi.
Amaliy sabab: **GTS credential'lari shusiz saqlanmaydi, ya'ni 2-faza boshlanmaydi.**

**2.2 · §18–§19 Customer auth va profil → 1-faza.** `PROJECT.md` §15 1-faza
natijasi: "auth (customer + staff)". `aud: public` tokeni 1-fazaning bir qismi.

**2.3 · §23 Public promokod → 2-faza (minimal), §33 → 4-faza.** `promo/apply/`
**to'lov summasini o'zgartiradi**. Uni keyin qo'shish saga va idempotentlikka
tegadi — ya'ni 3-fazaning "oqim va saga kodi o'zgarmagan" qabul mezonini
buzadi. Shuning uchun 2-fazada `promo` modulining to'lovga yetadigan minimal
qismi quriladi: kod, chegirma qoidasi, `apply/`, `discard/`, ishlatilish yozuvi.
CRUD, faollashtirish, statistika va panel — 4-fazada.
Chegirma **client marjasidan** ketadi, GTS to'liq tarifni oladi
([ARCHITECTURE.md](ARCHITECTURE.md) §14 A4).

**2.4 · §22 `installment/…` → relizga kirmaydi.** `PROJECT.md` D7 relizni Payme
va Click bilan cheklaydi, ikkalasi ham bo'lib to'lash bermaydi, provayder esa
umuman nomlanmagan. `API.md` §41 ga ko'chirildi — endpoint kontraktda qoladi va
`404 not_found` qaytaradi.

**2.5 · §25 Public murojaat → 4-faza.** ~~`POST /public/leads/` kelgan `fields`
ni `admin/leads/sources/` dagi sxema bo'yicha tekshiradi, ya'ni admin tomoni
public tomonidan oldin kerak.~~ Manbalar mashinasi kontraktdan chiqarildi va
`leads` oldinga tortildi — §2.14. 5-fazada bu yuza faqat **iste'mol qilinadi**.

**2.6 · §26 Kataloglar bo'linadi.** `places`, `airlines`, `currencies`
aviachipta qidiruv formasi uchun kerak → 2-faza. `stations/` faqat `railway`
uchun ma'noga ega → 3-faza. Valyuta kurslarini yangilaydigan beat vazifasi ham
2-fazada.

`document-types/` va `countries/` esa **1-fazaga tushdi**. Sabab: §19 yo'lovchi
formasi 1-fazada qurildi, `document_type` va `citizenship` ni klient tanlashi
kerak, va bu ikki ro'yxatni beradigan GTS `/static/` servisi **auth talab
qilmaydi** — ya'ni 2-fazaning 1-bo'lagini (sessiya menejeri) kutmaydi.

Bu faza chegarasini surgani uchun nima qilingani ochiq yozilsin:

- `providers/gts/static.py` — 2.1-bo'lak qo'shadigan `client.py` dan **alohida
  fayl**. 2.1 sof qo'shimcha bo'lib qoladi va bu faylga tegmaydi.
- Beat vazifasi ham, `catalog` jadvali ham, migratsiya ham **yo'q**. 2/3-faza
  Redis TTL sini rejalashtirilgan yangilanishga almashtiradi; router va service
  saqlanadi ([ARCHITECTURE.md](ARCHITECTURE.md) §5).
- Retry **yo'q** — API.md §12 dagi ikki takror `client.py` ga tegishli, oldida
  24 soatlik kesh turibdi.
- `integrations` ga bitta qo'shimcha: `service.gts_base_url()`. 2-fazaning
  "`integrations` ga tegmaydi" mezoni buzilmaydi — unga tegayotgani **shu**
  o'zgarish, 2-faza emas.
- `airlines/`, `currencies/`, `places/`, `stations/` tegilmadi va o'z fazasida
  qoladi.
- `airports/` ham oldinga tortildi (2026-08-12): aviachipta qidiruv formasiga
  aeroport avtoto'ldirish kerak, GTS `/static/airports/{search}` esa xuddi
  shu auth'siz servisda. Farqi — **keshsiz** jonli proxy: erkin matnli `q`
  chegarasiz kesh kalitini keltirib chiqarardi (API.md §26).

**2.7 · §19 `profile/cards/` — qayta ko'rildi (2026-08-11): endi 1-fazada.**
Dastlab bo'lim 2-fazaga surilgan edi, chunki karta provayderning karta-token API'si
orqali yaratilar edi. Bu qaror bekor qilindi: karta — **lokal autofill yozuvi**,
provayder saqlashda qatnashmaydi (`PROJECT.md` D7). `API.md` §19 endi faqat oddiy
CRUD: list/qo'shish/ko'rish/o'chirish; `verify/`, `resend-otp/` va `default/`
kontraktdan chiqdi. Raqam bazada AES-GCM shifrlangan holda turadi va 2-fazada
to'lov uni `reveal_card()` orqali oladi.

**Yana bir marta aniqlashtirildi (2026-08-19).** `transactions/{id}/card|confirm|
resend-otp/` — bu **to'lovning o'zi**, kartani saqlash emas, va u 2-fazada
quriladi (`API.md` §22, `O14`). Yuqoridagi §22 qatori ularni §41 ga yuborar edi:
o'sha ziddiyat shu bilan yopildi. Hosted redirect esa 2-fazaga ham qolmadi —
u qurilmaydi.

⚠ **PCI majburiyatini kim oladi** savoli ochiq (`PROJECT.md` §16.7) — endi u
saqlash nazoratini (PCI 3-talab) ham qamraydi, chunki shifrlangan raqam saqlanadi.

**2.8 · §19 `profile/notifications/` → 4-faza.** Ichki bildirishnomalarni
`notifications` moduli yozadi. 1-fazada yozadigan hech narsa yo'q.

**2.9 · §36 va §37 bo'linadi.** `ARCHITECTURE.md` §15: 7-faza — "hisobot
eksporti, ommaviy yuborish". Ya'ni shablon CRUD, `broadcasts/` tarixi,
`dashboard`, `sales`, `fields`, `views` → 4-faza; `broadcast/` **ijrosi** va
`reports/export/` → 7-faza. Ikkalasi ham `jobs` orqali ishlaydi.

**2.10 · §39 — bitta bo'lim, ikki faza.** `health`, `version`, `audit`,
`uploads` 1-fazada qurilgan; `GET /admin/jobs/{id}/` esa `jobs` moduli bilan
2-fazada. **§39 ni yaxlit iqtibos qilish xato** — har doim yo'l darajasida
ayting.

**2.11 · §18 `social/{provider}/` → 1-fazaning 5c-bo'lagi.** Faza o'zgarmaydi,
bo'lak o'zgaradi. Endpoint 1-faza qamrovida, lekin uni **sozlaydigan joy yo'q**
edi: §29 da Google `client_id`/`client_secret` ini saqlaydigan resurs yo'q edi.
Ikki yo'l bor edi — `customers` bo'lagining ichiga integratsiya shaklidagi
resurs qo'shish, yoki endpointni qolgan integratsiyalar bilan birga qurish.
Ikkinchisi tanlandi: credential §29 ning o'z jadvaliga qo'shiladi, oqim esa
`customers` modulida qoladi. Shunda kontrakt bir joyda, bir marta kengayadi.

O'z bo'lagi bor, chunki to'lov sozlamalari bilan hech narsani baham ko'rmaydi:
5c ga na GTS, na to'lov adapteri kerak.

**2.12 · §19 — `email` ni almashtirish qamrovda yo'q.** Bu qamrovni
qisqartirish emas: `PATCH /public/profile/` "shaxsiy ma'lumot" deydi va qaysi
maydonlar ekanini aytmaydi, ya'ni `email` u yerda hech qachon nomlanmagan.
Manzil — kirish identifikatori: OTP aynan shuni tasdiqlagan va parol tiklash
shunga ishonadi. Uni almashtirish yangi manzilga kod yuborib tasdiqlaydigan
oqimni talab qiladi, bunday oqim esa kontraktda **yo'q**.

Shuning uchun `PATCH` oltita maydonni oladi va `email` yuborilsa `422`
qaytaradi ([API.md](API.md) §19). Kerak bo'lsa avval §19 ga oqim yoziladi,
keyin kod — [PROJECT.md](PROJECT.md) §17 dagi "spekulyativ" xavfning aynan
o'zi shu tartibni talab qiladi.

**2.13 · §29 dagi ikkita `test/` → 2-faza.** `gts/test/` va
`payments/{code}/test/` sinov emas, **haqiqiy ulanish** demakdir — tugmaning
butun ma'nosi shu. Ulanishni esa adapter biladi, va
[PROJECT.md](PROJECT.md) §15 ikkalasini ham 2-fazaga qo'ygan: GTS klienti
1-bo'lakda, Payme va Click adapterlari 7-bo'lakda.

Bugungi kodda ular yo'q: `providers/gts/` faqat protokollar, `PaymentProvider`
portida esa tekshirish uchun chaqiriladigan metod umuman yo'q — undagi har bir
metod haqiqiy to'lovni boshlaydi. Shu holatda yozilgan `test/` "sozlangan" deb
aytardi, "yetib boradi" deb emas.

Shuning uchun 1-faza **sozlama va credential'ni** saqlaydi va tashqariga chiqish
uchun **seam** qoldiradi — 5-bo'lak GTS uchun (`service.active_credential`) va
5a pochta uchun (`service.notifier`) aynan shunday qilgan. Ikkala `test/` esa
o'z adapteri bilan birga keladi va shu paytgacha `404` qaytaradi.

`notifications/test/` bundan mustasno va ishlaydi: uning adapteri 5a da qurilgan.

**2.14 · FAQ, sahifalar va murojaatlar oldinga tortildi.** Customer ilovasiga
FAQ, maxfiylik siyosati, foydalanish shartlari, "ilova haqida" sahifasi va sodda
qo'llab-quvvatlash hozir kerak bo'ldi. Uchta o'zgarish:

- `cms` FAQ qismi (§24/§30) va soddalashgan `leads` (§25/§35) 4-fazadan oldinga
  tortildi — 2.6 dagi kabi bu **taqsimot**, qamrov o'zgarishi emas
  ([PROJECT.md](PROJECT.md) §15 tegilmadi).
- `pages/` §41 dan chiqdi: [PROJECT.md](PROJECT.md) §16 1-savolning **sahifa
  yarmi yechildi** — tana har til bo'yicha markdown (`API.md` §30 "Sahifa
  tanasi"). Menyu yarmi ochiq, `settings/menu/` §41 da qoladi.
- Leads'dagi manbalar mashinasi (`sources/` + dinamik `fields` sxemasi)
  kontraktdan olib tashlandi — murojaat qat'iy `topic + message + contact`
  ([ARCHITECTURE.md](ARCHITECTURE.md) §14 G1). Bu bilan 6-bo'limdagi "fields
  sxemasi formati" to'suvchi savoli ham o'z-o'zidan yopildi.

---

## 3. Faza 1 — Yadro

**Maqsad.** Panel ishga tushadi, sozlama DB'da va deploysiz o'zgaradi, ikkala
token sub'ekti ishlaydi.

**Qamrov.** `API.md` §17, §18, §19 (chetlanishlarsiz — §1 xaritasiga qarang),
§27, §28, §29, §38, §39 (`jobs/` dan tashqari).
Modullar: `settings`, `staff`, `audit`, `uploads`, `system`, `integrations`,
`customers`.

**KIRMAYDI**

| Nima | Qayerda |
|---|---|
| Panel ekranlari (integratsiyalar, sozlamalar) | 4-faza |
| `transactions/{id}/card\|confirm\|resend-otp/` | 2-faza (§2.7) |
| `profile/notifications/` | 4-faza (§2.8) |
| `settings/menu/` | 5-faza (§41) |
| Telefon + SMS OTP, `devices/`, `social/apple/` | §41 |

**Bog'liqliklar.** Yo'q — bu birinchi faza.

**Bo'laklar** — qurilgani [STATUS.md](STATUS.md) §2 da, qolgani quyida ⬜ bilan:

| # | Bo'lak | Holat |
|---|---|---|
| 1 | Poydevor: `core/`, `api/`, `db/`, `providers/` portlari, Celery, docker | ✅ |
| 2 | `staff` — admin auth, rotatsiyali refresh, jamoa, birinchi owner | ✅ |
| 3 | `audit` va `uploads` | ✅ |
| 4 | `settings` + `site-config` | ✅ |
| 5 | **`integrations`** — GTS credential'lari (§29) | ✅ |
| 5a | **`integrations`** — SMTP (§29) | ✅ |
| 5b | **`integrations`** — to'lov provayderlari sozlamasi (§29) | ⬜ |
| 5c | **Social kirish** — §29 credential'i + `social/{provider}/` oqimi | ⬜ |
| 5c | **Bo'lim bayroqlari** — `RequireFeature`, o'n bitta bayroq, sweep testi (§28) | ✅ |
| 6a | **`customers`** — auth (§18) | ⬜ |
| 6b | **`customers`** — profil (§19) | ⬜ |
| 7 | e2e qabul testi | ⬜ |

**5-bo'lak — `integrations`.** 2-fazani to'sib turgan qismi — **GTS
credential'lari**; u shu bo'lakda bajariladi, qolgani 5a ga suriladi.

- Jadval: `gts_credentials` — bir nechta qator, bittasi `is_active`, sir
  shifrlangan qiymat + **`key_version`** ([ARCHITECTURE.md](ARCHITECTURE.md)
  §10). "Aynan bittasi" partial unique index bilan kafolatlanadi.
- `app/core/crypto.py` shu yerda **birinchi marta** ishlatiladi; hozircha faqat
  testlari bor. Sirlar maskalanib qaytadi, hech qachon to'liq emas.
- Rollar: `GET` → `admin`, o'zgartiruvchi hamma narsa → **`owner`** (`API.md` §29).
- 2-fazaga qoldiriladigan seam **ikkita**: `ActiveGtsCredential` dataclass va
  `service.active_credential(session)`. Qabul mezoni — 2-faza
  `providers/gts/client.py` va `session.py` ni qo'shadi, `integrations` ga
  tegmaydi.

**5a-bo'lak — SMTP.** Qolgan integratsiyalardan **faqat shu** oldinga
olindi, chunki **6-bo'lakni to'sib turgan yagona narsa shu**: email OTP
SMTP'siz ishlamaydi (D6). To'lov provayderlari hech narsani to'smaydi.

- Jadval: `smtp_settings` — **singleton**, parol shifrlangan
  ([ARCHITECTURE.md](ARCHITECTURE.md) §10). Qator birinchi o'qishda yaratiladi.
- `providers/notifications/smtp.py` — `Notifier` portining SMTP adapteri,
  stdlib `smtplib` `asyncio.to_thread` ichida.
- Seam yo'nalishi tuzatildi: qaysi notifier ishlatilishini **modul** hal
  qiladi (`integrations.service.notifier(session)`), provayder emas —
  argumentsiz modul-global ko'p worker'da noto'g'ri va u sozlamani qayta
  o'qiy olmasdi. `set_notifier` override'i o'z nomi bilan qoladi.
- `test/` haqiqiy xabar yuboradi; relay rad etsa bu `502` emas, `200` +
  `ok: false` va sabab.

**5b-bo'lak — to'lov provayderlari.** 2-fazani to'smaydi.

- `payment_providers` jadvali: har bir kod uchun bitta qator, birinchi o'qishda
  yaratiladi. Credential'lar bitta shifrlangan JSON obyekt sifatida saqlanadi —
  qaysi kalitlar kerakligini adapter biladi, u esa 2-fazada keladi.
- Seam: `integrations.service.payment_providers(session)`. Qabul mezoni
  5-bo'lakdagi bilan bir xil — 2-faza `providers/payments/{payme,click}.py`
  qo'shadi, `integrations` ga tegmaydi.
- `test/` bu yerda emas — §2.13.
- Ergashuvchi ikkita ish: `system/health/` dagi qattiq `NOT_CONFIGURED`
  haqiqiy holatga almashadi (`app/modules/system/service.py`, **tarmoqqa
  chiqmasdan**), `site-config` ning bo'sh `payment_methods` to'ladi
  (`settings/service.py::_assemble`).
- `payment_logo` — yangi upload purpose. `logo` ni qayta ishlatish brend
  logosini to'lov usuliga ulash imkonini berardi, `uploads.service.link` esa
  aynan shuni to'sish uchun `purpose` oladi.

**5c-bo'lak — social kirish.** `PROJECT.md` D5: email+parol ustiga Google.
Adapter ham, GTS ham kerak emas, shuning uchun 5b dan mustaqil.

- §29 da `social_credentials` — provayder bo'yicha registry, `apple` keyin
  yangi qator bo'lishi uchun ([ARCHITECTURE.md](ARCHITECTURE.md) D5).
- Yangi port: `providers/social/` — `SocialVerifier` va `google.py`.
  `__init__.py` da faqat override qoladi, qaysi verifier ishlatilishini
  **modul** hal qiladi (`integrations.service.social_verifier`) — 5a dagi
  `notifier` bilan bir xil sabab (§4 dagi 25-qaror).
- Oqim `customers` da qoladi (§2.11): manzil bo'yicha topiladi yoki
  yaratiladi, va **tasdiqlangan** bo'ladi.

**6-bo'lak — `customers`.** SMTP tayyor, ya'ni email OTP yo'li ochiq. Bo'lak
ikkiga bo'lindi: kontraktdagi ikki bo'lim (§18 va §19) mustaqil o'qiladi va
ikkinchisi birinchisining jadvali ustiga quriladi.

**6a — auth (§18).**

- Jadvallar: `customers`, `customer_refresh_tokens`, `email_otps`.
- Auth `staff` naqshini takrorlaydi, `Audience.PUBLIC` bilan — jumladan
  rotatsiya, qayta ishlatishni aniqlash va `revoked_before` belgisi
  ([STATUS.md](STATUS.md) §4.17).
- `api/deps.py::current_customer` **qatorni yuklaydigan** bo'ladi —
  `current_staff` kabi ([STATUS.md](STATUS.md) §4.3).
- `social/{provider}/` bu yerda emas — §2.11.

**6b — profil (§19).** `passengers` jadvali va `customers.avatar_id`; `profile/`,
`password/`, akkauntni o'chirish. (Avatar dastlab yuklanadigan fayl edi —
`avatar/` endpointlari va `avatar` purpose'i bilan; 2026-08-10 da u klient
tanlaydigan kodga aylandi va ikkalasi ham olib tashlandi, STATUS.md §4.41.
Quyidagi ikki xatboshi o'sha eski dizaynni tasvirlaydi.) `avatar/` uchun mavjud
`uploads.service` ishlatilardi — yangi `avatar` purpose, `public`, chunki private yo'lni
`current_staff` qo'riqlaydi.

- Yo'lovchi maydonlari [PROJECT.md](PROJECT.md) §13 dagi ro'yxatdan olinadi va
  undan oshmaydi; `document_type` cheklanmagan satr bo'lib qoladi. Ro'yxatning
  o'zi endi §26 `document-types/` dan olinadi (§2.6), lekin ustunni cheklash —
  boshqa qaror: lokal enum GTS ro'yxati o'zgarganda unga zid bo'lib chiqardi.
  (2026-08-11 da satr §26 katalogidan tanlangan **to'liq JSONB obyektga**
  aylandi — `citizenship` ham; tekshiruv faqat `"code"`/`"type"` kaliti,
  STATUS.md §4.75. Enum/CHECK haqidagi xulosa kuchda qoladi.)
- `email` `PATCH` orqali o'zgarmaydi — yangi manzilni tasdiqlaydigan oqim
  kontraktda yo'q (§2.12).
- `cards/` — oddiy CRUD sifatida qurildi (§2.7 qayta ko'rildi); `notifications/` →
  4-faza (§2.8).

**Qabul mezoni** ([PROJECT.md](PROJECT.md) §15):

> **9-bo'lak (`booking` sagasi) qayta loyihalandi va 11 ta bo'lakka bo'lindi** —
> [`order-system/04-plan.md`](order-system/04-plan.md). Modul nomi ham o'zgardi:
> saga `orders` moduliga joylashadi.

| Mezon | Qayerda tekshiriladi |
|---|---|
| Panelga `owner` sifatida kirish mumkin | `tests/integration/test_staff_auth.py` ✅ |
| Brend rangi o'zgarsa `site-config` da deploysiz aks etadi | `tests/integration/test_site_config.py` ✅ |
| `admin` tokeni `owner` endpointida `403` oladi | `tests/integration/test_staff_crud.py` ✅ |
| Uchalasi ham **toza baza ustida, uchidan-uchiga** | `tests/e2e/test_phase1_acceptance.py` ⬜ |

**To'suvchi ochiq savollar.** Yo'q.

---

## 4. Faza 2 — GTS ulanishi va aviachipta

**Maqsad.** Aviachipta bo'yicha qidiruv → bron → to'lov → chipta uchidan-uchiga
ishlaydi, va pul jimgina yo'qolmaydi.

**Qamrov.** `API.md` §20 (`flight`), §21, §22, §23, §26 (`stations/` dan
tashqari), §31, §32, §39 `jobs/{id}/`, §40;
konvensiyalardan §9, §12 va §14 dagi `payment` chegarasi.
Modullar: `providers/gts/`, `catalog`, `products`, `booking`, `orders`,
`payments`, `promo` (minimal), `jobs`, `providers/payments/{payme,click}`.

**KIRMAYDI**

| Nima | Qayerda |
|---|---|
| `railway`, `insurance`, `esim`, `transfer` | 3-faza |
| `catalog/stations/` | 3-faza (§2.6) |
| Promokod CRUD, statistika, panel | 4-faza (§2.3) |
| `orders/{id}/push/` | 6-faza (§41) |
| `installment/…` | §41 |

**Bog'liqliklar.**

- 1-fazaning 5-bo'lagi (`integrations`) — GTS credential'i shu yerda saqlanadi.
- ⚠ **GTS mashina akkaunti uchun ikki bosqichli tasdiq o'chirilishi kerak**
  (`PROJECT.md` D1). Bu **yechimi bizda bo'lmagan yagona to'siq** — GTS jamoasi
  bilan oldindan hal qilinsin.

**Bo'laklar:**

| # | Bo'lak | Izoh |
|---|---|---|
| 1 | `providers/gts/` — klient, sessiya menejeri, ACL — **✅ 2026-08-12 (qisman)** | Sessiya Redis'da, **qulf ostida**; 401/403 da bitta avtomatik takror ([ARCHITECTURE.md](ARCHITECTURE.md) §7). `client.py` qurildi; `POST /admin/integrations/gts/test/` probe'i **hali qolgan** (§2.13). Sessiya cookie nomi (`sessionid`) — jonli GTS'da tekshiriladigan taxmin (STATUS.md §3) |
| 2 | Xato va status xaritalari — **✅ xato xaritasi 2026-08-12** | GTS xatoda ham HTTP 200 qaytaradi; asl matn `message` da (ro'yxat shaklidagi `message` ham), asl kod `meta.upstream` da (`API.md` §3). **Status xaritasi** (BO/PW/TI→kanonik) 6-bo'lakdan **9-bo'lakka ko'chdi** (2026-08-17): buyurtma qatori qurildi, lekin unda GTS kodi kelganicha turadi |
| 3 | `catalog` + beat sinxronizatsiyasi | Uzoq Redis TTL, ikkala yuzaga faqat o'qish |
| 4 | `ProductAdapter` porti + `flight` adapteri — **✅ 2026-08-14 (butun oqim)** | Port loyihalandi (flow metodlariga `GtsClient` per-call uzatiladi), `flight` adapteri passthrough sifatida qurildi ([ARCHITECTURE.md](ARCHITECTURE.md) §13.8). `upsell` va `verify` 2026-08-13 da, `book` va `cancel` 2026-08-14 da qo'shildi — **hammasi sof passthrough**. `book` sagani **kutmadi** (STATUS.md №77): saga 9-bo'lakda shu chaqiruv ustiga quriladi |
| 5 | `products` — holatsiz oqim — **✅ 2026-08-14 (oltita qadam)** | **Hech narsa saqlanmaydi** (D2); `tests/contract/test_search_passthrough.py` qo'riqlaydi — supurgi endi `booking` va `cancel` ni ham o'z ichiga oladi. Generik `/{product}/` router, gate `product_settings` orqali, `search` limiti 30/daq; `booking/` va `cancel/` da auth majburiy (D4) va gate token'dan **oldin** ishlaydi |
| 6 | `orders` — lokal yozuv — **✅ 2026-08-17 (yozuv va egalik)** | `orders` jadvali, `booking/` dan keyin GTS javobi `JSONB` bo'lib saqlanadi, `GET /public/orders/` va `/{id}/`, `cancel/` da egalik tekshiruvi (§8.14 yopildi). **Status xaritasi va `available_actions` bu bo'lakdan chiqarildi va 9-bo'lakka ko'chdi**: bugun qatorda GTS kodi kelganicha turadi ([API.md](API.md) §21). Sabab — xaritaning birinchi haqiqiy iste'molchisi bekor qilish/qaytarish qoidalari, ular esa saga bilan keladi; iste'molchisiz xarita taxmin bo'lardi. Admin yuzasi (§31) ham bu bo'lakka kirmadi |
| 7a | `PaymentProvider` porti + registry | Migratsiyasiz, marshrutsiz. Port **hozir** loyihalanadi: `dict` qaytishlar dataclass'ga, `handle_callback` xom baytlarni oladi, `verify()` qo'shiladi (§2.13) |
| 7b | `payments` yadrosi — jadvallar, o'qish endpointlari, admin ro'yxati | Provayder chaqiruvisiz, nol adapter bilan yashil |
| 7c | **Karta oqimi** — portni kengaytirish, `order_payments` ustunlari, `card/`+`confirm/`+`resend-otp/`, provayder tanlash | Redirect shu bo'lakda **olib tashlanadi** (`O14`, `O15`). Saqlangan karta (`card_id`) shu yerda — `reveal_card()` allaqachon bor va bitta `if` uchun alohida bo'lak arzimaydi. Karta raqami **ochiq matnda** hech bir jadvalga va Redis'ga tushmasligi **regressiya testi** bilan qo'riqlanadi |
| 7d | Payme va Click adapterlari | Bu yerda `POST /admin/integrations/payments/{code}/test/` ham ulanadi (§2.13). ⚠ Pul birligi: Payme **tiyin**, Click **so'm** — ikkalasi ham pinlangan test bilan. ⚠ Payme'ning `account_field` i merchant kabinetidan keladi (`API.md` §29) |
| 7e | Webhook'lar **ikkinchi eshik** sifatida + eskirgan urinishlarni supurish + `payments.reconcile` | Takroriy callback ikki marta yechmaydi; imzo yomon bo'lsa holat o'zgarmaydi (`API.md` §40); so'rov ichida yechilgan urinishga kelgan callback xato **emas** (`O16`) |
| 8 | `promo` minimal (§2.3) | To'lov summasiga ta'sir qiladi, shuning uchun shu yerda |
| 9 | **`booking` sagasi** | Transactional outbox + Celery; eng yuqori xavfli modul. `POST /public/{product}/booking/` **allaqachon ishlaydi** (4/5-bo'lak, sof passthrough) va buyurtma qatorini ham yozadi (6-bo'lak) — bu bo'lak ularni almashtirmaydi, ustiga `payment_id`, outbox va holat mashinasini qo'yadi. Shu yerda **status xaritasi** (BO/PW/TI→kanonik, 6-bo'lakdan ko'chdi), `available_actions` va `409 offer_expired` xaritasi ham qo'shiladi. `cancel` ning egalik tekshiruvi **6-bo'lakda yopildi** (STATUS.md §8.14) |
| 10 | `jobs` + `GET /admin/jobs/{id}/` | Async ish reyestri (`API.md` §9) |
| 11 | Beat: ochiq buyurtmalar sync, idempotency tozalash, valyuta kurslari | Sync ataylab **polling** ([ARCHITECTURE.md](ARCHITECTURE.md) §12) |

**Qabul mezoni** ([PROJECT.md](PROJECT.md) §15):

| Mezon | Qayerda tekshiriladi |
|---|---|
| Qidiruv → bron → to'lov → chipta uchidan-uchiga | `tests/e2e/test_flight_purchase.py` (soxta GTS va soxta provayder bilan) |
| Chipta xatosida avtomatik qaytarish | `tests/integration/test_saga.py` |
| Qaytarish ham xato → `needs_attention` | shu yerda |
| Takroriy webhook ikki marta yechmaydi | `tests/integration/test_webhooks.py` |
| Takliflar hech qayerda saqlanmaydi (D2) | `tests/contract/test_search_passthrough.py` |
| Saqlangan karta bilan to'lov ishlaydi | `tests/integration/test_saved_card_checkout.py` |
| Karta raqami **ochiq matnda** hech bir jadvalda va hech bir logda yo'q | `tests/integration/test_card_pan_never_stored.py` — har jadvalning har matnli ustuni supuriladi |

**To'suvchi ochiq savollar.**

| Savol | Qachon kerak |
|---|---|
| `PROJECT.md` §16.3 — qisman qaytarish siyosati | 9-bo'lak (`refund/`) |
| `ARCHITECTURE.md` §14 A9 — GTS qaysi `sort`/filtrni qo'llaydi | 5-bo'lak; javob kelmasa `API.md` §20 qisqartiriladi |
| **`PROJECT.md` §16.7 — PCI SAQ D majburiyatini kim oladi** | Tijoriy javob hali ochiq; texnik qaror qabul qilingan (shifrlangan raqam saqlanadi, §2.7, va 2026-08-19 dan boshlab to'lovning o'zi ham karta orqali — `O14`). Javob "yo'q" bo'lsa kartalar **ham** to'lov **ham** provayderning o'z formasiga ko'chadi va `O14` bekor qilinadi — ya'ni endi bu javob 7c ni ham qayta yozdiradi, faqat 7e ni emas |

---

## 5. Faza 3 — Qolgan vertikallar

**Maqsad.** To'rtta vertikal qo'shiladi va **oqim kodi o'zgarmaydi** — port
o'zini shu bilan oqlaydi.

**Qamrov.** `API.md` §20 — `railway`, `insurance`, `esim`, `transfer` va
ularning vertikalga xos yo'llari; §26 `catalog/stations/`.

**KIRMAYDI.** Yangi modul yo'q. Yangi endpoint naqshi yo'q. Har bir vertikal —
**bitta adapter fayli va bitta registry yozuvi**.

**Bog'liqliklar.** 2-faza to'liq. Har bir vertikal GTS tomonda alohida sinovni
talab qiladi (`PROJECT.md` §17).

**Bo'laklar:** `railway` (`trains/`, `train-details/` — `offers/` o'rniga) ·
`insurance` (`calculate/`, `upsell/`) · `esim` (`offer/` — `verify/` o'rniga) ·
`transfer` (`offer/`, `recommended-time/`) · `catalog/stations/`.

**Qabul mezoni** ([PROJECT.md](PROJECT.md) §15):

| Mezon | Qayerda tekshiriladi |
|---|---|
| To'rttasi ham to'liq oqimdan o'tadi | `tests/e2e/` — har vertikalga bittadan |
| **Oqim va saga kodiga o'zgarish kiritilmagan** | `tests/contract/test_adapter_port.py` — `products/` va `booking/` da vertikal kodi bo'yicha shoxlanish **yo'qligini** tekshiradi; ustiga `git diff` bilan qo'lda tasdiqlash |

> Oqim kodiga o'zgartirish kiritishga to'g'ri kelsa, demak port noto'g'ri
> loyihalangan ([ARCHITECTURE.md](ARCHITECTURE.md) §6). Bu mezonni "kichik
> istisno" bilan o'tkazib yuborish — fazaning ma'nosini yo'qotadi.

**To'suvchi ochiq savollar.** Yo'q.

---

## 6. Faza 4 — Panel

**Maqsad.** Panelning qolgan yuzasi va uning ortidagi modullar.

**Qamrov.** `API.md` §24, §25, §30, §33, §34, §35 (`subscriptions/export/` dan
tashqari), §36 (CRUD va tarix), §37 (`export/` dan tashqari), §19
`profile/notifications/`.
Modullar: `cms`, `feedback`, `promo` (to'liq), `leads`, `notifications`,
`reports`, `customers` ning admin tomoni.

> **Bu fazadagi har bir router o'z bayrog'i bilan mount qilinadi** —
> `dependencies=[Depends(RequireFeature("blog"))]` va hokazo
> ([API.md](API.md) §28, bayroqlar ro'yxati o'sha yerda). Unutish yo'li yo'q:
> `tests/contract/test_feature_coverage.py` har bir route yo yadro
> ro'yxatida, yo bayroq ostida ekanini talab qiladi. `customers` ning admin
> tomoni — **yadro**, bayroq olmaydi.

**KIRMAYDI**

| Nima | Qayerda |
|---|---|
| Statik sahifalar (`admin` va `public` sirtlarida `content/privacy-policy/`, `terms/`, `about/`) | Oldinga tortilgan — §2.14 |
| `reports/export/`, `subscriptions/export/` | 7-faza (§2.9) |
| `notifications/broadcast/` **ijrosi** | 7-faza (§2.9) |
| SMS va push kanallari | §41 |

**Bog'liqliklar.** 1-faza (`customers` — §34 uchun), 2-faza (`orders`,
`payments` — §37 uchun). 3-faza bilan **parallel** bo'lishi mumkin
([PROJECT.md](PROJECT.md) §15).

**Bo'laklar:**

| # | Bo'lak | Izoh |
|---|---|---|
| 1 | `cms` — 7 ta resurs, publish/unpublish/reorder | Tarjimali maydonlar JSONB; public o'qish alohida "yassilovchi" serializer orqali |
| 2 | §24 public kontent yuzasi | Shu modulning o'qish tomoni — 5-fazada qayta qurilmaydi |
| 3 | `feedback` — moderatsiya + `POST /public/feedbacks/` | `pending → accepted \| rejected` |
| 4 | `leads` — sodda murojaat: `POST /public/leads/` + admin ro'yxat/holat | Manbalar mashinasi yo'q — §2.14. Oldinga tortilib qurildi |
| 5 | `promo` to'liq — CRUD, activate/deactivate, stats, usages | Minimal model 2-fazadan |
| 6 | `customers` admin tomoni | `DELETE` — **`owner`** (`API.md` §5 dagi yagona eskalatsiya) |
| 7 | `notifications` — shablonlar, tarix, `profile/notifications/` | Kanal o'lchovi hozir loyihalanadi, garchi faqat email ketsa ham |
| 8 | `reports` — dashboard, sales, fields, views | Kun bo'yicha guruhlash o'rnatma vaqt mintaqasida (A6) |

**Qabul mezoni** ([PROJECT.md](PROJECT.md) §15):

| Mezon | Qayerda tekshiriladi |
|---|---|
| `admin` da jamoa bo'limi ko'rinmaydi | `GET /admin/auth/me/` dagi `role` — `tests/integration/test_staff_auth.py` ✅ |
| Integratsiya kalitlari va tizim ekranlari `admin` uchun faqat o'qish | `tests/contract/test_auth_surfaces.py` — route jadvali bo'ylab |
| Sirlar maskalangan holda qaytadi | `tests/integration/test_integrations.py` (1-faza, 5-bo'lak) |

**To'suvchi ochiq savollar.**

| Savol | Qachon kerak |
|---|---|
| `PROJECT.md` §16.4 — dashboard ko'rsatkichlari | 8-bo'lak. **`GET /admin/reports/dashboard/` javob tanasi `API.md` da umuman yo'q** — javob kelguncha bo'lak boshlanmaydi |
| `PROJECT.md` §16.2 — buyurtmani paneldan tahrirlash | §31 da tahrirlash endpointi yo'q, ya'ni javob "yo'q"; savol rasman yopilsin |
| ~~`leads/sources/` dagi `fields` sxemasining formati~~ | Yopildi — manbalar mashinasi kontraktdan chiqarildi (§2.14) |

---

## 7. Faza 5 — Sayt

**Maqsad.** Web frontend `site-config` va public API ustida; backend tomonda
yangi ish kam ([ARCHITECTURE.md](ARCHITECTURE.md) §15).

**Qamrov (backend).** `API.md` §28 `settings/menu/` §41 dan chiqadi; §17 ni
yakunlash. `pages/` bu yerdan chiqib ketdi — §2.14 bilan oldinga tortilib qurildi.

**KIRMAYDI.** Public kontent yuzasining qolgani — u 4-fazada qurilgan va bu
yerda faqat **iste'mol qilinadi**.

**Bog'liqliklar.** 4-faza (`cms`).

**Bo'laklar — hali yozilmaydi.**

> ⛔ **To'suvchi savol: `PROJECT.md` §16.1 (menyu yarmi)** — menyu modeli qat'iy
> tuzilmami yoki erkin konstruktor? Sahifa yarmi yechildi: tana har til bo'yicha
> markdown, `pages/` §2.14 bilan qurildi.
>
> Javob kelmaguncha bo'laklar yozilmaydi. Hozir o'ylab topilgan reja model
> aniqlangach baribir qayta yoziladi — bu aynan `PROJECT.md` §17 dagi
> "spekulyativ" xavf. Javob kelganda avval `API.md` §28 va §30 tahrirlanadi
> (kontrakt birinchi), keyin shu bo'lim to'ldiriladi.

**Qabul mezoni** ([PROJECT.md](PROJECT.md) §15): paneldagi rang/logo o'zgarishi
saytda **qayta build'siz** ko'rinadi · tarjima bo'lmagan maydon fallback bilan
ko'rsatiladi. Backend tomoni allaqachon `tests/integration/test_site_config.py`
va i18n fallback testlari bilan qoplangan.

---

## 8. Faza 6 — Mobil ilova

**Maqsad.** Flutter build pipeline va uni qo'llab-quvvatlaydigan backend.

**Qamrov (backend).** `API.md` §18 `auth/devices/` va `social/apple/`; §31
`orders/{id}/push/`; §36 ning push kanali; §19 dagi push bilan bog'liq qism.
Hammasi hozir §41 da.

**KIRMAYDI.** Ilovaning o'zi — bu backend hujjati emas. Brending
`site-config` dan keladi, build vaqtida qotadigan qismi
[PROJECT.md](PROJECT.md) §7 jadvalida.

**Bog'liqliklar.** 4-faza (`notifications`).

**Bo'laklar — hali yozilmaydi.** To'suvchi savol yo'q, lekin push xizmati
(APNs/FCM) tanlanmagan; u tanlangach `API.md` §41 dan chiqariladi va bo'laklar
yoziladi.

> ⚠ **`PROJECT.md` D5:** iOS ilovada Google bo'lsa Apple qoidalari **Sign in
> with Apple** ni majburiy qiladi. Shuning uchun social provayderlar 1-fazada
> **registry sifatida** quriladi — `apple` qo'shilishi yangi provayder yozuvi
> bo'lishi kerak, oqim o'zgarishi emas.

**Qabul mezoni** ([PROJECT.md](PROJECT.md) §15): ikkala store uchun build
tayyor · brending `site-config` dan keladi.

---

## 9. Faza 7 — Yetuklik va topshirish

**Maqsad.** Client hujjat bo'yicha o'z serveriga **mustaqil** o'rnata oladi.

**Qamrov.** `API.md` §37 `reports/export/`, §35 `subscriptions/export/`, §36
`broadcast/` ijrosi; va **o'rnatish / yangilash / zaxira hujjati** —
`PROJECT.md` §14 dagi uchta band, ayniqsa **shifrlash kalitini alohida
saqlash** ogohlantirishi.

**KIRMAYDI.** Yangi mahsulot imkoniyati yo'q. Bu faza — mavjudini topshirishga
tayyorlash.

**Bog'liqliklar.** 4-faza (`reports`, `notifications`), 2-faza (`jobs`).

**Bo'laklar — qisman yozilmaydi.**

| # | Bo'lak | Holat |
|---|---|---|
| 1 | Eksport mexanizmi — `jobs` ustida, natija `uploads` ning `export` purpose'ida | Yozilishi mumkin. ⚠ Eksport uchun **imzolangan URL** kerak ([STATUS.md](STATUS.md) §4.11) |
| 2 | Ommaviy yuborish ijrosi | Yozilishi mumkin |
| 3 | Saqlash muddatlari va tozalash beat vazifasi | ⛔ **`PROJECT.md` §16.5** — audit log va anonimlashtirilgan buyurtmalar necha muddat saqlanadi? |
| 4 | O'rnatish / yangilash / zaxira hujjati | Yozilishi mumkin — `PROJECT.md` §14 dan |

**Qabul mezoni** ([PROJECT.md](PROJECT.md) §15): client hujjat bo'yicha o'z
serveriga mustaqil o'rnata oladi. Tekshiruv — [ARCHITECTURE.md](ARCHITECTURE.md)
§16 dagi qo'lda e2e: ko'tarilish → migratsiya → `owner` yaratish → panelga
kirish → brend rangini o'zgartirish → `site-config` da deploysiz aks etishi →
aviachipta qidiruvi → bron → sandbox to'lovi → chipta → qaytarish.

---

## 10. Nazorat ro'yxati

Bu hujjat ishlashda davom etishi uchun:

- Yangi endpoint `API.md` ga qo'shilsa — **shu hujjatning §1 xaritasiga ham**
  qo'shiladi. Fazasiz endpoint qolmasin.
- Faza chegarasi o'zgarsa — **avval `PROJECT.md` §15**, keyin bu yer.
- Bo'lak tugaganda — `STATUS.md` yangilanadi, bu hujjat emas.
- ⛔ belgisi qo'yilgan joyda ish boshlanmaydi: avval `PROJECT.md` §16 dagi
  savolga javob, keyin (kerak bo'lsa) `API.md`, keyin bo'laklar.
