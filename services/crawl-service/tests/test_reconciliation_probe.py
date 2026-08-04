from crawl_service.reconciliation.probe import ProbeService


def _mk(volumes, previews):
    async def discover(source, limit):
        return {"ok": True, "refs": [f"{source}-{i}" for i in range(volumes[source])][:limit]}

    async def preview(source, ref):
        return previews[source]

    return ProbeService(discover=discover, preview=preview)


async def test_probe_recommends_source_with_better_structured_metadata():
    previews = {
        # district: rich structured fields present
        "district": {"status": 200, "body": {"starts_at": "2026-09-10T19:00:00", "venue": "Phoenix",
                                             "city": "Mumbai", "region": "MH", "price_min": 1499,
                                             "artists": ["Prateek Kuhad"]}},
        # skillbox: sparse (no city name/venue)
        "skillbox": {"status": 200, "body": {"starts_at": "2029-07-28T20:00:00"}},
    }
    ps = _mk({"district": 30, "skillbox": 40}, previews)
    out = await ps.compare(["district", "skillbox"], sample=3)
    assert out["recommendation"] == "district"
    assert out["results"]["district"]["structured_metadata_score"] > out["results"]["skillbox"]["structured_metadata_score"]


async def test_probe_reports_no_viable_source():
    async def discover(source, limit):
        return {"ok": True, "refs": ["x"]}

    async def preview(source, ref):
        return {"status": 503, "body": {}}

    ps = ProbeService(discover=discover, preview=preview)
    out = await ps.compare(["district", "skillbox"], sample=2)
    assert out["recommendation"] is None


async def test_probe_field_coverage_deterministic():
    previews = {"district": {"status": 200, "body": {"starts_at": "x", "venue": "y", "city": "z"}}}
    ps = _mk({"district": 5}, previews)
    a = await ps.probe_source("district", sample=3)
    b = await ps.probe_source("district", sample=3)
    assert a["field_coverage"] == b["field_coverage"]
