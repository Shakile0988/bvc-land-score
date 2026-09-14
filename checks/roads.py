"""
ARV (After Repair Value) estimation - using REAL comps, not guesses.

Approach: instead of an invented formula, we use the batch of listings
n8n already scraped from Zillow as real comparable data. For each property,
we look at OTHER active listings within a RADIUS (miles) AND a similar
ACREAGE TIER, and compute the median price-per-acre. That median x this
lot's acreage = a real, data-backed ARV estimate.

CRITICAL FIX: price-per-acre is NOT scale-invariant for land. A 0.04-acre
buildable lot and a 46-acre rural tract do not trade at the same $/acre -
small parcels command a huge premium per acre. The old version pooled ALL
listings together regardless of size, so small qualifying lots (<=0.5 acre)
got their ARV computed from mostly large rural-acreage comps, producing
absurdly low ARVs (e.g. $1,394 for a real $85K listing). Comps are now
restricted to a size band around the subject property before the radius
escalation runs, so a small lot is only ever compared to other small lots.

WHY RADIUS INSTEAD OF ZIP: state-wide scrapes land in dozens of ZIPs with
only 1-2 listings each, so "3+ comps in the exact ZIP" almost always fails.
A radius (escalating 20mi -> 40mi -> 75mi) finds comps across nearby ZIPs
while staying inside the same acreage tier.

Limitation (stated honestly): these are ASKING prices of active listings,
not confirmed SOLD prices. This is a reasonable free proxy only when
enough same-tier nearby comps exist.
"""

import math

MIN_COMPS = 3
COMP_RADII_MILES = [20, 40, 75]

# Acreage band around the subject property, expressed as a multiplier.
# e.g. for a 0.4-acre subject with LOW=0.3 / HIGH=3.0, only comps between
# 0.12 and 1.2 acres are eligible - this keeps "comp" meaning something for
# land pricing, instead of a 0.04-acre lot being priced off a 46-acre tract.
ACREAGE_BAND_LOW_MULT = 0.3
ACREAGE_BAND_HIGH_MULT = 3.0
# Floor/ceiling on the band itself so extremely tiny subjects don't end up
# with a near-zero-acre band that excludes everything.
ACREAGE_BAND_MIN_WIDTH = 0.25  # acres


def _acres(listing):
    lot = listing.get("lotArea") or {}
    value = lot.get("value")
    unit = lot.get("unit")
    if value is None or unit is None:
        return None
    if unit == "acres":
        return value
    if unit == "sqft":
        return value / 43560.0
    return None


def _miles_between(lat1, lon1, lat2, lon2):
    """Haversine distance in miles between two lat/lon points."""
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _acreage_band(this_acres):
    low = this_acres * ACREAGE_BAND_LOW_MULT
    high = this_acres * ACREAGE_BAND_HIGH_MULT
    if (high - low) < ACREAGE_BAND_MIN_WIDTH:
        pad = (ACREAGE_BAND_MIN_WIDTH - (high - low)) / 2
        low -= pad
        high += pad
    return max(low, 0.0001), high


def estimate_arv(listing, all_listings):
    """
    Returns:
      {
        "status": "ok" | "insufficient_comps" | "no_data",
        "arv": float | None,
        "comps_used": int,
        "price_to_arv_ratio": float | None
      }
    """
    price = (listing.get("listingPrice") or {}).get("amount")
    coords = listing.get("coordinates") or {}
    lat, lon = coords.get("latitude"), coords.get("longitude")
    this_acres = _acres(listing)

    if price is None or lat is None or lon is None or this_acres is None or this_acres <= 0:
        return {"status": "no_data", "arv": None, "comps_used": 0, "price_to_arv_ratio": None}

    band_low, band_high = _acreage_band(this_acres)

    candidates = []
    for other in all_listings:
        if other is listing:
            continue
        other_coords = other.get("coordinates") or {}
        other_lat, other_lon = other_coords.get("latitude"), other_coords.get("longitude")
        if other_lat is None or other_lon is None:
            continue
        other_price = (other.get("listingPrice") or {}).get("amount")
        other_acres = _acres(other)
        if other_price is None or other_acres is None or other_acres <= 0:
            continue
        # Acreage tier filter - the critical fix.
        if not (band_low <= other_acres <= band_high):
            continue
        dist = _miles_between(lat, lon, other_lat, other_lon)
        candidates.append((dist, other_price / other_acres))

    comps = []
    radius_used = None
    for radius in COMP_RADII_MILES:
        comps = [ppa for dist, ppa in candidates if dist <= radius]
        radius_used = radius
        if len(comps) >= MIN_COMPS:
            break

    if len(comps) < MIN_COMPS:
        return {
            "status": "insufficient_comps",
            "arv": None,
            "comps_used": len(comps),
            "price_to_arv_ratio": None,
        }

    comps.sort()
    mid = len(comps) // 2
    if len(comps) % 2 == 0:
        median_price_per_acre = (comps[mid - 1] + comps[mid]) / 2
    else:
        median_price_per_acre = comps[mid]

    arv = median_price_per_acre * this_acres
    ratio = price / arv if arv > 0 else None

    return {
        "status": "ok",
        "arv": round(arv, 2),
        "comps_used": len(comps),
        "comp_radius_miles": radius_used,
        "price_to_arv_ratio": round(ratio, 3) if ratio is not None else None,
    }
