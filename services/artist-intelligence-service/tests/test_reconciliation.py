"""Phase 5A.3.2 — canonical artist state reconciliation diagnostics + orphan audit.

Verifies the artist-intelligence read-side reconciliation view: canonical enumeration vs graph nodes vs
demand-referenced artists, orphan detection, and safe degradation when crawl/graph are unavailable.
Canonical creation/registry writes are owned by crawl (covered in crawl tests/test_governance.py).
"""

from datetime import UTC, datetime

from sqlalchemy import select

from artist_intelligence_service import candidates as cand, universe
from artist_intelligence_service.graph_client import GraphServiceClient
from artist_intelligence_service.models import ArtistExternalIdentity
from tests.conftest import seed_obs, days_ago


class FakeCrawl:
    def __init__(self, artists=None, fail=False):
        self._artists = artists or []
        self.fail = fail

    async def artists(self, *, limit=200, offset=0):
        if self.fail:
            raise RuntimeError("crawl down")
        return self._artists[offset:offset + limit]


def _seed_identity(db, artist, status="RESOLVED", provider="YOUTUBE"):
    now = datetime.now(UTC)
    db.add(ArtistExternalIdentity(
        id=f"id-{artist}", canonical_artist_id=artist, provider=provider, identity_type="CHANNEL_ID",
        provider_id=f"UC_{artist}", status=status, confidence=1.0, first_seen_at=now,
        created_at=now, updated_at=now, identity_metadata={}, provenance={}))
    db.flush()


async def test_reconciliation_reports_divergence_and_orphans(db, monkeypatch):
    # canonical registry has one artist; demand references two (one orphan)
    crawl = FakeCrawl(artists=[{"canonical_entity_id": "artist:in-registry", "canonical_name": "In Registry"}])
    _seed_identity(db, "artist:in-registry")
    _seed_identity(db, "artist:orphan-only")
    seed_obs(db, artist="artist:orphan-only", provider="YOUTUBE", metric="YOUTUBE_SUBSCRIBERS",
             value=100, observed_at=days_ago(1))
    db.commit()

    async def fake_nodes(self, *, limit=500):
        return [{"id": "artist:in-registry", "properties": {"display_name": "In Registry"}}]
    monkeypatch.setattr(GraphServiceClient, "list_artist_nodes", fake_nodes)

    r = await universe._reconciliation_diagnostics(db, crawl=crawl)
    assert r["canonical_registry_available"] is True
    assert r["canonical_registry_artists"] == 1
    assert r["graph_artist_nodes"] == 1
    assert r["artists_referenced_by_demand_identities"] == 2
    assert r["orphan_demand_artist_references"] == 1
    assert r["orphan_sample"] == ["artist:orphan-only"]     # audited, never rewritten


async def test_reconciliation_degrades_when_crawl_unavailable(db, monkeypatch):
    async def boom(self, *, limit=500):
        raise RuntimeError("graph down")
    monkeypatch.setattr(GraphServiceClient, "list_artist_nodes", boom)
    r = await universe._reconciliation_diagnostics(db, crawl=FakeCrawl(fail=True))
    assert r["canonical_registry_available"] is False
    assert r["graph_artist_nodes"] is None
    assert r["orphan_demand_artist_references"] == 0        # cannot assert orphans without the registry


async def test_no_orphans_when_all_referenced_artists_in_registry(db, monkeypatch):
    crawl = FakeCrawl(artists=[{"canonical_entity_id": "artist:a", "canonical_name": "A"}])
    _seed_identity(db, "artist:a")
    db.commit()
    async def fake_nodes(self, *, limit=500):
        return [{"id": "artist:a", "properties": {"display_name": "A"}}]
    monkeypatch.setattr(GraphServiceClient, "list_artist_nodes", fake_nodes)
    r = await universe._reconciliation_diagnostics(db, crawl=crawl)
    assert r["orphan_demand_artist_references"] == 0


async def test_artist_universe_includes_reconciliation_block(db, monkeypatch):
    async def fake_nodes(self, *, limit=500):
        return []
    monkeypatch.setattr(GraphServiceClient, "list_artist_nodes", fake_nodes)
    out = await universe.build_artist_universe(db, crawl=FakeCrawl(artists=[]))
    assert "canonical_reconciliation" in out
    assert out["canonical_reconciliation"]["authoritative_source"].startswith("crawl entity-resolution")
