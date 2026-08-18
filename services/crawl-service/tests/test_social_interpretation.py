"""Social interpretation & event-candidate integration (Phase 5C.2).

Evidence stays immutable; interpretation is a separate, versioned, deterministic derivation; event-bearing
evidence is projected into the EXISTING reconciliation surface (never a parallel registry) and NEVER
creates or mutates a canonical Event. No raw expressive content is ever persisted.
"""
from datetime import UTC, datetime

from sqlalchemy import select

from crawl_service.config import Settings
from crawl_service.models import EventMatchCandidate, SocialInterpretation, SocialMention
from crawl_service.reconciliation.views import EventView
from crawl_service.social_interpretation import classifier as C
from crawl_service.social_interpretation import projection as P
from crawl_service.social_interpretation.service import SocialInterpretationService

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
# EventView.starts_at is naive wall-clock (matcher semantics); build it from an aware value so the
# constructor carries tzinfo, then drop it — matching the committed test idiom, avoiding a naive ctor.
SEP20 = datetime(2026, 9, 20, 0, 0, tzinfo=UTC).replace(tzinfo=None)

# ---- enriched-extraction fixtures (§11): what signal-service persists as extracted_claims. The raw
# caption is a transient test input only; it is NEVER placed in a persisted row. ----
ANNOUNCE_TICKET = {  # (1)+(2) multi-class: announcement + ticketing, full identity
    "event_name": "Arijit Singh Live", "event_date": "2026-09-20", "event_time": "19:00",
    "city": "Kolkata", "venue_name": "Aquatica", "ticket_url": "https://example.org/t/arijit",
    "artists": ["Arijit Singh"],
    "signals": {"announcement": True, "ticketing": True},
    "surface_signals": ["announcement", "ticketing"]}
SOLD_OUT = {  # (3)
    "event_name": "Divine Live", "event_date": "2026-10-12", "city": "Mumbai",
    "venue_name": "MMRDA Grounds", "artists": ["Divine"],
    "signals": {"sold_out": True}, "surface_signals": ["sold_out"]}
CANCELLED = {  # (4)
    "event_name": "Autumn Sessions", "event_date": "2026-10-18", "city": "Bengaluru",
    "venue_name": "Phoenix Marketcity",
    "signals": {"cancellation": True}, "negation": True, "surface_signals": ["cancellation"]}
RESCHED = {  # (5)
    "event_name": "Winter Live", "event_date": "2026-11-25", "city": "Delhi",
    "venue_name": "JLN Stadium", "changes": {"date": {"from": "2026-11-10", "to": "2026-11-25"}},
    "signals": {"reschedule": True}, "surface_signals": ["reschedule"]}
VENUE_CH = {  # (6)
    "event_name": "City Beats", "event_date": "2026-10-30", "city": "Mumbai",
    "venue_name": "NSCI Dome",
    "changes": {"venue": {"from": "Gymkhana Grounds", "to": "NSCI Dome"}},
    "signals": {"venue_change": True}, "surface_signals": ["venue_change"]}
LINEUP = {  # (7)
    "event_name": "December Fest", "event_date": "2026-12-05", "city": "Pune",
    "venue_name": "Amphitheatre", "artists": ["Prateek Kuhad"],
    "signals": {"lineup_change": True}, "surface_signals": ["lineup_change"]}
ADDSHOW = {  # (8)
    "event_name": "Year End Live", "event_date": "2026-12-21", "city": "Hyderabad",
    "venue_name": "GMR Arena", "ticket_url": "https://example.org/t/yearend",
    "signals": {"additional_show": True, "ticketing": True},
    "surface_signals": ["additional_show", "ticketing"]}
PROMO = {  # (9) generic promotion, no resolvable event identity
    "signals": {"promotion": True}, "surface_signals": ["promotion"]}
AMBIG = {  # (10) ambiguous teaser, non-event
    "uncertainty": True, "signals": {}, "surface_signals": []}


def _cfg(**over):
    c = Settings(social_interpretation_enabled=True)
    for k, v in over.items():
        setattr(c, k, v)
    return c


