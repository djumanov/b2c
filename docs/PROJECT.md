# B2C platforma — loyiha haqida

Bu hujjat **nima quraiyotganimizni** va **qanday shartlarda** qurayotganimizni belgilaydi:
qamrov, mas'uliyat chegarasi, qabul qilingan qarorlar, bosqichlar va risklar.

Yakuniy hujjatlar to'plami — to'rtta fayl:

| Hujjat | Nima uchun |
|---|---|
| **PROJECT.md** (shu hujjat) | Mahsulot va loyiha: qamrov, qarorlar, bosqichlar, ekspluatatsiya |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Backend ichki tuzilishi: modullar, qatlamlar, DB, saga, adapterlar |
| [API.md](API.md) | REST kontrakti: konvensiyalar va barcha endpointlar |
| [GTS.md](GTS.md) | Mahsulot manbai — GTS B2B platformasi haqida ma'lumot |

> **O'qish tartibi.** Yangi kelgan odam uchun: PROJECT.md → GTS.md → API.md → ARCHITECTURE.md.
> Ziddiyat bo'lsa: kontrakt masalalarida **API.md**, ichki tuzilma masalalarida
> **ARCHITECTURE.md**, mahsulot va qamrov masalalarida **shu hujjat** ustun turadi.

---

## 1. Loyiha nima?

Biz **sayohat sotish uchun tayyor B2C mahsulot** quramiz: sayt + mobil ilova + admin panel +
backend. Mahsulotni clientga topshiramiz, client uni **o'z serveriga** o'rnatadi va **o'z brendi
ostida** ishlatadi. Aviachipta, poyezd, sug'urta, eSIM va transfer **GTS API'sidan** keladi.

```
Bizning mahsulot  ──topshiriladi──►  Client A serveri:  brand-a.uz + mobil app + panel + DB
                  ──topshiriladi──►  Client B serveri:  brand-b.com + mobil app + panel + DB
```

Har bir client — **mustaqil o'rnatma**. Tenant tushunchasi yo'q: bitta o'rnatma bitta clientga
tegishli, o'z DB'si, o'z domeni, o'z serveri, o'z sozlamalari. Clientlar bir-birining
ma'lumotini ko'rmaydi, chunki ular umuman bir serverda turmaydi.

Bundan kelib chiqadigan bosh qoida butun loyiha bo'ylab amal qiladi:

> **Clientga xos farq kodda emas, DB'dagi sozlamada bo'ladi.**
> Bitta kod bazasi, ko'p o'rnatma. Agar biror farq kodga tushsa, o'rnatmalar bir-biridan
> uzoqlashadi va bitta clientga qilingan tuzatish boshqalariga yetib bormaydi.

---

## 2. Atamalar

Hujjatlar bo'ylab shu atamalar aynan shu ma'noda ishlatiladi.

| Atama | Ma'nosi |
|---|---|
| **Client** | Mahsulotni bizdan olgan va o'z brendi ostida sotuvchi tashkilot |
| **O'rnatma** (installation) | Bitta clientning serveridagi ishlaydigan nusxa: backend + DB + Redis + sayt + panel |
| **Customer** | Oxirgi foydalanuvchi — saytda yoki ilovada chipta sotib oluvchi shaxs. Token `aud: public` |
| **Staff** | Client jamoasining xodimi — panelda ishlaydi. Token `aud: admin`. Customer bilan **hech qachon** almashmaydi |
| **Vertikal** | Mahsulot turi: aviachipta, poyezd, sug'urta, eSIM, transfer |
| **Taklif** (offer) | Qidiruv natijasidagi bitta sotib olish varianti. Amal muddati cheklangan |
| **`request_id`** | Bitta qidiruv sessiyasining identifikatori. Barcha takliflar shunga bog'langan |
| **Buyurtma** (order) | Bron qilingan va to'lanadigan/to'langan xarid. Statusi GTS'dan keladi |
| **GTS agent** | GTS ichidagi sotuvchi hisob yozuvi. Har bir o'rnatma GTS'ga shunday akkaunt bilan ulanadi |
| **`site-config`** | Sayt va ilova ishga tushganda oladigan konfiguratsiya: brending, tillar, mahsulotlar, menyu |

