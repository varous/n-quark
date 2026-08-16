import { useEffect, useState } from "react";
import {
  api, ApiError, type ArtistCoverage, type ArtistMovement, type CatalogArtist, type CovState,
  type DataQualityItem, type MovementItem,
} from "./api";
import {
  Card, Empty, ErrorBox, Link, Loading, Section, Table, Unavailable,
  fmt, relTime, useAsync,
} from "./ui";

// ================================================================================================
// Market Observatory (Phase 5B.2 inc.2) — product-facing Artists / Venues / Artist detail / Market
// Movement. Sourced from the AUTHORITATIVE canonical registry (source-handle projections never appear
// here). Plain language throughout; ontology/ids live under Evidence. Distinguishes the four
// missing-data meanings (zero-observed / not-collected / unavailable / insufficient-history).
// ================================================================================================

// ---- missing-data → human copy -----------------------------------------------------------------
function stateCopy(state: CovState | undefined, kind: "youtube" | "events" | "trends" | "movement" | "generic"): string {
  switch (state) {
    case "COLLECTED": return "";
    case "ZERO_OBSERVED":
      return kind === "events" ? "No events observed yet" : "None observed";
    case "NOT_COLLECTED":
      return kind === "youtube" ? "YouTube monitoring has not started" : "Not being collected yet";
    case "UNAVAILABLE":
      return kind === "trends" ? "Google Trends data is currently unavailable" : "Currently unavailable";
    case "INSUFFICIENT_HISTORY": return "Not enough history yet";
    default: return "";
  }
}

function Chip({ label, tone = "slate" }: { label: string; tone?: "emerald" | "brand" | "amber" | "slate" }) {
  const t = {
    emerald: "border-emerald-600/40 bg-emerald-500/10 text-emerald-300",
    brand: "border-brand-600/40 bg-brand-500/10 text-brand-200",
    amber: "border-amber-600/40 bg-amber-500/10 text-amber-300",
    slate: "border-slate-600/50 bg-slate-500/10 text-slate-300",
  }[tone];
  return <span className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-medium ${t}`}>{label}</span>;
}

function ytStateChip(s: string | null): { label: string; tone: "emerald" | "brand" | "amber" | "slate" } {
  if (s === "RESOLVED") return { label: "YouTube verified", tone: "emerald" };
  if (s === "AMBIGUOUS") return { label: "Needs review", tone: "amber" };
  if (s === "PENDING") return { label: "Finding YouTube identity", tone: "brand" };
  return { label: "No YouTube channel", tone: "slate" };
}

// ================================================================================================
// Artists list
// ================================================================================================
const ARTIST_FILTERS: Array<{ key: string; label: string }> = [
  { key: "watching", label: "Watching" },
  { key: "youtube_verified", label: "YouTube verified" },
  { key: "needs_identity", label: "Needs YouTube identity" },
  { key: "has_events", label: "Has live events" },
  { key: "has_demand", label: "Has demand data" },
  { key: "moving", label: "Content moving" },
];

export function Artists() {
  const [filters, setFilters] = useState<Record<string, boolean>>({});
  const active = Object.keys(filters).filter((k) => filters[k]);
  const { data, loading, error } = useAsync(
    () => api.catalogArtists({ limit: 200, ...Object.fromEntries(active.map((k) => [k, true])) }),
    [active.join(",")]);

  return (
    <Section title="Artists"
      subtitle="Registry-backed artists n-quark recognizes, with the monitoring and live activity observed for each.">
      <div className="mb-4 flex flex-wrap gap-2">
        {ARTIST_FILTERS.map((f) => (
          <button key={f.key} onClick={() => setFilters((s) => ({ ...s, [f.key]: !s[f.key] }))}
            className={`rounded-full border px-3 py-1 text-xs transition ${filters[f.key] ? "border-brand-500/50 bg-brand-500/15 text-brand-200" : "border-slate-700 text-slate-400 hover:bg-slate-800"}`}>
            {f.label}
          </button>
        ))}
      </div>
      {loading && <Loading label="Loading artists…" />}
      {error && <ErrorBox message={error} />}
      {data && data.available === false && <Unavailable what="the artist registry" />}
      {data && data.available !== false && (
        data.artists.length === 0 ? <Empty message="No artists match these filters." /> : (
          <>
            {!data.monitoring_available && (
              <div className="mb-3 rounded-lg border border-amber-800/50 bg-amber-950/20 p-2.5 text-xs text-amber-300/90">
                Monitoring data (YouTube · demand · movement) is temporarily unavailable; identity and live activity are shown.
              </div>
            )}
            <ArtistTable rows={data.artists} />
            <p className="mt-3 text-xs text-slate-500">{data.count ?? data.artists.length} registry-backed artists.</p>
          </>
        )
      )}
    </Section>
  );
}

function ArtistTable({ rows }: { rows: CatalogArtist[] }) {
  return (
    <Card>
      <Table<CatalogArtist>
        rows={rows}
        empty="No artists."
        columns={[
          { key: "name", header: "Artist", render: (r) => (
            <a href={`#/artists/${encodeURIComponent(r.canonical_artist_id)}`} className="font-medium text-slate-100 hover:text-brand-200">{r.name}</a>) },
          { key: "live", header: "Live activity", render: (r) => r.events_observed > 0
            ? <span className="text-slate-300">{r.events_observed} event{r.events_observed === 1 ? "" : "s"}</span>
            : <span className="text-slate-600">No events observed</span> },
          { key: "yt", header: "YouTube", render: (r) => { const c = ytStateChip(r.youtube_identity_state); return <Chip label={c.label} tone={c.tone} />; } },
          { key: "demand", header: "Demand", render: (r) => r.has_demand_data
            ? <span className="text-slate-300">Collected</span> : <span className="text-slate-600">Not started</span> },
          { key: "moving", header: "Movement", render: (r) => r.moving_content_count > 0
            ? <Chip label={`${r.moving_content_count} moving`} tone="brand" /> : <span className="text-slate-600">—</span> },
          { key: "watch", header: "", render: (r) => r.watching ? <Chip label="Watching" tone="emerald" /> : null },
        ]}
      />
    </Card>
  );
}

