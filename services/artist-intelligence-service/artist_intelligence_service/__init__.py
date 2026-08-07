"""artist-intelligence-service — n-quark's public demand-side intelligence layer (Phase 5A).

Two evidence systems meet here through ``canonical_artist_id`` only:

    EVENT SUPPLY  (Boshow / District → event observations → Shadow Ledger)  — owned elsewhere
    PUBLIC DEMAND (YouTube / Google Trends → artist demand observations)     — owned here

Demand observations have their OWN provenance + observation semantics; they are never written into the
event Shadow Ledger, and canonical artist identity is never created here (it stays owned by the
entity/graph architecture — this service only *attaches* external platform identities to it).

Acquisition is NOT duplicated: YouTube fetching lives solely in signal-service (the existing ingestion
path, extended with search + recent-videos). This service calls signal-service for acquisition and owns
persistence, identity resolution, quota accounting, refresh scheduling, and deterministic read models.
"""
