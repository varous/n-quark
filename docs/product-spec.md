# n-quark
## Master Context & Product Specification

Version: 0.1

---

# Vision

n-quark is an Intelligence Operating System for the live entertainment industry.

Its purpose is to continuously observe the live entertainment ecosystem, convert observations into structured knowledge, and generate intelligence that enables better decisions.

Every product—ticketing, CRM, venue management, sponsor tools, artist tools, AI agents—should consume intelligence from n-quark instead of building their own isolated datasets.

---

# Mission

Build the canonical intelligence layer for live entertainment.

---

# Product Philosophy

- Knowledge compounds.
- Observations are immutable.
- Intelligence must be explainable.
- Deterministic computation and AI reasoning are separate.
- External platforms provide signals, not truth.
- Market value is inferred, never scraped.
- Every event permanently improves the platform.

> **Cross-reference (additive):** these principles are extended — not replaced — by the appended
> section *[Independent Market Observation and Temporal Data Moat](#independent-market-observation-and-temporal-data-moat)*,
> which reframes the data asset as a longitudinal, cross-platform observation layer rather than a
> transaction ledger.

---

# What n-quark Is

A platform that:

- Observes
- Understands
- Predicts
- Recommends

It is infrastructure, not an end-user application.

---

# Core Concepts

## Observation

An immutable piece of evidence collected from a source.

Example:

Artist X performed at Venue Y on Date Z.

Every observation stores:

- source
- timestamp
- confidence
- evidence

> **Cross-reference (additive):** observations remain immutable. A richer *temporal* structure
> (`observed_at`, `effective_at`, `first_seen_at`, `valid_from`/`valid_to`, `disappeared_at`,
> `content_hash`, `previous_observation_id`) is proposed as **additive, optional fields** in the
> appended moat section — captured as state *transitions*, never by overwriting prior state.

---

## Entity

A real-world object.

Examples:

- Artist
- Event
- Organizer
- Venue
- Community
- Sponsor
- City
- Genre

---

## Knowledge

Relationships inferred from observations.

Examples:

Artist → Performs At → Venue

Organizer → Runs → Community

Community → Hosts → Event

---

## Intelligence

Insights derived from knowledge.

Examples:

- demand score
- momentum
- audience overlap
- venue fit
- forecast
- recommendation

---

# Platform Layers

1. Ingestion
2. Observation Engine
3. Knowledge Graph
4. Deterministic Analytics
5. Market Feature Store
6. Intelligence Engines
7. APIs

---

# Intelligence Engines

Initially:

- Community Intelligence
- Artist Intelligence
- Venue Intelligence
- Organizer Intelligence
- Sponsor Intelligence
- Audience Intelligence
- Creative Intelligence

Future:

- Forecast Engine
- Opportunity Engine
- Recommendation Engine
- Simulation Engine

---

# Artist Market Value Engine

Market Value is a function of:

- historical performance
- regional demand
- audience affinity
- venue fit
- organizer history
- pricing
- external signals
- market conditions

Digital popularity is only one signal.

---

# Signal Providers

Examples:

- Spotify
- YouTube
- Instagram
- Facebook
- Bandsintown
- Songkick
- Google Trends
- Weather
- Public calendars
- Ticketing platforms

Signals are normalized before entering the platform.

> **Cross-reference (additive):** first-party ticketing/commerce integrations are treated as
> *opportunistic, not foundational* (see the appended moat section). The defensible asset is built
> from **continuous public-state capture** + owned audience-intent (via crawl-space) + a limited
> verified calibration set — not from privileged access to any platform's ticket ledger.

---

# Product Principles

- Never mutate observations.
- Every insight should be explainable.
- AI is never the source of truth.
- Store provenance for everything.
- Keep services independent.
- Version schemas and calculations.
- APIs expose intelligence, not database tables.

---

# Long-Term Goal

Become the Bloomberg Terminal for live entertainment.

> **Cross-reference (additive):** the *how we get there when granular first-party data is
> unavailable* is set out in *[Revised Product Position](#revised-product-position)* within the
> appended section below.

---

# Independent Market Observation and Temporal Data Moat

> **Status:** this is an **additive strategy augmentation** (appended, not a rewrite). It extends
> the existing thesis — signal → immutable observation → canonical entity → graph → analytics →
> compliant feed — with the acquisition and inference strategy required when granular first-party
> ticketing/commerce integrations are unavailable. **No existing section above is superseded.** The
> existing deterministic-before-AI principle and the compliance, provenance, no-PII,
> redistribution-tier and canonical-identity invariants remain unchanged.

## Epistemic status legend

Two independent dimensions are tagged throughout this section. Do not conflate them.

**Build status** (roadmap dimension):
- `[CURRENT]` — implemented and, where noted, tracer-validated in the current build.
- `[PROPOSED]` — designed here as near-term work; not yet built.
- `[FUTURE]` — later-stage or optional; explicitly **not** an MVP dependency.
- `[INVARIANT]` — a rule that must hold across all builds (reaffirmed, not new).

**Data epistemics** (how any *value* the platform emits must be qualified — a first-class product
concept, mirrored by the Evidence Classes below):
- **Verified** — confirmed by a trusted/audited source or machine aggregate.
- **Observed public state** — directly captured from a public source at a timestamp (time-varying).
- **Reported outcome** — a claim by a party (organizer/sponsor/social), unverified.
- **Model estimate** — inferred by a deterministic rule or model; must carry a confidence and range.
- **Unknown** — insufficient evidence to estimate responsibly; must be surfaced as such.

The system must **never** silently blend these. Reported ≠ Verified ≠ Estimated ≠ Observed.

---

## Strategic correction — first-party integrations are opportunistic, not foundational  `[INVARIANT]`

n-quark must **not** depend on granular first-party integrations with major ticketing platforms,
promoters, venues, or commerce systems. Such businesses are unlikely to share — because these
datasets are their own competitive moats:

- order-level sales; inventory history; ticket-buyer records; checkout funnels; refunds;
- held inventory; complimentary allocations; scan attendance; settlement economics;
- artist fees; marketing spend; sponsor deal values.

First-party integrations may be pursued **opportunistically**, but they must never be a foundational
dependency of the product thesis. n-quark does not need to become the universal transaction ledger
for live entertainment; it should become **the most complete independent, longitudinal and
explainable observation layer for the Indian live-entertainment market.**

The defensible data asset is created from:

```text
Continuous public-state capture
+ canonical entity resolution
+ event lifecycle history
+ ticket-state transitions
+ campaign-pressure observation
+ owned audience-intent signals
+ post-event outcome evidence
+ a limited verified calibration set
+ privacy-safe benchmark contributions
```

This produces a cross-platform market view **no single ticketing platform can easily create**,
because each platform sees only its own inventory and transactions.

---

## Core moat definition  `[PROPOSED]`

A one-time scrape **can** reconstruct current state: title, lineup, venue, ticket price,
description, poster, availability.

A one-time scrape **cannot** reliably reconstruct the *history*:

- when the event was first announced; when tickets first went on sale;
- how prices changed; when ticket phases became unavailable; whether inventory reopened;
- whether the venue was upgraded or downgraded; when discounts/promo codes were introduced;
- how campaign intensity changed; when artists or sponsors were added;
- when the event was postponed/cancelled; what competing events were visible at the time;
- how public audience intent evolved; what outcome evidence appeared after the event;
- whether the property later expanded, contracted or disappeared.

Therefore the moat is **not** the event record. The moat is:

```text
event × observed state × timestamp × evidence
```

The proprietary unit becomes a **reconstructed commercial event lifecycle**.

---

## Shadow Market Ledger  `[PROPOSED]`

Introduce the **Shadow Market Ledger**: a longitudinal reconstruction of public and independently
observable commercial activity around an event. It is **not** a transaction ledger and must **never**
be represented as one (data epistemics: *observed public state* + *reported outcome*, never *verified
sales*).

For each event, observe and store **transitions** (never replace prior state with newest state):

```text
Event discovered
Event first announced
Presale announced
Presale opened
General sale opened

Ticket class appeared
Ticket class became unavailable
Ticket class reopened
Ticket class was withdrawn
Displayed price changed
Discount appeared
Promo code appeared
Purchase limit changed

"Selling fast" appeared
"Limited tickets" appeared
"Sold out" was claimed
Additional show was announced
Venue was upgraded
Venue was downgraded

Artist was added
Artist was removed
Sponsor was added
Creative was replaced

Event was postponed
Event was rescheduled
Event was cancelled
Listing disappeared
Event was completed
```

---

## Event commercial state machines  `[PROPOSED]`

Transitions and their **timing** are analytically more important than final-state labels.

**Event states:**

```text
DISCOVERED
ANNOUNCED
PRESALE_PENDING
PRESALE_ACTIVE
GENERAL_SALE_ACTIVE
LIMITED_AVAILABILITY
PUBLICLY_UNAVAILABLE
SOLD_OUT_CLAIMED
COMPLETED
CANCELLED
POSTPONED
RESCHEDULED
REMOVED
```

**Ticket-class states:**

```text
ANNOUNCED
AVAILABLE
UNAVAILABLE
SOLD_OUT_CLAIMED
REOPENED
WITHDRAWN
PRICE_CHANGED
```

**Campaign states:**

```text
NOT_OBSERVED
ORGANIC_ACTIVE
PAID_ACTIVE
PROMO_ACTIVE
DISCOUNT_ACTIVE
FINAL_PUSH
ENDED
```

Example transition chains:

```text
GENERAL_SALE_ACTIVE → LIMITED_AVAILABILITY → PUBLICLY_UNAVAILABLE
AVAILABLE → UNAVAILABLE → REOPENED
ORGANIC_ACTIVE → PAID_ACTIVE → DISCOUNT_ACTIVE
```

`SOLD_OUT_CLAIMED` and `PUBLICLY_UNAVAILABLE` are **observed/reported** states — never emit them as
*verified* sell-through.

---

## Temporal observation model  `[PROPOSED]` — additive migration only

The existing immutable observation model (see *Core Concepts → Observation* and
`docs/ontology.md → Observation Schema`) is **extended, not overwritten**. Mutable-state observations
gain temporal structure via **optional/additive columns** (compatible Alembic migration; existing
`observations` rows and the current write/read contracts stay valid):

```text
observed_at          # when n-quark captured the state
effective_at         # when the state became true in the real lifecycle, if known
source_published_at  # when the source claims it published the info
first_seen_at        # first time n-quark observed the state
last_seen_at         # last time the state remained observable
valid_from           # start of inferred validity period
valid_to             # end of inferred validity period
disappeared_at       # when the source content/state was no longer visible
```

Proposed normalized observation record (a superset of today's schema; new fields nullable/optional):

```text
observation_id
entity_id
metric_name
value
unit
observed_at
effective_at
source_published_at
first_seen_at
last_seen_at
valid_from
valid_to
disappeared_at
source_id
source_record_id
raw_snapshot_reference
content_hash
previous_observation_id
capture_status
confidence
redistribution_tier
```

**Do not** rewrite the existing schema blindly — extend it with compatible migrations or optional
fields. `content_hash` + `previous_observation_id` make transition detection cheap and idempotent.

---

## Raw evidence vs normalized facts  `[PROPOSED]` (partly `[CURRENT]`)

Keep distinct layers — raw evidence and normalized facts are **not** the same layer:

```text
Raw source evidence → extraction → normalized observation → detected transition → analytical inference
```

Where permitted (rights/retention/redistribution policies enforced), raw evidence may include: HTML,
structured JSON, response metadata, public page screenshots, ticket-state snapshots, image metadata,
public creative references, partner-provided aggregate payloads. Storage of raw evidence is gated by
the existing provenance + redistribution invariants.

---

## Evidence graph  `[PROPOSED]`

Extend the knowledge graph so claims, observations and estimates are linked **explicitly** (this
builds on the existing canonical-entity graph; see `docs/ontology.md`):

```text
Event
 ├── HAS_OBSERVATION → Ticket class unavailable
 ├── HAS_OBSERVATION → Sold-out post
 ├── HAS_OBSERVATION → Venue upgrade
 ├── HAS_OBSERVATION → High outbound-click velocity
 ├── HAS_OBSERVATION → Crowd image
 └── HAS_ESTIMATE    → Strong commercial outcome
```

An estimate links to its provenance and lineage:

```text
Estimate
 ├── SUPPORTED_BY      → Observation
 ├── CONTRADICTED_BY   → Observation
 ├── GENERATED_BY      → Model or rule version
 ├── VALID_FOR         → Event
 ├── REVISED_BY        → Later estimate
 └── CALIBRATED_AGAINST → Verified outcome cohort
```

**Never overwrite earlier estimates** when new evidence appears. Preserve: original estimate,
original evidence, model version, later revision, and the reason for revision. (Consistent with the
existing "nothing is deleted / version everything" rule.)

---

## Evidence classes  `[PROPOSED]` — the data-epistemics vocabulary made concrete

Every event outcome carries an **evidence class**:

- **Class A — Verified:** trusted partner-reported aggregate; organizer-confirmed attendance;
  verified QR-scan aggregate; known sellable capacity; audited/reliable public report.
- **Class B — Strongly observed:** all visible ticket classes became unavailable; credible sold-out
  announcement; no inventory reopening; additional show added; venue upgraded; strong post-event
  crowd evidence; no major contradictory evidence.
- **Class C — Estimated:** some ticket classes disappeared; capacity uncertain; campaign continued to
  event day; outcome claims unverified; post-event evidence incomplete.
- **Class D — Unknown:** insufficient evidence to estimate responsibly.

Analytics should expose fields similar to (exact design follows repo conventions):

```json
{
  "commercial_outcome": "strong",
  "estimated_occupancy_min": 0.75,
  "estimated_occupancy_max": 0.95,
  "evidence_class": "B",
  "confidence": 0.81,
  "verified": false,
  "supporting_observations": [],
  "contradicting_observations": [],
  "estimation_method": "public_state_triangulation"
}
```

---

## Reported vs observed vs estimated values  `[INVARIANT]`

The data model and API must keep these separate and never mix them within one metric without
qualification:

```text
reported_tickets_sold
verified_tickets_sold
observed_inventory_delta
estimated_sell_through_min
estimated_sell_through_max
estimated_sell_through_midpoint
estimation_confidence
estimation_method
```

**Do not generate false precision.** Prefer `Estimated occupancy: 75–90% (Confidence: Medium)` over
`Estimated occupancy: 83.7%` unless the evidence genuinely supports that precision.

---

## Ticket-state intelligence without exact inventory  `[PROPOSED]`

Infer commercial *movement* from public ticket-state changes **without claiming exact sales**. Useful
derived temporal metrics:

```text
announcement_to_on_sale_hours
on_sale_to_first_tier_unavailable_hours
days_to_limited_availability
days_to_sold_out_claim
price_phase_count
price_change_count
inventory_reopening_count
discount_introduction_day
additional_show_latency
venue_upgrade_latency
venue_downgrade_latency
```

These can support classification even where exact ticket quantities are unavailable.

---

## Sales-curve archetypes  `[PROPOSED]` — deterministic, explainable first

- **Immediate demand:** rapid first-tier exhaustion; strong early owned-intent; little discounting;
  low visible campaign pressure.
- **Steady organic:** consistent ticket-state progression; moderate campaign activity; no major late
  intervention.
- **Campaign-responsive:** weak/moderate initial movement; acceleration after a major campaign burst.
- **Late accelerating:** limited early movement; strong final-week ticket-state/intent movement; low
  discount dependency.
- **Discount-dependent:** movement follows discounts/promo codes/repeated offers.
- **Stalled:** no meaningful transitions; continuous availability; high campaign pressure; no
  expansion signals.
- **Supply-constrained:** rapid public unavailability; small initial venue; additional show or venue
  upgrade.

Initially deterministic/explainable classifications — **not** opaque ML.

---

## Four analytical layers — do not collapse into one demand score  `[PROPOSED]`

The existing single demand score (analytics-service, `[CURRENT]`) is preserved, but the model must
also maintain four distinct layers:

- **Attention:** search interest, views, follower growth, social mentions, content engagement.
- **Intent:** event saves, reminders, calendar adds, repeat views, shares, ticket-link clicks,
  artist/venue/community follows.
- **Commercial movement:** ticket-class transitions, price-phase progression, public unavailability,
  additional shows, venue upgrades, discounting, inventory reopening.
- **Realized outcome:** verified/reported sales, attendance estimate, verified attendance, recurrence,
  sponsor renewal, venue expansion, profitability label.

A high-attention artist may have low purchase intent; a lower-attention artist may have high local
commercial conversion. Preserve this distinction.

---

## Owned audience-intent layer via crawl-space  `[PARTLY CURRENT]`

crawl-space `[CURRENT — consumes the /v1/events feed]` is also treated as an **owned observation
surface** for audience intent `[PROPOSED — instrumentation]`. Collect privacy-safe events:

```text
event_impression        event_reminder      artist_follow
event_card_open         event_share         venue_follow
artist_profile_open     calendar_add        community_follow
venue_profile_open      ticket_link_click   repeat_event_view
search_result_impression event_save
```

Outbound ticket links may use an n-quark-controlled redirect:

```text
n-quark redirect → record consented aggregate interaction → immediately redirect to original ticket destination
```

Capture only privacy-safe fields: `event_id`, `timestamp`, `source_surface`, `campaign_placement`,
`broad_region`, `device_category`, `new_or_returning`, `referral_context`. **No invasive
fingerprinting, no unauthorized cross-site tracking, no raw PII** `[INVARIANT]`. This measures
discovery, consideration and expressed intent even when the sale occurs elsewhere.

---

## Campaign-pressure observation  `[PROPOSED]`

Commercial movement must be interpreted **relative to how heavily an event was promoted.** Observe
permitted public signals:

```text
organic_post_count   artist_reposts       promo_codes
post_dates           venue_reposts        giveaways
creative_versions    influencer_posts     press_mentions
collaboration_accounts paid_creatives_observed  radio_appearances
first_ad_seen        last_ad_seen         outdoor_campaign_evidence
```

Derive a deterministic **Campaign Pressure Index** from available components (organic publishing
velocity, paid creative breadth, artist/organizer/collaborator amplification, discount pressure,
giveaway frequency, press/media activity), then a **proxy**:

```text
Observed Commercial Movement ÷ Observed Campaign Pressure
```

Label this a **proxy** — not a verified return-on-ad-spend measure.

---

## Creative and media history  `[PROPOSED]` — existing media policy unchanged

The existing media policy (favour hotlinking; restricted redistribution; Level-2 re-hosting deferred
pending a rights review) is **not reversed here** `[INVARIANT]`. Within it:

- **Owned/licensed media:** archive original file, creative versions, OCR, detected artists/sponsors,
  perceptual hashes, first/last seen, permitted derived assets.
- **Third-party public media without archival permission:** store only permitted metadata — source
  URL, first/last seen, content hash, perceptual hash, dimensions, OCR where lawful, detected
  entities where lawful, redistribution prohibition.

Detect creative transitions: `CREATIVE_ADDED`, `CREATIVE_REPLACED`, `SPONSOR_ADDED`,
`SPONSOR_REMOVED`, `ARTIST_ADDED_TO_CREATIVE`, `DATE_CHANGED_ON_CREATIVE`, `VENUE_CHANGED_ON_CREATIVE`.

---

## Post-event outcome evidence  `[PROPOSED]`

Where exact attendance is unavailable, collect public evidence: credible sold-out claim,
additional-show announcement, venue-upgrade history, wide crowd photographs, public audience videos,
artist crowd footage, press/venue galleries, organizer/sponsor outcome claims, repeat booking, future
edition, sponsor renewal.

Computer-vision crowd estimates **must remain ranges** and must **never** be presented as verified
attendance `[INVARIANT]`:

```text
Visible occupancy evidence: High
Estimated attendance band: 900–1,300
Estimated sellable capacity: 1,200–1,500
Confidence: Low–medium
```

Store limitations: selective framing, peak-time capture, duplicate imagery, incomplete venue
visibility, unknown image timestamp, seating-configuration uncertainty.

---

## Recurrence and expansion signals  `[PROPOSED]`

Support event-series / edition relationships: `event_series_id`, `edition_number`,
`previous_edition_id`, `next_edition_id`. Track delayed commercial-outcome signals: `capacity_change`,
`price_change`, `venue_change`, `city_expansion`, `show_count_change`,
`announcement_lead_time_change`, `sponsor_retention`, `artist_retention`, `organizer_retention`.

---

## Revealed organizer confidence  `[PROPOSED]`

Organizers reveal private beliefs through public decisions: initial venue size, lead time, pricing
aggressiveness, number of price phases, additional-show decisions, venue upgrade/downgrade, discount
timing, campaign intensity, production scale, repeat booking. Potential deterministic, evidence-linked
outputs: `promoter_confidence_index`, `venue_risk_level`, `pricing_aggressiveness`, `campaign_pressure`,
`expansion_signal`, `contraction_signal`.

---

## Venue capacity uncertainty  `[PROPOSED]`

Venue capacity must **not** be a single universally-valid number. Support:

```text
physical_max_capacity          sellable_capacity_estimate_min
licensed_capacity              sellable_capacity_estimate_max
typical_capacity               capacity_source
event_configuration_estimate   capacity_confidence
```

Configurations vary (seated, standing, reduced/large stage, partial venue, production kills, FOH and
barricade displacement). Occupancy estimates must account for capacity uncertainty.

---

## Surrounding market context  `[PROPOSED]`

At important lifecycle moments, preserve market context: `competing_events_in_city`,
`same_genre_events`, `major_festivals`, `public_holidays`, `exam_periods`, `elections`,
`weather_risk`, `transport_disruptions`, `artist_release_activity`, `nearby_artist_shows`,
`major_sports_fixtures`, `religious_or_cultural_occasion`. Distinguish context that can be reliably
reconstructed later from context that depends on disappearing listings/states. Target question:
*"At the time this event was announced, what competing visible inventory already existed in the
relevant city and date window?"*

---

## Source coverage and confidence  `[PROPOSED]` — extends region verdicts

An apparently undersupplied region may be **under-observed**. This directly qualifies the current
analytics-service region verdict (`undersupplied / demand-led / supply-only / balanced`, `[CURRENT]`).
Add a **source coverage ledger**:

```text
source_id  region_id  category  expected_entities  entities_observed
last_success_at  capture_frequency  failure_rate  median_capture_delay
field_completeness  duplicate_rate  resolution_rate  historical_gap_hours  coverage_score
```

Region-level verdicts must include `coverage_score`, `source_count`, `last_successful_capture_at`,
`known_capture_gaps`, `field_completeness`, `confidence`. Withhold or qualify verdicts when coverage
is inadequate, e.g. `Apparently undersupplied · Coverage score: 0.34 · Verdict confidence: Low`.
**Do not present missing supply data as proof of market opportunity** `[INVARIANT]`.

---

## Adaptive collection frequency  `[PROPOSED]`

Do not crawl every event equally. Priority tiers: **high-priority tracked** (large capacity,
strategic artist, high audience intent, rapidly changing state, priority city, important organizer,
major series), **standard** (daily / several times per week), **long-tail discovery** (infrequent
unless signals change). Suggested model, source-specific and compliant with source restrictions:

```text
Newly discovered, not on sale:       daily
First 24 hours after on-sale:        every 30–60 minutes
More than 30 days away:              daily
14–30 days away:                     every 6–12 hours
Final 14 days:                       every 2–6 hours
Event day:                           hourly where justified
Post-event:                          checks at +1, +3 and +7 days
```

---

## Calibration strategy  `[PROPOSED]`

Universal ground truth is not required — a **limited but diverse** calibration set is. Sources:
Clockwork-managed events, live.work IPs, Boshow observations, friendly independent organizers, public
attendance reports, venue-owned events, college/cultural events, events with reliably known capacity
and public unavailability. First objective ≈ **100–300 diverse calibrated event lifecycles**, not
millions of shallow records. Cover cultural events, concerts, comedy, theatre, workshops, college
events, festivals, arena shows, sponsored/free events, multiple cities and capacities. Compare
`public-state history + campaign pressure + demand signals + owned intent → known outcome band`. **Do
not overfit to Clockwork's own operating segment.**

---

## Ask for labels, not ledgers  `[FUTURE]`

Where contributors participate, request **coarse outcome labels**, not sensitive raw data.
Attendance labels: `Under 25% / 25–50% / 50–75% / 75–90% / 90%+ / Sold out / Unknown`. Commercial
labels: `Below break-even / Around break-even / Profitable / Highly profitable / Unknown`. A minimal
contribution: `attendance_band`, `capacity_band`, `average_price_band`, `marketing_spend_band`,
`commercial_outcome_band`. **No customer-level or order-level records required.**

---

## Privacy-safe benchmark network  `[FUTURE]`

n-quark may eventually run an industry benchmarking exchange where participants keep proprietary
records and contribute limited aggregates. Rules: no raw customer data; no raw order export; no named
competitor disclosure; minimum cohort thresholds; explicit permitted-use/retention/deletion policies;
no ticket-sale interception; contributor controls. Apply cohort thresholds such as **k ≥ 10 events**
(consistent with the existing no-PII / k-anonymity invariant). Expose *"Comparable Kolkata comedy
events reached a median occupancy band of 65–80%"*; never *"Organizer X sold 742 tickets."*

## Federated / local aggregate computation  `[FUTURE]` — not an MVP dependency

Optionally, support computation where contributor data lives:
`Contributor database → n-quark-approved local aggregation function → anonymous aggregate output →
benchmark network`. Possible output:

```json
{ "occupancy_band": "75_90", "sales_curve_cluster": "late_accelerating",
  "refund_band": "under_3_percent", "scan_rate_band": "80_90", "price_realization_band": "90_100" }
```

A later path to useful benchmarks without exporting raw transactions.

## Contributor source diversity  `[FUTURE]`

Outcome evidence is not only from ticketing platforms. Partial contributors: organizers, venues,
production vendors, security teams, access-control vendors, artist managers, sponsors, media agencies,
hospitality vendors, parking operators, college committees. Each provides only partial evidence (e.g.
venue → configuration + sellable-capacity band + occupancy class; production vendor → seating/stage
config + whether rear sections opened; access-control → aggregate gate-throughput band; sponsor →
activation + audience-estimate band + renewal). **Partial evidence is combined but never mistaken for
complete ground truth.**

---

## Contributor & source reliability  `[PROPOSED]`

Track `source_reliability`, `historical_accuracy`, `claim_consistency`, `evidence_attachment_rate`,
`correction_rate`, `sample_size`. Evidence hierarchy (high → low): verified machine aggregate →
verified aggregate document → named professional contributor → multiple independent public sources →
single organizer claim → unverified social claim → model inference. Weight claims by evidence quality.

---

## Contradiction handling  `[PROPOSED]`

Contradictory evidence must **not** be silently resolved. Store `claim`, `source`, `timestamp`,
`evidence`, `conflict_set`, `resolution_status`. Example: organizer claims sold out, yet ticket
classes remain publicly available, crowd evidence suggests partial occupancy, and venue-capacity
estimate conflicts with claimed attendance → output `Outcome disputed · Confidence: Low` with
supporting **and** contradicting evidence listed. Contradictions are analytically useful and build
source-reliability histories.

---

## Source-independent event identity  `[PROPOSED]` — extends canonical entity resolution

The canonical event identity (see *Entity* + `docs/ontology.md → Entity Classification`) must survive
title changes, venue changes, postponements, reschedules, duplicate listings, source-platform
migration, additional-show creation, and poster replacement. Resolve using combinations of: artist/
lineup, venue, city, date/time, organizer, event-series identity, poster perceptual hash, title
similarity, source identifiers. Represent reschedules, duplicates, editions and related performances
**explicitly** rather than minting unrelated event nodes.

---

## Minimum viable Shadow Ledger + initial scope  `[PROPOSED]` — first backlog

First implementation focuses on these fields for priority events:

```text
canonical_event_id       displayed_prices           campaign_observations
first_seen_at            ticket_class_availability   crawl_space_impressions
announcement_date        public_commercial_messages  crawl_space_saves
on_sale_date             creative_versions           crawl_space_ticket_clicks
venue                    sold_out_claim              cancellation_or_postponement
capacity_range           post_event_evidence         event_series_and_recurrence
ticket_classes
```

Suggested initial scope: **3–5 cities · 4–6 event categories · 500–1,000 tracked events · 100–300
high-frequency priority events.**

---

## First analytical products  `[PROPOSED]` — evidence-supported before predictive ML

Do **not** begin with exact attendance prediction. Ship, in order:

- **Market Momentum:** demand movement, intent movement, upcoming supply.
- **Commercial State:** early sale / active / limited availability / publicly unavailable / stalled /
  discounting / reopened.
- **Sales-Curve Class:** immediate / steady / late accelerating / campaign-responsive /
  discount-dependent / stalled.
- **Campaign Efficiency Proxy:** observed commercial movement ÷ observed campaign pressure (a proxy).
- **Event Confidence:** venue expansion, additional show, price progression, low discount dependency.
- **Outcome Band:** weak / moderate / strong / very strong / unknown.
- **Data Confidence:** verified / strongly observed / estimated / insufficient evidence.

Deterministic and explainable before predictive ML is introduced (upholds *deterministic-before-AI*).

---

## Non-goals and prohibitions  `[INVARIANT]`

n-quark must **not**: circumvent authentication; exploit private APIs; defeat technical access
controls; evade blocking or rate limits; capture ticket-buyer PII; infer personal identities through
fingerprinting; represent unavailable inventory as confirmed sales; present computer-vision crowd
estimates as verified attendance; mix estimated and verified values; manufacture precise sales counts;
train opaque prediction models before adequate calibration; or weaken the existing provenance and
redistribution invariants. **A fragile or evasive acquisition strategy is not a defensible moat.**
(This reaffirms and extends the existing compliance posture — e.g. BookMyShow remains `partner_feed`
only, never evasively scraped.)

---

## Revised product position

n-quark is **the independent market-observation and inference layer for Indian live entertainment.**
It continuously records how events, prices, availability, campaigns, audiences and organizer decisions
change over time, then uses explainable evidence and limited calibration data to classify commercial
movement and estimate outcomes.

```text
Ticketing platforms know:  what sold through their platform.
Promoters know:            what happened within their own events.
Venues know:               what happened inside their rooms.

n-quark should know:       how the visible market evolved across platforms,
                           what signals preceded commercial outcomes,
                           how organizer and audience behaviour differed by city,
                           and how one event or market compares with another.
```

The moat is the **persistent cross-platform history**, not universal access to private ticket ledgers.

---

## Relationship to the existing thesis

This augmentation **strengthens** the existing pipeline rather than replacing it:

```text
signal → immutable observation → canonical entity → graph → analytics → compliant feed
```

is extended to:

```text
continuous signal capture
→ temporally valid observation
→ detected state transition
→ canonical entity and evidence graph
→ deterministic commercial classification
→ confidence-qualified estimate
→ compliant intelligence and feed
```

Unchanged: deterministic-before-AI; the compliance/provenance/no-PII/redistribution-tier/
canonical-identity invariants; and the demand/supply convergence thesis. This update adds only the
**acquisition and inference strategy** for when granular first-party ticketing integrations are
unavailable.

---

## Backlog introduced by this augmentation  `[BACKLOG]`

1. Additive temporal-observation migration (nullable fields above) on observation-service.
2. Transition detection (`content_hash` + `previous_observation_id`) → Shadow Market Ledger writer.
3. Event / ticket-class / campaign state machines + transition storage.
4. Evidence graph relationships (`HAS_OBSERVATION`, `HAS_ESTIMATE`, estimate lineage edges).
5. Evidence-class tagging (A/B/C/D) + reported/observed/estimated separation in analytics outputs.
6. Deterministic sales-curve archetype + commercial-state classifiers.
7. Four-layer analytics split (attention / intent / commercial movement / outcome).
8. crawl-space privacy-safe intent instrumentation + optional n-quark redirect (consented aggregates).
9. Campaign-pressure observation + Campaign Pressure Index (proxy).
10. Source coverage ledger + coverage-qualified region verdicts.
11. Adaptive collection-frequency scheduler (feeds crawl-service).
12. Calibration set assembly (100–300 lifecycles) + label-collection intake.
13. (Future) privacy-safe benchmark network, federated aggregate computation, contributor intake.
14. Source-independent event identity resolution (reschedules/editions/duplicates).
15. Venue capacity-uncertainty model; recurrence/expansion tracking; contradiction store.

## Open questions / contradictions to resolve  `[NOTES]`

- **Screenshot/raw-evidence retention vs media policy:** the moat wants ticket-state snapshots and
  page screenshots, but the current media policy favours hotlink-only and defers re-hosting. Storing
  raw HTML/screenshots of third-party pages needs an explicit rights/retention decision before build.
- **Redistribution tier for lifecycle/estimate data:** the existing feed tiers (open/link_only/
  excluded) govern event redistribution; a tiering decision is needed for *derived* estimates and
  Shadow-Ledger transitions (likely not part of the open feed).
- **`fill_ratio` naming:** the current build already ingests Boshow `fill_ratio` (an *observed public
  state*, not verified sell-through). Ensure it is surfaced under the reported/observed vocabulary
  above and never labelled *verified*.
- **crawl-service scope:** adaptive-frequency scheduling assumes an autonomous crawl-service, which is
  still a scaffold. This is future work, not current capability.

---

# Delivered — Public Demand Intelligence & Indian Artist Universe (Phase 5A → 5A.3.2)  `[CURRENT]`

_Appended 2026-08-09. Additive record of what the moat's **demand side** now actually ships. Existing
`[PROPOSED]`/`[FUTURE]` entries above are unchanged; items here move from proposed to current. This section
is append-only like the rest of this MCP._

## What shipped

- **artist-intelligence-service** (port 8010) — the demand-side observation layer, a **separate stateful
  service** so demand-layer failure never disrupts the crawl→signal collection spine. Demand and supply
  (the event Shadow Ledger) are two independent evidence systems that meet **only** through
  `canonical_artist_id`; demand metrics never enter the event ledger.
- **Own demand ledger** — `artist_external_identity`, `artist_demand_observation` (append-only, idempotent
  on `observation_key`, provenance + `evidence_status`), `provider_quota_day` / `provider_quota_bucket_day`,
  `demand_refresh_job`, plus the 5A.3 artist-universe tables (`artist_candidate`, `artist_market_evidence`,
  `youtube_video`). Migrations additive + reversible.
- **Single ingestion path** — all YouTube acquisition is delegated to **signal-service** (search / channel
  verify / recent videos / `videos.list` batch stats); the API key stays there. No parallel YouTube client.
- **YouTube identity integrity `[INVARIANT]`** — search is candidate discovery only; a CHANNEL_ID becomes
  `RESOLVED` **only** after an authoritative `channels.list` verification; `last_verified_at` is set only on
  real provider verification; only an authoritative NOT_FOUND invalidates an identity. Thresholds are never
  lowered to raise coverage — **a false identity match is worse than missing data**.
- **Google Trends** — official-API-or-import only, **no scraping**. Values are **relative search interest**
  (0–100 within a pull), never absolute volume; independently normalised exports are never merged;
  SEARCH_TERM vs TOPIC kept distinct. Production currently reports `ACCESS_UNAVAILABLE` (IMPORT available) —
  a legitimate state, not an error.
- **Deterministic read models** — momentum (independent components, never a composite score),
  geography (demand × observed supply, transparent labels), supply/demand juxtaposition, and event-response
  **temporal co-movement only (no causal inference)**. `INSUFFICIENT_HISTORY` is a first-class honest state.
- **Restart-safe scheduler** — persisted, lease-locked, idempotent; hourly YouTube live-metric buckets
  (Trends stays daily; historical daily records preserved, hourly precision never retrofitted); adaptive +
  event-aware cadence; per-provider quota **buckets** (SEARCH / GENERAL_READ / VIDEO_STATS_BATCH) with a
  configurable target-utilisation + **reserve** (defer, never invalidate, at the reserve) and a
  provider-timezone reset boundary.
- **Indian artist universe (decoupled from ticketing) `[INVARIANT]`** — artists enter via multiple
  discovery surfaces (event-derived, YouTube search/ecosystem, future authorized seams), not only ticketing.
  Discovery produces **candidates**, never canonical artists. Promotion to canonical requires deterministic,
  auditable evidence (match an existing canonical / multi-source confirmation / India-live + music identity)
  and is **routed through the entity/graph owner** — the demand layer never creates or owns canonical
  identity. India market presence is **evidence-classified** (`CONFIRMED_LIVE_INDIA` / `INDIA_DEMAND_OBSERVED`
  / `INDIA_MARKET_CANDIDATE`), never a single opaque relevance score. **BookMyShow is never a gatekeeper and
  is never evasively scraped.**
- **Canonical ownership + reconciliation `[INVARIANT]`** — canonical ARTIST **identity** is owned by
  crawl's entity-resolution registry; the graph is its **representation**. Externally/promotion-created
  artists are registered idempotently so the authoritative enumeration stays single and complete; demand
  `canonical_artist_id` references are **auditable** against it (orphans reported, never silently rewritten).
- **Inspection surface** — the demand read models are exposed through the existing **local-only,
  read-only** admin console (Demand Intelligence screen + artist/event demand context + artist-universe and
  quota-bucket diagnostics). No mutation controls; the admin BFF/frontend are never exposed on cloud.
- **Deployment** — runs on the existing private Fly topology (region `sin`, private Flycast, no public IP);
  no new public surface introduced.

## Reaffirmed invariants (demand side)

- No composite popularity / market-value / booking score; no causal claim from co-movement.
- Reported ≠ Verified ≠ Estimated ≠ Observed — YouTube subscriber counts are **provider-reported/rounded**,
  never shown as exact; Trends is relative interest, never volume.
- Canonical identity is owned by the entity/graph architecture; the demand layer only attaches to it.
- API keys are operator-set secrets, never handled by tooling, never in git/logs/images.

# Delivered — Production collection unblock: the append-only observation store is now deployed (Phase 5A.3.3)  `[CURRENT]`

_Appended 2026-08-11. Additive record. Existing entries above are unchanged; this section is append-only
like the rest of this MCP. It documents a production topology correction, not a model change: the moat's
irreplaceable **temporal observation layer** was not actually accruing in production, and now is._

## What was wrong, and why it matters to the thesis

The strategic moat is an **independent, cross-platform temporal observation layer** — data whose value
comes from being captured continuously and never being reconstructable after the fact. In production that
layer had silently stopped accruing: the always-on collector was discovering and fetching real events
(Boshow + District, HTTP 200), but **not one observation was being persisted**, so no canonical artists and
an empty graph. A moat that does not accrue is not a moat. The cause was infrastructural, not conceptual:

- The **capture write path** (signal-service ticketing `/ingest`) persists normalized observations to
  **observation-service** as a **hard dependency**. That service **was never deployed** in the private Fly
  collection topology (Phase 4D stood up graph/signal/media/crawl only). Every capture write therefore
  failed at DNS resolution → **HTTP 502** → the collector recorded the capture as `SOURCE_UNAVAILABLE`
  (a transport failure), never as an authoritative record. **0** observations, **0** canonical artists,
  **0/0** graph — while the collector itself looked healthy.

## What shipped (the correction)

- **The observation store is now part of the always-on private spine `[INVARIANT]`** — observation-service
  is deployed on the org's private network (no public IP, region `sin`), **always-on** (never scale-to-zero)
  precisely because it is a hard dependency of continuous capture. The collection topology is now
  graph → **observation** → signal → media → crawl. It shares the one Managed Postgres datastore with its
  own migration-version namespace (the established per-service pattern), so no second datastore was introduced.
- **A missing hard dependency must fail loudly, not degrade silently `[INVARIANT]`** — signal-service now
  exposes an explicit **readiness** signal that verifies the observation-store dependency and reports an
  unambiguous failure (with the reason) when it is unreachable, kept separate from the liveness signal so a
  transient blip does not flap routing. The class of failure that caused this outage — a silent transport
  error masquerading as data absence — is now observable directly. This reinforces the standing invariant
  that **a failed request is never recorded as record absence**: absence remains an authoritative signal only.
- **Discovery/observation load stays bounded `[INVARIANT]`** — the demand layer's periodic reconciliation of
  the canonical-artist enumeration is coalesced so one refresh cycle reads the authoritative listing once,
  rather than re-enumerating it per candidate. Internal read amplification is treated as a defect.

## Reaffirmed invariants (unchanged, now actually enforced end-to-end in production)

- **Continuous capture is the product.** The temporal observation layer must accrue in production, always-on;
  its hard dependencies are deployed and monitored as such.
- **Transport failure ≠ record absence.** A 404 is authoritative absence; a write/fetch failure is
  `SOURCE_UNAVAILABLE` and never enters the record as evidence.
- **One datastore, per-service migration namespaces; no new public surface; private Flycast only, no public
  IP.** Canonical identity remains owned by the entity/graph architecture; observation-service only persists
  the immutable observations the capture path produces.

# Delivered — Authenticated Production Intelligence Console (Admin D)  `[CURRENT]`

_Appended 2026-08-11. Additive record. Existing entries above are unchanged; this section is append-only
like the rest of this MCP. It records a deliberate, bounded change of posture: the console that observes
n-quark is now exposed to authenticated humans, not only to a developer's machine._

## What shipped

The accumulated observation layer is only valuable if humans can actually see it. Admin D makes the console
the **primary human interface for observing n-quark in production** — the operator should not need SQL,
`curl`, or Fly logs for ordinary product/data inspection.

- **One public application `[INVARIANT]`** — a single Fly app (`nquark-admin`) serves the console SPA and its
  read-only backend-for-frontend from one image and reaches the internal services over the private network.
  It is the **only** public surface introduced for the console; every collection service stays private.
- **Authenticated, organization-scoped access `[INVARIANT]`** — access requires a Google Workspace sign-in
  whose **verified email** is within the allowed organization domain (deny-by-default; an explicit extra
  allowlist is possible). Authorization is decided server-side on every request from a signed, httpOnly
  session; the browser never holds a bearer token. The unauthenticated single-context mode remains a
  developer-machine-only affordance and is never enabled on a public deployment.
- **Operationally read-only `[INVARIANT]`** — the deployed console exposes inspection only. It renders no
  mutation controls and the governed/operational write endpoints are disabled, so even a direct request is
  refused. Observing the system never changes it.
- **Faithful to live production `[INVARIANT]`** — the console reflects the live production state of the
  services and datastore. It does not read a local database, and local development data is never migrated
  into production to make a screen look populated. Panels degrade honestly to "unavailable" rather than
  fabricating data when a dependency is absent.
- **Intelligible exploration + bounded analysis** — events, artists, venues, organizers, observations,
  demand intelligence, and source/service health are explorable without query knowledge; an analysis view
  presents **deterministic aggregations only**. Consistent with the standing invariants, it shows collection
  integrity as **separate named components rather than a single opaque score**, places **supply and demand
  side by side and never fuses them into one index**, and makes **no prediction and no causal claim**.

## Reaffirmed invariants (console)

- The console is a lens, not an actor: read-only in production; identity is owned by the entity/graph
  architecture; demand and supply remain distinct evidence systems meeting only at `canonical_artist_id`.
- Authentication and authorization are enforced on the server for every request; the UI never gates access
  by hiding controls alone.
- No score that collapses popularity, value, or booking-worthiness into one number; no causal inference from
  co-movement; honest "insufficient history" / "not complete market coverage" labels are preserved.
- Credentials (OAuth client secret, session-signing secret) are operator-set secrets — never in git, never
  handled by tooling, never inlined in a deploy manifest.

# Delivered — Cryptographically-verified console authentication (Admin D.1)  `[CURRENT]`

_Appended 2026-08-12. Additive record; append-only like the rest of this MCP. It hardens the Admin D
posture from "authenticated" to "cryptographically authenticated" before the public console went live._

- **A console identity must be cryptographically proven, not merely decoded `[INVARIANT]`** — a Google
  sign-in is accepted only after the identity token's signature is verified against Google's published
  signing keys, and its audience, issuer, expiry, verified-email, Workspace-domain, and per-login nonce are
  all checked. An unsigned token, a token signed by an unknown key, or any claim mismatch is rejected. The
  console is now **live on the public internet** over HTTPS, reachable only through this gate; the internal
  collection services remain private.
- **The public console remains observe-only and faithful to production `[INVARIANT]`** — writes are refused
  server-side regardless of the interface; the deployed console reads only the live private services (no
  local data path); and it visibly identifies itself as the production, read-only environment so it can
  never be mistaken for a development surface. Session material is an httpOnly, secure, same-site cookie;
  no secret or token is ever exposed to the browser.
