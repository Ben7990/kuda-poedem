"""Рендер плана поездки в сообщения Telegram (HTML)."""

from html import escape

MAX_LEN = 3800


def _fmt_day(day: dict, index: int) -> str:
    lines = [f"<b>День {index}: {escape(str(day.get('date', '')))}, "
             f"{escape(str(day.get('title', '')))}</b>"]
    for item in day.get("items", []):
        time = escape(str(item.get("time", "")))
        activity = escape(str(item.get("activity", "")))
        transport = item.get("transport")
        cost = item.get("cost_rub")
        tips = item.get("tips")
        line = f"  • {time} — {activity}"
        if transport:
            line += f" (транспорт: {escape(str(transport))})"
        if cost:
            line += f" ~{cost} ₽"
        if tips:
            line += f"\n     💡 {escape(str(tips))}"
        lines.append(line)
    return "\n".join(lines)


def render_plan(plan: dict) -> list[str]:
    params = plan["params"]
    it = plan["itinerary"]
    links = plan["links"]

    header = (
        "🧭 <b>Ваш маршрут готов</b>\n"
        f"📍 {escape(params['destination'])} · из {escape(params['origin'])}\n"
        f"📅 {params['date_start']} — {params['date_end']} · "
        f"{escape(params['format'])} · {params['budget_rub']} ₽"
    )

    parts = [header, "", escape(str(it.get("summary", "")))]

    prices = plan.get("real_prices")
    if prices:
        parts.append("")
        parts.append("<b>🛩 Реальные цены на авиа</b>")
        for t in prices[:3]:
            price = f"{t['price']:,} ₽".replace(",", " ")
            line = f"  • {escape(str(t.get('depart_date', '')))} — <b>{price}</b>"
            if t.get("airline"):
                line += f" ({escape(str(t['airline']))})"
            parts.append(line)
        parts.append("  🔗 Переходите по ссылкам — там актуальное расписание.")

    for i, day in enumerate(it.get("days", []), start=1):
        parts.append("")
        parts.append(_fmt_day(day, i))

    checklist = it.get("checklist", [])
    if checklist:
        parts.append("")
        parts.append("<b>🎒 Чек-лист</b>")
        parts.extend(f"  ☐ {escape(str(x))}" for x in checklist)

    if it.get("budget_note"):
        parts.append("")
        parts.append(f"💰 {escape(str(it['budget_note']))}")

    parts.append("")
    parts.append("<b>🔗 Забронировать</b>")
    parts.append(f'  ✈️ <a href="{links["flights"]}">Авиабилеты</a>')
    parts.append(f'  🚆 <a href="{links["trains"]}">ЖД билеты</a>')
    parts.append(f'  🏨 <a href="{links["hotels"]}">Отели</a>')
    parts.append(f'  🎟 <a href="{links["tours"]}">Экскурсии</a>')

    return _chunk("\n".join(parts))


def _chunk(text: str) -> list[str]:
    if len(text) <= MAX_LEN:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > MAX_LEN:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks
