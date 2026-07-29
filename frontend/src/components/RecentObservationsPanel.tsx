import type { Observation } from "../api/client";
import { formatTime, formatValue } from "../api/client";

type Props = {
  observations: Observation[];
  total: number;
  onRefresh: () => void;
  loading: boolean;
  selectedEntity: string | null;
  onSelectEntity: (entity: string) => void;
};

export function RecentObservationsPanel({
  observations,
  total,
  onRefresh,
  loading,
  selectedEntity,
  onSelectEntity,
}: Props) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900 p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-medium">Recent Observations</h2>
          <p className="text-sm text-slate-500">
            {total} total · append-only evidence store
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading}
          className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50"
        >
          Refresh
        </button>
      </div>

      {observations.length === 0 ? (
        <p className="text-sm text-slate-500">
          No observations yet. Run the demo ingest below to populate signals.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-2 py-2">Entity</th>
                <th className="px-2 py-2">Attribute</th>
                <th className="px-2 py-2">Value</th>
                <th className="px-2 py-2">Source</th>
                <th className="px-2 py-2">Confidence</th>
                <th className="px-2 py-2">Ingested</th>
              </tr>
            </thead>
            <tbody>
              {observations.map((obs) => (
                <tr
                  key={obs.id}
                  className={`border-b border-slate-800/80 hover:bg-slate-950/50 ${
                    selectedEntity === obs.entity ? "bg-violet-950/20" : ""
                  }`}
                >
                  <td className="px-2 py-2">
                    <button
                      type="button"
                      onClick={() => onSelectEntity(obs.entity)}
                      className="font-mono text-xs text-violet-300 hover:underline"
                    >
                      {obs.entity}
                    </button>
                  </td>
                  <td className="px-2 py-2 text-slate-300">{obs.attribute}</td>
                  <td className="max-w-[200px] truncate px-2 py-2 text-slate-400">
                    {formatValue(obs.value)}
                  </td>
                  <td className="px-2 py-2 text-slate-400">{obs.source}</td>
                  <td className="px-2 py-2 text-slate-400">
                    {(obs.confidence * 100).toFixed(0)}%
                  </td>
                  <td className="px-2 py-2 text-slate-500">{formatTime(obs.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
