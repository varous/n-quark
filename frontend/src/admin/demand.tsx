// Phase 5A.2 — Demand Intelligence inspection surface (read-only, local-only).
// Exposes the Phase 5A demand read models through the existing admin console. No mutation controls:
// no resolve / refresh / import / retry / scheduler actions are rendered anywhere in this file.
import { useState } from "react";
import { api, type ArtistDemand as ArtistDemandT, type EventDemand } from "./api";
import { Badge, Card, Empty, ErrorBox, Link, Loading, Stat, Table, Unavailable, useAsync, useDrawer, fmt } from "./ui";

// ---- helpers -----------------------------------------------------------------------------------
type Obj = Record<string, unknown>;
const g = (o: unknown, k: string): unknown => (o && typeof o === "object" ? (o as Obj)[k] : undefined);
const num = (v: unknown, d = 0): string =>
  typeof v === "number" ? (Number.isInteger(v) ? v.toLocaleString() : v.toFixed(d)) : "—";

// Neutral note for LEGITIMATE evidence states (INSUFFICIENT_HISTORY, ACCESS_UNAVAILABLE, empty).
// These are NOT errors, so they never use an alarm colour.
function Note({ children }: { children: React.ReactNode }) {
  return <div className="rounded border border-slate-700 bg-slate-900/60 p-3 text-xs text-slate-400">{children}</div>;
}
function Warn({ children }: { children: React.ReactNode }) {
  return <div className="rounded border border-amber-800 bg-amber-950/30 p-3 text-xs text-amber-300">{children}</div>;
}

// A single window-delta component (7d / 30d). Independent measure — never combined into a score.
function Delta({ d }: { d: unknown }) {
  const status = String(g(d, "status") ?? "");
  if (status !== "OK") return <Badge label="INSUFFICIENT_HISTORY" />;
  const delta = g(d, "delta");
  const vel = g(d, "velocity_per_day");
  const sign = typeof delta === "number" && delta > 0 ? "+" : "";
  return (
    <span className="tabular-nums text-slate-200">{sign}{num(delta)}
      <span className="ml-2 text-xs text-slate-500">{num(vel, 4)}/day</span></span>
  );
}

// REAL / MOCK / UNKNOWN — MOCK must be impossible to miss in a production-connected session.
function ModeBadge({ mode }: { mode: string | null | undefined }) {
  const m = (mode ?? "UNKNOWN").toUpperCase();
  if (m === "REAL") return <span className="rounded border border-emerald-500/40 bg-emerald-500/15 px-2 py-0.5 text-xs font-semibold text-emerald-300">REAL</span>;
  if (m === "MOCK") return <span className="rounded border border-rose-500/60 bg-rose-500/25 px-2 py-0.5 text-xs font-bold text-rose-200">MOCK — not real provider data</span>;
  return <span className="rounded border border-slate-600/50 bg-slate-600/20 px-2 py-0.5 text-xs font-semibold text-slate-300">UNKNOWN</span>;
}

