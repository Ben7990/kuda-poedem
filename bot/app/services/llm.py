"""LLM-сервис: строит маршрут по параметрам поездки.

Основной путь — мировые провайдеры с OpenAI-совместимым API:
- OpenAI: https://api.openai.com/v1 (`gpt-4o-mini`);
- OpenRouter: https://openrouter.ai/api/v1 (есть бесплатные модели);
- Groq: https://api.groq.com/openai/v1 (бесплатный тир);
- DeepSeek: https://api.deepseek.com/v1 (`deepseek-chat`).

Запасной вариант для РФ — GigaChat (Сбер):
`gigachat_client_id` + `gigachat_client_secret`.

Выбор провайдера: `llm_provider=openai` (по умолчанию) — мировой OpenAI-
совместимый; `gigachat` — GigaChat; `auto` — GigaChat при наличии его ключей,
иначе OpenAI. Если ни одного ключа нет — включается демо-режим.
"""

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Ты — эксперт-тревел-планировщик, работающий с реальной географией, транспортом "
    "и визовыми режимами. Составляешь ДЕТАЛЬНЫЙ индивидуальный маршрут поездки.\n\n"
    "Отвечай СТРОГО одним валидным JSON без пояснений и markdown. Схема:\n"
    "{\n"
    '  "route": "нить маршрута целиком с IATA и транспортом, например: Ростов-на-Дону → '
    'поезд до Краснодара (KRR) → перелёт через Стамбул (IST) в Дакку (DAC) → ...",\n'
    '  "summary": "краткое резюме (2-3 предложения): почему выбрана такая логистика",\n'
    '  "departure": {\n'
    '    "status_note": "статус аэропорта вылета: открыт/закрыт/нет прямых рейсов '
    'и что делаем",\n'
    '    "options": [\n'
    '      {"city": "Краснодар", "iata": "KRR", "airport": "Пашковский", "ground": '
    '"поезд ~3 ч, ~1200 ₽", "note": "прямые рейсы в Стамбул/Анталью"}\n'
    "    ],\n"
    '    "chosen": {"city": "Краснодар", "iata": "KRR"}\n'
    "  },\n"
    '  "routing": {\n'
    '    "hubs": ["Стамбул (IST)", "Доха (DOH)"],\n'
    '    "airlines": ["Turkish Airlines", "Qatar Airways"],\n'
    '    "note": "прямого рейса нет, стыковка ~4-6 ч",\n'
    '    "approx_price_rub": 85000\n'
    "  },\n"
    '  "visa": "режим въезда для граждан РФ на даты поездки",\n'
    '  "dep_iata": "KRR",\n'
    '  "dest_iata": "ADD",\n'
    '  "practical": ["валюта и обмен", "страховка", "связь/SIM", "безопасность"],\n'
    '  "legs": [\n'
    '    {"mode": "train", "from": "Ростов-на-Дону", "to": "Краснодар", "date": "2026-11-10", '
    '"from_iata": "", "to_iata": "KRR", "note": "Ростов-Главный → Пашковский, удобнее дневной '
    'экспресс"},\n'
    '    {"mode": "flight", "from": "Краснодар", "to": "Стамбул", "date": "2026-11-10", '
    '"from_iata": "KRR", "to_iata": "IST", "note": "прямой рейс, точное время подставит система"}\n'
    "  ],\n"
    '  "days": [\n'
    "    {\n"
    '      "date": "2026-11-10",\n'
    '      "title": "конкретное название дня",\n'
    '      "overnight": "город ночёвки",\n'
    '      "items": [\n'
    '        {"time": "09:00", "activity": "КРЕКОНКРЕТНОЕ место: музей/крепость/рынок/'
    'пляж/ресторан с названием", "transport": "как добираемся + время в пути", '
    '"cost_rub": 1500, "tips": "совет"}\n'
    "      ]\n"
    "    }\n"
    "  ],\n"
    '  "checklist": ["вещь 1", "вещь 2"],\n'
    '  "budget_note": "как уложиться в бюджет, резерв",\n'
    '  "advice": "сезон, пограничные нюансы, что проверить перед поездкой"\n'
    "}\n\n"
    "ЖЁСТКИЕ ПРАВИЛА:\n"
    "1. ВСЕ даты диапазона: в days РОВНО столько объектов, сколько календарных дней "
    "поездки (по одному на день, дата в поле date). В дне 2-4 пункта.\n"
    "2. IATA-коды бери ТОЛЬКО из блока «ДАННЫЕ ПО АЭРОПОРТАМ» в сообщении пользователя. "
    "Ни в коем случае не выдумывай и не используй коды аэропортов, которых нет в этом "
    "блоке. Если нужного аэропорта нет в блоке — выбери из перечисленных ближайших "
    "альтернатив.\n"
    "3. Если аэропорт вылета закрыт или из него нет рейсов — блок departure ОБЯЗАТЕЛЕН: "
    "status_note объясняет ситуацию, options — 2-3 реальных альтернативы (город, IATA, "
    "название аэропорта) с наземной доставкой (поезд/авто: время в пути и ~цена в рублях), "
    "chosen — лучший вариант.\n"
    "4. Если прямых рейсов до пункта назначения нет (часто так для Африки/Южной Азии из "
    "РФ) — routing через реальные транзитные хабы с авиакомпаниями: Стамбул (IST), Доха "
    "(DOH), Дубай (DXB), Абу-Даби (AUH), Аддис-Абеба (ADD), Париж (CDG), Стамбул — и "
    "т.д.; укажи стыковку и ориентировочную цену туда-обратно на человека в рублях.\n"
    "5. АБСОЛЮТНЫЙ ЗАПРЕТ на шаблоны: нельзя писать «обзорная экскурсия», «прогулка по "
    "городу», «свободное время», «отдых на пляже» без конкретных названий мест. Каждый "
    "пункт — реальное место: музей, крепость, рынок, набережная, храм, пляж, ресторан — "
    "с названием. Для дневных пунктов используй названия ИЗ СПИСКА «РЕАЛЬНЫЕ "
    "ДОСТОПРИМЕЧАТЕЛЬНОСТИ РЯДОМ» в сообщении пользователя (не выдумывай свои).\n"
    "5а. Если пункт назначения — целая СТРАНА (например, «Бангладеш», «Таиланд»), "
    "построй маршрут по её главным городам/регионам (стойте 2-3 дня в каждом, ночёвки "
    "в поле overnight), а не в одном городе; аэропорты прилёта/вылета — из списка "
    "«Аэропорты пункта назначения».\n"
    "6. visa — режим въезда для граждан РФ на даты поездки (безвиз/виза по прилёту/e-visa/"
    "консульство, сроки, где оформлять). Если не уверен — так и напиши: «проверьте перед "
    "покупкой билетов».\n"
    "7. practical — 3-5 конкретных пунктов: валюта и где менять, страховка (обязательна "
    "для многих стран), связь/SIM, вода и еда, безопасность, транспортные приложения "
    "(Yandex Go/Bolt/InDrive), электрика 220В и переходники.\n"
    "8. cost_rub — реалистичные цены в рублях. Учитывай формат поездки, бюджет на "
    "человека и сезон.\n"
    "9. Все обязательные поля схемы (route, summary, departure, routing, visa, practical, "
    "legs, days, checklist, budget_note, advice) должны присутствовать. checklist — минимум 3 "
    "пункта.\n"
    "10. legs — это ПОЛНЫЙ список всех переездов между городами (включая наземный трансфер "
    "до аэропорта/вокзала, поезда, автобусы, перелёты, паромы), в хронологическом порядке, "
    "включая обратную дорогу домой. Для КАЖДОГО переезда: mode (train/flight/bus/car/ferry), "
    "from, to, date и, для перелётов, from_iata/to_iata строго из блока аэропортов. В note "
    "опиши ЧТО удобно (нужный вокзал/аэропорт, прямой или с пересадкой, станции пересадки), "
    "но НЕ придумывай точное время вылета и номера рейсов — их подставит система из реального "
    "расписания. Туда-обратно — отдельные legs.\n"
    "11. ЗАПРЕЩЕНО выдумывать точное время отправления/прилёта рейсов и поездов (и часы "
    "в пунктах days для транспорта) — реальные времена и номера рейсов система берёт из "
    "расписания Яндекс.Расписаний и показывает отдельным блоком. В days для пунктов-переездов "
    "пиши время приблизительно словами (например, «днём», «вечером») либо не указывай его, "
    "а в tips отсылай: «точное время — в блоке «Полный маршрут — транспорт»». Для обычных "
    "активностей (музей, обед) конкретное время можно указывать.\n"
    "12. Поле overnight у каждого дня — ОБЯЗАТЕЛЬНО конкретный город ночёвки. Если день "
    "целиком в пути и вы прибываете/убываете в разные дни, явно укажи город ночёвки "
    "(например, прибыли в Краснодар 10-го, вылет 11-го → ночёвка 10-го в Краснодаре), и "
    "добавь в days пункт про заселение в отель на этот город.\n"
)


