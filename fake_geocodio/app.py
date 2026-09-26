"""Fake Geocodio — deterministic forward/reverse geocoding, no network.

Shapes follow Geocodio v1.x JSON (results[].formatted_address, location,
address_components, accuracy, accuracy_type, source). Any api_key accepted.

Resolution order for forward geocoding:
  1. A small built-in gazetteer of US places (substring match, case-insensitive)
  2. A trailing 5-digit ZIP in the query, if the gazetteer knows it
  3. Otherwise a stable pseudo-location inside the continental US derived from
     a hash of the query, so the same text always returns the same point.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

app = FastAPI(title="locadev-fake-geocodio")

# name, city, state, zip, county, lat, lng
PLACES: list[tuple[str, str, str, str, str, float, float]] = [
    ("laguna beach", "Laguna Beach", "CA", "92651", "Orange County", 33.5427, -117.7854),
    ("irvine", "Irvine", "CA", "92618", "Orange County", 33.6846, -117.8265),
    ("newport beach", "Newport Beach", "CA", "92660", "Orange County", 33.6189, -117.9298),
    ("costa mesa", "Costa Mesa", "CA", "92626", "Orange County", 33.6411, -117.9187),
    ("anaheim", "Anaheim", "CA", "92805", "Orange County", 33.8366, -117.9143),
    ("los angeles", "Los Angeles", "CA", "90012", "Los Angeles County", 34.0537, -118.2428),
    ("san diego", "San Diego", "CA", "92101", "San Diego County", 32.7157, -117.1611),
    ("san francisco", "San Francisco", "CA", "94103", "San Francisco County", 37.7749, -122.4194),
    ("seattle", "Seattle", "WA", "98101", "King County", 47.6062, -122.3321),
    ("portland", "Portland", "OR", "97204", "Multnomah County", 45.5152, -122.6784),
    ("phoenix", "Phoenix", "AZ", "85004", "Maricopa County", 33.4484, -112.0740),
    ("denver", "Denver", "CO", "80202", "Denver County", 39.7392, -104.9903),
    ("austin", "Austin", "TX", "78701", "Travis County", 30.2672, -97.7431),
    ("dallas", "Dallas", "TX", "75201", "Dallas County", 32.7767, -96.7970),
    ("chicago", "Chicago", "IL", "60601", "Cook County", 41.8781, -87.6298),
    ("atlanta", "Atlanta", "GA", "30303", "Fulton County", 33.7490, -84.3880),
    ("miami", "Miami", "FL", "33131", "Miami-Dade County", 25.7617, -80.1918),
    ("boston", "Boston", "MA", "02108", "Suffolk County", 42.3601, -71.0589),
    ("new york", "New York", "NY", "10007", "New York County", 40.7128, -74.0060),
    ("washington", "Washington", "DC", "20001", "District of Columbia", 38.9072, -77.0369),
]


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "places": len(PLACES)}


def _result(street: str, place: tuple, lat: float, lng: float, accuracy: float, acc_type: str) -> dict[str, Any]:
    _, city, state, zip_, county, _, _ = place
    parts = [p for p in [street, city] if p]
    formatted = ", ".join(parts) + f", {state} {zip_}"
    comps: dict[str, Any] = {
        "city": city,
        "county": county,
        "state": state,
        "zip": zip_,
        "country": "US",
    }
    m = re.match(r"^\s*(\d+)\s+(.*)$", street or "")
    if m:
        comps["number"] = m.group(1)
        comps["formatted_street"] = m.group(2)
    return {
        "address_components": comps,
        "formatted_address": formatted,
        "location": {"lat": round(lat, 6), "lng": round(lng, 6)},
        "accuracy": accuracy,
        "accuracy_type": acc_type,
        "source": "locadev fake-geocodio",
    }


def _hash_point(text: str) -> tuple[float, float]:
    h = hashlib.sha256(text.lower().encode()).digest()
    # continental US box
    lat = 30.0 + (int.from_bytes(h[0:4], "big") / 2**32) * 17.0
    lng = -122.0 + (int.from_bytes(h[4:8], "big") / 2**32) * 47.0
    return lat, lng


def _forward(q: str) -> list[dict[str, Any]]:
    ql = q.lower()
    street = q.split(",")[0].strip()
    for place in PLACES:
        if place[0] in ql:
            if street.lower() == place[0]:
                return [_result("", place, place[5], place[6], 0.9, "place")]
            # jitter street addresses a little so different streets differ
            h = hashlib.sha256(street.lower().encode()).digest()
            dlat = (h[0] - 128) / 128 * 0.02
            dlng = (h[1] - 128) / 128 * 0.02
            return [_result(street, place, place[5] + dlat, place[6] + dlng, 1.0, "rooftop")]
    zm = re.search(r"\b(\d{5})\b\s*$", q)
    if zm:
        for place in PLACES:
            if place[3] == zm.group(1):
                return [_result(street, place, place[5], place[6], 0.8, "range_interpolation")]
    lat, lng = _hash_point(q)
    synthetic = ("", "Localville", "CA", "90000", "Local County", lat, lng)
    return [_result(street, synthetic, lat, lng, 0.5, "street_center")]


def _nearest(lat: float, lng: float) -> tuple:
    return min(PLACES, key=lambda p: (p[5] - lat) ** 2 + (p[6] - lng) ** 2)


@app.get("/v{version}/geocode")
def geocode(version: str, q: str = Query(""), limit: int = 0, api_key: str = "") -> JSONResponse:
    if not q.strip():
        return JSONResponse(status_code=422, content={"error": "Could not geocode address. Postal code or city required."})
    results = _forward(q)
    if limit > 0:
        results = results[:limit]
    return JSONResponse(content={"input": {"formatted_address": q}, "results": results})


@app.get("/v{version}/reverse")
def reverse(version: str, q: str = Query(""), limit: int = 0, api_key: str = "") -> JSONResponse:
    try:
        lat_s, lng_s = [s.strip() for s in q.split(",")[:2]]
        lat, lng = float(lat_s), float(lng_s)
    except Exception:
        return JSONResponse(status_code=422, content={"error": "Invalid coordinate"})
    place = _nearest(lat, lng)
    h = int(hashlib.sha256(f"{lat:.4f},{lng:.4f}".encode()).hexdigest()[:4], 16)
    street = f"{100 + h % 9800} Main St"
    results = [_result(street, place, lat, lng, 1.0, "rooftop")]
    if limit > 0:
        results = results[:limit]
    return JSONResponse(content={"results": results})
