import { useState } from "react";
import type { Question } from "../types";
import "./explain.css";

/**
 * The answer to "what do you do when they don't understand the question?".
 *
 * Three layers, each one click deeper: a plain-English gloss, a concrete
 * example, and the statutory wording itself. The statutory wording is last but
 * never removed - the seller is signing that sentence, so hiding it would be
 * dishonest, and burying it behind a click is what stops it from being the
 * thing they try to parse first.
 */
export function Explain({ question }: { question: Question }) {
  const [open, setOpen] = useState(false);
  const [legal, setLegal] = useState(false);

  if (!question.explain && !question.example && !question.legal) return null;

  return (
    <div className="explain">
      {!open ? (
        <button className="explain-toggle" type="button" onClick={() => setOpen(true)}>
          <span className="explain-glyph" aria-hidden>?</span>
          What does this mean?
        </button>
      ) : (
        <div className="explain-body rise">
          {question.explain && <p className="explain-text">{question.explain}</p>}
          {question.example && (
            <p className="explain-example">
              <span className="eyebrow">For example</span>
              <span>“{question.example}”</span>
            </p>
          )}
          {question.legal && (
            <div className="explain-legal">
              {legal ? (
                <>
                  <span className="eyebrow">The form's exact wording</span>
                  <p className="explain-legal-text">{question.legal}</p>
                </>
              ) : (
                <button className="explain-link" type="button" onClick={() => setLegal(true)}>
                  Show the form's exact wording
                </button>
              )}
            </div>
          )}
          <button className="explain-link" type="button" onClick={() => setOpen(false)}>
            Close
          </button>
        </div>
      )}
    </div>
  );
}