// ================================================================================================
// Operations / diagnostics screen
// ================================================================================================
export function DemandIntelligence() {
  const { data, loading, error } = useAsync(() => api.demandOverview(), []);
  if (loading) return <Loading label="Loading demand intelligence" />;
  if (error) return <ErrorBox message={error} />;
  if (!data) return <Empty message="No data." />;
  if (!data.available) {
    return (
      <div className="space-y-4">
        <IntroLine />
        <Unavailable />
        <Note>artist-intelligence-service is unreachable or disabled. The rest of the console is unaffected.</Note>
      </div>
    );
  }
  const cov = (data.coverage ?? {}) as Obj;
  const yt = ((data.provider_health?.providers?.youtube) ?? {}) as Obj;
  const ytMode = (g(yt, "mode") as Obj) ?? {};
  const trends = ((data.provider_health?.providers?.google_trends) ?? {}) as Obj;
  const sched = (data.scheduler ?? {}) as Obj;
  const ytStatus = (cov.youtube_identity_status ?? {}) as Obj;
  const quotaToday = (g(yt, "quota_today") ?? {}) as Obj;
  const trendsSplit = (cov.trends_api_vs_imported ?? {}) as Obj;

  return (
    <div className="space-y-6">
      <IntroLine />

      {/* Coverage */}
      <Card title="Coverage" right={<Badge label="Observed" />}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
          <Stat label="Canonical artists" value={fmt(cov.canonical_artists)} />
          <Stat label="With YouTube identity" value={fmt(ytStatus.artists_with_youtube_identity)} />
          <Stat label="Resolved" value={fmt(ytStatus.resolved)} />
          <Stat label="Ambiguous" value={fmt(ytStatus.ambiguous)} />
          <Stat label="Unresolved" value={fmt(ytStatus.unresolved)} />
          <Stat label="With observations" value={fmt(cov.artists_with_demand_observation)} />
          <Stat label="Regions covered" value={fmt(cov.regions_covered)} />
          <Stat label="Stale demand artists" value={fmt(cov.stale_demand_artists)} />
        </div>
        {cov.disclaimer ? <div className="mt-3"><Note>{String(cov.disclaimer)}</Note></div> : null}
      </Card>

      {/* YouTube provider */}
      <Card title="YouTube provider" right={<ModeBadge mode={String(g(ytMode, "mode") ?? "")} />}>
        {String(g(ytMode, "mode") ?? "").toUpperCase() === "MOCK" && (
          <div className="mb-3"><Warn>YouTube is in <strong>MOCK</strong> mode — values are illustrative, not real provider data.
            In a production-connected session this indicates the API key is not set on signal-service.</Warn></div>
        )}
        {String(g(ytMode, "mode") ?? "").toUpperCase() === "UNKNOWN" && (
          <div className="mb-3"><Note>Provider mode is UNKNOWN — signal-service health could not be read. Mode is never assumed REAL.</Note></div>
        )}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
          <Stat label="Enabled" value={String(yt.enabled ?? "—")} />
          <Stat label="Search enabled" value={String(yt.search_enabled ?? "—")} />
          <Stat label="Acquisition" value={String(yt.acquisition ?? "—")} />
          <Stat label="Requests today" value={fmt(quotaToday.requests)} />
          <Stat label="Search requests" value={fmt(quotaToday.search_requests)} />
          <Stat label="Search units" value={fmt(quotaToday.search_quota_units)} />
          <Stat label="Read units" value={fmt(quotaToday.non_search_quota_units)} />
          <Stat label="Successful calls" value={fmt(quotaToday.successful_calls)} />
          <Stat label="Failed calls" value={fmt(quotaToday.failed_calls)} />
          <Stat label="Quota errors" value={fmt(quotaToday.quota_errors)} />
        </div>
      </Card>

      {/* Scheduler */}
      <Card title="Refresh scheduler (read-only)"
        right={<Badge label={sched.enabled ? "Observed" : "Unknown"} />}>
        <p className="mb-3 text-xs text-slate-500">Scheduler state is inspection-only — no scheduler actions are exposed.</p>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
          <Stat label="Enabled" value={String(sched.enabled ?? "—")} />
          <Stat label="Queued / due" value={fmt(sched.queued_due)} />
          <Stat label="Running (leased)" value={fmt(sched.running_leased)} />
          <Stat label="Retrying" value={fmt(sched.retrying)} />
          <Stat label="Succeeded" value={fmt(sched.succeeded)} />
          <Stat label="Terminal failures" value={fmt(sched.terminal_failures)} />
        </div>
        <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-sm md:grid-cols-2">
          <div><dt className="text-xs uppercase text-slate-500">Latest successful refresh</dt><dd className="text-slate-200">{fmt(sched.latest_successful_refresh)}</dd></div>
          <div><dt className="text-xs uppercase text-slate-500">Next scheduled refresh</dt><dd className="text-slate-200">{fmt(sched.next_scheduled_refresh)}</dd></div>
        </dl>
      </Card>

      {/* Google Trends */}
      <Card title="Google Trends">
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <div className="mb-1 text-xs uppercase text-slate-500">Official API</div>
            <Badge label={String(g(trends, "status") ?? "—")} />
            <span className="ml-2 text-xs text-slate-500">{String(g(trends, "mode") ?? "")}</span>
            {String(g(trends, "status") ?? "") === "ACCESS_UNAVAILABLE" && (
              <div className="mt-2"><Note>Official API reports <strong>ACCESS_UNAVAILABLE</strong> (no alpha credentials). This is expected — not an error. IMPORT is the interim path.</Note></div>
            )}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Import enabled" value={String(g(trends, "import_enabled") ?? "—")} />
            <Stat label="Imported observations" value={fmt(trendsSplit.imported_provider_export)} />
            <Stat label="Official observations" value={fmt(trendsSplit.official_api)} />
            <Stat label="Trends mappings" value={fmt(cov.trends_mappings)} />
          </div>
        </div>
        <div className="mt-3"><Note>Google Trends values are <strong>relative search interest</strong> (0–100 within a pull), not absolute search volume. Independently normalised exports are never compared on one scale.</Note></div>
      </Card>

      <p className="text-xs text-slate-500">Inspect a specific artist from <Link to="/entities?entity_type=ARTIST">Entities → an artist</Link> (Demand Intelligence section), or open <span className="font-mono">#/demand/artists/&lt;canonical-artist-id&gt;</span>.</p>
    </div>
  );
}

