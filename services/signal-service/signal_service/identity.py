"""Namespaced external-identity aliases — the cross-source entity backbone.

Every adapter emits a signal *and* an identity cross-reference (MusicBrainz MBID, Google
Knowledge Graph mID). Folding those ids in as aliases on the canonical entity is what lets
different pipelines — a YouTube channel and a Google Trends query for the same act — converge
on one node. Ids are namespaced by scheme so an opaque external id never collides with a
source handle (``youtube:channel:...``) or with another scheme's id.
"""

MBID_SCHEME = "mbid"
KG_MID_SCHEME = "kgmid"


def mbid_alias(mbid: str) -> str:
    return f"{MBID_SCHEME}:{mbid}"


def kg_mid_alias(mid: str) -> str:
    return f"{KG_MID_SCHEME}:{mid}"
