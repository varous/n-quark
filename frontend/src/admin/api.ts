// Admin BFF client. The browser talks ONLY to the gateway admin surface (/api -> gateway /admin/v1).
const API_BASE = (import.meta.env.VITE_API_URL ?? "/api") + "/admin/v1";

const TOKEN_KEY = "nquark_admin_token";
const ROLE_KEY = "nquark_admin_role";
const SUB_KEY = "nquark_admin_sub";

export const session = {
  get token() {
    return localStorage.getItem(TOKEN_KEY);
  },
  get role() {
    return localStorage.getItem(ROLE_KEY) ?? "";
  },
  get sub() {
    return localStorage.getItem(SUB_KEY) ?? "";
  },
  set(token: string, role: string, sub: string) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(ROLE_KEY, role);
    localStorage.setItem(SUB_KEY, sub);
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(ROLE_KEY);
    localStorage.removeItem(SUB_KEY);
  },
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(init?.headers as Record<string, string>) };
  // Production uses an httpOnly session cookie (sent automatically). A bearer token is attached only
  // for the isolated dev-auth path when one is present in localStorage.
  if (session.token) headers.Authorization = `Bearer ${session.token}`;
  const res = await fetch(`${API_BASE}${path}`, { credentials: "same-origin", ...init, headers });
  if (res.status === 401) {
    throw new ApiError(401, "Not authenticated.");
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch { /* non-json */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const qs = (params: Record<string, unknown>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "" && v !== false) p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const api = {
  login: (username: string, role: string) =>
    req<{ token: string; role: string; sub: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, role }),
    }),
  me: () => req<Me>("/auth/me"),
  authStatus: () => req<AuthStatus>("/auth/status"),
  logout: () => req<unknown>("/auth/logout", { method: "POST" }),
  dashboard: () => req<Dashboard>("/dashboard"),
  sources: () => req<{ sources: SourceRow[] }>("/sources"),
  sourceDiagnostics: (source: string) => req<SourceDiagnostics>(`/sources/${encodeURIComponent(source)}/diagnostics`),
  events: (f: Record<string, unknown>) => req<Paged<EventRow>>(`/events${qs(f)}`),
  eventDetail: (id: string) => req<EventDetail>(`/events/${encodeURIComponent(id)}`),
  eventTimeline: (id: string) => req<Timeline>(`/events/${encodeURIComponent(id)}/timeline`),
  eventEvidence: (id: string) => req<Evidence>(`/events/${encodeURIComponent(id)}/evidence`),
  entities: (f: Record<string, unknown>) => req<Paged<EntityRow>>(`/entities${qs(f)}`),
  entityDetail: (t: string, id: string) => req<EntityDetail>(`/entities/${t}/${encodeURIComponent(id)}`),
  resolutionQueue: (f: Record<string, unknown>) => req<{ count: number; items: QueueItem[]; by_status?: Record<string, number>; states?: string[]; available: boolean }>(`/resolution-queue${qs(f)}`),
  captureJobs: (f: Record<string, unknown>) => req<{ count: number; jobs: Job[]; available: boolean }>(`/capture-jobs${qs(f)}`),
  captureJob: (id: string) => req<Job & { detail?: unknown }>(`/capture-jobs/${encodeURIComponent(id)}`),
  subgraph: (root: string, depth: number, rel?: string) =>
    req<Subgraph>(`/graph/subgraph${qs({ root, depth, rel_types: rel })}`),
  systemHealth: () => req<SystemHealth>("/system-health"),
  search: (q: string) => req<SearchResult>(`/search${qs({ q })}`),
  rerunEnrichment: (event_id: string, reason: string) =>
    req<{ request_id: string; ok: boolean }>("/operations/rerun-enrichment", {
      method: "POST",
      body: JSON.stringify({ event_id, reason }),
    }),
  rerunEntityResolution: (event_id: string, source: string, source_record_id: string, reason: string) =>
    req<{ request_id: string; ok: boolean }>("/operations/rerun-entity-resolution", {
      method: "POST",
      body: JSON.stringify({ event_id, source, source_record_id, reason }),
    }),
  captureNow: (source: string, source_record_id: string, reason: string) =>
    req<{ request_id: string; ok: boolean; result: unknown }>("/operations/capture-now", {
      method: "POST",
      body: JSON.stringify({ source, source_record_id, reason }),
    }),
  candidate: (candidate_id: string) => req<Record<string, unknown>>(`/resolution-queue/candidates/${candidate_id}`),
  // governance commands
  gov: (verb: string, body: Record<string, unknown>) =>
    req<Record<string, unknown>>(`/resolution-decisions/${verb}`, { method: "POST", body: JSON.stringify(body) }),
  reverse: (decision_id: string, reason: string) =>
    req<Record<string, unknown>>(`/resolution-decisions/${encodeURIComponent(decision_id)}/reverse`, {
      method: "POST", body: JSON.stringify({ reason }),
    }),
  decisions: (f: Record<string, unknown>) =>
    req<{ count: number; items: Array<Record<string, unknown>> }>(`/resolution-decisions${qs(f)}`),
  // Bounded export honouring active filters. Returns the BFF href (opened as a download link).
  exportHref: (table: string, format: "csv" | "json", f: Record<string, unknown>) =>
    `${API_BASE}/export/${table}${qs({ ...f, format })}`,
  // ---- demand intelligence (Phase 5A.2; read-only) ----
  demandOverview: () => req<DemandOverview>("/demand/overview"),
  demandSummary: () => req<DemandSummary>("/demand/summary"),
  demandArtist: (id: string) => req<ArtistDemand>(`/demand/artists/${encodeURIComponent(id)}`),
  demandArtistObservations: (id: string, f: Record<string, unknown>) =>
    req<DemandObservations>(`/demand/artists/${encodeURIComponent(id)}/observations${qs(f)}`),
  demandEvent: (id: string) => req<EventDemand>(`/demand/events/${encodeURIComponent(id)}`),
  // ---- product catalog + coverage + movement (Phase 5B.2) ----
  catalogArtists: (f: Record<string, unknown>) => req<CatalogArtistList>(`/catalog/artists${qs(f)}`),
  catalogVenues: (f: Record<string, unknown>) => req<CatalogVenueList>(`/catalog/venues${qs(f)}`),
  catalogVenueDetail: (id: string) => req<CatalogVenueDetail>(`/catalog/venues/${encodeURIComponent(id)}`),
  catalogCounts: () => req<ProductCounts>("/catalog/counts"),
  artistCoverage: (id: string) => req<ArtistCoverage>(`/demand/artists/${encodeURIComponent(id)}/coverage`),
  artistMovement: (id: string) => req<ArtistMovement>(`/demand/artists/${encodeURIComponent(id)}/movement`),
  marketMovement: () => req<MarketMovement>("/market/movement"),
  dataQuality: () => req<DataQualityAudit>("/data-quality"),
  dataQualityReview: () => req<{ available: boolean; count?: number; by_class?: Record<string, number>; items?: Array<Record<string, unknown>> }>("/data-quality/review-queue"),
  dataQualityMetrics: () => req<{ available: boolean; mentions_processed?: number; flow?: Record<string, number>; open_review_items?: number; oldest_review_age_hours?: number | null; operator_corrected?: number; interpretation_method?: string; by_type?: Record<string, Record<string, number>> }>("/data-quality/metrics"),
  dataQualityCorrect: (body: { action: string; canonical_entity_id?: string; candidate_id?: string; reason?: string }) =>
    req<Record<string, unknown>>("/data-quality/correct", { method: "POST", body: JSON.stringify(body) }),
  // ---- research watchlist (Phase 5B.1; controlled write: research configuration) ----
  watchlist: (f: Record<string, unknown>) => req<WatchlistList>(`/research/watchlist${qs(f)}`),
  watchlistDiagnostics: () => req<WatchlistDiagnostics>("/research/watchlist/diagnostics"),
  watchlistTarget: (id: string) => req<WatchTarget>(`/research/watchlist/${encodeURIComponent(id)}`),
  watchlistAdd: (body: { display_name: string; canonical_artist_id?: string; youtube_hint?: string; reason?: string; priority?: number }) =>
    req<{ created: boolean; target: WatchTarget }>("/research/watchlist", { method: "POST", body: JSON.stringify(body) }),
  watchlistBulkPreview: (text: string) =>
    req<BulkPreview>("/research/watchlist/bulk/preview", { method: "POST", body: JSON.stringify({ text }) }),
  watchlistBulkAdd: (text: string, reason?: string) =>
    req<{ received: number; created: number; existing: number; targets: WatchTarget[] }>("/research/watchlist/bulk", { method: "POST", body: JSON.stringify({ text, reason }) }),
  watchlistPause: (id: string) => req<WatchTarget>(`/research/watchlist/${encodeURIComponent(id)}/pause`, { method: "POST" }),
  watchlistResume: (id: string) => req<WatchTarget>(`/research/watchlist/${encodeURIComponent(id)}/resume`, { method: "POST" }),
  watchlistPriority: (id: string, priority: number) => req<WatchTarget>(`/research/watchlist/${encodeURIComponent(id)}/priority`, { method: "POST", body: JSON.stringify({ priority }) }),
  watchlistReject: (id: string, reason?: string) => req<WatchTarget>(`/research/watchlist/${encodeURIComponent(id)}/reject`, { method: "POST", body: JSON.stringify({ reason }) }),
};