function IntroLine() {
  return (
    <p className="text-sm text-slate-400">Public demand intelligence — YouTube identity + observations, deterministic momentum,
      Google Trends, and observed-supply context. Read-only; demand and supply meet only through the canonical artist id, with no composite score and no causal claim.</p>
  );
}

// ================================================================================================
// Full per-artist demand view (used at #/demand/artists/:id AND embedded in the ARTIST entity page)
// ================================================================================================
export function ArtistDemand({ id, embedded }: { id: string; embedded?: boolean }) {
  const { data, loading, error } = useAsync(() => api.demandArtist(id), [id]);
  if (loading) return <Loading label="Loading demand" />;
  if (error) return <ErrorBox message={error} />;
  if (!data) return <Empty message="No demand data." />;
  return (
    <div className="space-y-4">
      {!embedded && <div className="text-xs text-slate-500">Demand · <span className="font-mono">{id}</span></div>}
      {!data.available && <Unavailable />}
      <IdentitiesCard d={data} />
      <YouTubeCard d={data} />
      <MomentumCard d={data} />
      <TrendsCard d={data} />
      <GeographyCard d={data} />
      <SupplyDemandCard d={data} />
      <ObservationsCard id={id} />
    </div>
  );
}

function IdentitiesCard({ d }: { d: ArtistDemandT }) {
  const { open } = useDrawer();
  const rows = d.external_identities ?? [];
  return (
    <Card title="External platform identities">
      {rows.length === 0 ? <Empty message="No external identities resolved yet." /> : (
        <Table rows={rows} columns={[
          { key: "provider", header: "Provider" },
          { key: "identity_type", header: "Type" },
          { key: "provider_id", header: "Provider id", render: (r) => <span className="font-mono text-xs">{r.provider_id}</span> },
          { key: "display_name", header: "Name", render: (r) => fmt(r.display_name) },
          { key: "status", header: "Status", render: (r) => <Badge label={r.status} /> },
          { key: "verification", header: "Verification", render: (r) => (
            r.status === "RESOLVED" && r.last_verified_at
              ? <span className="text-xs text-emerald-300">provider-verified · {r.last_verified_at}</span>
              : r.last_verified_at
                ? <span className="text-xs text-slate-400">last valid: {r.last_verified_at}</span>
                : <span className="text-xs text-slate-500">not verified</span>
          ) },
          { key: "confidence", header: "Confidence", render: (r) => (
            <span className="tabular-nums text-slate-300" title="resolution match confidence — NOT popularity or artist quality">{r.confidence?.toFixed?.(2) ?? fmt(r.confidence)}</span>
          ) },
          { key: "reason", header: "Reason", render: (r) => fmt(r.invalidation_reason ?? r.reason) },
          { key: "url", header: "", render: (r) => (
            <span className="flex gap-2">
              {r.canonical_url ? <a href={r.canonical_url} target="_blank" rel="noreferrer" className="text-xs text-sky-400 hover:underline">open</a> : null}
              <button className="text-xs text-sky-400" onClick={() => open("Identity provenance", r)}>prov</button>
            </span>
          ) },
        ]} />
      )}
      <p className="mt-3 text-xs text-slate-500">Confidence is the identity-resolution match strength. It is <strong>not</strong> popularity, reach, or artist quality.
        A YouTube channel is <strong>RESOLVED</strong> only after an authoritative <span className="font-mono">channels.list</span> verification; search is discovery only.</p>
    </Card>
  );
}

