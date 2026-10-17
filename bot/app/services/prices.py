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
_airports_list: list[dict] = []
_airports_by_code: dict[str, dict] = {}
_cities_by_code: dict[str, dict] = {}

# Крупные международные хабы: в предложениях «откуда вылететь» они идут первыми,
# затем — остальные аэропорты по удалённости (мелкие/сезонные в конце).
HUB_AIRPORTS = {
    # Россия
    "SVO", "DME", "VKO", "LED", "KRR", "AER", "MRV", "VOG", "KZN", "UFA", "KUF",
    "OVB", "KJA", "IKT", "VVO", "KHV", "PEE", "KGD", "CEK", "OMS", "BQS", "AAQ", "GDZ",
    # Ближнее зарубежье и популярные хабы
    "IST", "AYT", "ESB", "DXB", "AUH", "DOH", "EVN", "TBS", "GYD", "ALA", "NQZ",
    "TAS", "FRU", "DYU", "BEG", "HEL", "WAW", "PRG", "VIE", "BUD", "OTP", "SOF",
    "MUC", "FRA", "CDG", "AMS", "LHR", "ZRH", "FCO", "MXP", "MAD", "BCN", "LIS",
    "ATH", "LCA", "TGD", "PEK", "SHA", "CAN", "ICN", "HND", "NRT", "BKK", "HKT",
    "DEL", "BOM", "SGN", "HAN", "DAD", "KUL", "DPS", "MLE", "CMB", "KTM", "CAI",
    "HRG", "SSH", "TLV", "RAK", "CMN", "NBO", "ADD", "JNB", "GRU", "CUN", "JFK", "MIA",
}

# Аэропорты/коды городов РФ без регулярных рейсов (ограничения ФАВТ, 2022+).
# Обновлять вручную по актуальным ограничениям.
CLOSED_AIRPORTS = {
    "ROV": "Ростов-на-Дону (Платов) — регулярные рейсы приостановлены",
    "URRP": "Ростов-на-Дону (Платов) — регулярные рейсы приостановлены",
    "VOZ": "Воронеж — регулярные рейсы приостановлены",
    "EGO": "Белгород — регулярные рейсы приостановлены",
    "BZK": "Брянск — регулярные рейсы приостановлены",
    "URS": "Курск — регулярные рейсы приостановлены",
    "LPK": "Липецк — регулярные рейсы приостановлены",
}

