import type { PlatformStatus } from "../api/client";

type Props = {
  platform: PlatformStatus;
};

export function PlatformStatusPanel({ platform }: Props) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900 p-6">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-medium">Platform Status</h2>
        <span
          className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ${
            platform.status === "ok"
              ? "bg-emerald-950 text-emerald-300 ring-1 ring-emerald-800"
              : "bg-amber-950 text-amber-300 ring-1 ring-amber-800"
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              platform.status === "ok" ? "bg-emerald-400" : "bg-amber-400"
            }`}
          />
          {platform.status === "ok" ? "Operational" : "Degraded"}
        </span>
        {platform.network_mode && (
          <span className="text-xs text-slate-500">
            {platform.network_mode} discovery
          </span>
        )}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {Object.entries(platform.services).map(([name, health]) => (
          <div
            key={name}
            className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm capitalize text-slate-300">{name}</span>
              <span
                className={`text-[10px] font-semibold uppercase ${
                  health.status === "ok" ? "text-emerald-400" : "text-red-400"
                }`}
              >
                {health.status}
              </span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