function YouTubeCard({ d }: { d: ArtistDemandT }) {
  const yt = d.youtube;
  if (!yt) return <Card title="YouTube"><Empty message="No YouTube data." /></Card>;
  const snap = (g(yt, "current_snapshot") ?? {}) as Obj;
  const deltas = (g(yt, "deltas") ?? {}) as Obj;
  const fresh = (g(yt, "data_freshness") ?? {}) as Obj;
  const subs = g(snap, "subscribers") as Obj | null;
  return (
    <Card title="YouTube — current state" right={<FreshnessBadge f={fresh} />}>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Subscribers" value={subs ? num(g(subs, "value")) : "—"}
          hint="provider-reported, rounded — treat as approximate" />
        <Stat label="Channel views" value={num(g(g(snap, "channel_views"), "value"))} />
        <Stat label="Video count" value={num(g(g(snap, "video_count"), "value"))} />
        <Stat label="Latest observation" value={subs ? String(g(subs, "observed_at") ?? "—").slice(0, 16) : "—"} />
      </div>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead><tr className="border-b border-slate-800 text-xs uppercase text-slate-400">
            <th className="px-3 py-2">Metric</th><th className="px-3 py-2">7-day</th><th className="px-3 py-2">30-day</th></tr></thead>
          <tbody>
            <tr className="border-b border-slate-800/60"><td className="px-3 py-2 text-slate-400">Channel views</td>
              <td className="px-3 py-2"><Delta d={deltas.channel_view_delta_7d} /></td>
              <td className="px-3 py-2"><Delta d={deltas.channel_view_delta_30d} /></td></tr>
            <tr className="border-b border-slate-800/60"><td className="px-3 py-2 text-slate-400">Subscribers</td>
              <td className="px-3 py-2"><Delta d={deltas.subscriber_delta_7d} /></td>
              <td className="px-3 py-2"><Delta d={deltas.subscriber_delta_30d} /></td></tr>
          </tbody>
        </table>
      </div>
      <div className="mt-3">
        <RecentVideos rv={g(yt, "recent_video_activity")} />
      </div>
      <p className="mt-3 text-xs text-slate-500">Subscriber counts are provider-reported and rounded; deltas between rounded values can be imprecise and are not shown as exact changes.</p>
    </Card>
  );
}

function RecentVideos({ rv }: { rv: unknown }) {
  const status = String(g(rv, "status") ?? "");
  if (status !== "OK") return <Note>Recent-video velocity: <Badge label="INSUFFICIENT_HISTORY" /> <span className="ml-1">{String(g(rv, "reason") ?? "")}</span></Note>;
  return (
    <div className="flex flex-wrap gap-4 text-sm text-slate-300">
      <span>Videos measured: <span className="tabular-nums">{fmt(g(rv, "videos_measured"))}</span></span>
      <span>Mean view velocity: <span className="tabular-nums">{num(g(rv, "mean_view_velocity_per_day"), 2)}/day</span></span>
    </div>
  );
}

function FreshnessBadge({ f }: { f: Obj }) {
  const status = String(g(f, "status") ?? "");
  if (status === "NO_DATA") return <Badge label="NO_DATA" />;
  const stale = g(f, "stale") === true;
  return <span className={`rounded border px-1.5 py-0.5 text-xs ${stale ? "border-orange-500/30 text-orange-300" : "border-emerald-500/30 text-emerald-300"}`}>
    {stale ? "Stale" : "Fresh"} · {num(g(f, "age_hours"), 1)}h</span>;
}

