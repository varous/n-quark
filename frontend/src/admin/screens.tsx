import { useState } from "react";
import { api } from "./api";
import { Badge, Card, ErrorBox, ExportButtons, Link, Loading, Stat, Table, Unavailable, useAsync, useHashQuery, fmt } from "./ui";

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

const CAPTURE_STATES = ["SUCCESS", "RECORD_ABSENT", "SOURCE_UNAVAILABLE", "PARSE_ERROR"];
const RESOLUTION_STATES = ["RESOLVED", "PARTIAL", "AMBIGUOUS", "UNRESOLVED", "POSSIBLE_MATCH", "CONFLICT", "NONE"];
const TEMPORAL_STATES = ["UPCOMING", "ONGOING", "PAST", "UNKNOWN"];
const PROVIDER_LIFECYCLES = ["SCHEDULED", "CANCELLED", "POSTPONED", "RESCHEDULED", "UNKNOWN"];

export function Events() {
  const [params, setParams] = useHashQuery();
  const [offset, setOffset] = useState(0);
  const limit = 25;
  const f = {
    q: params.q ?? "", source: params.source ?? "", city: params.city ?? "",
    capture_state: params.capture_state ?? "", resolution_status: params.resolution_status ?? "",
    temporal_state: params.temporal_state ?? "", provider_lifecycle: params.provider_lifecycle ?? "",
    has_transitions: params.has_transitions === "1", stale_only: params.stale_only === "1",
    date_from: params.date_from ?? "", date_to: params.date_to ?? "",
  };
  const set = (patch: Record<string, string>) => { setOffset(0); setParams(patch); };
  const { data, loading, error } = useAsync(() => api.events({ ...f, limit, offset }),
    [JSON.stringify(f), offset]);
  const exportFilters = { ...f, has_transitions: f.has_transitions ? "1" : "", stale_only: f.stale_only ? "1" : "" };
  return (
    <div className="space-y-4">
      <Card title="Filter events" right={<ExportButtons href={(fmt) => api.exportHref("events", fmt, exportFilters)} />}>
        <div className="flex flex-wrap items-end gap-3">
          <input className="w-64 rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm"
            placeholder="Search title / artist / venue / id…" value={f.q} onChange={(e) => set({ q: e.target.value })} />
          <Select label="Source" value={f.source} onChange={(v) => set({ source: v })} options={["", "boshow", "district"]} />
          <input className="w-32 rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm"
            placeholder="City" value={f.city} onChange={(e) => set({ city: e.target.value })} />
          <Select label="Capture state" value={f.capture_state} onChange={(v) => set({ capture_state: v })} options={["", ...CAPTURE_STATES]} />
          <Select label="Resolution" value={f.resolution_status} onChange={(v) => set({ resolution_status: v })} options={["", ...RESOLUTION_STATES]} />
          <Select label="When" value={f.temporal_state} onChange={(v) => set({ temporal_state: v })} options={["", ...TEMPORAL_STATES]} />
          <Select label="Listing change" value={f.provider_lifecycle} onChange={(v) => set({ provider_lifecycle: v })} options={["", ...PROVIDER_LIFECYCLES]} />
          <DateField label="From" value={f.date_from} onChange={(v) => set({ date_from: v })} />
          <DateField label="To" value={f.date_to} onChange={(v) => set({ date_to: v })} />
          <label className="flex items-center gap-1.5 text-sm text-slate-300">
            <input type="checkbox" checked={f.has_transitions} onChange={(e) => set({ has_transitions: e.target.checked ? "1" : "" })} /> Has transitions
          </label>
          <label className="flex items-center gap-1.5 text-sm text-slate-300">
            <input type="checkbox" checked={f.stale_only} onChange={(e) => set({ stale_only: e.target.checked ? "1" : "" })} /> Stale
          </label>
          <button className="rounded border border-slate-700 px-2 py-1.5 text-xs text-slate-400 hover:bg-slate-800"
            onClick={() => setParams(Object.fromEntries(Object.keys(params).map((k) => [k, ""])))}>Clear</button>
        </div>
      </Card>
      <div className="flex items-center gap-3 text-xs text-slate-500">
        {data && <span>{data.count} events{data.hydrated ? " (searched)" : ""}</span>}
        {data?.capped && <span className="text-amber-400">result set capped for search</span>}
      </div>
      {error && <ErrorBox message={error} />}
      {data && !data.available && <Unavailable />}
      {loading ? <Loading /> : (
        <Card>
          {/* Product-first columns (§5B.2.6 §7): Event / Date / City / Source / status in plain language.
              Resolution/state/transition counts live on the event's Advanced tabs, not the market list. */}
          <Table rows={data?.events ?? []} empty="No events match these filters." columns={[
            { key: "title", header: "Event", render: (r) => <Link to={`/events/${r.canonical_event_id}`}>{r.title ?? r.source_record_id}</Link> },
            { key: "starts_at", header: "Date", render: (r) => fmt(r.starts_at) },
            { key: "city", header: "City", render: (r) => r.city ? fmt(r.city) : <span className="text-slate-600">—</span> },
            { key: "source", header: "Source" },
            { key: "state_count", header: "Observed changes", render: (r) => <span className="text-slate-400">{r.transition_count || 0}</span> },
            { key: "lifecycle", header: "Status", render: (r) => {
              const lc = (r.lifecycle ?? {}) as Record<string, unknown>;
              const temporal = { UPCOMING: "Upcoming", ONGOING: "Happening now", PAST: "Past event", UNKNOWN: "Time unknown" }[String(lc.temporal_state)] ?? "Time unknown";
              const provider = String(lc.provider_lifecycle ?? "UNKNOWN");
              return <Badge label={provider === "UNKNOWN" || provider === "SCHEDULED" ? temporal : `${temporal} · ${provider[0]}${provider.slice(1).toLowerCase()}`} />;
            } },
          ]} />
          <Pager offset={offset} limit={limit} count={data?.count ?? 0} onChange={setOffset} />
        </Card>
      )}
    </div>
  );
}

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: string[] }) {
  return (
    <label className="text-sm">
      <span className="mb-1 block text-xs text-slate-500">{label}</span>
      <select className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o} value={o}>{o === "" ? "All" : o}</option>)}
      </select>
    </label>
  );
}

