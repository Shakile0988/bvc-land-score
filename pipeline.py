"""
Main scoring pipeline. Combines flood, road, zoning, and ARV checks.

Client rules being enforced:
  - Lot size <= 0.5 acre
  - Min ARV $40K+
  - Price <= 65% of ARV
  - Paved road (dirt road = skip)
  - Not in flood zone
  - Comp lots nearby selling $40K+

SCORING PHILOSOPHY:
  If a required data point is missing/unverifiable, that check contributes
  ZERO points and is logged in "data_gaps" - it does NOT get assumed as
  pass or fail. This keeps scores honest instead of guessed.
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
    # Client's rule is "residential lots <= 1/2 acre ONLY" - anything bigger
    # should never qualify no matter how well it scores elsewhere. Rejecting
    # here also skips the slow FEMA + Overpass calls below for the many
    # multi-acre listings in a typical Zillow scrape, which is most of why
    # a 50-listing GitHub Action run can take a long time.
    if acres is None:
        data_gaps.append("lot_size_missing")
        breakdown["lot_size"] = None
    elif acres > 0.5:
        return {
            "address": address.get("full"),
            "price": price,
            "acres": acres,
            "score": 0,
            "qualified": False,
            "breakdown": {"lot_size": False},
            "flood_zone": None,
            "road_surface": None,
            "zoning_hint": None,
            "arv_estimate": None,
            "comps_used": None,
            "data_gaps": ["lot_size_over_0.5_acre"],
            "zpid": listing.get("zpid"),
            "url": listing.get("propertyUrl"),
        }
    else:
        breakdown["lot_size"] = True
        score += WEIGHTS["lot_size"]

    # 2. Flood zone (FEMA - real check)
    flood = check_flood_zone(lat, lon)
    if flood["status"] != "ok":
        data_gaps.append(f"flood_check_{flood['status']}")
        breakdown["not_flood_zone"] = None
    else:
        passed = not flood["is_high_risk"]
        breakdown["not_flood_zone"] = passed
        if passed:
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
        comp_value_ok = arv_result["arv"] is not None  # already implies comps exist
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

    qualified = score >= QUALIFY_THRESHOLD and len(data_gaps) == 0

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
      "qualified": [...],      # score >= 70 AND no data gaps
      "needs_review": [...],   # score >= 70 BUT has data gaps (don't trust blindly)
      "rejected": [...],       # score < 70
      "summary": {...}
    }
    """
    results = [score_property(l, listings) for l in listings]

    qualified = [r for r in results if r["qualified"]]
    needs_review = [
        r for r in results
        if not r["qualified"] and r["score"] >= QUALIFY_THRESHOLD and r["data_gaps"]
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
