"""
Paved road check using OpenStreetMap's Overpass API.
100% free, no API key needed, community-verified map data.
Docs: https://wiki.openstreetmap.org/wiki/Overpass_API
"""
import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# OSM surface tags considered "paved"
PAVED_SURFACES = {
    "paved", "asphalt", "concrete", "concrete:plates",
    "concrete:lanes", "paving_stones", "sett", "cobblestone"
}
UNPAVED_SURFACES = {
    "unpaved", "dirt", "gravel", "ground", "grass",
    "sand", "clay", "compacted", "earth"
}

SEARCH_RADIUS_M = 60  # look for nearest road within 60 meters


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

    query = f"""
    [out:json][timeout:20];
    way(around:{SEARCH_RADIUS_M},{lat},{lon})["highway"];
    out tags;
    """

    try:
        resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {"status": "error", "surface": "unknown", "highway_type": None}

    elements = data.get("elements", [])
    if not elements:
        return {"status": "no_data", "surface": "unknown", "highway_type": None}

    # Take the first nearby way's tags
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
    # (primary/secondary/tertiary/residential are almost always paved in the US)
    likely_paved_classes = {
        "primary", "secondary", "tertiary", "residential",
        "trunk", "motorway", "unclassified"
    }
    if highway_type in likely_paved_classes:
        return {"status": "ok", "surface": "unknown", "highway_type": highway_type}

    return {"status": "no_data", "surface": "unknown", "highway_type": highway_type}
