"""
Flood zone check using FEMA's official National Flood Hazard Layer (NFHL) API.
100% free, no API key needed, official government data.
Docs: https://www.fema.gov/flood-maps/national-flood-hazard-layer

FIX: FEMA's endpoint occasionally times out or throttles under load.
This version retries a few times with a short backoff before giving up,
which reduces "error" results caused by transient network issues.
"""
import requests
import time

FEMA_NFHL_URL = "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28/query"

HIGH_RISK_ZONES = {"A", "AE", "AH", "AO", "AR", "A99", "V", "VE"}

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2


def check_flood_zone(lat, lon, timeout=20):
    """
    Returns dict:
      {
        "status": "ok" | "no_data" | "error",
        "zone": "X" | "AE" | None,
        "is_high_risk": bool | None
      }
    Never guesses. If FEMA has no data for the point, status = "no_data".
    """
    if lat is None or lon is None:
        return {"status": "no_data", "zone": None, "is_high_risk": None}

    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE",
        "returnGeometry": "false",
        "f": "json",
    }

    data = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(FEMA_NFHL_URL, params=params, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SECONDS)
            continue

    if data is None:
        return {"status": "error", "zone": None, "is_high_risk": None}

    features = data.get("features", [])
    if not features:
        return {"status": "no_data", "zone": None, "is_high_risk": None}

    zone = features[0].get("attributes", {}).get("FLD_ZONE")
    if not zone:
        return {"status": "no_data", "zone": None, "is_high_risk": None}

    return {
        "status": "ok",
        "zone": zone,
        "is_high_risk": zone.upper() in HIGH_RISK_ZONES,
    }
