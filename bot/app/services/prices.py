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
COUNTRIES_SPARQL_URL = "https://query.wikidata.org/sparql"

_countries_query = """
SELECT ?country ?countryLabel ?iso WHERE {
  ?country wdt:P31/wdt:P279* wd:Q3624078 .
  ?country wdt:P297 ?iso .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "ru,en". }
}
LIMIT 400
"""

_airports_index: dict[str, dict] | None = None
_cities_index: dict[str, dict] | None = None
_airports_list: list[dict] = []
_airports_by_code: dict[str, dict] = {}
_cities_by_code: dict[str, dict] = {}
_country_iso_by_name: dict[str, str] | None = None
_country_name_by_iso: dict[str, str] = {}

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
MAIN_COUNTRY_AIRPORT_CODES = {
    code for pairs in COUNTRY_MAIN_AIRPORTS_RAW.values() for code, _city in pairs
}

# Резервный список ISO2 -> русское название (если Wikidata недоступен).
# Основной источник — _load_countries() (Wikidata, ~198 стран).
COUNTRIES_FALLBACK = {
    "RU": "Россия", "BY": "Беларусь", "KZ": "Казахстан", "UA": "Украина",
    "UZ": "Узбекистан", "KG": "Киргизия", "TJ": "Таджикистан", "TM": "Туркменистан",
    "AM": "Армения", "GE": "Грузия", "AZ": "Азербайджан", "MD": "Молдавия",
    "TR": "Турция", "EG": "Египет", "AE": "ОАЭ", "SA": "Саудовская Аравия",
    "QA": "Катар", "IL": "Израиль", "JO": "Иордания", "IR": "Иран",
    "TH": "Таиланд", "VN": "Вьетнам", "CN": "Китай", "JP": "Япония",
    "KR": "Южная Корея", "IN": "Индия", "ID": "Индонезия", "MY": "Малайзия",
    "SG": "Сингапур", "PH": "Филиппины", "LK": "Шри-Ланка", "NP": "Непал",
    "MV": "Мальдивы", "BD": "Бангладеш", "PK": "Пакистан", "MM": "Мьянма",
    "KH": "Камбоджа", "LA": "Лаос", "MN": "Монголия",
    "DE": "Германия", "FR": "Франция", "IT": "Италия", "ES": "Испания",
    "PT": "Португалия", "GB": "Великобритания", "IE": "Ирландия", "NL": "Нидерланды",
    "BE": "Бельгия", "CH": "Швейцария", "AT": "Австрия", "CZ": "Чехия",
    "SK": "Словакия", "PL": "Польша", "HU": "Венгрия", "RO": "Румыния",
    "BG": "Болгария", "GR": "Греция", "HR": "Хорватия", "SI": "Словения",
    "RS": "Сербия", "BA": "Босния и Герцеговина", "ME": "Черногория",
    "MK": "Северная Македония", "AL": "Албания", "CY": "Кипр", "MT": "Мальта",
    "FI": "Финляндия", "SE": "Швеция", "NO": "Норвегия", "DK": "Дания",
    "EE": "Эстония", "LV": "Латвия", "LT": "Литва", "IS": "Исландия",
    "MA": "Марокко", "TN": "Тунис", "DZ": "Алжир", "LY": "Ливия",
    "ET": "Эфиопия", "KE": "Кения", "TZ": "Танзания",
    "ZA": "ЮАР", "NG": "Нигерия", "GH": "Гана", "SN": "Сенегал",
    "CI": "Кот-д'Ивуар", "CM": "Камерун", "UG": "Уганда", "RW": "Руанда",
    "MU": "Маврикий", "SC": "Сейшелы", "MG": "Мадагаскар", "NA": "Намибия",
    "BW": "Ботсвана", "ZW": "Зимбабве", "ZM": "Замбия", "MZ": "Мозамбик",
    "AO": "Ангола", "BF": "Буркина-Фасо", "ML": "Мали", "NE": "Нигер",
    "TD": "Чад", "SD": "Судан", "SO": "Сомали", "ER": "Эритрея",
    "US": "США", "CA": "Канада", "MX": "Мексика", "CU": "Куба",
    "DO": "Доминикана", "JM": "Ямайка", "HT": "Гаити", "PA": "Панама",
    "CR": "Коста-Рика", "GT": "Гватемала", "BZ": "Белиз", "SV": "Сальвадор",
    "HN": "Гондурас", "NI": "Никарагуа", "CO": "Колумбия", "VE": "Венесуэла",
    "EC": "Эквадор", "PE": "Перу", "BO": "Боливия", "CL": "Чили",
    "AR": "Аргентина", "BR": "Бразилия", "UY": "Уругвай", "PY": "Парагвай",
    "AU": "Австралия", "NZ": "Новая Зеландия", "FJ": "Фиджи", "PG": "Папуа — Новая Гвинея",
}


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


