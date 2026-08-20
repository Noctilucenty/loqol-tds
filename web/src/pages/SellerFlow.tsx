import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { Answering, ToggleGrid } from "../components/Answering";
import { VoicePanel } from "../components/VoicePanel";
import { Explain } from "../components/Explain";
import { Reconcile } from "../components/Reconcile";
import { buildSteps, stepIsComplete, type Step } from "../lib/gating";
import type { SellerBootstrap, SellerState } from "../types";
import "./seller.css";

type Save = "idle" | "saving" | "saved";

export function SellerFlow() {
  const { token = "" } = useParams();
  const [boot, setBoot] = useState<SellerBootstrap | null>(null);
  const [state, setState] = useState<SellerState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stepKey, setStepKey] = useState<string | null>(null);
  const [started, setStarted] = useState(false);
  const [save, setSave] = useState<Save>("idle");

  /** Every write from the tap lane goes through one chain.
   *
   *  They all mutate a single shared answer set, and the server runs requests
   *  concurrently, so two writes issued a hundred milliseconds apart can be
   *  serviced in either order. That is how a grid commit ends up reading state
   *  from before the tap that preceded it and writing the implied "no" over a
   *  real answer. Serialising here means arrival order is send order, and it
   *  also stops an older response from setState-ing over a newer one. */
  //: Last step index we actually resolved. Declared here with the other hooks -
  //: it is read further down, past several early returns, and a hook after a
  //: conditional return changes the hook count between renders (React #310).
  const lastKnown = useRef(0);

  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const enqueue = useCallback(<T,>(job: () => Promise<T>): Promise<T> => {
    const run = queue.current.then(job, job);
    queue.current = run.catch(() => undefined);
    return run;
  }, []);

  useEffect(() => {
    api
      .get<SellerBootstrap>(`/api/s/${token}`)
      .then((d) => {
        setBoot(d);
        setState(d);
      })
      .catch((e: ApiError) => setError(e.message));
  }, [token]);

  const form = boot?.form;
  const answers = state?.answers ?? {};

  const steps = useMemo<Step[]>(() => {
    if (!form) return [];
    const sellerChapters = form.chapters.filter(
      (c) => c.audience === "seller" && c.id !== "review",
    );
    return buildSteps(form.questions, sellerChapters, answers);
  }, [form, answers]);

  // Resume where they left off. The cursor is written server-side on every
  // answer, so closing the tab mid-question costs at most that question.
  useEffect(() => {
    if (!state || !steps.length || started || stepKey) return;
    const found = state.cursor
      ? steps.find(
          (s) =>
            (s.kind === "question" && s.question.id === state.cursor) ||
            (s.kind === "group" && s.questions.some((q) => q.id === state.cursor)),
        )
      : undefined;
    setStepKey(found?.key ?? steps[0]?.key ?? null);
  }, [state, steps, started, stepKey]);

  const answer = useCallback(
    async (questionId: string, value: unknown, status: "answered" | "unknown" = "answered") => {
      setSave("saving");
      // Optimistic, so tapping never feels like it is waiting on a network.
      setState((s) =>
        s
          ? {
              ...s,
              answers: {
                ...s.answers,
                [questionId]: {
                  value,
                  status,
                  source: "form",
                  revision: (s.answers[questionId]?.revision ?? 0) + 1,
                },
              },
            }
          : s,
      );
      try {
        const next = await enqueue(() =>
          api.put<SellerState & { supersededOtherLane: boolean }>(`/api/s/${token}/answers`, {
            question_id: questionId,
            value,
            status,
            source: "form",
            known_revision: answers[questionId]?.revision ?? null,
          }),
        );
        setState(next);
        setSave("saved");
        window.setTimeout(() => setSave("idle"), 1400);
      } catch (e: any) {
        setSave("idle");
        setError(e.message);
      }
    },
    [token, answers, enqueue],
  );

  const refresh = useCallback(async () => {
    try {
      setState(await api.get<SellerState>(`/api/s/${token}/state`));
    } catch {
      /* a failed refresh is not worth interrupting the seller for */
    }
  }, [token]);

  if (error && !boot) return <SellerError message={error} />;
  if (!boot || !state || !form) return <SellerLoading />;

  if (!started && state.progress.answered === 0) {
    return <Welcome state={state} onStart={() => setStarted(true)} />;
  }
  if (!started && state.progress.answered > 0) {
    return <Resume state={state} onStart={() => setStarted(true)} />;
  }

  // Resolved by key, so a step list that grew or shrank under us does not move
  // the seller. If the current step disappeared entirely - the answer that
  // opened it was changed - fall forward to the next surviving step.
  // If the current step disappeared - the answer that opened it changed - hold
  // position and fall forward. Clamping a -1 to 0 teleported the seller back to
  // the very first screen mid-interview.
  const found = steps.findIndex((s) => s.key === stepKey);
  if (found >= 0) lastKnown.current = found;
  const index = found >= 0 ? found : Math.min(lastKnown.current, Math.max(steps.length - 1, 0));
  const step = steps[Math.min(index, steps.length - 1)];

  /** Leaving a grid commits the implied "no" for every tile left untouched, so
   *  the stored answer set is explicit rather than relying on absence.
   *
   *  The whole group is handed to the server, which decides which are actually
   *  unanswered. Filtering here against the optimistic local copy would race a
   *  tap that has not round-tripped yet and overwrite it with false. */
  const advance = () => {
    const current = step;
    const next = steps[Math.min(index + 1, steps.length - 1)];
    if (next) setStepKey(next.key);
    if (current?.kind === "group") {
      enqueue(() =>
        api.post<SellerState>(`/api/s/${token}/answers/commit-group`, {
          questionIds: current.questions.map((q) => q.id),
        }),
      )
        .then(setState)
        .catch(() => undefined);
    }
  };
  const chapter = form.chapters.find(
    (c) => step && step.kind !== "review" && c.id === (step as any).chapterId,
  );
  const canAdvance = step ? stepIsComplete(step, answers) : false;
  const isLast = index >= steps.length - 1;

  return (
    <div className="seller">
      <header className="seller-top">
        <div className="wrap wrap-narrow seller-top-inner">
          <div className="seller-addr tiny muted">{state.property.address}</div>
          <div className="seller-meter" aria-hidden>
            <span style={{ width: `${state.progress.percent}%` }} />
          </div>
          <div className="seller-meta tiny">
            <span className="muted">
              {state.progress.percent}% done
              {state.progress.minutes_left > 0 && ` · about ${state.progress.minutes_left} min left`}
            </span>
            <span className={`saveflag ${save}`}>
              {save === "saving" ? "Saving…" : save === "saved" ? "Saved" : "Saved automatically"}
            </span>
          </div>
        </div>
      </header>

      <main className="wrap wrap-narrow seller-main">
        {step?.kind === "intro" && chapter && (
          <section className="rise stack" style={{ ["--gap" as any]: "1rem" }}>
            <div className="eyebrow">
              Section {form.chapters.filter((c) => c.audience === "seller" && c.id !== "review")
                .findIndex((c) => c.id === chapter.id) + 1}
            </div>
            <h1>{chapter.title}</h1>
            <p className="lede">{chapter.blurb}</p>
            {chapter.id === "awareness" && (
              <p className="lede">
                These are the sixteen questions the state requires. Most people have not thought
                about half of them. Take them one at a time, and say so whenever you are not sure.
              </p>
            )}
          </section>
        )}

        {step?.kind === "group" && (
          <section className="rise stack">
            <div className="eyebrow">{chapter?.title}</div>
            <h2>{step.group}</h2>
            <p className="muted small">
              Tap everything the property has. Leave the rest.
            </p>
            <ToggleGrid
              questions={step.questions}
              answers={answers}
              onAnswer={(id, v) => answer(id, v)}
            />
            <p className="tiny muted none-note">
              Anything you do not tap is recorded as “no”.
            </p>
          </section>
        )}

        {step?.kind === "question" && (
          <QuestionStep
            key={step.question.id}
            token={token}
            question={step.question}
            answers={answers}
            onAnswer={answer}
            onVoiceAnswer={refresh}
            chapterTitle={chapter?.title ?? ""}
          />
        )}

        {step?.kind === "review" && (
          <Reconcile token={token} state={state} form={form} onChange={setState} />
        )}
      </main>

      {step?.kind !== "review" && (
        <footer className="seller-foot">
          <div className="wrap wrap-narrow row-between">
            <button
              className="btn btn-quiet"
              onClick={() => setStepKey(steps[Math.max(0, index - 1)]?.key ?? null)}
              disabled={index === 0}
            >
              Back
            </button>
            <div className="row" style={{ gap: ".6rem" }}>
              {step?.kind === "question" && !canAdvance && (
                <button
                  className="btn btn-quiet"
                  onClick={() => setStepKey(steps[Math.min(index + 1, steps.length - 1)]?.key ?? null)}
                >
                  Skip for now
                </button>
              )}
              <button
                className="btn btn-primary btn-lg"
                onClick={advance}
                disabled={step?.kind === "question" && !canAdvance}
              >
                {isLast ? "Review" : "Continue"}
              </button>
            </div>
          </div>
        </footer>
      )}
    </div>
  );
}

