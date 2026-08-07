# Provider: Google Trends — Phase 5A

Google Trends is provider-neutral and feature-gated, with **two modes** and **no unofficial scraping**.

```
OFFICIAL_API   used only with valid alpha credentials + endpoint; else reports ACCESS_UNAVAILABLE
IMPORT         structured ingestion of legitimately-obtained CSV exports (the interim fallback)
```

## Alpha limitation

The official Google Trends API is in limited alpha. At implementation time no credential/config was
present in the environment (we check env/secrets only — we do **not** search developer machines for
hidden credentials). So the official provider is **disabled** and reports:

```json
{ "mode": "IMPORT", "status": "ACCESS_UNAVAILABLE",
  "reason": "Google Trends API alpha credentials/endpoint not configured", "fallback": "IMPORT" }
```

We do **not** fabricate endpoint shapes from inaccessible alpha docs. The official provider is already
wired behind the same contract; when `NQUARK_GOOGLE_TRENDS_API_KEY` + `NQUARK_GOOGLE_TRENDS_API_BASE` are
set and `NQUARK_GOOGLE_TRENDS_MODE=OFFICIAL_API`, it switches on without changing the read models.

### Manual alpha-access prerequisite

To enable OFFICIAL_API: obtain Google Trends API alpha access, then set (in
artist-intelligence-service secrets) `NQUARK_GOOGLE_TRENDS_MODE=OFFICIAL_API`,
`NQUARK_GOOGLE_TRENDS_API_KEY=<key>`, `NQUARK_GOOGLE_TRENDS_API_BASE=<endpoint>`. Until then, IMPORT is
the supported path. (Note: signal-service has a separate, pre-existing proxy-based Trends adapter used by
the legacy graph pipeline; it is deliberately **not** the Phase 5A demand path.)

## Import fallback

`POST /v1/internal/trends/import` ingests a CSV export obtained legitimately from the Trends UI. Supports
both shapes:

- **interest by region** (`Region,<query>: (geo)` → `West Bengal,100`) → `REGION` observations;
- **interest over time** (`Week,<query>: (range)` → `2026-07-01,80`) → `COUNTRY` observations with
  `provider_timestamp`.

The import preserves original values, the query/topic label, geography, date range, export/import
timestamps, the normalization context, and the source-file provenance. Every observation is
`IMPORTED_PROVIDER_EXPORT` (distinguishable from OFFICIAL_API data) and carries:

```json
{ "provider_mode": "IMPORT", "normalization": "trends_0_100_within_pull",
  "comparison_window": "...", "time_range": "...", "geo": "IN", "identity_basis": "SEARCH_TERM_BASED",
  "source_file": "...", "export_timestamp": "...",
  "scale_note": "relative interest 0-100 within THIS pull; not comparable across pulls" }
```

Malformed exports (no recognizable header, no numeric rows) are rejected with `TrendsImportError`
(HTTP 422). Re-importing the same file is idempotent (same **export fingerprint** → 0 new observations).

## Observation semantics

The single metric is `GOOGLE_SEARCH_INTEREST`, unit `relative_interest_0_100` — **relative** search
interest within one pull, **never** absolute search volume. Independently normalized exports carry
distinct export fingerprints and are grouped by normalization context; they are **never** compared as if
they shared a scale.

## Search term vs topic

`SEARCH_TERM` and `TOPIC` are kept distinct — a canonical artist may map to a Trends topic id **or** an
explicit search term. The `identity_type` (`SEARCH_TERM` / `TOPIC_ID`) and `provenance.identity_basis`
(`SEARCH_TERM_BASED` / `TOPIC_BASED`) record which interpretation produced the data. Their histories are
**never silently combined**.

## Geography

Region scope ids use ISO 3166-2:IN codes (e.g. `IN-WB`) with the provider's exact label preserved
(`West Bengal`). The provider's granularity is preserved exactly — no city-level precision is invented
from state/subregion data. Unmapped regions keep a slug scope id and a `region_iso_mapped=false` note.

## Configuration

`NQUARK_GOOGLE_TRENDS_MODE` (`IMPORT` default / `OFFICIAL_API` / `DISABLED`),
`NQUARK_GOOGLE_TRENDS_API_KEY`, `NQUARK_GOOGLE_TRENDS_API_BASE`, `NQUARK_GOOGLE_TRENDS_DEFAULT_REGION`.