---

## 3. Qamrov

**Kiradi:**

- Beshta vertikal bo'yicha qidiruv → bron → to'lov → buyurtma oqimi
- Mijoz akkaunti: ro'yxatdan o'tish, profil, saqlangan yo'lovchilar, buyurtmalar tarixi
- Kontent boshqaruvi (CMS): blog, aksiyalar, FAQ, sahifalar, bannerlar, kontaktlar
- Promokodlar, sharh moderatsiyasi, murojaatlar (lead) va obunalar
- Buyurtma va to'lov operatsion boshqaruvi, qaytarishlar
- Hisobotlar va eksport
- Ko'p tillilik (uz / ru / en) va ko'p valyutalilik
- Brending va sayt sozlamalari — paneldan, deploysiz
- Rollar, audit log, GTS support kirishi

**Kirmaydi** — ataylab, chunki bu GTS tomonda yoki umuman mahsulot doirasidan tashqarida:

| Nima | Nega |
|---|---|
| O'z narx / markup mexanizmi | Narx GTS shartnomasi va qoidalar mexanizmi orqali shakllanadi ([GTS.md](GTS.md) §6, §7) |
| Agentlik ierarxiyasi, shartnomalar, balanslar | GTS'ning o'z modeli. B2C bunga aralashmaydi |
| Provayderlarga to'g'ridan-to'g'ri integratsiya | Barcha supplier'lar GTS ortida |
| Ko'p tenantlik | Bitta o'rnatma = bitta client |
| Rol konstruktori | Rollar oldindan belgilangan (§9) |
| Korporativ mijoz kabineti | GTS tomonda mavjud, B2C'da yo'q |
| Loyalty / bonus dasturi | `site-config` da `features.loyalty` bor, lekin birinchi relizda `false` |
| Oflayn rejim | Sayt ham, ilova ham GTS'siz sotolmaydi (§12) |

---

## 4. Mas'uliyat chegarasi

Uch tomon bor va ularning chegarasi aniq bo'lishi kerak — ayniqsa o'rnatmani client o'zi
boshqargani uchun (§14).

| Kim | Nimaga javobgar |
|---|---|
| **Biz** (mahsulot jamoasi) | Kod, Docker artefaktlari, migratsiyalar, hujjatlar, xatolarni tuzatish, yangi versiyalar chiqarish, `gts_support` orqali diagnostika |
| **GTS** | Mahsulot inventari, narx va markup, shartnoma va balans, chipta chiqarish, provayderlarga ulanish, buyurtma statusi |
| **Client** | Server va domen, SSL, o'rnatish, **yangilash**, **zaxira nusxa**, to'lov provayderi bilan shartnoma, kontent va brend, o'z mijozlariga qo'llab-quvvatlash |

> Amaliy natija: sayt ishlamay qolsa sabab uch joydan birida — **bizning kodda**, **GTS tomonda**
> yoki **client infratuzilmasida**. `GET /admin/system/health/` aynan shu uchtasini ajratish
> uchun DB, Redis, GTS va to'lov provayderlari holatini alohida ko'rsatadi.

---

## 5. Client nima oladi

| Komponent | Izoh |
|---|---|
| **Website** | O'z domenida, o'z brendi bilan. Qidiruv → bron → to'lov to'liq ishlaydi. Bitta kod bazasi, per-client build |
| **Mobil ilova** | Flutter, iOS + Android — o'z ikonkasi, nomi va bundle id'si bilan |
| **Admin panel** | React SPA — brending, kontent, buyurtmalar, to'lovlar, promokodlar, mijozlar, hisobotlar |
| **Backend** | FastAPI + PostgreSQL + Redis — sayt, ilova va panelning ortidagi yagona servis |
| **Hujjat** | O'rnatish, yangilash, zaxira va tiklash bo'yicha qo'llanma — mahsulotning ajralmas qismi |

