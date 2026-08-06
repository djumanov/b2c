# Holat va qolgan ish

**Oxirgi yangilanish:** 2026-08-06 · `feat/payment-integrations`

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
| Bosqich | **1 — Yadro**, 3 qabul mezonining **uchalasi ham** bajarilgan |
| Endpointlar | 47 ta yo'l / 66 operatsiya (API.md dagi ~150 dan) |
| Jadvallar | 18 ta + `alembic_version` |
| Migratsiyalar | 11 ta, bitta head (`179a9459a8fa`) |
| Testlar | 504 ta — unit 15 fayl · contract 7 · integration 18 |
| Gate'lar | ruff · mypy strict · pytest — hammasi yashil |

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
| `integrations` (SMTP) | Singleton sozlama, shifrlangan parol, haqiqiy sinov xabari | 2 |
| `integrations` (to'lov) | Har bir provayder uchun qator, shifrlangan credential obyekti, `site-config` va `health` ga ulanish | 2 |
| `integrations` (social) | Google OAuth client'i; `client_id` ochiq, `client_secret` shifrlangan | 1 |
| `system` | health, version (setup'dan) | 2 |
| `customers` (auth) | Ro'yxatdan o'tish + email OTP, kirish, rotatsiyali refresh, uch qadamli parol tiklash | 9 |
| `customers` (profil) | Profil o'qish/tahrir, avatar, parol, akkauntni o'chirish, saqlangan yo'lovchilar CRUD | 5 |
| `customers` (social) | `social/{provider}/` — Google ID token tekshiruvi, topiladi yoki yaratiladi | 1 |
| `api/multipart` | Yuklanadigan tanani chegara bilan o'qish — ikkala yuza uchun bitta joyda | — |
| `api/deps` | `RequireFeature` — o'chirilgan bo'lim ikkala yuzada `404`; o'n bitta bayroq. `current_customer` endi **qatorni yuklaydi** | — |

> Ustundagi son — **yo'llar** soni (11+1+1+7+1+3+2+2+1+2+9+5+1 = 47).
> Operatsiyalar ko'proq: `settings` ning yettita yo'lida 12 ta bor, chunki
> beshtasi `GET`+`PATCH` juftligi (`products/` faqat `GET`, `cache/purge/`
> faqat `POST`); `integrations` ning to'qqizta yo'lida 14 ta. `customers` ning
> auth qismida 10 yo'l = 10 operatsiya (hammasi `POST`), profil qismida esa
> 5 yo'lda 11 ta.

**Jadvallar:** `staff`, `staff_refresh_tokens`, `audit_log`, `uploads`,
`branding`, `site`, `languages`, `currencies`, `features`, `product_settings`,
`gts_credentials`, `smtp_settings`, `customers`, `customer_refresh_tokens`,
`email_otps`, `passengers`, `payment_providers`, `social_credentials`.

**Beat jadvali:** `heartbeat` (5 daq) · `sweep_unlinked_uploads` (soatlik).

---

## 3. Keyingi ish

To'liq reja — [PHASES.md](PHASES.md).

1-bosqichdan **bitta bo'lak** qoldi:

| # | Bo'lak | Nima |
|---|---|---|
| 7 | `tests/e2e/test_phase1_acceptance.py` | Uchala qabul mezoni toza baza ustida, uchidan-uchiga |

**2-fazaga qoldirilgan, ya'ni bugun `404` qaytaradigan yo'llar:**

- `POST /admin/integrations/gts/test/` — probe `providers/gts/` ga tushadi
  (2-fazaning 1-bo'lagi) va 2FA holatini alohida ko'rsatishi kerak (D1).
- `POST /admin/integrations/payments/{code}/test/` — Payme va Click
  adapterlari bilan birga (2-fazaning 7-bo'lagi). Bugungi `PaymentProvider`
  portida sinash uchun chaqiriladigan metod yo'q: har biri haqiqiy to'lovni
  boshlaydi. Adapter kelganda portga `verify()` qo'shilishi kerak — `Notifier`
  da shunday.
- `POST /public/auth/devices/` — API.md §41, push bilan birga.

**Seam'lar 2-faza uchun tayyor**, va qabul mezoni 5-bo'lakdagi bilan bir xil:
2-faza `providers/gts/client.py` va `providers/payments/{payme,click}.py`
qo'shadi, `integrations` ga **tegmaydi**.

| Seam | Nima qaytaradi |
|---|---|
| `integrations.service.active_credential(session)` | Shifri ochilgan GTS akkaunti yoki `None` |
| `integrations.service.payment_providers(session)` | Yoqilgan va credential'i bor provayderlar |
| `integrations.service.notifier(session)` | SMTP yoki log adapteri |
| `integrations.service.social_verifier(session, provider)` | Google verifier yoki `None` |

Boshqa **koddagi aniq nuqtalar**:

- **2-faza uchun ochiq savol:** `passengers.document_type` cheklanmagan satr.
  Hujjat turlari katalogi GTS'da ([GTS.md](GTS.md) §9), API.md §26 da esa uni
  beradigan endpoint yo'q. Bron oqimi qat'iy qiymat talab qilsa — avval §26 ga
  `document-types/` qo'shiladi, keyin bu ustun cheklanadi.
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
| 30 | `RequireFeature` — class, `RateLimit`/`Audited` esa funksiya-fabrika | Sweep uni dependency daraxtidan `isinstance` bilan topishi kerak; closure'ga atribut osish ishlardi-yu, birinchi o'quvchidan omon qolmasdi |
| 31 | Noma'lum bayroq **import vaqtida** yiqiladi | `RATE_LIMITS[kind]` bilan bir xil: xato yozilgan bayroq jarayonni boot'da to'xtatadi, aks holda hech kim o'chira olmaydigan bo'lim jimgina xizmat qilaverardi |
| 32 | `register/` akkaunt qatorini **darhol**, tasdiqlanmagan holda yaratadi — kutayotgan ro'yxatlar uchun alohida joy yo'q | Bitta manzil bitta joyda turadi. Muqobili — tasdiqlanmagan yozuvlar do'koni, keyin uni `customers` bilan sinxron saqlash. `is_active` `email_verified_at` ni talab qilgani uchun bunday qator hech narsa bermaydi: u akkaunt emas, band qilingan joy |
| 33 | Band manzilga ham **`204`**, xabar esa **o'sha manzilning o'ziga** ketadi | `409` bo'lsa `register/` "bu odamning bizda akkaunti bormi?" degan savolga javob beradigan vositaga aylanardi, va u autentifikatsiyasiz. Kimga bilish kerak — manzil egasiga — o'ziga boradi |
| 34 | Kodlar **Postgres'da**, parol tiklash tokeni esa Redis'da | Olti raqam faqat urinishlar shifti bilan yetarli, shift esa kesh tozalanganda nolga qaytmasligi kerak. Token boshqa narsa: uzun, bir martalik va qisqa umrli — `getdel` unga aynan mos |
| 35 | Qayta yuborish oynasi **jim** ishlaydi: `204`, lekin xat ketmaydi | Ko'rinadigan `429` "bu manzilda kutayotgan kod bormi?" deb aytardi — ya'ni §4.33 dagi `204` ning ma'nosini yo'q qilardi. Ko'rinadigan chegara API.md §14 niki bo'lib qoladi, u manzilni emas, IP'ni sanaydi |
| 36 | Mijoz auth hodisalari `audit_log` ga **yozilmaydi** | PROJECT.md §13 jurnalni **paneldagi** mutatsiyalar bilan chegaralaydi va `tests/contract/test_audit_coverage.py` faqat `/admin/*` ni supuradi. Mijoz login'lari `/admin/system/audit/` ni ko'mib tashlardi — u aynan xodimlar faoliyatini ko'rsatish uchun bor |
| 37 | Parol pastki chegarasi `customers` da **qayta yozilgan**, `staff` dan import qilinmagan | Bugun ikkalasi 8, lekin ular boshqa-boshqa narsa: biri client o'z xodimlariga qo'ygan siyosat, ikkinchisi ommaga qo'yilgani. Umumiy konstanta bo'lsa bittasini o'zgartirish ikkalasini o'zgartirgandek ko'rinardi |
| 38 | Yo'lovchi maydonlari **`PROJECT.md` §13 ro'yxatidan** olingan va undan oshmaydi | Saqlanadigan shaxsiy ma'lumot ro'yxati e'lon qilingan hujjat. Modul o'z ehtiyoji uchun unga maydon qo'shsa, ro'yxat hujjatda emas, kodda bo'lib qoladi — va PII inventarini kod bo'ylab yig'ib chiqish kerak bo'lardi |
| 39 | `document_type` — **cheklanmagan satr**, enum ham, CHECK ham emas | Katalog GTS tomonda va §26 da uni beradigan endpoint yo'q. Lokal enum keyin keladigan katalogga zid bo'lib chiqishi mumkin, CHECK esa bu xatoni tuzatishni migratsiyaga aylantiradi |
| 40 | Yo'lovchida **bitta hujjat**, ichma-ich ro'yxat emas | "Yo'lovchilar va hujjatlari" ikki xil o'qiladi. Saqlangan yo'lovchi bron uchun bitta hujjat bilan ishlatiladi; ko'plik kerak bo'lsa bu jadval qatorlar bilan kengayadi, sxema bilan emas |
| 41 | `avatar` purpose'i **`public`** | Private fayl route'ini `current_staff` qo'riqlaydi (`uploads/router_files.py`), ya'ni private avatar — egasi ocholmaydigan avatar. Uni yechish uchun kalitga egalik tekshiruvi kerak bo'lardi, bu esa route'ning butun dizaynini o'zgartiradi |
| 42 | `avatar` — faqat **raster**, SVG yo'q | Logotipni client o'zi tanlaydi, avatarni esa notanish odam yuklaydi. SVG — XML, ichida `<script>` bo'lishi mumkin va imzo tekshiruvi buni ko'rmaydi (§4.13 bilan bir sabab, lekin bu yerda yuklovchi ishonchsiz) |
| 43 | Avatar **to'g'ridan-to'g'ri** `POST /public/profile/avatar/` ga yuklanadi, `uploads/` orqali emas | API.md §11 ning ikki qadamli naqshi `/admin/uploads/` ga tayanadi, unga mijoz tokeni kira olmaydi (§4). Public yuzada `uploads/` resursi ochish — bir endpoint uchun butun bir resurs, ustiga anonim yuklash yuzasi |
| 44 | Akkaunt o'chirilganda qator **bo'shatiladi**, faqat soft delete emas | `PROJECT.md` §13 "tozalanadi" deydi; `deleted_at` qatorni yashiradi, bo'shatmaydi. Manzilni ham bo'shatish — o'sha odam qaytadan ro'yxatdan o'ta olishining sharti, chunki unique indeks faqat tirik qatorlarni qamraydi |
| 45 | `DELETE /public/profile/` **parol so'raydi** | Mijoz tokeni bajara oladigan yagona qaytarib bo'lmaydigan amal. Narxi — `DELETE` da tana, va ba'zi klientlar (`httpx`) uni qisqartmada yubora olmaydi; kontraktda ogohlantirish bor |
| 46 | Yo'lovchi so'rovlari **har doim** egasi bo'yicha cheklangan, boshqa mijozniki `404` | `403` "bunday id bor" deb aytardi. Egasiz chaqirilishi mumkin bo'lgan yordamchi — bu oxir-oqibat egasiz chaqiriladigan yordamchi, shuning uchun `owned_passengers(customer_id)` dan boshqa kirish nuqtasi yo'q |
| 47 | Tanani chegara bilan o'qish `app/api/multipart.py` ga ko'chirildi | Ikkinchi yuza paydo bo'ldi. Muqobili — bir modul routerining boshqa modul routeridan yordamchi import qilishi, ya'ni ARCHITECTURE.md §4 to'sib turgan bog'liqlikning aynan o'zi |
| 48 | To'lov credential'lari — **bitta shifrlangan JSON obyekt**, kalit boshiga ustun emas | Payme va Click turli kalitlar so'raydi va ro'yxat har birining o'z hujjatidan keladi. Ustunlar bilan bo'lsa, kontraktda adapterlar keyin rad etadigan sxema paydo bo'lardi. Shu sababli `enabled: true` uchun ham faqat "bo'sh emas" tekshiriladi — aniq kalitlarni adapter biladi |
| 49 | `PATCH` credential'larni **birlashtiradi**, almashtirmaydi | Panel qiymatlarni faqat maskalangan holda oldi, ya'ni bitta kalitni tahrirlab qolganini qayta yubora olmaydi. Almashtirish rejimida bitta maydonni o'zgartirish qolganini o'chirardi |
| 50 | Maska ichida **bittagina** nuqta bo'lsa ham qiymat e'tiborsiz qoldiriladi | Birinchi versiya "hammasi nuqta" deb tekshirardi va haqiqiy maskani o'tkazib yuborardi: `mask_secret` oxirgi belgilarni ataylab ko'rsatadi. Provayder beradigan hech bir qiymatda `•` yo'q |
| 51 | Provayder qatorlari **birinchi o'qishda** yaratiladi, migratsiyada emas | Migratsiya faqat yozilgan paytdagi provayderlarni qamrardi. Bu yo'l bilan keyingi reliz qo'shgan provayder yangilangan o'rnatmada o'zi paydo bo'ladi — o'chirilgan holda |
| 52 | Standart `sort_order` — enum e'lon tartibi (o'nlab qadam bilan) | Aks holda tartib `code` bo'yicha alifbo bo'lardi, ya'ni hech kim so'ramagan savolga tasodifiy javob. O'nlab qadam keyingi provayderni ikkitasining orasiga qo'yish imkonini beradi |
| 53 | `health/` **tarmoqqa chiqmaydi**: `ok` "sinab ko'rishga narsa bor" degani | Bu endpoint pollanadi; har pollda GTS'ga kirish client'ning mashina akkauntini hech kim so'ramagan savolga sarflaydi. Da'vo `database`/`redis` nikidan torroq va buni docstring aytadi. Tiriklik — `test/` ning ishi, uni odam bosadi |
| 54 | `payment_logo` — alohida purpose, `logo` qayta ishlatilmadi | Ikkalasini boshqa-boshqa resurs biriktiradi, va `uploads.service.link` aynan shuni ajratish uchun `purpose` oladi. SVG bu yerda ruxsat: to'lov brendlari SVG chiqaradi va faylni xodim yuklaydi, avatardan farqli |
| 55 | Social credential'da `client_id` **ochiq**, faqat `client_secret` shifrlanadi | `client_id` tugmani chizadigan har bir brauzerga baribir beriladi. Uni panelda yashirish operatorni Google konsolidagi qiymat bilan solishtira oladigan yagona qiymatdan mahrum qilardi |
| 56 | Google tasdiqlagan manzil **bu yerda ham tasdiqlangan** | Email OTP manzilni kimdir boshqarishini isbotlash uchun bor; `email_verified` aynan shuni isbotlaydi. Ustiga kod so'rash bir xil isbotni ikki marta so'rash bo'lardi. `email_verified: false` esa rad etiladi — bu istalgan odam boshqasining pochtasi haqida qila oladigan da'vo |
| 57 | Social oqim akkauntni **topadi yoki yaratadi**, tasdiqlanmaganini esa tasdiqlaydi | Bitta manzilda ikkita akkaunt — bitta odamning ikkita buyurtma tarixi. Yaratilgan qatorda parol xeshi bo'sh: uni taxmin qilib bo'lmaydi, kerak bo'lsa parol tiklash orqali qo'yiladi |
| 58 | `social/{provider}/` yo'l parametri **enum emas, satr** | Enum bo'lsa noma'lum provayder `422` bo'lardi va anonim chaqiruvchiga bizning ro'yxatimizni tasvirlardi. Tashqaridan "biz buni qo'llamaymiz" va "client buni o'chirgan" farq qilmasligi kerak — ikkalasi ham `404` |
| 59 | ID token **lokal tekshiriladi**, Google'dan so'ralmaydi | `tokeninfo` chaqiruvi uchinchi tomonni har bir kirishning kritik yo'liga qo'yardi va ularning uzilishi bizniki bo'lardi. Google kalitlarni o'zi chop etadi va shu yo'lni tavsiya qiladi; kalitlar Redis'da keshlanadi |
| 60 | Verifier override'i **faqat o'z provayderiga** javob beradi | Aks holda pinlangan Google verifier'i qo'llanmaydigan provayderni ishlata boshlardi va testlar hech kim yeta olmaydigan route haqida o'zlari bilan kelishib qolardi |

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
qayta yozildi (§4 dagi 41–43). Zanjirdagi **birinchi shunday migratsiya**, va
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

    Uchinchisi tuzoq emas, lekin yodda tutilsin: `downgrade` torroq CHECK'ni
    qaytarishdan **oldin** `avatar` qatorlarini o'chiradi, aks holda mavjud
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
| Menyu va sahifalar modeli | 5-bosqich |
| Saqlash muddatlari | 7-bosqich |
| Kutilayotgan yuk | server o'lchami; D2 sababli har `offers/` GTS'ga boradi |
| **GTS 2FA (D1)** | 2-bosqich. Kolleksiyadan aniqlandi: bu **akkaunt bayrog'i** (`two_factory`), ya'ni GTS tomonda o'chiriladi — kod masalasi emas ([GTS.md](GTS.md) §3). Yoniga ikkita band qo'shildi: `white_list` (client IP'si) va `is_single` |

---

## 8. Ma'lum kamchiliklar

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

### 2026-08-06 — GTS credential'lari yozilayotganda topilgani

13. 🟠 **`mask_secret(value, visible=0)` sirni to'liq qaytarardi.**
    `value[-0:]` — bu `value[0:]`, ya'ni butun satr; umumiy ifoda uni o'z
    maskasiga qo'shib qo'yardi. Hech kim hali bunday chaqirmagan edi, lekin
    "hech narsa ko'rinmasin" degan so'rov sirni chop etishi jimgina kutib
    turgan tuzoq. Endi alohida shox.
