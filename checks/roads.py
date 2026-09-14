"""
Paved road check using OpenStreetMap's Overpass API.
100% free, no API key needed, community-verified map data.
Docs: https://wiki.openstreetmap.org/wiki/Overpass_API

FIX: Overpass API is notoriously flaky under load (frequent timeouts/errors).
This version retries against multiple public Overpass mirrors before
giving up, which dramatically reduces "error" / "unknown" results.
"""
import random
import requests
import time
import sys

# Multiple public Overpass mirrors - try each before giving up
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

# Overpass's usage policy (https://wiki.openstreetmap.org/wiki/Overpass_API)
# explicitly asks clients to identify themselves. requests' default
# "python-requests/x.y" User-Agent is a common trigger for silent
# throttling/blocking on shared mirrors - this fixes that.
REQUEST_HEADERS = {
    "User-Agent": "BVC-Land-Score/1.0 (contact: bluevalleyfunds.fund)"
}

PAVED_SURFACES = {
    "paved", "asphalt", "concrete", "concrete:plates",
    "concrete:lanes", "paving_stones", "sett", "cobblestone"
}
UNPAVED_SURFACES = {
    "unpaved", "dirt", "gravel", "ground", "grass",
    "sand", "clay", "compacted", "earth"
}

SEARCH_RADIUS_M = 60  # look for nearest road within 60 meters
MAX_RETRIES_PER_MIRROR = 2
RETRY_DELAY_SECONDS = 2


def _query_overpass(lat, lon, timeout):
    query = f"""
    [out:json][timeout:20];
    way(around:{SEARCH_RADIUS_M},{lat},{lon})["highway"];
    out tags;
    """

    last_error = None
    mirrors = OVERPASS_URLS[:]
    random.shuffle(mirrors)  # spread load instead of always hammering the same mirror first
    for url in mirrors:
        for attempt in range(MAX_RETRIES_PER_MIRROR):
            try:
                resp = requests.post(
                    url, data={"data": query}, headers=REQUEST_HEADERS, timeout=timeout
                )
                status_code = resp.status_code
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                last_error = e
                print(
                    f"[road check] mirror={url} attempt {attempt + 1}/{MAX_RETRIES_PER_MIRROR} "
                    f"failed for ({lat},{lon}): {type(e).__name__}: {e}",
                    file=sys.stderr,
                )
                time.sleep(RETRY_DELAY_SECONDS + random.uniform(0, 1))
                continue
    # All mirrors failed
    print(
        f"[road check] ALL MIRRORS FAILED for ({lat},{lon}). "
        f"last_error={type(last_error).__name__ if last_error else None}: {last_error}",
        file=sys.stderr,
    )
    raise last_error if last_error else RuntimeError("Overpass request failed")


def check_paved_road(lat, lon, timeout=25):
    """
    Returns dict:
      {
        "status": "ok" | "no_data" | "error",
        "surface": "paved" | "unpaved" | "unknown",
        "highway_type": str | None
      }
    Never guesses paved if no tag is found - returns "unknown" instead.
    """
    if lat is None or lon is None:
        return {"status": "no_data", "surface": "unknown", "highway_type": None}

    try:
        data = _query_overpass(lat, lon, timeout)
    except Exception:
        return {"status": "error", "surface": "unknown", "highway_type": None}

    elements = data.get("elements", [])
    if not elements:
        return {"status": "no_data", "surface": "unknown", "highway_type": None}

    tags = elements[0].get("tags", {})
    highway_type = tags.get("highway")
    surface = tags.get("surface")

    if surface:
        surface_l = surface.lower()
        if surface_l in PAVED_SURFACES:
            return {"status": "ok", "surface": "paved", "highway_type": highway_type}
        if surface_l in UNPAVED_SURFACES:
            return {"status": "ok", "surface": "unpaved", "highway_type": highway_type}
        return {"status": "ok", "surface": "unknown", "highway_type": highway_type}

    # No explicit surface tag - infer conservatively from highway class only
    likely_paved_classes = {
        "primary", "secondary", "tertiary", "residential",
        "trunk", "motorway", "unclassified"
    }
    if highway_type in likely_paved_classes:
        return {"status": "ok", "surface": "unknown", "highway_type": highway_type}

    return {"status": "no_data", "surface": "unknown", "highway_type": highway_type}
