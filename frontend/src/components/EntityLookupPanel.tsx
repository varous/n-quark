import type { Entity, EntityResolveResponse } from "../api/client";
import { formatTime } from "../api/client";

type Props = {
  aliasInput: string;
  onAliasInputChange: (value: string) => void;
  onLookup: () => void;
  entity: Entity | null;
  resolveResult: EntityResolveResponse | null;
  loading: boolean;
  error: string | null;
};

export function EntityLookupPanel({
  aliasInput,
  onAliasInputChange,
  onLookup,
  entity,
  resolveResult,
  loading,
  error,
}: Props) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900 p-6">
      <h2 className="text-lg font-medium">Canonical Entity</h2>
      <p className="mt-1 text-sm text-slate-500">
        Resolve external aliases to canonical artist IDs
      </p>

      <div className="mt-4 flex flex-col gap-3 sm:flex-row">
        <input
          type="text"
          value={aliasInput}
          onChange={(e) => onAliasInputChange(e.target.value)}
          placeholder="artist:spotify:4tZwfgrHOc3mvqYFCOCYO6"
          className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm text-slate-100 placeholder:text-slate-600"
        />
        <button
          type="button"
          onClick={onLookup}
          disabled={loading || !aliasInput.trim()}
          className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium hover:bg-violet-500 disabled:opacity-50"
        >
          Lookup
        </button>
      </div>

      {error && (
        <p className="mt-3 rounded-lg border border-red-900 bg-red-950/40 px-3 py-2 text-sm text-red-200">
          {error}
        </p>
      )}

      {entity && (
        <div className="mt-5 rounded-lg border border-slate-800 bg-slate-950/60 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-500">Canonical ID</p>
              <p className="mt-1 font-mono text-violet-300">{entity.id}</p>
            </div>
            {resolveResult && (
              <span
                className={`rounded-full px-2 py-0.5 text-xs ${
                  resolveResult.created
                    ? "bg-emerald-950 text-emerald-300"
                    : "bg-slate-800 text-slate-400"
                }`}
              >
                {resolveResult.created ? "Newly created" : "Existing"}
              </span>
            )}
          </div>
          <p className="mt-4 text-2xl font-semibold text-slate-100">{entity.display_name}</p>
          <p className="mt-1 text-sm text-slate-500 capitalize">{entity.entity_type}</p>

          {entity.aliases.length > 0 && (
            <div className="mt-4">
              <p className="text-xs uppercase tracking-wide text-slate-500">Aliases</p>
              <ul className="mt-2 space-y-1">
                {entity.aliases.map((alias) => (
                  <li key={alias.alias_key} className="font-mono text-xs text-slate-400">
                    {alias.alias_key}
                    <span className="ml-2 text-slate-600">({alias.source})</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="mt-4 text-xs text-slate-600">
            Updated {formatTime(entity.updated_at)}
          </p>
        </div>
      )}
    </section>
  );
}