function DateField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="text-sm">
      <span className="mb-1 block text-xs text-slate-500">{label}</span>
      <input type="date" className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm" value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
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

// Per-source crawler diagnostics (Phase C). Read-only, inspection-first.
export function Diagnostics() {
  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">Per-source capture &amp; parser health. Field state distinguishes
        <span className="text-slate-300"> present</span> / <span className="text-slate-300">valid</span> /
        <span className="text-slate-300"> specific</span> / <span className="text-slate-300">resolved</span>.</p>
      {["boshow", "district"].map((s) => <SourceDiagnosticsCard key={s} source={s} />)}
    </div>
  );
}

function SourceDiagnosticsCard({ source }: { source: string }) {
  const { data, loading, error } = useAsync(() => api.sourceDiagnostics(source), [source]);
  if (loading) return <Loading label={`Loading ${source}`} />;
  if (error) return <ErrorBox message={error} />;
  if (!data) return null;
  return (
    <Card title={`Source · ${source}`} right={<Badge label={data.available ? "Observed" : "Failed"} />}>
      {!data.available && <Unavailable />}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
        <Stat label="Tracked events" value={data.tracked_events} />
        <Stat label="Success rate" value={data.capture_success_rate === null ? "—" : `${Math.round(data.capture_success_rate * 100)}%`} />
        <Stat label="Jobs failed" value={data.jobs_failed} hint={`${data.jobs_failed_terminal} terminal`} />
        <Stat label="Parser failures" value={data.parser_failures} />
        <Stat label="Avg gap (h)" value={data.average_capture_gap_hours ?? "—"} />
        <Stat label="Stale events" value={data.stale_events} />
        <Stat label="Multi-state" value={data.events_with_multiple_states} />
        <Stat label="With transitions" value={data.events_with_transitions} />
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          <div className="mb-1 text-xs uppercase text-slate-500">Geography field state</div>
          <div className="flex flex-wrap gap-2 text-xs">
            <FieldChip label="present" value={data.geography.present} />
            <FieldChip label="valid" value={data.geography.valid} tone="emerald" />
            <FieldChip label="placeholder" value={data.geography.placeholder} tone="amber" />
            <FieldChip label="missing" value={data.geography.missing} tone="rose" />
          </div>
          {data.last_successful_capture && <div className="mt-2 text-xs text-slate-500">Last successful capture: {data.last_successful_capture}</div>}
        </div>
        <div>
          <div className="mb-1 text-xs uppercase text-slate-500">Failure classifications</div>
          {Object.keys(data.failure_classifications).length === 0
            ? <div className="text-xs text-slate-600">None.</div>
            : <ul className="text-xs text-slate-300">{Object.entries(data.failure_classifications).map(([k, v]) => <li key={k}><span className="font-mono">{k}</span>: {v}</li>)}</ul>}
        </div>
      </div>
      <div className="mt-4 text-xs">
        <div className="mb-1 uppercase text-slate-500">Entity resolution</div>
        <div className="flex flex-wrap gap-3 text-slate-400">
          {Object.entries(data.entity_resolution).map(([t, v]) => (
            <span key={t}><span className="text-slate-300">{t}</span> resolved {v.resolved} · ambiguous {v.ambiguous} · unresolved {v.unresolved}</span>
          ))}
        </div>
      </div>
    </Card>
  );
}

