"""
ARV (After Repair Value) estimation - using REAL comps, not guesses.

Approach: instead of an invented formula, we use the batch of listings
n8n already scraped from Zillow as real comparable data. For each property,
we look at OTHER active listings in the same ZIP code and compute the
median price-per-acre. That median x this lot's acreage = a real,
data-backed ARV estimate.

Limitation (stated honestly): these are ASKING prices of active listings,
not confirmed SOLD prices. True ARV should ideally use sold comps
(e.g. via a paid service like ATTOM or Redfin sold data). This free
version is a reasonable proxy only when enough same-zip comps exist.
If fewer than MIN_COMPS are found, status = "insufficient_comps" and
the property should be skipped, not scored on a guess.
"""

MIN_COMPS = 3


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
    zip_code = (listing.get("listingAddress") or {}).get("zipCode")
    this_acres = _acres(listing)

    if price is None or zip_code is None or this_acres is None or this_acres <= 0:
        return {"status": "no_data", "arv": None, "comps_used": 0, "price_to_arv_ratio": None}

    comps = []
    for other in all_listings:
        if other is listing:
            continue
        other_zip = (other.get("listingAddress") or {}).get("zipCode")
        if other_zip != zip_code:
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
