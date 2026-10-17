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
    "Ты — эксперт-тревел-планировщик. Составляешь ДЕТАЛЬНЫЙ маршрут поездки.\n"
    "Опирайся на реальную географию и транспорт: если из города отправления нет "
    "прямого рейса до пункта назначения — покажи связку ЖД/авто до ближайшего "
    "аэропорта, а затем перелёт, с учётом времени на стыковку.\n\n"
    "Отвечай СТРОГО одним валидным JSON без пояснений, markdown-разметки и кавычек "
    "вне JSON. Схема:\n"
    "{\n"
    '  "route": "Нить маршрута целиком, например: Ростов-на-Дону → жд до Краснодара '
    '(аэропорт Пашковский) → перелёт в Анталью → трансфер в Кемер → ... → обратно",\n'
    '  "summary": "краткое резюме поездки (2-3 предложения), включая почему так лучше",\n'
    '  "days": [\n'
    "    {\n"
    '      "date": "2026-11-10",\n'
    '      "title": "Перелёт и заселение",\n'
    '      "overnight": "город, где ночуем в этот день",\n'
    '      "items": [\n'
    '        {"time": "06:30", "activity": "что делаем (место, достопримечательность, '
    'экскурсия, кафе)", "transport": "как добираемся: такси/автобус/метро/аренда машины/пешком '
    '(укажите ориентировочное время в пути)", "cost_rub": 1500, "tips": "совет"}\n'
    "      ]\n"
    "    }\n"
    "  ],\n"
    '  "checklist": ["вещь 1", "вещь 2"],\n'
    '  "budget_note": "как уложиться в бюджет, резерв на непредвиденное",\n'
    '  "advice": "когда лучше ехать в этот сезон, что учесть, пограничные нюансы"\n'
    "}\n\n"
    "Правила:\n"
    "- Учитывай ВСЕ дни диапазона дат: количество объектов в массиве days ДОЛЖНО "
    "быть ровно равно числу календарных дней поездки — по одному на каждый день.\n"
    "- В каждом дне 2-4 пункта.\n"
    "- Если для дальнего выезда нужен ночлег — добавь в тот день поле overnight в другом городе "
    "и план возврата.\n"
    "- Указывай транспорт и время в пути; для самостоятельных поездок сравни с экскурсией.\n"
    "- Учитывай формат поездки, бюджет на человека и сезон.\n"
    "- ВСЕ обязательные поля схемы (route, summary, days, checklist, budget_note, advice) "
    "должны присутствовать; checklist — минимум 3 пункта."
)


def build_user_prompt(params: dict) -> str:
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
        f"Бюджет: {params['budget_rub']} руб. на {params['travelers']} чел.\n"
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
            "temperature": 0.7,
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
    required_top = ("route", "summary", "days", "checklist", "budget_note", "advice")
    for key in required_top:
        if key not in it:
            errors.append(f"отсутствует поле '{key}'")
    days = it.get("days")
    if not isinstance(days, list) or not days:
        errors.append("массив days пуст")
    else:
        expected = _expected_days(params)
        if len(days) != expected:
            errors.append(f"в days {len(days)} объектов, а должно быть {expected}")
        for d in days:
            for key in ("date", "title", "items"):
                if key not in d:
                    errors.append(f"в дне {d.get('date')} отсутствует '{key}'")
    if isinstance(it.get("checklist"), list) and len(it["checklist"]) < 3:
        errors.append("в checklist меньше 3 пунктов")
    return "; ".join(errors) if errors else None


async def generate_itinerary(params: dict) -> dict:
    if not settings.llm_enabled:
        logger.info("LLM не настроен, используем демо-маршрут")
        return demo_itinerary(params)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(params)},
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
    return itinerary if isinstance(itinerary, dict) else demo_itinerary(params)


def demo_itinerary(params: dict) -> dict:
    """Заглушка: даёт правдоподобный план на все дни (для теста без ключей)."""
    days = []
    start = datetime.fromisoformat(params["date_start"])
    end = datetime.fromisoformat(params["date_end"])
    total = (end - start).days + 1

    for i in range(total):
        date = start + timedelta(days=i)
        if i == 0:
            title = "Дорога и заселение"
            items = [
                {
                    "time": "07:00",
                    "activity": f"Выезд из {params['origin']} (жд/авто до ближайшего аэропорта)",
                    "transport": "жд до аэропорта",
                    "cost_rub": 2500,
                    "tips": "Закладывайте 3-4 ч запаса на стыковку и регистрацию",
                },
                {
                    "time": "15:00",
                    "activity": f"Поселение и прогулка по {params['destination']}",
                    "transport": "трансфер / такси",
                    "cost_rub": 1500,
                    "tips": "Уточните ранний заезд заранее",
                },
            ]
        elif i == total - 1:
            title = "Сборы и выезд домой"
            items = [
                {
                    "time": "10:00",
                    "activity": "Прогулка и сувениры",
                    "transport": "пешком / центр города",
                    "cost_rub": 0,
                    "tips": "Соберите вещи в первой половине дня",
                },
                {
                    "time": "16:00",
                    "activity": "Трансфер в аэропорт и перелёт домой",
                    "transport": "трансфер / такси",
                    "cost_rub": 2000,
                    "tips": "Будьте в аэропорту за 2-3 ч до рейса",
                },
            ]
        else:
            title = f"День {i + 1}: экскурсионная программа"
            items = [
                {
                    "time": "09:30",
                    "activity": "Обзорная экскурсия (исторический центр / главные достопримечательности)",
                    "transport": "общественный транспорт / пешком",
                    "cost_rub": 1000,
                    "tips": "Возьмите удобную обувь и воду",
                },
                {
                    "time": "14:00",
                    "activity": "Обед в местном кафе и прогулка",
                    "transport": "пешком",
                    "cost_rub": 1200,
                    "tips": "Пробуйте местную кухню",
                },
                {
                    "time": "18:00",
                    "activity": "Закат, фотосессия, свободное время",
                    "transport": "пешком",
                    "cost_rub": 0,
                    "tips": "Закладывайте время на возвращение отелем",
                },
            ]
        days.append(
            {
                "date": date.date().isoformat(),
                "title": title,
                "overnight": params["destination"],
                "items": items,
            }
        )

    return {
        "route": (
            f"{params['origin']} → ближайший аэропорт → {params['destination']} → обратно. "
            "Детальный маршрут и цены появятся после подключения LLM-ключа."
        ),
        "summary": (
            f"Демо-план: {params['destination']} из {params['origin']} "
            f"({params['date_start']} — {params['date_end']}), "
            f"формат «{params['format']}»."
        ),
        "days": days,
        "checklist": [
            "Загранпаспорт и виза (если нужна)",
            "Медицинская страховка",
            "Электронные копии документов",
            "Аптечка",
            "Зарядки и пауэрбанк",
        ],
        "budget_note": "Держите резерв ~10% бюджета на непредвиденное.",
        "advice": "Демо-режим: добавьте ключ LLM (GigaChat или OpenAI), чтобы получить реальный маршрут.",
    }