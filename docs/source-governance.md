# Supply source governance

Supply sources are assessed as evidence channels, not ranked with a universal score. A durable source
descriptor records its role, acquisition mode, provenance level, automation posture, whether continuous
collection is allowed, claim authority, policy-check date, and first-party policy/reference URL.

Disposition is `PRODUCTION`, `SUPPLEMENTARY`, or `REJECT`. A source reaches production only after a
policy-safe bounded probe measures valid live-entertainment records, canonical overlap, unique events,
entity contribution, city contribution, field completion, timestamp precision, price evidence, and
lifecycle evidence. Probe records never create canonical entities.

## 2026-08-14 findings

District remains primary discovery/ticketing evidence and Boshow remains an independent ticketing
source. District's single sitemap still mixed historical and current inventory and its former arbitrary
first-N discovery was confirmed unsafe. Discovery now scans the bounded sitemap document, prefers
current/future date hints, retains fresh unknown-date pages, and omits clearly historical hints. Slug
dates affect discovery ordering only; extracted source fields remain authoritative.

AllEvents is the leading additional-source candidate. Its first-party developer material advertises
REST JSON, stable IDs, city/date/category discovery, event details, organizers and production rate
limits. Current access is request/trial-based. Because this repository has no authorized API credential,
the required 100–500-record probe was not run and no production adapter was enabled. Current disposition:
`SUPPLEMENTARY / ACCESS_PENDING`, not `PRODUCTION`.

References checked 2026-08-14:

- https://developer.allevents.in/apis
- https://allevents.in/pages/events-api

## Executable source descriptors (2026-08-16)

The signal service exposes inspectable descriptors for role, acquisition mode, provenance, automation
posture, continuous-collection permission, claim authority, and policy reference. District is a production
public SSR/JSON-LD source with schedule, venue, organizer, ticketing, and lifecycle authority. Boshow is a
production public logged-out API source with schedule, venue, lineup, and ticketing authority; organizer
is deliberately absent. AllEvents is represented as supplementary, authorized-API-required,
continuous-collection-disabled, and ACCESS_PENDING.

District's deployed ranked discovery selected 100 current/future slugs spanning 2026-08-16 through
2026-08-20 in 0.63 seconds. The tracked baseline is 434 District listings (414 canonical events, 95
cities) and 66 Boshow listings (54 canonical events, 6 cities), with no canonical event overlap. This is
observed contribution, not total-market coverage or a universal source score.
