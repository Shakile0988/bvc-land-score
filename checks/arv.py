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

COMP_RADII_MILES is an escalating list, not a single fixed value: it tries
20mi first (most locally accurate), then 40mi, then 75mi, stopping as soon
as MIN_COMPS is met. This means a property in a dense area still gets a
tight, realistic radius, while a property in a sparse area (typical for
a state-wide scrape) still gets an ARV instead of "insufficient_comps" every
time. Adjust the list if you still see too many insufficient_comps results
(add a larger final radius) or if far-flung comps feel unrealistic for land
pricing (remove the largest radius).

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

# Escalating radii: try the tightest (most locally accurate) radius first,
# widen only if it doesn't find enough comps. A single fixed 20mi radius
# was failing constantly on state-wide scrapes where listings land in
# dozens of ZIPs with only 1-2 per ZIP - most points never had 3 comps
# within 20mi. Capping at 75mi keeps "comp" still meaning something for
# land pricing rather than comparing across the whole state.
COMP_RADII_MILES = [20, 40, 75]


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

    # Pre-compute distance + price-per-acre once per candidate, then just
    # filter by radius at each escalation step instead of re-scanning.
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
