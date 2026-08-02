# n-quark
## India-First Signal, Event & Community Adapter Roadmap

Version: 0.1
Status: Planning spec — supersedes the "Signal Providers" section of [product-spec.md](product-spec.md) for build sequencing.

> **Cross-reference (additive):** adapter *frequency* and *longitudinal* capture are now governed by
> [product-spec.md → Independent Market Observation and Temporal Data Moat](product-spec.md#independent-market-observation-and-temporal-data-moat)
> — see **Adaptive collection frequency**, **Shadow Market Ledger**, and **Source coverage and
> confidence**. This roadmap's *what to build* is unchanged; the moat section adds *how often to
> re-capture* and *what state transitions to persist*.

---

## 0. Purpose & strategic frame

This document specifies **what data adapters to build, in what order, and under what compliance
constraints**, to take n-quark from its current state (one Spotify adapter, artist-only entities)
to a working India-first live-entertainment intelligence layer.

### Why India-first

- India's live-music market is ~US$1.39B (2025), growing ~17.6% CAGR; ~34,000 shows in 2025;
  live footfalls up 90–210% in tier-2 cities. Strong, accelerating demand.
- The data plumbing is **fragmented, broken, and largely closed** — no incumbent aggregates it.
  That gap is the moat. Internationally the data is abundant *and* already served by incumbents
  (Chartmetric, Soundcharts, Viberate) — a late me-too.
- The defensible asset is **entity resolution + a knowledge graph over deliberately messy,
  half-closed Indian signals** — not clean API aggregation (anyone can call the YouTube API).

### Two data layers

1. **Structured layer** — artists + ticketed events. APIs and ticketing sites.
   Pattern: `structured source → clean observation`.
2. **Unstructured layer** — events, communities, activities (free or paid) living in social
   groups, promoter pages, and community platforms. Pattern:
   `unstructured post → extract → dedup → resolve → low-confidence observation`.
   This is the long tail — most of the 34,000 shows are *not* on BookMyShow — and it is the
   layer competitors do not have.

### Data-access reality (why the sequence looks the way it does)

| Source | Access (2026) | Use |
|---|---|---|
| YouTube Data API v3 | Open, cheap (1 quota unit); `regionCode=IN` | Primary digital-popularity signal |
| Google Trends | Open (unofficial) | Per-artist / per-city demand proxy |
| Spotify Web API | **Tightened Feb 2026** — popularity cut off for new apps; top-tracks removed | Secondary only |
| MusicBrainz | Fully open | Canonical entity/ID backbone |
| JioSaavn / Gaana | Unofficial endpoints (fragile) | India-native streaming signal |
| BookMyShow (~75% share) / District | **No public API** | Demand moat — managed scrape |
| Ticketmaster Discovery | **No India coverage** | N/A |
| Bandsintown / Songkick | Partnership-gated / closed | N/A |
| Instagram/Meta | Locked down; Groups API dead since 2024 | Aggregator (Phyllo) or guarded scrape |
| Knowafest / AllEvents.in | Public listings | Grassroots aggregator injection |
| Luma / Eventbrite / Meetup | Official APIs (+ Apify) | Urban/creative scene events |
| Telegram public channels | `t.me/s/{channel}` — no login, no account risk | Grassroots promoter/scene signal |
| WhatsApp groups | Encrypted, ToS-prohibited | **Do not scrape** — opt-in partner feed only |

---

## 1. Compliance envelope (build first, once)

Compliance is structural, not per-adapter. Every observation carries a standardized provenance
block. India-first means DPDP applies to personal data of Indian residents even when public —
so the platform stays **entity-level (artist / venue / event / organizer / community), never
individual-fan-level**.

### Shared schema addition — `shared/nquark_common/schemas.py`

```python
class ObservationProvenance(BaseModel):
    acquisition_method: Literal[
        "official_api", "aggregator_api", "public_scrape", "partner_feed"
    ]
    legal_basis: Literal[
        "platform_api_tos",            # we accepted the API ToS
        "aggregator_contract",         # Phyllo / Chartmetric license
        "public_figure_professional",  # artist/organizer in business capacity
        "legitimate_interest",
    ]
    data_subject_type: Literal["entity", "individual"] = "entity"
    contains_pii: bool = False
    source_url: str | None = None
    collected_at: datetime            # ingest time, distinct from observed timestamp
    adapter_version: str
    robots_respected: bool | None = None   # scrape adapters
    logged_out: bool | None = None         # scrape adapters
    consent_source: str | None = None      # partner_feed only
```

Stored in `metadata["provenance"]`.

### Enforcement rules (wired into the ingest path, not left to adapter authors)

1. Reject any observation with `data_subject_type == "individual"` or `contains_pii == True`
   under the India-first profile. (DPDP guardrail.)
2. `public_scrape` observations must have `logged_out == True` and `robots_respected == True`
   or they are rejected. (Avoids the *Meta v. Bright Data* breach-of-contract theory; logged-out
   collection means no ToS was agreed to.)
3. `partner_feed` observations must set `consent_source`. (WhatsApp / private-group admissibility.)
4. **Entity-extraction-only** for crawl adapters: persist only the event/organizer/venue/community
   entities extracted from a post — never the post author, commenters, or member lists as data
   subjects.

### Legal grounding (summary)

- Public, logged-out data is generally legal to scrape (*hiQ v. LinkedIn*; *Meta v. Bright Data*
  2024) **provided no access controls are bypassed** (no login-wall, no CAPTCHA defeat).
- ToS breach ≠ CFAA — scraping behind an accepted ToS can be breach of contract; logged-out avoids it.
- India DPDP (enforcement ramping 2025–26) + GDPR apply to personal data even when public.
- EU AI Act (2026) bans untargeted facial-image scraping — relevant if the vision pipeline touches faces.

**Effort: ~0.5 day. Makes every downstream adapter auditable by construction.**

---

## 2. Foundation dependencies (blockers)

Both must land before the high-value adapters.

- **Generalize entity-service beyond artists** — parameterize `entity_type`; add generic
  `create_entity` / `resolve_entity` for `venue`, `event`, `city`, `organizer`, `community`,
  `activity`. Hard blocker for all event/community adapters.
- **Batch observation write** — `POST /v1/observations/bulk` + replace the N+1 loop in
  signal-service's `observation_client`. Blocker for anything crawling (one event page = dozens
  of observations).

