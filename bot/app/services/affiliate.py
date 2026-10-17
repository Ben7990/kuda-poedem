"""Генерация партнёрских/поисковых ссылок.

Проверенные рабочие форматы (октябрь 2026):
- Авиа: https://www.aviasales.ru/search/{ORIGIN}{ДДММ}{DEST}{ДДММ}{pax}
- Экскурсии: https://experience.tripster.ru/search/?q={город}
- ЖД: https://www.tutu.ru/poezda/rasp.php?st1={код}&st2={код} (коды станций)
- Отели: https://ostrovok.ru/search/?query={город}&checkin=..&checkout=.. (виджет Островка)
"""

from datetime import datetime
from urllib.parse import quote, urlencode

import asyncio

from app.config import settings

# Коды станций (для Tutu rasp.php). Проверены: Краснодар-1, Сочи.
RAILWAY_STATION_CODES = {
    "краснодар": "110423",
    "краснодар1": "110423",
    "сочи": "106023",
    # Партнёры с кодом — добавить после проверки в Tutu.
}


def _station_code(city: str) -> str | None:
    norm = "".join(ch for ch in city.lower() if ch.isalnum())
    return RAILWAY_STATION_CODES.get(norm)


def _ddmm(iso_date: str) -> str:
    d = datetime.fromisoformat(iso_date)
    return f"{d.day:02d}{d.month:02d}"


def flights_link(origin_iata: str, dest_iata: str, date_start: str, date_end: str, travelers: int = 2) -> str:
    if origin_iata and dest_iata:
        path = f"{origin_iata}{_ddmm(date_start)}{dest_iata}{_ddmm(date_end)}{travelers}"
        base = f"https://www.aviasales.ru/search/{path}"
        params = {}
        if settings.aviasales_marker:
            params["marker"] = settings.aviasales_marker
        return base if not params else base + "?" + urlencode(params)
    base = "https://www.aviasales.ru/"
    params = {"origin": origin_iata or "", "destination": dest_iata or ""}
    if settings.aviasales_marker:
        params["marker"] = settings.aviasales_marker
    return base + "?" + urlencode(params)


def hotels_link(destination: str, date_start: str, date_end: str, adults: int = 2) -> str:
    d1 = datetime.strptime(date_start, "%Y-%m-%d")
    d2 = datetime.strptime(date_end, "%Y-%m-%d")
    params = {
        "query": destination,
        "checkin": d1.strftime("%d.%m.%Y"),
        "checkout": d2.strftime("%d.%m.%Y"),
        "adults": adults,
    }
    if settings.ostrovok_partner_id:
        params["partner"] = settings.ostrovok_partner_id
    return "https://ostrovok.ru/search/?" + urlencode(params)


def tours_link(destination: str) -> str:
    params = {"q": destination}
    return "https://experience.tripster.ru/search/?" + urlencode(params)


def trains_link(origin: str, destination: str, date_start: str) -> str:
    from_code = _station_code(origin)
    to_code = _station_code(destination)
    if from_code and to_code:
        params = {"st1": from_code, "st2": to_code}
        d = datetime.strptime(date_start, "%Y-%m-%d")
        params["date"] = d.strftime("%d.%m.%Y")
        return "https://www.tutu.ru/poezda/rasp.php?" + urlencode(params)
    return "https://ticket.rzd.ru/"


async def all_links(
    origin: str,
    destination: str,
    date_start: str,
    date_end: str,
    travelers: int = 2,
    origin_iata: str | None = None,
    dest_iata: str | None = None,
) -> dict:
    """Собирает пакет ссылок-подборок.

    origin_iata/dest_iata — готовые коды (например, выбранный аэропорт вылета
    из плана); если не заданы — резолвятся по названиям.
    Ключи: flights, trains, hotels, tours — под render.py.
    """
    from app.services import prices

    if not origin_iata or not dest_iata:
        resolved_origin, resolved_dest = await asyncio.gather(
            prices.resolve_iata(origin),
            prices.resolve_iata(destination),
        )
        origin_iata = origin_iata or resolved_origin
        dest_iata = dest_iata or resolved_dest

    return {
        "flights": flights_link(origin_iata, dest_iata, date_start, date_end, travelers),
        "trains": trains_link(origin, destination, date_start),
        "hotels": hotels_link(destination, date_start, date_end, travelers),
        "tours": tours_link(destination),
    }


def search_hints() -> str:
    hints = []
    if settings.aviasales_marker:
        hints.append("авиа с партнёрским маркером ✓")
    else:
        hints.append("без партнёрского маркера")
    if settings.ostrovok_partner_id:
        hints.append("отели с ID партнёра ✓")
    else:
        hints.append("отели без ID партнёра")
    return "; ".join(hints)