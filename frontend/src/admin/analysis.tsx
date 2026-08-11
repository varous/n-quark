import { api, type Dashboard, type DemandOverview, type SystemHealth } from "./api";
import { Bar, Card, ErrorBox, KeyValue, Link, Loading, num, Section, StatTile, Table, Tag, useAsync } from "./ui";

// Bounded, deterministic analysis over the accrued n-quark data. Everything here is a transparent
// aggregation of existing read models — no scoring, no prediction, no causal claim. Each panel states
// what it measures and what it does NOT. Supply and demand are shown side by side; they are never fused
// into a single number.

type Bundle = { dash: Dashboard; health: SystemHealth; demand: DemandOverview };
type GapRow = { gap: string; n: number; to: string };
const n = (v: unknown): number => (typeof v === "number" ? v : Number(v) || 0);

export function Analysis() {
  const { data, loading, error } = useAsync<Bundle>(async () => {
    const [dash, health, demand] = await Promise.all([api.dashboard(), api.systemHealth(), api.demandOverview()]);
    return { dash, health, demand };
  }, []);

  if (loading) return <Loading label="Computing analysis" />;
  if (error || !data) return <ErrorBox message={error ?? "No data."} />;

  const { dash, health, demand } = data;
  const c = dash.cards ?? {};
  const universe = (demand.artist_universe ?? {}) as Record<string, any>;
  const india = (universe.india_market_presence ?? {}) as Record<string, number>;
  const bySource = (universe.discovery_contribution_by_source ?? {}) as Record<string, number>;
  const recon = (universe.canonical_reconciliation ?? {}) as Record<string, any>;
  const ytStatus = (universe.youtube_identity ?? {}) as Record<string, number>;
  const dq = health.data_quality ?? {};

  // --- collection integrity, as transparent components (NOT a single opaque score) ---
  const artistRate = n(c.resolved_artist_rate);
  const components = [
    { label: "Artist resolution", pct: Math.round(artistRate * 100), tone: artistRate >= 0.9 ? "good" : "warn",
      def: "Share of artist mentions resolved to a canonical entity." },
    { label: "Geography present", pct: pctOf(dq.geography_present, dq.missing_geography), tone: "neutral",
      def: "Tracked events with a resolved city/region vs. missing." },
    { label: "Freshness", pct: freshnessPct(c), tone: n(c.stale_tracked_events) === 0 ? "good" : "warn",
      def: "Tracked events captured within the freshness window (not stale)." },
    { label: "Capture success", pct: capturePct(c), tone: n(c.failed_capture_jobs) === 0 ? "good" : "bad",
      def: "Capture jobs that are not currently failing." },
  ];

  return (
    <div>
      <Section title="Analysis"
        subtitle="Deterministic aggregations over what n-quark has actually observed. No predictions, no popularity or value scores, no causal inference — supply and demand are shown side by side, never fused.">
        <div className="mb-5 rounded-xl border border-slate-800 bg-slate-900/40 p-3 text-xs text-slate-400">
          {String(universe.disclaimer ?? "Coverage reflects observed inventory and demand only — not the complete market.")}
        </div>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatTile label="Canonical artists" value={num(recon.canonical_registry_artists ?? (health.canonical_counts ?? {}).ARTIST)} tone="brand" caption="authoritative registry" />
          <StatTile label="Graph nodes (artist)" value={num(recon.graph_artist_nodes)} caption="representation" />
          <StatTile label="Cross-source entities" value={num(c.cross_source_entities)} caption="shared across sources" />
          <StatTile label="Orphan demand refs" value={num(recon.orphan_demand_artist_references)} tone={n(recon.orphan_demand_artist_references) ? "warn" : "good"} caption="audited, never rewritten" />
        </div>
      </Section>

      <div className="grid gap-5 lg:grid-cols-2">
        <Section title="Collection integrity">
          <Card>
            <p className="mb-3 text-xs text-slate-500">Four independent components — shown separately, deliberately not combined into one score.</p>
            <div className="space-y-3">
              {components.map((cp) => (
                <div key={cp.label}>
                  <Bar label={cp.label} value={cp.pct} tone={cp.tone} />
                  <div className="mt-0.5 text-[11px] text-slate-500">{cp.def}</div>
                </div>
              ))}
            </div>
          </Card>
        </Section>

        <Section title="Supply × Demand" subtitle="Two independent evidence systems, side by side.">
          <div className="grid grid-cols-2 gap-3">
            <Card title="Observed supply">
              <KeyValue items={[
                ["Tracked events", num(c.active_tracked_events)],
                ["Captures", num(c.captures_total)],
                ["State transitions", num(c.transitions_total)],
                ["Confirmed live (IN)", num(india.confirmed_live_india)],
              ]} />
            </Card>
            <Card title="Observed demand">
              <KeyValue items={[
                ["YouTube identities", num(ytStatus.RESOLVED ?? universe.artists_with_demand_observation)],
                ["Demand-observed (IN)", num(india.india_demand_observed)],
                ["Market candidate (IN)", num(india.india_market_candidate)],
                ["Videos tracked", num((universe.videos ?? {}).total ?? (universe.videos ?? {}).videos)],
              ]} />
            </Card>
          </div>
          <p className="mt-2 px-1 text-[11px] text-slate-500">These are never merged into a single index; an artist can have supply without demand evidence and vice-versa.</p>
        </Section>

        <Section title="Discovery contribution" subtitle="Where the artist universe comes from.">
          <Card>
            {Object.keys(bySource).length === 0 ? <div className="text-sm text-slate-500">No discovery data yet.</div> : (
              <div className="space-y-3">
                {Object.entries(bySource).sort((a, b) => n(b[1]) - n(a[1])).map(([src, v]) => (
                  <Bar key={src} label={`${src} · ${num(v)}`} value={n(v)} max={Math.max(...Object.values(bySource).map(n))} tone="brand" />
                ))}
                <div className="flex items-center gap-2 pt-1 text-[11px] text-slate-500">
                  <Tag>multi-source: {num(universe.multi_source_artists)}</Tag>
                  <span>artists discovered by more than one surface.</span>
                </div>
              </div>
            )}
          </Card>
        </Section>

        <Section title="Coverage gaps & backlog" subtitle="Deterministic, actionable — no inference beyond counting.">
          <Card>
            <Table<GapRow> rows={[
              { gap: "Ambiguous entity candidates", n: n(c.ambiguous_entity_candidates), to: "/resolution" },
              { gap: "Unresolved entity candidates", n: n(c.unresolved_entity_candidates), to: "/resolution" },
              { gap: "Identity-resolution queue depth", n: n(universe.identity_resolution_queue_depth), to: "/demand" },
              { gap: "Stale tracked events", n: n(c.stale_tracked_events), to: "/events?stale=1" },
              { gap: "Failed capture jobs", n: n(c.failed_capture_jobs), to: "/captures" },
            ]}
              columns={[
                { key: "gap", header: "Gap", render: (r) => <span className="text-slate-200">{r.gap}</span> },
                { key: "n", header: "Count", render: (r) => <span className={`tabular-nums ${r.n > 0 ? "text-amber-300" : "text-slate-400"}`}>{num(r.n)}</span> },
                { key: "to", header: "", render: (r) => <Link to={r.to}>inspect →</Link> },
              ]} />
          </Card>
        </Section>
      </div>
    </div>
  );
}

function pctOf(present: unknown, missing: unknown): number {
  const p = Number(present) || 0, m = Number(missing) || 0;
  const denom = p + m;
  return denom ? Math.round((p / denom) * 100) : 0;
}
function freshnessPct(c: Record<string, unknown>): number {
  const active = Number(c.active_tracked_events) || 0, stale = Number(c.stale_tracked_events) || 0;
  return active ? Math.round(((active - stale) / active) * 100) : 100;
}
function capturePct(c: Record<string, unknown>): number {
  const caps = Number(c.captures_total) || 0, failed = Number(c.failed_capture_jobs) || 0;
  const denom = caps + failed;
  return denom ? Math.round((caps / denom) * 100) : 100;
}
