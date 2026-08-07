import os
from datetime import UTC, datetime, timedelta

# Isolated SQLite before importing the service config.
os.environ.setdefault("NQUARK_POSTGRES_URL", "sqlite://")
os.environ.setdefault("NQUARK_YOUTUBE_MAX_SEARCHES_PER_DAY", "50")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from artist_intelligence_service import identity as idlib
from artist_intelligence_service.models import ArtistDemandObservation, Base


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'demand.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture()
def db(session_factory):
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


# --------------------------------------------------------------------- fake signal-service client
class FakeSignal:
    """Stands in for signal-service acquisition. Configure per test; records call counts."""

    def __init__(self, *, search=None, channel=None, videos=None, mock=True):
        self._search = search or {}
        self._channel = channel or {}
        self._videos = videos or {}
        self.mock = mock
        self.calls = {"search": 0, "channel": 0, "videos": 0}
        self.fail_channel = False

    async def youtube_search(self, query, *, limit):
        self.calls["search"] += 1
        cands = self._search.get(query.strip().lower(), [])
        return {"query": query, "candidates": cands[:limit], "mock": self.mock}

    async def youtube_channel(self, channel_id):
        self.calls["channel"] += 1
        if self.fail_channel:
            raise RuntimeError("signal-service channel fetch failed")
        stats = self._channel.get(channel_id) or {}
        obs = [{"attribute": k, "value": v} for k, v in stats.items()]
        return {"channel_id": channel_id, "observations": obs, "mock": self.mock}

    async def youtube_videos(self, channel_id, *, limit):
        self.calls["videos"] += 1
        return {"channel_id": channel_id, "videos": (self._videos.get(channel_id) or [])[:limit],
                "mock": self.mock}


def candidate(channel_id, title, *, handle=None, topic=False, description=""):
    return {"channel_id": channel_id, "title": title, "handle": handle,
            "canonical_url": f"https://www.youtube.com/channel/{channel_id}",
            "topic_signal": topic, "description": description}


# --------------------------------------------------------------------- fake graph (supply side)
class FakeGraph:
    def __init__(self, *, events_for_artist=None, event_out=None, nodes=None):
        self._features = events_for_artist or {}     # artist_id -> [event neighbor dicts]
        self._out = event_out or {}                  # event_id -> [out neighbor dicts]
        self._nodes = nodes or {}
        self.base_url = "http://fake-graph"

    async def neighbors(self, node_id, *, direction="both", relationship=None):
        if direction == "in" and relationship == "FEATURES":
            return self._features.get(node_id, [])
        if direction == "out":
            return self._out.get(node_id, [])
        return []

    async def get_node(self, node_id):
        return self._nodes.get(node_id)


def event_neighbor(event_id, *, title=None, city=None, starts_at=None, status=None):
    return {"relationship": "FEATURES",
            "node": {"id": event_id, "properties": {"title": title, "city": city,
                                                     "starts_at": starts_at, "status": status}}}


# --------------------------------------------------------------------- seeding helper
def seed_obs(db, *, artist, provider, metric, value, observed_at, scope_type="GLOBAL",
             scope_id=None, scope_label=None, evidence_status="DIRECT_PROVIDER_VALUE",
             provider_timestamp=None, dedup_extra="", provenance=None, bucket=None):
    bucket = bucket or observed_at.date().isoformat()
    key = idlib.observation_key(canonical_artist_id=artist, provider=provider, metric=metric,
                                scope_type=scope_type, scope_id=scope_id, dedup_extra=dedup_extra,
                                bucket=bucket)
    row = ArtistDemandObservation(
        id=idlib.new_id(key), canonical_artist_id=artist, provider=provider, metric=metric,
        value_numeric=value, unit=None, scope_type=scope_type, scope_id=scope_id,
        scope_label=scope_label, provider_timestamp=provider_timestamp, observed_at=observed_at,
        evidence_status=evidence_status, provenance=provenance or {}, observation_key=key,
        created_at=datetime.now(UTC))
    db.add(row)
    db.flush()
    return row


def days_ago(n):
    return datetime.now(UTC) - timedelta(days=n)