def _ctx_block(ctx: dict | None) -> str:
    if not ctx:
        return ""
    lines = ["", "ДАННЫЕ ПО АЭРОПОРТАМ (используй ТОЛЬКО реальные коды отсюда):"]
    origin_airport = ctx.get("origin_airport")
    if origin_airport:
        status = "ЗАКРЫТ, регулярных рейсов нет" if origin_airport.get("closed") else "открыт"
        note = f" ({origin_airport['reason']})" if origin_airport.get("closed") else ""
        lines.append(
            f"- Вылет: {origin_airport.get('code', '?')} "
            f"{origin_airport.get('name', '')} {origin_airport.get('city', '')} — {status}{note}."
        )
    alternatives = ctx.get("alternatives") or []
    if alternatives:
        lines.append(
            "- Ближайшие аэропорты вылета: "
            + "; ".join(
                f"{a['code']} {a.get('city') or a.get('name', '')} ({a['distance_km']} км)"
                for a in alternatives
            )
            + "."
        )
    origin_airports = ctx.get("origin_airports") or []
    if origin_airports and not alternatives:
        lines.append(
            "- Аэропорты вылета (используй реальные коды): "
            + "; ".join(
                f"{a['code']} {a.get('city') or a.get('name', '')}".strip()
                for a in origin_airports[:6]
            )
            + "."
        )
    dest = ctx.get("dest_airports") or []
    if dest:
        shown = dest[:15]
        lines.append(
            f"- Аэропорты пункта назначения ({len(dest)} шт., используй реальные коды): "
            + "; ".join(
                f"{d['code']} {d.get('city', '')} {d.get('name', '')}".strip() for d in shown
            )
            + ("; др." if len(dest) > len(shown) else "")
            + "."
        )
    else:
        lines.append(
            f"- IATA для «{ctx.get('dest_label', '')}» не найден в справочнике — укажи главный "
            "международный аэропорт страны/города сам и явно названием в маршруте."
        )
    attractions = ctx.get("attractions") or []
    if attractions:
        lines.append(
            "- РЕАЛЬНЫЕ ДОСТОПРИМЕЧАТЕЛЬНОСТИ РЯДОМ (проверенный список из Wikidata — бери "
            "названия ТОЛЬКО отсюда, ничего не выдумывай): "
            + "; ".join(attractions[:25])
            + "."
        )
    return "\n".join(lines)


