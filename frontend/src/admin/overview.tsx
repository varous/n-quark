import type { ReactNode } from "react";
import { api, type Dashboard, type DemandSummary, type SystemHealth } from "./api";
import {
  Badge, Bar, Card, ErrorBox, KeyValue, Link, Loading, num, relTime, Section, StatTile, Table, Tag, useAsync,
} from "./ui";

// The console's landing view: the live state of the production observation layer at a glance — what is
// being tracked, what is being captured, what has resolved into the canonical universe, how demand
// coverage stands, and which services are up. Everything here reads live production; nothing is cached
// or estimated.

type Bundle = { dash: Dashboard & { downstream?: Record<string, boolean> }; health: SystemHealth; demand: DemandSummary };

const SERVICE_LABELS: Record<string, string> = {
  crawl: "Collector", signal: "Signal", observation: "Observations", graph: "Graph",
  media: "Media", artist_intelligence: "Demand", entity: "Entity", analytics: "Analytics",
};

export function Overview() {
  const { data, loading, error, reload } = useAsync<Bundle>(async () => {
    const [dash, health, demand] = await Promise.all([api.dashboard(), api.systemHealth(), api.demandSummary()]);
    return { dash, health, demand };
  }, []);

  if (loading) return <Loading label="Loading production overview" />;
  if (error || !data) return <ErrorBox message={error ?? "No data."} />;

  const { dash, health, demand } = data;
  const c = dash.cards ?? {};
  const canon = health.canonical_counts ?? {};
  const services = health.services ?? {};
  const up = Object.values(services).filter((s) => s.available && s.health).length;
  const total = Object.keys(services).length;
  const collectionOk = (dash.downstream?.crawl ?? false) && (c.active_tracked_events ?? 0) > 0;
  const artistRate = typeof c.resolved_artist_rate === "number" ? c.resolved_artist_rate : 0;

  return (
    <div>
      <Section
        title="Production overview"
        subtitle="The live state of n-quark's temporal observation layer — tracked supply, captures, the canonical universe, and demand coverage."
        actions={
          <div className="flex items-center gap-3">
            <span className="text-xs text-slate-500">checked {relTime(health.checked_at)}</span>
            <button onClick={reload} className="rounded-lg border border-slate-800 px-2.5 py-1.5 text-xs text-slate-300 hover:border-brand-500 hover:text-brand-300">Refresh</button>
          </div>
        }
      >
        <div className={`mb-5 flex items-center gap-3 rounded-xl border px-4 py-3 text-sm ${collectionOk ? "border-emerald-800/50 bg-emerald-950/20 text-emerald-200" : "border-amber-800/50 bg-amber-950/20 text-amber-200"}`}>
          <span className={`grid h-6 w-6 place-items-center rounded-full ${collectionOk ? "bg-emerald-500/20 text-emerald-300" : "bg-amber-500/20 text-amber-300"}`}>{collectionOk ? "✓" : "!"}</span>
          <span>
            {collectionOk
              ? <>Collection is <strong>active</strong> — {num(c.active_tracked_events)} events tracked, accruing observations continuously.</>
              : <>Collection may be degraded — verify the collector and observation store on the <Link to="/health">health</Link> page.</>}
          </span>
          <span className="ml-auto text-xs text-slate-400">{up}/{total} services up</span>
        </div>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <StatTile label="Tracked events" value={num(c.active_tracked_events)} tone="brand" to="/events" caption="active in the schedule" />
          <StatTile label="Captures" value={num(c.captures_total)} caption={`${num(c.transitions_total)} state transitions`} to="/captures" />
          <StatTile label="Canonical artists" value={num(canon.ARTIST ?? canon.artist)} tone="good" to="/entities?type=ARTIST" caption="in the graph" />
          <StatTile label="Venues" value={num(canon.VENUE ?? canon.venue)} to="/entities?type=VENUE" caption="canonical" />
          <StatTile label="Artist resolution" value={`${Math.round(artistRate * 100)}%`} tone={artistRate >= 0.9 ? "good" : artistRate >= 0.6 ? "warn" : "bad"} caption="mentions resolved" />
          <StatTile label="YouTube identities" value={num(demand.artists_with_youtube_identity)} tone="brand" to="/demand" caption={`${num(demand.resolved_youtube_artists)} verified`} />
        </div>
      </Section>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Section title="Sources">
            <Card>
              <Table<Record<string, unknown>>
                rows={Object.entries(dash.sources ?? {}).map(([source, v]) => ({ source, ...(v as Record<string, number>) } as Record<string, unknown>))}
                empty="No sources reporting."
                columns={[
                  { key: "source", header: "Source", render: (r) => <span className="font-medium text-slate-100">{String(r.source)}</span> },
                  { key: "tracked_events", header: "Events", render: (r) => num(r.tracked_events) },
                  { key: "transitions", header: "Transitions", render: (r) => num(r.transitions) },
                  { key: "resolved_artists", header: "Artists", render: (r) => num(r.resolved_artists) },
                  { key: "resolved_venues", header: "Venues", render: (r) => num(r.resolved_venues) },
                  { key: "ambiguous", header: "Ambiguous", render: (r) => (r.ambiguous ? <Badge label="AMBIGUOUS" /> : num(0)) },
                ]}
              />
            </Card>
          </Section>

          <Section title="Needs attention" subtitle="Deterministic queues — capture failures, stale trackings, and events missing geography.">
            <div className="grid gap-4 md:grid-cols-3">
              <AttentionList title="Capture failures" rows={dash.attention_queues?.recent_capture_failures ?? []}
                render={(j) => <><Link to={`/captures`}>{String(j.source)}·{String(j.canonical_event_id ?? j.job_id).slice(0, 22)}</Link> <Badge label={String(j.status)} /></>} />
              <AttentionList title="Stale trackings" rows={dash.attention_queues?.stale_events ?? []}
                render={(t) => <><Link to={`/events/${t.canonical_event_id}`}>{String(t.source_record_id ?? t.canonical_event_id).slice(0, 24)}</Link> <span className="text-xs text-slate-500">{num(t.capture_gap_hours)}h</span></>} />
              <AttentionList title="Missing geography" rows={dash.attention_queues?.events_without_geography ?? []}
                render={(t) => <Link to={`/events/${t.canonical_event_id}`}>{String(t.source_record_id ?? t.canonical_event_id).slice(0, 28)}</Link>} />
            </div>
          </Section>
        </div>

        <div>
          <Section title="Demand coverage">
            <Card>
              <KeyValue items={[
                ["YouTube mode", demand.youtube_mode ? <Badge label={String(demand.youtube_mode).toUpperCase()} /> : "—"],
                ["Artists w/ identity", num(demand.artists_with_youtube_identity)],
                ["Verified (RESOLVED)", num(demand.resolved_youtube_artists)],
                ["With demand obs.", num(demand.artists_with_demand_observation)],
                ["Stale demand", num(demand.stale_demand_artists)],
                ["Scheduler", demand.scheduler_enabled ? <Badge label="ACTIVE" /> : <Badge label="NONE" />],
              ]} />
              <div className="mt-3 border-t border-slate-800 pt-3">
                <Bar label="YouTube-resolved of identified"
                  value={Number(demand.resolved_youtube_artists ?? 0)} max={Number(demand.artists_with_youtube_identity ?? 0) || 1} tone="brand" />
              </div>
              <div className="mt-3 text-right"><Link to="/demand">Open demand intelligence →</Link></div>
            </Card>
          </Section>

          <Section title="Services">
            <Card>
              <div className="space-y-1.5">
                {Object.entries(services).map(([name, s]) => {
                  const ok = s.available && !!s.health;
                  return (
                    <div key={name} className="flex items-center justify-between gap-2 text-sm">
                      <span className="flex items-center gap-2">
                        <span className={`h-2 w-2 rounded-full ${ok ? "bg-emerald-400" : "bg-rose-500"}`} />
                        <span className="text-slate-300">{SERVICE_LABELS[name] ?? name}</span>
                      </span>
                      <span className="flex items-center gap-2">
                        {s.version && <Tag>{String(s.version)}</Tag>}
                        <span className="text-xs text-slate-500">{ok ? "up" : "down"}</span>
                      </span>
                    </div>
                  );
                })}
              </div>
              <div className="mt-3 text-right"><Link to="/health">Service health →</Link></div>
            </Card>
          </Section>
        </div>
      </div>
    </div>
  );
}

function AttentionList<T extends Record<string, unknown>>({ title, rows, render }: { title: string; rows: T[]; render: (r: T) => ReactNode }) {
  return (
    <Card title={`${title} · ${rows.length}`}>
      {rows.length === 0 ? (
        <div className="text-xs text-slate-500">Nothing flagged.</div>
      ) : (
        <ul className="space-y-1.5 text-sm">
          {rows.slice(0, 6).map((r, i) => <li key={i} className="truncate">{render(r)}</li>)}
        </ul>
      )}
    </Card>
  );
}
