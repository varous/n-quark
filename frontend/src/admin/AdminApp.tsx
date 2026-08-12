import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { AuthProvider, ConnectError, LoginScreen, useAuth } from "./auth";
import { DrawerProvider, Link, useHashRoute } from "./ui";
import { Captures, Diagnostics, Events, Health, Sources } from "./screens";
import { Resolution } from "./workbench";
import { EntityDetail, Entities, EventDetail, Graph } from "./detail";
import { ArtistDemand, DemandIntelligence } from "./demand";
import { Overview } from "./overview";
import { Analysis } from "./analysis";
import { Watchlist } from "./watchlist";

type NavItem = { to: string; label: string; icon: keyof typeof ICONS };
type NavGroup = { group: string; items: NavItem[] };

const NAV: NavGroup[] = [
  { group: "", items: [{ to: "/overview", label: "Overview", icon: "grid" }] },
  { group: "Explore", items: [
    { to: "/events", label: "Events", icon: "calendar" },
    { to: "/entities", label: "Artists & venues", icon: "users" },
    { to: "/demand", label: "Demand", icon: "pulse" },
    { to: "/graph", label: "Graph", icon: "share" },
  ] },
  { group: "Research", items: [{ to: "/watchlist", label: "Watchlist", icon: "eye" }] },
  { group: "Analyze", items: [{ to: "/analysis", label: "Analysis", icon: "spark" }] },
  { group: "Collection", items: [
    { to: "/sources", label: "Sources", icon: "feed" },
    { to: "/captures", label: "Captures", icon: "camera" },
    { to: "/diagnostics", label: "Diagnostics", icon: "gauge" },
    { to: "/resolution", label: "Resolution", icon: "merge" },
  ] },
  { group: "System", items: [{ to: "/health", label: "Service health", icon: "heart" }] },
];

const ICONS = {
  grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  calendar: "M7 3v3M17 3v3M4 8h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z",
  users: "M16 19v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1M9 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM22 19v-1a4 4 0 0 0-3-3.87M16 4.13A4 4 0 0 1 16 12",
  pulse: "M3 12h4l2 6 4-14 2 8h6",
  share: "M6 12a3 3 0 1 0 0-.01M18 6a3 3 0 1 0 0-.01M18 18a3 3 0 1 0 0-.01M8.6 10.5l6.8-3M8.6 13.5l6.8 3",
  spark: "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z",
  feed: "M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16M6 18a1.5 1.5 0 1 0 0-.01",
  camera: "M4 8h3l2-2h6l2 2h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1zM12 17a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z",
  gauge: "M12 14a2 2 0 1 0 0-.01M12 14l4-4M4.9 19a9 9 0 1 1 14.2 0",
  merge: "M7 4v6a4 4 0 0 0 4 4h6M17 10l3 4-3 4M7 4L4 8M7 4l3 4",
  heart: "M12 20s-7-4.3-9.3-8.3C1 8.5 2.5 5 6 5c2 0 3.2 1.2 4 2.3C10.8 6.2 12 5 14 5c3.5 0 5 3.5 3.3 6.7C19 15.7 12 20 12 20z",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
} as const;

function Icon({ name }: { name: keyof typeof ICONS }) {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d={ICONS[name]} /></svg>
  );
}

function parse(hash: string): { path: string; parts: string[]; params: URLSearchParams } {
  const [p, q] = hash.split("?");
  return { path: p, parts: p.split("/").filter(Boolean), params: new URLSearchParams(q ?? "") };
}

function Router({ hash }: { hash: string }) {
  const { parts, params } = parse(hash);
  const [top, a, b] = parts;
  switch (top) {
    case "overview": case undefined: return <Overview />;
    case "sources": return <Sources />;
    case "events": return a ? <EventDetail id={decodeURIComponent(parts.slice(1).join("/"))} /> : <Events />;
    case "entities": return a && b ? <EntityDetail type={a} id={decodeURIComponent(parts.slice(2).join("/"))} /> : <Entities />;
    case "demand":
      return a === "artists" && b
        ? <ArtistDemand id={decodeURIComponent(parts.slice(2).join("/"))} />
        : <DemandIntelligence />;
    case "analysis": return <Analysis />;
    case "watchlist": return <Watchlist />;
    case "resolution": return <Resolution />;
    case "graph": return <Graph initialRoot={params.get("root") ?? undefined} />;
    case "captures": return <Captures />;
    case "diagnostics": return <Diagnostics />;
    case "health": return <Health />;
    default: return <Overview />;
  }
}

