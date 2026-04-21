import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "ELDTripPlanner/1.0 (driver-trip-demo; educational use)"


def geocode(address: str) -> dict[str, Any]:
    """Resolve a free-text address to lat/lon using OpenStreetMap Nominatim."""
    if not address or not address.strip():
        raise ValueError("Address is required.")
    params = {"q": address.strip(), "format": "json", "limit": 1}
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError(f"No results found for address: {address!r}")
    row = data[0]
    return {
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "display_name": row.get("display_name", address),
    }
