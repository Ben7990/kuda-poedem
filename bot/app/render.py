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

    departure = it.get("departure") or {}
    if departure.get("status_note"):
        parts.append("")
        parts.append("<b>🛫 Вылет</b>")
        parts.append(f"  {escape(str(departure['status_note']))}")
        for opt in (departure.get("options") or [])[:4]:
            line = f"  • {escape(str(opt.get('iata', '')))} {escape(str(opt.get('city', '') or ''))}"
            if opt.get("airport"):
                line += f" ({escape(str(opt['airport']))})"
            if opt.get("ground"):
                line += f" — {escape(str(opt['ground']))}"
            if opt.get("note"):
                line += f"; {escape(str(opt['note']))}"
            parts.append(line)

    routing = it.get("routing") or {}
    if routing.get("hubs") or routing.get("airlines") or routing.get("note"):
        parts.append("")
        parts.append("<b>🌍 Как долететь</b>")
        if routing.get("hubs"):
            parts.append("  Транзит: " + escape(", ".join(str(h) for h in routing["hubs"])))
        if routing.get("airlines"):
            parts.append("  Авиакомпании: " + escape(", ".join(str(a) for a in routing["airlines"])))
        if routing.get("note"):
            parts.append(f"  {escape(str(routing['note']))}")
        if routing.get("approx_price_rub"):
            try:
                price = f"{int(routing['approx_price_rub']):,} ₽".replace(",", " ")
                parts.append(f"  Билеты туда-обратно: ~{price} на человека")
            except (TypeError, ValueError):
                parts.append(f"  Билеты: {escape(str(routing['approx_price_rub']))}")

    if it.get("visa"):
        parts.append("")
        parts.append(f"🛂 {escape(str(it['visa']))}")

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

    practical = it.get("practical") or []
    if practical:
        parts.append("")
        parts.append("<b>📌 На месте</b>")
        parts.extend(f"  • {escape(str(x))}" for x in practical[:6])

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
    parts.append(f'  ✈️ <a href="{escape(str(links["flights"]), quote=True)}">Авиабилеты</a>')
    if links.get("trains"):
        parts.append(
            f'  🚆 <a href="{escape(str(links["trains"]), quote=True)}">ЖД (Яндекс.Расписания)</a>'
        )
    hotels = links.get("hotels") or []
    if hotels:
        items = ", ".join(
            f'<a href="{escape(str(h["url"]), quote=True)}">{escape(str(h["label"]))}</a>'
            for h in hotels
        )
        parts.append(f"  🏨 Отели: {items}")
    tours = links.get("tours") or []
    if tours:
        items = ", ".join(
            f'<a href="{escape(str(t["url"]), quote=True)}">{escape(str(t["label"]))}</a>'
            for t in tours
        )
        parts.append(f"  🎟 Экскурсии: {items}")

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
