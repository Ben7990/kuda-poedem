# Развёртывание бота на VPS (Linux)

Бот не зависит от вашего ПК: поднимите его на любом VPS (1 vCPU / 512 MB достаточно)
или в Docker на free-тире (Fly.io, Railway, Render, Yandex Cloud Functions — нет, это
долгий процесс: нужен вебхук, а не polling; для MVP проще Docker-vps).

## Способ 1. Docker на VPS (рекомендуется)

```bash
# на VPS
git clone <ваш_репозиторий> project
cd project/bot
cp .env.example .env
nano .env            # вставьте BOT_TOKEN и остальные ключи

docker build -t puteshestviya-bot .
docker run -d --name puteshestviya-bot --restart unless-stopped \
  -v "$(pwd)/.env:/srv/bot/.env:ro" \
  -v "$(pwd)/data:/tmp/opencode-cache" \
  puteshestviya-bot

docker logs -f puteshestviya-bot   # проверка: "Start polling ... @kuda..."
```

Кэш IATA рекомендуется монтировать, чтобы не скачивать датасеты при каждом
перезапуске (укажите `CACHE_DIR` в `.env`).

## Способ 2. systemd (без Docker)

Требуется Python 3.11 на VPS:

```bash
sudo apt update && sudo apt install -y python3.11-venv
sudo useradd -m -s /bin/bash botuser
sudo -u botuser bash -c "git clone <repo> ~/bot"
cd /home/botuser/bot
sudo -u botuser python3.11 -m venv .venv
sudo -u botuser ./.venv/bin/pip install -r requirements.txt
sudo -u botuser cp .env.example .env && sudo -u botuser nano .env
```

Сервис `/etc/systemd/system/kudapoedem.service`:

```ini
[Unit]
Description=Travel planner Telegram bot
After=network-online.target

[Service]
User=botuser
WorkingDirectory=/home/botuser/bot
EnvironmentFile=/home/botuser/bot/.env
ExecStart=/home/botuser/bot/.venv/bin/python -X utf8 run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kudapoedem
sudo journalctl -u kudapoedem -f
```

## Обновление кода на VPS

```bash
docker compose: docker build -t puteshestviya-bot . && docker rm -f puteshestviya-bot && docker run -d --name puteshestviya-bot ...
# или systemd:
cd ~/bot && sudo -u botuser git pull && sudo systemctl restart kudapoedem
```

## Что положить в .env на VPS

Скопируйте рабочий `bot/.env` с вашего ПК (там уже есть BOT_TOKEN). Дополнительно
заполните для монетизации/реальных цен:

| Переменная | Зачем |
|---|---|
| `LLM_API_KEY` + `LLM_BASE_URL` + `LLM_MODEL` | мировые модели: OpenRouter/Groq/DeepSeek (см. .env.example) |
| `TRAVELPAYOUTS_TOKEN` | реальные цены на авиабилеты |
| `PAYMENTS_PROVIDER_TOKEN` | приём оплаты 690 ₽ в Telegram |
| `AVIASALES_MARKER`, `OSTROVOK_PARTNER_ID` | партнёрские ссылки (комиссия) |

Бот использует long-polling, наружний порт/nginx/домен не нужны.