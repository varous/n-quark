"""Cross-inventory entity resolution (Phase 3.1).

Resolves platform-exclusive Boshow and District source events onto shared canonical artists,
venues, organizers and event series — so exclusive inventories become comparable through the
entities they share, without requiring the *same event* to appear on both platforms and without
collapsing distinct source events. Deterministic and explainable only (no LLM).
"""
