# Event lifecycle

Event lifecycle has two independent dimensions.

- `provider_lifecycle`: a source claim (`SCHEDULED`, `CANCELLED`, `POSTPONED`,
  `RESCHEDULED`, or `UNKNOWN`). It is evidence, not a clock-derived conclusion.
- `temporal_state`: a read-time relationship to `evaluated_at` (`UPCOMING`, `ONGOING`, `PAST`,
  or `UNKNOWN`). `PAST` never means completed, attended, or successful.

The Admin BFF derives temporal state; React only renders the returned contract. With trustworthy
start and end instants, before/start-through-end/after yields UPCOMING/ONGOING/PAST. A start-only
instant becomes UPCOMING before it and PAST after it; ONGOING is not inferred because duration is
unknown. A date-only value is compared in an explicit source-local timezone. Yesterday/tomorrow are
PAST/UPCOMING; local today is UNKNOWN because inventing midnight or an all-day duration would be
false precision. A naïve clock timestamp with no established source timezone remains UNKNOWN.

The derivation returns its basis, effective timestamps, precision, timezone and evaluation time.
It does not mutate raw observations, so state naturally changes as time advances. District JSON-LD
`startDate`, `endDate`, and `eventStatus` are preserved, along with original timestamp strings.

Collection cadence remains deterministic and configurable. Upcoming events use far/mid/final/event-
day bands. Past events receive bounded T+1/T+3/T+7 captures, then continuous polling stops while the
Event and its evidence remain queryable. Cancellation receives one configurable confirmation capture
(24 hours by default), then stops. No listing presence/absence implies completion, attendance, or
sell-through.

## Production closure (2026-08-16)

The crawl scheduler and Admin BFF use the same lifecycle fixture contract: end time wins when present;
otherwise start time is the boundary; date-only today and missing/naive time evidence remain UNKNOWN.
Provider lifecycle (`SCHEDULED`, `CANCELLED`, `POSTPONED`, `RESCHEDULED`, `UNKNOWN`) is orthogonal and
retained per source, so disagreement is representable rather than collapsed. Ongoing events use the
event-day capture window; recently-past events receive bounded follow-ups; old-past events stop normal
polling; cancelled events receive bounded confirmation; postponed events use reduced monitoring.

The deployed diagnostic reported 468 canonical events (170 UPCOMING, 0 ONGOING, 298 PAST, 0 UNKNOWN),
all 468 with provider lifecycle UNKNOWN in the migrated historical cohort, no current disagreement,
288 recently-past rows awaiting bounded final capture, and 10 legacy old-past schedules still awaiting
cadence recalculation. The diagnostic is observational and does not fabricate or rewrite source claims.

## Cadence convergence + lifecycle preservation (5B.3.3, 2026-08-17)

The remaining legacy old-past normal-poll schedules converged to **zero** through the normal capture
path (`capture-now` → cadence recalculation), with no event-date edits and no job deletion. The
converged rows were District listings carrying a placeholder start (today's date); recalculation moved
them to bounded post-event follow-ups (offsets 1/3/7 → terminal), which stay idempotent on re-capture.

Provider lifecycle remains UNKNOWN for all 468 canonical events. Fresh District captures proved the
extract → observe → enrich → persist plumbing runs end-to-end (city/venue/start flow through), but the
District/Boshow payloads carry no `eventStatus`, so no value is available to preserve. UNKNOWN is a valid
state; provider lifecycle is never inferred from age, and no historical backfill is justified because the
immutable raw evidence contains no lifecycle status.
