"""Достопримечательности рядом с точкой — из Wikidata SPARQL (бесплатно, без ключей).

Возвращаем реальные названия объектов (музеи, памятники, крепости, храмы и т.п.)
рядом с городом, отсортированные по известности (число языковых разделов
Википедии). Данные используются как ground-truth в промпте LLM, чтобы маршрут
содержал настоящие места, а не выдуманные «обзорные экскурсии».

Результаты кэшируются на диск (по координатам), поэтому повторные запросы
не бьют по Wikidata.
"""

import json
import logging
import re
import time
from pathlib import Path
from tempfile import gettempdir

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SPARQL_URL = "https://query.wikidata.org/sparql"
CACHE_TTL_SECONDS = 30 * 24 * 3600  # 30 дней

# Служебные объекты, которые не являются достопримечательностями.
_SKIP_TITLE_RE = re.compile(
    r"(аэропорт|вокзал|станция|депо|метро|автовокзал|airport|railway station)",
    re.IGNORECASE,
)

# Типы объектов-достопримечательностей (Wikidata QID).
ATTRACTION_TYPES = (
    "Q570116",   # tourist attraction
    "Q33506",    # museum
    "Q207694",   # art museum
    "Q4989906",  # monument
    "Q839954",   # archaeological site
    "Q23413",    # castle
    "Q16970",    # church building
    "Q44613",    # monastery
    "Q473972",   # protected area
)

_QUERY_TEMPLATE = """
SELECT ?item ?itemLabel ?sl WHERE {
  SERVICE wikibase:around {
    ?item wdt:P625 ?coord .
    bd:serviceParam wikibase:center "Point(__LON__ __LAT__)"^^geo:wktLiteral .
    bd:serviceParam wikibase:radius "__RADIUS__" .
  }
  ?item wdt:P31/wdt:P279* ?type .
  VALUES ?type { __TYPES__ }
  ?item wikibase:sitelinks ?sl .
  FILTER(?sl >= 1)
  SERVICE wikibase:label { bd:serviceParam wikibase:language "ru,en". }
}
ORDER BY DESC(?sl)
LIMIT __LIMIT__
"""


def _cache_path(lat: float, lon: float, radius_km: int) -> Path:
    base = Path(settings.cache_dir) if settings.cache_dir else Path(gettempdir())
    name = f"attr_{lat:.3f}_{lon:.3f}_{radius_km}.json"
    return base / name


def _read_cache(path: Path) -> list[dict] | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


async def nearby_attractions(
    lat: float, lon: float, radius_km: int = 15, limit: int = 30
) -> list[dict]:
    """Список реальных достопримечательностей рядом: [{"title","popularity"}].

    При любой ошибке возвращает пустой список — бот продолжает работу.
    """
    if lat is None or lon is None:
        return []
    cache = _cache_path(lat, lon, radius_km)
    cached = _read_cache(cache)
    if cached is not None:
        return cached

    types = " ".join(f"wd:{t}" for t in sorted(set(ATTRACTION_TYPES)))
    query = (
        _QUERY_TEMPLATE.replace("__LON__", str(round(lon, 5)))
        .replace("__LAT__", str(round(lat, 5)))
        .replace("__RADIUS__", str(int(radius_km)))
        .replace("__TYPES__", types)
        .replace("__LIMIT__", str(limit))
    )
    try:
        async with httpx.AsyncClient(
            timeout=40, headers={"User-Agent": "kuda-poedem-bot/1.0 (travel planner)"}
        ) as client:
            resp = await client.get(
                SPARQL_URL, params={"query": query, "format": "json"}
            )
            resp.raise_for_status()
            rows = resp.json().get("results", {}).get("bindings", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wikidata attractions недоступен: %s", exc)
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        title = (row.get("itemLabel") or {}).get("value", "").strip()
        # отбрасываем безымянное и служебные QID-подписи
        if not title or title.startswith("Q") and title[1:].isdigit():
            continue
        if _SKIP_TITLE_RE.search(title):
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            popularity = int((row.get("sl") or {}).get("value", "0"))
        except (TypeError, ValueError):
            popularity = 0
        out.append({"title": title, "popularity": popularity})

    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return out
