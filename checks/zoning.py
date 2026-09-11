"""
Zoning check - HONEST LIMITATION NOTICE:
There is no single free, national (or even Georgia-wide) API that reliably
returns zoning for an arbitrary parcel. Real zoning data lives on each
county's own GIS/assessor site in different formats.

This module does NOT guess zoning. It only extracts zoning signal when it
is explicitly present in the scraped listing text (marketingTagline, etc.)
Everything else is marked "unknown" so the pipeline can decide to skip
rather than assume.

If you need guaranteed zoning verification, that field must eventually be
confirmed manually or via a paid parcel API (e.g. Regrid) - free scraping
of 45+ individual county sites is a much larger project than this script.
"""

RESIDENTIAL_HINTS = [
    "residential", "buildable", "shovel-ready", "subdivision",
    "single family", "multi-family", "platted"
]
NON_RESIDENTIAL_HINTS = [
    "commercial", "industrial", "agricultural", "ag-zoned", "farmland"
]


def check_zoning_hint(listing):
    """
    Looks at whatever text fields the Zillow scrape already gave us.
    Returns:
      {
        "status": "hint_found" | "no_data",
        "likely_zoning": "residential" | "non_residential" | "unknown"
      }
    """
    text_blob = " ".join(str(v) for v in [
        listing.get("marketingTagline", ""),
        listing.get("homeType", ""),
    ]).lower()

    for hint in NON_RESIDENTIAL_HINTS:
        if hint in text_blob:
            return {"status": "hint_found", "likely_zoning": "non_residential"}

    for hint in RESIDENTIAL_HINTS:
        if hint in text_blob:
            return {"status": "hint_found", "likely_zoning": "residential"}

    return {"status": "no_data", "likely_zoning": "unknown"}
