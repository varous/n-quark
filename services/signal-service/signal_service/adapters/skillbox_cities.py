"""Verified Skillbox city-ID map (Phase 4C.1).

Built ONLY from source evidence — each `city_id` was read directly from the Skillbox `event-details`
API for events actually observed in the bounded stratified sitemap probe (2026-08-07). Numeric ids are
never guessed, and no external geocoding is used. Cities not observed (notably **Kolkata**, which had
zero events in the sample) are intentionally absent.

Fields: skillbox_city_id -> (city_name, region/state, timezone). verified_at applies to the whole map.
"""

VERIFIED_AT = "2026-08-07"

# skillbox_city_id -> (city_name, state/region, timezone)
SKILLBOX_CITY_IDS: dict[str, tuple[str, str, str]] = {
    "5": ("Mumbai", "Maharashtra", "Asia/Kolkata"),
    "1106620": ("Bengaluru", "Karnataka", "Asia/Kolkata"),
    "1113278": ("Goa", "Goa", "Asia/Kolkata"),
    "1114881": ("Hyderabad", "Telangana", "Asia/Kolkata"),
    "1132982": ("Thane", "Maharashtra", "Asia/Kolkata"),
    "1131718": ("Sonipat", "Haryana", "Asia/Kolkata"),
    "1115282": ("Jaipur", "Rajasthan", "Asia/Kolkata"),
    "1115279": ("Jaipur City", "Rajasthan", "Asia/Kolkata"),
    "1114002": ("Guwahati", "Assam", "Asia/Kolkata"),
    "1110654": ("Dehradun", "Uttarakhand", "Asia/Kolkata"),
    "2790953": ("Gurugram", "Haryana", "Asia/Kolkata"),
    "1130829": ("Shillong", "Meghalaya", "Asia/Kolkata"),
    # Kolkata intentionally absent — zero Kolkata events in the bounded stratified sample.
}


def verified_city(city_id: str | None) -> tuple[str, str, str] | None:
    """Return (city_name, region, timezone) for a stable Skillbox city id, or None if unmapped."""
    return SKILLBOX_CITY_IDS.get(city_id) if city_id else None
