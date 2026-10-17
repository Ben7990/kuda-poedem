"""Генерация поисковых/партнёрских ссылок.

Проверенные рабочие форматы (октябрь 2026):
- Авиа: https://www.aviasales.ru/search/{ORIGIN}{ДДММ}{DEST}{ДДММ}{pax}
- Отели (Авиасейлс): https://www.aviasales.ru/hotels?query={город}&checkIn=..&checkOut=..
- Отели (Яндекс Путешествия): https://travel.yandex.ru/hotels/search/?query={город}&checkin=..&checkout=..
- Отели (Booking): https://www.booking.com/searchresults.html?ss={город}&checkin=..&checkout=..
- Отели (OneTwoTrip): https://www.onetwotrip.com/ru/hotels/
- Экскурсии: https://experience.tripster.ru/search/?q={город}
- ЖД: https://rasp.yandex.ru/search/train/?fromName=..&toName=..&when=ДД.ММ.ГГГГ
"""

from datetime import datetime
from urllib.parse import quote, urlencode

import asyncio

from app.config import settings

# Страны, между которыми имеет смысл показывать ЖД-ссылку
# (бывший СССР + Европа + Монголия/Китай). Для остальных — только авиа.
RAIL_COUNTRIES = {
    "RU", "BY", "UA", "MD", "KZ", "KG", "UZ", "TJ", "TM", "AM", "GE", "AZ",
    "MN", "CN", "FI", "EE", "LV", "LT", "PL", "SK", "CZ", "DE", "AT", "HU",
    "RO", "BG", "RS", "HR", "SI", "BA", "ME", "MK", "AL", "GR", "IT", "FR",
    "ES", "PT", "NL", "BE", "DK", "SE", "NO", "CH", "GB", "IE", "TR",
}


def _ddmm(iso_date: str) -> str:
    d = datetime.fromisoformat(iso_date)
    return f"{d.day:02d}{d.month:02d}"


def _city_name(text: str) -> str:
    """«Кемер, Турция» -> «Кемер»."""
    return (str(text).split(",")[0] or "").strip()


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


def hotels_links(destination: str, date_start: str, date_end: str, adults: int = 2) -> list[dict]:
    """Набор ссылок на подбор отелей в разных сервисах."""
    city = _city_name(destination)
    d1 = datetime.strptime(date_start, "%Y-%m-%d").strftime("%Y-%m-%d")
    d2 = datetime.strptime(date_end, "%Y-%m-%d").strftime("%Y-%m-%d")
    links: list[dict] = [
        {
            "label": "Авиасейлс Отели",
            "url": "https://www.aviasales.ru/hotels?"
            + urlencode({"query": city, "checkIn": d1, "checkOut": d2, "adults": adults}),
        },
        {
            "label": "Яндекс Путешествия",
            "url": "https://travel.yandex.ru/hotels/search/?"
            + urlencode({"query": city, "checkin": d1, "checkout": d2, "adults": adults}),
        },
        {
            "label": "Booking.com",
            "url": "https://www.booking.com/searchresults.html?"
            + urlencode(
                {
                    "ss": city,
                    "checkin": d1,
                    "checkout": d2,
                    "group_adults": adults,
                    "no_rooms": 1,
                    "group_children": 0,
                }
            ),
        },
        {
            "label": "OneTwoTrip",
            "url": "https://www.onetwotrip.com/ru/hotels/",
        },
    ]
    if settings.ostrovok_partner_id:
        links.append(
            {
                "label": "Ostrovok",
                "url": "https://ostrovok.ru/hotels/?" + urlencode({"query": city, "partner": settings.ostrovok_partner_id}),
            }
        )
    return links


def tours_links(destination: str) -> list[dict]:
    city = _city_name(destination)
    return [
        {
            "label": "Tripster (экскурсии)",
            "url": "https://experience.tripster.ru/search/?" + urlencode({"q": city}),
        },
    ]


def trains_link(
    origin: str,
    destination: str,
    date_start: str,
    origin_country: str | None = None,
    dest_country: str | None = None,
) -> str | None:
    """Ссылка на расписание поездов (Яндекс.Расписания) или None, если ЖД неуместен."""
    from_city = _city_name(origin)
    to_city = _city_name(destination)
    if not from_city or not to_city:
        return None
    # ЖД-ссылку показываем только если обе страны определены и железнодорожные.
    if not origin_country or not dest_country:
        return None
    if origin_country not in RAIL_COUNTRIES or dest_country not in RAIL_COUNTRIES:
        return None
    try:
        when = datetime.strptime(date_start, "%Y-%m-%d").strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        when = ""
    params = {"fromName": from_city, "toName": to_city}
    if when:
        params["when"] = when
    return "https://rasp.yandex.ru/search/train/?" + urlencode(params)