function MomentumCard({ d }: { d: ArtistDemandT }) {
  const m = d.momentum;
  if (!m) return <Card title="Momentum"><Unavailable /></Card>;
  const c = (m.components ?? {}) as Obj;
  const cov = (m.coverage ?? {}) as Obj;
  const upload = (c.youtube_upload_activity ?? {}) as Obj;
  const eng = (c.youtube_recent_video_engagement_ratio ?? {}) as Obj;
  const cw = (c.youtube_channel_view_velocity ?? {}) as Obj;
  const sc = (c.youtube_subscriber_change ?? {}) as Obj;
  const gi = (c.google_search_interest_change ?? {}) as Obj;
  return (
    <Card title="Momentum — independent components (not a score)"
      right={<span className="flex gap-2 text-xs">
        <span className={`rounded border px-1.5 py-0.5 ${cov.has_7d_history ? "border-emerald-500/30 text-emerald-300" : "border-slate-600/40 text-slate-500"}`}>7d history</span>
        <span className={`rounded border px-1.5 py-0.5 ${cov.has_30d_history ? "border-emerald-500/30 text-emerald-300" : "border-slate-600/40 text-slate-500"}`}>30d history</span></span>}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead><tr className="border-b border-slate-800 text-xs uppercase text-slate-400">
            <th className="px-3 py-2">Component</th><th className="px-3 py-2">7-day</th><th className="px-3 py-2">30-day</th></tr></thead>
          <tbody>
            <MomentumDeltaRow label="Channel view velocity" d7={g(cw, "delta_7d")} d30={g(cw, "delta_30d")} />
            <MomentumDeltaRow label="Subscriber change" d7={g(sc, "delta_7d")} d30={g(sc, "delta_30d")} />
            <MomentumDeltaRow label="Google search interest change" d7={g(gi, "delta_7d")} d30={g(gi, "delta_30d")} />
          </tbody>
        </table>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-3">
        <div className="rounded border border-slate-800 p-3">
          <div className="text-xs uppercase text-slate-500">Recent video velocity</div>
          <RecentVideos rv={c.youtube_recent_video_velocity} />
        </div>
        <div className="rounded border border-slate-800 p-3">
          <div className="mb-1 text-xs uppercase text-slate-500">Upload activity</div>
          {String(g(upload, "status") ?? "") === "OK"
            ? <div className="text-sm text-slate-300">{fmt(g(upload, "recent_upload_count_30d"))} in 30d · {num(g(upload, "uploads_per_week"), 2)}/wk</div>
            : <Badge label="INSUFFICIENT_HISTORY" />}
        </div>
        <div className="rounded border border-slate-800 p-3">
          <div className="mb-1 text-xs uppercase text-slate-500">Engagement ratio (newest video)</div>
          {String(g(eng, "status") ?? "") === "OK"
            ? <div className="text-sm text-slate-300 tabular-nums">{num(g(eng, "engagement_ratio"), 5)}</div>
            : <Badge label="INSUFFICIENT_HISTORY" />}
        </div>
      </div>
      <p className="mt-3 text-xs text-slate-500">Each component is an independent measure of observed activity. They are never combined into a momentum / popularity / booking score.</p>
    </Card>
  );
}

function MomentumDeltaRow({ label, d7, d30 }: { label: string; d7: unknown; d30: unknown }) {
  return (
    <tr className="border-b border-slate-800/60">
      <td className="px-3 py-2 text-slate-400">{label}</td>
      <td className="px-3 py-2"><Delta d={d7} /></td>
      <td className="px-3 py-2"><Delta d={d30} /></td>
    </tr>
  );
}

