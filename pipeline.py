"""
Main scoring pipeline. Combines flood, road, zoning, and ARV checks.

Client rules being enforced:
  - Lot size <= 0.5 acre
  - Min ARV $40K+
  - Price <= 65% of ARV
  - Paved road (dirt road = skip)
  - Not in flood zone (confirmed flood zone = HARD REJECT, never "review")
  - Comp lots nearby selling $40K+

SCORING PHILOSOPHY:
  If a required data point is missing/unverifiable, that check contributes
  ZERO points and is logged in "data_gaps" - it does NOT get assumed as
  pass or fail. This keeps scores honest instead of guessed.

  EXCEPTION: if a check comes back with a CONFIRMED negative result
  (e.g. flood zone is definitely high-risk), that is not a "gap" - it's
  a known fact, and the client's rule says never chase it. So it hard-
  rejects immediately instead of falling into needs_review.
"""

from checks.flood import check_flood_zone
from checks.roads import check_paved_road
from checks.zoning import check_zoning_hint
from checks.arv import estimate_arv

QUALIFY_THRESHOLD = 70

# Point weights per passing criterion (sums to 1000 if everything passes)
WEIGHTS = {
    "lot_size": 150,
    "min_arv": 150,
    "price_under_65pct_arv": 250,
    "paved_road": 200,
    "not_flood_zone": 150,
    "comp_value_40k": 100,
}

# Gaps in this set are informational only and never block qualification.
# Zoning is excluded from scoring entirely (see checks/zoning.py docstring -
# free data is unreliable for it), so an unknown zoning hint must not sit
# in data_gaps and silently zero out every qualified result.
NON_BLOCKING_GAPS = {"zoning_unknown"}


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


def _reject(listing, address, price, acres, reason_gap, extra=None):
    """Build a hard-rejected result. Used for both the lot-size hard filter
    and the confirmed-flood-zone hard filter, so both reasons are visible
    directly in the rejected bucket instead of leaking into needs_review."""
    base = {
        "address": address.get("full"),
        "price": price,
        "acres": acres,
        "score": 0,
        "qualified": False,
        "breakdown": {"lot_size": None},
        "flood_zone": None,
        "road_surface": None,
        "zoning_hint": None,
        "arv_estimate": None,
        "comps_used": None,
        "data_gaps": [reason_gap],
        "zpid": listing.get("zpid"),
        "url": listing.get("propertyUrl"),
    }
    if extra:
        base.update(extra)
    return base


