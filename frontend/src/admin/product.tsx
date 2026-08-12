import { useState } from "react";
import {
  api, type ArtistCoverage, type ArtistMovement, type CatalogArtist, type CovState,
  type MovementItem,
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
  const { data, loading, error } = useAsync(() => api.entityDetail("VENUE", id), [id]);
  if (loading) return <Loading />;
  if (error && !data) return <ErrorBox message={error} />;
  if (!data || data.available === false) return <Unavailable what="this venue" />;
  const events = (data.linked_events ?? []) as string[];
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-100">{data.canonical_name ?? shortName(id)}</h1>
        <div className="mt-2 flex gap-2"><Chip label="Venue" /><Chip label={`${events.length} event${events.length === 1 ? "" : "s"} observed`} tone="brand" /></div>
      </div>
      <Card title="Events observed here">
        {events.length === 0 ? <p className="text-sm text-slate-500">No events observed at this venue yet.</p> : (
          <ul className="space-y-1 text-sm">
            {events.slice(0, 50).map((e) => (
              <li key={e}><a href={`#/events/${encodeURIComponent(e)}`} className="text-slate-300 hover:text-brand-200">{shortName(e)}</a></li>
            ))}
          </ul>
        )}
      </Card>
      <details className="rounded-xl border border-slate-800 bg-slate-900/40 p-4 text-sm">
        <summary className="cursor-pointer text-slate-400">Evidence &amp; sources</summary>
        <div className="mt-2 text-xs text-slate-500">Canonical id: <span className="text-slate-300">{id}</span></div>
        <div className="mt-1 text-xs text-slate-500">Source listings: <span className="text-slate-300">{fmt((data.source_handles ?? []).length)}</span></div>
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