def _mention(session_factory, claims, *, post_id="IG_1", version=1, is_current=True,
             prev=None, mid=None, entity="artist:arijit-singh"):
    mid = mid or f"m-{post_id}-{version}"
    with session_factory() as s, s.begin():
        s.add(SocialMention(
            id=mid, platform="INSTAGRAM", source_account="arijitsingh",
            platform_post_id=post_id, post_url=f"https://example.org/{post_id}",
            published_at=NOW, observed_at=NOW, canonical_entity_id=entity,
            canonical_entity_type="ARTIST", linked_canonical_entity_ids=[],
            extracted_claims=claims, evidence_role="OFFICIAL_ACCOUNT_EVIDENCE", confidence=0.5,
            parser_version="social-claim-extractor-2", content_hash=f"h-{post_id}-{version}",
            provenance={"raw_content_retention": "EPHEMERAL"}, version=version,
            is_current=is_current, previous_mention_id=prev, processing_status="UNPROCESSED",
            created_at=NOW, updated_at=NOW))
    return mid


def _svc(session_factory, cfg=None, provider=None):
    return SocialInterpretationService(session_factory, cfg or _cfg(),
                                       existing_views_provider=provider)


def _existing_arijit_view():
    return EventView(source="boshow", source_record_id="boshow:evt-arijit",
                     canonical_event_id="event:arijit-kolkata",
                     title="Arijit Singh Live", starts_at=SEP20,
                     city="Kolkata", venue_name="Aquatica", performers=["Arijit Singh"])


# ---- 1. classification never mutates the evidence ------------------------------------------------
def test_classification_leaves_evidence_unchanged(session_factory):
    mid = _mention(session_factory, ANNOUNCE_TICKET)
    with session_factory() as s:
        before = s.get(SocialMention, mid)
        snap = (before.extracted_claims, before.content_hash, before.version,
                before.is_current, before.evidence_role, before.confidence)
    _svc(session_factory).interpret_mention(mid)
    with session_factory() as s:
        after = s.get(SocialMention, mid)
        assert (after.extracted_claims, after.content_hash, after.version, after.is_current,
                after.evidence_role, after.confidence) == snap
        # ONLY workflow metadata may change
        assert after.processing_status == "INTERPRETED"


# ---- 2. interpretation is stored in its own layer ------------------------------------------------
def test_interpretation_stored_separately(session_factory):
    mid = _mention(session_factory, ANNOUNCE_TICKET)
    _svc(session_factory).interpret_mention(mid)
    with session_factory() as s:
        rows = s.execute(select(SocialInterpretation).where(
            SocialInterpretation.social_mention_id == mid)).scalars().all()
    assert len(rows) == 1 and rows[0].is_current and rows[0].evidence_version == 1


# ---- 3. same evidence + same classifier version = idempotent -------------------------------------
def test_idempotent_same_evidence_same_classifier(session_factory):
    mid = _mention(session_factory, ANNOUNCE_TICKET)
    svc = _svc(session_factory)
    svc.interpret_mention(mid)
    svc.interpret_mention(mid)  # re-run
    with session_factory() as s:
        rows = s.execute(select(SocialInterpretation).where(
            SocialInterpretation.social_mention_id == mid)).scalars().all()
    assert len(rows) == 1  # no new version


# ---- 4. changed classifier version preserves the prior interpretation ----------------------------
def test_classifier_version_change_preserves_prior(session_factory, monkeypatch):
    mid = _mention(session_factory, ANNOUNCE_TICKET)
    svc = _svc(session_factory)
    monkeypatch.setattr(C, "CLASSIFIER_VERSION", "social-classifier-0")
    svc.interpret_mention(mid)
    monkeypatch.setattr(C, "CLASSIFIER_VERSION", "social-classifier-9")
    svc.interpret_mention(mid)
    with session_factory() as s:
        rows = s.execute(select(SocialInterpretation).where(
            SocialInterpretation.social_mention_id == mid
        ).order_by(SocialInterpretation.version)).scalars().all()
    assert [r.version for r in rows] == [1, 2]
    assert rows[0].classifier_version == "social-classifier-0" and not rows[0].is_current
    assert rows[1].classifier_version == "social-classifier-9" and rows[1].is_current
    assert rows[1].previous_interpretation_id == rows[0].id  # lineage preserved, prior not rewritten


