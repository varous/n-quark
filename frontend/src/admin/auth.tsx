import { createContext, useContext, useState, type ReactNode } from "react";
import { api, session, ApiError } from "./api";

type AuthState = { token: string | null; role: string; sub: string };
type AuthCtx = AuthState & {
  login: (username: string, role: string) => Promise<void>;
  logout: () => void;
  can: (role: string) => boolean;
};

const RANK = ["VIEWER", "ANALYST", "OPERATOR", "ADMIN"];
const Ctx = createContext<AuthCtx | null>(null);
export const useAuth = () => useContext(Ctx)!;

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ token: session.token, role: session.role, sub: session.sub });
  const value: AuthCtx = {
    ...state,
    async login(username, role) {
      const r = await api.login(username, role);
      session.set(r.token, r.role, r.sub);
      setState({ token: r.token, role: r.role, sub: r.sub });
    },
    logout() {
      session.clear();
      setState({ token: null, role: "", sub: "" });
    },
    can(role) {
      return RANK.indexOf(state.role) >= RANK.indexOf(role);
    },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("admin");
  const [role, setRole] = useState("OPERATOR");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, role);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 p-4 text-slate-100">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-xl border border-slate-800 bg-slate-900/70 p-6">
        <div>
          <h1 className="text-lg font-semibold">n-quark · Admin Console</h1>
          <p className="text-xs text-slate-400">Internal observability & data-governance. Development sign-in.</p>
        </div>
        <label className="block text-sm">
          <span className="mb-1 block text-slate-300">Username</span>
          <input className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm" value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-slate-300">Role</span>
          <select className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm" value={role} onChange={(e) => setRole(e.target.value)}>
            {RANK.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        {error && <div className="rounded border border-rose-800 bg-rose-950/40 p-2 text-xs text-rose-300">{error}</div>}
        <button disabled={busy} className="w-full rounded bg-sky-600 px-3 py-2 text-sm font-medium hover:bg-sky-500 disabled:opacity-50">
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="text-center text-xs text-slate-500">Dev auth is flag-gated and not a production identity provider.</p>
      </form>
    </div>
  );
}