---

## 3. New entity dimensions

The ontology names these; nothing emits them yet.

- **Organizer** — a promoter, collective, college society, or brand running events.
- **Community** — a recurring night, a Telegram/Discord scene, a festival, a scene identity.
- **Activity** — a lighter Event: open mic, jam, workshop, meetup. No ticket, no venue API,
  often only a social post.

Extend the Event model with:
- `formality`: `ticketed_event` ↔ `informal_activity`
- `price_type`: `free` / `paid` / `donation`

Graph edges these unlock (from the ontology):
`Organizer→runs→Community`, `Community→hosts→Event/Activity`,
`Artist→performs_for→Community`, `Event→occurs_at→Venue`, `Venue→located_in→City`.

---

## 4. Structured layer — artist & event adapters

Each follows the existing `adapters/<name>.py` pattern: a `normalize_*()` producing
`list[NormalizedObservation]`, a `Client` class, and a route. Adapters emit raw alias entities
(e.g. `artist:youtube:<channelId>`) that entity-service resolves to canonical IDs.

Effort key: S ≈ 1–2 days, M ≈ 3–5 days, L ≈ 1–2 weeks.

| # | Adapter | Type | Tier / method | Rationale | Effort |
|---|---|---|---|---|---|
| 1 | **YouTube Data API** | signal | official API | Replaces Spotify as primary; open, cheap, India's #1 music surface | S |
| 2 | **Google Trends** | signal | open (unofficial) | Per-artist / per-city demand proxy | S |
| 3 | **MusicBrainz** | entity backbone | open API | Canonical IDs + metadata; dedups everything after | M |
| 4 | **JioSaavn** | signal | unofficial endpoints | India-native streaming; isolate (fragile) but irreplaceable | M |
| 5 | **BookMyShow / District** | signal + graph | managed scrape (logged-out) | The demand moat; real sell-through no API exposes | L |
| 8 | **Spotify (existing)** | signal | official API — demoted | Keep as secondary; no new work | — |

