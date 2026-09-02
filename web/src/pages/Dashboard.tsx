import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { AgentShell } from "../components/AgentShell";
import type { Deal } from "../types";
import "./agent.css";

const STATUS: Record<string, { label: string; cls: string }> = {
  draft: { label: "Not started", cls: "" },
  in_progress: { label: "In progress", cls: "chip-brass" },
  ready_for_review: { label: "Ready for review", cls: "chip-sage" },
  sent_for_signature: { label: "Out for signature", cls: "chip-brass" },
  completed: { label: "Signed", cls: "chip-sage" },
};

export function Dashboard() {
  const nav = useNavigate();
  const [deals, setDeals] = useState<Deal[] | null>(null);
  const [creating, setCreating] = useState(false);

  const load = () =>
    api
      .get<Deal[]>("/api/agent/deals")
      .then(setDeals)
      .catch((e) => (e.status === 401 ? nav("/login") : undefined));

  useEffect(() => {
    load();
  }, []);

  return (
    <AgentShell>
      <div className="row-between page-head">
        <div>
          <div className="eyebrow">Disclosures</div>
          <h1>Your deals</h1>
        </div>
        <button className="btn btn-primary" onClick={() => setCreating(true)}>
          New deal
        </button>
      </div>

      {creating && <NewDeal onDone={() => { setCreating(false); load(); }} onCancel={() => setCreating(false)} />}

      {deals === null && <div className="skeleton skeleton-line" style={{ height: 80 }} />}

      {deals?.length === 0 && !creating && (
        <div className="empty card">
          <h3>No deals yet</h3>
          <p className="muted small">
            Create a deal, send the seller their link, and their answers land here as they go.
          </p>
          <button className="btn btn-brass" onClick={() => setCreating(true)}>
            Create the first one
          </button>
        </div>
      )}

      <div className="deal-list">
        {deals?.map((d) => {
          const s = STATUS[d.status] ?? STATUS.draft;
          return (
            <Link key={d.id} to={`/agent/deals/${d.id}`} className="deal card">
              <div className="deal-main">
                <div className="deal-addr">{d.property_address}</div>
                <div className="tiny muted">
                  {d.seller_name}
                  {d.city && ` · ${d.city}`}
                  {d.county && `, ${d.county} County`}
                </div>
              </div>

              <div className="deal-progress">
                <div className="deal-meter" aria-hidden>
                  <span style={{ width: `${d.percent}%` }} />
                </div>
                <span className="tiny muted">{d.percent}%</span>
              </div>

              <div className="deal-flags">
                {d.open_flags > 0 && (
                  <span className="chip chip-clay">
                    {d.open_flags} to check
                  </span>
                )}
              </div>

              <span className={`chip ${s.cls}`}>{s.label}</span>
            </Link>
          );
        })}
      </div>
    </AgentShell>
  );
}

function NewDeal({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState({
    property_address: "", city: "", county: "",
    seller_name: "", seller_email: "", co_seller_name: "", co_seller_email: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF({ ...f, [k]: e.target.value });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/agent/deals", f);
      onDone();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card new-deal rise" onSubmit={submit}>
      <h3 style={{ marginBottom: "1.1rem" }}>New deal</h3>
      <div className="grid-2">
        <label className="field" style={{ gridColumn: "1 / -1" }}>
          <span>Property address</span>
          <input className="input" required value={f.property_address}
                 placeholder="123 Demo Property Ln, Culver City, CA 90230"
                 onChange={set("property_address")} />
        </label>
        <label className="field"><span>City</span>
          <input className="input" value={f.city} onChange={set("city")} /></label>
        <label className="field"><span>County</span>
          <input className="input" value={f.county} onChange={set("county")} /></label>
        <label className="field"><span>Seller name</span>
          <input className="input" required value={f.seller_name} onChange={set("seller_name")} /></label>
        <label className="field"><span>Seller email</span>
          <input className="input" type="email" required value={f.seller_email} onChange={set("seller_email")} /></label>
        <label className="field"><span>Co-seller name <span className="muted tiny">optional</span></span>
          <input className="input" value={f.co_seller_name} onChange={set("co_seller_name")} /></label>
        <label className="field"><span>Co-seller email <span className="muted tiny">optional</span></span>
          <input className="input" type="email" value={f.co_seller_email} onChange={set("co_seller_email")} /></label>
      </div>
      {error && <p className="auth-error small">{error}</p>}
      <div className="row" style={{ justifyContent: "flex-end", marginTop: ".5rem" }}>
        <button type="button" className="btn btn-quiet" onClick={onCancel}>Cancel</button>
        <button className="btn btn-primary" disabled={busy}>{busy ? "Creating…" : "Create deal"}</button>
      </div>
    </form>
  );
}
