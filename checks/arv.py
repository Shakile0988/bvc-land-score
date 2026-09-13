"""
ARV (After Repair Value) estimation - using REAL comps, not guesses.

Approach: instead of an invented formula, we use the batch of listings
n8n already scraped from Zillow as real comparable data. For each property,
we look at OTHER active listings within a RADIUS (miles, not ZIP code) and
compute the median price-per-acre. That median x this lot's acreage = a
real, data-backed ARV estimate.

WHY RADIUS INSTEAD OF ZIP:
When scraping is state-wide (e.g. all of Georgia), listings land in dozens
of different ZIP codes with only 1-2 per ZIP. Requiring 3+ comps in the
EXACT same ZIP almost always fails ("insufficient_comps") even with 50+
listings scraped, because they're spread out. Using a radius instead finds
comps across nearby ZIPs, so ARV can actually be computed in practice.
COMP_RADIUS_MILES is tunable - widen it if you still get too many
"insufficient_comps" results, narrow it if comps feel too far apart to be
realistic for land pricing.

Limitation (stated honestly): these are ASKING prices of active listings,
not confirmed SOLD prices. True ARV should ideally use sold comps
(e.g. via a paid service like ATTOM or Redfin sold data). This free
version is a reasonable proxy only when enough nearby comps exist.
If fewer than MIN_COMPS are found within the radius, status =
"insufficient_comps" and the property should be skipped, not scored on
a guess.
"""

import math

MIN_COMPS = 3
COMP_RADIUS_MILES = 20


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
    R = 3958.8  # earth radius in miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


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

    comps = []
    for other in all_listings:
        if other is listing:
            continue
        other_coords = other.get("coordinates") or {}
        other_lat, other_lon = other_coords.get("latitude"), other_coords.get("longitude")
        if other_lat is None or other_lon is None:
            continue
        if _miles_between(lat, lon, other_lat, other_lon) > COMP_RADIUS_MILES:
            continue
        other_price = (other.get("listingPrice") or {}).get("amount")
        other_acres = _acres(other)
        if other_price is None or other_acres is None or other_acres <= 0:
            continue
        comps.append(other_price / other_acres)

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
        "price_to_arv_ratio": round(ratio, 3) if ratio is not None else None,
    }
