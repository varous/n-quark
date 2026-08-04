import { useState } from "react";
import { api, ApiError } from "./api";
import { useAuth } from "./auth";
import { Badge, Card, Empty, ErrorBox, Loading, useAsync, fmt } from "./ui";

const REJECT_REASONS = ["WRONG_ENTITY", "TRIBUTE_OR_COVER", "DIFFERENT_CITY", "DIFFERENT_VENUE",
  "GENERIC_NAME", "SOURCE_DATA_ERROR", "DUPLICATE_CANDIDATE", "OTHER"];

type Candidate = Record<string, unknown>;

export function Resolution() {
  const [type, setType] = useState("");
  const [selected, setSelected] = useState<Candidate | null>(null);
  const { data, loading, error, reload } = useAsync(() => api.resolutionQueue({ entity_type: type, limit: 100 }), [type]);
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-semibold text-slate-200">Resolution Workbench</h2>
        <select className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm" value={type} onChange={(e) => { setType(e.target.value); setSelected(null); }}>
          <option value="">All types</option>
          {["ARTIST", "VENUE", "ORGANIZER", "EVENT_SERIES"].map((t) => <option key={t}>{t}</option>)}
        </select>
        {data && <span className="text-xs text-slate-500">{data.count} in queue</span>}
      </div>
      {error && <ErrorBox message={error} />}
      {loading ? <Loading /> : (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card title="Queue">
            {(data?.items ?? []).length === 0 ? <Empty message="Queue is clear." /> : (
              <ul className="space-y-1">
                {data!.items.map((c) => (
                  <li key={String(c.id)}>
                    <button onClick={() => setSelected(c)}
                      className={`w-full rounded px-2 py-1.5 text-left text-sm ${selected?.id === c.id ? "bg-sky-600/20 text-sky-200" : "hover:bg-slate-800"}`}>
                      <div className="flex items-center justify-between">
                        <span className="truncate">{fmt(c.raw_name)}</span>
                        <Badge label={String(c.status)} />
                      </div>
                      <div className="text-xs text-slate-500">{fmt(c.entity_type)} · {fmt(c.source)} · {fmt(c.reason)}</div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          {selected ? <SourceEvidence c={selected} /> : <Card title="Source evidence"><Empty message="Select a candidate." /></Card>}
          {selected ? <DecisionPane key={String(selected.id)} c={selected} onDone={() => { setSelected(null); reload(); }} /> : <Card title="Decision & impact"><Empty message="Select a candidate." /></Card>}
        </div>
      )}
    </div>
  );
}

function SourceEvidence({ c }: { c: Candidate }) {
  const { data } = useAsync(() => api.candidate(String(c.id)), [c.id]);
  const ev = (data?.evidence ?? c.evidence ?? {}) as Record<string, unknown>;
  return (
    <Card title="Source evidence">
      <dl className="space-y-1.5 text-sm">
        <Row k="Raw value" v={fmt(c.raw_name)} />
        <Row k="Normalized" v={fmt(c.normalized_name)} />
        <Row k="Source" v={fmt(c.source)} />
        <Row k="Handle" v={<span className="font-mono text-xs">{fmt(c.handle)}</span>} />
        <Row k="Event" v={fmt(c.canonical_event_id)} />
        <Row k="Status" v={<Badge label={String(c.status)} />} />
        <Row k="Reason" v={fmt(c.reason)} />
        {"city" in ev && <Row k="City" v={fmt(ev.city)} />}
      </dl>
      {Array.isArray(data?.history) && data!.history.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-xs uppercase text-slate-500">Resolution history</div>
          <ul className="space-y-1 text-xs text-slate-400">
            {(data!.history as Array<Record<string, unknown>>).map((h, i) => (
              <li key={i}>{fmt(h.previous_status)} → <span className="text-slate-200">{fmt(h.new_status)}</span> ({fmt(h.reason)})</li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-3"><dt className="text-slate-500">{k}</dt><dd className="text-right text-slate-200">{v}</dd></div>;
}

function DecisionPane({ c, onDone }: { c: Candidate; onDone: () => void }) {
  const { can } = useAuth();
  const [target, setTarget] = useState("");
  const [newName, setNewName] = useState(String(c.raw_name ?? ""));
  const [city, setCity] = useState("");
  const [reason, setReason] = useState(REJECT_REASONS[0]);
  const [impact, setImpact] = useState<Record<string, unknown> | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canWrite = can("ANALYST");

  async function run(fn: () => Promise<unknown>, label: string) {
    setBusy(true); setMsg(null);
    try {
      const r = (await fn()) as Record<string, unknown>;
      if (r.already_applied) setMsg("Already applied (idempotent).");
      else setMsg(`${label} recorded · decision ${(r.decision as Record<string, unknown>)?.id?.toString().slice(0, 8) ?? ""}`);
      setTimeout(onDone, 700);
    } catch (e) {
      setMsg(e instanceof ApiError ? `Conflict/error: ${e.message}` : String(e));
    } finally { setBusy(false); }
  }

  async function preview(action: string, extra: Record<string, unknown> = {}) {
    setBusy(true); setMsg(null);
    try {
      setImpact(await api.gov("preview", { action, candidate_id: c.id, ...extra }));
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  }

  if (!canWrite) return <Card title="Decision & impact"><Empty message="ANALYST role required to submit decisions." /></Card>;

  return (
    <Card title="Decision & impact">
      <div className="space-y-3 text-sm">
        <div>
          <label className="mb-1 block text-xs text-slate-500">Target canonical id (accept / link)</label>
          <input className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 font-mono text-xs" value={target}
            onChange={(e) => setTarget(e.target.value)} placeholder={`${String(c.entity_type).toLowerCase()}:…`} />
          <div className="mt-2 flex flex-wrap gap-2">
            <button disabled={busy || !target} className="rounded border border-slate-700 px-2 py-1 text-xs disabled:opacity-40"
              onClick={() => preview("ACCEPT_CANDIDATE", { canonical_entity_id: target })}>Preview</button>
            <button disabled={busy || !target} className="rounded bg-sky-600 px-2 py-1 text-xs hover:bg-sky-500 disabled:opacity-40"
              onClick={() => run(() => api.gov("accept", { candidate_id: c.id, canonical_entity_id: target, expected_status: c.status }), "Accept")}>Accept</button>
            <button disabled={busy || !target} className="rounded border border-slate-600 px-2 py-1 text-xs disabled:opacity-40"
              onClick={() => run(() => api.gov("link-handle", { candidate_id: c.id, canonical_entity_id: target }), "Link handle")}>Link handle</button>
          </div>
        </div>

        <div className="border-t border-slate-800 pt-3">
          <label className="mb-1 block text-xs text-slate-500">Create new canonical entity</label>
          <input className="mb-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="canonical name" />
          {c.entity_type === "VENUE" && <input className="mb-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs" value={city} onChange={(e) => setCity(e.target.value)} placeholder="city (required for venue)" />}
          <button disabled={busy || !newName} className="rounded bg-emerald-600 px-2 py-1 text-xs hover:bg-emerald-500 disabled:opacity-40"
            onClick={() => run(() => api.gov("create-entity", { entity_type: c.entity_type, canonical_name: newName, candidate_id: c.id, city: city || undefined, reason: "manual create" }), "Create")}>Create entity</button>
        </div>

        <div className="border-t border-slate-800 pt-3">
          <label className="mb-1 block text-xs text-slate-500">Reject / keep unresolved</label>
          <select className="mb-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs" value={reason} onChange={(e) => setReason(e.target.value)}>
            {REJECT_REASONS.map((r) => <option key={r}>{r}</option>)}
          </select>
          <div className="flex gap-2">
            <button disabled={busy} className="rounded border border-rose-800 px-2 py-1 text-xs text-rose-300 disabled:opacity-40"
              onClick={() => run(() => api.gov("reject", { candidate_id: c.id, reason_code: reason }), "Reject")}>Reject</button>
            <button disabled={busy} className="rounded border border-slate-700 px-2 py-1 text-xs disabled:opacity-40"
              onClick={() => run(() => api.gov("mark-unresolved", { candidate_id: c.id }), "Mark unresolved")}>Mark unresolved</button>
          </div>
        </div>

        {impact && (
          <div className="rounded border border-slate-700 bg-slate-900 p-2 text-xs">
            <div className="mb-1 font-medium text-slate-300">Impact preview</div>
            <div className="text-slate-400">proposed → <span className="font-mono">{fmt(impact.proposed_canonical_target)}</span></div>
            <div className="text-slate-400">events affected: {(impact.events_affected as unknown[])?.length ?? 0} · conflicting: {(impact.possible_conflicting_candidates as unknown[])?.length ?? 0}</div>
            <div className="text-slate-400">scheduler change: {String(impact.scheduler_metadata_change)} · dup-event reconcile affected: {String(impact.duplicate_event_reconciliation_affected)}</div>
            <div className="text-emerald-400">source evidence retained: {String(impact.source_evidence_retained)}</div>
          </div>
        )}
        {msg && <div className="rounded border border-slate-700 bg-slate-950 p-2 text-xs text-slate-300">{msg}</div>}
      </div>
    </Card>
  );
}
