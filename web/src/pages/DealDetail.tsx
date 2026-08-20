import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { AgentShell } from "../components/AgentShell";
import type { Deal, FormSpec } from "../types";
import "./agent.css";

interface Review {
  deal: Deal;
  answers: Record<string, { value: unknown; status: string; source: string; revision: number; transcript: string | null; updated_at: string }>;
  progress: { percent: number; answered: number; total: number; chapters: any[] };
  flags: { id: string; rule_id: string; severity: string; message: string; prompt: string; question_ids: string[] }[];
  missing_required: string[];
  field_count: number;
  addendum_blocks: number;
}

interface HistoryRow {
  question_id: string; prompt: string; value: unknown; previous_value: unknown;
  changed: boolean; status: string; source: string; transcript: string | null;
  actor: string; created_at: string;
}

export function DealDetail() {
  const { dealId = "" } = useParams();
  const [review, setReview] = useState<Review | null>(null);
  const [form, setForm] = useState<FormSpec | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryRow[] | null>(null);
  const [tab, setTab] = useState<"answers" | "history">("answers");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = () => {
    api.get<Review>(`/api/agent/deals/${dealId}/review`).then(setReview);
    api.get<FormSpec>("/api/agent/form-spec").then(setForm);
  };
  useEffect(load, [dealId]);

  const makeLink = async () => {
    setBusy("link");
    try {
      const r = await api.post<{ url: string }>(`/api/agent/deals/${dealId}/link`);
      setLink(r.url);
      setNotice("Link created. It replaces any link sent before.");
      load();
    } finally {
      setBusy(null);
    }
  };

  const revoke = async () => {
    setBusy("revoke");
    try {
      await api.del(`/api/agent/deals/${dealId}/link`);
      setLink(null);
      setNotice("Link revoked. The old one no longer opens.");
      load();
    } finally {
      setBusy(null);
    }
  };

  const send = async () => {
    setBusy("send");
    setNotice(null);
    try {
      const r = await api.post<{ sign_url: string }>(`/api/agent/deals/${dealId}/send-for-signature`);
      setNotice(r.sign_url ? "Sent to DocuSeal." : "Submission created.");
      if (r.sign_url) window.open(r.sign_url, "_blank", "noopener");
      load();
    } catch (e: any) {
      setNotice(e.message);
    } finally {
      setBusy(null);
    }
  };

  const openHistory = async () => {
    setTab("history");
    if (!history) setHistory(await api.get<HistoryRow[]>(`/api/agent/deals/${dealId}/history`));
  };

  if (!review || !form) {
    return <AgentShell><div className="skeleton skeleton-line" style={{ height: 120 }} /></AgentShell>;
  }

  const chapters = form.chapters.filter((c) => c.id !== "review");

  return (
    <AgentShell>
      <div className="page-head">
        <Link to="/agent" className="navlink tiny">← All deals</Link>
        <h1 style={{ marginTop: ".5rem" }}>{review.deal.property_address}</h1>
        <p className="muted small" style={{ margin: ".3rem 0 0" }}>
          {review.deal.seller_name} · {review.deal.seller_email}
        </p>
      </div>

      {notice && <div className="card panel" style={{ marginBottom: "1.1rem" }}><p className="small" style={{ margin: 0 }}>{notice}</p></div>}

      <div className="detail-grid">
        <div>
          <div className="card panel">
            <div className="row-between" style={{ marginBottom: "1rem" }}>
              <div>
                <h3>Submitted answers</h3>
                <p className="panel-note" style={{ margin: 0 }}>
                  {review.progress.answered} of {review.progress.total} answered ·
                  {" "}{review.field_count} form fields resolved
                  {review.addendum_blocks > 0 && ` · ${review.addendum_blocks} addendum block${review.addendum_blocks === 1 ? "" : "s"}`}
                </p>
              </div>
              <div className="row" style={{ gap: ".4rem" }}>
                <button className={`btn btn-quiet tiny ${tab === "answers" ? "is-on" : ""}`}
                        onClick={() => setTab("answers")}>Answers</button>
                <button className={`btn btn-quiet tiny ${tab === "history" ? "is-on" : ""}`}
                        onClick={openHistory}>History</button>
              </div>
            </div>

            {tab === "answers" && chapters.map((c) => {
              const qs = form.questions.filter((q) => q.chapter === c.id && review.answers[q.id]);
              if (!qs.length) return null;
              return (
                <div key={c.id}>
                  <div className="section-h eyebrow">{c.title}</div>
                  {qs.map((q) => {
                    const a = review.answers[q.id];
                    return (
                      <div key={q.id} className="answer-row">
                        <span className="answer-q">{q.prompt}</span>
                        <span className={`answer-v ${a.status === "unknown" ? "is-unknown" : ""}`}>
                          <span className={`src-dot src-${a.source}`} title={`answered by ${a.source}`} />
                          {renderValue(a, q)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              );
            })}

            {tab === "history" && (
              <div>
                <p className="panel-note">
                  Every write, newest first. Answers that were changed show what they were.
                </p>
                {history?.map((h, i) => (
                  <div key={i} className="history-row">
                    <div className="tiny muted">
                      {new Date(h.created_at).toLocaleString()} · {h.source} · {h.actor === "seller" ? "seller" : "agent"}
                    </div>
                    <div className="history-change">
                      {h.prompt}{" — "}
                      {h.changed && h.previous_value !== null && h.previous_value !== undefined ? (
                        <>
                          <del>{String(h.previous_value)}</del> <ins>{String(h.value)}</ins>
                        </>
                      ) : (
                        <ins>{String(h.value)}</ins>
                      )}
                    </div>
                    {h.transcript && <div className="tiny muted">“{h.transcript}”</div>}
                  </div>
                ))}
                {history?.length === 0 && <p className="muted small">Nothing yet.</p>}
              </div>
            )}
          </div>
        </div>

        <aside>
          <div className="card panel">
            <h3>Seller link</h3>
            <p className="panel-note">
              The link is the credential. Anyone holding it can answer, so send it directly and
              rotate it if it goes astray.
            </p>
            {link && (
              <div className="linkbox">
                <code>{link}</code>
                <button className="btn btn-quiet tiny" onClick={() => navigator.clipboard?.writeText(link)}>
                  Copy
                </button>
              </div>
            )}
            <div className="row" style={{ gap: ".5rem" }}>
              <button className="btn btn-brass" onClick={makeLink} disabled={busy === "link"}>
                {review.deal.link_issued ? "Replace link" : "Create link"}
              </button>
              {review.deal.link_issued && (
                <button className="btn btn-ghost" onClick={revoke} disabled={busy === "revoke"}>
                  Revoke
                </button>
              )}
            </div>
            {review.deal.link_last_used && (
              <p className="tiny muted" style={{ marginTop: ".7rem", marginBottom: 0 }}>
                Last opened {new Date(review.deal.link_last_used).toLocaleString()}
              </p>
            )}
          </div>

          <div className="card panel">
            <h3>Progress</h3>
            <p className="panel-note" style={{ marginBottom: ".8rem" }}>{review.progress.percent}% complete</p>
            {review.progress.chapters.map((c: any) => (
              <div key={c.id} className="answer-row" style={{ gridTemplateColumns: "1fr auto" }}>
                <span className="answer-q">{c.title}</span>
                <span className="tiny muted">{c.answered}/{c.total}</span>
              </div>
            ))}
          </div>

          {review.flags.length > 0 && (
            <div className="card panel">
              <h3>To check</h3>
              <p className="panel-note">Raised automatically. The seller sees these at review too.</p>
              {review.flags.map((f) => (
                <div key={f.id} style={{ marginBottom: ".9rem" }}>
                  <span className={`chip ${f.severity === "hard" ? "chip-clay" : "chip-brass"}`}>
                    {f.severity}
                  </span>
                  <p className="small" style={{ margin: ".4rem 0 0" }}>{f.message}</p>
                </div>
              ))}
            </div>
          )}

          <div className="card panel">
            <h3>Form</h3>
            <p className="panel-note">
              The preview is rendered from the answers directly, so it is always available.
            </p>
            <a className="btn btn-ghost btn-block" href={`/api/agent/deals/${dealId}/preview.pdf`}
               target="_blank" rel="noreferrer">
              Open filled PDF
            </a>
            <button className="btn btn-primary btn-block" style={{ marginTop: ".55rem" }}
                    onClick={send} disabled={busy === "send"}>
              {busy === "send" ? "Sending…" : "Send for signature"}
            </button>
            {review.missing_required.length > 0 && (
              <p className="tiny muted" style={{ marginTop: ".6rem", marginBottom: 0 }}>
                {review.missing_required.length} required question
                {review.missing_required.length === 1 ? "" : "s"} still unanswered.
              </p>
            )}
          </div>
        </aside>
      </div>
    </AgentShell>
  );
}

function renderValue(a: { value: unknown; status: string }, q: any): string {
  if (a.status === "unknown") return "Not sure";
  const v = a.value;
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (Array.isArray(v)) {
    const labels = v.map((id) => q.options.find((o: any) => o.id === id)?.label ?? id);
    return labels.join(", ") || "None";
  }
  if (v === "yes") return "Yes";
  if (v === "no") return "No";
  if (v === "unknown") return "Not sure";
  const s = String(v ?? "");
  const opt = q.options?.find((o: any) => o.id === s);
  if (opt) return opt.label;
  return s.length > 70 ? `${s.slice(0, 70)}…` : s;
}
