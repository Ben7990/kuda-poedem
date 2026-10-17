"""Временная проверка качества генерации (запускается в GitHub Actions)."""

import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.services import llm, planner, prices  # noqa: E402

CASES = [
    {
        "destination": "Бангладеш",
        "origin": "Ростов-на-Дону",
        "date_start": "2026-11-10",
        "date_end": "2026-11-14",
        "format": "культура и экскурсии",
        "budget_rub": 200000,
        "travelers": 1,
    },
    {
        "destination": "Буркина-Фасо",
        "origin": "Ростов-на-Дону",
        "date_start": "2026-12-01",
        "date_end": "2026-12-04",
        "format": "культура и экскурсии",
        "budget_rub": 250000,
        "travelers": 1,
    },
]


async def run_case(params: dict) -> None:
    print("=" * 70)
    print(f"КЕЙС: {params['origin']} -> {params['destination']}")
    ctx = await prices.travel_context(params)
    print("CTX alternatives:", [a["code"] for a in ctx["alternatives"]])
    print("CTX dest:", [d["code"] for d in ctx["dest_airports"]])
    it = await llm.generate_itinerary(params, ctx)
    print("ROUTE:", it.get("route"))
    print("DEPARTURE:", json.dumps(it.get("departure"), ensure_ascii=False))
    print("ROUTING:", json.dumps(it.get("routing"), ensure_ascii=False))
    print("VISA:", it.get("visa"))
    print("PRACTICAL:", json.dumps(it.get("practical"), ensure_ascii=False))
    for d in it.get("days", [])[:3]:
        print(" ", d.get("date"), "|", d.get("title"))
        for x in d.get("items", []):
            print("    -", x.get("activity"))
    print("problem:", llm._validate_itinerary(it, params))


async def main() -> None:
    for params in CASES:
        await run_case(params)
        await asyncio.sleep(60)


asyncio.run(main())
