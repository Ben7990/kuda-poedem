"""Реальное расписание рейсов/поездов из Яндекс.Расписаний (SSR JSON, без ключей).

Страница `/search/{kind}/` отдаёт серверный JSON со списком `segments`
(номер рейса, авиакомпания, локальные время вылета/прилёта, длительность).
Извлекаем его и используем как ground-truth, чтобы в маршруте были РЕАЛЬНЫЕ
времена, а не выдуманные LLM.

Результаты кэшируются на диск (короткий TTL — расписание меняется).
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from tempfile import gettempdir

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 6 * 3600
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_KIND_MAP = {
    "plane": "plane",
    "flight": "plane",
    "авиа": "plane",
    "train": "train",
    "rail": "train",
    "поезд": "train",
    "bus": "bus",
    "автобус": "bus",
    "ferry": "ferry",
    "паром": "ferry",
    "suburban": "suburban",
    "электричка": "suburban",
}

_lock = asyncio.Lock()
_last_call = 0.0

# Позволяет отключить сетевые обращения (используется в офлайн-тестах).
DISABLED = False


def _cache_path(kind: str, frm: str, to: str, date: str) -> Path:
    base = Path(settings.cache_dir) if settings.cache_dir else Path(gettempdir())
    safe = "".join(ch for ch in f"{kind}_{frm}_{to}_{date}" if ch.isalnum() or ch in "-_")[:120]
    return base / f"rasp_{safe}.json"


def _read_cache(path: Path) -> list[dict] | None:
    if not path.exists() or time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


def _extract_segments(html: str) -> list[dict]:
    marker = '"segments":['
    idx = html.find(marker)
    if idx < 0:
        return []
    decoder = json.JSONDecoder()
    pos = idx
    guard = 0
    while pos > 0 and guard < 4000:
        pos = html.rfind("{", 0, pos)
        if pos < 0:
            break
        guard += 1
        try:
            obj, _ = decoder.raw_decode(html[pos:])
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("segments"), list):
            return [s for s in obj["segments"] if isinstance(s, dict)]
    return []


def _hhmm(*values: str | None) -> str:
    for value in values:
        if value and len(value) >= 16 and value[10] in ("T", " "):
            return value[11:16]
    return ""


def _minutes(seconds) -> int | None:
    try:
        return int(seconds) // 60
    except (TypeError, ValueError):
        return None


def _company(seg: dict) -> str:
    company = seg.get("company")
    if isinstance(company, dict):
        for key in ("title", "name", "ufsTitle", "shortTitle"):
            value = company.get(key)
            if value:
                return str(value).strip()
        return ""
    if isinstance(company, str):
        return company.strip()
    thread = seg.get("thread") if isinstance(seg.get("thread"), dict) else {}
    return str(seg.get("carrier") or thread.get("title") or "").strip()


def _normalize(seg: dict) -> dict | None:
    number = str(seg.get("number") or seg.get("shortTitle") or "").strip()
    dep = _hhmm(seg.get("departureLocalDt"), seg.get("departure"))
    arr = _hhmm(seg.get("arrivalLocalDt"), seg.get("arrival"))
    if not dep and not arr:
        return None
    return {
        "number": number,
        "company": _company(seg),
        "route": str(seg.get("title") or "").replace("\xa0", " ").strip(),
        "dep": dep,
        "arr": arr,
        "duration_min": _minutes(seg.get("duration")),
    }


async def search_schedule(
    kind: str, from_name: str, to_name: str, date: str, limit: int = 6
) -> list[dict]:
    """Реальные рейсы/поезда from->to на дату: [{"number","company","dep","arr","duration_min"}].

    При любой ошибке (в т.ч. анти-бот) — пустой список.
    """
    kind = _KIND_MAP.get(str(kind).lower(), "plane")
    frm = (str(from_name).split(",")[0] or "").strip()
    to = (str(to_name).split(",")[0] or "").strip()
    if DISABLED or not frm or not to or not date:
        return []
    try:
        when = time.strftime("%d.%m.%Y", time.strptime(str(date)[:10], "%Y-%m-%d"))
    except ValueError:
        return []

    cache = _cache_path(kind, frm, to, date)
    cached = _read_cache(cache)
    if cached is not None:
        return cached[:limit]

    url = f"https://rasp.yandex.ru/search/{kind}/"
    params = {"fromName": frm, "toName": to, "when": when}
    global _last_call
    async with _lock:
        wait = 1.0 - (time.time() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(
                timeout=35,
                follow_redirects=True,
                headers={"User-Agent": _UA, "Accept-Language": "ru-RU,ru;q=0.9"},
            ) as client:
                resp = await client.get(url, params=params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Яндекс.Расписания недоступны (%s %s->%s): %s", kind, frm, to, exc)
            return []
        finally:
            _last_call = time.time()

    if resp.status_code != 200:
        logger.info("Яндекс.Расписания %s: HTTP %s", kind, resp.status_code)
        return []

    out: list[dict] = []
    seen: set[tuple] = set()
    for seg in _extract_segments(resp.text):
        item = _normalize(seg)
        if not item:
            continue
        key = (item["number"], item["dep"], item["arr"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    if not out:
        return []

    out.sort(key=lambda x: x.get("dep") or "99:99")
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return out[:limit]
