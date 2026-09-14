"""
Flood zone check using FEMA's official National Flood Hazard Layer (NFHL) API,
with a fallback to Esri's Living Atlas mirror of the same NFHL data.

FIX: hazards.fema.gov (a .gov domain) blocks/rate-limits requests coming
from GitHub Actions' shared datacenter IPs almost 100% of the time - this
is a documented, common pattern (many .gov and community sites block cloud
runner IP ranges). Headers/retries alone cannot beat an IP-level block, so
this version adds a second, independently-hosted source: Esri's ArcGIS
Online mirror of the same NFHL dataset (source: FEMA, hosted by Esri, part
of the Living Atlas of the World). It uses the same underlying NFHL schema
(field FLD_ZONE), so the risk classification logic is unchanged - only the
transport/host differs.

Both sources are official/derived-from-FEMA data. If BOTH fail, status is
honestly reported as "error" - never guessed.
"""
import random
import requests
import time
import sys

# Primary: FEMA's own NFHL REST endpoint
FEMA_NFHL_URL = "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28/query"

# Fallback: Esri Living Atlas mirror of the same NFHL data (different host/infra)
ESRI_NFHL_MIRROR_URL = "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Flood_Hazard_Reduced_Set_gdb/FeatureServer/0/query"

HIGH_RISK_ZONES = {"A", "AE", "AH", "AO", "AR", "A99", "V", "VE"}

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2

# A generic default User-Agent is more likely to get quietly rate-limited
# by government-hosted APIs behind bot protection than an identified one.
REQUEST_HEADERS = {
    "User-Agent": "BVC-Land-Score/1.0 (contact: bluevalleyfunds.fund)"
}


def _query_source(url, lat, lon, timeout, source_label):
    """
    Tries one Esri REST endpoint with MAX_RETRIES attempts.
    Returns the parsed JSON dict on success, or None if this source
    could not be reached at all after all retries.
    """
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE",
        "returnGeometry": "false",
        "f": "json",
    }

    last_error = None
    last_status_code = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=timeout)
            last_status_code = resp.status_code
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_error = e
            print(
                f"[flood check:{source_label}] attempt {attempt + 1}/{MAX_RETRIES} failed for "
                f"({lat},{lon}): {type(e).__name__}: {e} (http_status={last_status_code})",
                file=sys.stderr,
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SECONDS + random.uniform(0, 1))
            continue

    print(
        f"[flood check:{source_label}] GAVE UP for ({lat},{lon}) after {MAX_RETRIES} attempts. "
        f"last_error={type(last_error).__name__ if last_error else None}: {last_error} "
        f"last_http_status={last_status_code}",
        file=sys.stderr,
    )
    return None


def _extract_zone(data):
    """
    Pulls FLD_ZONE out of a successful Esri query response.
    Returns (zone_or_None, had_features_bool).
    Never raises - malformed/unexpected responses are treated as "no data".
    """
    if not isinstance(data, dict):
        return None, False
    features = data.get("features", [])
    if not features:
        return None, False
    zone = (features[0].get("attributes") or {}).get("FLD_ZONE")
    if not zone:
        return None, True  # feature existed but zone field empty - treat as no zone info
    return zone, True


def check_flood_zone(lat, lon, timeout=20):
    """
    Returns dict:
      {
        "status": "ok" | "no_data" | "error",
        "zone": "X" | "AE" | None,
        "is_high_risk": bool | None,
        "source": "fema" | "esri_mirror" | None
      }
    Never guesses. If neither source has data for the point, status = "no_data".
    If both sources are unreachable, status = "error".
    """
    if lat is None or lon is None:
        return {"status": "no_data", "zone": None, "is_high_risk": None, "source": None}

    # 1. Try FEMA's own server first (authoritative, but sometimes blocked from cloud IPs)
    data = _query_source(FEMA_NFHL_URL, lat, lon, timeout, "fema")
    source = "fema"

    # 2. If FEMA is unreachable, fall back to the Esri-hosted mirror of the same data
    if data is None:
        data = _query_source(ESRI_NFHL_MIRROR_URL, lat, lon, timeout, "esri_mirror")
        source = "esri_mirror"

    if data is None:
        # Both sources failed - honestly report error, do not guess.
        return {"status": "error", "zone": None, "is_high_risk": None, "source": None}

    zone, had_feature = _extract_zone(data)
    if not had_feature:
        return {"status": "no_data", "zone": None, "is_high_risk": None, "source": source}
    if not zone:
        return {"status": "no_data", "zone": None, "is_high_risk": None, "source": source}

    return {
        "status": "ok",
        "zone": zone,
        "is_high_risk": zone.upper() in HIGH_RISK_ZONES,
        "source": source,
    }
