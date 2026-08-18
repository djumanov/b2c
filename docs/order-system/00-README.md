# Order system — hujjatlar to'plami

Bu papka **buyurtma tizimi** bo'yicha yagona manbadir: bron, to'lov, chipta
chiqarish, bekor qilish va pul qaytarish. To'rtta hujjat bir zanjir bo'lib
o'qiladi — tadqiqot, audit, dizayn, reja.

## Vakolat

| Mavzu | Kim ustun turadi |
|---|---|
| Buyurtma, to'lov, chipta, bekor qilish, qaytarish — **kontrakt, model, holat mashinasi, jadvallar, fon ishlari** | **`docs/order-system/03-design.md`** |
| Qolgan hamma narsa (auth, profil, CMS, katalog, sozlamalar, integratsiyalar…) | [`../API.md`](../API.md) va [`../ARCHITECTURE.md`](../ARCHITECTURE.md) |

Ya'ni vakolat **mavzu bo'yicha** bo'linadi, hujjat turi bo'yicha emas. Order
tizimiga tegishli har qanday ziddiyatda `03-design.md` yutadi; boshqa hamma
joyda eski tartib o'z kuchida qoladi ([`../../CLAUDE.md`](../../CLAUDE.md)).

> **Nega alohida papka.** `API.md` — kontrakt, `ARCHITECTURE.md` — ichki
> tuzilma, `PROJECT.md` — mahsulot. Order tizimi uchalasini ham kesib o'tadi:
> u bir vaqtning o'zida yangi endpointlar, yangi jadvallar, yangi fon ishlari
> va yangi biznes qoidasi. Uni uch hujjatga sochish har bir qarorni uch joyda
> qidirishga majbur qilardi. Shu sababdan bitta joyda turadi, va tashqi
> hujjatlarda faqat **yo'naltiruvchi eslatma** qoladi.

Bu papka **kod yozilishidan oldin** to'liq yozildi. Kod hujjatga ergashadi,
teskarisi emas — bu `CLAUDE.md` ning "hujjat avval" qoidasi.

## O'qish tartibi

| Hujjat | Nima uchun |
|---|---|
| [`01-research.md`](01-research.md) | Dunyoda bunday tizimlar qanday qurilgan va biz nimani oldik. Qaror qabul qilinmaydi — **asos** tayyorlanadi |
| [`02-current-audit.md`](02-current-audit.md) | Mavjud kodning holati: nima noto'g'ri, nima hisobga olinmagan, nima qayta ishlatiladi. Har bir da'vo `fayl:qator` bilan |
| [`03-design.md`](03-design.md) | **Avtoritet.** Domain model, holat mashinasi, DB, API kontrakt, fon ishlari, idempotentlik, observability |
| [`04-plan.md`](04-plan.md) | Bosqichma-bosqich qurish rejasi. Har bir bo'lak mustaqil test qilinadi |

Ishni qaytadan qo'lga olayotgan bo'lsangiz: `04-plan.md` qayerda to'xtaganini,
`03-design.md` nima uchun shunday ekanini aytadi.

## Tashqi hujjatlar bilan aloqa

| Hujjat | Order tizimiga nima beradi |
|---|---|
| [`../GTS.md`](../GTS.md) | GTS nima, aviachipta oqimi, status kodlari, xato konvensiyasi |
| [`../PROJECT.md`](../PROJECT.md) | D3 (bron→to'lov→chipta), D4 (mehmon xaridi yo'q), D7 (Payme/Click), §13 (PII va karta) |
| [`../ARCHITECTURE.md`](../ARCHITECTURE.md) | §4 modul chegarasi, §7 anti-corruption qatlami, §10 DB konvensiyalari |
| [`../API.md`](../API.md) | §1–§16 umumiy qoidalar (envelope, xato katalogi, sahifalash, `Idempotency-Key`) |
| [`../PHASES.md`](../PHASES.md), [`../STATUS.md`](../STATUS.md) | Bajarilish tartibi va hozirgi holat. Avtoritet emas |

`EASY_GATEWAY` Postman kolleksiyasi (`~/Downloads/EASY_GATEWAY_V5_5_16.postman_collection.json`)
va yozib olingan jonli javoblar (`~/Downloads/drct-error*.json`) — GTS shakllari
bo'yicha **`GTS.md` dan ham ustun** manba: ular hujjat emas, haqiqiy chaqiruv.

## Qarorlar reyestri

`03-design.md` dagi qarorlar `O1`, `O2`, … deb raqamlanadi (Order Decision).
Ular `PROJECT.md` dagi `D1…D10` bilan aralashmaydi va ularni bekor qilmaydi —
faqat order tizimi doirasida aniqlashtiradi.
