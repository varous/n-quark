"""Redistribution policy — what crawl-space (a consumer product) may do with each event.

n-quark ingests events as entity data for intelligence (public_scrape / legitimate_interest).
Showing them to end users is *redistribution*, a higher bar, so the feed tags every event with
a tier and the consumer honors it. Policy lives here (server-side, with the data) — not in the
consumer — and is derived from signals we already capture: source, price, and the verified flag.

  open       — free anything + grassroots/community (Boshow, Townscript, Luma, Meetup, Knowafest):
               safe to re-surface as full cards; organizers want the reach.
  link_only  — mainstream ticketing (District/Skillbox) paid, and aggregator re-listings
               (AllEvents): show as discovery, link out to the source, never intercept the sale.
  excluded   — unverified / spam (e.g. Townscript listings with a spamScore): not exported.
"""

# Mainstream ticketing platforms: their *paid* inventory is link-out only (commercial-harm risk).
_LINK_ONLY_PAID_SOURCES = frozenset({"district", "skillbox"})
# Aggregators re-list third parties — link out regardless of price, never re-host as our card.
_AGGREGATOR_SOURCES = frozenset({"allevents"})

OPEN = "open"
LINK_ONLY = "link_only"
EXCLUDED = "excluded"


def redistribution_tier(source: str, price_min: float | None, verified: bool) -> str:
    """Classify an event's redistribution tier from source + price + verification."""
    if not verified:
        return EXCLUDED
    if source in _AGGREGATOR_SOURCES:
        return LINK_ONLY
    is_free = price_min == 0
    if source in _LINK_ONLY_PAID_SOURCES and not is_free:
        return LINK_ONLY
    return OPEN
