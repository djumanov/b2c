# 1. Tadqiqot — bunday tizimlar dunyoda qanday quriladi

Bu hujjatda qaror qabul qilinmaydi. U `03-design.md` uchun **asos** tayyorlaydi:
sayohat va e-commerce sohasida buyurtma tizimlari qanday shakllangan, qaysi
xatolar takrorlanadi va nima uchun deyarli hamma bir xil yechimga keladi.

---

## 1.1 Nega buyurtma tizimi "oddiy CRUD" emas

Oddiy CRUD'da yozuv **bizning** bazamizda yashaydi va biz uni to'liq
boshqaramiz. Buyurtmada esa haqiqat **uch joyda** turadi:

| Joy | Nimani biladi | Biz nimani qila olamiz |
|---|---|---|
| GTS (va uning ortidagi provayder/GDS) | O'rin band qilinganmi, chipta chiqqanmi, tarif hali kuchdami | So'raymiz, buyuramiz — lekin egasi biz emasmiz |
| To'lov provayderi (Payme/Click) | Pul yechildimi, qaytarildimi | So'raymiz, buyuramiz — egasi biz emasmiz |
| Bizning bazamiz | Kim buyurtma qildi, biz nimani so'radik, nima javob keldi | To'liq nazorat |

Uchala tizim ham alohida ishdan chiqadi, alohida sekinlashadi va alohida
"javobsiz" qoladi. **Bu — tarqoq tranzaksiya**, va uning ustida ikki
fundamental cheklov turadi:

1. **Ikki tizim o'rtasida atomik commit yo'q.** "Pulni yech va chiptani chiqar"
   ni bitta amalda bajarish imkonsiz.
2. **Tarmoqda "javob kelmadi" ≠ "bajarilmadi".** Timeout — bu *noma'lumlik*,
   xato emas. Bron chindan ochilgan bo'lishi mumkin.

Buyurtma tizimining butun murakkabligi shu ikki jumladan kelib chiqadi.

---

## 1.2 Sohaviy amaliyot

### Aviatsiya: GDS / Amadeus / Sabre

Aviatsiya sanoati **bron va chiptani hech qachon aralashtirmagan**:

| Tushuncha | Nima | Umri |
|---|---|---|
| **PNR** (Passenger Name Record) | Bron yozuvi: yo'lovchi, segmentlar, kontakt. `gds_pnr` — bizning javobimizdagi maydon | Chipta chiqmasa **avtomatik o'ladi** |
| **Ticket Time Limit (TTL)** | Aviakompaniya belgilagan muddat: shu vaqtgacha chipta chiqmasa PNR bekor qilinadi | Soatlar yoki kunlar |
| **E-ticket** | Alohida hujjat, o'z raqami bilan (13 xonali: aviakompaniyaning 3 xonali kodi + 10 raqam) | Odatda 1 yil |
| **Void** | Chiqarilgan chiptani **iz qoldirmasdan** bekor qilish. Faqat qisqa oyna ichida (odatda chiqarilgan kun oxirigacha) | — |
| **Refund** | Chiptaga qarshi qaytarish. Jarima tarif qoidasidan keladi | Void oynasidan keyin |

Buning bizga bergan uchta darsi:

1. **Bron ≠ chipta.** Ular alohida obyekt, alohida bekor qilish yo'li
   (`cancel` vs `void`/`refund`) va alohida muddat.
2. **Bronning muddati bor va u tashqarida belgilanadi.** To'lov oynasi shu
   muddatdan uzun bo'lolmaydi.
3. **Void va refund — bir xil narsa emas.** Void tekin va tez, refund jarimali
   va sekin. UI ularni bir tugma qilib qo'ysa, pul yo'qoladi.

GTS aynan shu modelni ochib beradi: `booking` → `ticketing` → `void │ cancel │
refund-check → refund-commit` ([`../GTS.md`](../GTS.md) §4).

### Aviabilet OTA'lari (Aviasales/Kiwi/Trip.com tipidagi)

B2C tomonda hammasi bir xil naqshga keladi:

```
qidiruv → taklif → bron (hold) → to'lov → "chipta chiqarilmoqda" → chipta
                                              ↘ chiqmasa → pul qaytariladi
```

Muhim jihat: **"chipta chiqarilmoqda" — foydalanuvchiga ko'rinadigan holat.**
U yashirilmaydi, chunki u soniyalar emas, daqiqalar davom etishi mumkin. Va
aynan shu ekranda foydalanuvchiga **va'da beriladi**: "chipta chiqmasa pul
to'liq qaytariladi". Bu va'da — texnik talab: uni bajarish uchun tizimda
avtomatik kompensatsiya bo'lishi shart.