# Правильное написание курортов (в ALIASES ключи — в нижнем регистре).
# "алания" исправляем на корректное "Аланья" (Alanya).
_RESORT_DISPLAY = {k: ("Аланья" if k == "алания" else k.capitalize()) for k in ALIASES}

_VOWELS = "аеёиоуыэюя"
_CONSONANTS = "бвгджзйклмнпрстфхцчшщ"


def _declension_keys(name: str) -> list[str]:
    """Частые падежные формы названия (normalized) для индекса автокоррекции.

    Позволяет понять "стамбуле"/"москву"/"дubaи" как "Стамбул"/"Москва"/"Дubaай".
    """
    out: list[str] = []
    low = name.lower()
    if low.endswith("ай"):
        out.append(_norm(name[:-2] + "и"))  # Дubaай -> Дubaи
    if low.endswith("й"):
        stem = name[:-1]
        for suf in ("и", "ю", "е", "ем"):
            out.append(_norm(stem + suf))
    elif low.endswith("а"):
        stem = name[:-1]
        for suf in ("ы", "е", "у", "ой", "ей", "ам", "ах"):
            out.append(_norm(stem + suf))
    elif low.endswith("я"):
        stem = name[:-1]
        for suf in ("и", "ю", "е", "ей"):
            out.append(_norm(stem + suf))
    elif low.endswith("ь"):
        stem = name[:-1]
        for suf in ("и", "ю", "ем"):
            out.append(_norm(stem + suf))
    elif low and low[-1] in _CONSONANTS:
        for suf in ("а", "у", "е", "ом", "ы", "и"):
            out.append(_norm(name + suf))
    return [k for k in out if k and k != _norm(name)]

_PLACE_RU_CACHE: dict[str, str] | None = None
_PLACE_EN_CACHE: dict[str, str] | None = None


async def _place_lookup() -> tuple[dict[str, str], dict[str, str]]:
    """Индексы для автокоррекции: normalized -> каноничное написание.

    Русский индекс строится из городов, аэропортов, стран и курортов;
    английский — из name_translations.en (маппится на русское название).
    """
    global _PLACE_RU_CACHE, _PLACE_EN_CACHE
    if _PLACE_RU_CACHE is not None and _PLACE_EN_CACHE is not None:
        return _PLACE_RU_CACHE, _PLACE_EN_CACHE
    await _load_indexes()
    await _load_countries()
    ru: dict[str, str] = {}
    en: dict[str, str] = {}
    # Проход 1: точные канонические названия.
    for rec in _cities_by_code.values():
        name = (rec.get("name") or "").strip()
        if not name:
            continue
        ru.setdefault(_norm(name), name)
        eng = ((rec.get("name_translations") or {}).get("en") or "").strip()
        if eng:
            en.setdefault(_norm(eng), name)
    for rec in _airports_by_code.values():
        name = (rec.get("name") or "").strip()
        if not name:
            continue
        ru.setdefault(_norm(name), name)
        eng = ((rec.get("name_translations") or {}).get("en") or "").strip()
        if eng:
            en.setdefault(_norm(eng), name)
    for name in (_country_name_by_iso or {}).values():
        if name:
            ru.setdefault(_norm(name), name)
    # Популярные сокращённые формы стран/городов.
    for k, v in [
        ("ОАЭ", "ОАЭ"),
        ("США", "США"),
        ("оаэ", "ОАЭ"),
        ("сша", "США"),
        ("usa", "США"),
        ("us", "США"),
        ("ростов", "Ростов-на-Дону"),
        ("спб", "Санкт-Петербург"),
        ("питер", "Санкт-Петербург"),
        ("петербург", "Санкт-Петербург"),
        ("мск", "Москва"),
    ]:
        ru.setdefault(_norm(k), v)
    for key, disp in _RESORT_DISPLAY.items():
        ru.setdefault(_norm(key), disp)
    # Популярные английские названия для корректного автодополнения.
    _EN_OVERRIDES = {
        "paris": "Париж",
        "london": "Лондон",
        "newyork": "Нью-Йорк",
        "ny": "Нью-Йорк",
        "losangeles": "Лос-Анджелес",
        "milan": "Милан",
        "rome": "Рим",
        "barcelona": "Барселона",
        "madrid": "Мадрид",
        "amsterdam": "Амстердам",
        "berlin": "Берлин",
        "prague": "Прага",
        "vienna": "Вена",
        "istanbul": "Стамбул",
        "dubai": "Дубай",
        "bangkok": "Бангкок",
        "singapore": "Сингапур",
        "tokyo": "Токио",
        "seoul": "Сеул",
        "beijing": "Пекин",
        "shanghai": "Шанхай",
        "moscow": "Москва",
        "stpetersburg": "Санкт-Петербург",
        "saintpetersburg": "Санкт-Петербург",
        "addisababa": "Аддис-Абеба",
    }
    for k, v in _EN_OVERRIDES.items():
        en[_norm(k)] = v
    # Англоязычные сокращения стран
    en[_norm("oae")] = "ОАЭ"
    en[_norm("usa")] = "США"
    en[_norm("uae")] = "ОАЭ"
    # Проход 2: падежные формы (уступают точным названиям из-за setdefault).
    for rec in _cities_by_code.values():
        name = (rec.get("name") or "").strip()
        for key in _declension_keys(name):
            ru.setdefault(key, name)
    for name in (_country_name_by_iso or {}).values():
        if name:
            for key in _declension_keys(name):
                ru.setdefault(key, name)
    _PLACE_RU_CACHE, _PLACE_EN_CACHE = ru, en
    return ru, en



