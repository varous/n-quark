import { useCallback, useEffect, useState } from "react";
import {
  DEMO_SPOTIFY_ALIAS,
  DEMO_SPOTIFY_ID,
  fetchEntityByAlias,
  fetchPlatformStatus,
  fetchRecentObservations,
  ingestSpotifyArtist,
  resolveSpotifyArtist,
  type Entity,
  type EntityResolveResponse,
  type Observation,
  type PlatformStatus,
} from "./api/client";
import { EntityLookupPanel } from "./components/EntityLookupPanel";
import { IngestDemoPanel } from "./components/IngestDemoPanel";
import { PlatformStatusPanel } from "./components/PlatformStatusPanel";
import { RecentObservationsPanel } from "./components/RecentObservationsPanel";
import "./index.css";

function App() {
  const [platform, setPlatform] = useState<PlatformStatus | null>(null);
  const [observations, setObservations] = useState<Observation[]>([]);
  const [observationTotal, setObservationTotal] = useState(0);
  const [aliasInput, setAliasInput] = useState(DEMO_SPOTIFY_ALIAS);
  const [entity, setEntity] = useState<Entity | null>(null);
  const [resolveResult, setResolveResult] = useState<EntityResolveResponse | null>(null);
  const [selectedEntity, setSelectedEntity] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [entityLoading, setEntityLoading] = useState(false);
  const [demoLoading, setDemoLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [entityError, setEntityError] = useState<string | null>(null);
  const [demoMessage, setDemoMessage] = useState<string | null>(null);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [status, recent] = await Promise.all([
        fetchPlatformStatus(),
        fetchRecentObservations(50),
      ]);
      setPlatform(status);
      setObservations(recent.observations);
      setObservationTotal(recent.count);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }, []);

  const lookupEntity = useCallback(async (alias: string) => {
    setEntityLoading(true);
    setEntityError(null);
    try {
      const result = await fetchEntityByAlias(alias);
      setEntity(result);
      setResolveResult(null);
    } catch (err) {
      setEntity(null);
      setResolveResult(null);
      setEntityError(err instanceof Error ? err.message : "Entity not found");
    } finally {
      setEntityLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  useEffect(() => {
    if (selectedEntity?.startsWith("artist:spotify:")) {
      setAliasInput(selectedEntity);
      void lookupEntity(selectedEntity);
    }
  }, [selectedEntity, lookupEntity]);

  const runDemo = async () => {
    setDemoLoading(true);
    setDemoMessage(null);
    setError(null);
    try {
      const ingest = await ingestSpotifyArtist(DEMO_SPOTIFY_ID);
      const resolved = await resolveSpotifyArtist(DEMO_SPOTIFY_ID, ingest.name);
      setEntity(resolved.entity);
      setResolveResult(resolved);
      setAliasInput(DEMO_SPOTIFY_ALIAS);
      setSelectedEntity(ingest.entity);
      setDemoMessage(
        `Ingested ${ingest.observations_created} observations → resolved to ${resolved.canonical_id}`,
      );
      await loadDashboard();
    } catch (err) {
      setDemoMessage(null);
      setError(err instanceof Error ? err.message : "Demo pipeline failed");
    } finally {
      setDemoLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-900/80 px-6 py-8">
        <p className="text-sm font-medium uppercase tracking-widest text-violet-400">
          Intelligence Operating System
        </p>
        <h1 className="mt-2 text-4xl font-semibold tracking-tight">n-quark</h1>
        <p className="mt-3 max-w-2xl text-slate-400">
          Observe signals, store immutable evidence, and resolve canonical entities.
        </p>
      </header>

      <main className="mx-auto max-w-6xl space-y-6 px-6 py-10">
        {error && (
          <div className="rounded-lg border border-red-800 bg-red-950/50 p-4 text-red-200">
            {error}
          </div>
        )}

        <IngestDemoPanel onRunDemo={() => void runDemo()} loading={demoLoading} message={demoMessage} />

        {platform && <PlatformStatusPanel platform={platform} />}

        <div className="grid gap-6 lg:grid-cols-2">
          <RecentObservationsPanel
            observations={observations}
            total={observationTotal}
            onRefresh={() => void loadDashboard()}
            loading={loading}
            selectedEntity={selectedEntity}
            onSelectEntity={setSelectedEntity}
          />
          <EntityLookupPanel
            aliasInput={aliasInput}
            onAliasInputChange={setAliasInput}
            onLookup={() => void lookupEntity(aliasInput.trim())}
            entity={entity}
            resolveResult={resolveResult}
            loading={entityLoading}
            error={entityError}
          />
        </div>
      </main>
    </div>
  );
}

export default App;
