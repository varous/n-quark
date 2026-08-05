"""MediaService integration (sqlite + stub fetch + stub graph): identity dedup, transitions,
fetch-failure isolation, disappearance/reappearance, out-of-order, idempotency, linkage, graph link."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import func, select

from media_service import fetcher, transitions
from media_service.fetcher import FetchResult
from media_service.models import MediaAsset, MediaObservation, MediaTransition
from media_service.service import MediaService, ObserveInput
from media_service.storage import ContentAddressedStore
from tests.conftest import png_bytes

T0 = datetime(2026, 8, 5, tzinfo=UTC)


def _cfg(fetch=True, graph=False, storage=True):
    return SimpleNamespace(media_fetch_enabled=fetch, media_graph_link_enabled=graph)


def _fetched(data):
    return FetchResult(fetcher.FETCHED, http_status=200, content_type="image/png", data=data,
                       final_url="https://cdn/x.png")


def _svc(session_factory, tmp_path, results, *, graph=None, fetch=True, storage=True):
    async def fetch_fn(url):
        return results[url]
    store = ContentAddressedStore(str(tmp_path), enabled=storage, max_bytes=10_000_000)
    return MediaService(session_factory=session_factory, fetch_fn=fetch_fn, store=store,
                        graph=graph, cfg=_cfg(fetch, graph is not None, storage))


def _count(sf, model):
    with sf() as s:
        return s.scalar(select(func.count()).select_from(model))


async def test_exact_content_dedup_across_events(session_factory, tmp_path):
    img = png_bytes(10, 10, b"same")
    svc = _svc(session_factory, tmp_path,
               {"https://a/1.png": _fetched(img), "https://b/2.png": _fetched(img)})
    await svc.observe(ObserveInput("event:a", "boshow", "https://a/1.png", "POSTER", observed_at=T0))
    await svc.observe(ObserveInput("event:b", "district", "https://b/2.png", "POSTER", observed_at=T0))
    assert _count(session_factory, MediaAsset) == 1          # one content asset
    assert _count(session_factory, MediaObservation) == 2    # two source-specific observations


async def test_url_change_same_content_is_not_a_content_change(session_factory, tmp_path):
    img = png_bytes(10, 10, b"c")
    svc = _svc(session_factory, tmp_path,
               {"https://a/1.png": _fetched(img), "https://a/2.png": _fetched(img)})
    await svc.observe(ObserveInput("event:a", "boshow", "https://a/1.png", "POSTER", observed_at=T0))
    r2 = await svc.observe(ObserveInput("event:a", "boshow", "https://a/2.png", "POSTER",
                                        observed_at=T0 + timedelta(hours=1)))
    assert r2["transitions"] == [transitions.MEDIA_URL_CHANGED_SAME_CONTENT]
    assert r2["state"]["version"] == 1


async def test_changed_bytes_same_url_is_content_change(session_factory, tmp_path):
    svc = _svc(session_factory, tmp_path,
               {"https://a/1.png": _fetched(png_bytes(10, 10, b"v1"))})
    await svc.observe(ObserveInput("event:a", "boshow", "https://a/1.png", "POSTER", observed_at=T0))
    # same URL, different bytes on the second observe
    svc2 = _svc(session_factory, tmp_path, {"https://a/1.png": _fetched(png_bytes(10, 10, b"v2"))})
    r2 = await svc2.observe(ObserveInput("event:a", "boshow", "https://a/1.png", "POSTER",
                                         observed_at=T0 + timedelta(hours=1)))
    assert r2["transitions"] == [transitions.MEDIA_CONTENT_CHANGED] and r2["state"]["version"] == 2


async def test_fetch_failure_preserves_state(session_factory, tmp_path):
    img = png_bytes(10, 10, b"ok")
    svc = _svc(session_factory, tmp_path, {"https://a/1.png": _fetched(img)})
    r1 = await svc.observe(ObserveInput("event:a", "boshow", "https://a/1.png", "POSTER", observed_at=T0))
    asset_id = r1["media_asset_id"]
    fail = _svc(session_factory, tmp_path,
                {"https://a/1.png": FetchResult(fetcher.NOT_FOUND, http_status=404)})
    r2 = await fail.observe(ObserveInput("event:a", "boshow", "https://a/1.png", "POSTER",
                                         observed_at=T0 + timedelta(hours=1)))
    assert r2["fetch_status"] == fetcher.NOT_FOUND and r2["transitions"] == []
    assert r2["state"]["present"] is True and r2["state"]["current_media_asset_id"] == asset_id


async def test_authoritative_disappearance_and_reappearance(session_factory, tmp_path):
    svc = _svc(session_factory, tmp_path, {"https://a/1.png": _fetched(png_bytes(10, 10, b"x"))})
    await svc.observe(ObserveInput("event:a", "boshow", "https://a/1.png", "POSTER", observed_at=T0))
    gone = await svc.observe(ObserveInput("event:a", "boshow", None, "POSTER",
                                          observed_at=T0 + timedelta(hours=1), authoritative=True))
    assert gone["transitions"] == [transitions.MEDIA_DISAPPEARED] and gone["state"]["present"] is False
    back = _svc(session_factory, tmp_path, {"https://a/9.png": _fetched(png_bytes(10, 10, b"z"))})
    r = await back.observe(ObserveInput("event:a", "boshow", "https://a/9.png", "POSTER",
                                        observed_at=T0 + timedelta(hours=2)))
    assert transitions.MEDIA_REAPPEARED in r["transitions"] and r["state"]["present"] is True


async def test_out_of_order_does_not_rewrite(session_factory, tmp_path):
    svc = _svc(session_factory, tmp_path,
               {"https://a/1.png": _fetched(png_bytes(10, 10, b"a")),
                "https://a/2.png": _fetched(png_bytes(10, 10, b"b"))})
    await svc.observe(ObserveInput("event:a", "boshow", "https://a/1.png", "POSTER",
                                   observed_at=T0 + timedelta(hours=2)))
    r = await svc.observe(ObserveInput("event:a", "boshow", "https://a/2.png", "POSTER", observed_at=T0))
    assert r["out_of_order"] is True and r["transitions"] == []


async def test_idempotent_repeat(session_factory, tmp_path):
    svc = _svc(session_factory, tmp_path, {"https://a/1.png": _fetched(png_bytes(10, 10, b"a"))})
    inp = ObserveInput("event:a", "boshow", "https://a/1.png", "POSTER", observed_at=T0)
    await svc.observe(inp)
    r2 = await svc.observe(ObserveInput("event:a", "boshow", "https://a/1.png", "POSTER", observed_at=T0))
    assert r2["idempotent"] is True
    assert _count(session_factory, MediaObservation) == 1
    assert _count(session_factory, MediaTransition) == 1  # only FIRST_SEEN


async def test_size_rejection_records_but_changes_no_state(session_factory, tmp_path):
    svc = _svc(session_factory, tmp_path,
               {"https://a/1.png": FetchResult(fetcher.TOO_LARGE, http_status=200)})
    r = await svc.observe(ObserveInput("event:a", "boshow", "https://a/1.png", "POSTER", observed_at=T0))
    assert r["fetch_status"] == fetcher.TOO_LARGE and r["media_asset_id"] is None
    assert r["state"] is None  # no creative state created from a rejected fetch


async def test_graph_link_written_when_enabled(session_factory, tmp_path):
    calls = []

    class StubGraph:
        async def get_node(self, nid):
            return None

        async def upsert_media_asset(self, asset_id, props):
            calls.append(("node", asset_id))

        async def link_uses_creative(self, event_id, asset_id, props):
            calls.append(("edge", event_id, asset_id, props["asset_role"]))

    svc = _svc(session_factory, tmp_path, {"https://a/1.png": _fetched(png_bytes(10, 10, b"a"))},
               graph=StubGraph())
    await svc.observe(ObserveInput("event:a", "boshow", "https://a/1.png", "POSTER", observed_at=T0))
    assert any(c[0] == "node" for c in calls)
    assert any(c[0] == "edge" and c[1] == "event:a" and c[3] == "POSTER" for c in calls)


async def test_graph_link_failure_never_fails_observation(session_factory, tmp_path):
    class BoomGraph:
        async def get_node(self, nid):
            return None

        async def upsert_media_asset(self, *a, **k):
            raise RuntimeError("graph down")

        async def link_uses_creative(self, *a, **k):
            raise RuntimeError("graph down")

    svc = _svc(session_factory, tmp_path, {"https://a/1.png": _fetched(png_bytes(10, 10, b"a"))},
               graph=BoomGraph())
    r = await svc.observe(ObserveInput("event:a", "boshow", "https://a/1.png", "POSTER", observed_at=T0))
    assert r["media_asset_id"] is not None and r["state"]["present"] is True  # observation still succeeded
