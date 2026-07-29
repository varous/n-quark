export type Observation = {
  id: string;
  entity: string;
  attribute: string;
  value: unknown;
  source: string;
  timestamp: string;
  confidence: number;
  evidence: Record<string, unknown>;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type RecentObservationsResponse = {
  count: number;
  observations: Observation[];
};

export type EntityAlias = {
  alias_key: string;
  source: string;
  created_at: string;
};

export type Entity = {
  id: string;
  entity_type: string;
  display_name: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  aliases: EntityAlias[];
};

export type EntityResolveResponse = {
  canonical_id: string;
  entity: Entity;
  created: boolean;
  alias_linked: boolean;
};

export type PlatformStatus = {
  status: string;
  network_mode?: string;
  timestamp: string;
  services: Record<
    string,
    { status: string; service?: string; detail?: string; timestamp?: string }
  >;
};

export const API_BASE = import.meta.env.VITE_API_URL ?? "/api";

export const DEMO_SPOTIFY_ID = "4tZwfgrHOc3mvqYFCOCYO6";
export const DEMO_SPOTIFY_ALIAS = `artist:spotify:${DEMO_SPOTIFY_ID}`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function fetchPlatformStatus() {
  return request<PlatformStatus>("/v1/platform/status");
}

export function fetchRecentObservations(limit = 50) {
  return request<RecentObservationsResponse>(`/v1/observations/recent?limit=${limit}`);
}

export function fetchEntityByAlias(aliasKey: string) {
  return request<Entity>(`/v1/entities/by-alias/${encodeURIComponent(aliasKey)}`);
}

export function resolveSpotifyArtist(spotifyId: string, displayName: string) {
  return request<EntityResolveResponse>(
    `/v1/entities/artists/resolve-spotify/${spotifyId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: displayName }),
    },
  );
}

export function ingestSpotifyArtist(spotifyId: string) {
  return request<{ observations_created: number; entity: string; name: string }>(
    `/v1/signals/spotify/artists/${spotifyId}/ingest`,
    { method: "POST" },
  );
}

export function formatValue(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString();
}