async def canonicalize_place(query: str) -> str:
    """Исправляет регистр и опечатки в названии города/страны/курорта.

    Работает с русским и английским написанием, а также с формой без
    дефисов/пробелов. Нераспознанные места возвращаются как есть.
    """
    text = (query or "").strip()
    if not text:
        return text
    ru, en = await _place_lookup()
    parts = [p.strip() for p in re.split(r"[,;]", text) if p.strip()]
    if not parts:
        return text
    fixed: list[str] = []
    for part in parts:
        key = _norm(part)
        proper = ru.get(key) or en.get(key)
        if proper is None and len(key) >= 4:
            import difflib

            cand = difflib.get_close_matches(key, list(ru), n=1, cutoff=0.85)
            if cand:
                proper = ru[cand[0]]
        fixed.append(proper if proper else part)
    return ", ".join(fixed)


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


async def _load_countries() -> None:
    """ISO2 -> русское название страны (из Wikidata), плюс обратный индекс.

    Позволяет резолвить запросы вида «Бангладеш»/«Таиланд» в код страны и
    затем — во все её аэропорты. Кэшируется на диск; при сбое используем
    встроенный резервный набор (COUNTRIES_FALLBACK).
    """
    global _country_iso_by_name, _country_name_by_iso
    if _country_iso_by_name is not None:
        return
    name_by_iso: dict[str, str] = {}
    iso_by_name: dict[str, str] = {}
    data = None
    cache = _cache_path("countries.json")
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            data = None
    if data is None:
        try:
            import httpx

            async with httpx.AsyncClient(
                timeout=40, headers={"User-Agent": "kuda-poedem-bot/1.0 (travel planner)"}
            ) as client:
                resp = await client.get(
                    COUNTRIES_SPARQL_URL,
                    params={"query": _countries_query, "format": "json"},
                )
                resp.raise_for_status()
                rows = resp.json().get("results", {}).get("bindings", [])
            data = {
                (r.get("iso") or {}).get("value", "").upper(): (r.get("countryLabel") or {}).get("value", "")
                for r in rows
                if (r.get("iso") or {}).get("value")
            }
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("countries (Wikidata) недоступны: %s", exc)
            data = {}
    for iso, name in (data or {}).items():
        if iso and name:
            name_by_iso[iso] = name
            iso_by_name.setdefault(_norm(name), iso)
    for name, iso in COUNTRIES_FALLBACK.items():
        iso_by_name.setdefault(_norm(name), iso)
        name_by_iso.setdefault(iso, name)
    _country_name_by_iso, _country_iso_by_name = name_by_iso, iso_by_name


async def resolve_country(query: str) -> str | None:
    """Возвращает ISO2 страны по названию (рус/англ) или самому коду."""
    await _load_indexes()
    await _load_countries()
    tokens = [_norm(t) for t in re.split(r"[,;]", query) if _norm(t)]
    for token in tokens:
        if len(token) == 2 and token.upper() in (_country_name_by_iso or {}):
            return token.upper()
        iso = (_country_iso_by_name or {}).get(token)
        if iso:
            return iso
    return None


async def country_for(query: str) -> str | None:
    """ISO2 страны для города/аэропорта/страны: сначала по названию страны,
    затем по стране аэропорта/города из датасета."""
    iso = await resolve_country(query)
    if iso:
        return iso
    await _load_indexes()
    record = await _record_for(query)
    if record is not None:
        code = record.get("country_code")
        if code:
            return code
        cc = record.get("city_code") or record.get("code")
        ref = _cities_by_code.get(cc) or _airports_by_code.get(cc)
        return (ref or {}).get("country_code")
    return None