// ================================================================================================
// Artist detail — Data Coverage is the centrepiece
// ================================================================================================
export function ArtistDetail({ id }: { id: string }) {
  const cov = useAsync(() => api.artistCoverage(id), [id]);
  const mov = useAsync(() => api.artistMovement(id), [id]);

  if (cov.loading) return <Loading label="Loading artist…" />;
  if (cov.error && !cov.data) return <ErrorBox message={cov.error} />;
  const c = cov.data;
  if (!c || c.available === false) return <Unavailable what="this artist" />;

  const yt = c.youtube ?? {};
  const ytVerified = c.identity?.youtube_identity?.state === "VERIFIED";
  const events = c.live_activity?.events_observed ?? 0;
  return (
    <div className="space-y-6">
      {/* 1. Who is this + compact states */}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-100">{shortName(id)}</h1>
        <div className="mt-2 flex flex-wrap gap-2">
          {c.identity?.canonical_status === "REGISTERED" && <Chip label="Artist" tone="slate" />}
          {ytVerified ? <Chip label="YouTube verified" tone="emerald" />
            : <Chip label={ytStateChip(mapYtState(c)).label} tone={ytStateChip(mapYtState(c)).tone} />}
          {(c.demand?.youtube_metrics?.state === "COLLECTED") && <Chip label="Demand monitored" tone="brand" />}
        </div>
      </div>

      {/* 2. Current picture — plain sentences */}
      <Card title="Current picture">
        <ul className="space-y-1.5 text-sm text-slate-300">
          <li>{events > 0 ? `${events} live event${events === 1 ? "" : "s"} observed` : "No live events observed yet"}
            {c.live_activity?.cities?.length ? ` · ${c.live_activity.cities.slice(0, 4).join(" · ")}` : ""}</li>
          <li>{ytVerified
            ? `YouTube monitoring active — ${yt.owned_videos_tracked ?? 0} video${(yt.owned_videos_tracked ?? 0) === 1 ? "" : "s"} tracked`
            : yt.state === "NOT_COLLECTED" ? "No verified YouTube channel yet — add this artist to the Watchlist to begin monitoring"
            : "Finding a verified YouTube identity"}</li>
          <li>{movementSentence(mov.data)}</li>
        </ul>
      </Card>

      {/* 3. Data Coverage */}
      <Section title="What n-quark knows">
        <div className="grid gap-4 lg:grid-cols-2">
          <CoverageCard title="Identity" rows={[
            ["Artist", c.identity?.canonical_status === "REGISTERED" ? "Identified" : "Not registered"],
            ["YouTube identity", ytLabel(c)],
            ["Verified channel", c.identity?.youtube_identity?.verified_channel_id ?? notCollected("youtube")],
            ["Identity last verified", c.identity?.youtube_identity?.last_verified_at ? relTime(c.identity.youtube_identity.last_verified_at) : "—"],
          ]} />
          <CoverageCard title="Live activity" rows={[
            ["Events observed", events > 0 ? String(events) : "No events observed yet"],
            ["Cities", c.live_activity?.cities?.length ? c.live_activity.cities.join(" · ") : "None observed"],
            ["Venues", covNum(c.live_activity?.venues_count, "events")],
            ["Organizers worked with", covNum(c.live_activity?.organizers_count, "events")],
            ["Most recent event", c.live_activity?.last_live_observation ? relTime(c.live_activity.last_live_observation) : "—"],
          ]} />
          <CoverageCard title="YouTube" rows={ytVerified ? [
            ["Verified channel", "Yes"],
            ["Owned videos tracked", String(yt.owned_videos_tracked ?? 0)],
            ["Ecosystem videos tracked", String(yt.ecosystem_videos_tracked ?? 0)],
            ["Observed in last 24h", String(yt.videos_observed_last_24h ?? 0)],
            ["Enough history for movement", `${yt.videos_with_sufficient_history ?? 0} video(s)`],
            ["Last statistics check", yt.last_statistics_observation ? relTime(yt.last_statistics_observation) : "—"],
          ] : [["Status", stateCopy(yt.state, "youtube") || yt.reason || "No verified YouTube channel yet"]]} />
          <CoverageCard title="Demand" rows={[
            ["YouTube metrics", c.demand?.youtube_metrics?.state === "COLLECTED"
              ? `${c.demand.youtube_metrics.observation_count} observations` : "None observed"],
            ["Google Trends", c.demand?.google_trends?.state === "COLLECTED"
              ? `${c.demand.google_trends.observation_count} observations` : "Not currently available"],
            ["Geographic demand", (c.demand?.geographic_demand?.regions_covered ?? 0) > 0
              ? `${c.demand?.geographic_demand?.regions_covered} regions` : "None observed"],
            ["Last demand update", c.demand?.observation_history?.last_demand_update
              ? relTime(c.demand.observation_history.last_demand_update) : "—"],
          ]} />
        </div>
      </Section>

      {/* 4. Content movement */}
      <MovementSection data={mov.data} loading={mov.loading} />

      {/* 5. Evidence / Advanced */}
      <details className="rounded-xl border border-slate-800 bg-slate-900/40 p-4 text-sm">
        <summary className="cursor-pointer text-slate-400">Evidence &amp; technical details</summary>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-3">
          <div><dt className="text-slate-500">Canonical id</dt><dd className="truncate text-slate-300">{id}</dd></div>
          <div><dt className="text-slate-500">Sources contributing</dt><dd className="text-slate-300">{c.evidence?.sources_contributing?.join(", ") || "—"}</dd></div>
          <div><dt className="text-slate-500">First observation</dt><dd className="text-slate-300">{c.evidence?.first_observation ? relTime(c.evidence.first_observation) : "—"}</dd></div>
          <div><dt className="text-slate-500">Most recent observation</dt><dd className="text-slate-300">{c.evidence?.most_recent_observation ? relTime(c.evidence.most_recent_observation) : "—"}</dd></div>
        </dl>
        <div className="mt-3"><Link to={`/demand/artists/${encodeURIComponent(id)}`} className="text-brand-300 hover:text-brand-200">Full demand detail →</Link></div>
      </details>
      <p className="text-xs text-slate-600">{c.disclaimer}</p>
    </div>
  );
}

