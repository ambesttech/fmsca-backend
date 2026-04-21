"""Shared helpers for duty log formatting (HOS simulation runs in plan_builder)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


def _merge_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not events:
        return []
    out: list[dict[str, Any]] = []
    cur = {**events[0]}
    for e in events[1:]:
        if e["status"] == cur["status"] and e["note"] == cur["note"]:
            cur["end"] = e["end"]
            cur["hours"] = round(cur["hours"] + e["hours"], 3)
        else:
            out.append(cur)
            cur = {**e}
    out.append(cur)
    return out


def _split_by_day(events: list[dict[str, Any]], tz_name: str) -> list[dict[str, Any]]:
    tz = ZoneInfo(tz_name or "UTC")
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        start = datetime.fromisoformat(e["start"].replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        local = start.astimezone(tz)
        key = local.date().isoformat()
        by_date[key].append(e)

    days = sorted(by_date.keys())
    return [
        {
            "date": d,
            "events": by_date[d],
            "totals": _day_totals(by_date[d]),
        }
        for d in days
    ]


def _day_totals(events: list[dict[str, Any]]) -> dict[str, float]:
    totals = {"OFF": 0.0, "SB": 0.0, "DR": 0.0, "ON": 0.0}
    for e in events:
        totals[e["status"]] = totals.get(e["status"], 0.0) + float(e["hours"])
    return {k: round(v, 2) for k, v in totals.items()}
