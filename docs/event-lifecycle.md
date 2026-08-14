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

