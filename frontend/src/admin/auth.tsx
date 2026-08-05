import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Me } from "./api";

// Phase C: the console is LOCAL-ONLY and UNAUTHENTICATED. There is no login screen, no role
// selector, and no token — a single fixed INTERNAL_USER context. This provider just reads /auth/me
// once so the header can show the context and so we can tell whether the gateway is actually in
// local mode (a public/non-local gateway returns 401 → we show a plain notice instead of a login).

type MeState = { me: Me | null; loading: boolean; error: string | null };
const Ctx = createContext<MeState>({ me: null, loading: true, error: null });
export const useMe = () => useContext(Ctx);

export function MeProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<MeState>({ me: null, loading: true, error: null });
  useEffect(() => {
    let cancelled = false;
    api.me()
      .then((me) => !cancelled && setState({ me, loading: false, error: null }))
      .catch((e) => !cancelled && setState({ me: null, loading: false, error: e.message ?? String(e) }));
    return () => { cancelled = true; };
  }, []);
  return <Ctx.Provider value={state}>{children}</Ctx.Provider>;
}

export function NotLocal({ message }: { message: string }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 p-4 text-slate-100">
      <div className="max-w-md space-y-3 rounded-xl border border-slate-800 bg-slate-900/70 p-6 text-sm">
        <h1 className="text-lg font-semibold">n-quark · Inspection Console</h1>
        <p className="text-slate-400">This dashboard is <strong>local-only and unauthenticated</strong>. It
          could not reach a gateway running in local mode.</p>
        <p className="rounded border border-amber-800 bg-amber-950/30 p-2 text-xs text-amber-300">{message}</p>
        <p className="text-xs text-slate-500">Start the gateway with <code>NQUARK_ADMIN_API_ENABLED=true</code> and
          <code> NQUARK_ADMIN_LOCAL_MODE=true</code> on your machine. Do not expose it publicly.</p>
      </div>
    </div>
  );
}
