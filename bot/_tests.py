"""Автотесты логики (без Telegram). Запуск: py -X utf8 _tests.py"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.render import render_plan
from app.services import affiliate, planner, prices

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
    check("4 типа ссылок", set(links) == {"flights", "hotels", "tours", "trains"})
    check("ссылка авиа содержит домен", "aviasales.ru" in links["flights"])
    check("ссылка отелей содержит домен", "ostrovok.ru" in links["hotels"])
    check("URL корректно закодирован", "%D0%9A" in links["hotels"])


async def test_plan() -> None:
    print("[5] сборка плана (demo) и рендер")
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

    plan["real_prices"] = [{"price": 14500, "airline": "S7", "depart_date": "2026-11-10"}]
    chunks = render_plan(plan)
    text = "\n".join(chunks)
    check("рендер: блок цен", "Реальные цены" in text)
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
    await test_plan()
    print(f"\nИТОГО: {PASS} passed, {FAIL} failed, {SKIP} skipped")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())