function CoverageCard({ title, rows }: { title: string; rows: Array<[string, React.ReactNode]> }) {
  return (
    <Card title={title}>
      <dl className="space-y-1.5 text-sm">
        {rows.map(([k, v], i) => (
          <div key={i} className="flex items-baseline justify-between gap-3">
            <dt className="shrink-0 text-slate-500">{k}</dt>
            <dd className="truncate text-right text-slate-200">{v}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function MovementSection({ data, loading }: { data: ArtistMovement | null; loading: boolean }) {
  if (loading) return <Card title="Content movement"><Loading /></Card>;
  if (!data || data.available === false) return <Card title="Content movement"><p className="text-sm text-slate-500">Movement data is temporarily unavailable.</p></Card>;
  const total = (data.breakout_candidates?.length ?? 0) + (data.rising?.length ?? 0) + (data.cooling?.length ?? 0);
  if ((data.videos_considered ?? 0) === 0)
    return <Card title="Content movement"><p className="text-sm text-slate-500">No YouTube content has been collected for this artist yet, so there is nothing to analyze for movement.</p></Card>;
  if (total === 0)
    return <Card title="Content movement"><p className="text-sm text-slate-400">No unusual content movement currently detected across {data.videos_considered} tracked video(s).</p></Card>;
  return (
    <Section title="What is moving">
      <div className="space-y-3">
        {(data.breakout_candidates ?? []).map((m, i) => <MovementCard key={`b${i}`} m={m} kind="Breakout candidate" tone="brand" />)}
        {(data.rising ?? []).map((m, i) => <MovementCard key={`r${i}`} m={m} kind="Rising" tone="emerald" />)}
        {(data.cooling ?? []).map((m, i) => <MovementCard key={`c${i}`} m={m} kind="Cooling" tone="amber" />)}
      </div>
      <p className="mt-2 text-xs text-slate-600">{data.disclaimer}</p>
    </Section>
  );
}

function MovementCard({ m, kind, tone }: { m: MovementItem; kind: string; tone: "brand" | "emerald" | "amber" }) {
  const sv = m.supporting_values ?? {};
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Chip label={kind} tone={tone} />
          <div className="mt-1.5 truncate text-sm font-medium text-slate-100">{m.title ?? m.video_id}</div>
          <div className="mt-0.5 text-xs text-slate-500">{m.relationship_type === "ECOSYSTEM_CONTENT" ? "Ecosystem content" : "Owned content"}
            {sv.age_hours != null ? ` · ${Math.round(sv.age_hours)}h old` : ""}</div>
        </div>
      </div>
      <p className="mt-2 text-xs text-slate-400">Why this is notable:{" "}
        {sv.velocity_ratio_vs_baseline != null
          ? `${sv.velocity_ratio_vs_baseline}× the ${m.comparison_cohort ?? "comparable-age"} view velocity`
          : "abnormal view velocity"}{sv.acceleration != null ? `, acceleration ${sv.acceleration}×` : ""}.</p>
      <p className="mt-1 text-[11px] text-slate-600">Evidence: {m.observation_count ?? 0} observations · {m.baseline_sample_size ?? 0}-video baseline
        {m.calculated_at ? ` · checked ${relTime(m.calculated_at)}` : ""}.</p>
    </Card>
  );
}

// ================================================================================================
// Venues
// ================================================================================================
export function Venues() {
  const { data, loading, error } = useAsync(() => api.catalogVenues({ limit: 200 }), []);
  return (
    <Section title="Venues" subtitle="Registry-backed venues and the live activity observed at each.">
      {loading && <Loading label="Loading venues…" />}
      {error && <ErrorBox message={error} />}
      {data && data.available === false && <Unavailable what="the venue registry" />}
      {data && data.available !== false && (data.venues.length === 0 ? <Empty message="No venues observed yet." /> : (
        <>
          <Card>
            <Table
              rows={data.venues}
              columns={[
                { key: "name", header: "Venue", render: (r) => <a href={`#/venues/${encodeURIComponent(r.canonical_venue_id)}`} className="font-medium text-slate-100 hover:text-brand-200">{r.name}</a> },
                { key: "events", header: "Events observed", render: (r) => r.events_observed > 0 ? String(r.events_observed) : <span className="text-slate-600">None yet</span> },
                { key: "sources", header: "Sources", render: (r) => <span className="text-slate-400">{r.sources.join(", ") || "—"}</span> },
                { key: "last", header: "Last observed", render: (r) => r.last_observed ? relTime(r.last_observed) : "—" },
              ]}
            />
          </Card>
          <p className="mt-3 text-xs text-slate-500">{data.count ?? data.venues.length} registry-backed venues.</p>
        </>
      ))}
    </Section>
  );
}

export function VenueDetail({ id }: { id: string }) {
  const { data, loading, error } = useAsync(() => api.catalogVenueDetail(id), [id]);
  if (loading) return <Loading label="Loading venue…" />;
  if (error && !data) return <ErrorBox message={error} />;
  if (!data || data.available === false) return <Unavailable what="this venue" />;
  const events = data.events ?? [];
  const artists = data.artists ?? [];
  const organizers = data.organizers ?? [];
  const lifecycle = (data.event_lifecycle ?? []) as Array<{ canonical_event_id: string; lifecycle: { temporal_state?: string } }>;
  const upcoming = lifecycle.filter((e) => e.lifecycle?.temporal_state === "UPCOMING");
  const past = lifecycle.filter((e) => e.lifecycle?.temporal_state === "PAST");
  return (
    <div className="space-y-6">
      {/* Venue — where is this */}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-100">{data.name ?? shortName(id)}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Chip label="Venue" />
          {data.city && <Chip label={data.city} tone="slate" />}
          <Chip label={`${data.events_observed ?? 0} event${(data.events_observed ?? 0) === 1 ? "" : "s"} observed`} tone="brand" />
        </div>
      </div>

      {/* Activity — how much have we observed */}
      <Card title="Activity">
        <ul className="space-y-1.5 text-sm text-slate-300">
          <li>{(data.events_observed ?? 0) > 0
            ? `${data.events_observed} event${data.events_observed === 1 ? "" : "s"} observed here`
            : "No events observed here yet"}
            {data.city ? ` · ${data.city}` : ""}</li>
          <li>{artists.length > 0 ? `${artists.length} artist${artists.length === 1 ? "" : "s"} have appeared here` : "No artists identified here yet"}</li>
          <li>{organizers.length > 0 ? `${organizers.length} organizer${organizers.length === 1 ? "" : "s"} active here` : "No organizers identified here yet"}</li>
          <li>Last observed {data.last_observed ? relTime(data.last_observed) : "—"}</li>
        </ul>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={`Artists appearing here${artists.length ? ` · ${artists.length}` : ""}`}>
          {artists.length === 0 ? <Empty message="None identified yet." /> : (
            <ul className="flex flex-wrap gap-1.5 text-sm">
              {artists.map((a) => (
                <li key={a.canonical_artist_id}>
                  <a href={`#/artists/${encodeURIComponent(a.canonical_artist_id)}`} className="rounded-md border border-slate-700 px-2 py-1 text-xs text-slate-200 hover:border-brand-500 hover:text-brand-200">{a.name}</a>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title={`Organizers active here${organizers.length ? ` · ${organizers.length}` : ""}`}>
          {organizers.length === 0 ? <Empty message="None identified yet." /> : (
            <ul className="flex flex-wrap gap-1.5 text-sm">
              {organizers.map((o) => (
                <li key={o.canonical_organizer_id}>
                  <span className="rounded-md border border-slate-700 px-2 py-1 text-xs text-slate-200">{o.name}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title={`Events observed here${events.length ? ` · ${events.length}` : ""}`}>
        {events.length === 0 ? <p className="text-sm text-slate-500">No events observed at this venue yet.</p> : (
          <>
            <ul className="space-y-1 text-sm">
              {events.slice(0, 60).map((e) => (
                <li key={e}><a href={`#/events/${encodeURIComponent(e)}`} className="text-slate-300 hover:text-brand-200">{shortName(e)}</a></li>
              ))}
            </ul>
            {data.events_truncated && (
              <p className="mt-2 text-xs text-slate-600">Showing the {data.events_aggregated} most recent for relationship aggregation.</p>
            )}
          </>
        )}
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={`Upcoming Events · ${upcoming.length}`}>
          {upcoming.length ? <ul className="space-y-1 text-sm">{upcoming.map((e) => <li key={e.canonical_event_id}><a href={`#/events/${encodeURIComponent(e.canonical_event_id)}`} className="text-slate-300 hover:text-brand-200">{shortName(e.canonical_event_id)}</a></li>)}</ul> : <Empty message="No upcoming events observed." />}
        </Card>
        <Card title={`Past Events · ${past.length}`}>
          {past.length ? <ul className="space-y-1 text-sm">{past.slice(0, 20).map((e) => <li key={e.canonical_event_id}><a href={`#/events/${encodeURIComponent(e.canonical_event_id)}`} className="text-slate-300 hover:text-brand-200">{shortName(e.canonical_event_id)}</a></li>)}</ul> : <Empty message="No past events observed." />}
        </Card>
      </div>

      <details className="rounded-xl border border-slate-800 bg-slate-900/40 p-4 text-sm">
        <summary className="cursor-pointer text-slate-400">Evidence &amp; sources</summary>
        <div className="mt-2 text-xs text-slate-500">Sources: <span className="text-slate-300">{(data.sources ?? []).join(", ") || "—"}</span></div>
        <div className="mt-1 text-xs text-slate-500">Canonical id: <span className="text-slate-300">{id}</span></div>
        <div className="mt-1 text-xs text-slate-500">Source listings: <span className="text-slate-300">{fmt(data.source_handles ?? 0)}</span></div>
      </details>
    </div>
  );
}

// ================================================================================================
// Market Movement
// ================================================================================================
export function Market() {
  const { data, loading, error } = useAsync(() => api.marketMovement(), []);
  return (
    <Section title="Market Movement"
      subtitle="Observed abnormal YouTube movement across monitored artists — not a prediction or a ranking.">
      {loading && <Loading label="Scanning monitored content…" />}
      {error && <ErrorBox message={error} />}
      {data && (() => {
        const total = (data.breakout_candidates?.length ?? 0) + (data.rising?.length ?? 0) + (data.cooling?.length ?? 0);
        if ((data.artists_considered ?? 0) === 0 || total === 0) {
          return (
            <Card>
              <p className="text-sm text-slate-300">No verified YouTube channels are currently being monitored, so n-quark does not yet have content-movement evidence.</p>
              <p className="mt-2 text-sm text-slate-400">Add an artist's official YouTube channel through the{" "}
                <Link to="/watchlist" className="text-brand-300 hover:text-brand-200">Watchlist</Link> to begin content sensing.</p>
            </Card>
          );
        }
        return (
          <div className="space-y-6">
            <MarketGroup title="Breakout candidates" items={data.breakout_candidates} />
            <MarketGroup title="Rising" items={data.rising} />
            <MarketGroup title="Cooling" items={data.cooling} />
            <p className="text-xs text-slate-600">{data.disclaimer}</p>
          </div>
        );
      })()}
    </Section>
  );
}

function MarketGroup({ title, items }: { title: string; items?: MovementItem[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div>
      <div className="mb-2 text-xs font-medium uppercase tracking-widest text-slate-500">{title} · {items.length}</div>
      <div className="space-y-3">
        {items.map((m, i) => (
          <Card key={i}>
            <div className="flex items-center justify-between gap-3">
              <a href={`#/artists/${encodeURIComponent(m.canonical_artist_id ?? "")}`} className="text-sm font-medium text-brand-200 hover:text-brand-100">{shortName(m.canonical_artist_id ?? "")}</a>
              <Chip label={m.relationship_type === "ECOSYSTEM_CONTENT" ? "Ecosystem" : "Owned"} tone="slate" />
            </div>
            <div className="mt-1 truncate text-sm text-slate-200">{m.title ?? m.video_id}</div>
            <p className="mt-1 text-xs text-slate-500">{(m.supporting_values?.velocity_ratio_vs_baseline ?? "?")}× {m.comparison_cohort ?? "baseline"} · {m.observation_count ?? 0} obs · {m.baseline_sample_size ?? 0}-video baseline</p>
          </Card>
        ))}
      </div>
    </div>
  );
}

// ================================================================================================
// Data Quality / Identity Review (Advanced)
// ================================================================================================
const PROBLEM_LABEL: Record<string, string> = {
  PLACEHOLDER_ENTITY: "Placeholder (not a real entity)",
  COMPOUND_ENTITY: "Compound string (several artists)",
  CROSS_TYPE_CONFLICT: "Possible wrong type",
};

export function DataQuality() {
  const [tick, setTick] = useState(0);
  const reload = () => setTick((n) => n + 1);
  const { data, loading, error } = useAsync(() => api.dataQuality(), [tick]);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [picking, setPicking] = useState<string | null>(null);   // candidate_id whose picker is open

  const metrics = useAsync(() => api.dataQualityMetrics(), [tick]);
  const review = useAsync(() => api.dataQualityReview(), [tick]);

  async function correct(item: DataQualityItem, action: string) {
    setBusy(item.canonical_entity_id + action);
    setMsg(null);
    try {
      await api.dataQualityCorrect({ action, canonical_entity_id: item.canonical_entity_id,
        reason: `operator ${action} from Data Quality` });
      setMsg(`${action.replace(/_/g, " ").toLowerCase()} applied to “${item.name}”.`);
      reload();
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    } finally { setBusy(null); }
  }

  async function correctCandidate(candidateId: string, action: string, label: string) {
    setBusy(candidateId + action);
    setMsg(null);
    try {
      await api.dataQualityCorrect({ action, candidate_id: candidateId,
        reason: `operator ${action} from review queue` });
      setMsg(`${label} applied.`);
      reload();
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    } finally { setBusy(null); }
  }

  // §21 — link a review mention to an EXISTING canonical the operator selected (never an arbitrary id;
  // the BFF + resolver both reject anything that is not a known canonical of the correct type).
  async function linkExisting(candidateId: string, canonicalId: string, name: string) {
    setBusy(candidateId + "LINK");
    setMsg(null);
    try {
      await api.dataQualityCorrect({ action: "CONFIRM_EXISTING_MATCH", candidate_id: candidateId,
        canonical_entity_id: canonicalId, reason: `operator linked to existing ${canonicalId}` });
      setMsg(`Linked to “${name}”.`);
      setPicking(null);
      reload();
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    } finally { setBusy(null); }
  }

  return (
    <Section title="Data Quality" subtitle="Questionable canonical interpretations flagged for review. Corrections are governed, authenticated and audited — nothing is changed automatically.">
      {loading && <Loading label="Auditing the registry…" />}
      {error && <ErrorBox message={error} />}
      {data && data.available === false && <Unavailable what="the data-quality audit" />}
      {data && data.available !== false && (
        <>
          <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Card><div className="text-2xl font-semibold text-slate-100">{data.canonical_entities_audited ?? 0}</div><div className="mt-1 text-xs text-slate-500">Entities audited</div></Card>
            <Card><div className="text-2xl font-semibold text-emerald-300">{data.clean ?? 0}</div><div className="mt-1 text-xs text-slate-500">Look clean</div></Card>
            <Card><div className="text-2xl font-semibold text-amber-300">{data.open_issues ?? ((data.counts_by_problem?.PLACEHOLDER_ENTITY ?? 0) + (data.counts_by_problem?.COMPOUND_ENTITY ?? 0) + (data.counts_by_problem?.CROSS_TYPE_CONFLICT ?? 0))}</div><div className="mt-1 text-xs text-slate-500">Open issues</div></Card>
            <Card><div className="text-2xl font-semibold text-slate-300">{data.repaired_issues ?? 0}</div><div className="mt-1 text-xs text-slate-500">Repaired (quarantined)</div></Card>
          </div>
          {msg && <div className="mb-3 rounded-lg border border-slate-800 bg-slate-900/60 p-2.5 text-xs text-slate-300">{msg}</div>}
          {(data.manifest?.length ?? 0) === 0 ? <Empty message="No data-quality issues detected." /> : (
            <div className="space-y-2.5">
              {(data.manifest ?? []).map((r) => (
                <Card key={r.canonical_entity_id + r.problem_class}>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className={`truncate text-sm font-medium ${r.repaired ? "text-slate-400 line-through decoration-slate-600" : "text-slate-100"}`}>{r.name}</span>
                        <span className="text-xs text-slate-500">{r.entity_type}</span>
                        {r.repaired
                          ? <Chip label="Repaired · quarantined" tone="emerald" />
                          : <Chip label={PROBLEM_LABEL[r.problem_class] ?? r.problem_class} tone="amber" />}
                        {!r.repaired && r.auto_safe && <Chip label="Auto-safe" tone="emerald" />}
                      </div>
                      <div className="mt-0.5 text-xs text-slate-500">{r.repaired ? "Already suppressed via governance — kept for provenance; no longer an open issue." : describeIssue(r)}</div>
                    </div>
                    <div className="flex items-center gap-1.5">
                      {!r.repaired && r.problem_class === "PLACEHOLDER_ENTITY" && (
                        <button disabled={!!busy} onClick={() => correct(r, "MARK_PLACEHOLDER")}
                          className="rounded-md border border-rose-800/60 px-2.5 py-1 text-xs text-rose-300 hover:bg-rose-950/30 disabled:opacity-50">Suppress placeholder</button>
                      )}
                      {!r.repaired && r.problem_class === "CROSS_TYPE_CONFLICT" && (
                        <button disabled className="rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-500" title="Confirm on the specific mention in the review queue">Dual-role → review</button>
                      )}
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          )}
          <p className="mt-3 text-xs text-slate-600">{data.note}</p>

          {/* live review queue — mentions the gate flagged, with context-sensitive governed actions */}
          <div className="mt-8">
            <h3 className="mb-2 text-sm font-semibold text-slate-200">Needs review <span className="text-slate-500">({review.data?.count ?? 0})</span></h3>
            {(review.data?.items?.length ?? 0) === 0 ? (
              <p className="text-sm text-slate-500">No live review items — every recent mention resolved cleanly.</p>
            ) : (
              <div className="space-y-2.5">
                {(review.data?.items ?? []).map((it, i) => {
                  const cid = String(it.candidate_id ?? "");
                  const cls = String(it.problem_class ?? "");
                  const etype = String(it.expected_role ?? it.entity_type ?? "").toUpperCase();
                  const interp = (it.evidence ?? {}) as Record<string, unknown>;
                  const parts = Array.isArray(interp.parts) ? (interp.parts as string[])
                    : Array.isArray((interp.compound as Record<string, unknown> | undefined)?.parts)
                      ? ((interp.compound as Record<string, unknown>).parts as string[]) : [];
                  const isCompound = String(interp.state ?? "") === "AMBIGUOUS_COMPOUND" || parts.length > 1;
                  return (
                    <Card key={cid || i}>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="truncate text-sm font-medium text-slate-100">{String(it.raw_name ?? "")}</span>
                            <span className="text-xs text-slate-500">source called it {etype.toLowerCase() || "?"}</span>
                            <Chip label={cls === "ROLE_CONFLICT" ? "Possible wrong type" : isCompound ? "Compound — several artists?" : cls === "REVIEW_REQUIRED" ? "Ambiguous — needs review" : cls} tone="amber" />
                          </div>
                          <div className="mt-0.5 text-xs text-slate-500">{String(it.source ?? "")}{it.reason ? ` · ${String(it.reason)}` : ""}</div>
                          {isCompound && parts.length > 0 && (
                            <div className="mt-1.5 text-xs text-slate-400">
                              Suggested interpretation:
                              <span className="ml-1">{parts.map((p, j) => (
                                <span key={j} className="mr-1 inline-block rounded border border-slate-700 px-1.5 py-0.5 text-slate-300">{p}</span>
                              ))}</span>
                              <div className="mt-1 text-[11px] text-slate-600">Re-run resolves each part independently — no combined canonical is ever created.</div>
                            </div>
                          )}
                        </div>
                        <div className="flex flex-wrap items-center gap-1.5">
                          {cls === "ROLE_CONFLICT" && (
                            <button disabled={!!busy} onClick={() => correctCandidate(cid, "CONFIRM_MULTI_ROLE", "Confirm dual-role")}
                              className="rounded-md border border-emerald-800/60 px-2.5 py-1 text-xs text-emerald-300 hover:bg-emerald-950/30 disabled:opacity-50">Confirm dual-role</button>
                          )}
                          <button disabled={!!busy} onClick={() => setPicking(picking === cid ? null : cid)}
                            className={`rounded-md border px-2.5 py-1 text-xs disabled:opacity-50 ${picking === cid ? "border-brand-500 text-brand-200" : "border-slate-700 text-slate-300 hover:bg-slate-800"}`}>Link to existing…</button>
                          <button disabled={!!busy} onClick={() => correctCandidate(cid, "REQUEUE_RESOLUTION", isCompound ? "Confirm split (re-resolve parts)" : "Re-run resolution")}
                            className="rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-50">{isCompound ? "Confirm split" : "Re-run"}</button>
                          <button disabled={!!busy} onClick={() => correctCandidate(cid, "REJECT_MATCH", "Reject match")}
                            className="rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-400 hover:bg-slate-800 disabled:opacity-50">Leave unresolved</button>
                        </div>
                      </div>
                      {picking === cid && (
                        <CanonicalPicker entityType={etype} busy={!!busy}
                          onPick={(canonicalId, name) => linkExisting(cid, canonicalId, name)}
                          onClose={() => setPicking(null)} />
                      )}
                    </Card>
                  );
                })}
              </div>
            )}
          </div>

          {/* live integrity-flow metrics */}
          {metrics.data && metrics.data.available !== false && (
            <div className="mt-8">
              <h3 className="mb-2 text-sm font-semibold text-slate-200">Quality metrics</h3>
              <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                <Card><div className="text-lg font-semibold text-slate-100">{metrics.data.mentions_processed ?? 0}</div><div className="text-xs text-slate-500">Mentions processed</div></Card>
                <Card><div className="text-lg font-semibold text-slate-100">{metrics.data.flow?.placeholder_suppressed ?? 0}</div><div className="text-xs text-slate-500">Placeholders suppressed</div></Card>
                <Card><div className="text-lg font-semibold text-slate-100">{metrics.data.flow?.compound_split ?? 0}</div><div className="text-xs text-slate-500">Compound split</div></Card>
                <Card><div className="text-lg font-semibold text-amber-300">{metrics.data.open_review_items ?? 0}</div><div className="text-xs text-slate-500">Open review items</div></Card>
              </div>
              <p className="mt-2 text-xs text-slate-600">Interpretation method: {metrics.data.interpretation_method ?? "deterministic"} · {metrics.data.operator_corrected ?? 0} operator-corrected.</p>
            </div>
          )}
        </>
      )}
    </Section>
  );
}

// §21 — bounded canonical search/select. Queries the authenticated BFF search (which reads the
// suppressing /entities endpoint, so quarantined/invalid canonicals never appear), filters to the
// mention's own type, and only ever emits an existing canonical id — no free-text id entry.
function CanonicalPicker({ entityType, busy, onPick, onClose }:
  { entityType: string; busy: boolean; onPick: (id: string, name: string) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Array<{ id: string; name: string; type: string }>>([]);
  const [searching, setSearching] = useState(false);
  useEffect(() => {
    if (q.trim().length < 2) { setResults([]); return; }
    let cancelled = false;
    const t = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await api.search(q);
        if (cancelled) return;
        const matches = (res.results.entities ?? [])
          .filter((e) => String(e.type ?? "").toUpperCase() === entityType)
          .map((e) => ({ id: String(e.canonical_entity_id), name: String(e.name ?? e.canonical_entity_id), type: String(e.type) }));
        setResults(matches);
      } catch { if (!cancelled) setResults([]); }
      finally { if (!cancelled) setSearching(false); }
    }, 220);
    return () => { cancelled = true; clearTimeout(t); };
  }, [q, entityType]);
  return (
    <div className="mt-3 rounded-lg border border-slate-800 bg-slate-950/60 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs text-slate-400">Link to an existing {entityType.toLowerCase() || "canonical"}</span>
        <button onClick={onClose} className="text-xs text-slate-500 hover:text-slate-300">Cancel</button>
      </div>
      <input autoFocus value={q} onChange={(e) => setQ(e.target.value)}
        placeholder={`Search ${entityType.toLowerCase() || "canonical"}s by name…`}
        className="w-full rounded-md border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-sm text-slate-100 placeholder:text-slate-600 focus:border-brand-500" />
      <div className="mt-2 max-h-52 space-y-1 overflow-y-auto">
        {searching && <div className="px-1 py-1 text-xs text-slate-600">Searching…</div>}
        {!searching && q.trim().length >= 2 && results.length === 0 && (
          <div className="px-1 py-1 text-xs text-slate-600">No matching {entityType.toLowerCase()} canonical found.</div>
        )}
        {results.map((r) => (
          <button key={r.id} disabled={busy} onClick={() => onPick(r.id, r.name)}
            className="flex w-full items-center justify-between gap-2 rounded-md border border-slate-800 px-2.5 py-1.5 text-left text-sm hover:border-brand-500 hover:bg-slate-800/50 disabled:opacity-50">
            <span className="truncate text-slate-200">{r.name}</span>
            <span className="shrink-0 font-mono text-[10px] text-slate-500">{r.id}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function describeIssue(r: DataQualityItem): string {
  if (r.problem_class === "PLACEHOLDER_ENTITY") return "This looks like an absence marker, not a real entity.";
  if (r.problem_class === "CROSS_TYPE_CONFLICT") {
    const other = (r.evidence?.also_exists_as as string[] | undefined)?.join(", ");
    return `The same identity also exists as ${other ?? "another type"} — may be a legitimate dual role, or a wrong type. Review needed.`;
  }
  if (r.problem_class === "COMPOUND_ENTITY") return "This name looks like several artists combined into one.";
  return r.proposed_action.replace(/_/g, " ");
}

// ---- helpers -----------------------------------------------------------------------------------
function shortName(id: string): string {
  const tail = id.split(":").pop() ?? id;
  return tail.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
function notCollected(kind: "youtube"): string { return kind === "youtube" ? "None yet" : "—"; }
function covNum(n: number | undefined, kind: "events"): string {
  return (n ?? 0) > 0 ? String(n) : (kind === "events" ? "None observed" : "—");
}
function mapYtState(c: ArtistCoverage): string | null {
  const s = c.identity?.youtube_identity?.state;
  if (s === "VERIFIED") return "RESOLVED";
  if (s === "PENDING") return "PENDING";
  return null;
}
function ytLabel(c: ArtistCoverage): string {
  const s = c.identity?.youtube_identity?.state;
  if (s === "VERIFIED") return "Verified";
  if (s === "PENDING") return "Finding identity";
  return "Not started";
}
function movementSentence(m: ArtistMovement | null): string {
  if (!m || m.available === false) return "Movement data temporarily unavailable";
  if ((m.videos_considered ?? 0) === 0) return "No YouTube content has been collected yet";
  const moving = (m.breakout_candidates?.length ?? 0) + (m.rising?.length ?? 0);
  return moving > 0 ? `${moving} piece(s) of content moving unusually` : "No unusual content movement currently detected";
}
