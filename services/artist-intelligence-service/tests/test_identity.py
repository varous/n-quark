"""Identity resolution (Phase 5A §7/§24): exact id, ambiguous, wrong-name, unresolved, idempotency,
and — critically — provider resolution never creates a canonical artist."""

from sqlalchemy import func, select

from artist_intelligence_service.models import ArtistExternalIdentity
from artist_intelligence_service.providers.youtube import YouTubeProvider
from artist_intelligence_service.service import DemandService
from tests.conftest import FakeSignal, candidate

ARTIST = "artist:arijit-singh"


def _svc(fake):
    return DemandService(youtube=YouTubeProvider(signal=fake))


async def test_exact_channel_id_resolves(db):
    fake = FakeSignal(search={"arijit singh": [candidate("UC_real", "Arijit Singh", topic=True)]})
    out = await _svc(fake).resolve_youtube(db, ARTIST, query="Arijit Singh",
                                           hints={"provider_id": "UC_real"})
    assert out["status"] == "RESOLVED"
    assert out["provider_id"] == "UC_real"


async def test_topic_channel_resolves_without_explicit_id(db):
    fake = FakeSignal(search={"nucleya": [candidate("UC_nuc", "Nucleya", topic=True)]})
    out = await _svc(fake).resolve_youtube(db, "artist:nucleya", query="Nucleya")
    assert out["status"] == "RESOLVED"


async def test_ambiguous_two_topic_channels(db):
    fake = FakeSignal(search={"the local train": [
        candidate("UC_a", "The Local Train", topic=True),
        candidate("UC_b", "The Local Train", topic=True)]})
    out = await _svc(fake).resolve_youtube(db, "artist:the-local-train", query="The Local Train")
    assert out["status"] == "AMBIGUOUS"      # equal names, no margin → never guessed


async def test_wrong_name_candidate_is_unresolved(db):
    fake = FakeSignal(search={"arijit singh": [candidate("UC_x", "Random Cover Uploads")]})
    out = await _svc(fake).resolve_youtube(db, ARTIST, query="Arijit Singh")
    assert out["status"] == "UNRESOLVED"


async def test_no_candidates_unresolved(db):
    out = await _svc(FakeSignal()).resolve_youtube(db, ARTIST, query="Nobody At All")
    assert out["status"] == "UNRESOLVED"
    assert out["reason"] == "no_candidates"


async def test_repeated_resolution_is_idempotent(db):
    fake = FakeSignal(search={"nucleya": [candidate("UC_nuc", "Nucleya", topic=True)]})
    svc = _svc(fake)
    await svc.resolve_youtube(db, "artist:nucleya", query="Nucleya")
    await svc.resolve_youtube(db, "artist:nucleya", query="Nucleya")
    n = db.execute(select(func.count()).select_from(ArtistExternalIdentity)
                   .where(ArtistExternalIdentity.provider_id == "UC_nuc")).scalar_one()
    assert n == 1


async def test_resolution_creates_no_canonical_artist(db):
    """No entity-service call is made; identity attaches to the given canonical id and nothing else."""
    fake = FakeSignal(search={"nucleya": [candidate("UC_nuc", "Nucleya", topic=True)]})
    out = await _svc(fake).resolve_youtube(db, "artist:nucleya", query="Nucleya")
    rows = db.execute(select(ArtistExternalIdentity)).scalars().all()
    assert {r.canonical_artist_id for r in rows} == {"artist:nucleya"}
    assert out["canonical_artist_id"] == "artist:nucleya"
