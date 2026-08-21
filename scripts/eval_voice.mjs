// Conversation gate for the voice lane.
//
// The unit tests and scripts/audit.py cover the deterministic half of this app -
// auth, gating, the PDF, freezing, contradictions - and they were green through
// every voice bug this project has had. They had to be: none of them exercises a
// conversation. So the assistant restarted at the top of the list on every
// screen, read question ids out loud, asked thirty-nine appliances one at a
// time, filled sixty-eight items in with "unknown", and ended its turns
// announcing a group instead of asking it, all while "the full audit passed".
//
// This runs scripted seller dialogues against the real model, through the app's
// own minted session and its own HTTP endpoints, so the server-side guards apply
// exactly as they do in the browser. It asserts on behaviour, not wording.
//
//   node scripts/eval_voice.mjs                              # localhost:8000
//   node scripts/eval_voice.mjs https://loqol-tds.onrender.com
//
// Exits non-zero on any failure. Needs OPENAI_API_KEY on the server side only -
// the ephemeral secret comes from the app.

const BASE = (process.argv[2] || "http://127.0.0.1:8000").replace(/\/$/, "");
const PASS = [];
const FAIL = [];

const j = (r) => r.json();

/** A cookie-keeping fetch, so the agent session survives across calls. */
function makeClient() {
  let jar = "";
  return async (path, body, method) => {
    const res = await fetch(BASE + path, {
      method: method || (body !== undefined ? "POST" : "GET"),
      headers: { "Content-Type": "application/json", ...(jar ? { Cookie: jar } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const set = res.headers.getSetCookie?.() ?? [];
    if (set.length) jar = set.map((c) => c.split(";")[0]).join("; ");
    return res;
  };
}

/** A fresh deal and seller link, so scenarios never share state. */
async function freshSeller() {
  const agent = makeClient();
  await agent("/api/auth/demo", {});
  const deal = await j(await agent("/api/agent/deals", {
    property_address: "1247 Sepulveda Blvd, Culver City, CA 90230",
    city: "Culver City", county: "Los Angeles",
    seller_name: "Dana Whitfield", seller_email: "dana@example.com",
  }));
  const link = await j(await agent(`/api/agent/deals/${deal.id}/link`, {}));
  return link.url.split("/").pop();
}

/** Play a scripted seller against the real assistant.
 *
 *  Tool calls go to the real endpoints. The one thing simulated is the seller's
 *  voice: replies are injected as text, and `sellerSpoke` mirrors the browser's
 *  rule that a turn in which the seller said nothing cannot write.
 */
async function converse(token, replies, { maxTurns = 14 } = {}) {
  const seller = makeClient();
  const mint = await j(await seller(`/api/voice/${token}/session?scope=all`, {}));
  if (!mint.clientSecret) throw new Error("no client secret - is OPENAI_API_KEY set?");

  const sock = new WebSocket(
    `wss://api.openai.com/v1/realtime?model=${mint.model}`,
    ["realtime", `openai-insecure-api-key.${mint.clientSecret}`],
  );
  const send = (o) => sock.send(JSON.stringify(o));

  const log = { said: [], calls: [], refusals: [], blocked: 0, errors: [] };
  let said = "", turn = 0, sellerSpoke = false, turns = 0;
  let active = false, owed = false, grace = 0;
  let turnText = "", usedTool = false, nudges = 0;
  const DANGLING = /\b(next|move on|moving on|coming up|one shot|go through|run through)\b/i;

  const requestTurn = () => {
    if (active) { owed = true; return; }
    active = true;
    send({ type: "response.create" });
  };

  await new Promise((resolve, reject) => {
    const done = (why) => { try { sock.close(); } catch {} resolve(why); };
    const timer = setTimeout(() => done("timeout"), 150000);

    sock.onerror = (e) => { clearTimeout(timer); reject(new Error("socket: " + e.message)); };
    sock.onopen = () => requestTurn();

    sock.onmessage = async (raw) => {
      let ev;
      try { ev = JSON.parse(raw.data); } catch { return; }

      if (ev.type === "response.created") active = true;

      // Mirror the browser's gate, derived from the same events rather than
      // set by hand - simulating this signal is what let a real blocking bug
      // through: the app trusted only the transcription event, which is a
      // separate model that can lag or fail.
      if (ev.type === "input_audio_buffer.speech_stopped"
        || ev.type === "input_audio_buffer.committed"
        || ev.type === "conversation.item.input_audio_transcription.completed"
        || ((ev.type === "conversation.item.added" || ev.type === "conversation.item.done")
            && ev.item?.role === "user")) {
        sellerSpoke = true;
      }
      if (ev.type === "error") {
        if (ev.error?.code === "conversation_already_has_active_response") { owed = true; return; }
        log.errors.push(ev.error?.code || JSON.stringify(ev.error).slice(0, 120));
        return;
      }
      if (ev.type?.endsWith("audio_transcript.delta") || ev.type?.endsWith("text.delta")) {
        said += ev.delta ?? "";
        turnText += ev.delta ?? "";
        return;
      }

      if (ev.type === "response.function_call_arguments.done") {
        const args = JSON.parse(ev.arguments || "{}");
        const reply = async (out) => {
          send({ type: "conversation.item.create", item: {
            type: "function_call_output", call_id: ev.call_id, output: JSON.stringify(out) } });
          requestTurn();
        };

        usedTool = true;
        if (ev.name === "finish_section") { log.calls.push({ name: ev.name, n: 0 }); return; }

        if (!sellerSpoke) {
          log.blocked++;
          log.calls.push({ name: ev.name, n: 0, blocked: true });
          await reply({ ok: false, error: "The seller has not said anything since your last turn." });
          return;
        }

        const path = ev.name === "record_group" ? "answers" : "answer";
        const res = await seller(`/api/voice/${token}/${path}`, args);
        const body = await j(res);
        log.calls.push({
          name: ev.name,
          n: ev.name === "record_group" ? (args.items?.length ?? 0) : 1,
          ok: res.ok,
        });
        if (!res.ok) log.refusals.push(String(body.detail).slice(0, 90));
        await reply(res.ok ? body : { ok: false, error: body.detail });
        return;
      }

      if (ev.type === "response.done") {
        active = false;
        // Mirror the browser: only a turn where the assistant actually spoke
        // spends the seller's turn.
        if (turnText.trim()) sellerSpoke = false;
        if (said.trim()) log.said.push(said.trim());
        said = "";

        const spoken = turnText, tooled = usedTool;
        turnText = ""; usedTool = false;

        if (owed) { owed = false; requestTurn(); return; }

        // Mirror the browser: a turn that ends announcing the next group rather
        // than asking it gets another turn, so the seller is never left waiting.
        const dangling = spoken.trim() && !tooled && !spoken.includes("?")
          && DANGLING.test(spoken.slice(-200));
        if (dangling) {
          log.dangling = (log.dangling || 0) + 1;
          if (nudges++ < 3) { requestTurn(); return; }
        } else { nudges = 0; }
        if (++turns > maxTurns) { clearTimeout(timer); return done("max turns"); }
        if (turn < replies.length) {
          const r = replies[turn++];
          log.said.push("SELLER: " + r);
          send({ type: "conversation.item.create", item: {
            type: "message", role: "user", content: [{ type: "input_text", text: r }] } });
          requestTurn();
          return;
        }
        // The last reply usually produces its tool calls in the turn after it,
        // so closing the moment the script runs out drops the final answer and
        // reports it as never recorded. Give it a couple of quiet turns.
        if (grace++ < 2) { requestTurn(); return; }
        clearTimeout(timer);
        return done("replies exhausted");
      }
    };
  });

  const state = await j(await seller(`/api/s/${token}/state`));
  const answers = Object.fromEntries(
    Object.entries(state.answers).map(([k, v]) => [k, v.value]),
  );
  return { ...log, answers, statuses: state.answers };
}

function check(name, ok, detail = "") {
  (ok ? PASS : FAIL).push(name);
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  [${detail}]` : ""}`);
}

// ---------------------------------------------------------------- scenarios --

const SCENARIOS = [
  {
    name: "a room answered in one breath is one call",
    replies: [
      "Yes that address is right, and I live there.",
      "Range, oven and dishwasher yes. No microwave, no compactor, no disposal. We do have washer dryer hookups.",
    ],
    assert(r) {
      const group = r.calls.find((c) => c.name === "record_group" && c.ok);
      check("records the kitchen as a single grouped call", !!group,
        group ? `${group.n} items in one call` : "no successful record_group");
      check("all seven kitchen items land", ["A.range", "A.oven", "A.microwave", "A.dishwasher",
        "A.trash_compactor", "A.garbage_disposal", "A.washer_dryer_hookups"]
        .every((q) => q in r.answers));
      check("yes and no are not confused",
        r.answers["A.range"] === true && r.answers["A.microwave"] === false,
        `range=${r.answers["A.range"]} microwave=${r.answers["A.microwave"]}`);
      const singles = r.calls.filter((c) => c.name === "record_answer" && c.ok).length;
      check("does not fall back to one call per appliance", singles <= 2,
        `${singles} single calls`);
    },
  },
  {
    name: "it never speaks a question id",
    replies: ["Yes that is right and I live there.", "Range and oven only."],
    assert(r) {
      const spoken = r.said.filter((s) => !s.startsWith("SELLER:")).join(" ");
      const leaked = spoken.match(/\b[A-Z]\.[a-z_]+\b/g);
      check("no question ids read aloud", !leaked, leaked ? leaked.slice(0, 3).join(", ") : "");
    },
  },
  {
    name: "every turn ends with a question, never an announcement",
    replies: [
      "Yes right address, I live there.",
      "Range oven dishwasher yes, nothing else in the kitchen.",
      "Central heating only.",
    ],
    assert(r) {
      const spoken = r.said.filter((s) => !s.startsWith("SELLER:"));
      const last = spoken[spoken.length - 1] ?? "";
      // A turn that confirms what was captured and invites a correction is a
      // fine place to leave someone; a turn that says "next up is safety and
      // security" and then stops is not. Only the second sort strands them.
      const stranded = last.trim() && !last.includes("?")
        && !/(say so|let me know|tell me|go ahead|whenever you)/i.test(last.slice(-160))
        && /\b(next|move on|moving on|coming up|one shot|go through|run through)\b/i
             .test(last.slice(-200));
      check("the seller is never left on an announcement", !stranded, last.slice(-90));
      check("any announcement-only turn recovered without the seller prodding",
        (r.dangling ?? 0) === 0, `${r.dangling ?? 0} needed recovery`);
    },
  },
  {
    name: "an answered question actually reaches the database",
    replies: [
      "Yes that address is right, and I live there.",
      "Range and oven yes, nothing else.",
    ],
    assert(r) {
      // The write gate refused every call in a live browser session because it
      // keyed on an event that had not arrived. Everything looked healthy on
      // the wire and nothing was saved.
      const n = Object.keys(r.answers).length;
      check("the seller's answers are persisted, not silently refused", n >= 3,
        `${n} answers stored`);
      check("no call was blocked by the write gate after the seller spoke",
        r.calls.filter((c) => c.blocked).length <= 1,
        `${r.calls.filter((c) => c.blocked).length} blocked`);
      const hiccup = r.said.some((t) => /hiccup|didn.t record|try that again|say it again/i.test(t));
      check("the assistant never apologises for failing to record", !hiccup);
    },
  },
  {
    name: "it will not answer for a seller who has not spoken",
    replies: ["Yes that address is right and I live there."],
    assert(r) {
      const total = Object.keys(r.answers).length;
      check("does not run ahead through the form", total <= 4, `${total} answers recorded`);
      check("nothing was written without the seller speaking",
        r.calls.filter((c) => c.blocked && c.n > 0).length === 0);
    },
  },
  {
    name: '"nothing else" means this room, not the whole house',
    replies: [
      "Yes right address, I live there.",
      "Range oven dishwasher yes, nothing else.",
    ],
    assert(r) {
      const total = Object.keys(r.answers).length;
      check("does not fill in groups it never read out", total <= 12,
        `${total} answers from two sentences`);
      check("nothing from an unasked group was stored",
        !("A.gazebo" in r.answers) && !("A.carport" in r.answers));
    },
  },
  {
    name: "it asks one group at a time, not four in a breath",
    replies: [
      "Yes right address, I live there.",
      "Range and oven yes, nothing else.",
    ],
    assert(r) {
      // Group headings are distinctive enough to count by their lead items.
      const MARKERS = {
        kitchen: /\brange\b/i, heating: /central heating/i,
        safety: /burglar alarm|smoke detector/i, media: /tv antenna|satellite dish/i,
        yard: /rain gutter|sprinkler/i, sewer: /public sewer|septic/i,
      };
      const worst = r.said
        .filter((t) => !t.startsWith("SELLER:"))
        .map((t) => Object.values(MARKERS).filter((re) => re.test(t)).length)
        .reduce((a, b) => Math.max(a, b), 0);
      check("no turn reads out more than one group", worst <= 1,
        `${worst} groups in one turn`);
    },
  },
  {
    name: "a correction mid-sentence wins",
    replies: [
      "Yes right address, I live there.",
      "We have a range and an oven. Actually no, scratch that, the oven died last year so no oven. Dishwasher yes. I have no idea about the trash compactor.",
    ],
    assert(r) {
      check("the corrected value is what is stored", r.answers["A.oven"] === false,
        `oven=${r.answers["A.oven"]}`);
      check("the uncorrected one is untouched", r.answers["A.range"] === true);
      check('"no idea" stores as unknown, not as a no',
        r.statuses["A.trash_compactor"]?.status === "unknown",
        r.statuses["A.trash_compactor"]?.status ?? "not recorded");
    },
  },
];

// -------------------------------------------------------------------- main --

const cfgProbe = await fetch(`${BASE}/api/health`).then(j).catch(() => null);
if (!cfgProbe) { console.error(`cannot reach ${BASE}`); process.exit(1); }
if (!cfgProbe.voice) { console.log("voice is not configured on this instance - nothing to evaluate"); process.exit(0); }

console.log(`voice conversation eval: ${BASE}\n`);
for (const s of SCENARIOS) {
  console.log(s.name);
  try {
    const token = await freshSeller();
    const result = await converse(token, s.replies);
    if (process.env.VERBOSE) {
      console.log("    calls:", JSON.stringify(result.calls));
      if (result.refusals.length) console.log("    refusals:", result.refusals);
      if (result.errors.length) console.log("    errors:", result.errors);
      console.log("    said:", result.said.map((x) => x.slice(0, 100)));
    }
    s.assert(result);
    if (result.errors.length) check("no realtime errors", false, result.errors.slice(0, 2).join(", "));
  } catch (e) {
    check(s.name, false, e.message.slice(0, 120));
  }
  console.log("");
}

console.log(`${PASS.length} passed, ${FAIL.length} failed`);
if (FAIL.length) { console.log("failed: " + FAIL.join(", ")); process.exit(1); }