### 4.1 YouTube Data API — #1
`source="youtube"`, `acquisition_method="official_api"`, `legal_basis="platform_api_tos"`, confidence 0.95
- Attributes: `subscriber_count`, `total_view_count`, `video_count`,
  `trending_rank_in` (via `videos.list?chart=mostPopular&regionCode=IN&videoCategoryId=10`),
  `recent_upload_velocity`.
- Use `chart=mostPopular` (1 quota unit), never `search.list` (100 units).

### 4.2 Google Trends — #2
`source="google_trends"`, `acquisition_method="public_scrape"`, `logged_out=True`, confidence 0.7
- Attributes: `search_interest_national` (0–100), `search_interest_by_city` (top Indian metros),
  `interest_momentum_4w`.
- Best *geographic* demand signal → powers regional-strength / venue-fit features later.

### 4.3 MusicBrainz — #3
`source="musicbrainz"`, `acquisition_method="official_api"`, confidence 0.9
- Entity enrichment, not popularity: canonical MBID, aliases, area/country, genres → resolves
  into entity-service as the ID spine.
- Known gap: Indian regional artists without English aliases won't have an MBID. Fall back to
  creating a native canonical entity and flag `metadata["mbid_missing"]=True` for later reconciliation.

### 4.4 JioSaavn — #4
`source="jiosaavn"`, `acquisition_method="public_scrape"`, `logged_out=True`, `robots_respected=True`, confidence 0.6
- Attributes: `jiosaavn_follower_count`, `chart_rank`, `play_estimate` where exposed.
- **Isolation requirement:** circuit-breaker + feature flag; endpoint break must degrade gracefully,
  never fail ingest. Best-effort.

### 4.5 BookMyShow / District — #5 (the moat)
`source="bookmyshow"`, `acquisition_method="public_scrape"`, `legal_basis="public_figure_professional"`, `logged_out=True`
- Via Bright Data or Apify against **public event pages only** — no login, no CAPTCHA bypass.
- Emits a graph, not just attributes: `event` (name, datetime, city), `venue` (name, city,
  capacity if shown), `artist`↔`event` link, price band → feeds graph-service.
- Only source of real sell-through/demand — highest defensibility.

---

## 5. Media layer — flyer / poster OCR (`media-service`)

`source="flyer_ocr"`, `acquisition_method="public_scrape"`, confidence ≈ 0.5, `metadata["extraction"]="ocr"`

Pipeline (open-source stack): acquire image (`yt-dlp` / direct) → **PaddleOCR + Tesseract**
(Indian-script capable; grayscale / 2× upscale / contrast preprocessing for stylized overlays)
→ regex/LLM extraction of `{date, venue, city, lineup, price}` → **CLIP** embedding for
dedup/similarity → emit event/venue/artist observations (lower trust, human-verifiable).

Why it matters: for tier-2/3 cities (Shillong, Guwahati, Nashik) a flyer is often the *only*
structured event record. Turns image chaos into graph nodes.

---

## 6. Unstructured layer — community & grassroots crawl (`crawl-service`)

Architecturally closer to flyer-OCR than to the YouTube adapter. PII risk is highest here —
groups contain individuals. The envelope's `data_subject_type` + entity-extraction-only rules are
the protection: ingest the *activity / organizer-page* as an entity, never the member list.

### Source taxonomy (tiered by openness + risk)

| Tier | Sources | Access | Risk | Verdict |
|---|---|---|---|---|
| Structured aggregators | **Knowafest** (15,000+ India college fests), **AllEvents.in**, Insider/District listings | Public listing pages | Low | Start here — aggregation already done |
| Semi-open event platforms | **Luma** (official API, 77+ cities), **Eventbrite**, **Meetup** | Official APIs / Apify | Low | Clean; urban/creative scenes |
| Open social channels | **Telegram public channels** `t.me/s/{channel}` | No login, no account risk | Low | Gem; also Telethon/MTProto for depth |
| Semi-closed social | **Instagram promoter/organizer pages** | Phyllo business-discovery or logged-out scrape | Medium | Organizer pages → Organizer entities |
| Community servers | **Discord** scene servers | Official bot API (join first) | Medium | Later; niche, high-intent |
| Closed + defended | **Facebook Events / Groups** | API dead since 2024; scrape-only, heavy anti-bot | High | Last resort, managed scrape, logged-out, or skip |
| Red line | **WhatsApp groups** | Encrypted, ToS-prohibited, PII-dense | Severe | Do not scrape — opt-in partner feed only |

