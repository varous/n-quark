import type { ReactNode } from "react";
import {
  api, type Dashboard, type DemandSummary, type ProductCounts, type SystemHealth,
} from "./api";
import {
  Badge, Bar, Card, ErrorBox, Link, Loading, num, relTime, Section, StatTile, Tag, useAsync,
} from "./ui";

// The console's landing view (Phase 5B.2 inc.6): answers "what is n-quark seeing?" in product terms —
// Events / Artists / Venues / Organizers observed, monitoring coverage, what changed, what needs
// attention — not infrastructure. Product totals come from the AUTHORITATIVE canonical registry
// (never raw graph-node counts). Everything reads live production; nothing is cached or estimated.

type QMetrics = Awaited<ReturnType<typeof api.dataQualityMetrics>>;
type Bundle = {
  dash: Dashboard & { downstream?: Record<string, boolean> };
  health: SystemHealth; demand: DemandSummary; counts: ProductCounts; quality: QMetrics | null;
};

const SERVICE_LABELS: Record<string, string> = {
  crawl: "Collector", signal: "Signal", observation: "Observations", graph: "Graph",
  media: "Media", artist_intelligence: "Demand", entity: "Entity", analytics: "Analytics",
};

export function Overview() {
  const { data, loading, error, reload } = useAsync<Bundle>(async () => {
    const [dash, health, demand, counts, quality] = await Promise.all([
      api.dashboard(), api.systemHealth(), api.demandSummary(), api.catalogCounts(),
      api.dataQualityMetrics().catch(() => null),
    ]);
    return { dash, health, demand, counts, quality };
  }, []);

  if (loading) return <Loading label="Loading the market overview" />;
  if (error || !data) return <ErrorBox message={error ?? "No data."} />;

  const { dash, health, demand, counts, quality } = data;
  const c = dash.cards ?? {};
  const services = health.services ?? {};
  const up = Object.values(services).filter((s) => s.available && s.health).length;
  const total = Object.keys(services).length;
  const sourcesActive = Object.values(dash.sources ?? {}).filter((s) => (s.tracked_events ?? 0) > 0).length;
  const collectionOk = (dash.downstream?.crawl ?? true) && (c.active_tracked_events ?? 0) > 0;

  // Registry-backed product cohorts (§5) — never raw graph-node counts.
  const artists = counts.artists;
  const venues = counts.venues;
  const organizers = counts.organizers;
  const eventLifecycle = counts.events?.by_temporal_state ?? {};
  const monitored = demand.artists_with_demand_observation ?? null;
  const ytVerified = demand.resolved_youtube_artists ?? 0;

  // Attention model (§6) — only categories backed by real supported states.
  const openReview = quality?.available !== false ? (quality?.open_review_items ?? 0) : null;
  const failedCaptures = dash.attention_queues?.recent_capture_failures?.length ?? 0;

  return (
    <div>
      <Section
        title="Market overview"
        subtitle="What n-quark is currently observing across the live-entertainment market — supply, identity, and demand coverage."
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
              ? <>Collection is <strong>active</strong> — {num(c.active_tracked_events)} events tracked across {sourcesActive} source{sourcesActive === 1 ? "" : "s"}, accruing observations continuously.</>
              : <>Collection may be degraded — verify the collector and observation store on <Link to="/health">System health</Link>.</>}
          </span>
          <span className="ml-auto text-xs text-slate-400">{up}/{total} services up</span>
        </div>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <StatTile label="Events observed" value={countOr(counts.events?.canonical_events ?? null)} tone="brand" to="/events" caption="historical corpus" />
          <StatTile label="Upcoming" value={num(eventLifecycle.UPCOMING ?? 0)} tone="good" to="/events?temporal_state=UPCOMING" caption="current supply" />
          <StatTile label="Happening now" value={num(eventLifecycle.ONGOING ?? 0)} tone="brand" to="/events?temporal_state=ONGOING" caption="time-supported" />
          <StatTile label="Past" value={num(eventLifecycle.PAST ?? 0)} to="/events?temporal_state=PAST" caption="historical intelligence" />
          <StatTile label="Artists identified" value={countOr(artists)} tone="good" to="/artists" caption="registry-backed" />
          <StatTile label="Venues identified" value={countOr(venues)} to="/venues" caption="registry-backed" />
          <StatTile label="Organizers identified" value={countOr(organizers)} to="/organizers" caption="registry-backed" />
          <StatTile label="Artists monitored" value={countOr(monitored)} tone="brand" to="/demand" caption={`of ${countOr(artists)} identified`} />
          <StatTile label="Sources active" value={num(sourcesActive)} to="/sources" caption={`${num(c.captures_total)} captures observed`} />
        </div>
      </Section>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Section title="Needs attention" subtitle="Real supported states only — each drills into the relevant screen.">
            <div className="grid gap-3 sm:grid-cols-2">
              <AttentionCard
                title="Data quality"
                to="/data-quality"
                ok={openReview === 0 || openReview === null}
                okCopy={openReview === null ? "Review status unavailable right now." : "No open identity-review issues."}
                alertCopy={`${openReview} identity mention${openReview === 1 ? "" : "s"} awaiting review.`}
              />
              <AttentionCard
                title="Collection"
                to="/captures"
                ok={failedCaptures === 0}
                okCopy="No recent capture failures."
                alertCopy={`${failedCaptures} recent capture failure${failedCaptures === 1 ? "" : "s"}.`}
              />
              <AttentionCard
                title="Content movement"
                to={ytVerified > 0 ? "/market" : "/watchlist"}
                ok={ytVerified > 0}
                okCopy={`${ytVerified} verified YouTube channel${ytVerified === 1 ? "" : "s"} being sensed.`}
                alertCopy="No verified YouTube channels yet — add one from the Watchlist to begin content sensing."
                alertTone="brand"
              />
              <AttentionCard
                title="Demand monitoring"
                to="/demand"
                ok={(monitored ?? 0) > 0}
                okCopy={`${countOr(monitored)} of ${countOr(artists)} identified artists have demand state.`}
                alertCopy="No demand observations collected yet."
                alertTone="brand"
              />
            </div>
          </Section>

          <Section title="Sources" subtitle="Observed supply per source — this is coverage, not total market.">
            <Card>
              <div className="grid gap-3 sm:grid-cols-2">
                {Object.entries(dash.sources ?? {}).map(([source, v]) => {
                  const s = v as Record<string, number>;
                  return (
                    <div key={source} className="rounded-lg border border-slate-800 p-3">
                      <div className="mb-1 flex items-center justify-between">
                        <span className="font-medium text-slate-100">{source}</span>
                        <span className="text-xs text-slate-500">{num(s.tracked_events)} events</span>
                      </div>
                      <div className="text-xs text-slate-400">
                        {num(s.resolved_artists)} artists · {num(s.resolved_venues)} venues · {num(s.transitions)} state changes
                        {(s.ambiguous ?? 0) > 0 && <span className="ml-1 text-amber-400">· {num(s.ambiguous)} to review</span>}
                      </div>
                    </div>
                  );
                })}
                {Object.keys(dash.sources ?? {}).length === 0 && <div className="text-sm text-slate-500">No sources reporting.</div>}
              </div>
            </Card>
          </Section>
        </div>

        <div>
          <Section title="Demand coverage">
            <Card>
              <dl className="space-y-2 text-sm">
                <CoverRow k="Artists identified" v={countOr(artists)} />
                <CoverRow k="Demand monitoring" v={`${countOr(monitored)} enrolled`} />
                <CoverRow k="YouTube channels" v={ytVerified > 0
                  ? <span className="text-slate-200">{ytVerified} verified</span>
                  : <span className="text-slate-400">No verified channels yet</span>} />
                <CoverRow k="Search interest" v={demand.youtube_mode ? <Badge label={String(demand.youtube_mode).toUpperCase()} /> : <span className="text-slate-400">—</span>} />
                <CoverRow k="Scheduler" v={demand.scheduler_enabled ? <Badge label="ACTIVE" /> : <span className="text-slate-400">idle</span>} />
              </dl>
              {(artists ?? 0) > 0 && (
                <div className="mt-3 border-t border-slate-800 pt-3">
                  <Bar label="Artists with demand state" value={Number(monitored ?? 0)} max={Number(artists ?? 0) || 1} tone="brand" />
                </div>
              )}
              <div className="mt-3 text-right"><Link to="/demand">Open demand →</Link></div>
            </Card>
          </Section>

          <Section title="Services" subtitle="Service reachability — distinct from evidence availability.">
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

function CoverRow({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="shrink-0 text-slate-500">{k}</dt>
      <dd className="truncate text-right text-slate-200">{v}</dd>
    </div>
  );
}

function AttentionCard({ title, to, ok, okCopy, alertCopy, alertTone = "amber" }:
  { title: string; to: string; ok: boolean; okCopy: string; alertCopy: string; alertTone?: "amber" | "brand" }) {
  const tone = ok ? "border-slate-800" : alertTone === "brand" ? "border-brand-700/50 bg-brand-950/20" : "border-amber-800/50 bg-amber-950/20";
  return (
    <a href={`#${to}`} className={`block rounded-lg border p-3 transition hover:border-brand-600/60 ${tone}`}>
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-200">{title}</span>
        <span className={`h-2 w-2 rounded-full ${ok ? "bg-emerald-400" : alertTone === "brand" ? "bg-brand-400" : "bg-amber-400"}`} />
      </div>
      <p className={`mt-1 text-xs ${ok ? "text-slate-500" : "text-slate-300"}`}>{ok ? okCopy : alertCopy}</p>
    </a>
  );
}

function countOr(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : num(n);
}
