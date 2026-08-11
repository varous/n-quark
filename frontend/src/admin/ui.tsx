import {
  createContext, useContext, useEffect, useRef, useState,
  type CSSProperties, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes,
} from "react";

// ================================================================================================
// Shared UI primitives for the n-quark console. Every export the existing screens rely on is kept
// with the same signature; the visuals are the redesigned system (see index.css tokens).
// ================================================================================================

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
    <a href={`#${to}`} className={className ?? "text-brand-300 hover:text-brand-400 hover:underline underline-offset-2"}>
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

// ---- epistemic / status labels (colour never alone: each carries its text) ---------------------
const EPI: Record<string, string> = {
  Observed: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  Derived: "bg-violet-500/15 text-violet-300 border-violet-500/30",
  Resolved: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  RESOLVED: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  Estimated: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  Unknown: "bg-slate-500/15 text-slate-300 border-slate-500/30",
  Ambiguous: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  AMBIGUOUS: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  Conflicting: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  CONFLICT: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  Stale: "bg-orange-500/15 text-orange-300 border-orange-500/30",
  STALE: "bg-orange-500/15 text-orange-300 border-orange-500/30",
  Failed: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  FAILED: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  FAILED_TERMINAL: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  FAILED_RETRYABLE: "bg-orange-500/15 text-orange-300 border-orange-500/30",
  PENDING: "bg-slate-500/15 text-slate-300 border-slate-500/30",
  RUNNING: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  SUCCEEDED: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  POSSIBLE_MATCH: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  POSSIBLE_DUPLICATE: "bg-orange-500/15 text-orange-300 border-orange-500/30",
  UNRESOLVED: "bg-slate-500/15 text-slate-300 border-slate-500/30",
  CANONICAL: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  ALIAS_LINKED: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  LEGACY_PROJECTION: "bg-orange-500/15 text-orange-300 border-orange-500/30",
  SUPERSEDED: "bg-violet-500/15 text-violet-300 border-violet-500/30",
  LOW_CONFIDENCE: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  PARTIAL: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  NONE: "bg-slate-500/15 text-slate-400 border-slate-500/30",
  SUCCESS: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  SUCCESS_RECORD_PRESENT: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  SUCCESS_RECORD_ABSENT: "bg-slate-500/15 text-slate-300 border-slate-500/30",
  SOURCE_UNAVAILABLE: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  TIMEOUT: "bg-orange-500/15 text-orange-300 border-orange-500/30",
  RATE_LIMITED: "bg-orange-500/15 text-orange-300 border-orange-500/30",
  ACTIVE: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  ok: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  degraded: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  error: "bg-rose-500/15 text-rose-300 border-rose-500/30",
};
export function Badge({ label }: { label: string | null | undefined }) {
  if (label === null || label === undefined || label === "") return <span className="text-slate-500">—</span>;
  const cls = EPI[label] ?? "bg-slate-600/25 text-slate-300 border-slate-600/40";
  return <span className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${cls}`}>{label}</span>;
}

// ---- layout primitives -------------------------------------------------------------------------
export function Card({ title, children, right, className }: { title?: string; children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-slate-800 bg-slate-900/50 shadow-sm shadow-black/20 ${className ?? ""}`}>
      {title && (
        <div className="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-2.5">
          <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
          {right}
        </div>
      )}
      <div className="p-4">{children}</div>
    </div>
  );
}

