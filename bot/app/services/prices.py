"""Реальные цены на авиабилеты через Travelpayouts (Aviasales) API.

- IATA-коды городов резолвим из публичных датасетов travelpayouts
  (airports.json/cities.json), кэшируются локально.
- Поиск: endpoint /v1/prices/cheap (+ /v1/prices/monthly как fallback).
- Если токен не задан / произошла ошибка — возвращаем пустой список,
  бот продолжает работать в режиме ссылок.
"""

import json
import logging
import re
from pathlib import Path
from tempfile import gettempdir

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

AIRPORTS_URL = "https://api.travelpayouts.com/data/ru/airports.json"
CITIES_URL = "https://api.travelpayouts.com/data/ru/cities.json"
SEARCH_URL = "http://api.travelpayouts.com/v1/prices/cheap"
MONTHLY_URL = "http://api.travelpayouts.com/v1/prices/monthly"

_airports_index: dict[str, dict] | None = None
_cities_index: dict[str, dict] | None = None


def _cache_path(name: str) -> Path:
    base = Path(settings.cache_dir) if settings.cache_dir else Path(gettempdir())
    return base / name


def _norm(value: str) -> str:
    return re.sub(r"[\s\-.,()«»]+", "", str(value).lower())


# Курорты без собственного IATA: резолвим в ближайший аэропорт.
ALIASES = {
    "кемер": "AYT",
    "алания": "GZP",
    "белек": "AYT",
    "сиде": "AYT",
    "мармарис": "DLM",
    "бодрум": "BJV",
    "памуккале": "DNZ",
}


async def _load_dataset(url: str, fname: str) -> list:
    path = _cache_path(fname)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        return resp.json()


async def _load_indexes() -> None:
    global _airports_index, _cities_index
    if _airports_index is not None and _cities_index is not None:
        return
    airports: dict[str, dict] = {}
    try:
        for item in await _load_dataset(AIRPORTS_URL, "tp_airports.json"):
            if item.get("is_active") is False:
                continue
            name = _norm(item.get("name", ""))
            if name:
                airports.setdefault(name, item)
    except Exception as exc:  # noqa: BLE001
        logger.warning("airports dataset недоступен: %s", exc)
    cities: dict[str, dict] = {}
    try:
        for item in await _load_dataset(CITIES_URL, "tp_cities.json"):
            name = _norm(item.get("name", ""))
            if name:
                cities.setdefault(name, item)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cities dataset недоступен: %s", exc)
    _airports_index, _cities_index = airports, cities


async def resolve_iata(query: str) -> str | None:
    """Возвращает IATA-код города/аэропорта по названию или None."""
    await _load_indexes()
    tokens = [_norm(t) for t in re.split(r"[,;]", query) if _norm(t)]

    for token in tokens:
        if token in ALIASES:
            return ALIASES[token]
        match = (_cities_index or {}).get(token)
        if match and match.get("code"):
            return match["code"]
        match = (_airports_index or {}).get(token)
        if match and match.get("code"):
            return match["code"]

    logger.info("IATA не найдено для: %s", query)
    return None


def _parse_cheap(data: dict, limit: int = 5) -> list[dict]:
    out: list[dict] = []
    for _origin, dest_map in (data or {}).items():
        for _dest, date_map in dest_map.items():
            for date_key, raw in date_map.items():
                if not isinstance(raw, dict) or not raw.get("price"):
                    continue
                out.append(
                    {
                        "price": int(raw["price"]),
                        "airline": raw.get("airline"),
                        "flight_number": raw.get("flight_number"),
                        "depart_date": (raw.get("departure_at") or date_key)[:10],
                        "return_date": (raw.get("return_at") or "")[:10],
                    }
                )
    out.sort(key=lambda x: x["price"])
    return out[:limit]


def _parse_monthly(data: dict, limit: int = 5) -> list[dict]:
    out: list[dict] = []
    for _origin, dest_map in (data or {}).items():
        for _dest, tickets in dest_map.items():
            if not isinstance(tickets, list):
                continue
            for raw in tickets:
                if not isinstance(raw, dict) or not raw.get("price"):
                    continue
                out.append(
                    {
                        "price": int(raw["price"]),
                        "airline": raw.get("airline"),
                        "flight_number": raw.get("flight_number"),
                        "depart_date": raw.get("depart_date", "")[:10],
                        "return_date": raw.get("return_date", "")[:10],
                    }
                )
    out.sort(key=lambda x: x["price"])
    return out[:limit]


async def _fetch(url: str, params: dict) -> dict:
    params.update({"currency": "RUB", "token": settings.travelpayouts_token})
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


async def search_cheap(origin: str, dest: str, depart: str, ret: str, limit: int = 5) -> list[dict]:
    data = await _fetch(
        SEARCH_URL,
        {"origin": origin, "destination": dest, "depart_date": depart, "return_date": ret, "limit": limit},
    )
    return _parse_cheap((data or {}).get("data") or {})


async def search_monthly(origin: str, dest: str, month: str, limit: int = 5) -> list[dict]:
    data = await _fetch(MONTHLY_URL, {"origin": origin, "destination": dest, "month": month})
    return _parse_monthly((data or {}).get("data") or {})


async def get_best_flights(origin: str, destination: str, depart: str, ret: str) -> list[dict]:
    if not settings.travelpayouts_token:
        return []
    try:
        origin_iata = await resolve_iata(origin)
        dest_iata = await resolve_iata(destination)
        if not origin_iata or not dest_iata:
            logger.info("IATA не найдены, пропускаем цены: %s / %s", origin, destination)
            return []
        tickets = await search_cheap(origin_iata, dest_iata, depart, ret)
        if tickets:
            return tickets
        month = depart[:7]
        return await search_monthly(origin_iata, dest_iata, month)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ошибка получения цен: %s", exc)
        return []