import type { AnswerRecord, Question } from "../types";

/** Mirror of app/tds/gating.py. The server stays authoritative - this exists so
 *  the UI can show and hide follow-ups instantly instead of waiting on a round
 *  trip. Both read the same `dependsOn` strings off the same question graph. */

export type AnswerMap = Record<string, AnswerRecord>;

export function valueOf(answers: AnswerMap, id: string): unknown {
  const a = answers[id];
  if (!a) return undefined;
  if (a.status === "unknown") return "unknown";
  if (a.status === "skipped") return undefined;
  return a.value;
}

function literal(token: string): unknown {
  const t = token.trim();
  if (t === "true") return true;
  if (t === "false") return false;
  return t;
}

function clause(text: string, answers: AnswerMap): boolean {
  const t = text.trim();

  if (t.startsWith("any ")) {
    const [pattern, expected] = t.slice(4).split(" is ");
    const prefix = pattern.trim().replace(/\*$/, "");
    const want = literal(expected ?? "");
    return Object.keys(answers).some(
      (k) => k.startsWith(prefix) && valueOf(answers, k) === want,
    );
  }

  if (t.includes(" contains ")) {
    const [id, option] = t.split(" contains ");
    const v = valueOf(answers, id.trim());
    if (v === undefined || v === null) return false;
    return Array.isArray(v) ? v.includes(option.trim()) : v === option.trim();
  }

  if (t.includes(" is ")) {
    const [id, expected] = t.split(" is ");
    return valueOf(answers, id.trim()) === literal(expected);
  }
  return true;
}

export function isVisible(q: Question, answers: AnswerMap): boolean {
  if (!q.dependsOn) return true;
  return q.dependsOn.split(" or ").some((part) => clause(part, answers));
}

export function isAnswered(answers: AnswerMap, id: string): boolean {
  const a = answers[id];
  if (!a) return false;
  if (a.status === "unknown") return true;
  if (a.status === "skipped") return false;
  const v = a.value;
  if (v === null || v === undefined || v === "") return false;
  if (Array.isArray(v) && v.length === 0) return false;
  return true;
}

/* ---------------- step model ---------------- */

export type Step = (
  | { kind: "group"; chapterId: string; group: string; questions: Question[] }
  | { kind: "question"; chapterId: string; question: Question }
  | { kind: "review" }
) & {
  /** Stable identity for this step.
   *
   *  The step list is rebuilt on every answer, because gating opens and closes
   *  follow-up questions as the seller goes. Its length therefore changes while
   *  the seller is inside it, so a positional cursor silently slides them onto a
   *  different screen - which, on a grid, reads as a tap undoing itself. The
   *  cursor is a key, never an index. */
  key: string;
};

/**
 * Consecutive plain yes/no inventory questions sharing a group collapse into a
 * single grid. That is the routing thesis made structural: enumeration is a
 * thing you scan and tap in parallel, so putting fifty of them on fifty screens
 * would be as wrong as reading them aloud.
 */
export function buildSteps(questions: Question[], chapters: { id: string }[], answers: AnswerMap): Step[] {
  const steps: Step[] = [];

  for (const chapter of chapters) {
    const inChapter = questions.filter(
      (q) => q.chapter === chapter.id && isVisible(q, answers),
    );
    if (inChapter.length === 0) continue;

    // No chapter-intro screen. A screen with nothing to answer on it is a click
    // the seller has to spend to learn something the label above the next
    // question already tells them - eight of them across the interview, which is
    // most of the distance to the voice questions.
    let i = 0;
    while (i < inChapter.length) {
      const q = inChapter[i];
      // A tile in a grid is an *item* the property either has or does not:
      // "Range", "Sauna", "Rain Gutters". Three kinds of question look eligible
      // and are not:
      //   - a gate, whose answer opens or closes what comes next. Side by side
      //     with the thing it gates, the dependency is invisible.
      //   - anything with its own gate, which would appear before the question
      //     that decides whether to ask it.
      //   - anything phrased as a sentence, because a grid of full questions
      //     reads as a checklist of things you are claiming, not answering.
      const groupable =
        q.kind === "bool" &&
        q.lane === "tap" &&
        q.group !== "" &&
        !q.dependsOn &&
        q.why !== "gate" &&
        !q.prompt.trim().endsWith("?");
      if (!groupable) {
        steps.push({ kind: "question", chapterId: chapter.id, question: q, key: `q:${q.id}` });
        i += 1;
        continue;
      }
      const batch: Question[] = [];
      while (
        i < inChapter.length &&
        inChapter[i].group === q.group &&
        inChapter[i].kind === "bool" &&
        inChapter[i].lane === "tap" &&
        !inChapter[i].dependsOn &&
        inChapter[i].why !== "gate" &&
        !inChapter[i].prompt.trim().endsWith("?")
      ) {
        batch.push(inChapter[i]);
        i += 1;
      }
      steps.push(
        batch.length === 1
          ? { kind: "question", chapterId: chapter.id, question: batch[0], key: `q:${batch[0].id}` }
          : {
              kind: "group",
              chapterId: chapter.id,
              group: q.group,
              questions: batch,
              key: `group:${chapter.id}:${q.group}`,
            },
      );
    }
  }

  steps.push({ kind: "review", key: "review" });
  return steps;
}

export function stepIsComplete(step: Step, answers: AnswerMap): boolean {
  if (step.kind === "question") return isAnswered(answers, step.question.id);
  // A group is never "incomplete". On the paper form an unchecked box means the
  // property does not have the item, so demanding an explicit tap on all fifty
  // would invent a requirement the instrument does not have - and would turn the
  // fastest part of the interview into its most tedious.
  return true;
}


/** The first step the seller still has something to do on.
 *
 *  Switching out of the spoken run-through used to drop them on step one - the
 *  address they had just confirmed out loud - and make them tap Continue past
 *  every question they had already answered. The answers survived the switch;
 *  their place in the form did not.
 *
 *  This deliberately does not reuse stepIsComplete. That call answers "may the
 *  seller submit", where an untouched inventory group is complete because an
 *  unchecked box on the paper form means the property does not have the item.
 *  Navigation is a different question - "is there anything here still to do" -
 *  and answering it with stepIsComplete would throw a seller who is 14% done
 *  past forty untouched items to the end of the form.
 */
export function firstUnfinished(steps: Step[], answers: AnswerMap): Step | undefined {
  return (
    steps.find((s) =>
      s.kind === "question"
        ? !isAnswered(answers, s.question.id)
        : s.kind === "group"
          ? !s.questions.every((q) => isAnswered(answers, q.id))
          : false,
    ) ?? steps[steps.length - 1]
  );
}