# Главные международные аэропорты стран, куда из РФ нет прямых рейсов
# (ключи — нормализованные названия стран; значения — [(IATA, город)]).
COUNTRY_MAIN_AIRPORTS_RAW = {
    "бангладеш": [("DAC", "Дакка")],
    "буркинафасо": [("OUA", "Уагадугу")],
    "нигерия": [("LOS", "Лагос"), ("ABV", "Абуджа")],
    "гана": [("ACC", "Аккра")],
    "сенегал": [("DSS", "Дакар")],
    "кения": [("NBO", "Найроби")],
    "танзания": [("DAR", "Дар-эс-Салам"), ("ZNZ", "Занзибар")],
    "юар": [("JNB", "Йоханнесбург"), ("CPT", "Кейптаун")],
    "эфиопия": [("ADD", "Аддис-Абеба")],
    "марокко": [("RAK", "Марракеш"), ("CMN", "Касабланка")],
    "тунис": [("TUN", "Тунис")],
    "алжир": [("ALG", "Алжир")],
    "мальдивы": [("MLE", "Мале")],
    "шриланка": [("CMB", "Коломбо")],
    "непал": [("KTM", "Катманду")],
    "индия": [("DEL", "Дели"), ("BOM", "Мумбаи")],
    "таиланд": [("BKK", "Бангкок"), ("HKT", "Пхукет")],
    "вьетнам": [("SGN", "Хошимин"), ("HAN", "Ханой")],
    "индонезия": [("DPS", "Денпасар/Бали")],
    "малайзия": [("KUL", "Куала-Лумпур")],
    "филиппины": [("MNL", "Манила")],
    "китай": [("PEK", "Пекин"), ("SHA", "Шанхай")],
    "япония": [("HND", "Токио"), ("KIX", "Осака")],
    "южнаякорея": [("ICN", "Сеул")],
    "тайвань": [("TPE", "Тайбэй")],
    "монголия": [("ULN", "Улан-Батор")],
    "турция": [("IST", "Стамбул"), ("AYT", "Анталья")],
    "египет": [("CAI", "Каир"), ("HRG", "Хургада"), ("SSH", "Шарм-эль-Шейх")],
    "оаэ": [("DXB", "Дубай"), ("AUH", "Абу-Даби")],
    "саудовскаяаравия": [("RUH", "Эр-Рияд"), ("JED", "Джидда")],
    "израиль": [("TLV", "Тель-Авив")],
    "грузия": [("TBS", "Тбилиси"), ("BUS", "Батуми")],
    "армения": [("EVN", "Ереван")],
    "азербайджан": [("GYD", "Баку")],
    "казахстан": [("ALA", "Алматы"), ("NQZ", "Астана"), ("SCO", "Актау")],
    "узбекистан": [("TAS", "Ташкент")],
    "киргизия": [("FRU", "Бишкек")],
    "таджикистан": [("DYU", "Душанбе")],
    "сербия": [("BEG", "Белград")],
    "черногория": [("TGD", "Подгорица")],
    "кипр": [("LCA", "Ларнака"), ("PFO", "Пафос")],
    "гречесия": [("ATH", "Афины")],
    "испания": [("MAD", "Мадрид"), ("BCN", "Барселона")],
    "италия": [("FCO", "Рим"), ("MXP", "Милан")],
    "франция": [("CDG", "Париж")],
    "германия": [("FRA", "Франкфурт"), ("MUC", "Мюнхен")],
    "великобритания": [("LHR", "Лондон")],
    "сша": [("JFK", "Нью-Йорк"), ("MIA", "Майами"), ("LAX", "Лос-Анджелес")],
    "мексика": [("CUN", "Канкун"), ("MEX", "Мехико")],
    "куба": [("HAV", "Гавана"), ("VRA", "Варадеро")],
    "доминикана": [("PUJ", "Пунта-Кана")],
    "бразилия": [("GRU", "Сан-Паулу")],
    "аргентина": [("EZE", "Буэнос-Айрес")],
    "перу": [("LIM", "Лима")],
    "колумбия": [("BOG", "Богота")],
    "чили": [("SCL", "Сантьяго")],
    "австралия": [("SYD", "Сидней"), ("MEL", "Мельбурн")],
    "новаязеландия": [("AKL", "Окленд")],
}
# Ключи уже нормализованы (без пробелов/дефисов) — см. _norm().
COUNTRY_MAIN_AIRPORTS = COUNTRY_MAIN_AIRPORTS_RAW


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
    global _airports_index, _cities_index, _airports_list, _airports_by_code, _cities_by_code
    if _airports_index is not None and _cities_index is not None:
        return
    airports: dict[str, dict] = {}
    by_code: dict[str, dict] = {}
    airport_list: list[dict] = []
    try:
        for item in await _load_dataset(AIRPORTS_URL, "tp_airports.json"):
            if item.get("is_active") is False:
                continue
            code = item.get("code")
            if not code:
                continue
            by_code.setdefault(code, item)
            if (item.get("type") or "airport") == "airport":
                airport_list.append(item)
            name = _norm(item.get("name", ""))
            if name:
                airports.setdefault(name, item)
    except Exception as exc:  # noqa: BLE001
        logger.warning("airports dataset недоступен: %s", exc)
    cities: dict[str, dict] = {}
    cities_by_code: dict[str, dict] = {}
    try:
        for item in await _load_dataset(CITIES_URL, "tp_cities.json"):
            code = item.get("code")
            if code:
                cities_by_code.setdefault(code, item)
            name = _norm(item.get("name", ""))
            if name:
                cities.setdefault(name, item)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cities dataset недоступен: %s", exc)
    _airports_index, _cities_index = airports, cities
    _airports_list, _airports_by_code, _cities_by_code = airport_list, by_code, cities_by_code


def _coords(item: dict | None) -> tuple[float, float] | None:
    if not item:
        return None
    coords = item.get("coordinates") or {}
    lat = coords.get("lat", item.get("lat"))
    lng = coords.get("lon", coords.get("lng", item.get("lon", item.get("lng"))))
    try:
        return float(lat), float(lng)
    except (TypeError, ValueError):
        return None


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    from math import asin, cos, radians, sin, sqrt

    lat1, lon1, lat2, lon2 = map(radians, (*a, *b))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return round(2 * 6371 * asin(sqrt(h)))