Ikkinchi jihat: to'lov o'tgach bron **bizniki bo'lib qoladi** — mijoz endi
"bekor qilaman" desa, bu *qaytarish*, *bekor qilish* emas.

### Mehmonxona (Booking.com tipidagi)

Bu yerda to'lov ko'pincha **keyinroq** yoki umuman mehmonxonada bo'ladi.
Natijada `reservation` va `payment` bir-biridan yanada aniqroq ajraladi:
bron tasdiqlangan bo'lishi va to'lov umuman bo'lmasligi mumkin. Bekor qilish
siyosati **tarifga biriktirilgan** (free cancellation until X).

Biz uchun dars: **to'lov holati buyurtma holatining ichida yashamasligi
kerak.** Aks holda kelajakdagi "hozir bron qil, keyin to'la" mahsuloti holat
mashinasini portlatadi. Shuning uchun `payments` alohida entity bo'ladi.

### To'lov: Stripe PaymentIntent

Stripe'ning eng ko'p nusxa ko'chirilgan g'oyasi — **niyat va urinishni
ajratish**:

```
PaymentIntent (bitta niyat: "shu buyurtma uchun shu summani yig'ish")
  └── Charge / attempt (ko'p urinish: karta rad etdi, boshqa usul bilan qayta)
```

Holatlar: `requires_payment_method → requires_confirmation → requires_action
→ processing → succeeded | canceled` (hold ishlatilsa oraliqda
`requires_capture`).

Uchta olinadigan narsa:

1. **Bitta buyurtmaga bitta `payment`, uning ostida ko'p `transaction`.**
   Mijoz Payme'da rad etilib Click bilan to'lasa — bu ikkita urinish, bitta
   to'lov. `API.md` §22 kontrakti ham aynan shunday tuzilgan.
2. **`Idempotency-Key` 24 soat.** Stripe buni sanoat standartiga aylantirdi.
3. **Webhook — at-least-once.** Stripe hodisani takroran yuboradi va
   `event.id` bo'yicha deduplikatsiyani **mijozdan** talab qiladi. Payme
   protokoli ham shunday ishlaydi ([`../API.md`](../API.md) §40).

### E-commerce (Shopify/Amazon tipidagi)

Buyurtma **hech qachon o'chirilmaydi va deyarli hech qachon o'zgarmaydi**;
uning ustiga bolalar qo'shiladi: `fulfillments`, `payments`, `refunds`.
Buyurtmaning o'zi — moliyaviy hujjat.

Dars: `orders` qatori — **hisobot va nizolar uchun dalil**. Soft delete unga
qo'llanmaydi; anonimlashtirish qo'llanadi.

---

## 1.3 Order vs Booking vs Payment vs Ticket

To'rt tushuncha doim aralashtiriladi. Ular quyidagicha ajraladi:

