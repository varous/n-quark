import { useState } from "react";
import { api, type Subgraph } from "./api";
import { Badge, Card, Empty, ErrorBox, Link, Loading, Table, Unavailable, useAsync, useDrawer, fmt } from "./ui";
import { Pager } from "./screens";

const TABS = ["Current", "Source records", "Timeline", "Evidence", "Entities", "Relationships", "Capture status"];

export function EventDetail({ id }: { id: string }) {
  const [tab, setTab] = useState("Current");
  const { data, loading, error } = useAsync(() => api.eventDetail(id), [id]);
  return (
    <div className="space-y-4">
      <div className="text-xs text-slate-500">Event · <span className="font-mono">{id}</span></div>
      {error && <ErrorBox message={error} />}
      {data && !data.available && <Unavailable />}
      <div className="flex flex-wrap gap-1 border-b border-slate-800">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`px-3 py-2 text-sm ${tab === t ? "border-b-2 border-sky-500 text-slate-100" : "text-slate-400 hover:text-slate-200"}`}>{t}</button>
        ))}
      </div>
      {loading ? <Loading /> : !data ? <Empty message="Not found." /> : (
        <>
          {tab === "Current" && <CurrentView d={data} />}
          {tab === "Source records" && <SourceRecords records={data.source_records} />}
          {tab === "Timeline" && <TimelineTab id={id} />}
          {tab === "Evidence" && <EvidenceTab id={id} />}
          {tab === "Entities" && <EntitiesTab entities={data.resolved_entities} />}
          {tab === "Relationships" && <RelationshipsTab rels={data.relationships} />}
          {tab === "Capture status" && <CaptureStatus id={id} d={data} />}
        </>
      )}
    </div>
  );
}

function CurrentView({ d }: { d: Awaited<ReturnType<typeof api.eventDetail>> }) {
  const { open } = useDrawer();
  const cv = d.current_view;
  const fields = ["title", "starts_at", "city", "organizer", "price_min", "currency", "fill_ratio", "source"];
  return (
    <Card title="Current resolved view" right={<Badge label={String(cv.epistemic ?? "Observed")} />}>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-3">
        {fields.map((f) => (
          <div key={f}>
            <dt className="text-xs uppercase text-slate-500">{f}</dt>
            <dd className="text-slate-200">{fmt(cv[f])}</dd>
          </div>
        ))}
      </dl>
      <button className="mt-4 rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800" onClick={() => open("Event current view (provenance)", cv)}>
        Provenance
      </button>
    </Card>
  );
}

function SourceRecords({ records }: { records: Array<Record<string, unknown>> }) {
  return (
    <Card title="Source records (kept separate — values never flattened)">
      <Table rows={records} empty="No linked source records." columns={[
        { key: "source", header: "Source" },
        { key: "canonical_event_id", header: "Canonical", render: (r) => fmt(r.canonical_event_id) },
        { key: "self", header: "Self", render: (r) => (r.self ? "yes" : "linked") },
        { key: "via_match", header: "Via match", render: (r) => fmt(r.via_match) },
      ]} />
    </Card>
  );
}

