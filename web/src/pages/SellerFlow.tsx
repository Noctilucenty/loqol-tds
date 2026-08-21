import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { Answering, ToggleGrid } from "../components/Answering";
import { VoicePanel } from "../components/VoicePanel";
import { Explain } from "../components/Explain";
import { Reconcile } from "../components/Reconcile";
import { buildSteps, isAnswered, isVisible, stepIsComplete, type Step } from "../lib/gating";
import type { AnswerRecord, SellerBootstrap, SellerState } from "../types";

type AnswerMapT = Record<string, AnswerRecord>;
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
  //: Voice is available on the inventory grids too. It is not the fast way to
  //: answer fifty checkboxes, which is the whole routing argument - but a seller
  //: who cannot use the grid must still have a way through.
  const [groupVoiceOpen, setGroupVoiceOpen] = useState(false);
  //: The seller asked to do the whole form by talking, so the assistant opens on
  //: every screen and its session covers every question rather than the 19 the
  //: router would have picked.
  const [spokenThrough, setSpokenThrough] = useState(false);

  const queue = useRef<Promise<unknown>>(Promise.resolve());

  /** The assistant finished this section. Skip past every spoken question it
   *  already answered, rather than landing on one that is now filled in.
   *
   *  Declared up here with the other hooks. A useCallback below the early
   *  returns changes the hook count between renders and blanks the whole flow -
   *  the same crash as before, which is why the smoke test now asserts that the
   *  landing screen is gone AND something replaced it. */
  const latest = useRef<{ steps: Step[]; answers: AnswerMapT }>({ steps: [], answers: {} });
  const onVoiceFinished = useCallback(() => {
    refreshRef.current?.();
    setStepKey((currentKey) => {
      const { steps: cur, answers: ans } = latest.current;
      const from = cur.findIndex((s) => s.key === currentKey);
      for (let i = Math.max(from, 0) + 1; i < cur.length; i += 1) {
        const s = cur[i];
        if (s.kind !== "question") return s.key;
        if (!isAnswered(ans, s.question.id)) return s.key;
      }
      return cur[cur.length - 1]?.key ?? currentKey;
    });
  }, []);
  const refreshRef = useRef<(() => void) | null>(null);
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

  refreshRef.current = refresh;

  if (error && !boot) return <SellerError message={error} />;
  if (!boot || !state || !form) return <SellerLoading />;

  if (!started && state.progress.answered === 0) {
    return (
      <Welcome
        state={state}
        onStart={() => setStarted(true)}
        onStartTalking={() => {
          setSpokenThrough(true);
          setStarted(true);
        }}
      />
    );
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
  latest.current = { steps, answers };

  const step = steps[Math.min(index, steps.length - 1)];

  // How far through the spoken questions the seller is, so the panel can show a
  // finish line rather than an open-ended conversation.
  const currentChapter = step && step.kind !== "review" ? (step as any).chapterId : null;
  const voiceQuestions = form.questions.filter(
    (q) => q.lane === "voice" && q.chapter === currentChapter && isVisible(q, answers),
  );
  const voiceTotal = voiceQuestions.length;
  const voiceCovered = voiceQuestions.filter((q) => isAnswered(answers, q.id)).length;

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
        {step?.kind === "group" && (
          <section className="rise stack">
            <div className="eyebrow">{chapter?.title}</div>
            <h2>{step.group}</h2>
            <p className="muted small">
              Tap everything the property has. Leave the rest.
            </p>
            {groupVoiceOpen || spokenThrough ? (
              <VoicePanel
                token={token}
                onAnswerRecorded={refresh}
                onFinished={refresh}
                scope={spokenThrough ? "all" : "voice"}
              />
            ) : (
              <button type="button" className="talk-instead" onClick={() => setGroupVoiceOpen(true)}>
                <span className="talk-instead-mark" aria-hidden>
                  <svg viewBox="0 0 24 24" width="13" height="13">
                    <path d="M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3Z" fill="currentColor" />
                    <path d="M5 11a7 7 0 0 0 14 0M12 18v3" fill="none" stroke="currentColor"
                          strokeWidth="2" strokeLinecap="round" />
                  </svg>
                </span>
                Rather read these out?
              </button>
            )}
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
            knownAddress={state.property.address}
            spokenThrough={spokenThrough}
            onAnswer={answer}
            onVoiceAnswer={refresh}
            onVoiceFinished={onVoiceFinished}
            chapterTitle={chapter?.title ?? ""}
            voiceCovered={voiceCovered}
            voiceTotal={voiceTotal}
          />
        )}

        {step?.kind === "review" && (
          <Reconcile
            token={token}
            state={state}
            form={form}
            onChange={setState}
            onJumpTo={(questionId) => {
              const target = steps.find(
                (s) =>
                  (s.kind === "question" && s.question.id === questionId) ||
                  (s.kind === "group" && s.questions.some((q) => q.id === questionId)),
              );
              if (target) setStepKey(target.key);
            }}
          />
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
  onVoiceFinished,
  chapterTitle,
  voiceCovered,
  voiceTotal,
  knownAddress,
  spokenThrough,
}: {
  token: string;
  question: any;
  answers: any;
  knownAddress: string;
  spokenThrough: boolean;
  onAnswer: (id: string, v: unknown, s?: "answered" | "unknown") => void;
  onVoiceAnswer: () => void;
  onVoiceFinished: () => void;
  chapterTitle: string;
  voiceCovered: number;
  voiceTotal: number;
}) {
  const voiceLane = question.lane === "voice" || spokenThrough;
  // Routing decides the *default*, never what is possible. On a tap question the
  // assistant is one line away rather than absent, so a seller who would rather
  // talk is never told they may not. That is the brief's "should never feel like
  // they're being made to use the wrong tool", taken literally.
  const [voiceOpen, setVoiceOpen] = useState(false);
  return (
    <section className="rise stack" style={{ ["--gap" as any]: "1.15rem" }}>
      <div className="eyebrow">{chapterTitle}</div>
      <h2 className="q-prompt">{question.prompt}</h2>

      {(voiceLane || voiceOpen) && (
        <VoicePanel
          token={token}
          onAnswerRecorded={onVoiceAnswer}
          onFinished={onVoiceFinished}
          covered={voiceCovered}
          total={voiceTotal}
          scope={spokenThrough ? "all" : "voice"}
        />
      )}

      {!voiceLane && !voiceOpen && (
        <button type="button" className="talk-instead" onClick={() => setVoiceOpen(true)}>
          <span className="talk-instead-mark" aria-hidden>
            <svg viewBox="0 0 24 24" width="13" height="13">
              <path d="M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3Z" fill="currentColor" />
              <path d="M5 11a7 7 0 0 0 14 0M12 18v3" fill="none" stroke="currentColor"
                    strokeWidth="2" strokeLinecap="round" />
            </svg>
          </span>
          Rather talk this one through?
        </button>
      )}

      {question.id === "P.address_ok" && (
        // The question asks about a specific address, so it has to be on screen.
        // Reading it out of the small sticky header is not "checking" it.
        <div className="confirm-value">{knownAddress}</div>
      )}

      <Explain question={question} />

      <div className="q-answer">
        {(voiceLane || voiceOpen) && (
          <div className="or-tap tiny muted">Or answer it here</div>
        )}
        <Answering
          question={question}
          // The address is shown pre-filled from the deal so there is something
          // to actually check. It is a display default, not a stored answer:
          // storing it made the disclosure look started before the seller had
          // opened it, and made their first correction look like a contradiction.
          answer={
            // Start the correction box from what is on file, so they edit a
            // digit rather than retype the whole thing.
            question.id === "P.address" && !answers[question.id] && knownAddress
              ? { value: knownAddress, status: "answered", source: "agent", revision: 0 }
              : answers[question.id]
          }
          onAnswer={(v, s) => onAnswer(question.id, v, s)}
          autoFocus={!voiceLane && !voiceOpen}
        />
      </div>
    </section>
  );
}

function Welcome({
  state, onStart, onStartTalking,
}: {
  state: SellerState;
  onStart: () => void;
  onStartTalking: () => void;
}) {
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
      <div className="gate-actions">
        <button className="btn btn-primary btn-lg" onClick={onStart}>
          Start tapping
        </button>
        <button className="btn btn-ghost btn-lg" onClick={onStartTalking}>
          Talk me through the whole thing
        </button>
      </div>
      <p className="tiny muted gate-note">
        Either way you can switch at any point &mdash; every question takes both.
      </p>
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
