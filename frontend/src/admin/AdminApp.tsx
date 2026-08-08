import { useState } from "react";
import { api } from "./api";
import { MeProvider, NotLocal, useMe } from "./auth";
import { DrawerProvider, Link, Loading, useHashRoute } from "./ui";
import { Captures, Diagnostics, Events, Health, Overview, Sources } from "./screens";
import { Resolution } from "./workbench";
import { EntityDetail, Entities, EventDetail, Graph } from "./detail";
import { ArtistDemand, DemandIntelligence } from "./demand";

const NAV = [
  ["/overview", "Overview"], ["/sources", "Sources"], ["/events", "Events"],
  ["/entities", "Entities"], ["/demand", "Demand Intelligence"], ["/resolution", "Resolution"],
  ["/graph", "Graph"], ["/captures", "Captures"], ["/diagnostics", "Diagnostics"], ["/health", "Health"],
];

function parse(hash: string): { path: string; parts: string[]; params: URLSearchParams } {
  const [p, q] = hash.split("?");
  return { path: p, parts: p.split("/").filter(Boolean), params: new URLSearchParams(q ?? "") };
}

function Router({ hash }: { hash: string }) {
  const { parts, params } = parse(hash);
  const [top, a, b] = parts;
  if (top === "sources" && a) return <Sources />; // source detail folds into Sources view for now
  switch (top) {
    case "overview": case undefined: return <Overview />;
    case "sources": return <Sources />;
    case "events": return a ? <EventDetail id={decodeURIComponent(parts.slice(1).join("/"))} /> : <Events />;
    case "entities": return a && b ? <EntityDetail type={a} id={decodeURIComponent(parts.slice(2).join("/"))} /> : <Entities />;
    case "demand":
      return a === "artists" && b
        ? <ArtistDemand id={decodeURIComponent(parts.slice(2).join("/"))} />
        : <DemandIntelligence />;
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
  return (
    <nav className="mb-4 text-xs text-slate-500" aria-label="Breadcrumb">
      <Link to="/overview">admin</Link>
      {parts.map((p, i) => (
        <span key={i}> / <span className="text-slate-300">{decodeURIComponent(p)}</span></span>
      ))}
    </nav>
  );
}

function GlobalSearch() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Awaited<ReturnType<typeof api.search>> | null>(null);
  const [open, setOpen] = useState(false);
  async function run(v: string) {
    setQ(v);
    if (v.trim().length < 2) { setRes(null); setOpen(false); return; }
    try { setRes(await api.search(v)); setOpen(true); } catch { setRes(null); }
  }
  return (
    <div className="relative w-72">
      <input className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm" placeholder="Search events, entities…"
        value={q} onChange={(e) => run(e.target.value)} onFocus={() => res && setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)} />
      {open && res && (
        <div className="absolute z-30 mt-1 max-h-96 w-96 overflow-y-auto rounded border border-slate-700 bg-slate-950 p-2 text-sm shadow-xl">
          <div className="mb-1 text-xs uppercase text-slate-500">Entities</div>
          {res.results.entities.length === 0 && <div className="px-2 py-1 text-xs text-slate-600">none</div>}
          {res.results.entities.map((e, i) => (
            <a key={i} href={`#/entities/${e.type}/${e.canonical_entity_id}`} className="block truncate rounded px-2 py-1 hover:bg-slate-800">{String(e.name ?? e.canonical_entity_id)} <span className="text-xs text-slate-500">{String(e.type)}</span></a>
          ))}
          <div className="mb-1 mt-2 text-xs uppercase text-slate-500">Events</div>
          {res.results.events.length === 0 && <div className="px-2 py-1 text-xs text-slate-600">none</div>}
          {res.results.events.map((e, i) => (
            <a key={i} href={`#/events/${e.canonical_event_id}`} className="block truncate rounded px-2 py-1 hover:bg-slate-800">{String(e.source_record_id)} <span className="text-xs text-slate-500">{String(e.source)}</span></a>
          ))}
        </div>
      )}
    </div>
  );
}

function Shell() {
  const [hash] = useHashRoute();
  const { me } = useMe();
  const active = "/" + (parse(hash).parts[0] ?? "overview");
  return (
    <div className="flex min-h-screen bg-slate-950 text-slate-100">
      <aside className="w-56 shrink-0 border-r border-slate-800 bg-slate-900/40 p-4">
        <div className="mb-6">
          <div className="text-sm font-semibold">n-quark</div>
          <div className="text-xs text-slate-500">Inspection Console</div>
        </div>
        <nav className="space-y-1">
          {NAV.map(([to, label]) => (
            <a key={to} href={`#${to}`} className={`block rounded px-3 py-1.5 text-sm ${active === to ? "bg-sky-600/20 text-sky-300" : "text-slate-300 hover:bg-slate-800"}`}>{label}</a>
          ))}
        </nav>
        <div className="mt-6 rounded border border-amber-800/60 bg-amber-950/20 p-2 text-[11px] leading-snug text-amber-300/90">
          Local-only · unauthenticated. Do not expose publicly.
        </div>
      </aside>
      <div className="flex-1">
        <header className="flex items-center justify-between border-b border-slate-800 px-6 py-3">
          <GlobalSearch />
          <div className="flex items-center gap-3 text-sm text-slate-400">
            <span>{me?.sub ?? "internal-user"} · <span className="rounded bg-slate-800 px-1.5 py-0.5 text-xs">{me?.auth_mode ?? "local"}</span></span>
            {me && !me.mutations_enabled && <span className="rounded border border-slate-700 px-1.5 py-0.5 text-[11px] text-slate-500">read-only</span>}
          </div>
        </header>
        <main className="p-6">
          <Breadcrumbs hash={hash} />
          <Router hash={hash} />
        </main>
      </div>
    </div>
  );
}

function Gate() {
  const { me, loading, error } = useMe();
  if (loading) return <div className="min-h-screen bg-slate-950 p-6"><Loading label="Connecting" /></div>;
  if (!me || error) return <NotLocal message={error ?? "Gateway did not return a local principal."} />;
  return <Shell />;
}

export default function AdminApp() {
  return (
    <MeProvider>
      <DrawerProvider>
        <Gate />
      </DrawerProvider>
    </MeProvider>
  );
}