function TrendsCard({ d }: { d: ArtistDemandT }) {
  const t = d.google_trends;
  if (!t) return <Card title="Google Trends"><Empty message="No Trends data." /></Card>;
  const cur = g(t, "current_interest") as Obj | null;
  const regions = (g(t, "regional_distribution") as Array<Obj>) ?? [];
  const norm = (g(t, "normalization_context") ?? {}) as Obj;
  const fresh = (g(t, "data_freshness") ?? {}) as Obj;
  return (
    <Card title="Google Trends — relative search interest" right={<FreshnessBadge f={fresh} />}>
      <div className="mb-3"><Note>Values are <strong>relative search interest</strong> (0–100 within a single pull), not absolute volume. Different exports are independently normalised and never compared on one scale.</Note></div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Current interest" value={cur ? num(g(cur, "value")) : "—"} />
        <Stat label="History points" value={fmt(g(t, "historical_interest_points"))} />
        <Stat label="Provider mode" value={String(g(norm, "provider_mode") ?? "—")} />
        <Stat label="Geo" value={String(g(norm, "geo") ?? "—")} />
      </div>
      {regions.length > 0 && (
        <div className="mt-4">
          <div className="mb-1 text-xs uppercase text-slate-500">Regional distribution</div>
          <Table rows={regions} columns={[
            { key: "region", header: "Region", render: (r) => fmt(r.region) },
            { key: "scope_id", header: "ISO", render: (r) => <span className="font-mono text-xs">{fmt(r.scope_id)}</span> },
            { key: "interest", header: "Interest (relative)", render: (r) => <span className="tabular-nums">{num(r.interest)}</span> },
          ]} />
        </div>
      )}
      {(norm.normalization || norm.comparison_window || norm.time_range) ? (
        <div className="mt-3 text-xs text-slate-500">Normalisation: {fmt(norm.normalization)} · window {fmt(norm.comparison_window)} · range {fmt(norm.time_range)} · basis {fmt(norm.identity_basis)}</div>
      ) : null}
    </Card>
  );
}

function GeographyCard({ d }: { d: ArtistDemandT }) {
  const geo = d.geography;
  const [sortKey, setSortKey] = useState<"search_interest" | "observed_supply_count">("search_interest");
  if (!geo) return <Card title="Geography"><Unavailable /></Card>;
  const regions = [...((geo.regions as Array<Obj>) ?? [])].sort((a, b) => Number(b[sortKey] ?? -1) - Number(a[sortKey] ?? -1));
  return (
    <Card title="Geography — demand × observed supply" right={
      <select className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs" value={sortKey} onChange={(e) => setSortKey(e.target.value as typeof sortKey)}>
        <option value="search_interest">Sort by interest</option>
        <option value="observed_supply_count">Sort by observed supply</option>
      </select>}>
      {regions.length === 0 ? <Empty message="No regional demand or supply observed yet." /> : (
        <Table rows={regions} columns={[
          { key: "region_label", header: "Region", render: (r) => fmt(r.region_label ?? r.region_slug) },
          { key: "search_interest", header: "Trends interest", render: (r) => <span className="tabular-nums">{num(r.search_interest)}</span> },
          { key: "observed_supply_count", header: "Observed events", render: (r) => <span className="tabular-nums">{fmt(r.observed_supply_count)}</span> },
          { key: "recent_live_activity", header: "Recent", render: (r) => fmt(r.recent_live_activity) },
          { key: "upcoming_live_activity", header: "Upcoming", render: (r) => fmt(r.upcoming_live_activity) },
          { key: "evidence_status", header: "Evidence", render: (r) => <Badge label={String(r.evidence_status ?? "—")} /> },
          { key: "label", header: "Label", render: (r) => <span className="text-xs text-slate-400">{fmt(r.label)}</span> },
        ]} />
      )}
      <p className="mt-3 text-xs text-slate-500">Labels are relative to this artist's analysed cohort; the underlying values are always shown. Provider geography granularity (state-level) is preserved — no city precision is invented.</p>
    </Card>
  );
}

function SupplyDemandCard({ d }: { d: ArtistDemandT }) {
  const s = d.observed_live_supply;
  if (!s) return <Card title="Observed live supply"><Unavailable /></Card>;
  return (
    <Card title="Observed live supply" right={<Badge label="Observed" />}>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
        <Stat label="Observed events" value={fmt(g(s, "event_count"))} />
        <Stat label="Upcoming" value={fmt(g(s, "upcoming_events"))} />
        <Stat label="Recent" value={fmt(g(s, "recent_events"))} />
        <Stat label="Cities" value={fmt(g(s, "cities"))} />
        <Stat label="Venues" value={fmt(g(s, "venues"))} />
        <Stat label="Organizers" value={fmt(g(s, "organizers"))} />
      </div>
      <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-sm md:grid-cols-2">
        <div><dt className="text-xs uppercase text-slate-500">First observed</dt><dd className="text-slate-200">{fmt(g(s, "first_observed"))}</dd></div>
        <div><dt className="text-xs uppercase text-slate-500">Last observed</dt><dd className="text-slate-200">{fmt(g(s, "last_observed"))}</dd></div>
      </dl>
      <p className="mt-3 text-xs text-slate-500">This is <strong>observed live supply</strong> from the captured inventory — not total live activity. Demand and supply are juxtaposed via the canonical artist id, never fused into one score.</p>
    </Card>
  );
}

