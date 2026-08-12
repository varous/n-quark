import { useState } from "react";
import { api, ApiError, type BulkPreview, type WatchTarget } from "./api";
import { Card, Empty, ErrorBox, Loading, Section, StatTile, Unavailable, relTime, useAsync } from "./ui";

// ================================================================================================
// Artists / Watchlist (Phase 5B.1) — the operator's research-configuration surface.
// "Start watching this artist" without needing ids, SQL, or provider knowledge. Adding a target does
// NOT create a canonical artist; n-quark tries to match/resolve it from available evidence. Human-
// readable states lead; the raw lifecycle enum lives in the Evidence area.
// ================================================================================================

const STATE_TONE: Record<string, string> = {
  Watching: "border-emerald-600/40 bg-emerald-500/10 text-emerald-300",
  "Finding YouTube identity": "border-brand-600/40 bg-brand-500/10 text-brand-200",
  "Needs review": "border-amber-600/40 bg-amber-500/10 text-amber-300",
  "Waiting for stronger evidence": "border-slate-600/50 bg-slate-500/10 text-slate-300",
  Queued: "border-brand-600/40 bg-brand-500/10 text-brand-200",
  Paused: "border-slate-600/50 bg-slate-700/30 text-slate-400",
  Removed: "border-slate-700 bg-slate-800/40 text-slate-500",
};