| Entity | Kim yaratadi | Nimani anglatadi | Nechta bo'ladi |
|---|---|---|---|
| **Order** (buyurtma) | **Biz** | Mijozning xarid *niyati* va uning butun umri. Bizning ID, bizning holat mashinasi | Xaridga bitta |
| **Booking** (bron) | **Provayder** (GTS/GDS) | O'rin haqiqatan band qilingani. `order_number`, `gds_pnr`, `ticket_time_limit` | Order'ga bitta (bo'lmasligi ham mumkin) |
| **Payment** (to'lov) | **Biz**, provayder bajaradi | Pul yig'ish niyati va uning urinishlari | Order'ga bitta; urinishlar ko'p |
| **Ticket** (chipta) | **Provayder** | Yo'lovchiga tegishli hujjat, o'z raqami bilan | Yo'lovchiga bitta (marshrutga qarab ko'p ham) |

**Ularning umri mos kelmaydi** — asosiy sabab shu:

```
Order      ├──────────────────────────────────────────────────────────┤
Booking        ├────────────────────────┤ (TTL o'tsa yoki cancel bo'lsa tugaydi)
Payment            ├──────┤ (yechildi)          ├──────┤ (qaytarildi)
Ticket                          ├───────────────────────────────────┤
```

Shuning uchun:

* **Order — yagona aggregate root.** Holat mashinasi unda, boshqa hech qayerda.
* **Booking — order'ning provayderdagi aksi**, alohida jadval emas: uning
  identifikatorlari (`provider_order_number`, `pnr`, `ticket_time_limit_at`)
  order ustunlarida, xom javobi esa snapshot'da yashaydi.
* **Payment — alohida entity**, chunki umri boshqa va kelajakda "keyin to'lash"
  mumkin.
* **Ticket — yo'lovchiga biriktirilgan**, order'ga emas: bitta buyurtmada uchta
  yo'lovchi bo'lsa, uchta chipta raqami bor va ulardan biri chiqmasligi mumkin.

**Anti-pattern:** hammasini bitta jadvalga siqish (`orders.ticket_number`,
`orders.payme_transaction_id`). Bu birinchi ko'p yo'lovchili buyurtmada yoki
birinchi ikkinchi to'lov urinishida yiqiladi.

---

## 1.4 Holat mashinasi dizayni

### Nechta holat kerak

Ikki qarama-qarshi bosim bor:

* **Kam holat** — o'tishlar soni kam, tushunish oson. Lekin holat *ma'noni
  yo'qotadi*: `processing` nima qilyapti — to'lovni kutyaptimi, chipta
  chiqaryaptimi?
* **Ko'p holat** — har biri aniq. Lekin o'tishlar jadvali kombinatorik o'sadi.

Amaliy mezon: **holat qo'shiladi, agar u (a) foydalanuvchiga boshqa matn
ko'rsatsa, (b) boshqa amallar to'plamini ochsa, yoki (c) boshqa fon ishini
anglatsa.** Uchalasi ham "yo'q" bo'lsa — bu holat emas, bu *sabab maydoni*.

Shu mezon bo'yicha `cancelled_by_user` va `cancelled_by_timelimit` alohida
holat **emas** — ular `cancelled` + `cancellation_reason`. Ammo `ticketing`
alohida holat, chunki uchala mezonga ham javob beradi.

### Gerund holatlar

Tashqi tizimga chaqiruv ketayotgan payt **alohida holat bo'lishi kerak**
(`ticketing`, `refunding`). Sabab: bu payt tizim "ikki joyda" — biz so'radik,
javob kelmadi. Bunday holat bo'lmasa, retry mantiqi va "bu buyurtma ustida ish
ketyaptimi?" savoliga javob beradigan joy qolmaydi.

### Terminal holat aslida nima

"Terminal" — noaniq atama. Amalda uch xil bo'ladi:

| Tur | Ma'no | Misol |
|---|---|---|
| **Yopiq** | Hech qanday chiqish yo'q | `failed`, `cancelled`, `refunded` |
| **Yakuniy** | Fon ishi yo'q, lekin **yangi** amal boshlanishi mumkin | `ticketed` (keyin refund) |
| **Qo'lda** | Faqat odam chiqara oladi | `needs_attention` |

Bu farqni yozib qo'ymaslik keyinchalik "ticketed terminal edi-ku, nega undan
chiqyapmiz?" degan chalkashlikni keltiradi.

### Noqonuniy o'tish — xato, jimlik emas

O'tish jadvalidan tashqaridagi urinish **`409 conflict`** bo'lishi kerak, `200`
emas va jimgina e'tiborsizlik ham emas. Chunki u odatda ikkita haqiqiy
muammoning belgisi: race condition yoki mijozning eskirgan ekrani.

---

## 1.5 Tarqoq tranzaksiya: saga, outbox, idempotentlik

### Saga

Ikki fazali commit (2PC) tashqi HTTP API'lar ustida ishlamaydi. O'rniga —
**saga**: ketma-ket lokal tranzaksiyalar va har biri uchun **kompensatsiya**.

| Tur | Qanday ishlaydi | Baho |
|---|---|---|
| **Orchestration** | Markaziy koordinator qadamlarni chaqiradi va holatni yozadi | Kuzatish oson, oqim bitta joyda ko'rinadi |
| **Choreography** | Har bir servis hodisaga reaksiya qiladi | Kam bog'liqlik, lekin oqimni hech kim to'liq ko'rmaydi |

Modulli monolit uchun **orchestration** yagona to'g'ri javob: servislar aro
hodisa shinasi yo'q, va oqimni bitta joyda o'qish — eng qimmatli xususiyat.
Koordinatorning holati — buyurtma qatorining o'zi; saga framework kerak emas.

**Kompensatsiya — rollback emas.** Chipta chiqmagach to'lovni "orqaga qaytarib"
bo'lmaydi; **yangi** amal — refund — bajariladi. Kompensatsiyaning o'zi ham
ishdan chiqishi mumkin, va shuning uchun uning ham oxiri bor bo'lishi kerak.

### Transactional outbox

Klassik muammo:

```
BEGIN; UPDATE orders SET status='paid'; COMMIT;
<--- server shu yerda o'ladi --->
celery.send_task("ticket", order_id)     # hech qachon ketmaydi
```

Buyurtma to'langan, chipta hech qachon chiqmaydi, va buni hech kim bilmaydi.

Yechim: yon ta'sirni **holat o'zgarishi bilan bitta tranzaksiyada** jadvalga
yozish, keyin alohida jarayon uni yuborish:

```
BEGIN;
  UPDATE orders SET status='paid';
  INSERT INTO outbox (kind='orders.ticket', aggregate_id=…);
COMMIT;
-- dispatcher: outbox'dan o'qiydi, taskni yuboradi, dispatched deb belgilaydi
```

Kafolat **at-least-once**: dispatcher yuborgandan keyin, belgilashdan oldin
o'lsa, task ikki marta ketadi. Shuning uchun keyingi band majburiy.

### Idempotentlik — uch qavatda

**"Exactly-once delivery" mavjud emas.** Mavjud bo'lgan narsa — at-least-once
yetkazish + idempotent qabul qiluvchi. Amalda uch qavat kerak:

| Qavat | Nimadan himoya qiladi | Mexanizm |
|---|---|---|
| **HTTP** | Mijoz ikki marta bosdi, tarmoq javobni yo'qotdi | `Idempotency-Key` header, 24 soat saqlanadi |
| **Baza** | Kesh yo'qoldi, ikkita worker parallel ketdi | `UNIQUE` cheklov (idempotency kaliti, provayder ref'i) |
| **Task** | Outbox ikki marta yubordi, provayder webhook'ni takrorladi | Har bir task holatni tekshiradi: "allaqachon `ticketed` bo'lsa — hech nima qilma, muvaffaqiyat qaytar" |