function QuestionStep({
  token,
  question,
  answers,
  onAnswer,
  onVoiceAnswer,
  chapterTitle,
}: {
  token: string;
  question: any;
  answers: any;
  onAnswer: (id: string, v: unknown, s?: "answered" | "unknown") => void;
  onVoiceAnswer: () => void;
  chapterTitle: string;
}) {
  const voiceLane = question.lane === "voice";
  return (
    <section className="rise stack" style={{ ["--gap" as any]: "1.15rem" }}>
      <div className="eyebrow">{chapterTitle}</div>
      <h2 className="q-prompt">{question.prompt}</h2>

      {voiceLane && (
        <VoicePanel
          token={token}
          onAnswerRecorded={onVoiceAnswer}
          onFinished={onVoiceAnswer}
        />
      )}

      <Explain question={question} />

      <div className="q-answer">
        {voiceLane && (
          <div className="or-tap tiny muted">Or answer it here</div>
        )}
        <Answering
          question={question}
          answer={answers[question.id]}
          onAnswer={(v, s) => onAnswer(question.id, v, s)}
          autoFocus={!voiceLane}
        />
      </div>
    </section>
  );
}

function Welcome({ state, onStart }: { state: SellerState; onStart: () => void }) {
  return (
    <div className="wrap wrap-narrow gate rise">
      <div className="eyebrow">California Transfer Disclosure Statement</div>
      <h1 className="gate-title">Hello {state.sellerName.split(" ")[0]}.</h1>
      <p className="lede">
        {state.agentName ? `${state.agentName} has ` : "Your agent has "}
        asked you to complete the disclosure for <strong>{state.property.address}</strong>.
      </p>
      <p className="lede">
        It takes about {state.progress.minutes_left} minutes. Most of it is tapping things your
        home has. A handful of questions are easier to just talk through, so you can do those
        out loud. Nothing is final until you sign.
      </p>
      <div className="gate-points">
        <Point title="It saves as you go">
          Close the tab whenever you need to. You will come back to the same question.
        </Point>
        <Point title="“I'm not sure” is an answer">
          It is a real, safe answer on this form. Guessing is the thing to avoid.
        </Point>
        <Point title="Nothing is sent yet">
          Your agent sees it only after you finish, and you sign it separately.
        </Point>
      </div>
      <button className="btn btn-primary btn-lg" onClick={onStart}>
        Start
      </button>
    </div>
  );
}