function Breadcrumbs({ hash }: { hash: string }) {
  const { parts } = parse(hash);
  if (parts.length <= 1) return null;
  return (
    <nav className="mb-4 text-xs text-slate-500" aria-label="Breadcrumb">
      <Link to="/overview" className="text-slate-500 hover:text-slate-300">Console</Link>
      {parts.map((p, i) => (
        <span key={i}> <span className="text-slate-700">/</span> <span className={i === parts.length - 1 ? "text-slate-300" : ""}>{decodeURIComponent(p)}</span></span>
      ))}
    </nav>
  );
}

function GlobalSearch() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Awaited<ReturnType<typeof api.search>> | null>(null);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === "/" || (e.key === "k" && (e.metaKey || e.ctrlKey))) && document.activeElement !== ref.current) {
        e.preventDefault(); ref.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  async function run(v: string) {
    setQ(v);
    if (v.trim().length < 2) { setRes(null); setOpen(false); return; }
    try { setRes(await api.search(v)); setOpen(true); } catch { setRes(null); }
  }
  return (
    <div className="relative w-full max-w-md">
      <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>
      </span>
      <input ref={ref} className="w-full rounded-lg border border-slate-800 bg-slate-900/70 py-2 pl-9 pr-10 text-sm text-slate-100 placeholder:text-slate-600 focus:border-brand-500"
        placeholder="Search artists, venues, events…" value={q} onChange={(e) => run(e.target.value)}
        onFocus={() => res && setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)} />
      <kbd className="pointer-events-none absolute right-2.5 top-1/2 hidden -translate-y-1/2 rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-500 sm:block">/</kbd>
      {open && res && (
        <div className="absolute z-30 mt-1.5 max-h-96 w-full overflow-y-auto rounded-xl border border-slate-800 bg-slate-950/95 p-2 text-sm shadow-2xl backdrop-blur">
          <div className="px-2 pb-1 pt-1 text-[10px] uppercase tracking-wide text-slate-500">Entities</div>
          {res.results.entities.length === 0 && <div className="px-2 py-1 text-xs text-slate-600">none</div>}
          {res.results.entities.map((e, i) => (
            <a key={i} href={`#/entities/${e.type}/${e.canonical_entity_id}`} className="block truncate rounded-md px-2 py-1.5 hover:bg-slate-800">{String(e.name ?? e.canonical_entity_id)} <span className="text-xs text-slate-500">{String(e.type)}</span></a>
          ))}
          <div className="px-2 pb-1 pt-2 text-[10px] uppercase tracking-wide text-slate-500">Events</div>
          {res.results.events.length === 0 && <div className="px-2 py-1 text-xs text-slate-600">none</div>}
          {res.results.events.map((e, i) => (
            <a key={i} href={`#/events/${e.canonical_event_id}`} className="block truncate rounded-md px-2 py-1.5 hover:bg-slate-800">{String(e.source_record_id)} <span className="text-xs text-slate-500">{String(e.source)}</span></a>
          ))}
        </div>
      )}
    </div>
  );
}