def score_property(listing, all_listings):
    address = listing.get("listingAddress", {})
    coords = listing.get("coordinates") or {}
    lat, lon = coords.get("latitude"), coords.get("longitude")
    price = (listing.get("listingPrice") or {}).get("amount")
    acres = _acres(listing)

    data_gaps = []
    score = 0
    breakdown = {}

    # 1. Lot size <= 0.5 acre -- HARD FILTER, not just a weighted point.
    if acres is None:
        data_gaps.append("lot_size_missing")
        breakdown["lot_size"] = None
    elif acres > 0.5:
        return _reject(
            listing, address, price, acres,
            "lot_size_over_0.5_acre",
            extra={"breakdown": {"lot_size": False}},
        )
    else:
        breakdown["lot_size"] = True
        score += WEIGHTS["lot_size"]

    # 2. Flood zone (FEMA - real check)
    # If the check couldn't run at all, that's a genuine data gap -> review.
    # If it DID run and confirms high-risk, that's not a gap, it's a known
    # fact -> hard reject immediately per client rule ("never chase flood
    # zone lots"). Only a confirmed low-risk zone earns the scoring points.
    flood = check_flood_zone(lat, lon)
    if flood["status"] != "ok":
        data_gaps.append(f"flood_check_{flood['status']}")
        breakdown["not_flood_zone"] = None
    elif flood["is_high_risk"]:
        return _reject(
            listing, address, price, acres,
            "flood_zone_confirmed_high_risk",
            extra={
                "breakdown": {"lot_size": breakdown["lot_size"], "not_flood_zone": False},
                "flood_zone": flood.get("zone"),
            },
        )
    else:
        breakdown["not_flood_zone"] = True
        score += WEIGHTS["not_flood_zone"]

    # 3. Paved road (OpenStreetMap - real check)
    road = check_paved_road(lat, lon)
    if road["status"] != "ok" or road["surface"] == "unknown":
        data_gaps.append(f"road_check_{road['status']}_{road['surface']}")
        breakdown["paved_road"] = None
    else:
        passed = road["surface"] == "paved"
        breakdown["paved_road"] = passed
        if passed:
            score += WEIGHTS["paved_road"]

    # 4. ARV + price <= 65% of ARV (real comps from batch)
    arv_result = estimate_arv(listing, all_listings)
    if arv_result["status"] != "ok":
        data_gaps.append(f"arv_{arv_result['status']}")
        breakdown["min_arv"] = None
        breakdown["price_under_65pct_arv"] = None
    else:
        arv_passed = arv_result["arv"] >= 40000
        breakdown["min_arv"] = arv_passed
        if arv_passed:
            score += WEIGHTS["min_arv"]

        ratio = arv_result["price_to_arv_ratio"]
        price_passed = ratio is not None and ratio <= 0.65
        breakdown["price_under_65pct_arv"] = price_passed
        if price_passed:
            score += WEIGHTS["price_under_65pct_arv"]

    # 5. Comp lots nearby selling $40K+ (median comp price itself)
    if arv_result["status"] == "ok" and arv_result["comps_used"] >= 3:
        comp_value_ok = arv_result["arv"] is not None
        breakdown["comp_value_40k"] = comp_value_ok
        if comp_value_ok and arv_result["arv"] >= 40000:
            score += WEIGHTS["comp_value_40k"]
    else:
        data_gaps.append("insufficient_comps_for_area_check")
        breakdown["comp_value_40k"] = None

    # 6. Zoning hint (informational only - not scored due to unreliable free data)
    zoning = check_zoning_hint(listing)
    if zoning["status"] == "no_data":
        data_gaps.append("zoning_unknown")

    # Qualification only blocks on REAL/blocking gaps. zoning_unknown is
    # informational-only per client instructions and must not zero out
    # every result just because free zoning data rarely exists.
    blocking_gaps = [g for g in data_gaps if g not in NON_BLOCKING_GAPS]
    qualified = score >= QUALIFY_THRESHOLD and len(blocking_gaps) == 0

    return {
        "address": address.get("full"),
        "price": price,
        "acres": acres,
        "score": score,
        "qualified": qualified,
        "breakdown": breakdown,
        "flood_zone": flood.get("zone"),
        "road_surface": road.get("surface"),
        "zoning_hint": zoning.get("likely_zoning"),
        "arv_estimate": arv_result.get("arv"),
        "comps_used": arv_result.get("comps_used"),
        "data_gaps": data_gaps,
        "zpid": listing.get("zpid"),
        "url": listing.get("propertyUrl"),
    }


def run_pipeline(listings):
    """
    listings: list of raw Zillow property dicts (as scraped by Apify)
    Returns: {
      "qualified": [...],      # score >= 70 AND no BLOCKING data gaps
      "needs_review": [...],   # score >= 70 BUT has a blocking data gap
      "rejected": [...],       # score < 70, OR hard-rejected (lot size / confirmed flood zone)
      "summary": {...}
    }
    """
    results = [score_property(l, listings) for l in listings]

    qualified = [r for r in results if r["qualified"]]
    needs_review = [
        r for r in results
        if not r["qualified"]
        and r["score"] >= QUALIFY_THRESHOLD
        and any(g not in NON_BLOCKING_GAPS for g in r["data_gaps"])
    ]
    rejected = [
        r for r in results
        if r not in qualified and r not in needs_review
    ]

    return {
        "qualified": qualified,
        "needs_review": needs_review,
        "rejected": rejected,
        "summary": {
            "total_scanned": len(results),
            "qualified_count": len(qualified),
            "needs_review_count": len(needs_review),
            "rejected_count": len(rejected),
        },
    }