# ---- 5. multi-label classification ---------------------------------------------------------------
def test_multi_label(session_factory):
    out = _svc(session_factory).interpret_mention(_mention(session_factory, ANNOUNCE_TICKET))
    assert set(out["claim_types"]) == {C.ANNOUNCEMENT, C.TICKETING}
    assert out["primary_claim_type"] == C.TICKETING  # ticketing outranks announcement


# ---- 6. SELL_OUT stays a source claim, never verified sell-through -------------------------------
def test_sell_out_stays_a_claim(session_factory):
    out = _svc(session_factory).interpret_mention(_mention(session_factory, SOLD_OUT))
    assert C.SELL_OUT_CLAIM in out["claim_types"]
    assert "SELL_OUT_SOURCE_CLAIM_ONLY" in out["reason_codes"]


# ---- 7. ambiguous evidence stays UNKNOWN + non-event ---------------------------------------------
def test_ambiguous_is_unknown_non_event(session_factory):
    out = _svc(session_factory).interpret_mention(_mention(session_factory, AMBIG))
    assert out["claim_types"] == [] and out["primary_claim_type"] == C.UNKNOWN
    assert out["event_bearing"] is False


# ---- 8. event-bearing decision is deterministic --------------------------------------------------
def test_event_bearing_deterministic(session_factory):
    yes = _svc(session_factory).interpret_mention(_mention(session_factory, ANNOUNCE_TICKET))
    no = _svc(session_factory).interpret_mention(_mention(session_factory, PROMO, post_id="IG_PROMO"))
    assert yes["event_bearing"] is True
    assert no["event_bearing"] is False and "GENERIC_PROMOTION" in no["reason_codes"]


# ---- 9. a non-event interpretation creates no candidate ------------------------------------------
def test_non_event_creates_no_candidate(session_factory):
    svc = _svc(session_factory, provider=None)
    svc.interpret_mention(_mention(session_factory, PROMO, post_id="IG_PROMO"),
                          existing=[_existing_arijit_view()])
    with session_factory() as s:
        assert s.execute(select(EventMatchCandidate)).scalars().all() == []


# ---- 10. candidate provenance traces to the EXACT evidence version -------------------------------
def test_candidate_traces_to_exact_evidence_version(session_factory):
    mid = _mention(session_factory, ANNOUNCE_TICKET)
    out = _svc(session_factory).interpret_mention(mid, existing=[_existing_arijit_view()])
    assert out["event_candidate_status"] == P.MATCHED_EXISTING
    with session_factory() as s:
        cand = s.execute(select(EventMatchCandidate)).scalar_one()
        interp = s.execute(select(SocialInterpretation).where(
            SocialInterpretation.social_mention_id == mid)).scalar_one()
    assert cand.left_source == "social" and cand.left_source_record_id == mid
    assert cand.right_canonical_event_id == "event:arijit-kolkata"
    assert interp.event_match_candidate_id == cand.id
    assert interp.matched_canonical_event_id == "event:arijit-kolkata"


# ---- 11. sparse evidence does NOT weaken the existing thresholds ---------------------------------
def test_sparse_evidence_does_not_auto_match(session_factory):
    sparse = {"event_name": "Mystery Gig", "signals": {"announcement": True},
              "surface_signals": ["announcement"]}   # name only, no date/venue/city
    existing = EventView(source="boshow", source_record_id="boshow:x",
                         canonical_event_id="event:x", title="Mystery Gig Night",
                         starts_at=SEP20, city="Kolkata")
    out = _svc(session_factory).interpret_mention(
        _mention(session_factory, sparse, post_id="IG_SPARSE"), existing=[existing])
    assert out["event_bearing"] is True
    assert out["event_candidate_status"] != P.MATCHED_EXISTING  # never a false auto-match


# ---- 12. event-bearing social evidence CAN match an existing event via existing reconciliation ----
def test_matches_existing_event_via_reconciliation(session_factory):
    out = _svc(session_factory).interpret_mention(
        _mention(session_factory, ANNOUNCE_TICKET), existing=[_existing_arijit_view()])
    assert out["event_candidate_status"] == P.MATCHED_EXISTING
    assert out["matched_canonical_event_id"] == "event:arijit-kolkata"


