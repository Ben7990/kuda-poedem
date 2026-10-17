"""Оркестратор: собирает итоговый план из LLM-маршрута и партнёрских ссылок."""

import logging
import re
from datetime import datetime

from app.services import affiliate
from app.services.llm import demo_itinerary, generate_itinerary

logger = logging.getLogger(__name__)

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
    params = build_params(state_data)
    itinerary = demo_itinerary(params) if demo else await generate_itinerary(params)
    links = await affiliate.all_links(
        origin=params["origin"],
        destination=params["destination"],
        date_start=params["date_start"],
        date_end=params["date_end"],
    )
    real_prices: list[dict] = []
    if not demo:
        from app.services import prices

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
        "real_prices": real_prices,
    }
