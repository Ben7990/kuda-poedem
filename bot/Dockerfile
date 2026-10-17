FROM python:3.11-slim

WORKDIR /srv/bot

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY run.py .
COPY app ./app
# Секреты через volume/environment; образ копирует .env если он рядом.
COPY .env ./

ENV PYTHONUNBUFFERED=1

CMD ["python", "run.py"]