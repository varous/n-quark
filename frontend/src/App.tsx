import { useCallback, useEffect, useState } from "react";
import "./index.css";

type ServiceHealth = {
  status: string;
  service?: string;
  timestamp?: string;
  detail?: string;
};

type PlatformStatus = {
  status: string;
  timestamp: string;
  services: Record<string, ServiceHealth>;
};

const API_BASE = import.meta.env.VITE_API_URL ?? "/api";

function App() {
  const [platform, setPlatform] = useState<PlatformStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/v1/platform/status`);
      if (!response.ok) {
        throw new Error(`API responded with ${response.status}`);
      }
      setPlatform(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch platform status");
      setPlatform(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchStatus();
  }, [fetchStatus]);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-900/80 px-6 py-8">
        <p className="text-sm font-medium uppercase tracking-widest text-violet-400">
          Intelligence Operating System
        </p>
        <h1 className="mt-2 text-4xl font-semibold tracking-tight">n-quark</h1>
        <p className="mt-3 max-w-2xl text-slate-400">
          Canonical intelligence layer for live entertainment — observe, understand,
          predict, and recommend.
        </p>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-10">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-xl font-medium">Platform Status</h2>
          <button
            type="button"
            onClick={() => void fetchStatus()}
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium hover:bg-violet-500"
          >
            Refresh
          </button>
        </div>

        {loading && <p className="text-slate-400">Loading platform status…</p>}

        {error && (
          <div className="rounded-lg border border-red-800 bg-red-950/50 p-4 text-red-200">
            {error}
          </div>
        )}

        {platform && (
          <>
            <div
              className={`mb-8 inline-flex items-center gap-2 rounded-full px-4 py-1.5 text-sm font-medium ${
                platform.status === "ok"
                  ? "bg-emerald-950 text-emerald-300 ring-1 ring-emerald-800"
                  : "bg-amber-950 text-amber-300 ring-1 ring-amber-800"
              }`}
            >
              <span
                className={`h-2 w-2 rounded-full ${
                  platform.status === "ok" ? "bg-emerald-400" : "bg-amber-400"
                }`}
              />
              {platform.status === "ok" ? "All systems operational" : "Degraded"}
            </div>

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(platform.services).map(([name, health]) => (
                <article
                  key={name}
                  className="rounded-xl border border-slate-800 bg-slate-900 p-5"
                >
                  <div className="flex items-center justify-between">
                    <h3 className="font-medium capitalize">{name}</h3>
                    <span
                      className={`text-xs font-semibold uppercase ${
                        health.status === "ok" ? "text-emerald-400" : "text-red-400"
                      }`}
                    >
                      {health.status}
                    </span>
                  </div>
                  {health.service && (
                    <p className="mt-2 text-sm text-slate-500">{health.service}</p>
                  )}
                  {health.detail && (
                    <p className="mt-2 text-sm text-red-300">{health.detail}</p>
                  )}
                </article>
              ))}
            </div>
          </>
        )}
      </main>
    </div>
  );
}

export default App;