def _rasp_link(kind: str, origin: str, destination: str, date: str) -> str:
    params = {"fromName": _city_name(origin), "toName": _city_name(destination)}
    try:
        params["when"] = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        pass
    return f"https://rasp.yandex.ru/search/{kind}/?" + urlencode(params)


def flight_oneway_link(from_iata: str, to_iata: str, date: str, travelers: int = 2) -> str | None:
    if not from_iata or not to_iata:
        return None
    try:
        path = f"{from_iata}{_ddmm(date)}{to_iata}{travelers}"
    except (TypeError, ValueError):
        return None
    base = f"https://www.aviasales.ru/search/{path}"
    return base + ("?" + urlencode({"marker": settings.aviasales_marker}) if settings.aviasales_marker else "")


def _looks_like_iata(code: str | None) -> bool:
    return bool(code) and len(code) == 3 and code.isalpha() and code.isupper()


def leg_link(leg: dict, travelers: int = 2) -> str | None:
    """Ссылка «выбрать рейс» для одного переезда маршрута."""
    mode = str(leg.get("mode", "flight")).lower()
    origin = leg.get("from", "")
    destination = leg.get("to", "")
    date = leg.get("date", "")
    if mode == "flight":
        from_iata = (leg.get("from_iata") or "").strip().upper()
        to_iata = (leg.get("to_iata") or "").strip().upper()
        if _looks_like_iata(from_iata) and _looks_like_iata(to_iata):
            return flight_oneway_link(from_iata, to_iata, date, travelers)
        # без кодов — общий поиск по городам
        return "https://www.aviasales.ru/?" + urlencode({"origin_name": origin, "destination_name": destination})
    if mode in ("train", "rail", "поезд"):
        return _rasp_link("train", origin, destination, date)
    if mode in ("bus", "автобус"):
        return _rasp_link("bus", origin, destination, date)
    if mode in ("ferry", "паром"):
        return _rasp_link("train", origin, destination, date)
    if mode in ("car", "авто"):
        return (
            "https://yandex.ru/maps/?rtext="
            + quote(f"{origin}~{destination}")
            + "&rtt=auto"
        )
    return _rasp_link("train", origin, destination, date)


MODE_ICONS = {
    "flight": "✈️",
    "train": "🚆",
    "rail": "🚆",
    "поезд": "🚆",
    "bus": "🚌",
    "автобус": "🚌",
    "ferry": "⛴",
    "паром": "⛴",
    "car": "🚗",
    "авто": "🚗",
}


def legs_with_links(itinerary: dict, travelers: int = 2) -> list[dict]:
    """Список переездов с ссылкой на выбор рейса/расписание и места."""
    out: list[dict] = []
    for leg in itinerary.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        url = leg_link(leg, travelers)
        out.append(
            {
                "mode": str(leg.get("mode", "")).lower(),
                "icon": MODE_ICONS.get(str(leg.get("mode", "")).lower(), "🚏"),
                "from": leg.get("from", ""),
                "to": leg.get("to", ""),
                "date": leg.get("date", ""),
                "note": leg.get("note", ""),
                "url": url,
            }
        )
    return out


async def all_links(
    origin: str,
    destination: str,
    date_start: str,
    date_end: str,
    travelers: int = 2,
    origin_iata: str | None = None,
    dest_iata: str | None = None,
    destination_city: str | None = None,
) -> dict:
    """Собирает пакет ссылок-подборок.

    origin_iata/dest_iata — готовые коды (например, выбранный аэропорт вылета
    из плана); если не заданы — резолвятся по названиям.
    destination_city — конкретный город для отелей/экскурсий (если назначение —
    страна, сюда передаём главный город).
    Ключи: flights (str), hotels (list), tours (list), trains (str|None).
    """
    from app.services import prices

    if not origin_iata or not dest_iata:
        resolved_origin, resolved_dest = await asyncio.gather(
            prices.resolve_iata(origin),
            prices.resolve_iata(destination),
        )
        origin_iata = origin_iata or resolved_origin
        dest_iata = dest_iata or resolved_dest

    origin_country, dest_country = await asyncio.gather(
        prices.country_for(origin),
        prices.country_for(destination),
    )

    city_for_travel = destination_city or destination
    return {
        "flights": flights_link(origin_iata, dest_iata, date_start, date_end, travelers),
        "hotels": hotels_links(city_for_travel, date_start, date_end, travelers),
        "tours": tours_links(city_for_travel),
        "trains": trains_link(origin, destination, date_start, origin_country, dest_country),
    }


def search_hints() -> str:
    hints = []
    if settings.aviasales_marker:
        hints.append("авиа/отели с партнёрским маркером ✓")
    else:
        hints.append("без партнёрского маркера")
    if settings.ostrovok_partner_id:
        hints.append("отели Ostrovok с ID партнёра ✓")
    return "; ".join(hints)