Mahsulot tarkibi (qaysi vertikallar sotiladi), markup va provayder bog'lanishi **GTS tomonda**
shartnoma orqali belgilanadi — panel ularni faqat ko'rsatadi, o'zgartira olmaydi.

---

## 6. Arxitektura

```
Client serveri:

  React admin panel          Sayt (web build) / Flutter app
         │                              │
         ▼                              ▼
  /api/v1/admin/*                /api/v1/public/*
         └──────────────┬───────────────┘
                        ▼
           FastAPI backend  +  PostgreSQL  +  Redis
                        │
                        ▼
                    GTS API  (qidiruv, bron, chipta chiqarish)
```

**Bitta backend, ikkita API yuzasi:**

| Yuza | Kim ishlatadi | Nima uchun |
|---|---|---|
| `/api/v1/public/*` | Sayt va Flutter app | Oxirgi foydalanuvchi: qidiruv, bron, to'lov, profil, kontent o'qish |
| `/api/v1/admin/*` | React panel | Client jamoasi: kontent, buyurtmalar, to'lovlar, sozlamalar |

Ikkalasi bitta domen model va bitta DB ustida ishlaydi, lekin **auth va rol modeli alohida**:
customer va staff — turli sub'ektlar, turli tokenlar, bir-biriga o'tmaydi. Customer tokeni bilan
`/admin/*` ga urinish `403` beradi.

Uchinchi, kichik yuza: `/api/v1/webhooks/payments/{provider}/` — to'lov provayderlari uchun,
imzo bo'yicha tekshiriladi, auth talab qilmaydi.