Faqat birinchi qavat bilan cheklanish keng tarqalgan xato: Redis'dagi
idempotency yozuvi **kesh**, kafolat emas.

---

## 1.6 Ishdan chiqish ssenariylari

Har bir jiddiy buyurtma tizimi shu jadvalga javob berishi kerak:

| # | Ssenariy | To'g'ri xatti-harakat |
|---|---|---|
| 1 | Bron so'rovi **timeout** — bron ochilganmi, noma'lum | Buyurtmani "noma'lum" holatda qoldirish va **solishtirish** (reconciliation) bilan aniqlash. Hech qachon "xato" deb yozib, mijozni qayta bron qilishga yubormaslik |
| 2 | To'lov o'tdi, ticketing **ishdan chiqdi** | Xato turini ajratish: vaqtinchalikmi (retry) yoki yakuniymi (refund) |
| 3 | Ticketing ishdan chiqdi, **refund ham** ishdan chiqdi | Qo'lda hal qilinadigan terminal holat + panelda ko'rinish. **Pul jimgina yo'qolmaydi** |
| 4 | Webhook **ikki marta** keldi | Provayder ref'i bo'yicha deduplikatsiya; ikkinchisi muvaffaqiyat qaytaradi, ikkinchi marta yechmaydi |
| 5 | Webhook **umuman kelmadi** | Provayderdan holatni **so'rab** oladigan davriy solishtirish |
| 6 | Mijoz to'lov tugmasini **ikki marta** bosdi | `Idempotency-Key` + bazadagi `UNIQUE` |
| 7 | Ikki qurilmadan **parallel bekor qilish** | `SELECT … FOR UPDATE`; ikkinchisi `409` |
| 8 | **Bron muddati** to'lovsiz o'tdi | Muddat supurgichi bronni provayderda bo'shatadi va buyurtmani yopadi |
| 9 | To'lov bilan ticketing orasida **narx o'zgardi** | Ticketingdan oldin qayta narxlash; farq tolerantlikdan oshsa — chipta chiqarilmaydi, pul qaytariladi |
| 10 | Provayderdagi **balansimiz bo'sh** | Bu *bizning* operatsion nosozligimiz: retry + alert, avtomatik refund **emas** |
| 11 | Worker commit bilan yuborish **orasida o'ldi** | Outbox uni qaytadan oladi |
| 12 | Provayder buyurtma holatini **biz bilmagan holda** o'zgartirdi (masalan o'zi bekor qildi) | Ochiq buyurtmalarni davriy sinxronlash |

10-band alohida e'tiborga loyiq: **hamma ticketing xatosi refundga olib
kelmaydi.** Bu farqni qilmagan tizim balans bo'shaganda o'sha kundagi barcha
buyurtmalarni keraksiz qaytaradi.

---