function StateChip({ label }: { label: string }) {
  const tone = STATE_TONE[label] ?? "border-slate-600/50 bg-slate-500/10 text-slate-300";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-medium ${tone}`}>
      {label}
    </span>
  );
}

// ---- explainer: the single most important thing an operator must understand --------------------
function Explainer() {
  return (
    <div className="mb-6 rounded-xl border border-brand-800/50 bg-brand-950/20 p-4 text-sm text-slate-300">
      <p className="font-medium text-slate-200">Adding an artist to the watchlist does not create a canonical artist.</p>
      <p className="mt-1 text-slate-400">
        n-quark records your research instruction, then attempts to <span className="text-slate-300">match or resolve</span> the
        artist using available evidence — an existing canonical match, or independent evidence through the normal
        promotion path. A pasted YouTube link is a hint that is still <span className="text-slate-300">provider-verified</span> before
        it is trusted. Artists without sufficient evidence stay pending rather than being fabricated.
      </p>
    </div>
  );
}

// ---- add one artist ----------------------------------------------------------------------------
function AddOne({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "ok" | "err"; text: string } | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      const out = await api.watchlistAdd({ display_name: name.trim(), youtube_hint: url.trim() || undefined });
      const t = out.target;
      setMsg({ tone: "ok", text: `${out.created ? "Added" : "Already watching"} “${t.display_name}” — ${t.human_state}.` });
      setName("");
      setUrl("");
      onDone();
    } catch (err) {
      setMsg({ tone: "err", text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Add an artist">
      <form onSubmit={submit} className="space-y-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-slate-400">Artist name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Anuv Jain"
            className="w-full rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-brand-500" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-slate-400">YouTube channel or video URL <span className="text-slate-600">(optional hint)</span></label>
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://youtube.com/@… or /channel/… or a video URL"
            className="w-full rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-brand-500" />
        </div>
        <div className="flex items-center gap-3">
          <button type="submit" disabled={busy || !name.trim()}
            className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-400 disabled:opacity-50">
            {busy ? "Adding…" : "Add to watchlist"}
          </button>
          {msg && <span className={`text-xs ${msg.tone === "ok" ? "text-emerald-400" : "text-rose-400"}`}>{msg.text}</span>}
        </div>
      </form>
    </Card>
  );
}

// ---- bulk add ----------------------------------------------------------------------------------
function AddBulk({ onDone }: { onDone: () => void }) {
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<BulkPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function runPreview() {
    if (!text.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      setPreview(await api.watchlistBulkPreview(text));
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    setBusy(true);
    setMsg(null);
    try {
      const out = await api.watchlistBulkAdd(text);
      setMsg(`Created ${out.created} new, ${out.existing} already present.`);
      setText("");
      setPreview(null);
      onDone();
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const DISP: Record<string, { label: string; cls: string }> = {
    NEW: { label: "new", cls: "text-emerald-400" },
    MATCHES_CANONICAL: { label: "matches existing artist", cls: "text-brand-300" },
    DUPLICATE: { label: "already on watchlist", cls: "text-slate-500" },
  };

  return (
    <Card title="Bulk add">
      <p className="mb-2 text-xs text-slate-500">One artist per line. Preview shows existing matches and duplicates before anything is created.</p>
      <textarea value={text} onChange={(e) => { setText(e.target.value); setPreview(null); }} rows={5}
        placeholder={"Anuv Jain\nPrateek Kuhad\nHanumankind\nPeter Cat Recording Co."}
        className="w-full rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-brand-500" />
      <div className="mt-3 flex items-center gap-3">
        {!preview ? (
          <button onClick={runPreview} disabled={busy || !text.trim()}
            className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50">
            {busy ? "Checking…" : "Preview"}
          </button>
        ) : (
          <>
            <button onClick={confirm} disabled={busy || preview.new === 0}
              className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white hover:bg-brand-400 disabled:opacity-50">
              {busy ? "Adding…" : `Add ${preview.new} artist${preview.new === 1 ? "" : "s"}`}
            </button>
            <button onClick={() => setPreview(null)} className="text-xs text-slate-400 hover:text-slate-200">Edit list</button>
          </>
        )}
        {msg && <span className="text-xs text-slate-400">{msg}</span>}
      </div>
      {preview && (
        <div className="mt-3 max-h-56 overflow-y-auto rounded-lg border border-slate-800">
          <table className="w-full text-sm">
            <tbody>
              {preview.items.map((it, i) => (
                <tr key={i} className="border-b border-slate-800/60 last:border-0">
                  <td className="px-3 py-1.5 text-slate-200">{it.display_name}</td>
                  <td className={`px-3 py-1.5 text-right text-xs ${DISP[it.disposition]?.cls ?? "text-slate-400"}`}>{DISP[it.disposition]?.label ?? it.disposition}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

// ---- one target row ----------------------------------------------------------------------------
function TargetRow({ t, onChange }: { t: WatchTarget; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try { await fn(); onChange(); } finally { setBusy(false); }
  }
  const facts: string[] = [];
  if (t.status === "WATCHING") {
    if (t.youtube_identity_state === "RESOLVED") facts.push("Monitoring YouTube");
    if (t.videos_tracked > 0) facts.push(`${t.videos_tracked} video${t.videos_tracked === 1 ? "" : "s"} tracked`);
    if (t.last_observed_at) facts.push(`Last checked ${relTime(t.last_observed_at)}`);
  }
  return (
    <div className="border-b border-slate-800/70 last:border-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            <span className="truncate text-sm font-medium text-slate-100">{t.display_name}</span>
            <StateChip label={t.human_state} />
          </div>
          <div className="mt-0.5 truncate text-xs text-slate-500">
            {facts.length > 0 ? facts.join(" · ") : (t.canonical_artist_id ?? "no canonical match yet")}
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          {t.status === "PAUSED" ? (
            <button disabled={busy} onClick={() => act(() => api.watchlistResume(t.id))}
              className="rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-50">Resume</button>
          ) : t.status !== "REJECTED" && (
            <button disabled={busy} onClick={() => act(() => api.watchlistPause(t.id))}
              className="rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-50">Pause</button>
          )}
          {t.status !== "REJECTED" && (
            <button disabled={busy} onClick={() => act(() => api.watchlistPriority(t.id, t.priority <= 20 ? 40 : 10))}
              className="rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-50">
              {t.priority <= 20 ? "Normal priority" : "Prioritize"}
            </button>
          )}
          <button onClick={() => setOpen((o) => !o)}
            className="rounded-md px-2 py-1 text-xs text-slate-500 hover:text-slate-300">{open ? "Hide" : "Evidence"}</button>
        </div>
      </div>
      {open && (
        <div className="border-t border-slate-800/70 bg-slate-950/40 px-4 py-3 text-xs">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3">
            <div><dt className="text-slate-500">Lifecycle status</dt><dd className="text-slate-300">{t.status}</dd></div>
            <div><dt className="text-slate-500">Canonical artist</dt><dd className="truncate text-slate-300">{t.canonical_artist_id ?? "—"}</dd></div>
            <div><dt className="text-slate-500">YouTube identity</dt><dd className="text-slate-300">{t.youtube_identity_state ?? "—"}</dd></div>
            <div><dt className="text-slate-500">Resolution method</dt><dd className="truncate text-slate-300">{t.resolution_method ?? "—"}</dd></div>
            <div><dt className="text-slate-500">Added by</dt><dd className="truncate text-slate-300">{t.created_by}</dd></div>
            <div><dt className="text-slate-500">Priority</dt><dd className="text-slate-300">{t.priority}</dd></div>
          </dl>
          {t.canonical_artist_id && (
            <a href={`#/artists/${encodeURIComponent(t.canonical_artist_id)}`}
              className="mt-2 inline-block text-brand-300 hover:text-brand-200">Open artist →</a>
          )}
        </div>
      )}
    </div>
  );
}