// ---- types (loose; the BFF is the source of truth) ----
export type Me = { sub: string; role: string; auth_mode: string; local_mode: boolean; mutations_enabled: boolean; environment?: string; region?: string; read_only?: boolean };
export type AuthStatus = { auth_mode: "oidc" | "local" | "disabled"; authenticated: boolean; sub: string | null; login_url: string; environment?: string; region?: string };
export type Paged<T> = { count: number; limit: number; offset: number; available?: boolean; hydrated?: boolean; capped?: boolean } & Record<string, T[]>;
export type SourceDiagnostics = {
  source: string; tracked_events: number; last_successful_capture: string | null;
  capture_success_rate: number | null; jobs_total: number; jobs_succeeded: number; jobs_failed: number;
  jobs_failed_terminal: number; failure_classifications: Record<string, number>; parser_failures: number;
  average_capture_gap_hours: number | null; stale_events: number; events_with_multiple_states: number;
  events_with_transitions: number; geography: { present: number; valid: number; placeholder: number; missing: number };
  entity_resolution: Record<string, { resolved: number; ambiguous: number; unresolved: number }>;
  available: boolean;
};
export type Dashboard = {
  cards: Record<string, number>;
  sources: Record<string, Record<string, number>>;
  attention_queues: Record<string, Array<Record<string, unknown>>>;
};
export type SourceRow = { source: string } & Record<string, number>;
export type EventRow = {
  canonical_event_id: string; title: string | null; source: string; source_record_id: string;
  city: string | null; starts_at: string | null; tracking_status: string; last_capture_status: string | null;
  state_count: number; transition_count: number; capture_gap_hours: number | null;
  enrichment_status: string | null; resolution_status: string | null; stale: boolean;
};
export type InterpretedRelationships = {
  canonical_event_id: string;
  artists: { resolved: Array<Record<string, unknown>>; resolved_count: number; needs_review: Array<Record<string, unknown>>; needs_review_count: number; unresolved_mentions: Array<Record<string, unknown>> };
  venue: { state: string; canonical_entity_id: string | null; raw_mentions: string[] };
  organizer: { state: string; canonical_entity_id: string | null; raw_mentions: string[] };
  note?: string;
};
export type EventDetail = {
  canonical_event_id: string; current_view: Record<string, unknown>;
  relationships: Rel[]; resolved_entities: EntitySummary[]; source_records: Array<Record<string, unknown>>;
  interpreted?: InterpretedRelationships | null;
  available: boolean;
};
export type Rel = { relationship: string; canonical_target: string; target_name: string | null; target_type: string; confidence: number | null; resolution_status: string | null };
export type EntitySummary = Record<string, unknown>;
export type Timeline = { canonical_event_id: string; available: boolean; transitions: TransitionRow[]; states: StateRow[]; current?: unknown };
export type TransitionRow = Record<string, unknown>;
export type StateRow = Record<string, unknown>;
export type Evidence = { canonical_event_id: string; available: boolean; resolved_fields?: Record<string, unknown>; candidates?: Array<Record<string, unknown>> };
export type EntityRow = {
  canonical_entity_id: string; canonical_name: string; entity_type: string; source_handles: number;
  sources: string[]; linked_event_count: number; linked_source_count: number; resolution_status: string;
  ambiguous_candidate_count: number; identity_state: string; last_observed: string | null;
};
export type EntityDetail = EntityRow & {
  identity_state: string; legacy_projection_id: string | null; linked_events: string[];
  source_handles: Array<Record<string, unknown>>; candidates: Array<Record<string, unknown>>;
  available: boolean;
};
export type QueueItem = Record<string, unknown>;
export type Job = {
  id: string; source: string; source_record_id: string; canonical_event_id: string | null;
  status: string; scheduled_at: string | null; started_at: string | null; completed_at: string | null;
  attempt_count: number; worker_id: string | null; lock_expires_at: string | null;
  result_code: string | null; next_capture_at: string | null;
};
export type Subgraph = {
  root: string; depth: number; max_nodes: number; capped: boolean; node_count: number; edge_count: number;
  nodes: Array<{ id: string; type: string; label: string }>;
  edges: Array<{ source: string; relationship: string; target: string; confidence: number | null }>;
};
export type SystemHealth = {
  services: Record<string, { available: boolean; status: number; health: Record<string, unknown> | null;
    version: string | null; flags: Record<string, unknown>; last_check: string }>;
  data_quality: Record<string, number>;
  checked_at?: string;
  gateway_migration?: Record<string, unknown>;
  feature_flags?: Record<string, boolean>;
  canonical_counts?: Record<string, number | null>;
};
export type SearchResult = { query: string; results: { events: Array<Record<string, unknown>>; entities: Array<Record<string, unknown>> } };

