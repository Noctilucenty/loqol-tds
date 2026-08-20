import { useEffect, useRef, useState } from "react";
import type { AnswerRecord, Question } from "../types";
import "./answering.css";

interface Props {
  question: Question;
  answer?: AnswerRecord;
  onAnswer: (value: unknown, status?: "answered" | "unknown") => void;
  autoFocus?: boolean;
}

/** Yes / No / I'm not sure.
 *
 *  "I'm not sure" is given the same visual weight as the other two rather than
 *  being hidden behind a link. On a disclosure form an unsure seller who feels
 *  nudged toward No is the single most expensive outcome the product can
 *  produce, so the honest answer is never the inconvenient one. */
function TriChoice({ answer, onAnswer }: Pick<Props, "answer" | "onAnswer">) {
  const current = answer?.status === "unknown" ? "unknown" : (answer?.value as string | undefined);
  const options = [
    { id: "yes", label: "Yes" },
    { id: "no", label: "No" },
    { id: "unknown", label: "I'm not sure" },
  ];
  return (
    <div className="tri">
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          className={`tri-btn ${current === o.id ? "is-on" : ""} ${o.id === "unknown" ? "tri-unsure" : ""}`}
          aria-pressed={current === o.id}
          onClick={() => onAnswer(o.id === "unknown" ? "unknown" : o.id, o.id === "unknown" ? "unknown" : "answered")}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function BoolChoice({ answer, onAnswer }: Pick<Props, "answer" | "onAnswer">) {
  const v = answer?.value;
  return (
    <div className="tri">
      {[
        { id: true, label: "Yes" },
        { id: false, label: "No" },
      ].map((o) => (
        <button
          key={String(o.id)}
          type="button"
          className={`tri-btn ${v === o.id ? "is-on" : ""}`}
          aria-pressed={v === o.id}
          onClick={() => onAnswer(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Choices({ question, answer, onAnswer, multi }: Props & { multi: boolean }) {
  const selected: string[] = multi
    ? ((answer?.value as string[]) ?? [])
    : answer?.value
      ? [answer.value as string]
      : [];

  const toggle = (id: string) => {
    if (!multi) return onAnswer(id);
    const next = selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id];
    onAnswer(next);
  };

  return (
    <div className="choices" role={multi ? "group" : "radiogroup"}>
      {question.options.map((o) => {
        const on = selected.includes(o.id);
        return (
          <button
            key={o.id}
            type="button"
            className={`choice ${on ? "is-on" : ""}`}
            aria-pressed={on}
            onClick={() => toggle(o.id)}
          >
            <span className={`choice-mark ${multi ? "is-box" : "is-dot"}`} aria-hidden>
              {on && multi && (
                <svg viewBox="0 0 14 14" width="11" height="11">
                  <path d="M2 7.4 5.4 11 12 3.4" fill="none" stroke="currentColor" strokeWidth="2.2"
                        strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
            </span>
            <span>{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}

/** Free text. Saves on blur and on a debounce, never on a Save button - the
 *  seller should not have to know that saving is a thing that happens. */
function TextAnswer({ question, answer, onAnswer, autoFocus }: Props) {
  const [draft, setDraft] = useState(String(answer?.value ?? ""));
  const ref = useRef<HTMLTextAreaElement | HTMLInputElement | null>(null);
  const timer = useRef<number>();

  useEffect(() => {
    setDraft(String(answer?.value ?? ""));
  }, [question.id]);

  useEffect(() => {
    if (autoFocus) ref.current?.focus();
  }, [autoFocus, question.id]);

  const change = (v: string) => {
    setDraft(v);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => onAnswer(v), 700);
  };
  const flush = () => {
    window.clearTimeout(timer.current);
    if (draft !== String(answer?.value ?? "")) onAnswer(draft);
  };

  if (question.kind === "longtext") {
    return (
      <textarea
        ref={ref as React.RefObject<HTMLTextAreaElement>}
        className="input"
        value={draft}
        placeholder={question.example ? `For example: ${question.example}` : "Type your answer"}
        onChange={(e) => change(e.target.value)}
        onBlur={flush}
      />
    );
  }
  if (question.kind === "int") {
    return (
      <input
        ref={ref as React.RefObject<HTMLInputElement>}
        className="input input-short"
        type="number"
        min={0}
        inputMode="numeric"
        value={draft}
        onChange={(e) => change(e.target.value)}
        onBlur={flush}
      />
    );
  }
  return (
    <input
      ref={ref as React.RefObject<HTMLInputElement>}
      className="input"
      value={draft}
      placeholder={question.example || "Type your answer"}
      onChange={(e) => change(e.target.value)}
      onBlur={flush}
    />
  );
}

export function Answering(props: Props) {
  switch (props.question.kind) {
    case "tri":
      return <TriChoice {...props} />;
    case "bool":
      return <BoolChoice {...props} />;
    case "multi":
      return <Choices {...props} multi />;
    case "single":
      return <Choices {...props} multi={false} />;
    default:
      return <TextAnswer {...props} />;
  }
}

/** The enumeration grid: many closed-set items, scanned and tapped in parallel. */
export function ToggleGrid({
  questions,
  answers,
  onAnswer,
}: {
  questions: Question[];
  answers: Record<string, AnswerRecord>;
  onAnswer: (id: string, value: boolean) => void;
}) {
  return (
    <div className="grid-toggles">
      {questions.map((q) => {
        const on = answers[q.id]?.value === true;
        const touched = answers[q.id] !== undefined;
        return (
          <button
            key={q.id}
            type="button"
            className={`tile ${on ? "is-on" : ""} ${touched && !on ? "is-off" : ""}`}
            aria-pressed={on}
            onClick={() => onAnswer(q.id, !on)}
          >
            <span className="tile-mark" aria-hidden>
              {on && (
                <svg viewBox="0 0 14 14" width="12" height="12">
                  <path d="M2 7.4 5.4 11 12 3.4" fill="none" stroke="currentColor" strokeWidth="2.4"
                        strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
            </span>
            <span className="tile-label">{q.prompt}</span>
          </button>
        );
      })}
    </div>
  );
}
