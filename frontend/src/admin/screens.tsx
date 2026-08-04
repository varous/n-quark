import { useState } from "react";
import { api } from "./api";
import { Badge, Card, Empty, ErrorBox, Link, Loading, Stat, Table, Unavailable, useAsync, fmt } from "./ui";

const CARD_LABELS: Record<string, string> = {
  active_tracked_events: "Active tracked events",
  captures_total: "Captures (total)",
  events_with_3plus_states: "Events ≥3 states",
  transitions_total: "Transitions (total)",
  resolved_artist_rate: "Resolved artist rate",
  ambiguous_entity_candidates: "Ambiguous candidates",
  unresolved_entity_candidates: "Unresolved candidates",
  stale_tracked_events: "Stale tracked events",
  failed_capture_jobs: "Failed capture jobs",
  cross_source_entities: "Cross-source entities",
};

export function Overview() {
  const { data, loading, error } = useAsync(() => api.dashboard(), []);
  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} />;
  if (!data) return <Empty message="No data." />;
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
        {Object.entries(data.cards).map(([k, v]) => (
          <Stat key={k} label={CARD_LABELS[k] ?? k} value={typeof v === "number" && !Number.isInteger(v) ? v.toFixed(2) : v} />
        ))}
      </div>
      <Card title="Sources">
        <div className="grid gap-3 md:grid-cols-2">
          {Object.entries(data.sources).map(([src, s]) => (
            <div key={src} className="rounded border border-slate-800 p-3">
              <div className="mb-2 flex items-center justify-between">
                <Link to={`/sources/${src}`} className="font-medium text-sky-400 hover:underline">{src}</Link>
                <span className="text-xs text-slate-500">{s.tracked_events} tracked</span>
              </div>
              <div className="grid grid-cols-3 gap-2 text-xs text-slate-400">
                <span>artists {s.resolved_artists}</span>
                <span>venues {s.resolved_venues}</span>
                <span>orgs {s.resolved_organizers}</span>
                <span>series {s.series_links}</span>
                <span>states {s.distinct_states}</span>
                <span>transitions {s.transitions}</span>
                <span>ambiguous {s.ambiguous}</span>
                <span>unresolved {s.unresolved}</span>
              </div>
            </div>
          ))}
        </div>
      </Card>
      <div className="grid gap-4 lg:grid-cols-3">
        <AttentionQueue title="Recent capture failures" items={data.attention_queues.recent_capture_failures}
          render={(q) => <><Badge label="Failed" /> <span className="text-slate-300">{fmt(q.canonical_event_id ?? q.job_id)}</span> <span className="text-slate-500">{fmt(q.result_code ?? q.status)}</span></>} />
        <AttentionQueue title="Stale events" items={data.attention_queues.stale_events}
          render={(q) => <Link to={`/events/${q.canonical_event_id}`}><Badge label="Stale" /> {fmt(q.source_record_id)}</Link>} />
        <AttentionQueue title="Events without geography" items={data.attention_queues.events_without_geography}
          render={(q) => <Link to={`/events/${q.canonical_event_id}`}>{fmt(q.source_record_id)}</Link>} />
      </div>
    </div>
  );
}

function AttentionQueue({ title, items, render }: { title: string; items: Array<Record<string, unknown>>; render: (q: Record<string, unknown>) => React.ReactNode }) {
  return (
    <Card title={title}>
      {items.length === 0 ? <Empty message="Clear." /> : (
        <ul className="space-y-1.5 text-sm">
          {items.map((q, i) => <li key={i} className="truncate">{render(q)}</li>)}
        </ul>
      )}
    </Card>
  );
}

export function Sources() {
  const { data, loading, error } = useAsync(() => api.sources(), []);
  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} />;
  return (
    <Card title="Sources">
      <Table rows={data?.sources ?? []} columns={[
        { key: "source", header: "Source", render: (r) => <Link to={`/sources/${r.source}`}>{r.source}</Link> },
        { key: "tracked_events", header: "Tracked" },
        { key: "resolved_artists", header: "Artists" },
        { key: "resolved_venues", header: "Venues" },
        { key: "resolved_organizers", header: "Organizers" },
        { key: "series_links", header: "Series" },
        { key: "ambiguous", header: "Ambiguous" },
        { key: "unresolved", header: "Unresolved" },
      ]} />
    </Card>
  );
}