def build_user_prompt(params: dict, ctx: dict | None = None) -> str:
    date_start = datetime.fromisoformat(params["date_start"])
    date_end = datetime.fromisoformat(params["date_end"])
    dates = [date_start + timedelta(days=i) for i in range((date_end - date_start).days + 1)]
    days_list = ", ".join(d.strftime("%d.%m (%A)") for d in dates)
    return (
        f"Поездка: {params['destination']}\n"
        f"Откуда выезжаем: {params['origin']}\n"
        f"Даты: {params['date_start']} — {params['date_end']} "
        f"({len(dates)} календарных дней: {days_list})\n"
        f"Формат: {params['format']}\n"
        f"Бюджет: {params['budget_rub']} руб. на {params['travelers']} чел."
        f"{_ctx_block(ctx)}\n\n"
        f"В массиве days должно быть РОВНО {len(dates)} объектов — по одному на каждый "
        "из перечисленных дней, с датой каждого дня в поле date."
    )


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|```\s*$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


class OpenAILLM:
    name = "openai"

    async def chat(self, messages: list[dict]) -> str:
        url = settings.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.llm_model,
            "messages": messages,
            "temperature": 0.4,
            "max_tokens": 6000,
        }
        # Некоторые провайдеры (OpenRouter free-модели, Groq) не поддерживают
        # response_format/json_object — пробуем с ним, при 4xx повторяем без.
        for attempt, body in enumerate(
            (
                {**payload, "response_format": {"type": "json_object"}},
                payload,
            )
        ):
            try:
                resp = await self._post(url, headers, body)
                return resp["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 429:
                    raise  # rate-limit уходит на уровень generate_itinerary (backoff)
                if status in (400, 404, 422) and attempt == 0:
                    logger.info("response_format не поддержан, повторяю запрос без него (%s)", exc)
                    continue
                raise

    async def _post(self, url: str, headers: dict, payload: dict) -> dict:
        timeout = 90
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
            for _ in range(3):
                if response.status_code != 429:
                    break
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else 30.0
                logger.warning("LLM rate limit (429), жду %.0f с и повторяю", wait)
                await asyncio.sleep(wait)
                response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
        return response.json()


class GigaChatLLM:
    name = "gigachat"
    _token: str | None = None
    _expires_at: float = 0.0

    async def _access_token(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        cred = f"{settings.gigachat_client_id}:{settings.gigachat_client_secret}".encode()
        headers = {
            "Authorization": f"Basic {base64.b64encode(cred).decode()}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        # OAuth-хост использует самоподписанный сертификат — verify=False.
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            resp = await client.post(
                "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
                data="scope=GIGACHAT_API_PERS",
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        self._token = data["access_token"]
        self._expires_at = data.get("expires_at", time.time() + 1800)
        logger.info("Получен новый токен GigaChat")
        return self._token

    async def chat(self, messages: list[dict]) -> str:
        token = await self._access_token()
        payload = {"model": settings.gigachat_model, "messages": messages, "temperature": 0.7}
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def get_client() -> OpenAILLM | GigaChatLLM:
    provider = settings.llm_provider.lower()
    use_gigachat = provider == "gigachat" or (
        provider == "auto" and settings.gigachat_client_id and settings.gigachat_client_secret
    )
    return GigaChatLLM() if use_gigachat else OpenAILLM()


def _expected_days(params: dict) -> int:
    start = datetime.fromisoformat(params["date_start"])
    end = datetime.fromisoformat(params["date_end"])
    return (end - start).days + 1


def _validate_itinerary(it: dict, params: dict) -> str | None:
    """Возвращает описание проблемы схемы или None, если всё ок."""
    errors = []
    required_top = (
        "route",
        "summary",
        "departure",
        "routing",
        "visa",
        "practical",
        "legs",
        "days",
        "checklist",
        "budget_note",
        "advice",
    )
    for key in required_top:
        if key not in it:
            errors.append(f"отсутствует поле '{key}'")
    departure = it.get("departure")
    if isinstance(departure, dict):
        options = departure.get("options")
        if not isinstance(options, list) or not options:
            errors.append("в departure пуст options — перечисли 2-3 альтернативы вылета")
    legs = it.get("legs")
    if not isinstance(legs, list) or not legs:
        errors.append("массив legs пуст — перечисли все переезды маршрута")
    else:
        for i, leg in enumerate(legs):
            if not isinstance(leg, dict):
                errors.append(f"legs[{i}] не объект")
                continue
            for key in ("mode", "from", "to", "date"):
                if not leg.get(key):
                    errors.append(f"в legs[{i}] отсутствует '{key}'")
    days = it.get("days")
    if not isinstance(days, list) or not days:
        errors.append("массив days пуст")
    else:
        expected = _expected_days(params)
        if len(days) != expected:
            errors.append(f"в days {len(days)} объектов, а должно быть {expected}")
        for d in days:
            for key in ("date", "title", "overnight", "items"):
                if key not in d:
                    errors.append(f"в дне {d.get('date')} отсутствует '{key}'")
    if isinstance(it.get("checklist"), list) and len(it["checklist"]) < 3:
        errors.append("в checklist меньше 3 пунктов")
    if isinstance(it.get("practical"), list) and len(it["practical"]) < 1:
        errors.append("practical пуст — добавь 3-5 практических советов")
    return "; ".join(errors) if errors else None


async def generate_itinerary(params: dict, ctx: dict | None = None) -> dict:
    if not settings.llm_enabled:
        logger.info("LLM не настроен, используем демо-маршрут")
        return demo_itinerary(params, ctx)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(params, ctx)},
    ]
    client = get_client()
    for attempt in range(2):
        try:
            content = await client.chat(messages)
            itinerary = _extract_json(content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM (%s) ошибка: %s", client.name, exc)
            itinerary = None
        else:
            problem = _validate_itinerary(itinerary, params)
            if problem is None:
                return itinerary
            logger.info("LLM (%s): план не прошёл проверку: %s", client.name, problem)
            # Даём оконному лимиту токенов восстановиться перед повторным запросом.
            await asyncio.sleep(45)
            messages = messages + [{
                "role": "user",
                "content": (
                    f"Твой предыдущий ответ не прошёл проверку: {problem}. "
                    "Исправь маршрут и верни полный JSON по исходной схеме."
                ),
            }]
    logger.warning("LLM не смог пройти проверку схемы за 2 попытки, отдаю лучший вариант")
    return itinerary if isinstance(itinerary, dict) else demo_itinerary(params, ctx)


def _attraction_picks(attractions: list[str], day_index: int, count: int) -> list[str]:
    """Реальные достопримечательности на день (дневной индекс без дня прилёта)."""
    if not attractions:
        return []
    start = day_index * count
    return attractions[start : start + count]


def demo_itinerary(params: dict, ctx: dict | None = None) -> dict:
    """Заглушка: даёт правдоподобный план на все дни (для теста без ключей)."""
    days = []
    start = datetime.fromisoformat(params["date_start"])
    end = datetime.fromisoformat(params["date_end"])
    total = (end - start).days + 1

    origin = params["origin"]
    destination = params["destination"]
    origin_airport = (ctx or {}).get("origin_airport") or {}
    alternatives = list((ctx or {}).get("alternatives") or [])[:3]
    attractions = list((ctx or {}).get("attractions") or [])

    origin_airports = list((ctx or {}).get("origin_airports") or [])
    need_ground = bool(origin_airport.get("closed")) and bool(alternatives)

    if need_ground:
        status_note = (
            f"⚠️ {origin_airport.get('city') or origin} "
            f"({origin_airport.get('code', '?')}) — {origin_airport.get('reason', 'аэропорт закрыт')}. "
            "Вылетаем из ближайшего открытого аэропорта."
        )
        dep_options = [
            {
                "city": a.get("city") or a.get("name", ""),
                "iata": a["code"],
                "airport": a.get("name", ""),
                "ground": f"наземный трансфер ~{a['distance_km']} км (поезд/авто — расписание уточните)",
                "note": "ближайший открытый аэропорт",
            }
            for a in alternatives
        ]
        chosen_city = alternatives[0].get("city") or alternatives[0].get("name", "")
        chosen_iata = alternatives[0]["code"]
    else:
        status_note = (
            f"Аэропорт вылета: {origin_airport.get('code', '?')} "
            f"{origin_airport.get('city', origin)}. Точные рейсы — по поиску авиабилетов."
        )
        dep_options = [
            {
                "city": a.get("city") or a.get("name", ""),
                "iata": a.get("code", ""),
                "airport": a.get("name", ""),
                "ground": "",
                "note": "аэропорт вылета",
            }
            for a in origin_airports[:4]
        ]
        chosen = origin_airports[0] if origin_airports else {}
        chosen_city = chosen.get("city") or origin_airport.get("city", origin) or origin
        chosen_iata = chosen.get("code") or origin_airport.get("code", "") or ""

    departure = {
        "status_note": status_note,
        "options": dep_options,
        "chosen": {"city": chosen_city, "iata": chosen_iata},
    }

    for i in range(total):
        date = start + timedelta(days=i)
        if i == 0:
            title = "Дорога и заселение"
            overnight = (departure["chosen"]["city"] or destination) if alternatives else destination
            items = [
                {
                    "time": "утро",
                    "activity": f"Выезд из {origin} до ближайшего открытого аэропорта",
                    "transport": "жд/авто — точное время в блоке «Полный маршрут — транспорт»",
                    "cost_rub": 2500,
                    "tips": "Закладывайте 3-4 ч запаса на стыковку и регистрацию",
                },
                {
                    "time": "вечер",
                    "activity": f"Заселение в отель в {overnight}, прогулка",
                    "transport": "трансфер / такси",
                    "cost_rub": 1500,
                    "tips": "Уточните ранний заезд заранее",
                },
            ]
        elif i == total - 1:
            title = "Сборы и выезд домой"
            overnight = origin
            items = [
                {
                    "time": "утро",
                    "activity": "Прогулка и сувениры",
                    "transport": "пешком / центр города",
                    "cost_rub": 0,
                    "tips": "Соберите вещи в первой половине дня",
                },
                {
                    "time": "день",
                    "activity": "Трансфер в аэропорт и перелёт домой",
                    "transport": "трансфер / такси — точное время в блоке «Полный маршрут — транспорт»",
                    "cost_rub": 2000,
                    "tips": "Будьте в аэропорту за 2-3 ч до рейса",
                },
            ]
        else:
            title = f"День {i + 1}: {params['format']}"
            overnight = destination
            picks = _attraction_picks(attractions, i - 1, 3)
            if picks:
                times = ("10:00", "14:00", "17:30")
                items = [
                    {
                        "time": times[idx] if idx < len(times) else "10:00",
                        "activity": f"Осмотр: {name}",
                        "transport": "пешком / общественный транспорт",
                        "cost_rub": 500,
                        "tips": "Проверьте часы работы и возьмите билеты заранее",
                    }
                    for idx, name in enumerate(picks)
                ]
            else:
                items = [
                    {
                        "time": "10:00",
                        "activity": f"Главные достопримечательности ({destination})",
                        "transport": "общественный транспорт / пешком",
                        "cost_rub": 1000,
                        "tips": "Возьмите удобную обувь и воду",
                    },
                    {
                        "time": "18:00",
                        "activity": "Вечерняя прогулка и ужин местной кухней",
                        "transport": "пешком",
                        "cost_rub": 1200,
                        "tips": "Попробуйте фирменные блюда региона",
                    },
                ]
        days.append(
            {
                "date": date.date().isoformat(),
                "title": title,
                "overnight": overnight,
                "items": items,
            }
        )

    dest_airports = list((ctx or {}).get("dest_airports") or [])
    dest_iata = next((d.get("code") for d in dest_airports if d.get("code")), "")
    chosen_iata = departure["chosen"]["iata"]
    chosen_city = departure["chosen"]["city"] or origin
    legs = []
    if need_ground and chosen_iata:
        legs.append(
            {
                "mode": "train",
                "from": origin,
                "to": chosen_city,
                "date": params["date_start"],
                "from_iata": "",
                "to_iata": chosen_iata,
                "note": "наземная доставка до аэропорта вылета; расписание и места — по ссылке",
            }
        )
    legs.append(
        {
            "mode": "flight",
            "from": chosen_city,
            "to": destination,
            "date": params["date_start"],
            "from_iata": chosen_iata,
            "to_iata": dest_iata,
            "note": "перелёт к месту; варианты, время и места — по ссылке",
        }
    )
    legs.append(
        {
            "mode": "flight",
            "from": destination,
            "to": chosen_city,
            "date": params["date_end"],
            "from_iata": dest_iata,
            "to_iata": chosen_iata,
            "note": "обратный перелёт домой",
        }
    )

    return {
        "route": (
            f"{origin} → "
            + (f"{departure['chosen']['city'] or 'ближайший аэропорт'} ({departure['chosen']['iata']}) → " if departure["chosen"]["iata"] else "")
            + f"{destination} → обратно."
        ),
        "summary": (
            f"Пример плана: {destination} из {origin} "
            f"({params['date_start']} — {params['date_end']}), "
            f"формат «{params['format']}». Точно такой же по структуре маршрут вы получите "
            "для своего направления — с реальными рейсами и временем, отелями, экскурсиями, "
            "бюджетом по дням и всеми ссылками."
        ),
        "departure": departure,
        "routing": {
            "hubs": ["Стамбул (IST)", "Доха (DOH)", "Дубай (DXB)"],
            "airlines": [],
            "note": "демо-режим: реальные транзитные хабы и авиакомпании укажет LLM-план",
            "approx_price_rub": None,
        },
        "visa": (
            "Демо-режим: проверьте визовый режим для граждан РФ в консульстве страны назначения "
            "(обычно загранпаспорт действует ≥6 мес. после въезда)."
        ),
        "dep_iata": chosen_iata,
        "dest_iata": dest_iata,
        "practical": [
            "Демо-режим: практические советы (валюта, связь, безопасность) добавит LLM-план.",
            "Оформите медицинскую страховку — для многих стран она обязательна.",
            "Проверьте актуальные ограничения на вылет из своего аэропорта.",
        ],
        "legs": legs,
        "days": days,
        "checklist": [
            "Загранпаспорт и виза (если нужна)",
            "Медицинская страховка",
            "Электронные копии документов",
            "Аптечка",
            "Зарядки и пауэрбанк",
        ],
        "budget_note": "Держите резерв ~10% бюджета на непредвиденное.",
        "advice": "Демо-режим: добавьте ключ LLM, чтобы получить реальный индивидуальный маршрут.",
    }