function Resume({ state, onStart }: { state: SellerState; onStart: () => void }) {
  return (
    <div className="wrap wrap-narrow gate rise">
      <div className="eyebrow">Welcome back</div>
      <h1 className="gate-title">You are {state.progress.percent}% through.</h1>
      <p className="lede">
        {state.property.address}. About {state.progress.minutes_left} minutes left. Everything you
        answered is saved.
      </p>
      <div className="resume-chapters">
        {state.progress.chapters.map((c) => (
          <div key={c.id} className={`resume-row ${c.complete ? "is-done" : ""}`}>
            <span className="resume-tick" aria-hidden>
              {c.complete ? "✓" : ""}
            </span>
            <span className="resume-name">{c.title}</span>
            <span className="tiny muted">
              {c.answered}/{c.total}
            </span>
          </div>
        ))}
      </div>
      <button className="btn btn-primary btn-lg" onClick={onStart}>
        Pick up where I left off
      </button>
    </div>
  );
}

function Point({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="point">
      <div className="point-title">{title}</div>
      <p className="small muted">{children}</p>
    </div>
  );
}

function SellerLoading() {
  return (
    <div className="wrap wrap-narrow gate">
      <div className="skeleton skeleton-eyebrow" />
      <div className="skeleton skeleton-title" />
      <div className="skeleton skeleton-line" />
      <div className="skeleton skeleton-line short" />
    </div>
  );
}

function SellerError({ message }: { message: string }) {
  return (
    <div className="wrap wrap-narrow gate rise">
      <div className="eyebrow">Disclosure link</div>
      <h1 className="gate-title">This link is not valid.</h1>
      <p className="lede">{message}</p>
      <p className="lede">
        Links expire, and your agent can replace one at any time. Ask them to send a new link.
      </p>
    </div>
  );
}