// ---- demand intelligence types (loose; the BFF is the source of truth) ----
export type ProviderMode = { mode: "REAL" | "MOCK" | "UNKNOWN"; source: string; available: boolean };
export type DemandOverview = {
  available: boolean;
  coverage: Record<string, unknown> | null;
  provider_health: { providers?: { youtube?: Record<string, unknown>; google_trends?: Record<string, unknown> } } | null;
  quota: { days?: Array<Record<string, unknown>>; youtube_max_searches_per_day?: number } | null;
  quota_buckets: Record<string, unknown> | null;
  scheduler: Record<string, unknown> | null;
  youtube_pipeline: Record<string, unknown> | null;
  artist_universe: Record<string, unknown> | null;
  downstream: Record<string, boolean>;
};
export type DemandSummary = {
  available: boolean; resolved_youtube_artists?: number | null;
  artists_with_youtube_identity?: number | null; artists_with_demand_observation?: number | null;
  stale_demand_artists?: number | null; youtube_mode?: string | null;
  scheduler_enabled?: boolean | null; scheduler_terminal_failures?: number | null;
};
export type ExternalIdentity = {
  id: string; provider: string; identity_type: string; provider_id: string;
  display_name: string | null; canonical_url: string | null; status: string;
  resolution_method: string | null; confidence: number; first_seen_at: string;
  last_verified_at: string | null; reason?: string | null; invalidation_reason?: string | null;
};
export type ArtistDemand = {
  canonical_artist_id: string; available: boolean;
  external_identities: ExternalIdentity[];
  youtube: Record<string, unknown> | null;
  google_trends: Record<string, unknown> | null;
  observed_live_supply: Record<string, unknown> | null;
  momentum: { components?: Record<string, unknown>; coverage?: Record<string, unknown>; notes?: string[] } | null;
  geography: { regions?: Array<Record<string, unknown>>; notes?: string[] } | null;
  notes: string[];
  downstream: Record<string, boolean>;
};
export type DemandObservations = {
  available: boolean; total?: number; limit?: number; offset?: number;
  items?: Array<Record<string, unknown>>;
};
// ---- product catalog + coverage + movement types (loose; the BFF is the source of truth) ----
export type CatalogArtist = {
  canonical_artist_id: string; name: string; events_observed: number; sources: string[];
  last_observed: string | null; watching: boolean; watch_status: string | null;
  youtube_identity_state: string | null; youtube_verified: boolean; owned_videos: number;
  has_demand_data: boolean; moving_content_count: number; last_demand_update: string | null;
};
export type CatalogArtistList = { available: boolean; monitoring_available: boolean; count: number | null; limit: number; offset: number; artists: CatalogArtist[] };
export type CatalogVenue = { canonical_venue_id: string; name: string; events_observed: number; sources: string[]; last_observed: string | null };
export type CatalogVenueList = { available: boolean; count: number | null; limit: number; offset: number; venues: CatalogVenue[] };
export type CatalogVenueDetail = {
  available: boolean; canonical_venue_id: string; name?: string; city?: string | null;
  events_observed?: number; sources?: string[]; last_observed?: string | null;
  events?: string[]; events_aggregated?: number; events_truncated?: boolean;
  artists?: Array<{ canonical_artist_id: string; name: string }>;
  organizers?: Array<{ canonical_organizer_id: string; name: string }>;
  source_handles?: number;
};
export type ProductCounts = { available: boolean; artists: number | null; venues: number | null; organizers: number | null };
export type CovState = "COLLECTED" | "ZERO_OBSERVED" | "NOT_COLLECTED" | "UNAVAILABLE" | "INSUFFICIENT_HISTORY" | string;
export type ArtistCoverage = {
  available?: boolean; canonical_artist_id: string;
  identity: { canonical_status?: string; youtube_identity?: { state?: string; verified_channel_id?: string | null; channel_url?: string | null; last_verified_at?: string | null } };
  live_activity: { state?: CovState; events_observed?: number; upcoming_events?: number; past_events?: number; cities?: string[]; venues_count?: number; organizers_count?: number; last_live_observation?: string | null; reason?: string };
  youtube: { state?: CovState; reason?: string; owned_videos_tracked?: number; ecosystem_videos_tracked?: number; videos_observed_last_24h?: number; videos_with_sufficient_history?: number; insufficient_history_videos?: number; most_recent_content_discovery?: string | null; last_statistics_observation?: string | null; moving_content_count?: number; movement_states?: Record<string, number>; cross_channel_activity?: boolean; unavailable_videos?: number };
  demand: { youtube_metrics?: { state?: CovState; observation_count?: number }; google_trends?: { state?: CovState; observation_count?: number; note?: string | null }; geographic_demand?: { state?: CovState; regions_covered?: number }; observation_history?: { first_observation?: string | null; last_demand_update?: string | null; total_observations?: number } };
  evidence: { first_observation?: string | null; most_recent_observation?: string | null; sources_contributing?: string[] };
  disclaimer?: string;
};
export type MovementItem = { canonical_artist_id?: string; video_id?: string; title?: string | null; relationship_type?: string; classification?: string; comparison_cohort?: string | null; observation_count?: number; baseline_sample_size?: number; supporting_values?: Record<string, number | null>; thresholds?: Record<string, number>; calculated_at?: string };
export type ArtistMovement = { available?: boolean; canonical_artist_id?: string; videos_considered?: number; counts?: Record<string, number>; moving_owned?: number; moving_ecosystem?: number; highest_velocity_per_hour?: number | null; independent_active_channels?: number; cross_channel_activity?: boolean; breakout_candidates?: MovementItem[]; rising?: MovementItem[]; cooling?: MovementItem[]; disclaimer?: string };
export type MarketMovement = { available?: boolean; artists_considered?: number; breakout_candidates?: MovementItem[]; rising?: MovementItem[]; cooling?: MovementItem[]; cross_channel_activity?: Array<Record<string, unknown>>; disclaimer?: string };
export type DataQualityItem = { canonical_entity_id: string; entity_type: string; name: string; problem_class: string; proposed_action: string; auto_safe: boolean; requires_review: boolean; state?: "open" | "repaired"; repaired?: boolean; sources: string[]; evidence: Record<string, unknown> };
export type DataQualityAudit = { available?: boolean; canonical_entities_audited?: number; clean?: number; counts_by_problem?: Record<string, number>; counts_by_type?: Record<string, Record<string, number>>; open_issues?: number; repaired_issues?: number; open_by_problem?: Record<string, number>; repaired_by_problem?: Record<string, number>; manifest?: DataQualityItem[]; manifest_truncated?: boolean; note?: string };