function ObservationsCard({ id }: { id: string }) {
  const [provider, setProvider] = useState("");
  const { data, loading, error } = useAsync(() => api.demandArtistObservations(id, { provider, limit: 100 }), [id, provider]);
  return (
    <Card title="Observation history (bounded)" right={
      <select className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs" value={provider} onChange={(e) => setProvider(e.target.value)}>
        <option value="">All providers</option><option value="YOUTUBE">YOUTUBE</option><option value="GOOGLE_TRENDS">GOOGLE_TRENDS</option>
      </select>}>
      {error && <ErrorBox message={error} />}
      {data && !data.available && <Unavailable />}
      {loading ? <Loading /> : (
        <>
          <div className="mb-2 text-xs text-slate-500">{data?.total ?? 0} observations (showing up to {data?.limit ?? 100})</div>
          <Table rows={data?.items ?? []} empty="No observations." columns={[
            { key: "observed_at", header: "Observed", render: (r) => <span className="text-xs">{String(r.observed_at ?? "").slice(0, 16)}</span> },
            { key: "provider", header: "Provider" },
            { key: "metric", header: "Metric", render: (r) => <span className="font-mono text-xs">{fmt(r.metric)}</span> },
            { key: "value_numeric", header: "Value", render: (r) => <span className="tabular-nums">{fmt(r.value_numeric ?? r.value_text)}</span> },
            { key: "scope_label", header: "Scope", render: (r) => fmt(r.scope_label ?? r.scope_type) },
            { key: "evidence_status", header: "Evidence", render: (r) => <Badge label={String(r.evidence_status ?? "")} /> },
          ]} />
        </>
      )}
    </Card>
  );
}

// ================================================================================================
// Compact section embedded in the ARTIST entity detail page
// ================================================================================================
export function DemandSection({ artistId }: { artistId: string }) {
  return (
    <Card title="Demand Intelligence"
      right={<Link to={`/demand/artists/${encodeURIComponent(artistId)}`} className="rounded border border-slate-700 px-2 py-0.5 text-xs text-sky-400 hover:bg-slate-800">Full demand view</Link>}>
      <div className="-mx-4 -mb-4">
        <div className="px-4 pb-4"><ArtistDemand id={artistId} embedded /></div>
      </div>
    </Card>
  );
}

// ================================================================================================
// Event demand context (embedded as a tab in event detail)
// ================================================================================================
export function EventDemandContext({ eventId }: { eventId: string }) {
  const { data, loading, error } = useAsync(() => api.demandEvent(eventId), [eventId]);
  if (loading) return <Loading label="Loading demand context" />;
  if (error) return <ErrorBox message={error} />;
  if (!data) return <Empty message="No demand context." />;
  if (!data.available) return <Unavailable />;
  if (data.resolved_artist_count === 0) return <Note>No resolved artists on this event — no demand context to show.</Note>;
  return (
    <div className="space-y-4">
      <Note>Temporal co-movement only. No causal inference between demand signals and event state.</Note>
      {data.capped && <Warn>Showing the first {data.artists.length} resolved artists (bounded).</Warn>}
      {data.artists.map((a) => <EventArtistCard key={a.canonical_artist_id} a={a} />)}
    </div>
  );
}

