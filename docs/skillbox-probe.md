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
