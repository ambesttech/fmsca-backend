import logging
import math
from typing import Any

import requests

logger = logging.getLogger(__name__)

OSRM_BASE = "https://router.project-osrm.org/route/v1/driving"


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_route(waypoints: list[dict[str, Any]]) -> dict[str, Any]:
    """
    waypoints: [{"lat","lon","label"}, ...] in visit order.
    Uses public OSRM demo server (OpenStreetMap-based, no API key).
    """
    if len(waypoints) < 2:
        raise ValueError("At least two waypoints are required for routing.")
    coord_str = ";".join(f"{w['lon']},{w['lat']}" for w in waypoints)
    url = f"{OSRM_BASE}/{coord_str}"
    params = {"overview": "full", "geometries": "geojson", "steps": "true"}
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise ValueError("Routing failed: OSRM returned no route.")
    route = payload["routes"][0]
    geom = route.get("geometry") or {}
    coords = geom.get("coordinates") or []
    distance_m = float(route.get("distance") or 0)
    duration_s = float(route.get("duration") or 0)
    legs = route.get("legs") or []

    # Per-leg summaries for UI
    leg_summaries = []
    for i, leg in enumerate(legs):
        leg_summaries.append(
            {
                "from_label": waypoints[i]["label"],
                "to_label": waypoints[i + 1]["label"],
                "distance_m": float(leg.get("distance") or 0),
                "duration_s": float(leg.get("duration") or 0),
            }
        )

    miles = distance_m * 0.000621371
    return {
        "geojson_line": {"type": "LineString", "coordinates": coords},
        "distance_m": distance_m,
        "distance_miles": miles,
        "duration_s": duration_s,
        "duration_hours": duration_s / 3600.0,
        "legs": leg_summaries,
        "waypoints": waypoints,
    }


def interpolate_along_linestring(
    coordinates: list[list[float]], target_dist_m: float
) -> tuple[float, float]:
    """Find lat/lon at approximate cumulative distance along LineString (lon, lat)."""
    if not coordinates:
        raise ValueError("Empty geometry")
    cum = 0.0
    for i in range(len(coordinates) - 1):
        lon1, lat1 = coordinates[i]
        lon2, lat2 = coordinates[i + 1]
        seg = _haversine_m(lat1, lon1, lat2, lon2)
        if cum + seg >= target_dist_m:
            frac = (target_dist_m - cum) / seg if seg > 0 else 0
            frac = max(0.0, min(1.0, frac))
            lat = lat1 + (lat2 - lat1) * frac
            lon = lon1 + (lon2 - lon1) * frac
            return lat, lon
        cum += seg
    lon, lat = coordinates[-1]
    return lat, lon


def fuel_stop_positions(
    coordinates: list[list[float]], total_miles: float, every_miles: float = 1000.0
) -> list[dict[str, Any]]:
    """Place fuel stops so interval does not exceed every_miles (assumes full tank at start)."""
    if total_miles <= every_miles or not coordinates:
        return []
    stops = []
    n = int((total_miles - 1) // every_miles)
    distance_m = total_miles / 0.000621371
    for k in range(1, n + 1):
        d_m = k * every_miles / 0.000621371
        d_m = min(d_m, distance_m * 0.999)
        lat, lon = interpolate_along_linestring(coordinates, d_m)
        stops.append(
            {
                "lat": lat,
                "lon": lon,
                "label": f"Fuel (~{k * int(every_miles)} mi)",
                "type": "fuel",
                "mile_marker_approx": k * every_miles,
            }
        )
    return stops
