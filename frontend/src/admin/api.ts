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
  // Local mode issues no token; a bearer token (dev-auth) is attached only if one is present.
  if (session.token) headers.Authorization = `Bearer ${session.token}`;
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (res.status === 401) {
    throw new ApiError(401, "Not authorized — the gateway is not in local mode. See docs/deployment.md.");
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
};

// ---- types (loose; the BFF is the source of truth) ----
export type Me = { sub: string; role: string; auth_mode: string; local_mode: boolean; mutations_enabled: boolean };
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
export type EventDetail = {
  canonical_event_id: string; current_view: Record<string, unknown>;
  relationships: Rel[]; resolved_entities: EntitySummary[]; source_records: Array<Record<string, unknown>>;
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