const BUCKETS: Array<{ title: string; match: (t: WatchTarget) => boolean }> = [
  { title: "Watching", match: (t) => t.status === "WATCHING" },
  { title: "Needs review", match: (t) => t.status === "AMBIGUOUS" },
  { title: "Waiting for stronger evidence", match: (t) => t.status === "RESOLUTION_PENDING" || t.status === "NEW" },
  { title: "Paused", match: (t) => t.status === "PAUSED" },
];

export function Watchlist() {
  const [tick, setTick] = useState(0);
  const reloadAll = () => setTick((n) => n + 1);
  const list = useAsync(() => api.watchlist({ limit: 500 }), [tick]);
  const diag = useAsync(() => api.watchlistDiagnostics(), [tick]);

  return (
    <Section title="Artists · Watchlist"
      subtitle="Tell n-quark which artists to watch. Targets are resolved and, where justified, enrolled into demand monitoring — no ids or SQL required.">
      <Explainer />

      <div className="mb-6 grid gap-4 lg:grid-cols-2">
        <AddOne onDone={reloadAll} />
        <AddBulk onDone={reloadAll} />
      </div>

      {diag.data && diag.data.available !== false && (
        <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <StatTile label="Total" value={diag.data.total ?? 0} />
          <StatTile label="Watching" value={diag.data.watching ?? 0} tone="good" />
          <StatTile label="Pending" value={diag.data.resolution_pending ?? 0} tone="neutral" />
          <StatTile label="Needs review" value={diag.data.ambiguous ?? 0} tone="warn" />
          <StatTile label="Verified YouTube" value={diag.data.targets_with_verified_youtube_identity ?? 0} tone="brand" />
          <StatTile label="Receiving demand" value={diag.data.targets_receiving_demand_observations ?? 0} tone="good" />
        </div>
      )}

      {list.loading && <Loading label="Loading watchlist…" />}
      {list.error && <ErrorBox message={list.error} />}
      {list.data && list.data.available === false && <Unavailable what="the watchlist service" />}
      {list.data && list.data.available !== false && (
        list.data.targets.filter((t) => t.status !== "REJECTED").length === 0 ? (
          <Empty message="No artists watched yet. Add one above to begin." />
        ) : (
          <div className="space-y-6">
            {BUCKETS.map((b) => {
              const rows = list.data!.targets.filter(b.match);
              if (rows.length === 0) return null;
              return (
                <div key={b.title}>
                  <div className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-widest text-slate-500">
                    {b.title} <span className="rounded-full bg-slate-800 px-1.5 py-0.5 text-[10px] tabular-nums text-slate-400">{rows.length}</span>
                  </div>
                  <Card>
                    {rows.map((t) => <TargetRow key={t.id} t={t} onChange={reloadAll} />)}
                  </Card>
                </div>
              );
            })}
          </div>
        )
      )}
    </Section>
  );
}
