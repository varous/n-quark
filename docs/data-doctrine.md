# Data Acquisition, Transformation & Knowledge-Space Doctrine

> n-quark converts transient multi-source public evidence into persistent factual observations,
> canonical knowledge, temporal history and derived analytical representations. Source content is an
> **input**; the accumulated knowledge graph, Shadow Ledger, embeddings and inference capability are the
> **product**.

Status: architectural direction. Applies to District, Boshow, BookMyShow, social sources, and any future
public-web source. It guides **new** work first — it is not a mandate to rewrite existing storage (see
"Implementation sequence"). It does **not** by itself resolve any source's Terms of Service or
acquisition-rights questions (see "Two governance axes").

## Core principle

```
SOURCE DATA  ≠  N-QUARK PRODUCT
```

n-quark is not a warehouse or resale layer for source-platform datasets. The commercial IP is
canonicalization + reconciliation + the temporal Shadow Ledger + the derived feature space + knowledge
embeddings + analytics/inference — never the underlying source dataset.

## Data flow

```
PUBLIC SOURCE
  → EPHEMERAL ACQUISITION
  → FACT / CLAIM EXTRACTION
  → RAW EXPRESSIVE CONTENT PURGE
  → SOURCE OBSERVATION LEDGER
  → CANONICAL ENTITY RESOLUTION
  → CROSS-SOURCE RECONCILIATION
  → TEMPORAL STATE / FEATURES
  → CANONICAL KNOWLEDGE REPRESENTATION
  → EMBEDDINGS / N-DIMENSIONAL KNOWLEDGE SPACE
  → ANALYTICS / INFERENCE
  → COMMERCIAL PRODUCT
```

The default must **not** be `scraped page → permanent vector`. Prefer
`scraped page → factual structured evidence → canonical knowledge → derived embedding`.

## Ephemeral raw acquisition

Raw fetched content (HTML, layout, promotional copy, biographies, descriptions, navigation, reviews,
source imagery, other expressive content) is transient by default: `raw_content_retention = EPHEMERAL`.
Use it only long enough to (1) parse factual claims, (2) compute hashes/provenance, (3) optionally derive
temporary resolution features, (4) validate extraction — then purge it unless a specific operational,
evidentiary or legal requirement justifies retention. Do not retain whole source pages merely because
storage is cheap.

## What is persisted

**Structured factual observations** — source, source_record_id, source_url, observed_at; event
name/date/start/end/city; artist/venue/organizer mentions; ticket price/currency; availability + ticket
+ provider lifecycle; source identifiers; claim confidence, claim origin, parser version.

**Minimal provenance** — source, source_record_id, durable URL reference, fetch timestamp, content hash
where useful, extractor/parser version, acquisition method, evidence type.

The objective is *purge source **expression***, not *purge provenance*.

## Provenance policy

Provenance stays internally essential even when customers never see source-level data as the product:
auditability, source disagreement, retraction/correction, parser debugging, confidence, legal/compliance
review, whether two claims were independently observed, future source removal, and proving intelligence
is genuinely multi-source. Its *commercial* relevance may be low; its *evidentiary* relevance is high.
Never make provenance intentionally unrecoverable.

## Structured observation layer

The permanent source-facing layer represents **claims, not pages** — conceptually a `SourceObservation`
(source, source_record_id, source_url, observed_at, subject_hint, claim_type, claim_value,
`evidence_origin ∈ {STRUCTURED_DATA, PAGE_TEXT, IMAGE_TEXT, API, SOCIAL_CAPTION, SOURCE_METADATA, OTHER}`,
confidence, parser_version, content_hash, optional linked_canonical_entity_id / linked_canonical_event_id).
Reuse the existing observation/Shadow-Ledger evidence models where they express this cleanly — do not
build duplicate infrastructure.

Prefer extracting factual market claims (event exists, date/time, venue, artist, organizer, city, public
price, public availability, cancellation/postponement/reschedule, ticket-status transitions, source
identity). Avoid permanently storing expressive content (long descriptions, articles, promotional prose,
reviews, biographies, posters, photographs, page copy). Poster/social-image understanding may be performed
transiently, with the resulting claims stored carrying image-derived provenance.

## Canonical knowledge precedes permanent embeddings

Permanent embeddings are generated primarily from n-quark's canonical/derived representation:
`multiple source observations → canonical event/entity → derived features → embedding`. The resulting
vector is an n-quark knowledge representation, not a semantic copy of one source page.

### Two vector-spaces model

- **A — Source-resolution embeddings**: entity matching, duplicate detection, event reconciliation, fuzzy
  title/entity matching. May be temporary/internal resolution artifacts; must **not** become the core
  commercial dataset.
- **B — Canonical knowledge embeddings**: persistent, generated from canonical entities + n-quark-derived
  features. Purpose: similarity, clustering, anomaly detection, semantic search, neighbourhood analysis,
  market segmentation, opportunity detection, downstream ML. These form the n-dimensional knowledge space.

## Vector DB is not the system of record

System-of-record hierarchy: `raw/source evidence → structured observations → canonical registry →
temporal Shadow Ledger → derived features → embeddings`. Vectors are **analytical projections**, must be
rebuildable, and must never be the sole home of business truth. "What was the observed ticket price on 12
Aug?" is answered from the Shadow Ledger, never from vector similarity.

Treat embeddings as **derived data**, not guaranteed-irreversible anonymization: apply access controls,
prefer embeddings of canonical/derived objects over source pages, and never use "we only retain embeddings"
as the sole compliance argument.