function EnvBadge({ compact }: { compact?: boolean }) {
  const { me, status } = useAuth();
  const env = me?.environment ?? status?.environment ?? (status?.auth_mode === "local" ? "local" : "unknown");
  const region = (me?.region ?? status?.region ?? "").toUpperCase();
  const prod = env === "production";
  const showRegion = region && region !== "LOCAL" && region !== "UNKNOWN";
  const tone = prod ? "border-emerald-600/40 bg-emerald-500/10 text-emerald-300" : "border-amber-600/40 bg-amber-500/10 text-amber-300";
  return (
    <div className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 font-semibold uppercase tracking-wider ${tone} ${compact ? "text-[10px]" : "text-[11px]"}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${prod ? "bg-emerald-400" : "bg-amber-400"}`} />
      {prod ? "Production" : env} · Read only{showRegion ? ` · ${region}` : ""}
    </div>
  );
}

function Identity() {
  const { me, status, signOut } = useAuth();
  const mode = me?.auth_mode ?? status?.auth_mode ?? "local";
  return (
    <div className="flex items-center gap-3">
      <div className="hidden text-right leading-tight sm:block">
        <div className="max-w-[16rem] truncate text-xs font-medium text-slate-200">{me?.sub ?? "internal-user"}</div>
        <div className="text-[10px] uppercase tracking-wide text-slate-500">{mode} · read-only</div>
      </div>
      <div className="grid h-8 w-8 place-items-center rounded-full border border-slate-700 bg-slate-800 text-xs font-semibold text-slate-300">
        {(me?.sub ?? "i").slice(0, 1).toUpperCase()}
      </div>
      {mode === "oidc" && (
        <button onClick={signOut} title="Sign out"
          className="rounded-lg border border-slate-800 px-2.5 py-1.5 text-xs text-slate-400 hover:border-slate-700 hover:text-slate-200">Sign out</button>
      )}
    </div>
  );
}

function Shell() {
  const [hash] = useHashRoute();
  const active = "/" + (parse(hash).parts[0] ?? "overview");
  return (
    <div className="flex min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-20 flex w-60 shrink-0 flex-col border-r border-slate-800/80 bg-slate-950/70 backdrop-blur">
        <div className="flex items-center gap-2.5 px-5 py-4">
          <svg width="24" height="24" viewBox="0 0 32 32" aria-hidden>
            <circle cx="16" cy="16" r="3.2" fill="#8b93f8" />
            <circle cx="16" cy="16" r="8" fill="none" stroke="#5a54e6" strokeWidth="1.6" />
            <circle cx="16" cy="16" r="12" fill="none" stroke="#2dd4bf" strokeWidth="1" opacity="0.5" />
          </svg>
          <div className="leading-tight">
            <div className="text-sm font-semibold tracking-tight text-slate-100">n-quark</div>
            <div className="text-[10px] uppercase tracking-widest text-slate-500">Console</div>
          </div>
        </div>
        <div className="px-4 pb-3"><EnvBadge /></div>
        <nav className="flex-1 space-y-4 overflow-y-auto px-3 py-2">
          {NAV.map((grp, gi) => (
            <div key={gi}>
              {grp.group && <div className="px-2 pb-1 text-[10px] font-medium uppercase tracking-widest text-slate-600">{grp.group}</div>}
              <div className="space-y-0.5">
                {grp.items.map((it) => {
                  const on = active === it.to;
                  return (
                    <a key={it.to} href={`#${it.to}`}
                      className={`flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors ${on ? "bg-brand-500/15 text-brand-200 ring-1 ring-inset ring-brand-500/25" : "text-slate-400 hover:bg-slate-800/50 hover:text-slate-200"}`}>
                      <span className={on ? "text-brand-300" : "text-slate-500"}><Icon name={it.icon} /></span>
                      {it.label}
                    </a>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>
        <div className="px-4 py-3 text-[10px] leading-relaxed text-slate-600">
          Live production · read-only. Data reflects the observation layer as accrued.
        </div>
      </aside>

      <div className="flex min-h-screen flex-1 flex-col pl-60">
        <header className="sticky top-0 z-10 flex items-center gap-4 border-b border-slate-800/80 bg-slate-950/70 px-6 py-3 backdrop-blur">
          <GlobalSearch />
          <div className="ml-auto"><Identity /></div>
        </header>
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-6 py-6">
          <Breadcrumbs hash={hash} />
          <Router hash={hash} />
        </main>
      </div>
    </div>
  );
}

function Gate() {
  const { status, me, loading, error } = useAuth();
  if (loading) return <div className="grid min-h-screen place-items-center text-sm text-slate-500">Connecting…</div>;
  if (error && !status) return <ConnectError message={error} />;
  if (status?.auth_mode === "oidc" && !me) return <LoginScreen status={status} />;
  if (status?.auth_mode === "disabled") return <LoginScreen status={status} />;
  return <Shell />;
}

export default function AdminApp() {
  return (
    <AuthProvider>
      <DrawerProvider>
        <Gate />
      </DrawerProvider>
    </AuthProvider>
  );
}