// ---- research watchlist types (loose; the BFF is the source of truth) ----
export type WatchTarget = {
  id: string; display_name: string; status: string; human_state: string;
  canonical_artist_id: string | null; youtube_identity_state: string | null;
  youtube_channel_id: string | null; youtube_hint: string | null;
  videos_tracked: number; last_observed_at: string | null;
  source: string; reason: string | null; priority: number; resolution_method: string | null;
  created_by: string; last_resolved_at: string | null; created_at: string; updated_at: string;
  detail: Record<string, unknown>;
};
export type WatchlistList = { available: boolean; total: number; limit?: number; offset?: number; targets: WatchTarget[] };
export type WatchlistDiagnostics = {
  available: boolean; total?: number; watching?: number; resolution_pending?: number;
  ambiguous?: number; paused?: number; rejected?: number; new?: number;
  targets_with_canonical_artist?: number; targets_with_verified_youtube_identity?: number;
  targets_receiving_demand_observations?: number;
};
export type BulkPreviewItem = { display_name: string; disposition: "NEW" | "DUPLICATE" | "MATCHES_CANONICAL"; canonical_artist_id: string | null };
export type BulkPreview = { count: number; new: number; duplicates: number; matches_canonical: number; items: BulkPreviewItem[] };

export type EventDemand = {
  canonical_event_id: string; available: boolean; resolved_artist_count: number; capped: boolean;
  artists: Array<{
    canonical_artist_id: string; raw_name: string | null; available: boolean;
    youtube: Record<string, unknown> | null; google_trends: Record<string, unknown> | null;
    momentum: Record<string, unknown> | null; event_response: Record<string, unknown> | null;
  }>;
  notes: string[];
};
