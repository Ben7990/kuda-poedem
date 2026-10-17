"""Оркестратор: собирает итоговый план из LLM-маршрута и партнёрских ссылок."""

import asyncio
import logging
import re
from datetime import datetime, timedelta

from app.services import affiliate, schedule
from app.services.llm import demo_itinerary, generate_itinerary

logger = logging.getLogger(__name__)

# Готовые примеры для демо-плана: показываем полный маршрут ДРУГОГО направления,
# чтобы продемонстрировать формат, но не отдавать персональный план бесплатно.
DEMO_SAMPLES = [
    {"destination": "Стамбул, Турция", "origin": "Москва", "format": "culture", "days": 5, "budget_rub": 180000},
    {"destination": "Дубай, ОАЭ", "origin": "Москва", "format": "beach", "days": 5, "budget_rub": 250000},
    {"destination": "Рим, Италия", "origin": "Москва", "format": "culture", "days": 5, "budget_rub": 220000},
]


def _pick_demo_sample(user_destination: str | None) -> dict:
    """Выбирает пример, направление которого не совпадает с запросом пользователя."""
    wanted = affiliate._city_name(user_destination or "").lower()
    for sample in DEMO_SAMPLES:
        if wanted and wanted in affiliate._city_name(sample["destination"]).lower():
            continue
        return sample
    return DEMO_SAMPLES[0]

# Сопоставление режима переезда с типом поиска в Яндекс.Расписаниях.
_SCHEDULE_KIND = {
    "flight": "plane",
    "train": "train",
    "rail": "train",
    "поезд": "train",
    "bus": "bus",
    "автобус": "bus",
    "ferry": "ferry",
    "паром": "ferry",
}
_MAX_SCHEDULE_LEGS = 8


async def _enrich_legs_with_schedule(legs: list[dict], travelers: int) -> None:
    """Добавляет к переездам реальные рейсы (номер, время вылета/прилёта)."""
    kind_for = _SCHEDULE_KIND

    async def one(leg: dict) -> None:
        kind = kind_for.get(leg.get("mode", ""))
        if not kind or not leg.get("date"):
            return
        options = await schedule.search_schedule(
            kind, leg.get("from", ""), leg.get("to", ""), leg["date"], limit=5
        )
        if options:
            leg["options"] = options
            leg["best"] = options[0]

    await asyncio.gather(*(one(leg) for leg in legs[:_MAX_SCHEDULE_LEGS]))

DATE_RE = re.compile(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})")
DATE_SPLIT_RE = re.compile(r"\s*(?:-|–|—|по|до|с|\.\.\.|\u2026)\s*", re.IGNORECASE)
FORMAT_LABELS = {
    "business": "деловая поездка",
    "blogger": "тревел-блогерство",
    "beach": "отдых у моря",
    "culture": "культура и экскурсии",
    "extreme": "экстрим",
}


def parse_dates(text: str) -> tuple[str, str] | None:
    """'10.11.2026 - 20.11.2026' -> ('2026-11-10', '2026-11-20')."""
    found = DATE_RE.findall(text)
    if len(found) < 2:
        return None
    result = []
    for day, month, year in found[:2]:
        if len(year) == 2:
            year = "20" + year
        try:
            result.append(datetime(int(year), int(month), int(day)).date().isoformat())
        except ValueError:
            return None
    if result[0] > result[1]:
        return None
    return result[0], result[1]


def parse_budget(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    value = int(digits)
    return value if value > 0 else None


def format_label(key: str) -> str:
    return FORMAT_LABELS.get(key, key)


def build_params(state_data: dict) -> dict:
    return {
        "destination": state_data["destination"],
        "origin": state_data["origin"],
        "date_start": state_data["date_start"],
        "date_end": state_data["date_end"],
        "format": format_label(state_data["format"]),
        "budget_rub": state_data["budget_rub"],
        "travelers": state_data.get("travelers", 2),
    }


async def build_plan(state_data: dict, demo: bool = False) -> dict:
    from app.services import prices

    user_destination = state_data.get("destination")
    if demo:
        # Демо показывает полный маршрут ДРУГОГО направления.
        sample = _pick_demo_sample(user_destination)
        start = (datetime.now().date() + timedelta(days=30))
        end = start + timedelta(days=sample["days"] - 1)
        state_data = {
            **state_data,
            "destination": sample["destination"],
            "origin": sample["origin"],
            "date_start": start.isoformat(),
            "date_end": end.isoformat(),
            "format": sample["format"],
            "budget_rub": sample["budget_rub"],
        }

    params = build_params(state_data)
    ctx = await prices.travel_context(params)
    itinerary = demo_itinerary(params, ctx) if demo else await generate_itinerary(params, ctx)

    dep_iata = itinerary.get("dep_iata") or (((itinerary.get("departure") or {}).get("chosen")) or {}).get("iata")
    if not dep_iata and ctx.get("alternatives"):
        dep_iata = ctx["alternatives"][0].get("code")
    dest_iata = itinerary.get("dest_iata") or next(
        (d.get("code") for d in (ctx.get("dest_airports") or []) if d.get("code")), None
    )

    # Если пункт назначения — страна, для отелей/экскурсий берём её главный город.
    dest_city = None
    if await prices.resolve_country(params["destination"]):
        dest_city = next(
            (d.get("city") for d in (ctx.get("dest_airports") or []) if d.get("city")), None
        )

    links = await affiliate.all_links(
        origin=params["origin"],
        destination=params["destination"],
        date_start=params["date_start"],
        date_end=params["date_end"],
        travelers=params["travelers"],
        origin_iata=dep_iata,
        dest_iata=dest_iata,
        destination_city=dest_city,
    )
    legs = affiliate.legs_with_links(itinerary, params["travelers"])
    await _enrich_legs_with_schedule(legs, params["travelers"])
    real_prices: list[dict] = []
    if not demo:
        real_prices = await prices.get_best_flights(
            origin=params["origin"],
            destination=params["destination"],
            depart=params["date_start"],
            ret=params["date_end"],
        )
    return {
        "params": params,
        "itinerary": itinerary,
        "links": links,
        "legs": legs,
        "real_prices": real_prices,
        "is_demo": demo,
        "demo_destination": user_destination if demo else None,
    }