## 1.7 Retry, kompensatsiya, solishtirish

### Retry

* **Faqat vaqtinchalik xatolar** takrorlanadi. Xatoni sinflash — retry
  siyosatining o'zidan muhimroq.
* **Eksponensial backoff + jitter.** Jittersiz barcha buyurtmalar bir vaqtda
  qayta uriladi.
* **Retry cheksiz emas.** Aviatsiyada tabiiy chegara bor: bron muddati.
  Muddatdan keyin urinishning ma'nosi yo'q — kompensatsiyaga o'tiladi.
* **Bron va to'lov qayta urinilmaydi** (`API.md` §12): takroriy bron — ikkinchi
  haqiqiy o'rin. Takrorlash mijozning ishi, `Idempotency-Key` orqali.

### Kompensatsiya

* Kompensatsiya ham qadam — u ham ishdan chiqadi, u ham retry qilinadi.
* Kompensatsiyaning oxiri **odam** bo'lishi kerak. Avtomatik zanjir tugagan
  joyda navbat boshlanadi.
* Kompensatsiya **idempotent**: ikki marta refund qilinmasligi bazadagi
  cheklov bilan ta'minlanadi, mantiq bilan emas.

### Solishtirish (reconciliation)

Eng ko'p e'tibordan chetda qoladigan qism. Ikki turi bor:

| Tur | Nima qiladi |
|---|---|
| **Holat sinxronizatsiyasi** | Ochiq buyurtmalarni provayderdan qayta o'qib, farqni topadi. Provayder webhook bermasa — yagona yo'l |
| **Yetim izlash** | Bizda "noma'lum" holatda qolgan buyurtmalar uchun provayderdagi mos yozuvni qidiradi |

Ikkalasi ham **polling**. Bu kamchilik emas: webhook — optimallashtirish,
solishtirish esa poydevor. Webhook keyin qo'shilsa, solishtirish olib
tashlanmaydi.

---

## 1.8 Audit izi: event sourcing kerakmi?

| Yondashuv | Nima | Narxi |
|---|---|---|
| **Event sourcing** | Yagona haqiqat — hodisalar oqimi; joriy holat ulardan qayta hisoblanadi | Har bir o'qish uchun proyeksiya; migratsiya og'ir; jamoada tajriba talab qiladi |
| **Status history** | Joriy holat ustunda; har bir o'tish alohida jadvalga qo'shiladi | Arzon, tanish, SQL bilan o'qiladi |
| **Snapshot log** | Tashqi tizim javoblari xom holda saqlanadi | Nizolarda dalil; PII tozalash muammosi |

**Xulosa: event sourcing kerak emas.** Sabablari:

1. Buyurtma holatlari **kam va chiziqli**. Qayta hisoblashdan olinadigan
   moslashuvchanlik bu yerda ishlatilmaydi.
2. Bizga kerak bo'lgan narsa — "nima bo'ldi va qachon" (tarix), "holat nima"
   emas. Buni append-only jadval to'liq beradi.
3. Provayder javoblarini xom saqlash (snapshot) nizolar uchun event
   sourcing'dan **ko'ra foydali**: mijoz "men boshqa narsa ko'rgandim" desa,
   kerak bo'ladigan dalil aynan o'sha xom javob.

Shuning uchun: **status history + snapshot log**, event sourcing'siz. Bu
qaror `03-design.md` da `O9` sifatida qayd etilgan.

---

## 1.9 Bu tadqiqotdan olingan xulosalar

| # | Xulosa | Dizaynda qayerda |
|---|---|---|
| X1 | Order — yagona aggregate root; holat mashinasi faqat unda | `03-design.md` §3 |
| X2 | Booking, payment, ticket — alohida umrga ega, alohida modellashtiriladi | §2 |
| X3 | Bronning tashqarida belgilangan muddati bor; to'lov oynasi undan uzun bo'lolmaydi | §3, §6 |
| X4 | "Chipta chiqarilmoqda" — foydalanuvchiga ko'rinadigan holat va berilgan va'da | §3, §5 |
| X5 | Yon ta'sirlar outbox orqali; at-least-once + idempotent qabul | §6 |
| X6 | Idempotentlik uch qavatda: HTTP, baza, task | §7 |
| X7 | Ticketing xatolari sinflanadi: vaqtinchalik ≠ yakuniy | §3, §6 |
| X8 | Kompensatsiyaning oxiri — odam (`needs_attention`) | §3 |
| X9 | Solishtirish poydevor, webhook optimallashtirish | §6 |
| X10 | Event sourcing emas — status history + snapshot | §4 |