# ---- 13. an unresolved social hypothesis creates nothing canonical -------------------------------
def test_new_event_hypothesis_creates_no_canonical(session_factory):
    out = _svc(session_factory).interpret_mention(
        _mention(session_factory, RESCHED, post_id="IG_RES"), existing=[])  # nothing to match
    assert out["event_candidate_status"] == P.NEW_EVENT_HYPOTHESIS
    assert out["matched_canonical_event_id"] is None
    with session_factory() as s:
        assert s.execute(select(EventMatchCandidate)).scalars().all() == []  # no candidate either


# ---- 14. two versions of an edited post are interpreted independently ----------------------------
def test_edited_post_versions_interpreted_independently(session_factory):
    v1 = _mention(session_factory, {**VENUE_CH, "venue_name": "Gymkhana Grounds",
                                    "changes": {}}, post_id="IG_EDIT", version=1, is_current=False,
                  mid="m-edit-1")
    v2 = _mention(session_factory, VENUE_CH, post_id="IG_EDIT", version=2, is_current=True,
                  prev="m-edit-1", mid="m-edit-2")
    svc = _svc(session_factory)
    o1 = svc.interpret_mention(v1)
    o2 = svc.interpret_mention(v2)
    assert o1["interpretation_id"] != o2["interpretation_id"]
    with session_factory() as s:
        i1 = s.get(SocialInterpretation, o1["interpretation_id"])
        i2 = s.get(SocialInterpretation, o2["interpretation_id"])
    assert i1.social_mention_id == v1 and i2.social_mention_id == v2
    assert i1.interpreted_fields.get("venue_name") == "Gymkhana Grounds"
    assert i2.interpreted_fields.get("venue_name") == "NSCI Dome"
    assert C.VENUE_CHANGE in i2.claim_types


# ---- 15. no raw expressive content is ever persisted on an interpretation ------------------------
def test_no_raw_content_persisted(session_factory):
    caption = "Kolkata! Live in concert 20 Sep 2026 at Aquatica. Tickets live now."
    mid = _mention(session_factory, ANNOUNCE_TICKET)
    _svc(session_factory).interpret_mention(mid)
    with session_factory() as s:
        interp = s.execute(select(SocialInterpretation)).scalar_one()
        blob = str({c.name: getattr(interp, c.name) for c in interp.__table__.columns})
    assert caption not in blob and "Live in concert" not in blob


# ---- 16. run_once is bounded, retry-safe, and advances only workflow status ----------------------
def test_run_once_processes_unprocessed_only(session_factory):
    for i, claims in enumerate((ANNOUNCE_TICKET, SOLD_OUT, PROMO, AMBIG)):
        _mention(session_factory, claims, post_id=f"IG_R{i}", mid=f"m-r{i}")
    svc = _svc(session_factory, provider=None)
    import asyncio
    r1 = asyncio.run(svc.run_once(trace=True))
    assert r1["metrics"]["selected"] == 4 and r1["metrics"]["interpreted"] == 4
    # re-run: nothing left UNPROCESSED → idempotent
    r2 = asyncio.run(svc.run_once())
    assert r2["metrics"]["selected"] == 0
    with session_factory() as s:
        assert s.execute(select(SocialMention).where(
            SocialMention.processing_status == "UNPROCESSED")).scalars().all() == []


# ---- 17. reads: history + coverage ---------------------------------------------------------------
def test_history_and_coverage(session_factory, monkeypatch):
    mid = _mention(session_factory, ANNOUNCE_TICKET)
    svc = _svc(session_factory)
    monkeypatch.setattr(C, "CLASSIFIER_VERSION", "social-classifier-0")
    svc.interpret_mention(mid)
    monkeypatch.setattr(C, "CLASSIFIER_VERSION", "social-classifier-1")
    svc.interpret_mention(mid)
    hist = svc.interpretation_history(mid)
    assert hist["count"] == 2 and [v["version"] for v in hist["versions"]] == [1, 2]
    cov = svc.coverage()
    assert cov["current_interpretations"] == 1 and cov["total_interpretation_versions"] == 2
