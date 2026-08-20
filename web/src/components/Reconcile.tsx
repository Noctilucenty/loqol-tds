import { useState } from "react";
import { api } from "../api";
import { Answering } from "./Answering";
import { isAnswered, type AnswerMap } from "../lib/gating";
import type { FormSpec, SellerState } from "../types";
import "./reconcile.css";

/**
 * The review step, and the answer to "what do you do when they give you an
 * answer that contradicts one from three questions ago?".
 *
 * Not at the moment it happens. Interrupting someone mid-recall to argue about
 * something they said forty minutes ago is how you lose them at 10pm, and the
 * contradiction is not urgent - the form is not filed yet. So conflicts are
 * detected on write, queued, and brought back here, phrased as a question rather
 * than an error, with both answers shown and neither pre-selected.
 *
 * Hard conflicts block submission because they would produce a self-contradictory
 * legal document. Soft ones are shown, and the seller can confirm both are right.
 */
export function Reconcile({
  token,
  state,
  form,
  onChange,
}: {
  token: string;
  state: SellerState;
  form: FormSpec;
  onChange: (s: SellerState) => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean } | null>(null);

  const byId = Object.fromEntries(form.questions.map((q) => [q.id, q]));
  const answers = state.answers as AnswerMap;
  const hard = state.flags.filter((f) => f.severity === "hard");
  const soft = state.flags.filter((f) => f.severity !== "hard");
  const blocked = hard.length > 0 || state.missingRequired.length > 0;

  /** Which questions a flag actually wants the seller to act on.
   *
   *  A rule names every question it inspects, but only some of them are the
   *  problem. "Section B is Yes but the explanation is empty" names both the
   *  Yes/No gate and the explanation - showing the gate is showing the one thing
   *  that is already answered, which reads as nonsense. Prefer the unanswered
   *  ones, and fall back to the whole set when everything is filled in and the
   *  contradiction is between two real values. */
  const questionsNeeding = (ids: string[]) => {
    const present = ids.map((id) => byId[id]).filter(Boolean);
    const empty = present.filter((q) => !isAnswered(answers, q.id));
    return empty.length ? empty : present;
  };

  // A question raised by a flag is handled there, with copy that explains why.
  // Listing it again under "Still needed" is the same ask twice in two voices.
  const coveredByFlag = new Set(state.flags.flatMap((f) => f.questionIds));
  const stillNeeded = state.missingRequired.filter((id) => !coveredByFlag.has(id));

  const saveAnswer = async (questionId: string, value: unknown, status = "answered") => {
    const next = await api.put<SellerState>(`/api/s/${token}/answers`, {
      question_id: questionId,
      value,
      status,
      source: "form",
    });
    onChange(next);
  };

  const resolve = async (flagId: string, questionId?: string, value?: unknown, status = "answered") => {
    const next = await api.post<SellerState>(`/api/s/${token}/flags/${flagId}/resolve`, {
      questionId,
      value,
      status,
    });
    onChange(next);
  };

  const submit = async () => {
    setSubmitting(true);
    try {
      const res = await api.post<any>(`/api/s/${token}/submit`);
      setResult({ ok: res.ok });
      if (res.ok) onChange(res);
      else onChange({ ...state, ...res });
    } finally {
      setSubmitting(false);
    }
  };

  if (result?.ok) {
    return (
      <section className="rise gate">
        <div className="eyebrow">Done</div>
        <h1 className="gate-title">That's everything.</h1>
        {/* No email promise. Nothing in this app sends one, and DocuSeal is
            called with send_email disabled - telling a seller to wait for a
            message that never arrives is worse than telling them nothing. */}
        <p className="lede">
          Your disclosure has gone to {state.agentName || "your agent"} for review. They will look
          it over and send it back to you to sign.
        </p>
        <p className="lede muted small">
          You can close this tab. If you remember something or spot a mistake, this same link
          still works &mdash; come back and change your answer any time before it is sent for
          signature.
        </p>
      </section>
    );
  }

  return (
    <section className="rise stack" style={{ ["--gap" as any]: "1.5rem" }}>
      <div>
        <div className="eyebrow">Last step</div>
        <h1>Let's check a few things.</h1>
      </div>

      {hard.length === 0 && soft.length === 0 && state.missingRequired.length === 0 && (
        <p className="lede">
          Nothing looks inconsistent. {state.progress.answered} answers recorded across the whole
          form.
        </p>
      )}

      {stillNeeded.length > 0 && (
        <div className="rec rec-open">
          <div className="rec-head">
            <span className="chip chip-brass">Still needed</span>
          </div>
          <p className="rec-q">
            {stillNeeded.length} question{stillNeeded.length === 1 ? "" : "s"} still{" "}
            {stillNeeded.length === 1 ? "needs" : "need"} an answer.
          </p>
          {/* Answerable here rather than listed. Sending someone back through
              twelve screens to find three gaps is the whole reason forms get
              abandoned at the last step. */}
          {stillNeeded.map((id) => {
            const q = byId[id];
            if (!q) return null;
            return (
              <div key={id} className="rec-fix">
                <div className="tiny muted rec-fix-label">{q.prompt}</div>
                <Answering
                  question={q}
                  answer={state.answers[id]}
                  onAnswer={(v, st) => saveAnswer(id, v, st ?? "answered")}
                />
              </div>
            );
          })}
        </div>
      )}

      {[...hard, ...soft].map((flag) => {
        const questions = questionsNeeding(flag.questionIds);
        return (
          <div key={flag.id} className={`rec ${flag.severity === "hard" ? "rec-hard" : "rec-soft"}`}>
            <div className="rec-head">
              <span className={`chip ${flag.severity === "hard" ? "chip-clay" : "chip-brass"}`}>
                {flag.severity === "hard" ? "Needs a decision" : "Worth a look"}
              </span>
            </div>
            <p className="rec-q">{flag.prompt || flag.message}</p>
            <p className="rec-detail small muted">{flag.message}</p>

            {questions.map((question) => (
              <div className="rec-fix" key={question.id}>
                <div className="tiny muted rec-fix-label">{question.prompt}</div>
                <Answering
                  question={question}
                  answer={state.answers[question.id]}
                  onAnswer={(v, s) => resolve(flag.id, question.id, v, s ?? "answered")}
                />
              </div>
            ))}

            {flag.severity !== "hard" && (
              <button className="btn btn-quiet" onClick={() => resolve(flag.id)}>
                Both are right, leave them
              </button>
            )}
          </div>
        );
      })}

      {result && !result.ok && (
        <p className="small" style={{ color: "var(--clay)" }}>
          There are still some things to settle above.
        </p>
      )}

      <div className="rec-submit">
        <button
          className="btn btn-primary btn-lg btn-block"
          onClick={submit}
          disabled={submitting || blocked}
        >
          {submitting ? "Sending…" : "Send to my agent"}
        </button>
        {blocked && (
          <p className="tiny muted" style={{ marginTop: ".6rem", textAlign: "center" }}>
            Settle the items above first.
          </p>
        )}
      </div>
    </section>
  );
}
