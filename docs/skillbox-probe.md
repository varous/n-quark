# Skillbox source notes & quality probe report (Phase 4C)

Skillbox (`skillboxes.com`) is the third live ticketing source. It is a nationwide DIY/creator ticketing
platform (public_scrape; robots-allowed). Discovery is the event sitemap; extraction is the public JSON
API `POST /servers/v3/api/event-new/event-details {slug}`.

## Source identity & shape

- **Stable identity**: `EventId` (numeric) — used as `source_event_id`, never a title/date combination.
  The public slug is the canonical event slug and the source_record_id fallback.
- **Record-not-found**: `success:false` / empty `data` → `EventNotFound` → the scheduler records an
  authoritative absence (`SUCCESS_RECORD_ABSENT`), not a source failure.
- **Fields**: `event_display_name`, `date_from`, `venue_name`, `city_name`, `min_price`, `cover_image`,
  `status`. Notably **`city_name` doubles as the state** and dates are **timezone-naive**.

## Probe findings (live, bounded Kolkata-first pass, 2026-08-05)

A bounded validated pass over the first 25 sitemap events (`/v1/internal/sources/skillbox/quality`):

| Metric | Value | Reading |
|---|---|---|
| records discovered (fetched+validated) | 25 | bounded sample |
| accepted (Kolkata) | **0** | the sitemap head is not Kolkata-first |
| rejected | 1 (`PLACEHOLDER_DATE`) | a real far-future placeholder caught |
| out-of-scope (other cities) | 24 | correct city partitioning |
| title present/valid/specific | 1.0 / 1.0 / 1.0 | titles are good |
| city present/valid/specific | 1.0 / 1.0 / **0.76** | 24% of cities aren't in the verified map |
| venue present/valid/specific | 1.0 / 0.8 / 0.8 | ~20% placeholder venues (e.g. "To Be Decided") |
| date present/valid/specific | 1.0 / 0.96 / **0.0** | **all dates tz-naive**; 4% far-future placeholders |

Real rejected example: `taba-chake-india-tour-2026-Pre-Sale-Registration-…` → `PLACEHOLDER_DATE`
(a pre-sale registration shell with a far-future placeholder date). Real accepted-but-weak example (no
city filter): `DANCE WORKSHOP WITH VIREN CHAUHAN` (Bengaluru, venue "To Be Decided", 2027 — accepted on a
verified city + real title/date, venue flagged non-specific).

## Assessment

Skillbox is **real but low-quality** relative to Boshow/District: timezone-naive dates, placeholder
venues, far-future pre-sale/registration shells, and a sitemap that is not ordered by city. The Phase 4C
quality gate correctly:
- rejects placeholder dates, multiple-cities placeholders, numeric city ids without a mapping, generic
  locations without a real venue, spam/non-event pages, deleted shells, and missing identities **before**
  enrollment (no graph events for rejected records);
- normalizes tz-naive dates to IST **only** for verified Indian cities (never a blind guess);
- keeps direct-source geography separate from derived geography.

**Kolkata pilot honesty:** no Kolkata Skillbox event met validation in the bounded live sample, so **no
live cross-source convergence with Boshow/District is claimed** — the disjoint-cohort finding from Phase 3
persists. A deeper (paged) discovery would be needed to surface Kolkata Skillbox inventory; that is a
bounded follow-up, not a fabrication to inflate the pilot.

## Pipeline parity

Skillbox runs the **identical** capture path as District (discovery → validation → tracked_event →
scheduled_capture_job → signal fetch → graph projection → Shadow Ledger → enrichment → entity resolution →
media observation), enabled purely by adding `skillbox` to the crawl source-set env lists. Entity- and
media-resolution hooks remain best-effort; a media or entity failure never fails capture; retry/locking/
idempotency are unchanged; Boshow/District jobs are unaffected.

---

# Phase 4C.1 — Targeted discovery probe & decision (2026-08-07)

## 1. Discovery surfaces inspected (live)

| Surface | Result |
|---|---|
| city-filtered event API | **none public** — guessed `/servers/v3/api/…` list endpoints all 404; browse APIs live in lazy Angular chunks (reverse-engineering out of scope + borderline private) |
| city IDs | **yes** — `event-details` returns a stable `city_id` (e.g. Mumbai=`5`, Bengaluru=`1106620`) |
| city catalogue | **yes** — `sitemap-cities.xml` (46 cities incl. `/events-kolkata`) |
| city public pages | exist (`/events-kolkata`) but are **non-SSR SPA shells** — no embedded events, no JSON-LD |
| event sitemap | `sitemap-event.xml` — **24,401 URLs, single file, all-cities, not paginated, not city-ordered** |
| pagination | none on the sitemap or any public list endpoint |

**Conclusion:** the only reliable public bulk surface is the 24k-event all-cities sitemap (a full crawl is
out of scope) plus the cities sitemap and the per-slug `event-details` API. **No efficient city-targeted
discovery exists** without SPA reverse-engineering.

