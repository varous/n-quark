import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

// ---- hash router -------------------------------------------------------------------------------
export function useHashRoute(): [string, (to: string) => void] {
  const [hash, setHash] = useState(() => window.location.hash.replace(/^#/, "") || "/overview");
  useEffect(() => {
    const on = () => setHash(window.location.hash.replace(/^#/, "") || "/overview");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const nav = (to: string) => {
    window.location.hash = to;
  };
  return [hash, nav];
}

export function Link({ to, children, className }: { to: string; children: ReactNode; className?: string }) {
  return (
    <a href={`#${to}`} className={className ?? "text-sky-400 hover:underline"}>
      {children}
    </a>
  );
}

// ---- async hook --------------------------------------------------------------------------------
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; loading: boolean; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e.message ?? String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, loading, error, reload: () => setTick((t) => t + 1) };
}

// ---- epistemic labels (never colour alone: each has a text label) ------------------------------
const EPI: Record<string, string> = {
  Observed: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  Derived: "bg-indigo-500/15 text-indigo-300 border-indigo-500/30",
  Resolved: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  RESOLVED: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  Estimated: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  Unknown: "bg-slate-500/15 text-slate-300 border-slate-500/30",
  Ambiguous: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  AMBIGUOUS: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  Conflicting: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  CONFLICT: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  Stale: "bg-orange-500/15 text-orange-300 border-orange-500/30",
  Failed: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  POSSIBLE_MATCH: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  POSSIBLE_DUPLICATE: "bg-orange-500/15 text-orange-300 border-orange-500/30",
  UNRESOLVED: "bg-slate-500/15 text-slate-300 border-slate-500/30",
  CANONICAL: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  ALIAS_LINKED: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  LEGACY_PROJECTION: "bg-orange-500/15 text-orange-300 border-orange-500/30",
};
export function Badge({ label }: { label: string | null | undefined }) {
  if (!label) return <span className="text-slate-500">—</span>;
  const cls = EPI[label] ?? "bg-slate-600/20 text-slate-300 border-slate-600/40";
  return <span className={`inline-block rounded border px-1.5 py-0.5 text-xs font-medium ${cls}`}>{label}</span>;
}

// ---- primitives --------------------------------------------------------------------------------
export function Card({ title, children, right }: { title?: string; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60">
      {title && (
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-2.5">
          <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
          {right}
        </div>
      )}
      <div className="p-4">{children}</div>
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
      <div className="text-2xl font-semibold tabular-nums text-slate-100">{value}</div>
      <div className="mt-1 text-xs uppercase tracking-wide text-slate-400">{label}</div>
      {hint && <div className="mt-0.5 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

export function Table<T>({ columns, rows, empty }: { columns: { key: string; header: string; render?: (r: T) => ReactNode }[]; rows: T[]; empty?: string }) {
  if (!rows.length) return <Empty message={empty ?? "No rows."} />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-400">
            {columns.map((c) => (
              <th key={c.key} className="px-3 py-2 font-medium">{c.header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-slate-800/60 hover:bg-slate-800/30">
              {columns.map((c) => (
                <td key={c.key} className="px-3 py-2 align-top text-slate-200">
                  {c.render ? c.render(r) : String((r as Record<string, unknown>)[c.key] ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Loading({ label }: { label?: string }) {
  return (
    <div className="animate-pulse space-y-2 p-4">
      <div className="h-4 w-1/3 rounded bg-slate-800" />
      <div className="h-24 rounded bg-slate-800/60" />
      <div className="sr-only">{label ?? "Loading"}</div>
    </div>
  );
}
export function Empty({ message }: { message: string }) {
  return <div className="p-6 text-center text-sm text-slate-500">{message}</div>;
}
export function ErrorBox({ message }: { message: string }) {
  return <div className="rounded border border-rose-800 bg-rose-950/40 p-4 text-sm text-rose-300">{message}</div>;
}
export function Unavailable() {
  return (
    <div className="rounded border border-amber-800 bg-amber-950/30 p-3 text-xs text-amber-300">
      Downstream service unavailable or disabled — showing partial data.
    </div>
  );
}

// ---- provenance drawer (reusable) --------------------------------------------------------------
type DrawerState = { title: string; data: unknown } | null;
const DrawerCtx = createContext<{ open: (title: string, data: unknown) => void }>({ open: () => {} });
export const useDrawer = () => useContext(DrawerCtx);

export function DrawerProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<DrawerState>(null);
  return (
    <DrawerCtx.Provider value={{ open: (title, data) => setState({ title, data }) }}>
      {children}
      {state && (
        <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-label="Provenance">
          <div className="flex-1 bg-black/50" onClick={() => setState(null)} />
          <div className="w-[32rem] max-w-full overflow-y-auto border-l border-slate-800 bg-slate-950 p-5">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-100">{state.title}</h3>
              <button className="rounded px-2 py-1 text-slate-400 hover:bg-slate-800" onClick={() => setState(null)}>✕</button>
            </div>
            <p className="mb-2 text-xs text-slate-500">Raw third-party payloads and retained HTML are never shown here.</p>
            <pre className="whitespace-pre-wrap break-words rounded bg-slate-900 p-3 text-xs text-slate-300">
              {JSON.stringify(state.data, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </DrawerCtx.Provider>
  );
}

export const fmt = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));
