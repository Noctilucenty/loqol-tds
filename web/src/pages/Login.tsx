import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import "./agent.css";

const DEMO = { email: "agent@loqol.ai", password: "disclosure-demo-1" };

export function Login() {
  const nav = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [form, setForm] = useState({ email: "", password: "", name: "", brokerage: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent, override?: typeof DEMO) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body = override ?? form;
    try {
      await api.post(`/api/auth/${override ? "login" : mode}`, body);
      nav("/agent");
    } catch (err: any) {
      // The seeded demo account may not exist yet on a fresh database.
      if (override && err.status === 401) {
        try {
          await api.post("/api/auth/register", { ...DEMO, name: "Demo Agent", brokerage: "Loqol" });
          nav("/agent");
          return;
        } catch (e2: any) {
          setError(e2.message);
        }
      } else {
        setError(err.message);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth">
      <div className="auth-art">
        <div className="auth-art-inner">
          <div className="eyebrow" style={{ color: "#a8a396" }}>Loqol</div>
          <h1 className="auth-quote">
            Disclosures are the messiest part of selling a home.
          </h1>
          <p className="auth-sub">
            Three pages of statute, sixteen questions most owners cannot parse, and a form that
            gets put off until it is late. This is the part we made answerable.
          </p>
        </div>
      </div>

      <div className="auth-panel">
        <form className="auth-form" onSubmit={(e) => submit(e)}>
          <div className="eyebrow">Agent access</div>
          <h2 style={{ margin: ".4rem 0 1.5rem" }}>
            {mode === "login" ? "Sign in" : "Create an account"}
          </h2>

          {mode === "register" && (
            <>
              <label className="field">
                <span>Your name</span>
                <input className="input" required value={form.name}
                       onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </label>
              <label className="field">
                <span>Brokerage</span>
                <input className="input" value={form.brokerage}
                       onChange={(e) => setForm({ ...form, brokerage: e.target.value })} />
              </label>
            </>
          )}

          <label className="field">
            <span>Email</span>
            <input className="input" type="email" required autoComplete="email" value={form.email}
                   onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </label>
          <label className="field">
            <span>Password</span>
            <input className="input" type="password" required minLength={8}
                   autoComplete={mode === "login" ? "current-password" : "new-password"}
                   value={form.password}
                   onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </label>

          {error && <p className="auth-error small">{error}</p>}

          <button className="btn btn-primary btn-block btn-lg" disabled={busy} type="submit">
            {busy ? "One moment…" : mode === "login" ? "Sign in" : "Create account"}
          </button>

          <button type="button" className="btn btn-ghost btn-block" style={{ marginTop: ".6rem" }}
                  onClick={(e) => submit(e as any, DEMO)} disabled={busy}>
            Use the demo account
          </button>

          <p className="tiny muted" style={{ marginTop: "1.3rem", textAlign: "center" }}>
            {mode === "login" ? "No account yet?" : "Already have one?"}{" "}
            <button type="button" className="explain-link"
                    onClick={() => setMode(mode === "login" ? "register" : "login")}>
              {mode === "login" ? "Create one" : "Sign in"}
            </button>
          </p>
        </form>
      </div>
    </div>
  );
}