export function Section({ title, subtitle, actions, children }: { title: string; subtitle?: ReactNode; actions?: ReactNode; children?: ReactNode }) {
  return (
    <section className="mb-7">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-slate-100" style={{ textWrap: "balance" } as CSSProperties}>{title}</h2>
          {subtitle && <p className="mt-0.5 max-w-2xl text-sm text-slate-400">{subtitle}</p>}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
      <div className="text-2xl font-semibold tabular-nums text-slate-100">{value}</div>
      <div className="mt-1 text-xs uppercase tracking-wide text-slate-400">{label}</div>
      {hint && <div className="mt-0.5 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

// Richer KPI tile: big number, caption, optional trend spark + delta, optional accent stripe + link.
export function StatTile(props: {
  label: string; value: ReactNode; caption?: ReactNode; series?: number[];
  tone?: "neutral" | "good" | "warn" | "bad" | "brand"; to?: string;
}) {
  const tone = props.tone ?? "neutral";
  const stripe = {
    neutral: "before:bg-slate-600", good: "before:bg-emerald-500", warn: "before:bg-amber-500",
    bad: "before:bg-rose-500", brand: "before:bg-brand-500",
  }[tone];
  const inner = (
    <div className={`relative overflow-hidden rounded-xl border border-slate-800 bg-slate-900/50 p-4 pl-5 transition-colors hover:border-slate-700 before:absolute before:inset-y-0 before:left-0 before:w-1 ${stripe}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-[11px] font-medium uppercase tracking-wide text-slate-400">{props.label}</div>
          <div className="mt-1 text-[26px] font-semibold leading-none tabular-nums text-slate-50">{props.value}</div>
        </div>
        {props.series && props.series.length > 1 && <Sparkline data={props.series} tone={tone} />}
      </div>
      {props.caption && <div className="mt-2 text-xs text-slate-500">{props.caption}</div>}
    </div>
  );
  return props.to ? <a href={`#${props.to}`} className="block">{inner}</a> : inner;
}

export function Sparkline({ data, tone = "brand", width = 76, height = 26 }: { data: number[]; tone?: string; width?: number; height?: number }) {
  if (!data.length) return null;
  const max = Math.max(...data), min = Math.min(...data);
  const span = max - min || 1;
  const stroke = { good: "#34d399", warn: "#fbbf24", bad: "#fb7185", brand: "#8b93f8", neutral: "#939cab" }[tone] ?? "#8b93f8";
  const pts = data.map((d, i) => {
    const x = (i / (data.length - 1 || 1)) * (width - 2) + 1;
    const y = height - 2 - ((d - min) / span) * (height - 4);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <svg width={width} height={height} className="shrink-0" aria-hidden>
      <polyline points={pts.join(" ")} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={pts[pts.length - 1].split(",")[0]} cy={pts[pts.length - 1].split(",")[1]} r="1.8" fill={stroke} />
    </svg>
  );
}

export function Bar({ value, max = 100, tone = "brand", label }: { value: number; max?: number; tone?: string; label?: string }) {
  const pct = Math.max(0, Math.min(100, (value / (max || 1)) * 100));
  const bg = { good: "bg-emerald-500", warn: "bg-amber-500", bad: "bg-rose-500", brand: "bg-brand-500", neutral: "bg-slate-500" }[tone] ?? "bg-brand-500";
  return (
    <div>
      {label && <div className="mb-1 flex justify-between text-xs text-slate-400"><span>{label}</span><span className="tabular-nums text-slate-300">{Math.round(pct)}%</span></div>}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800"><div className={`h-full rounded-full ${bg}`} style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

export function KeyValue({ items }: { items: Array<[string, ReactNode]> }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5 text-sm">
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-slate-500">{k}</dt>
          <dd className="min-w-0 break-words text-slate-200">{v ?? <span className="text-slate-500">—</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Tag({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "brand" }) {
  const cls = tone === "brand" ? "border-brand-500/40 bg-brand-500/10 text-brand-300" : "border-slate-700 bg-slate-800/60 text-slate-300";
  return <span className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[11px] ${cls}`}>{children}</span>;
}

export function Table<T>({ columns, rows, empty }: { columns: { key: string; header: string; render?: (r: T) => ReactNode }[]; rows: T[]; empty?: string }) {
  if (!rows.length) return <Empty message={empty ?? "No rows."} />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-800 text-[11px] uppercase tracking-wide text-slate-400">
            {columns.map((c) => (
              <th key={c.key} className="px-3 py-2 font-medium">{c.header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-slate-800/50 transition-colors hover:bg-slate-800/25">
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
    <div className="animate-pulse space-y-3 p-1">
      <div className="h-3 w-1/4 rounded bg-slate-800" />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[0, 1, 2, 3].map((i) => <div key={i} className="h-20 rounded-xl bg-slate-800/60" />)}
      </div>
      <div className="h-40 rounded-xl bg-slate-800/40" />
      <div className="sr-only">{label ?? "Loading"}</div>
    </div>
  );
}
export function Empty({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-slate-800 p-8 text-center">
      <div className="text-sm text-slate-400">{message}</div>
    </div>
  );
}
export function ErrorBox({ message }: { message: string }) {
  return <div className="rounded-lg border border-rose-800/70 bg-rose-950/30 p-4 text-sm text-rose-300">{message}</div>;
}
export function Unavailable({ what }: { what?: string }) {
  return (
    <div className="rounded-lg border border-amber-800/60 bg-amber-950/20 p-3 text-xs text-amber-300/90">
      {what ?? "This service"} is unavailable or not deployed — showing partial data. Other panels are unaffected.
    </div>
  );
}

// ---- provenance drawer (reusable) --------------------------------------------------------------
type DrawerState = { title: string; data: unknown } | null;
const DrawerCtx = createContext<{ open: (title: string, data: unknown) => void }>({ open: () => {} });
export const useDrawer = () => useContext(DrawerCtx);

export function DrawerProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<DrawerState>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setState(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <DrawerCtx.Provider value={{ open: (title, data) => setState({ title, data }) }}>
      {children}
      {state && (
        <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-label="Provenance">
          <div className="flex-1 bg-black/60 backdrop-blur-[1px]" onClick={() => setState(null)} />
          <div className="w-[34rem] max-w-full overflow-y-auto border-l border-slate-800 bg-slate-950 p-5">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-100">{state.title}</h3>
              <button className="rounded-md px-2 py-1 text-slate-400 hover:bg-slate-800" onClick={() => setState(null)}>✕</button>
            </div>
            <p className="mb-2 text-xs text-slate-500">Raw third-party payloads and retained HTML are never shown here.</p>
            <pre className="mono whitespace-pre-wrap break-words rounded-lg bg-slate-900 p-3 text-xs text-slate-300">
              {JSON.stringify(state.data, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </DrawerCtx.Provider>
  );
}

export const fmt = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));

export function relTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return String(iso);
  const s = Math.round((Date.now() - t) / 1000);
  const a = Math.abs(s);
  const units: Array<[number, string]> = [[60, "s"], [3600, "m"], [86400, "h"], [2592000, "d"], [31536000, "mo"]];
  let out = `${a}s`;
  for (let i = 0; i < units.length; i++) {
    const [limit, label] = units[i];
    if (a < limit) { const div = i === 0 ? 1 : units[i - 1][0]; out = `${Math.round(a / div)}${i === 0 ? "s" : label}`; break; }
    if (i === units.length - 1) out = `${Math.round(a / limit)}${label}`;
  }
  return s >= 0 ? `${out} ago` : `in ${out}`;
}

// ---- URL-persisted filters (hash query string) -------------------------------------------------
export function useHashQuery(): [Record<string, string>, (patch: Record<string, string>) => void] {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const [path, query] = hash.replace(/^#/, "").split("?");
  const params = Object.fromEntries(new URLSearchParams(query ?? ""));
  const setParams = (patch: Record<string, string>) => {
    const next = new URLSearchParams(query ?? "");
    for (const [k, v] of Object.entries(patch)) {
      if (v === "" || v === undefined) next.delete(k);
      else next.set(k, v);
    }
    const s = next.toString();
    window.location.hash = s ? `${path}?${s}` : path;
  };
  return [params, setParams];
}

// ---- form controls -----------------------------------------------------------------------------
export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-1.5 text-sm text-slate-100 placeholder:text-slate-600 focus:border-brand-500 ${props.className ?? ""}`} />;
}
export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`rounded-lg border border-slate-700 bg-slate-950/60 px-2.5 py-1.5 text-sm text-slate-200 focus:border-brand-500 ${props.className ?? ""}`} />;
}

// ---- bounded filtered export (CSV / JSON) ------------------------------------------------------
export function ExportButtons({ href }: { href: (fmt: "csv" | "json") => string }) {
  return (
    <span className="flex items-center gap-2 text-xs">
      <span className="text-slate-500">Export</span>
      {(["csv", "json"] as const).map((f) => (
        <a key={f} href={href(f)} download
           className="rounded-md border border-slate-700 px-2 py-0.5 text-slate-300 hover:border-brand-500 hover:text-brand-300">{f.toUpperCase()}</a>
      ))}
    </span>
  );
}

// ---- misc --------------------------------------------------------------------------------------
export function useInterval(cb: () => void, ms: number | null) {
  const saved = useRef(cb);
  useEffect(() => { saved.current = cb; });
  useEffect(() => {
    if (ms === null) return;
    const id = setInterval(() => saved.current(), ms);
    return () => clearInterval(id);
  }, [ms]);
}

export function num(v: unknown): string {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n.toLocaleString() : "—";
}
