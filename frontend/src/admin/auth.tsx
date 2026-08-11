import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, session, type AuthStatus, type Me } from "./api";

// ================================================================================================
// Auth gate (Admin D). Three modes, resolved from /auth/status:
//   - "local":    local dev console, no login (single internal context).
//   - "oidc":     production — Google Workspace sign-in required; a valid session shows the app.
//   - "disabled": the admin API is reachable but no identity provider is configured.
// The session itself is an httpOnly cookie set by the server; the browser stores no token.
// ================================================================================================

type AuthState = { status: AuthStatus | null; me: Me | null; loading: boolean; error: string | null };
const Ctx = createContext<AuthState & { reload: () => void; signOut: () => void }>({
  status: null, me: null, loading: true, error: null, reload: () => {}, signOut: () => {},
});
export const useAuth = () => useContext(Ctx);
// Back-compat for screens that read the principal.
export const useMe = () => { const { me, loading, error } = useAuth(); return { me, loading, error }; };

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: null, me: null, loading: true, error: null });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await api.authStatus();
        let me: Me | null = null;
        if (status.auth_mode === "local" || (status.auth_mode === "oidc" && status.authenticated)) {
          me = await api.me().catch(() => null);
        }
        if (!cancelled) setState({ status, me, loading: false, error: null });
      } catch (e) {
        if (!cancelled) setState({ status: null, me: null, loading: false, error: (e as Error).message ?? String(e) });
      }
    })();
    return () => { cancelled = true; };
  }, [tick]);

  const signOut = () => {
    session.clear();
    api.logout().catch(() => {}).finally(() => { window.location.hash = ""; setTick((t) => t + 1); });
  };
  return <Ctx.Provider value={{ ...state, reload: () => setTick((t) => t + 1), signOut }}>{children}</Ctx.Provider>;
}

// Re-export under the old name so existing imports keep working.
export const MeProvider = AuthProvider;

function Mark() {
  return (
    <div className="flex items-center gap-2.5">
      <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
        <rect x="1" y="1" width="30" height="30" rx="8" fill="#10131a" stroke="#2b323e" />
        <circle cx="16" cy="16" r="3.4" fill="#8b93f8" />
        <circle cx="16" cy="16" r="8.5" fill="none" stroke="#5a54e6" strokeWidth="1.6" opacity="0.85" />
        <circle cx="16" cy="16" r="12" fill="none" stroke="#2dd4bf" strokeWidth="1.1" opacity="0.5" />
      </svg>
      <div className="leading-tight">
        <div className="text-sm font-semibold tracking-tight text-slate-100">n-quark</div>
        <div className="text-[11px] text-slate-500">Intelligence Console</div>
      </div>
    </div>
  );
}

export function LoginScreen({ status }: { status: AuthStatus | null }) {
  const failed = new URLSearchParams(window.location.search).get("login_error");
  const disabled = status?.auth_mode === "disabled";
  const loginUrl = (status?.login_url ?? "/admin/v1/auth/login") + "?next=/";
  const env = status?.environment ?? "unknown";
  const region = (status?.region ?? "").toUpperCase();
  const prod = env === "production";
  return (
    <div className="grid min-h-screen place-items-center p-6">
      <div className="w-full max-w-sm">
        <div className="mb-4 flex justify-center"><Mark /></div>
        <div className="mb-5 flex justify-center">
          <span className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wider ${prod ? "border-emerald-600/40 bg-emerald-500/10 text-emerald-300" : "border-amber-600/40 bg-amber-500/10 text-amber-300"}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${prod ? "bg-emerald-400" : "bg-amber-400"}`} />
            {prod ? "Production" : env} · Read only{region && region !== "LOCAL" && region !== "UNKNOWN" ? ` · ${region}` : ""}
          </span>
        </div>
        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6 shadow-xl shadow-black/30">
          <h1 className="text-base font-semibold text-slate-100">Sign in</h1>
          <p className="mt-1 text-sm text-slate-400">
            The n-quark production console is restricted to your organization's Google Workspace accounts.
          </p>
          {failed && (
            <div className="mt-4 rounded-lg border border-rose-800/70 bg-rose-950/30 p-2.5 text-xs text-rose-300">
              That account isn't permitted, or sign-in was cancelled. Use your organization Google account.
            </div>
          )}
          {disabled ? (
            <div className="mt-4 rounded-lg border border-amber-800/60 bg-amber-950/20 p-3 text-xs text-amber-300/90">
              Sign-in is not configured on this deployment (no identity provider set).
            </div>
          ) : (
            <a href={loginUrl}
               className="mt-5 flex w-full items-center justify-center gap-2.5 rounded-lg border border-slate-700 bg-slate-100 px-4 py-2.5 text-sm font-medium text-slate-900 transition hover:bg-white">
              <svg width="17" height="17" viewBox="0 0 18 18" aria-hidden>
                <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z" />
                <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z" />
                <path fill="#FBBC05" d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z" />
                <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.47.9 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z" />
              </svg>
              Sign in with Google
            </a>
          )}
          <p className="mt-4 text-center text-[11px] leading-relaxed text-slate-600">
            Read-only console. Your access is recorded. Session ends after 8 hours.
          </p>
        </div>
      </div>
    </div>
  );
}

export function ConnectError({ message }: { message: string }) {
  return (
    <div className="grid min-h-screen place-items-center p-6">
      <div className="w-full max-w-md space-y-3 rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
        <div className="flex justify-center"><Mark /></div>
        <p className="text-center text-sm text-slate-400">The console could not reach the gateway.</p>
        <p className="rounded-lg border border-amber-800/60 bg-amber-950/20 p-2.5 text-xs text-amber-300/90">{message}</p>
      </div>
    </div>
  );
}