async def country_airports(query: str, limit: int = 20) -> list[dict]:
    """Все работающие аэропорты страны (по названию или ISO2).

    Сортировка: главный аэропорт страны -> крупные хабы -> прочие.
    """
    iso = await resolve_country(query)
    if not iso:
        return []
    await _load_indexes()
    scored: list[tuple[int, int, int, str, dict]] = []
    for ap in _airports_list:
        if ap.get("country_code") != iso:
            continue
        if ap.get("flightable") is False:
            continue
        if not _is_airport(ap):
            continue
        code = ap.get("code", "")
        if not code or is_closed(code):
            continue
        rank0 = 0 if code in MAIN_COUNTRY_AIRPORT_CODES else 1
        rank1 = 0 if ap.get("main_airport") else 1
        rank2 = 0 if code in HUB_AIRPORTS else 1
        scored.append((rank0, rank1, rank2, code, ap))
    scored.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    out: list[dict] = []
    for _r0, _r1, _r2, code, ap in scored[:limit]:
        city = (_cities_by_code.get(ap.get("city_code")) or {}).get("name", "")
        coords = _coords(ap)
        out.append(
            {
                "code": code,
                "name": ap.get("name", ""),
                "city": city,
                "lat": coords[0] if coords else None,
                "lon": coords[1] if coords else None,
            }
        )
    return out


def _is_airport(ap: dict) -> bool:
    """Отсеивает ж/д станции и автовокзалы (iata_type: railway/bus и т.п.)."""
    kind = str(ap.get("iata_type") or "airport").lower()
    return kind in ("", "airport")


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
        if not _is_airport(ap):
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
    """Аэропорты пункта назначения: по городу/аэропорту или по стране.

    Каждая запись: {"code", "name", "city", "lat", "lon"}.
    """
    await _load_indexes()
    out: list[dict] = []
    record = await _record_for(destination)
    if record is not None:
        code = record.get("code", "")
        if code in _airports_by_code:
            ap = _airports_by_code[code]
            city = (_cities_by_code.get(ap.get("city_code")) or {}).get("name", "")
            coords = _coords(ap) or _coords(_cities_by_code.get(ap.get("city_code")))
            out.append(
                {
                    "code": code,
                    "name": ap.get("name", ""),
                    "city": city,
                    "lat": coords[0] if coords else None,
                    "lon": coords[1] if coords else None,
                }
            )
        siblings = [
            ap
            for ap in _airports_list
            if ap.get("city_code") == code
            and ap.get("code") != code
            and ap.get("flightable") is not False
            and _is_airport(ap)
        ]
        siblings.sort(key=lambda ap: (0 if ap.get("main_airport") else 1, ap.get("code", "")))
        for ap in siblings:
            city = (_cities_by_code.get(code) or {}).get("name", "")
            coords = _coords(ap) or _coords(_cities_by_code.get(code))
            out.append(
                {
                    "code": ap["code"],
                    "name": ap.get("name", ""),
                    "city": city,
                    "lat": coords[0] if coords else None,
                    "lon": coords[1] if coords else None,
                }
            )
        if out:
            return out
    # Страна (все работающие аэропорты через Wikidata ISO2).
    country = await country_airports(destination)
    if country:
        return country
    # Резервный курируемый список главных аэропортов страны.
    for iata, city in COUNTRY_MAIN_AIRPORTS.get(_norm(destination), []):
        ap = _airports_by_code.get(iata)
        coords = _coords(ap)
        out.append(
            {
                "code": iata,
                "name": (ap or {}).get("name", ""),
                "city": city,
                "lat": coords[0] if coords else None,
                "lon": coords[1] if coords else None,
            }
        )
    return out


async def travel_context(params: dict) -> dict:
    """Справочные данные для LLM: статус аэропорта вылета, альтернативы, прилёт,
    реальные достопримечательности пункта назначения."""
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
    alternatives = await departure_alternatives(params["origin"]) if (origin_airport or {}).get("closed") else []
    origin_airports = await destination_airports(params["origin"])
    dest_airports = await destination_airports(params["destination"])

    attractions: list[str] = []
    spots = [d for d in dest_airports if d.get("lat") and d.get("lon")][:3]
    if spots:
        from app.services import attractions as attractions_service

        seen: set[str] = set()
        for sp in spots:
            if len(attractions) >= 40:
                break
            find = await attractions_service.nearby_attractions(sp["lat"], sp["lon"])
            for item in find:
                title = item["title"]
                if title.lower() in seen:
                    continue
                seen.add(title.lower())
                attractions.append(title)

    return {
        "origin_label": params["origin"],
        "dest_label": params["destination"],
        "origin_airport": origin_airport,
        "origin_airports": origin_airports,
        "alternatives": alternatives,
        "dest_airports": dest_airports,
        "attractions": attractions,
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