def is_closed(iata: str | None) -> bool:
    return bool(iata) and iata.upper() in CLOSED_AIRPORTS


async def _record_for(query: str) -> dict | None:
    """Находит запись города/аэропорта по запросу (через resolve_iata)."""
    await _load_indexes()
    code = await resolve_iata(query)
    if not code:
        return None
    return _cities_by_code.get(code) or _airports_by_code.get(code)


async def departure_alternatives(origin: str, limit: int = 4) -> list[dict]:
    """Ближайшие открытые аэропорты вылета (по прямой, км), без закрытых."""
    await _load_indexes()
    record = await _record_for(origin)
    if record is None:
        return []
    origin_code = record.get("code", "")
    origin_country = record.get("country_code")
    origin_coords = _coords(record)
    if origin_coords is None and record.get("city_code"):
        origin_coords = _coords(_cities_by_code.get(record["city_code"]) or _airports_by_code.get(record["city_code"]))
    if origin_coords is None:
        return []
    scored: list[tuple[int, int, int, float, dict]] = []
    for ap in _airports_list:
        code = ap.get("code", "")
        if not code or code == origin_code or ap.get("city_code") == origin_code:
            continue
        if ap.get("flightable") is False:
            continue
        if is_closed(code):
            continue
        coords = _coords(ap)
        if coords is None:
            continue
        km = _haversine_km(origin_coords, coords)
        is_hub = code in HUB_AIRPORTS
        same_country = ap.get("country_code") == origin_country
        main = bool(ap.get("main_airport"))
        scored.append((0 if is_hub else 1, 0 if same_country else 1, 0 if main else 1, km, ap))
    scored.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    out: list[dict] = []
    for _hub, _same, _main, km, ap in scored[:limit]:
        city = (_cities_by_code.get(ap.get("city_code")) or {}).get("name", "")
        out.append(
            {
                "code": ap["code"],
                "name": ap.get("name", ""),
                "city": city,
                "distance_km": km,
                "country_code": ap.get("country_code", ""),
            }
        )
    return out


async def destination_airports(destination: str) -> list[dict]:
    """Аэропорты пункта назначения: по городу/аэропорту или по стране."""
    await _load_indexes()
    out: list[dict] = []
    record = await _record_for(destination)
    if record is not None:
        code = record.get("code", "")
        if code in _airports_by_code:
            ap = _airports_by_code[code]
            city = (_cities_by_code.get(ap.get("city_code")) or {}).get("name", "")
            out.append({"code": code, "name": ap.get("name", ""), "city": city})
        for ap in _airports_list:
            if ap.get("city_code") == code and ap.get("code") != code:
                city = (_cities_by_code.get(code) or {}).get("name", "")
                out.append({"code": ap["code"], "name": ap.get("name", ""), "city": city})
        if out:
            return out
    pairs = COUNTRY_MAIN_AIRPORTS.get(_norm(destination), [])
    for iata, city in pairs:
        ap = _airports_by_code.get(iata)
        out.append({"code": iata, "name": (ap or {}).get("name", ""), "city": city})
    return out


async def travel_context(params: dict) -> dict:
    """Справочные данные для LLM: статус аэропорта вылета, альтернативы, прилёт."""
    await _load_indexes()
    origin_record = await _record_for(params["origin"])
    origin_code = (origin_record or {}).get("code")
    origin_airport = None
    if origin_record is not None:
        origin_airport = {
            "code": origin_code,
            "name": origin_record.get("name", ""),
            "city": (_cities_by_code.get(origin_code) or {}).get("name", "")
            or (origin_record.get("city_code") and (_cities_by_code.get(origin_record["city_code"]) or {}).get("name", ""))
            or "",
            "closed": is_closed(origin_code),
            "reason": CLOSED_AIRPORTS.get((origin_code or "").upper(), ""),
        }
    alternatives = await departure_alternatives(params["origin"])
    dest_airports = await destination_airports(params["destination"])
    return {
        "origin_label": params["origin"],
        "dest_label": params["destination"],
        "origin_airport": origin_airport,
        "alternatives": alternatives,
        "dest_airports": dest_airports,
    }


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