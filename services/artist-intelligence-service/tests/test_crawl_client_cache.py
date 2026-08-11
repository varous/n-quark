"""The canonical-artist enumeration read from crawl is cached process-wide for a short TTL so one
collector tick (backfill + per-candidate promotion lookups + reconciliation) does not re-page /entities
hundreds of times. See crawl_client._artists_cache and config.crawl_artists_cache_ttl_seconds."""

import pytest

import artist_intelligence_service.crawl_client as cc
from artist_intelligence_service.config import settings


@pytest.fixture(autouse=True)
def _clear_cache():
    cc._artists_cache.clear()
    yield
    cc._artists_cache.clear()


@pytest.mark.asyncio
async def test_repeated_reads_hit_crawl_once_within_ttl(monkeypatch):
    calls = {"n": 0}

    async def fake_fetch(self, *, limit, offset):
        calls["n"] += 1
        return [{"canonical_entity_id": "a1", "canonical_name": "Artist One"}]

    monkeypatch.setattr(settings, "crawl_artists_cache_ttl_seconds", 60.0)
    monkeypatch.setattr(cc.CrawlServiceClient, "_fetch_artists", fake_fetch)

    # Many independent clients (as promotion/backfill/reconciliation each construct their own) reading
    # the same page collapse to a single upstream fetch within the TTL window.
    for _ in range(25):
        rows = await cc.CrawlServiceClient().artists(limit=200, offset=0)
        assert rows == [{"canonical_entity_id": "a1", "canonical_name": "Artist One"}]
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_distinct_pages_are_cached_separately(monkeypatch):
    calls = {"n": 0}

    async def fake_fetch(self, *, limit, offset):
        calls["n"] += 1
        return [{"canonical_entity_id": f"off{offset}"}]

    monkeypatch.setattr(settings, "crawl_artists_cache_ttl_seconds", 60.0)
    monkeypatch.setattr(cc.CrawlServiceClient, "_fetch_artists", fake_fetch)

    await cc.CrawlServiceClient().artists(limit=200, offset=0)
    await cc.CrawlServiceClient().artists(limit=200, offset=200)
    await cc.CrawlServiceClient().artists(limit=200, offset=0)  # cached
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_ttl_zero_disables_cache(monkeypatch):
    calls = {"n": 0}

    async def fake_fetch(self, *, limit, offset):
        calls["n"] += 1
        return []

    monkeypatch.setattr(settings, "crawl_artists_cache_ttl_seconds", 0.0)
    monkeypatch.setattr(cc.CrawlServiceClient, "_fetch_artists", fake_fetch)

    await cc.CrawlServiceClient().artists()
    await cc.CrawlServiceClient().artists()
    assert calls["n"] == 2
