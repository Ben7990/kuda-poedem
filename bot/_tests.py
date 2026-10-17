"""Автотесты логики (без Telegram). Запуск: py -X utf8 _tests.py"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.render import render_plan
from app.services import affiliate, llm, planner, prices

PASS = 0
FAIL = 0
SKIP = 0


def check(name: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


def check_eq(name: str, got, expected) -> None:
    check(f"{name}: got {got!r}, expected {expected!r}", got == expected)


def test_dates() -> None:
    print("[1] parse_dates / parse_budget")
    check_eq("даты через '-'", planner.parse_dates("10.11.2026 - 20.11.2026"), ("2026-11-10", "2026-11-20"))
    check_eq("даты через 'по'", planner.parse_dates("10.11.2026 по 20.11.2026"), ("2026-11-10", "2026-11-20"))
    check_eq("даты обратный порядок", planner.parse_dates("20.11.2026 - 10.11.2026"), None)
    check_eq("даты мусор", planner.parse_dates("скоро поедем"), None)
    check_eq("бюджет с пробелом", planner.parse_budget("180 000 руб"), 180000)
    check_eq("бюджет строка", planner.parse_budget("миллион"), None)


def test_prices_parsing() -> None:
    print("[2] парсинг ответов Travelpayouts")
    cheap = {"MOW": {"AYT": {"2026-11-10": {"price": 14500, "airline": "S7", "flight_number": 123}}}}
    parsed = prices._parse_cheap(cheap)
    check_eq("cheap цена", parsed[0]["price"], 14500)
    check_eq("cheap авиалиния", parsed[0]["airline"], "S7")
    monthly = {"MOW": {"AYT": [{"price": 9000, "depart_date": "2026-11-12", "return_date": "2026-11-22", "airline": "TK"}]}}
    check_eq("monthly цена", prices._parse_monthly(monthly)[0]["price"], 9000)
    check_eq("пустые данные", prices._parse_cheap({}), [])
    check_eq("сортировка по цене", [t["price"] for t in prices._parse_cheap(
        {"MOW": {"AYT": {"a": {"price": 50000}, "b": {"price": 10000}}}})], [10000, 50000])


async def test_iata() -> None:
    global SKIP
    print("[3] резолв IATA из датасетов")
    for q, expected in [("Ростов-на-Дону", "ROV"), ("Анталья", "AYT"), ("Кемер", "AYT"), ("Москва", "MOW")]:
        try:
            got = await prices.resolve_iata(q)
        except Exception as exc:
            SKIP += 1
            print(f"  - {q}: датасеты недоступны ({exc}), пропуск")
            continue
        check(f"IATA {q} -> {got}" if got == expected else f"IATA {q} -> {got} != {expected}", got == expected)


async def test_affiliate() -> None:
    print("[4] генерация ссылок")
    links = await affiliate.all_links("Ростов-на-Дону", "Кемер, Турция", "2026-11-10", "2026-11-20")
    check("ключи ссылок", set(links) == {"flights", "hotels", "tours", "trains"})
    check("ссылка авиа содержит домен", "aviasales.ru" in links["flights"])
    check("отели — список сервисов", isinstance(links["hotels"], list) and len(links["hotels"]) >= 3)
    check("среди отелей есть Авиасейлс", any("aviasales.ru" in h["url"] for h in links["hotels"]))
    check("отели: город закодирован", any("%D0%9A" in h["url"] for h in links["hotels"]))
    check("экскурсии — Tripster", any("tripster.ru" in t["url"] for t in links["tours"]))
    check("ЖД для Кемер — есть Яндекс.Расписания",
          bool(links["trains"]) and "rasp.yandex.ru" in links["trains"])
    far = await affiliate.all_links("Ростов-на-Дону", "Бангладеш", "2026-11-10", "2026-11-20")
    check("ЖД для Бангладеш — нет (только авиа)", far["trains"] is None)
    # Ссылки на переезды (legs)
    train_leg = {"mode": "train", "from": "Ростов-на-Дону", "to": "Краснодар", "date": "2026-11-10"}
    check("leg train -> Яндекс.Расписания", "rasp.yandex.ru/search/train" in affiliate.leg_link(train_leg))
    bus_leg = {"mode": "bus", "from": "Ростов-на-Дону", "to": "Краснодар", "date": "2026-11-10"}
    check("leg bus -> Яндекс.Расписания", "rasp.yandex.ru/search/bus" in affiliate.leg_link(bus_leg))
    fl_leg = {"mode": "flight", "from": "Краснодар", "to": "Стамбул", "date": "2026-11-10", "from_iata": "KRR", "to_iata": "IST"}
    check("leg flight -> aviasales one-way", "aviasales.ru/search/KRR1011IST2" in affiliate.leg_link(fl_leg))
    check("leg flight без IATA -> общий поиск", "aviasales.ru" in affiliate.leg_link({**fl_leg, "from_iata": "", "to_iata": ""}))
    with_links = affiliate.legs_with_links({"legs": [train_leg, fl_leg]})
    check("legs_with_links: 2 переезда", len(with_links) == 2 and with_links[0]["icon"] == "🚆")


async def test_airport_context() -> None:
    global SKIP
    print("[4] контекст аэропортов (закрытые/альтернативы/страны)")
    check("закрытые: Ростов (ROV)", prices.is_closed("ROV"))
    check("закрытые: Воронеж (VOZ)", prices.is_closed("VOZ"))
    check("открытые: Краснодар (KRR)", not prices.is_closed("KRR"))
    try:
        rosters = await prices.departure_alternatives("Ростов-на-Дону")
    except Exception as exc:
        SKIP += 1
        print(f"  - датасеты недоступны ({exc}), пропуск")
        return
    check("Ростов: есть альтернативы", len(rosters) >= 2)
    codes = [a["code"] for a in rosters]
    check(f"Ростов: среди альтернатив нет закрытого ROV ({codes})", "ROV" not in codes)
    check("Ростов: KRR — первый (крупный хаб)", codes[0] == "KRR")
    check("Ростов: у альтернатив есть расстояние", all(a["distance_km"] > 0 for a in rosters))
    dhaka = await prices.destination_airports("Бангладеш")
    check("Бангладеш -> есть DAC", any(d["code"] == "DAC" for d in dhaka))
    ouaga = await prices.destination_airports("Буркина-Фасо")
    check("Буркина-Фасо -> есть OUA", any(d["code"] == "OUA" for d in ouaga))
    kemer = await prices.destination_airports("Кемер, Турция")
    check("Кемер -> AYT", kemer and kemer[0]["code"] == "AYT")
    bangla = await prices.country_airports("Бангладеш")
    check("страна Бангладеш: есть DAC", any(a["code"] == "DAC" for a in bangla))
    thai = await prices.country_airports("Таиланд")
    check("страна Таиланд: есть BKK", any(a["code"] == "BKK" for a in thai))
    check("resolve_country Бангладеш -> BD", await prices.resolve_country("Бангладеш") == "BD")
    check("resolve_country Ростов -> None", await prices.resolve_country("Ростов-на-Дону") is None)
    ctx = await prices.travel_context(
        {"origin": "Ростов-на-Дону", "destination": "Бангладеш"}
    )
    check("ctx: аэропорт вылета закрыт", bool(ctx["origin_airport"]["closed"]))
    check("ctx: альтернативы заполнены", len(ctx["alternatives"]) >= 2)
    check("ctx: назначение DAC (среди прочих)", any(d["code"] == "DAC" for d in ctx["dest_airports"]))
    check("ctx: есть ключ attractions", "attractions" in ctx)


def test_validation() -> None:
    print("[5] валидация схемы LLM")
    params = {"date_start": "2026-11-10", "date_end": "2026-11-11"}
    good = {
        "route": "R",
        "summary": "S",
        "departure": {"status_note": "n", "options": [{"city": "К", "iata": "KRD"}], "chosen": {"iata": "KRD"}},
        "routing": {"hubs": ["IST"]},
        "visa": "безвиз",
        "practical": ["валюта"],
        "legs": [{"mode": "flight", "from": "Краснодар", "to": "Стамбул", "date": "2026-11-10", "from_iata": "KRR", "to_iata": "IST"}],
        "days": [{"date": "2026-11-10", "title": "T", "items": []},
                 {"date": "2026-11-11", "title": "T", "items": []}],
        "checklist": ["a", "b", "c"],
        "budget_note": "b",
        "advice": "a",
    }
    check("валидная схема проходит", llm._validate_itinerary(good, params) is None)
    bad = {**good}
    del bad["departure"]
    check("нет departure — ошибка", "departure" in (llm._validate_itinerary(bad, params) or ""))
    empty_dep = {**good, "departure": {"status_note": "n", "options": []}}
    check("пустой options — ошибка", "options" in (llm._validate_itinerary(empty_dep, params) or ""))
    no_legs = {**good}
    del no_legs["legs"]
    check("нет legs — ошибка", "legs" in (llm._validate_itinerary(no_legs, params) or ""))
    short = {**good, "days": good["days"][:1]}
    check("меньше дней — ошибка", "должно быть" in (llm._validate_itinerary(short, params) or ""))


async def test_plan() -> None:
    print("[6] сборка плана (demo) и рендер")
    data = {
        "destination": "Кемер, Турция",
        "origin": "Ростов-на-Дону",
        "date_start": "2026-11-10",
        "date_end": "2026-11-20",
        "format": "culture",
        "budget_rub": 180000,
        "travelers": 2,
    }
    plan = await planner.build_plan(data, demo=True)
    check("demo: нет цен без токена", plan["real_prices"] == [],)
    check("demo: есть summary", bool(plan["itinerary"]["summary"]))
    check("demo: есть дни", len(plan["itinerary"]["days"]) >= 1)
    check("demo: есть блок вылета", bool(plan["itinerary"]["departure"]["status_note"]))
    check("demo: есть виза", bool(plan["itinerary"]["visa"]))
    check("demo: есть практика", len(plan["itinerary"]["practical"]) >= 1)
    check("demo: ссылка на авиа не из закрытого ROV", "ROV" not in plan["links"]["flights"])
    check("demo: отели — список сервисов", isinstance(plan["links"]["hotels"], list) and len(plan["links"]["hotels"]) >= 1)
    check("demo: есть переезды с ссылками", len(plan["legs"]) >= 1 and all(l.get("url") for l in plan["legs"]))

    plan["real_prices"] = [{"price": 14500, "airline": "S7", "depart_date": "2026-11-10"}]
    chunks = render_plan(plan)
    text = "\n".join(chunks)
    check("рендер: блок цен", "Реальные цены" in text)
    check("рендер: блок вылета", "Вылет" in text)
    check("рендер: блок маршрута", "Как долететь" in text)
    check("рендер: блок транспорта по маршруту", "Полный маршрут" in text and "выбрать рейс и места" in text)
    check("рендер: практика", "На месте" in text)
    check("рендер: чек-лист", "Чек-лист" in text)
    check("рендер: ссылки", "Авиабилеты" in text)
    check("рендер: длина < лимит", all(len(c) <= 4096 for c in chunks))
    check("рендер: html-экранирование", "<script>" not in render_plan(
        {**plan, "itinerary": {**plan["itinerary"], "summary": "<script>alert(1)</script>"}})[0])


async def main() -> None:
    test_dates()
    test_prices_parsing()
    await test_iata()
    await test_affiliate()
    await test_airport_context()
    test_validation()
    await test_plan()
    print(f"\nИТОГО: {PASS} passed, {FAIL} failed, {SKIP} skipped")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())