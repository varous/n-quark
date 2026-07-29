import { DEMO_SPOTIFY_ALIAS, DEMO_SPOTIFY_ID } from "../api/client";

type Props = {
  onRunDemo: () => void;
  loading: boolean;
  message: string | null;
};

export function IngestDemoPanel({ onRunDemo, loading, message }: Props) {
  return (
    <section className="rounded-xl border border-violet-900/50 bg-violet-950/20 p-6">
      <h2 className="text-lg font-medium text-violet-100">Demo Pipeline</h2>
      <p className="mt-1 text-sm text-violet-200/70">
        Ingest Spotify mock signals for Daft Punk, then resolve to canonical{" "}
        <code className="text-violet-300">artist:daft-punk</code>
      </p>
      <p className="mt-2 font-mono text-xs text-violet-300/80">{DEMO_SPOTIFY_ALIAS}</p>
      <button
        type="button"
        onClick={onRunDemo}
        disabled={loading}
        className="mt-4 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-500 disabled:opacity-50"
      >
        {loading ? "Running…" : "Run ingest + resolve"}
      </button>
      {message && <p className="mt-3 text-sm text-violet-100/90">{message}</p>}
    </section>
  );
}

export { DEMO_SPOTIFY_ID };
