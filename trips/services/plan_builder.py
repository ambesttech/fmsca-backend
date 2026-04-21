from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from zoneinfo import ZoneInfo

from trips.services import geocoding, routing
from trips.services.hos_engine import _merge_events, _split_by_day


def _fuel_threshold_drive_hours(total_miles: float, total_drive_h: float) -> list[float]:
    """
    Cumulative driving hours from trip start when a fueling stop is modeled.
    Assumes full tanks at origin; requires a stop before exceeding each 1,000 mi slice.
    """
    if total_miles <= 1000.0 or total_drive_h <= 1e-6:
        return []
    n = int((total_miles - 1) // 1000)
    out: list[float] = []
    for k in range(1, n + 1):
        mile = k * 1000.0
        out.append((mile / total_miles) * total_drive_h)
    return out


def _build_drive_queue(
    leg1_drive_h: float, leg2_drive_h: float, fuel_hours: list[float]
) -> list[tuple[str, float, str]]:
    """
    Returns ordered operations: ('DR', hours, note) or ('FUEL_ON', hours, note).
    """
    thresholds = sorted({float(x) for x in fuel_hours})
    ops: list[tuple[str, float, str]] = []

    def append_leg(start_cum: float, end_cum: float, note: str) -> None:
        cur = start_cum
        for th in thresholds:
            if th <= start_cum + 1e-6:
                continue
            if th >= end_cum - 1e-6:
                break
            if th > cur + 1e-6:
                ops.append(("DR", th - cur, note))
                ops.append(("FUEL_ON", 0.5, "Fueling (on duty not driving)"))
                cur = th
        if end_cum - cur > 1e-6:
            ops.append(("DR", end_cum - cur, note))

    append_leg(0.0, leg1_drive_h, "Driving (to pickup)")
    ops.append(("PICKUP_ON", 1.0, "Pickup (on duty not driving)"))
    append_leg(leg1_drive_h, leg1_drive_h + leg2_drive_h, "Driving (pickup to dropoff)")
    ops.append(("DROP_ON", 1.0, "Dropoff (on duty not driving)"))
    return ops


def build_trip_plan(payload: dict[str, Any]) -> dict[str, Any]:
    current = geocoding.geocode(payload["current_location"])
    pickup = geocoding.geocode(payload["pickup_location"])
    dropoff = geocoding.geocode(payload["dropoff_location"])

    waypoints = [
        {"lat": current["lat"], "lon": current["lon"], "label": "Current", "type": "current"},
        {"lat": pickup["lat"], "lon": pickup["lon"], "label": "Pickup", "type": "pickup"},
        {"lat": dropoff["lat"], "lon": dropoff["lon"], "label": "Dropoff", "type": "dropoff"},
    ]
    route = routing.build_route(waypoints)
    coords = route["geojson_line"]["coordinates"]
    total_miles = float(route["distance_miles"])
    leg1_h = route["legs"][0]["duration_s"] / 3600.0
    leg2_h = route["legs"][1]["duration_s"] / 3600.0
    total_drive_h = leg1_h + leg2_h

    fuel_hours = _fuel_threshold_drive_hours(total_miles, total_drive_h)
    fuel_stops = routing.fuel_stop_positions(coords, total_miles, 1000.0)

    trip_start_raw = payload.get("trip_start")
    if trip_start_raw:
        trip_start = datetime.fromisoformat(str(trip_start_raw).replace("Z", "+00:00"))
        if trip_start.tzinfo is None:
            trip_start = trip_start.replace(tzinfo=timezone.utc)
    else:
        trip_start = datetime.now(timezone.utc)

    log_tz = str(payload.get("log_timezone") or "UTC")
    try:
        ZoneInfo(log_tz)
    except Exception:
        log_tz = "UTC"

    hos = _simulate_from_queue(
        trip_start=trip_start,
        leg1_drive_h=leg1_h,
        leg2_drive_h=leg2_h,
        fuel_hours=fuel_hours,
        initial_cycle_used_h=float(payload["cycle_used_hrs"]),
        log_timezone=log_tz,
    )

    stops_for_map = [
        {
            "lat": current["lat"],
            "lon": current["lon"],
            "label": "Current",
            "type": "current",
            "description": current["display_name"],
        },
        {
            "lat": pickup["lat"],
            "lon": pickup["lon"],
            "label": "Pickup",
            "type": "pickup",
            "description": pickup["display_name"],
        },
    ]
    for fs in fuel_stops:
        stops_for_map.append(
            {
                "lat": fs["lat"],
                "lon": fs["lon"],
                "label": fs["label"],
                "type": "fuel",
                "description": "Planned fueling stop (~1000 mi intervals)",
            }
        )
    stops_for_map.append(
        {
            "lat": dropoff["lat"],
            "lon": dropoff["lon"],
            "label": "Dropoff",
            "type": "dropoff",
            "description": dropoff["display_name"],
        }
    )

    return {
        "geocoded": {"current": current, "pickup": pickup, "dropoff": dropoff},
        "route": {
            "geojson_line": route["geojson_line"],
            "distance_miles": round(total_miles, 1),
            "duration_drive_hours": round(total_drive_h, 2),
            "legs": route["legs"],
        },
        "stops": stops_for_map,
        "assumptions": {
            "driver_type": "Property-carrying",
            "hos_basis": "70 hours / 8 days (simplified model)",
            "adverse_driving": False,
            "pickup_dropoff_each_h": 1.0,
            "fuel_interval_miles": 1000,
            "fuel_on_duty_h": 0.5,
            "log_timezone": log_tz,
        },
        "hos": hos,
    }


def _simulate_from_queue(
    trip_start: datetime,
    leg1_drive_h: float,
    leg2_drive_h: float,
    fuel_hours: list[float],
    initial_cycle_used_h: float,
    log_timezone: str,
) -> dict[str, Any]:
    """
    Runs HOS simulation by expanding queue so fuel can occur on either leg.
    """
    queue = _build_drive_queue(leg1_drive_h, leg2_drive_h, fuel_hours)
    trip_start = trip_start.astimezone(timezone.utc)
    t = trip_start
    from dataclasses import dataclass

    @dataclass
    class S:
        window_left_h: float = 14.0
        drive_left_h: float = 11.0
        drive_since_break_h: float = 0.0
        cycle_used_h: float = 0.0
        consecutive_reset_off_h: float = 0.0

    state = S()
    state.cycle_used_h = max(0.0, min(70.0, float(initial_cycle_used_h)))
    events: list[dict[str, Any]] = []

    def _iso(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    def push(
        status: str,
        hours: float,
        note: str,
        *,
        counts_toward_ten: bool = True,
    ) -> None:
        nonlocal t
        if hours <= 1e-9:
            return
        end = t + timedelta(hours=hours)
        events.append(
            {
                "status": status,
                "start": _iso(t),
                "end": _iso(end),
                "hours": round(hours, 3),
                "note": note,
            }
        )
        if status in ("ON", "DR"):
            state.cycle_used_h += hours
            state.consecutive_reset_off_h = 0.0
            if status == "DR":
                state.window_left_h -= hours
                state.drive_left_h -= hours
                state.drive_since_break_h += hours
            else:
                state.window_left_h -= hours
        elif status in ("OFF", "SB"):
            if counts_toward_ten:
                state.consecutive_reset_off_h += hours
                if state.consecutive_reset_off_h >= 10.0 - 1e-6:
                    state.window_left_h = 14.0
                    state.drive_left_h = 11.0
                    state.drive_since_break_h = 0.0
                    state.consecutive_reset_off_h = 0.0
        t = end

    def apply_ten_off(note: str = "10-hour reset") -> None:
        push("OFF", 10.0, note, counts_toward_ten=True)

    def apply_thirty_four(note: str = "34-hour cycle restart (simplified)") -> None:
        push("OFF", 34.0, note, counts_toward_ten=True)
        state.cycle_used_h = 0.0

    def ensure_thirty_min_break() -> None:
        if state.drive_since_break_h >= 8.0 - 1e-6:
            push(
                "OFF",
                0.5,
                "30-minute break (after 8 hours driving)",
                counts_toward_ten=False,
            )
            state.drive_since_break_h = 0.0

    def ensure_window_and_drive() -> None:
        while state.window_left_h <= 1e-6 or state.drive_left_h <= 1e-6:
            apply_ten_off()
        while state.cycle_used_h > 70.0 + 1e-6:
            apply_thirty_four()

    def drive_hours(block_h: float, note: str) -> None:
        remaining = float(block_h)
        while remaining > 1e-6:
            ensure_window_and_drive()
            ensure_thirty_min_break()
            ensure_window_and_drive()
            if state.cycle_used_h > 70.0 + 1e-6:
                apply_thirty_four()
                continue
            chunk = min(remaining, state.drive_left_h, state.window_left_h)
            if state.drive_since_break_h >= 8.0 - 1e-6:
                ensure_thirty_min_break()
                continue
            if chunk < 1e-6:
                apply_ten_off()
                continue
            push("DR", chunk, note)
            remaining -= chunk

    for op, hours, note in queue:
        if op == "DR":
            drive_hours(hours, note)
        elif op == "FUEL_ON":
            push("ON", hours, note)
        elif op == "PICKUP_ON":
            push("ON", hours, note)
        elif op == "DROP_ON":
            push("ON", hours, note)

    merged = _merge_events(events)
    daily_logs = _split_by_day(merged, log_timezone)
    return {
        "duty_events": merged,
        "daily_logs": daily_logs,
        "summary": {
            "total_on_duty_h": round(sum(e["hours"] for e in merged if e["status"] in ("ON", "DR")), 2),
            "total_driving_h": round(sum(e["hours"] for e in merged if e["status"] == "DR"), 2),
            "total_off_duty_h": round(sum(e["hours"] for e in merged if e["status"] == "OFF"), 2),
            "cycle_used_end_h": round(state.cycle_used_h, 2),
        },
    }