function FieldChip({ label, value, tone }: { label: string; value: number; tone?: string }) {
  const cls = tone === "emerald" ? "border-emerald-500/30 text-emerald-300"
    : tone === "amber" ? "border-amber-500/30 text-amber-300"
    : tone === "rose" ? "border-rose-500/30 text-rose-300" : "border-slate-600/40 text-slate-300";
  return <span className={`rounded border px-2 py-0.5 ${cls}`}>{label} <span className="tabular-nums font-semibold">{value}</span></span>;
}

export function Captures() {
  const [status, setStatus] = useState("");
  const [source, setSource] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 25;
  const { data, loading, error } = useAsync(() => api.captureJobs({ status, source, limit, offset }), [status, source, offset]);
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <select className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
          <option value="">All statuses</option>
          {["PENDING", "RUNNING", "SUCCEEDED", "FAILED_RETRYABLE", "FAILED_TERMINAL", "SKIPPED"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <select className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm" value={source} onChange={(e) => { setSource(e.target.value); setOffset(0); }}>
          <option value="">All sources</option><option value="boshow">boshow</option><option value="district">district</option>
        </select>
        {data && <span className="text-xs text-slate-500">{data.count} jobs</span>}
        <div className="ml-auto"><ExportButtons href={(fmt) => api.exportHref("capture-jobs", fmt, { status, source })} /></div>
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
  const gm = data?.gateway_migration ?? {};
  return (
    <div className="space-y-6">
      <Card title="Service health" right={data?.checked_at ? <span className="text-xs text-slate-500">checked {data.checked_at}</span> : undefined}>
        <Table rows={Object.entries(data?.services ?? {}).map(([name, s]) => ({ name, ...s }))} columns={[
          { key: "name", header: "Service" },
          { key: "available", header: "Reachable", render: (r) => <Badge label={r.available ? "Observed" : "Failed"} /> },
          { key: "status", header: "HTTP" },
          { key: "version", header: "Version", render: (r) => <span className="font-mono text-xs">{fmt(r.version)}</span> },
          { key: "flags", header: "Flags", render: (r) => <span className="text-xs text-slate-400">{Object.entries(r.flags ?? {}).map(([k, v]) => `${k.replace(/_enabled/, "")}=${v}`).join(", ") || "—"}</span> },
          { key: "last_check", header: "Last check", render: (r) => <span className="text-xs text-slate-500">{fmt(r.last_check)}</span> },
        ]} />
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Gateway migration & flags">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
            <div><dt className="text-xs uppercase text-slate-500">Migration version</dt><dd className="font-mono text-slate-200">{fmt(gm.version)}</dd></div>
            <div><dt className="text-xs uppercase text-slate-500">Migration ok</dt><dd><Badge label={gm.ok ? "Observed" : "Failed"} /></dd></div>
          </dl>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            {Object.entries(data?.feature_flags ?? {}).map(([k, v]) => (
              <span key={k} className={`rounded border px-2 py-0.5 ${v ? "border-emerald-500/30 text-emerald-300" : "border-slate-600/40 text-slate-400"}`}>{k.replace(/^admin_/, "")}={String(v)}</span>
            ))}
          </div>
        </Card>
        <Card title="Canonical counts">
          <div className="grid grid-cols-3 gap-3">
            {Object.entries(data?.canonical_counts ?? {}).map(([k, v]) => <Stat key={k} label={k.replace(/_/g, " ")} value={v ?? "—"} />)}
          </div>
        </Card>
      </div>
      <Card title="Data quality indicators">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
          {Object.entries(data?.data_quality ?? {}).map(([k, v]) => <Stat key={k} label={k.replace(/_/g, " ")} value={v} />)}
        </div>
      </Card>
    </div>
  );
}
