import { useState } from "react";
import { api } from "./api";
import { Badge, Card, Empty, ErrorBox, ExportButtons, Loading, Table, Unavailable, useAsync, useDrawer, fmt } from "./ui";

// Phase C: the Resolution view is INSPECTION-FIRST. It surfaces the resolver's uncertainty queues
// (AMBIGUOUS / UNRESOLVED / POSSIBLE_MATCH / CONFLICT / LOW_CONFIDENCE) with the raw + normalized
// evidence, candidate entities, supporting/contradicting signals, resolver reason and history — and
// NO mutation controls. The governed decision commands still exist on the BFF for developer
// debugging (see docs/admin-console.md), but they are intentionally not exposed in this console.

const TYPES = ["", "ARTIST", "VENUE", "ORGANIZER", "EVENT_SERIES"];

export function Resolution() {
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [source, setSource] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const { data, loading, error } = useAsync(
    () => api.resolutionQueue({ status, entity_type: type, source, limit: 200 }),
    [status, type, source]);
  const states: string[] = data?.states ?? ["AMBIGUOUS", "UNRESOLVED", "POSSIBLE_MATCH", "CONFLICT", "LOW_CONFIDENCE"];
  const byStatus: Record<string, number> = data?.by_status ?? {};
  return (
    <div className="space-y-4">
      <Card title="Resolution diagnostics" right={<ExportButtons href={(fmt) => api.exportHref("resolution-queue", fmt, { status, entity_type: type, source })} />}>
        <p className="mb-3 text-xs text-slate-500">Inspection-first — uncertainty is surfaced, not edited. Governed
          mutation commands remain available on the BFF for developer debugging only.</p>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => setStatus("")} className={`rounded border px-2 py-1 text-xs ${status === "" ? "border-sky-500 text-sky-300" : "border-slate-700 text-slate-400"}`}>All</button>
          {states.map((s) => (
            <button key={s} onClick={() => setStatus(s)} className={`rounded border px-2 py-1 text-xs ${status === s ? "border-sky-500 text-sky-300" : "border-slate-700 text-slate-400"}`}>
              {s} <span className="tabular-nums text-slate-500">{byStatus[s] ?? 0}</span>
            </button>
          ))}
          <select className="ml-2 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs" value={type} onChange={(e) => setType(e.target.value)}>
            {TYPES.map((t) => <option key={t} value={t}>{t === "" ? "All types" : t}</option>)}
          </select>
          <select className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">All sources</option><option value="boshow">boshow</option><option value="district">district</option>
          </select>
          {data && <span className="text-xs text-slate-500">{data.count} shown</span>}
        </div>
      </Card>
      {error && <ErrorBox message={error} />}
      {data && !data.available && <Unavailable />}
      <div className="grid gap-4 lg:grid-cols-2">
        {loading ? <Loading /> : (
          <Card title="Queue">
            <Table rows={data?.items ?? []} empty="Queue is clear." columns={[
              { key: "entity_type", header: "Type", render: (r) => fmt(r.entity_type) },
              { key: "raw_name", header: "Raw", render: (r) => fmt(r.raw_name ?? r.raw_value) },
              { key: "normalized", header: "Normalized", render: (r) => fmt(r.normalized_value ?? r.normalized) },
              { key: "source", header: "Source", render: (r) => fmt(r.source) },
              { key: "status", header: "Status", render: (r) => <Badge label={String(r.status ?? "")} /> },
              { key: "insp", header: "", render: (r) => <button className="text-xs text-sky-400 hover:underline"
                  onClick={() => setSelected(String(r.candidate_id ?? r.id ?? ""))}>inspect</button> },
            ]} />
          </Card>
        )}
        <CandidateInspector id={selected} />
      </div>
    </div>
  );
}

function CandidateInspector({ id }: { id: string | null }) {
  const { open } = useDrawer();
  const { data, loading, error } = useAsync(() => (id ? api.candidate(id) : Promise.resolve(null)), [id]);
  if (!id) return <Card title="Evidence"><Empty message="Select a queue item to inspect its evidence." /></Card>;
  if (loading) return <Card title="Evidence"><Loading /></Card>;
  if (error) return <Card title="Evidence"><ErrorBox message={error} /></Card>;
  if (!data) return <Card title="Evidence"><Empty message="Not found." /></Card>;
  const d = data as Record<string, unknown>;
  const supporting = (d.supporting ?? d.supporting_signals ?? []) as unknown[];
  const contradicting = (d.contradicting ?? d.contradicting_signals ?? []) as unknown[];
  const candidates = (d.candidate_entities ?? d.candidates ?? []) as Array<Record<string, unknown>>;
  const history = (d.history ?? d.resolution_history ?? []) as Array<Record<string, unknown>>;
  return (
    <Card title="Evidence" right={<button className="text-xs text-sky-400" onClick={() => open("Candidate (raw)", d)}>raw</button>}>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <F label="Raw value" value={fmt(d.raw_name ?? d.raw_value)} />
        <F label="Normalized" value={fmt(d.normalized_value ?? d.normalized)} />
        <F label="Type" value={fmt(d.entity_type)} />
        <F label="Source event" value={<span className="font-mono text-xs">{fmt(d.source_event_id ?? d.canonical_event_id)}</span>} />
        <F label="Status" value={<Badge label={String(d.status ?? "")} />} />
        <F label="Resolver reason" value={fmt(d.reason ?? d.reason_code)} />
      </dl>
      <Section title="Candidate entities">
        {candidates.length === 0 ? <Empty message="None." /> : (
          <ul className="space-y-1 text-xs">
            {candidates.map((c, i) => <li key={i} className="font-mono text-slate-300">{fmt(c.canonical_entity_id ?? c.id)} <span className="text-slate-500">{fmt(c.score)}</span></li>)}
          </ul>
        )}
      </Section>
      <div className="grid grid-cols-2 gap-4">
        <Section title="Supporting">{signals(supporting)}</Section>
        <Section title="Contradicting">{signals(contradicting)}</Section>
      </div>
      <Section title="History">
        {history.length === 0 ? <Empty message="No history." /> : (
          <ol className="space-y-1 text-xs text-slate-400">
            {history.map((h, i) => <li key={i}>{fmt(h.status ?? h.new_status)} <span className="text-slate-600">{fmt(h.reason ?? h.reason_code ?? h.at ?? h.created_at)}</span></li>)}
          </ol>
        )}
      </Section>
    </Card>
  );
}

function signals(items: unknown[]) {
  if (!items.length) return <Empty message="None." />;
  return <ul className="space-y-0.5 text-xs text-slate-400">{items.map((s, i) => <li key={i}>{typeof s === "object" ? JSON.stringify(s) : String(s)}</li>)}</ul>;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-4">
      <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">{title}</div>
      {children}
    </div>
  );
}

function F({ label, value }: { label: string; value: React.ReactNode }) {
  return <div><dt className="text-xs uppercase text-slate-500">{label}</dt><dd className="text-slate-200">{value}</dd></div>;
}