function EventArtistCard({ a }: { a: EventDemand["artists"][number] }) {
  const m = (a.momentum ?? {}) as Obj;
  const comp = (g(m, "components") ?? {}) as Obj;
  const cw = (comp.youtube_channel_view_velocity ?? {}) as Obj;
  const sc = (comp.youtube_subscriber_change ?? {}) as Obj;
  const yt = (a.youtube ?? {}) as Obj;
  const fresh = (g(yt, "data_freshness") ?? {}) as Obj;
  const er = a.event_response;
  return (
    <Card title={a.raw_name ?? a.canonical_artist_id}
      right={<Link to={`/demand/artists/${encodeURIComponent(a.canonical_artist_id)}`} className="text-xs text-sky-400 hover:underline">demand →</Link>}>
      {!a.available ? <Unavailable /> : (
        <>
          <div className="flex flex-wrap items-center gap-4 text-sm">
            <span className="text-slate-400">YouTube freshness:</span> <FreshnessBadge f={fresh} />
            <span className="text-slate-400">Views 7d:</span> <Delta d={cw.delta_7d} />
            <span className="text-slate-400">Subs 7d:</span> <Delta d={sc.delta_7d} />
            <span className="text-slate-400">Views 30d:</span> <Delta d={cw.delta_30d} />
          </div>
          <div className="mt-3"><EventResponse er={er} /></div>
        </>
      )}
    </Card>
  );
}

function EventResponse({ er }: { er: unknown }) {
  const status = String(g(er, "status") ?? "");
  if (!er || status === "EVENT_NOT_FOUND") return <Note>Event not found in the graph — no response timeline.</Note>;
  if (status === "INSUFFICIENT_HISTORY") return <div className="flex items-center gap-2"><span className="text-sm text-slate-400">Event-response:</span> <Badge label="INSUFFICIENT_HISTORY" /></div>;
  const rows = (g(er, "timeline") as Array<Obj>) ?? [];
  return (
    <div>
      <div className="mb-1 text-xs uppercase text-slate-500">Event-relative timeline (T-60 → T+7) · co-movement only</div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead><tr className="border-b border-slate-800 text-xs uppercase text-slate-400">
            <th className="px-3 py-2">Offset</th><th className="px-3 py-2">Date</th>
            <th className="px-3 py-2">Trends interest</th><th className="px-3 py-2">YT view velocity</th>
            <th className="px-3 py-2">Transitions</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-slate-800/60">
                <td className="px-3 py-2 font-mono text-xs">T{Number(r.offset_days) >= 0 ? "+" : ""}{fmt(r.offset_days)}</td>
                <td className="px-3 py-2 text-xs text-slate-400">{String(r.date ?? "").slice(0, 10)}</td>
                <td className="px-3 py-2 tabular-nums">{num(r.google_search_interest)}</td>
                <td className="px-3 py-2 tabular-nums">{num(r.youtube_view_velocity_per_day, 2)}</td>
                <td className="px-3 py-2 text-xs text-slate-400">{((r.ticket_price_creative_transitions as unknown[]) ?? []).length || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ================================================================================================
// Compact dashboard summary card
// ================================================================================================
export function DemandSummaryCard() {
  const { data, loading, error } = useAsync(() => api.demandSummary(), []);
  if (loading) return <Card title="Demand intelligence"><Loading /></Card>;
  if (error || !data) return null;  // demand layer optional — never break the dashboard
  if (!data.available) return (
    <Card title="Demand intelligence" right={<Link to="/demand">open</Link>}>
      <Note>Demand layer unavailable or disabled.</Note>
    </Card>
  );
  return (
    <Card title="Demand intelligence" right={<Link to="/demand">open</Link>}>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
        <Stat label="Resolved YT artists" value={fmt(data.resolved_youtube_artists)} />
        <Stat label="With observations" value={fmt(data.artists_with_demand_observation)} />
        <Stat label="Stale" value={fmt(data.stale_demand_artists)} />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-slate-400">
        <span>YouTube: <ModeBadge mode={data.youtube_mode} /></span>
        <span>Scheduler: <Badge label={data.scheduler_enabled ? "Observed" : "Unknown"} /></span>
        {typeof data.scheduler_terminal_failures === "number" && data.scheduler_terminal_failures > 0 &&
          <span className="text-amber-300">{data.scheduler_terminal_failures} terminal failures</span>}
      </div>
    </Card>
  );
}