## N-dimensional knowledge space

A derived semantic/feature space over Events, Artists, Venues, Organizers, Cities, Genres, Communities,
ticket states, pricing behaviour, demand observations, and temporal behaviour — encoding identity
features, graph relationships, historical activity, cadence, price behaviour, source diversity, demand
movement, geographic presence, event/venue/organizer context. It answers questions like "which artists
behave similarly in the live market?", "which venues host similar ecosystems?", "which new events
resemble historically strong clusters?", "which entities are anomalous relative to peers?" — **never**
reduced to a single opaque universal score.

## Commercialization boundary

```
INTERNAL EVIDENCE ZONE (District, Boshow, BMS, Instagram, Facebook, Reddit, future sources
  → source observations → canonical entities → reconciliation → Shadow Ledger → features → embeddings → inference)
================ COMMERCIALIZATION BOUNDARY ================
CUSTOMER-FACING: canonical entities, derived relationships, market movement, comparisons, historical
activity, analytics, alerts, search, inference, aggregates, intelligence APIs.
```

Do **not** ship customer-facing raw-source export products (`/export/bookmyshow`, `/district/catalogue.csv`,
etc.). Source-specific raw datasets are not the commercial product. Commercially relevant intelligence is
constructed from multiple observations + canonicalization + temporal history + derived analysis — not
reformatted source records. Source identity and observed facts stay distinguishable from n-quark inference
(a provider `FAST_FILLING` claim must not silently become "80% sold").

## Two governance axes (kept independent)

Transformation strengthens minimization, non-republication, non-substitution, analytical independence and
copyright-expression separation — but it does **not** cure prohibited acquisition, ToS/contract
restrictions, circumvention, access-control bypass, or unlawful collection.

- **Axis A — acquisition posture** (per source): public/private, auth required, robots posture, terms
  posture, commercial-access posture, circumvention required, source permission, rate limits, technical
  blocks, legal-review status.
- **Axis B — transformation/commercialization posture** (per source): raw-content retention, fact
  extraction, source redistribution, multi-source reconciliation, derived analytics, commercial export,
  embedding basis, commercial-output boundary.

Never conflate the two.

### Source-governance descriptor extensions (future guidance)

Where the existing descriptor cannot already express it, extend with concepts such as
`raw_content_retention ∈ {EPHEMERAL, BOUNDED, REQUIRED}`, `fact_extraction_allowed`,
`expressive_content_retention`, `source_dataset_redistribution=false`, `commercial_raw_export=false`,
`canonicalization_required=true`, `derived_embedding_basis ∈ {SOURCE, CANONICAL, HYBRID}` (preferred
CANONICAL), `commercial_output_mode=DERIVED_ANALYTICS`, `provenance_retention=REQUIRED`,
`content_hash_retention`. Do **not** add fields blindly if the existing source-governance model already
expresses the concept.

## Design smell test

For every new source: *"if this source disappeared tomorrow, would n-quark retain useful independent
intelligence?"* The answer must be **yes** — because n-quark retains canonical entities, historical
factual observations, cross-source relationships, derived state transitions, analytical features,
embeddings and inferences. "No, we're just a frontend over that source" is an architecture failure.

## Derived-IP priority layers (the moat)

entity-type classification · canonical identity resolution · event reconciliation · temporal Shadow
Ledger · source-disagreement modeling · source authority · event lifecycle · social event interpretation
· price/availability movement · graph-derived features · canonical embeddings · demand × supply
relationships · anomaly detection · market clustering · inference. The moat is the accumulation of these
derived layers over time.

## Non-negotiable invariants

1. Raw source content is not the commercial product.
2. Vector embeddings are not canonical truth.
3. The vector DB is not the system of record.
4. Provenance survives even when raw source content is purged.
5. Canonical entities remain registry-owned.
6. Source observations remain source-scoped claims.
7. Derived inference stays distinguishable from observed facts.
8. No bulk raw-source exports are exposed commercially.
9. Expressive content is not stored unnecessarily.
10. Embeddings are not treated as legally/technically irreversible.
11. Acquisition legality/governance stays separate from downstream transformation.
12. New sources should increase n-quark's independent derived knowledge, not merely enlarge a copied catalogue.

## Implementation sequence

Do **not** pause integrity work or perform a large storage rewrite for this doctrine, and avoid premature
infrastructure. Order: (1) finish Phase 5B.3 (done at `e3c7083`); (2) document this doctrine (this file);
(3) apply it natively to **Phase 5C — Social Signal Intelligence**; (4) audit existing District/Boshow
raw-content retention; (5) introduce the canonical/derived embedding architecture; (6) migrate legacy
source retention only where safe and worthwhile — first auditing what features/reconciliation/debugging
depend on, and preserving factual observations, provenance, hashes/identifiers, reconciliation
auditability and rollback/debug capability; (7) apply the same model to BMS if/when enabled.

**Phase 5C must be built to this doctrine from the start**: `social source → ephemeral acquisition →
factual/semantic claims → source evidence (SocialMention) → media-retention policy → Event candidate →
canonical reconciliation → Shadow Ledger → derived features → canonical knowledge embedding` — never a
permanent raw-post warehouse. A dedicated derived-intelligence phase (canonical feature engineering +
knowledge embeddings + n-dimensional market space) follows once enough longitudinal data exists; early
use cases are semantic search, reconciliation assistance, and artist/venue/organizer/event similarity —
demand×supply and temporal-trajectory embeddings and opportunity inference come later.