Backend ichki tuzilishi — [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 7. Sozlamalar DB'da va build/runtime chegarasi

Brending, sayt sozlamalari, to'lov provayderlari va GTS credential'lari — hammasi **DB'da**
saqlanadi va paneldan boshqariladi. Credential va kalitlar shifrlangan holda.

Natija: client logoni yoki to'lov provayderini o'zgartirsa, **qayta deploy kerak emas**.
Faqat infratuzilma parametrlari env'da qoladi: DB ulanishi, Redis, shifrlash kaliti, log darajasi.

**Chegara aniq bo'lishi shart.** Aks holda client panelda rangni o'zgartiradi-yu, natija
ko'rinmaydi. Quyidagi jadval — shu ajratmaning yakuniy ro'yxati:

| Element | Build vaqtida qotadi | Runtime'da (`site-config`) | Izoh |
|---|---|---|---|
| API URL | ✓ | — | Domen o'zgarsa sayt/ilova qayta build qilinadi |
| Sayt nomi, kontaktlar | — | ✓ | |
| Logo, favicon | — | ✓ | URL `site-config` dan keladi |
| Ranglar (primary, accent, background) | — | ✓ | CSS o'zgaruvchilari runtime'da o'rnatiladi |
| Shrift | — | ✓ | Oldindan belgilangan ro'yxatdan tanlanadi |
| Menyu va sahifalar | — | ✓ | |
| Tillar va valyutalar | — | ✓ | |
| Yoqilgan mahsulotlar | — | ✓ | GTS shartnomasidan keladi, faqat o'qish |
| Yoqilgan to'lov usullari | — | ✓ | |
| Bo'limlarni yoqish/o'chirish (`features`) | — | ✓ | |
| **Ilova ikonkasi** | ✓ | — | Store'ga qayta chiqarishni talab qiladi |
| **Ilova nomi** | ✓ | — | ⟪shu kabi⟫ |
| **Bundle / package id** | ✓ | — | ⟪shu kabi⟫ |
| Push sertifikatlari (APNs / FCM) | ✓ | — | Store akkauntiga bog'langan |
| Splash screen | ✓ | — | Ilova ochilganda `site-config` hali yuklanmagan bo'ladi |

> Qoida: **store'ga bog'liq narsa build vaqtida, qolgani runtime'da.** Yangi brending elementi
> qo'shilganda birinchi savol — u shu qoidaning qaysi tomonida.

---

## 8. Mahsulot vertikallari

**Beshtasi ham birinchi relizga kiradi.**

| Vertikal | Kod | GTS prefiksi | Oqim | Vertikalga xos qadamlar |
|---|---|---|---|---|
| Aviachipta | `flight` | `/v1/content/` | search → offers → verify → booking | `seat-map`, `additional-services` |
| Poyezd | `railway` | `/v1/railway/` | search → **trains → train-details** → booking | `offers/` o'rniga `trains/` va `train-details/` |
| Sug'urta | `insurance` | `/v1/insurance/` | search → offers → verify → booking | `calculate/`, `upsell/` |
| eSIM | `esim` | `/v1/esim/` | search → offers → **offer** → booking | `verify/` o'rniga `offer/` |
| Transfer | `transfer` | — | search → offers → **offer** → booking | `offer/`, `recommended-time/` |

Barcha vertikallar public API'da **bir xil naqshda** ishlaydi (`/public/{product}/…`), yuqoridagi
chetlanishlar bundan mustasno. Shuning uchun backend'da har bir vertikal alohida router emas,
**bitta oqim + adapter** sifatida quriladi — batafsili [ARCHITECTURE.md](ARCHITECTURE.md) §6.

> Beshta vertikal birinchi relizda bo'lgani uchun adapter abstraksiyasi **spekulyativ emas**:
> u birinchi kundanoq beshta turli oqim bilan sinovdan o'tadi. Buning evaziga 3-bosqich (§15)
> sezilarli hajmga ega va har bir vertikal GTS tomonda alohida sinovni talab qiladi.

---

## 9. Rollar

Bitta o'rnatma ichida oltita rol. Rollar **oldindan belgilangan** — panel orqali yangi rol
yaratilmaydi, faqat xodimga mavjud rol biriktiriladi.

| Rol | Nima qila oladi |
|---|---|
| `owner` | Hammasi, jumladan xodimlarni boshqarish va integratsiya kalitlari |
| `admin` | Sozlamalar, kontent, buyurtmalar, to'lovlar — kalitlardan tashqari |
| `content` | Faqat kontent va sharh moderatsiyasi |
| `operator` | Buyurtmalar, mijozlar bilan ishlash, push yuborish |
| `finance` | To'lovlar, qaytarishlar, promokodlar, hisobotlar |
| `gts_support` | GTS xodimi uchun vaqtinchalik diagnostika kirishi — client yoqadi/o'chiradi |

Resurs guruhlari bo'yicha to'liq ruxsat matritsasi — [API.md](API.md) §5. Bu yerda takrorlanmaydi.

`gts_support` — muammo bo'lganda GTS jamoasi client o'rnatmasiga kira olishi uchun. Doim
yoqilgan emas: client paneldan yoqadi, amal muddati tugaganda **o'zi o'chadi**, barcha amallar
audit log'ga alohida belgi bilan tushadi. Yozish huquqi yo'q (tizim diagnostikasidan tashqari).

---

## 10. Modullar

| Modul | Nima qiladi | Birinchi reliz |
|---|---|---|
| **Brending** | Logo, ranglar, shrift, favicon, app ikonka va nomi | ✓ |
| **Sayt sozlamalari** | Domen, tillar, valyutalar, yoqilgan bo'limlar | ✓ |
| **Menyu va sahifalar** | Menyu elementlari, statik sahifalar | keyinroq (§16) |
| **Integratsiyalar** | GTS ulanishi, to'lov provayderlari, email xizmati | ✓ |
| **Kontent (CMS)** | Blog, aksiyalar, FAQ, kontaktlar, bannerlar, mashhur yo'nalishlar | ✓ |
| **Sharhlar** | Moderatsiya: qabul qilish / rad etish | ✓ |
| **Buyurtmalar** | Barcha vertikallar bo'yicha ro'yxat, tafsilot, bekor qilish, sync | ✓ |
| **To'lovlar** | Tranzaksiyalar, qaytarishlar, provayder holati | ✓ |
| **Promokodlar** | Yaratish, sozlash, statistika | ✓ |
| **Mijozlar** | Ro'yxatdan o'tgan foydalanuvchilar va ularning buyurtmalari | ✓ |
| **Murojaatlar** | Lead'lar, manbalar sxemasi, obunalar | ✓ |
| **Bildirishnomalar** | Shablonlar, ommaviy yuborish, push | qisman — email ✓, SMS/push keyinroq |
| **Hisobotlar** | Dashboard, sotuv, Excel/CSV eksport (async) | ✓ |
| **Jamoa** | Xodimlar va rollar | ✓ |
| **Tizim** | Holat, versiya, audit log, support kirishi | ✓ |

---

## 11. Qabul qilingan qarorlar

Batafsil asoslar — [ARCHITECTURE.md](ARCHITECTURE.md) §2.

| # | Qaror |
|---|---|
| **D1** | GTS bilan aloqa — **o'rnatmaning o'z GTS agent akkaunti** orqali. Credential DB'da, shifrlangan. ⚠ Ochiq bog'liqlik: mashina akkaunti uchun GTS tomondagi ikki bosqichli tasdiq o'chirilishi kerak |
| **D2** | Qidiruv takliflari **keshlanmaydi**; qidiruv **to'liq holatsiz**. GTS `request_id` bo'yicha o'z keshini yuritadi, biz uni takrorlamaymiz — `offers/` GTS'ga passthrough qilinadi. Evaziga saralash va filtr GTS imkoniyati bilan chegaralanadi |
| **D3** | **Bron → to'lov → avtomatik chipta.** Chipta chiqmasa — avtomatik qaytarish; qaytarish ham bajarilmasa buyurtma `needs_attention` holatiga tushadi va panelda ko'rinadi |
| **D4** | Xarid uchun **akkaunt majburiy** — mehmon sifatida xarid yo'q |
| **D5** | Auth — **email + parol**, qo'shimcha **Google**. ⚠ iOS ilovada Google bo'lsa Apple qoidalari **Sign in with Apple** ni talab qiladi (6-bosqich) |
| **D6** | MVP'da OTP va parol tiklash — **faqat email/SMTP**. Telefon + SMS keyinroq |
| **D7** | To'lov provayderlari — **Payme + Click**. Ikkalasi redirect + webhook; karta+OTP oqimi yo'q, demak **karta raqami serverimizdan o'tmaydi** |
| **D8** | Tillar — **uz + ru + en**. Bo'sh qolgan tarjima [API.md](API.md) §7 fallback zanjiriga tushadi |
| **D9** | Beshta vertikal ham **birinchi relizda** (§8) |
| **D10** | O'rnatish, yangilash va zaxira — **clientning zimmasida**; biz artefakt va hujjat beramiz (§14) |

---

## 12. Nofunksional talablar

| Talab | Qiymat | Manba |
|---|---|---|
| Qidiruv javobi | `search/` **darhol** `request_id` qaytaradi; natijalar `offers/` orqali sahifalab olinadi | Asinxron oqim |
| GTS timeout | Qidiruv **40 s**, boshqa amallar **15 s** | [API.md](API.md) §12 |
| Retry | Faqat idempotent `GET`, 2 marta. Bron va to'lovda retry **yo'q** | [API.md](API.md) §12 |
| So'rov chegarasi | Auth 5/daq/IP · qidiruv 30/daq · boshqa public 120/daq · admin 300/daq | [API.md](API.md) §14 |
| Taklif amal muddati | **GTS tomonda** belgilanadi; muddati o'tganda `409 offer_expired` va qidiruv qaytadan boshlanadi | [API.md](API.md) §3, D2 |
| Tillar | `uz`, `ru`, `en` | D8 |
| Valyutalar | Asosiy `UZS`; qo'shimcha valyuta sozlamada | `site-config` |
| Brauzerlar | Chrome, Safari, Firefox, Edge — oxirgi ikkita versiya | |
| Mobil OS | iOS 14+, Android 8+ | Flutter tayanchi |
| Loglar | JSON, stdout'ga; har bir so'rovda `X-Request-Id` | [API.md](API.md) §13 |

**Ishlamay qolganda xatti-harakat.** GTS ishlamasa sayt **kontentni ko'rsatishda davom etadi**,
lekin sotolmaydi: qidiruv `502 upstream_error` yoki `504 upstream_timeout` qaytaradi va
foydalanuvchiga tushunarli xabar ko'rsatiladi. GTS xatosi hech qachon yashirilmaydi — asl matn
`message` da, asl kod `meta.upstream` da saqlanadi.

> Kutilayotgan yuk raqamlari (bir vaqtdagi foydalanuvchilar, yiliga buyurtmalar soni) hali
> aniqlanmagan — §16 ga qarang. Ular server o'lchamiga va GTS'ga ketadigan so'rovlar soniga
> ta'sir qiladi: D2 bo'yicha har bir `offers/` chaqiruvi GTS'ga boradi, chunki bizda kesh yo'q.

---

## 13. Xavfsizlik va shaxsiy ma'lumot

**Saqlanadigan shaxsiy ma'lumot:** ism va familiya, email, telefon, tug'ilgan sana, hujjat turi
va raqami (yo'lovchi ma'lumoti sifatida), buyurtma tarixi, IP manzil, qurilma push tokeni.

| Mavzu | Qoida |
|---|---|
| **Karta ma'lumoti** | **Hech qachon saqlanmaydi va log'ga tushmaydi.** To'lov provayderi tokeni bilan ishlanadi. D7 tufayli karta raqami umuman serverimizdan o'tmaydi |
| Parollar | `argon2` bilan xeshlanadi |
| Integratsiya kalitlari | DB'da shifrlangan (AES-GCM), kalit env'da. O'qishda **maskalanadi** — faqat oxirgi belgilar ko'rinadi |
| Kalit rotatsiyasi | Har bir yozuvda `key_version` bor, shuning uchun kalitni almashtirish uchun credential'larni qayta kiritish shart emas |
| Transport | Barcha muhitlarda HTTPS majburiy |
| Tokenlar | Access qisqa muddatli; refresh **rotatsiya bilan** — har yangilashda eskisi bekor qilinadi |
| Audit | Paneldagi **har bir mutatsiya** yoziladi: kim, qachon, qaysi resurs, qanday o'zgarish. Auth hodisalari va `gts_support` yoqilishi alohida belgilanadi |
| CORS | Public — sayt va ilova domenlari; admin — faqat panel domeni |

**Akkaunt o'chirilganda** (`DELETE /public/profile/`) shaxsiy ma'lumot tozalanadi, lekin
buyurtma va to'lov yozuvlari **moliyaviy hujjat sifatida anonimlashtirilib saqlanadi** — ular
hisobot va qaytarish uchun kerak.

> Saqlash muddatlari (audit log, anonimlashtirilgan buyurtmalar) hali belgilanmagan — §16.

---

## 14. Yetkazib berish va ekspluatatsiya

O'rnatmani **client o'zi boshqaradi** (D10). Biz artefakt va hujjat beramiz.

### Artefaktlar

| Artefakt | Shakl | Per-client nima o'zgaradi |
|---|---|---|
| Backend | Docker image | Hech nima — sozlama DB'da |
| Admin panel | Docker image (yoki statik build) | Hech nima — brending `site-config` dan |
| Web sayt | Statik build | API URL va statik identifikator (§7) |
| Flutter ilova | iOS + Android build | Ikonka, nom, bundle id, push sertifikatlari (§7) |

### O'rnatish

Docker Compose bilan ko'tariladi: `api`, `worker`, `beat`, `postgres`, `redis`, reverse proxy.
Konteyner ishga tushganda **migratsiya avtomatik bajariladi**, so'ng **faqat birinchi marta**
env'dagi ma'lumotdan birinchi `owner` foydalanuvchisi yaratiladi.

Env'da faqat infratuzilma: DB ulanishi, Redis ulanishi, JWT kaliti, **shifrlash kaliti**,
log darajasi, birinchi owner ma'lumoti. Boshqa hamma narsa — paneldan.

### Yangilash

Client yangi image'ni oladi va konteynerni qayta ko'taradi; migratsiya o'zi ishga tushadi.
Yangilash vaqtini **biz nazorat qilmaymiz**, shundan ikkita talab kelib chiqadi:

- migratsiyalar **oldinga mos** bo'lishi — bir necha versiya oshirib sakrash ishlashi kerak;
- reliz izohlari (changelog) artefakt bilan birga berilishi.

### Zaxira nusxa

Zaxira **clientning zimmasida**. Uchta narsa nusxalanishi shart:

| Nima | Nega |
|---|---|
| PostgreSQL bazasi | Barcha ma'lumot va sozlamalar |
| Yuklangan fayllar volume'i | Logo, rasm, hujjatlar, eksport natijalari |
| **Shifrlash kaliti** | ⚠ **Kalit yo'qolsa barcha integratsiya credential'lari qayta kiritiladi** — GTS va to'lov provayderlari ulanishi to'xtaydi |

> Shifrlash kaliti DB zaxirasi ichida **emas** va bo'lmasligi ham kerak. Uni alohida, xavfsiz
> joyda saqlash — o'rnatish hujjatida alohida ta'kidlanadigan yagona eng muhim nuqta.

### Kuzatuv va qo'llab-quvvatlash

- `GET /admin/system/health/` — DB, Redis, GTS va to'lov provayderlari holati (§4).
- `GET /admin/system/version/` — backend va panel versiyasi. Yordam so'raganda birinchi so'raladigan narsa.
- Loglar JSON shaklida stdout'ga; har bir so'rov `X-Request-Id` bilan bog'lanadi.
- Muammo chuqurroq bo'lsa — client `gts_support` oynasini yoqadi (§9), muddati tugaganda o'zi yopiladi.

---

## 15. Bosqichlar va qabul mezoni

| Bosqich | Natija | Qabul mezoni |
|---|---|---|
| **1. Yadro** | FastAPI skeleti, envelope va xato katalogi, auth (customer + staff), RBAC, sozlamalar + shifrlangan credential'lar, migratsiyalar, audit, `site-config`, health | Panelga `owner` sifatida kirish mumkin · brend rangi o'zgartirilsa `site-config` da **deploysiz** aks etadi · RBAC matritsa testi o'tadi |
| **2. GTS ulanishi va birinchi vertikal** | GTS sessiya klienti va anti-corruption qatlami, `ProductAdapter` porti, **aviachipta** adapteri, holatsiz qidiruv oqimi (D2), verify/bron, Payme + Click, saga, buyurtmalar | Aviachipta bo'yicha qidiruv → bron → to'lov → chipta uchidan-uchiga ishlaydi · chipta xatosida avtomatik qaytarish ishlaydi · takroriy webhook ikki marta yechmaydi |
| **3. Qolgan vertikallar** | Poyezd, sug'urta, eSIM, transfer — har biri adapter sifatida | To'rttasi ham to'liq oqimdan o'tadi · **oqim va saga kodiga o'zgarish kiritilmagan** — adapter porti o'zini shu bilan oqlaydi |
| **4. Panel** | Brending, sozlamalar, integratsiyalar, kontent, buyurtmalar, to'lovlar, mijozlar, promokodlar, xodimlar, dashboard | Har bir rol faqat o'ziga ruxsat etilgan ekranlarni ko'radi · sirlar maskalangan holda qaytadi |
| **5. Sayt** | Web frontend `site-config` va public API ustida, uz/ru/en | Paneldagi rang/logo o'zgarishi saytda **qayta build'siz** ko'rinadi · tarjima bo'lmagan maydon fallback bilan ko'rsatiladi |
| **6. Mobil ilova** | Flutter, per-client build pipeline, **Sign in with Apple** (D5) | Ikkala store uchun build tayyor · brending `site-config` dan keladi |
| **7. Yetuklik va topshirish** | Hisobotlar va eksport, ommaviy yuborish, support kirishi, **o'rnatish/yangilash/zaxira hujjati** | Client hujjat bo'yicha o'z serveriga **mustaqil** o'rnata oladi |

3 va 4-bosqichlar turli modullarga tegadi, shuning uchun parallel bajarilishi mumkin.

---

## 16. Ochiq savollar

| # | Savol | Nimaga ta'sir qiladi |
|---|---|---|
| 1 | **Menyu va sahifalar modeli** — qat'iy tuzilmami yoki erkin konstruktor? Sahifa tanasi sxemasi qanday (til bo'yicha rich-text yoki bloklar konstruktori)? | CMS moduli va sayt frontendi. Birinchi relizga kirmagani uchun 5-bosqichgacha kutadi |
| 2 | **Buyurtmani tahrirlash** — panel yo'lovchi ma'lumotini o'zgartira oladimi yoki bu faqat GTS tomonda bo'ladimi? | Buyurtmalar moduli |
| 3 | **Qaytarish siyosati** — qisman qaytarish qanday hisoblanadi, kim tasdiqlaydi? | To'lovlar moduli. Vaqtinchalik taxmin: `finance` boshlaydi, summa GTS `refund-check` dan olinadi |
| 4 | **Dashboard ko'rsatkichlari** — aniq ro'yxat kerak (sotuv, konversiya, o'rtacha chek, …) | Hisobotlar moduli |
| 5 | **Saqlash muddatlari** — audit log va anonimlashtirilgan buyurtmalar necha muddat saqlanadi? | §13, DB o'sishi |
| 6 | **Kutilayotgan yuk** — bir vaqtdagi foydalanuvchilar va yiliga buyurtmalar soni | Server o'lchami va GTS'ga ketadigan so'rovlar soni (§12, D2) |

**Ochiq tashqi bog'liqlik:** GTS mashina akkaunti uchun ikki bosqichli tasdiq o'chirilishi kerak
(D1). Bu **2-bosqichni to'sib qo'yishi mumkin bo'lgan yagona narsa** va yechimi bizning
qo'limizda emas — GTS jamoasi bilan oldindan hal qilinishi kerak.

---

## 17. Risklar

| Risk | Nima bo'ladi | Yumshatish |
|---|---|---|
| **GTS'ga bog'liqlik** | GTS ishlamay qolsa sayt sotolmaydi | Timeout va retry siyosati oldindan belgilangan (§12) · GTS xatosi yashirilmaydi, foydalanuvchiga tushunarli xabar ko'rsatiladi · kontent GTS'siz ham ishlaydi |
| **Kod bazasining tarqalib ketishi** | Bir clientga qilingan tuzatish boshqalariga yetmaydi, o'rnatmalar uzoqlashadi | Bosh qoida (§1): client-specific farq **kodda emas, sozlamada** · yangi sozlama qo'shilganda birinchi savol — "bu haqiqatan clientga xosmi?" |
| **Versiya tarqoqligi** | Client yangilashni o'zi bajargani uchun (D10) o'rnatmalar turli versiyada qoladi; xato haqidagi xabar qaysi versiyaga tegishli ekani noaniq bo'ladi | Migratsiyalar bir necha versiya sakrashni ko'tarishi · `system/version/` har doim aniq versiyani ko'rsatishi · changelog artefakt bilan berilishi |
| **Build/runtime chegarasi** | Noto'g'ri ajratilsa, paneldagi o'zgarish ko'rinmaydi | §7 dagi jadval — yakuniy ro'yxat · yangi element qo'shilganda shu jadvalga yozilishi shart |
| **Shifrlash kaliti** | Kalit yo'qolsa barcha integratsiya sozlamalari qayta kiritiladi | `key_version` orqali rotatsiya · zaxira ro'yxatida alohida band (§14) · o'rnatish hujjatida alohida ogohlantirish |
| **Beshta vertikal bir vaqtda** | Birinchi reliz hajmi katta; har bir vertikal GTS tomonda alohida sinovni talab qiladi | Aviachipta **etalon** sifatida to'liq ishlab bo'lingach qolganlari adapter sifatida qo'shiladi (§15, 2 → 3-bosqich) · oqim kodi o'zgarmasligi qabul mezoniga kiritilgan |
| **Pul oqimidagi qisman xato** | To'lov o'tib, chipta chiqmay qolishi mumkin | D3: avtomatik qaytarish · qaytarish ham bajarilmasa `needs_attention` holati va panelda ko'rinish — **pul jimgina yo'qolmaydi** |