function TimelineTab({ id }: { id: string }) {
  const [raw, setRaw] = useState(false);
  const { data, loading, error } = useAsync(() => api.eventTimeline(id), [id]);
  const { open } = useDrawer();
  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} />;
  if (!data?.available) return <Unavailable />;
  return (
    <Card title="Shadow Ledger timeline" right={
      <button className="rounded border border-slate-700 px-2 py-1 text-xs" onClick={() => setRaw(!raw)}>{raw ? "Transitions" : "Raw states"}</button>
    }>
      {raw ? (
        <Table rows={data.states} empty="No states." columns={[
          { key: "observed_at", header: "Observed", render: (r) => fmt(r.observed_at) },
          { key: "source_id", header: "Source", render: (r) => fmt(r.source_id) },
          { key: "snapshot_completeness", header: "Completeness", render: (r) => <Badge label={String(r.snapshot_completeness ?? "")} /> },
          { key: "capture_status", header: "Capture status", render: (r) => fmt(r.capture_status) },
        ]} />
      ) : (
        <ol className="space-y-2">
          {data.transitions.length === 0 && <Empty message="No transitions." />}
          {data.transitions.map((t, i) => (
            <li key={i} className="rounded border border-slate-800 p-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="font-medium text-slate-200"><Badge label="Observed" /> {fmt(t.transition_type)}</span>
                <span className="text-xs text-slate-500">{fmt(t.detected_at)} · {fmt(t.source_id)}</span>
              </div>
              <div className="mt-1 text-xs text-slate-400">
                {fmt(t.field_name)}: {fmt(t.previous_value)} → {fmt(t.current_value)} {t.out_of_order ? <Badge label="out_of_order" /> : null}
              </div>
              <button className="mt-1 text-xs text-sky-400 hover:underline" onClick={() => open("Transition evidence", t)}>evidence</button>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function EvidenceTab({ id }: { id: string }) {
  const { data, loading, error } = useAsync(() => api.eventEvidence(id), [id]);
  const { open } = useDrawer();
  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} />;
  if (!data?.available) return <Unavailable />;
  const resolved = data.resolved_fields ?? {};
  return (
    <div className="space-y-4">
      <Card title="Field resolutions">
        <Table rows={Object.entries(resolved).map(([field, v]) => ({ field, ...(v as Record<string, unknown>) })) as Record<string, unknown>[]} empty="No resolved fields."
          columns={[
            { key: "field", header: "Field" },
            { key: "value", header: "Value", render: (r) => fmt(r.value) },
            { key: "method", header: "Method", render: (r) => fmt(r.method) },
            { key: "confidence", header: "Confidence", render: (r) => fmt(r.confidence) },
            { key: "review_status", header: "Status", render: (r) => <Badge label={String(r.review_status ?? "")} /> },
          ]} />
      </Card>
      <Card title="Candidates">
        <Table rows={data.candidates ?? []} empty="No candidates." columns={[
          { key: "field", header: "Field", render: (r) => fmt(r.field) },
          { key: "value", header: "Value", render: (r) => fmt(r.value) },
          { key: "source_type", header: "Surface", render: (r) => fmt(r.source_type) },
          { key: "confidence", header: "Confidence", render: (r) => fmt(r.confidence) },
          { key: "status", header: "Status", render: (r) => <Badge label={String(r.status ?? "")} /> },
          { key: "prov", header: "", render: (r) => <button className="text-xs text-sky-400" onClick={() => open("Candidate provenance", r)}>view</button> },
        ]} />
      </Card>
    </div>
  );
}

function EntitiesTab({ entities }: { entities: Array<Record<string, unknown>> }) {
  return (
    <Card title="Resolved entities">
      <Table rows={entities} empty="No resolved entities." columns={[
        { key: "entity_type", header: "Type", render: (r) => fmt(r.entity_type) },
        { key: "raw_name", header: "Name", render: (r) => fmt(r.raw_name) },
        { key: "canonical_entity_id", header: "Canonical", render: (r) => r.canonical_entity_id ? <Link to={`/entities/${r.entity_type}/${r.canonical_entity_id}`}>{String(r.canonical_entity_id)}</Link> : "—" },
        { key: "status", header: "Status", render: (r) => <Badge label={String(r.status ?? "")} /> },
        { key: "reason", header: "Reason", render: (r) => fmt(r.reason) },
      ]} />
    </Card>
  );
}

function RelationshipsTab({ rels }: { rels: Awaited<ReturnType<typeof api.eventDetail>>["relationships"] }) {
  const { open } = useDrawer();
  return (
    <Card title="Graph relationships (bounded local view)">
      <Table rows={rels} empty="No relationships." columns={[
        { key: "relationship", header: "Relationship", render: (r) => <span className="font-mono text-xs">{r.relationship}</span> },
        { key: "target_name", header: "Target", render: (r) => <Link to={`/graph?root=${encodeURIComponent(r.canonical_target)}`}>{r.target_name ?? r.canonical_target}</Link> },
        { key: "target_type", header: "Type" },
        { key: "resolution_status", header: "Status", render: (r) => <Badge label={r.resolution_status} /> },
        { key: "confidence", header: "Confidence", render: (r) => fmt(r.confidence) },
        { key: "insp", header: "", render: (r) => <button className="text-xs text-sky-400" onClick={() => open("Relationship", r)}>inspect</button> },
      ]} />
    </Card>
  );
}

// Phase C: read-only capture freshness. Mutation controls (capture-now / re-run) are intentionally
// not exposed in the local inspection console; those governed/operational endpoints remain on the
// BFF for developer debugging only.
function CaptureStatus({ id, d }: { id: string; d: Awaited<ReturnType<typeof api.eventDetail>> }) {
  const { data, loading } = useAsync(() => api.eventTimeline(id), [id]);
  const rec = (d.source_records?.[0] ?? {}) as Record<string, unknown>;
  const cv = d.current_view as Record<string, unknown>;
  const source = String(cv.source ?? rec.source ?? "");
  const sid = String(rec.source_record_id ?? "");
  const cur = (data?.current ?? {}) as Record<string, unknown>;
  return (
    <Card title="Capture freshness (read-only)">
      <p className="mb-3 text-xs text-slate-500">Target <span className="font-mono">{source}:{sid}</span>. This console is
        inspection-only — capture and re-run actions are not exposed here.</p>
      {loading ? <Loading /> : (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-3">
          <Field label="Last observed" value={fmt(cur.observed_at ?? cur.last_observed_at)} />
          <Field label="Capture status" value={<Badge label={String(cur.capture_status ?? "Unknown")} />} />
          <Field label="Completeness" value={<Badge label={String(cur.snapshot_completeness ?? "Unknown")} />} />
          <Field label="States" value={fmt((data?.states ?? []).length)} />
          <Field label="Transitions" value={fmt((data?.transitions ?? []).length)} />
        </dl>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ entities
export function Entities() {
  const [type, setType] = useState("");
  const [cross, setCross] = useState(false);
  const [offset, setOffset] = useState(0);
  const limit = 50;
  const { data, loading, error } = useAsync(() => api.entities({ entity_type: type, cross_source_only: cross, limit, offset }), [type, cross, offset]);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <select className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm" value={type} onChange={(e) => { setType(e.target.value); setOffset(0); }}>
          <option value="">All types</option>
          {["ARTIST", "VENUE", "ORGANIZER", "EVENT_SERIES"].map((t) => <option key={t}>{t}</option>)}
        </select>
        <label className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={cross} onChange={(e) => { setCross(e.target.checked); setOffset(0); }} /> Cross-source only</label>
        {data && <span className="text-xs text-slate-500">{data.count} entities</span>}
      </div>
      {error && <ErrorBox message={error} />}
      {loading ? <Loading /> : (
        <Card>
          <Table rows={data?.entities ?? []} columns={[
            { key: "canonical_name", header: "Name", render: (r) => <Link to={`/entities/${r.entity_type}/${r.canonical_entity_id}`}>{r.canonical_name}</Link> },
            { key: "entity_type", header: "Type" },
            { key: "identity_state", header: "Identity", render: (r) => <Badge label={r.identity_state} /> },
            { key: "linked_source_count", header: "Sources" },
            { key: "linked_event_count", header: "Events" },
            { key: "source_handles", header: "Handles" },
            { key: "ambiguous_candidate_count", header: "Ambiguous" },
            { key: "resolution_status", header: "Status", render: (r) => <Badge label={r.resolution_status} /> },
          ]} />
          <Pager offset={offset} limit={limit} count={data?.count ?? 0} onChange={setOffset} />
        </Card>
      )}
    </div>
  );
}

export function EntityDetail({ type, id }: { type: string; id: string }) {
  const { data, loading, error } = useAsync(() => api.entityDetail(type, id), [type, id]);
  const { open } = useDrawer();
  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} />;
  if (!data) return <Empty message="Not found." />;
  const dd = data as unknown as Record<string, unknown>;
  const aliases = (dd.aliases ?? []) as unknown[];
  const supersededBy = dd.superseded_by ?? dd.superseded_by_id;
  const supersedes = (dd.supersedes ?? dd.superseded_identities ?? []) as unknown[];
  const history = (dd.resolution_history ?? dd.history ?? []) as Array<Record<string, unknown>>;
  return (
    <div className="space-y-4">
      <Card title={data.canonical_name ?? id}
        right={<span className="flex items-center gap-2"><Badge label={data.identity_state} />
          <Link to={`/graph?root=${encodeURIComponent(data.canonical_entity_id)}`} className="rounded border border-slate-700 px-2 py-0.5 text-xs text-sky-400 hover:bg-slate-800">Graph neighbourhood</Link></span>}>
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-4">
          <Field label="Canonical id" value={<span className="font-mono text-xs">{data.canonical_entity_id}</span>} />
          <Field label="Type" value={data.entity_type} />
          <Field label="Sources" value={data.linked_source_count} />
          <Field label="Linked events" value={data.linked_event_count} />
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          {["CANONICAL", "ALIAS_LINKED", "LEGACY_PROJECTION", "SUPERSEDED", "POSSIBLE_DUPLICATE", "UNRESOLVED"].map((s) => (
            <span key={s} className={`rounded border px-1.5 py-0.5 ${s === data.identity_state ? "border-sky-500 text-sky-300" : "border-slate-800 text-slate-600"}`}>{s}</span>
          ))}
        </div>
        {data.legacy_projection_id && (
          <div className="mt-3 rounded border border-orange-800 bg-orange-950/30 p-2 text-xs text-orange-300">
            Possible legacy/naive-projection duplicate: <span className="font-mono">{data.legacy_projection_id}</span> (observed, not migrated).
          </div>
        )}
        {supersededBy != null && (
          <div className="mt-3 rounded border border-purple-800 bg-purple-950/30 p-2 text-xs text-purple-300">
            Superseded by <span className="font-mono">{String(supersededBy)}</span> (non-destructive; original preserved).
          </div>
        )}
      </Card>
      {(aliases.length > 0 || supersedes.length > 0 || history.length > 0) && (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card title="Aliases">
            {aliases.length === 0 ? <Empty message="None." /> :
              <ul className="space-y-1 text-xs text-slate-300">{aliases.map((a, i) => <li key={i} className="font-mono">{typeof a === "object" ? JSON.stringify(a) : String(a)}</li>)}</ul>}
          </Card>
          <Card title="Superseded identities">
            {supersedes.length === 0 ? <Empty message="None." /> :
              <ul className="space-y-1 text-xs text-slate-300">{supersedes.map((a, i) => <li key={i} className="font-mono">{typeof a === "object" ? JSON.stringify(a) : String(a)}</li>)}</ul>}
          </Card>
          <Card title="Resolution history">
            {history.length === 0 ? <Empty message="None." /> :
              <ol className="space-y-1 text-xs text-slate-400">{history.map((h, i) => <li key={i}>{fmt(h.status ?? h.new_status)} <span className="text-slate-600">{fmt(h.reason ?? h.reason_code ?? h.created_at)}</span></li>)}</ol>}
          </Card>
        </div>
      )}
      <Card title="Source handles">
        <Table rows={data.source_handles ?? []} empty="No handles." columns={[
          { key: "source", header: "Source", render: (r) => fmt(r.source) },
          { key: "handle", header: "Handle", render: (r) => <span className="font-mono text-xs">{fmt(r.handle)}</span> },
          { key: "method", header: "Method", render: (r) => fmt(r.method) },
          { key: "confidence", header: "Confidence", render: (r) => fmt(r.confidence) },
        ]} />
      </Card>
      <Card title="Linked events">
        {(data.linked_events ?? []).length === 0 ? <Empty message="None." /> : (
          <ul className="flex flex-wrap gap-2 text-sm">
            {data.linked_events.map((e) => <li key={e}><Link to={`/events/${e}`} className="rounded border border-slate-700 px-2 py-1 text-xs">{e}</Link></li>)}
          </ul>
        )}
      </Card>
      <Card title="Candidates (mentions)" right={<button className="text-xs text-sky-400" onClick={() => open("Entity candidates", data.candidates)}>raw</button>}>
        <Table rows={data.candidates ?? []} empty="None." columns={[
          { key: "source", header: "Source", render: (r) => fmt(r.source) },
          { key: "raw_name", header: "Raw", render: (r) => fmt(r.raw_name) },
          { key: "status", header: "Status", render: (r) => <Badge label={String(r.status ?? "")} /> },
          { key: "reason", header: "Reason", render: (r) => fmt(r.reason) },
        ]} />
      </Card>
    </div>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return <div><dt className="text-xs uppercase text-slate-500">{label}</dt><dd className="text-slate-200">{value}</dd></div>;
}

// ------------------------------------------------------------------ graph explorer (bounded)
const REL_TYPES = ["FEATURES", "OCCURS_AT", "ORGANIZED_BY", "PART_OF_SERIES", "IN_REGION", "IDENTIFIES", "REPRESENTED_BY"];
const TYPE_COLORS: Record<string, string> = {
  event: "#38bdf8", artist: "#a78bfa", venue: "#34d399", organizer: "#fbbf24",
  event_series: "#f472b6", region: "#22d3ee", source_handle: "#94a3b8", unknown: "#64748b",
};

const NODE_TYPES = ["event", "artist", "venue", "organizer", "event_series", "region", "source_handle"];

export function Graph({ initialRoot }: { initialRoot?: string }) {
  const [root, setRoot] = useState(initialRoot ?? "");
  const [query, setQuery] = useState(initialRoot ?? "");
  const [depth, setDepth] = useState(1);
  const [rels, setRels] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [srcFilter, setSrcFilter] = useState("");
  const { data, loading, error } = useAsync<Subgraph | null>(() => (root ? api.subgraph(root, depth, rels.join(",")) : Promise.resolve(null)), [root, depth, rels.join(",")]);
  const { open } = useDrawer();
  // client-side entity-type + source-substring filtering (keeps root; drops edges to hidden nodes)
  const view = data ? filterSubgraph(data, types, srcFilter) : null;
  return (
    <div className="space-y-4">
      <Card title="Graph explorer (bounded)">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="mb-1 block text-slate-400">Root node id</span>
            <input className="w-80 rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm font-mono" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="event:… / artist:… / venue:…" onKeyDown={(e) => e.key === "Enter" && setRoot(query.trim())} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-slate-400">Depth (hops)</span>
            <select className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm" value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
              {[0, 1, 2].map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-slate-400">Source contains</span>
            <input className="w-28 rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm" value={srcFilter} onChange={(e) => setSrcFilter(e.target.value)} placeholder="boshow…" />
          </label>
          <button className="rounded bg-sky-600 px-3 py-1.5 text-sm hover:bg-sky-500" onClick={() => setRoot(query.trim())}>Load</button>
          <button className="rounded border border-slate-700 px-3 py-1.5 text-sm" onClick={() => { setRoot(""); setQuery(""); setTypes([]); setRels([]); setSrcFilter(""); }}>Reset</button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          <span className="text-slate-500">Relationships:</span>
          {REL_TYPES.map((r) => (
            <label key={r} className={`cursor-pointer rounded border px-2 py-0.5 ${rels.includes(r) ? "border-sky-500 text-sky-300" : "border-slate-700 text-slate-400"}`}>
              <input type="checkbox" className="hidden" checked={rels.includes(r)} onChange={() => setRels((cur) => cur.includes(r) ? cur.filter((x) => x !== r) : [...cur, r])} />{r}
            </label>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap gap-2 text-xs">
          <span className="text-slate-500">Node types:</span>
          {NODE_TYPES.map((t) => (
            <label key={t} className={`cursor-pointer rounded border px-2 py-0.5 ${types.includes(t) ? "border-emerald-500 text-emerald-300" : "border-slate-700 text-slate-400"}`}>
              <input type="checkbox" className="hidden" checked={types.includes(t)} onChange={() => setTypes((cur) => cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t])} />{t}
            </label>
          ))}
        </div>
      </Card>
      {error && <ErrorBox message={error} />}
      {loading && <Loading />}
      {data?.capped && (
        <div className="rounded border border-amber-800 bg-amber-950/30 p-2 text-xs text-amber-300">
          Result capped at the server node limit ({data.max_nodes}) — this is a bounded local view, not the whole graph. Narrow with filters or a deeper root.
        </div>
      )}
      {view && (
        <Card title={`Subgraph — ${view.nodes.length} nodes, ${view.edges.length} edges${data?.capped ? " · capped" : ""}${(types.length || srcFilter) ? " · filtered" : ""}`}>
          {view.nodes.length === 0 ? <Empty message="No nodes match the current filters." /> :
            <SubgraphSVG data={view} onNode={(id) => { setRoot(id); setQuery(id); }} onEdge={(e) => open("Relationship", e)} />}
        </Card>
      )}
      {!root && !loading && <Empty message="Enter a node id (e.g. an event or artist canonical id) and Load." />}
    </div>
  );
}

function filterSubgraph(data: Subgraph, types: string[], srcFilter: string): Subgraph {
  const src = srcFilter.trim().toLowerCase();
  const keep = (nid: string, type: string) =>
    (nid === data.root) ||
    ((types.length === 0 || types.includes(type)) && (!src || nid.toLowerCase().includes(src)));
  const nodes = data.nodes.filter((n) => keep(n.id, n.type));
  const ids = new Set(nodes.map((n) => n.id));
  const edges = data.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  return { ...data, nodes, edges };
}

function SubgraphSVG({ data, onNode, onEdge }: { data: Subgraph; onNode: (id: string) => void; onEdge: (e: unknown) => void }) {
  const W = 760, H = 460, cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 60;
  const n = data.nodes.length;
  const pos: Record<string, { x: number; y: number }> = {};
  data.nodes.forEach((node, i) => {
    if (node.id === data.root) { pos[node.id] = { x: cx, y: cy }; return; }
    const angle = (2 * Math.PI * i) / Math.max(1, n - (data.nodes.some((x) => x.id === data.root) ? 1 : 0));
    pos[node.id] = { x: cx + R * Math.cos(angle), y: cy + R * Math.sin(angle) };
  });
  return (
    <div className="overflow-x-auto">
      <svg width={W} height={H} className="min-w-full">
        {data.edges.map((e, i) => {
          const a = pos[e.source], b = pos[e.target];
          if (!a || !b) return null;
          return (
            <g key={i} className="cursor-pointer" onClick={() => onEdge(e)}>
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#334155" strokeWidth={1.2} />
              <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2} fontSize={8} fill="#64748b" textAnchor="middle">{e.relationship}</text>
            </g>
          );
        })}
        {data.nodes.map((node) => {
          const p = pos[node.id];
          return (
            <g key={node.id} transform={`translate(${p.x},${p.y})`} className="cursor-pointer" onClick={() => onNode(node.id)}>
              <circle r={node.id === data.root ? 11 : 7} fill={TYPE_COLORS[node.type] ?? TYPE_COLORS.unknown} stroke="#0f172a" strokeWidth={2} />
              <text y={-12} fontSize={9} fill="#cbd5e1" textAnchor="middle">{(node.label ?? node.id).slice(0, 22)}</text>
              <title>{`${node.type}: ${node.id}`}</title>
            </g>
          );
        })}
      </svg>
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-400">
        {Object.entries(TYPE_COLORS).map(([t, c]) => <span key={t} className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full" style={{ background: c }} />{t}</span>)}
      </div>
    </div>
  );
}
