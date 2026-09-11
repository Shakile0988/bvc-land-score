"""
Flood zone check using FEMA's official National Flood Hazard Layer (NFHL) API.
100% free, no API key needed, official government data.
Docs: https://www.fema.gov/flood-maps/national-flood-hazard-layer
"""
import requests

FEMA_NFHL_URL = "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28/query"

# Zones considered high-risk / avoid
HIGH_RISK_ZONES = {"A", "AE", "AH", "AO", "AR", "A99", "V", "VE"}


def check_flood_zone(lat, lon, timeout=15):
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

    try:
        resp = requests.get(FEMA_NFHL_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {"status": "error", "zone": None, "is_high_risk": None}

    features = data.get("features", [])
    if not features:
        # No flood layer at this point = FEMA has no mapped data here.
        # We do NOT assume "safe" - we flag as no_data.
        return {"status": "no_data", "zone": None, "is_high_risk": None}

    zone = features[0].get("attributes", {}).get("FLD_ZONE")
    if not zone:
        return {"status": "no_data", "zone": None, "is_high_risk": None}

    return {
        "status": "ok",
        "zone": zone,
        "is_high_risk": zone.upper() in HIGH_RISK_ZONES,
    }
