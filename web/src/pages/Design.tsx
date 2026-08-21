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
            Talk about the questions that are hard to understand.<br />
            Tap the ones that are just long.
          </h1>
          <p className="lede">
            My first instinct was to put voice on the scary legal sections and leave tapping for
            the easy stuff. Then I actually read the form. Section A is fifty appliance names, and
            imagining someone saying all fifty out loud settled it.
          </p>
          <p className="lede">
            So the question I ended up asking about each one was: what is actually slow here? If
            it is working out what is being asked, talking helps. If it is just getting a long
            list out of your head, tapping wins and it is not close.
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
              <span className={`chip ${
                why === "agent_owned" ? "chip-sage" : questions[0].lane === "voice" ? "chip-brass" : ""
              }`}>
                {why === "agent_owned"
                  ? "Agent"
                  : questions[0].lane === "voice"
                    ? "Voice"
                    : "Tap"}
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
            <h2>None of this is a lock</h2>
          </div>
          <p className="design-rationale">
            All of the above is about what happens by default. Every question still shows its tap
            controls, every question can be answered out loud, and the welcome screen offers to
            talk you through the whole form if that is what you would rather do.
          </p>
          <p className="design-rationale">
            Both paths call the same function on the server, so an answer you said and an answer
            you tapped are the same row in the same table. That is what lets you start one way and
            finish the other without losing anything.
          </p>
          <p className="design-rationale">
            If the two disagree — you tapped no, then told the assistant yes — the newer answer
            wins, because it is newer. But the disagreement gets saved and brought back at the end.
            Changing your mind usually means you remembered something, and that is worth noticing
            rather than quietly overwriting.
          </p>
        </section>
      </main>
    </div>
  );
}
