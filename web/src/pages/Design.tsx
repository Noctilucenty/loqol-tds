import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { FormSpec } from "../types";
import "./agent.css";
import "./design.css";

const WHY_LABEL: Record<string, string> = {
  enumeration: "Enumeration",
  precision: "Precision",
  gate: "Gate",
  comprehension: "Comprehension",
  narrative: "Narrative",
  compound: "Compound",
  agent_owned: "Not the seller's question",
};

/**
 * The routing table, generated from the same spec the form renders from. It is a
 * page in the product rather than a paragraph in the README because the decision
 * it documents is the product, and because a table that is generated cannot drift
 * away from the behaviour it claims to describe.
 */
export function Design() {
  const [spec, setSpec] = useState<FormSpec | null>(null);
  useEffect(() => {
    api.get<FormSpec>("/api/agent/form-spec").then(setSpec).catch(() => {});
  }, []);

  if (!spec) return <div className="wrap page"><div className="skeleton skeleton-line" style={{ height: 90 }} /></div>;

  const groups = Object.entries(
    spec.questions.reduce<Record<string, typeof spec.questions>>((acc, q) => {
      (acc[q.why] ??= []).push(q);
      return acc;
    }, {}),
  ).sort((a, b) => b[1].length - a[1].length);

  const voice = spec.questions.filter((q) => q.lane === "voice").length;
  const tap = spec.questions.length - voice;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="wrap row-between topbar-inner">
          <Link to="/agent" className="brand">
            <span className="brand-mark" aria-hidden />
            <span className="brand-name">Loqol</span>
            <span className="brand-sub tiny muted">Disclosures</span>
          </Link>
          <Link to="/agent" className="navlink tiny">← Back</Link>
        </div>
      </header>

      <main className="wrap page">
        <div className="design-hero">
          <div className="eyebrow">Design notes</div>
          <h1 className="design-thesis">
            Speak when the bottleneck is understanding.<br />
            Tap when the bottleneck is enumeration.
          </h1>
          <p className="lede">
            The intuitive split is voice for the scary legal parts and tapping for the easy parts.
            That split produces a seller reading fifty appliance names aloud, and it is wrong.
            What actually decides the lane is where the difficulty sits: in working out what is
            being asked, or in getting a long closed list of answers out of your head.
          </p>
          <div className="design-counts">
            <div><strong>{tap}</strong><span>tap</span></div>
            <div><strong>{voice}</strong><span>voice</span></div>
            <div><strong>{spec.questions.length}</strong><span>questions</span></div>
          </div>
        </div>

        {groups.map(([why, questions]) => (
          <section key={why} className="design-block">
            <div className="design-block-head">
              <span className={`chip ${questions[0].lane === "voice" ? "chip-brass" : ""}`}>
                {questions[0].lane === "voice" ? "Voice" : "Tap"}
              </span>
              <h2>{WHY_LABEL[why] ?? why}</h2>
              <span className="tiny muted">{questions.length} questions</span>
            </div>
            <p className="design-rationale">{spec.rationale[why]}</p>
            <div className="design-examples">
              {questions.slice(0, 6).map((q) => (
                <span key={q.id} className="design-pill">{q.prompt}</span>
              ))}
              {questions.length > 6 && (
                <span className="design-pill is-more">+{questions.length - 6} more</span>
              )}
            </div>
          </section>
        ))}

        <section className="design-block">
          <div className="design-block-head">
            <h2>Where the two lanes meet</h2>
          </div>
          <p className="design-rationale">
            Routing is a default, never a lock. Every question renders its tap control regardless
            of lane, and the voice agent can answer any question in the graph. Both write through
            one server-side path, so an answer given aloud and an answer tapped are the same row in
            the same table — which is what makes it possible to start in one lane, finish in the
            other, and lose nothing. When the two lanes disagree about a question, the later answer
            stands and the disagreement is queued for the seller to settle at review, rather than
            being silently overwritten or thrown in their face mid-sentence.
          </p>
        </section>
      </main>
    </div>
  );
}