## 2. City-targeted strategy used

With no city API, the honest bounded strategy is a **stratified sitemap sample** (head/thirds/tail, hard
cap 30 fetches) filtered by the fetched event's `city_id`/`city_name`. This is bounded and never crawls
the catalogue. It is a probe, not a production discovery path.

## 3. Verified city mapping (`adapters/skillbox_cities.py`)

Built **only from source evidence** — every `city_id` read from `event-details` for events actually
observed: Mumbai `5`, Bengaluru `1106620`, Goa `1113278`, Hyderabad `1114881`, Thane `1132982`, Sonipat
`1131718`, Jaipur `1115282`/`1115279`, Guwahati `1114002`, Dehradun `1110654`, Gurugram `2790953`,
Shillong `1130829` — all `Asia/Kolkata`. **Kolkata is intentionally absent** (zero Kolkata events observed;
no id guessed). The map corroborates an unverified city name (`VERIFIED_BY_ID`) and derives region/tz.

## 4. Live probe results

Stratified sample of 30 events across the 24,401-URL sitemap: **0 Kolkata**. City distribution skewed to
Bengaluru (7), Goa (6), Hyderabad (2), Mumbai (1) and a long tail; ~6 records had no city. 16/30 fell in
current n-quark markets — but **none in Kolkata**. Skillbox records remain low-quality (tz-naive dates,
placeholder venues like "To Be Decided"/"Mutiple Cities", far-future pre-sale shells).

## 5. Skillbox source classification: **OPPORTUNISTIC_SOURCE**

- Stable event ids (`EventId`) and city ids ✅; repeatable `event-details` fetch ✅.
- Real validated inventory in **some covered markets** (Mumbai, Bengaluru) ✅.
- **Zero Kolkata inventory** in a fair sample ❌; **no efficient city-targeted discovery** ❌; weak date
  validity (tz-naive) and mixed venue specificity ❌; **no artist evidence** (event-details returns none).

Not `ACTIVE_SOURCE` (Kolkata-first goal unmet, no targeted discovery, weak quality); not fully
`LOW_VALUE_SOURCE` (real Mumbai/Bengaluru inventory + stable ids). **Kept disabled by default.**

## 6. Full pipeline proof (real Mumbai events)

Qualifying inventory exists in Mumbai, so two real events were run through the **complete** pipeline
(enrolled via `/sync`, captured via the normal scheduler path):
- `event:domi-jd-beck-who-asked-tour-mumbai` (venue *antiSOCIAL Lower Parel*): capture
  `SUCCESS_RECORD_PRESENT` → entity resolution `SUCCEEDED` → new canonical `venue:antisocial-lower-parel
  --mumbai` (source handle `skillbox:venue:antisocial-lower-parel`).
- `event:ad-design-show-2026-mumbai` (venue *Jio World Convention Centre*): capture
  `SUCCESS_RECORD_PRESENT` → entity resolution `SUCCEEDED` → new canonical venue; media hook
  `MEDIA_OBSERVED` (fetch classified `BLOCKED` — Skillbox image host).

## 7. Cross-source convergence (honest)

Both Skillbox Mumbai venues resolved to **new** city-scoped canonical venues (`sources=[skillbox]`) — **no
overlap** with District/Boshow entities in this cohort. **Zero real cross-source convergence** for the
enrolled Skillbox events; the `shared entity ≠ duplicate event` invariant held and **no duplicate event
was fabricated**. Artists did not resolve (Skillbox provides no artist evidence).

## 8. Quality snapshot (Mumbai-covered, discovery-time)

Stable ids ✅ · repeatable fetch ✅ · date valid ✅ but **date specific 0** (tz-naive) · venue specificity
~0.8 (placeholder venues present) · **artist coverage 0** · organizer coverage 0 · image coverage present
but fetch often `BLOCKED`. Capture success (enrolled cohort) high; entity-resolution creates **new**
canonical venues (no reuse yet).

## 9. Stop decision

Skillbox is **OPPORTUNISTIC / low-value for Kolkata**. Per the stop condition: keep the shared adapter,
keep the quality gates, **leave Skillbox disabled by default**, do **not** build a Skillbox-specific
discovery pipeline (no city API exists; sitemap sampling is inefficient and unbounded for a single city).
**Recommend directing acquisition effort elsewhere** — Skillbox may be re-enabled opportunistically for
Mumbai/Bengaluru, but is not worth further Kolkata-targeted optimization.

*Config caveats surfaced during the proof (follow-ups, not blockers): enrolling Skillbox via `/sync`
currently bypasses the quality gate at enrollment (raw discovery); media fetch of Skillbox images returns
`BLOCKED`; Shadow-Ledger state recording for Skillbox needs the source added to the shadow write path.*