### Crawl adapters — build order

| # | Adapter | Emits | Method | Effort |
|---|---|---|---|---|
| C1 | **Knowafest + AllEvents.in** | Event/Activity, Organizer (society), City | Public-listing scrape | S–M |
| C2 | **Luma / Eventbrite / Meetup** | Event/Activity, Organizer, Venue | Official API + Apify | M |
| C3 | **Telegram public channels** | Activity, Organizer, Community | `t.me/s/` render → LLM extract | M |
| C4 | **Instagram organizer pages** | Organizer, Activity, Artist↔Community | Phyllo / logged-out scrape → extract | M–L |
| C5 | **Discord scene servers** | Community, Activity | Bot API (opted-in servers) | L |
| C6 | **Facebook Events (guarded)** | Event/Activity | Managed scrape, logged-out | L / optional |
| C7 | **WhatsApp / partner feeds** | Community, Activity | Opt-in consented feed only | — |

### Extraction + resolution pipeline (shared by C1–C6)

```
raw post / listing
  → LLM/NER extraction  {title, datetime, venue, city, price_type, lineup, organizer}
  → confidence 0.4–0.6, metadata["extraction"]="community_crawl"
  → fuzzy dedup  (title + date + city embedding match against graph)
  → entity resolution: Organizer, Community, Venue, Activity  (needs generalized entity-service)
  → graph edges: Organizer→runs→Community, Community→hosts→Activity,
                 Artist→performs_for→Community, Activity→occurs_at→Venue
  → bulk observation write with provenance block
```

This is what produces **Community Intelligence** — the scene-centric layer streaming-centric
competitors lack.

### Compliance deltas for this layer

- **`entity_extraction_only`** — enforced at ingest; persist only event/organizer/venue/community
  entities, never authors/commenters/members.
- **`consent_source` on `partner_feed`** — WhatsApp / private-group data admissible only when set.

---

## 7. Consolidated timeline

```
Week 1     Foundation: provenance envelope + enforcement + generalized entity-service + bulk endpoint
Week 2-3   Artist signals #1 YouTube, #2 Google Trends, #3 MusicBrainz, #4 JioSaavn
             ‖  C1 Knowafest / AllEvents  (quick structured injection of Organizer/Community/Activity nodes)
Week 4-5   #5 BookMyShow / District  ‖  C2 Luma/Eventbrite/Meetup  ‖  C3 Telegram
Week 5-6   #6 Flyer OCR (media-service)  ‖  C4 Instagram organizers
Week 7+    C5 Discord, C6 Facebook (guarded), C7 partner feeds — scene-by-scene
ongoing    #8 Spotify demoted to secondary — no new work
```

**Critical path:** Foundation → generalized entities → #5 / C-series.
Everything before #5 is a quick win that also de-risks by producing a working multi-signal artist
score while the hard scraping/extraction proceeds. By end of Week 3 there is a genuinely
multi-source India-relevant artist signal (YouTube + Trends + JioSaavn + MusicBrainz spine) plus a
graph made alive by Knowafest's college-fest nodes — enough to drive the first deterministic
demand score.

**Pulled early on purpose:** C1 (Knowafest = 15,000+ structured fests) and C3 (Telegram `t.me/s/`
= highest grassroots-signal-per-risk).

**Deliberately deferred:** X/Twitter (twscrape — high maintenance, low India music signal),
Chartmetric/Soundcharts (buy-vs-build — revisit once the India graph beats theirs),
`crawl-service` broad web crawl and `media-service` beyond flyer OCR.

---

## 8. Guiding principle

Treat scraping as the expensive last resort, not the default. Prefer official/aggregator APIs and
no-login channels that keep collection entity-level and logged-out; make the closed, PII-dense
sources (Facebook Groups, WhatsApp) opt-in or skip them. The moat is not scraping harder than
everyone else — it is the **extraction + graph resolution** that turns messy Indian scene data
into structured, explainable community and artist intelligence.