export function Events() {
  const [source, setSource] = useState("");
  const [stale, setStale] = useState(false);
  const [offset, setOffset] = useState(0);
  const limit = 25;
  const { data, loading, error } = useAsync(() => api.events({ source, stale_only: stale, limit, offset }), [source, stale, offset]);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <select className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm" value={source} onChange={(e) => { setSource(e.target.value); setOffset(0); }}>
          <option value="">All sources</option>
          <option value="boshow">boshow</option>
          <option value="district">district</option>
        </select>
        <label className="flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={stale} onChange={(e) => { setStale(e.target.checked); setOffset(0); }} /> Stale only
        </label>
        {data && <span className="text-xs text-slate-500">{data.count} events</span>}
      </div>
      {error && <ErrorBox message={error} />}
      {data && !data.available && <Unavailable />}
      {loading ? <Loading /> : (
        <Card>
          <Table rows={data?.events ?? []} columns={[
            { key: "title", header: "Title", render: (r) => <Link to={`/events/${r.canonical_event_id}`}>{r.title ?? r.source_record_id}</Link> },
            { key: "source", header: "Source" },
            { key: "city", header: "City", render: (r) => fmt(r.city) },
            { key: "state_count", header: "States" },
            { key: "transition_count", header: "Transitions" },
            { key: "enrichment_status", header: "Enrichment", render: (r) => <Badge label={r.enrichment_status} /> },
            { key: "last_capture_status", header: "Capture", render: (r) => <Badge label={r.stale ? "Stale" : r.last_capture_status} /> },
          ]} />
          <Pager offset={offset} limit={limit} count={data?.count ?? 0} onChange={setOffset} />
        </Card>
      )}
    </div>
  );
}

export function Pager({ offset, limit, count, onChange }: { offset: number; limit: number; count: number; onChange: (o: number) => void }) {
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(count / limit));
  return (
    <div className="mt-3 flex items-center justify-between text-xs text-slate-400">
      <span>Page {page} / {pages}</span>
      <div className="flex gap-2">
        <button disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))} className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40">Prev</button>
        <button disabled={page >= pages} onClick={() => onChange(offset + limit)} className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40">Next</button>
      </div>
    </div>
  );
}

export function Resolution() {
  const [type, setType] = useState("");
  const { data, loading, error } = useAsync(() => api.resolutionQueue({ entity_type: type, limit: 100 }), [type]);
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <select className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">All types</option>
          {["ARTIST", "VENUE", "ORGANIZER", "EVENT_SERIES"].map((t) => <option key={t}>{t}</option>)}
        </select>
        {data && <span className="text-xs text-slate-500">{data.count} in queue</span>}
      </div>
      {error && <ErrorBox message={error} />}
      {loading ? <Loading /> : (
        <Card title="Resolution queue">
          <Table rows={data?.items ?? []} empty="Queue is clear." columns={[
            { key: "entity_type", header: "Type" },
            { key: "raw_name", header: "Raw", render: (r) => fmt(r.raw_name) },
            { key: "source", header: "Source" },
            { key: "status", header: "Status", render: (r) => <Badge label={String(r.status)} /> },
            { key: "reason", header: "Reason", render: (r) => fmt(r.reason) },
            { key: "score", header: "Score", render: (r) => fmt(r.score) },
          ]} />
        </Card>
      )}
    </div>
  );
}

export function Captures() {
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 25;
  const { data, loading, error } = useAsync(() => api.captureJobs({ status, limit, offset }), [status, offset]);
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <select className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
          <option value="">All statuses</option>
          {["PENDING", "RUNNING", "SUCCEEDED", "FAILED_RETRYABLE", "FAILED_TERMINAL", "SKIPPED"].map((s) => <option key={s}>{s}</option>)}
        </select>
        {data && <span className="text-xs text-slate-500">{data.count} jobs</span>}
      </div>
      {error && <ErrorBox message={error} />}
      {loading ? <Loading /> : (
        <Card title="Capture jobs">
          <Table rows={data?.jobs ?? []} columns={[
            { key: "id", header: "Job", render: (r) => <span className="font-mono text-xs">{r.id.slice(0, 10)}</span> },
            { key: "source", header: "Source" },
            { key: "canonical_event_id", header: "Event", render: (r) => r.canonical_event_id ? <Link to={`/events/${r.canonical_event_id}`}>{r.source_record_id}</Link> : r.source_record_id },
            { key: "status", header: "Status", render: (r) => <Badge label={r.status.includes("FAILED") ? "Failed" : r.status} /> },
            { key: "result_code", header: "Result", render: (r) => fmt(r.result_code) },
            { key: "attempt_count", header: "Attempts" },
            { key: "next_capture_at", header: "Next", render: (r) => fmt(r.next_capture_at) },
          ]} />
          <Pager offset={offset} limit={limit} count={data?.count ?? 0} onChange={setOffset} />
        </Card>
      )}
    </div>
  );
}

export function Health() {
  const { data, loading, error } = useAsync(() => api.systemHealth(), []);
  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} />;
  return (
    <div className="space-y-6">
      <Card title="Service health">
        <Table rows={Object.entries(data?.services ?? {}).map(([name, s]) => ({ name, ...s }))} columns={[
          { key: "name", header: "Service" },
          { key: "available", header: "Reachable", render: (r) => <Badge label={r.available ? "Observed" : "Failed"} /> },
          { key: "status", header: "HTTP" },
          { key: "flags", header: "Flags", render: (r) => <span className="text-xs text-slate-400">{r.health ? Object.entries(r.health).filter(([k]) => k.includes("enabled")).map(([k, v]) => `${k.replace(/_enabled/, "")}=${v}`).join(", ") || "—" : "—"}</span> },
        ]} />
      </Card>
      <Card title="Data quality">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          {Object.entries(data?.data_quality ?? {}).map(([k, v]) => <Stat key={k} label={k.replace(/_/g, " ")} value={v} />)}
        </div>
      </Card>
    </div>
  );
}
