import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import type { AgentUser } from "../types";

export function AgentShell({ children }: { children: React.ReactNode }) {
  const nav = useNavigate();
  const [me, setMe] = useState<AgentUser | null>(null);

  useEffect(() => {
    api.get<AgentUser>("/api/auth/me").then(setMe).catch(() => nav("/login"));
  }, []);

  const logout = async () => {
    await api.post("/api/auth/logout");
    nav("/login");
  };

  return (
    <div className="shell">
      <header className="topbar">
        <div className="wrap row-between topbar-inner">
          <Link to="/agent" className="brand">
            <span className="brand-mark" aria-hidden />
            <span className="brand-name">Loqol</span>
            <span className="brand-sub tiny muted">Disclosures</span>
          </Link>
          <nav className="row" style={{ gap: "1.15rem" }}>
            <Link to="/design" className="navlink tiny">Design notes</Link>
            {me && <span className="tiny muted">{me.name}</span>}
            <button className="btn btn-quiet tiny" onClick={logout}>Sign out</button>
          </nav>
        </div>
      </header>
      <main className="wrap page">{children}</main>
    </div>
  );
}
