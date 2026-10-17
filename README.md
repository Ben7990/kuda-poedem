# 🧭 Планировщик путешествий — MVP

Продукт: пользователь отвечает на 5 вопросов (куда / откуда / даты / формат /
бюджет) — и получает маршрут по дням со ссылками на билеты, отели и экскурсии.

## 📂 Что в проекте

| Раздел | Описание |
|---|---|
| `docs/01-бизнес-план.md` | монетизация, экономика, каналы, риски |
| `docs/02-архитектура.md` | схема, компоненты, контракт LLM |
| `docs/03-ТЗ.md` | ТЗ на MVP, приёмка |
| `docs/deploy.md` | развёртывание на VPS (Docker / systemd) |
| `bot/` | Telegram-бот (Python, aiogram 3) |
| `web/` | SEO-лендинг (статический HTML) |

## 🚀 Запуск бота

Требуется Python 3.11+.

```powershell
cd bot
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Настройка:

```powershell
Copy-Item .env.example .env
```

Заполните `BOT_TOKEN` (у @BotFather). Если указать `LLM_API_KEY` — маршруты
строит LLM, иначе бот работает в демо-режиме с заглушкой.

Запуск:

```powershell
py run.py
```

## 🔑 Переменные окружения

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | токен Telegram-бота (обязательно) |
| `LLM_PROVIDER` | `openai` (по умолчанию) или `gigachat`; `auto` выберет сам |
| `LLM_API_KEY` | ключ LLM-провайдера (от 0 ₽: бесплатные модели OpenRouter/Groq) |
| `LLM_BASE_URL` | базовый URL API (OpenAI / OpenRouter / Groq / DeepSeek) |
| `LLM_MODEL` | модель (например `gpt-4o-mini`, `meta-llama/llama-3.3-70b-instruct:free`) |
| `GIGACHAT_CLIENT_ID` / `GIGACHAT_CLIENT_SECRET` | запасной вариант для РФ (необязательно) |
| `AVIASALES_MARKER` | партнёрский маркер Aviasales/Travelpayouts |
| `OSTROVOK_PARTNER_ID` | ID партнёра Ostrovok |
| `TRAVELPAYOUTS_TOKEN` | токен Travelpayouts (реальные цены на авиа) |
| `PAYMENTS_PROVIDER_TOKEN` | токен провайдера оплат @BotFather (ЮKassa) — пусто = демо |
| `PRICE_PER_TRIP_RUB` | цена полного маршрута в рублях, по умолчанию 690 |
| `CACHE_DIR` | папка кэша IATA-данных (по умолчанию системный temp) |

## 🗺 Сценарий

1. `/start` → «Спланировать поездку»
2. куда → откуда → даты (`10.11.2026 - 20.11.2026`) → формат (кнопки) → бюджет
3. предложение: **демо-план бесплатно** или **полный маршрут — 690 ₽** (оплата
   в Telegram, выдаётся сразу после успешного платежа)
4. в полном маршруте добавляются **реальные цены на авиа** (Travelpayouts)
   и ссылки на билеты/отели/экскурсии

## 📈 Текущий статус (V1)

- Реальные цены на авиа через Travelpayouts (IATA-резолв из датасетов).
- Оплата полного маршрута через Telegram Payments (ЮKassa).
- Разовый платный маршрут + бесплатный демо-план.
- Дальше: БД (PostgreSQL), SEO-сайт на Next.js, подписки.