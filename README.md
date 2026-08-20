# Loqol disclosures — California TDS

A web app that interviews a home seller and turns their answers into a completed,
signable California Transfer Disclosure Statement.

- **Live:** _see DEPLOY.md_
- **Design notes** (the routing decision, generated from the live form spec): `/design`
- **Demo agent login:** `agent@loqol.ai` / `disclosure-demo-1`, or the "Use the demo
  account" button on the sign-in page.

---

## The one decision this exercise is really about

> **Speak when the bottleneck is understanding. Tap when the bottleneck is enumeration.**

The intuitive split is *voice for the scary legal parts, tapping for the easy
parts*. I think that split is wrong, and following it produces a seller reading
fifty appliance names out loud.

What actually decides the lane is where the difficulty sits:

**Section A is fifty checkboxes** — Range, Oven, Microwave, Trash Compactor, Sauna,
Gazebo. Not one of them is hard to understand. The difficulty is purely
*enumeration*: getting a long closed list out of your head. Speaking that list is
slower than tapping it, less accurate, and gives the seller no way to scan for
what they forgot. A grid is a better instrument than a conversation because the
eye does in parallel what speech has to serialise. **Tap.**

**Section C is sixteen questions** — "Any encroachments, easements or similar
matters that may affect your interest in the subject property." A homeowner
cannot answer that as written. The difficulty is *comprehension*, and the fix for
comprehension is a conversation that rephrases, gives an example, and checks it
landed. It is also where a bare yes is useless: the form demands the story, with
dates and repair status, and typing a legal narrative at 10pm on a phone is the
single most abandonable moment in the whole flow. **Speak.**

That framing predicts the opposite of the intuitive one and is the one that
survives contact with the actual form.

### The full routing table

Every question carries its lane *and the reason*, as data
(`app/tds/routing.py`, `app/tds/questions.py`). The `/design` page renders the
table from the live spec, so it cannot drift from behaviour.

| Reason | Lane | Count | Where |
|---|---|---|---|
| **Enumeration** — closed set, many items, no ambiguity | Tap | 39 | Section A inventory, Section B component list |
| **Comprehension** — the seller does not know what is being asked | **Voice** | 16 | All of Section C |
| **Narrative** — the usable answer is a story with dates and status | **Voice** | 3 | Section A "not working", Section B explanation, Section C shared explanation |
| **Precision** — an exact string, number or date | Tap | 7 | Address, room lists, remote count |
| **Gate** — one binary that visibly opens or closes a section | Tap | 7 | Occupancy, Section A catch-all, Section B gate, garage, pool, hot tub |
| **Compound** — one question, several sub-answers | Tap | 5 | Water heater / water supply / gas supply / pool heater fuels, garage attachment |
| **Not the seller's question** | — | 4 | Section I, routed to the agent |

**Routing is a default, never a lock.** Every question renders its tap control
regardless of lane, and the voice agent can answer any question in the graph.
Both lanes write through one server-side path (`services.write_answer`), so an
answer spoken and an answer tapped are the same row in the same table. That is
what makes "start in one and finish in the other" true rather than aspirational —
there is no second code path that could drift.

### Two things I moved off the seller entirely

**Section I** (which inspection reports travel with the transfer) is in the agent
view, not the seller's. Asking a homeowner that is asking them to do their
agent's job.

**Form order.** The printed form opens with Section I and buries the two
questions carrying real legal risk in nine-point type on page two. The interview
is re-cut into eight chapters ordered by what a person can actually answer:
confirm the property, say whether you live there, sweep the inventory (fast, and
it builds momentum), then the hard recall, then review.

---

## The seller experience

The person filling this in is stressed, not technical, and doing it at 10pm. Each
of these is a specific answer to a specific way that goes wrong.

**"What order should questions come in?"** Not form order — see above. Chapters
open with a plain-language framing, and the fifty-item inventory is grouped into
scannable grids (Kitchen and laundry, Safety and security, Pool and spa) rather
than served one screen at a time.

**"What if they don't understand a question?"** Every question has a "What does
this mean?" affordance with three layers, each one click deeper: a plain-English
gloss, a concrete example in a homeowner's own voice, and then the statutory
wording. The statutory wording is last but never removed — they are signing that
sentence, so hiding it would be dishonest, and putting it first is what makes
people give up.

**"What if they don't know the answer?"** **"I'm not sure" is a first-class
answer**, given the same visual weight as Yes and No rather than hidden behind a
link. This is the single most consequential decision in the data model. On a TDS,
answering *No* when the honest answer is *I never checked* is how sellers create
liability for themselves. So `AnswerStatus.UNKNOWN` is not `False` and not
absent, and when it reaches the form **both statutory boxes are left clear** —
which is exactly what a careful seller would do on paper. There is a test for it.

**"What if they contradict an answer from three questions ago?"** Sixteen
consistency rules specific to this form (`app/tds/rules.py`) — septic tank *and*
public sewer, a child-resistant pool barrier with no pool, a gas water heater
with no gas supply, an HOA with no CC&Rs, unpermitted work that is somehow
code-compliant. They are split into `hard` (cannot both be true, blocks
submission) and `soft` (unusual, probably a slip, but legitimate).

Nothing blocks at the moment of the contradiction. Interrupting someone mid-recall
to argue about something they said forty minutes ago is how you lose them at 10pm,
and the form is not filed yet. Conflicts are detected on write, queued, and
brought back at review **phrased as a question, with both answers shown and
neither pre-selected**:

> You told us the house is on both a public sewer and a septic tank. Almost every
> home has one or the other. Which is it?

Soft flags additionally offer "Both are right, leave them", because sometimes
they are. Flags that stop firing close themselves — fixing a contradiction should
not also require dismissing the warning about it.

**"How do they know how much is left?"** Chapter-level progress plus **an estimate
in minutes**, not "38 of 150 fields". A count that large reads as a threat, and it
is also a lie: most of those 150 fields are unreachable for any given property.
Progress is measured over the questions *this* seller will actually be asked,
recomputed as gating opens and closes follow-ups.

**"What if they close the tab halfway through?"** Every answer is a `PUT` the
moment it changes — there is no Save button, because the seller should not have
to know that saving is a thing that happens. The server writes a resume cursor on
every write, so closing the tab costs at most the question currently on screen.
Returning shows a chapter-by-chapter summary and drops them back where they were.

---

## Data model, and why it is shaped that way

```
agents ──< deals ──< disclosure_sessions ──< answers          (current value, unique per question)
                                          ├─< answer_events   (append-only, every write ever)
                                          ├─< access_tokens   (the seller's credential, hashed)
                                          ├─< flags           (contradictions awaiting a decision)
                                          └─< voice_sessions  (cost ceiling + audit)
```

### Why answers and events are separate tables

A TDS is a sworn statement, and two questions get asked of it that pull in
opposite directions.

*"What is the answer?"* — cheap against a current-value table, an expensive fold
over an event log. It is asked on every render and every PDF.

*"What did the seller say, and when, and through which channel?"* — impossible
against a current-value table alone. It is asked once, in a dispute, when it
matters enormously.

So `answers` holds exactly one row per `(session, question)` under a unique
constraint and is upserted, and `answer_events` is append-only and retains every
write including the superseded ones, with the lane it came from, the actor, and
the voice transcript it was extracted from. The agent's **History** tab is that
table.

### "What breaks when a seller answers the same question twice?"

Nothing breaks, and three things happen:

1. The `answers` row is **upserted**, so there is exactly one current value and
   no ambiguity about what prints on the form. `revision` increments.
2. An `answer_events` row is appended with the previous value alongside the new
   one. Nothing is lost.
3. If the new value **disagrees** with the old one *and arrived from a different
   lane*, a flag is raised rather than letting the last writer silently win.

That third point is the interesting one. A seller who taps *No* and then tells the
voice agent "well, actually, yes" has not made a mistake — they have remembered
something. The later answer stands, because it is later; but the disagreement is
surfaced at review so a human decides which one they meant. Silently overwriting
would lose a real signal, and blocking would punish them for remembering.

Concurrency between the lanes is handled the same way: both go through
`write_answer`, the client sends the `revision` it thought it was editing, and the
response says whether the other lane had already moved on.

---

## Auth

### Agents: email and password

Argon2id password hashing. A successful login creates a row in `agent_sessions`
and sets an **HttpOnly, SameSite=Lax, Secure** cookie holding a 256-bit random
token; only the SHA-256 of that token is stored.

Deliberately a server-side session, not a JWT. A stateless token cannot be
revoked before it expires, and "log this person out now" is a requirement for a
tool that opens legal documents. Login failures take the same path whether the
email or the password was wrong.

### Sellers: no password at all

Requiring a stressed homeowner to create an account at 10pm to fill in a form
their agent sent them is how you lose them before question one. **The link is the
credential**: 256 bits of URL-safe randomness, stored only as a SHA-256 hash,
scoped to exactly one disclosure session, expiring in 14 days, revocable and
rotatable by the agent from the deal page.

### "What happens if someone guesses a seller's URL?"

They do not. 2^256 is not a guessable space, and the token is compared against a
hash, so a dump of `access_tokens` does not yield working links either. Wrong,
revoked and expired tokens all return the same 404, so a stranger cannot learn
that a token was ever real.

The honest risk is not guessing, it is **leakage** — a forwarded text, a shared
screen, a referrer header — because anyone holding the link is treated as the
seller. What is done about it:

- no PII in the URL, and `Referrer-Policy: no-referrer` on every response
- `Cache-Control: no-store` on all seller routes
- every use recorded with IP, user-agent and a running count, shown to the agent
  as "last opened at…"
- one-click revoke-and-reissue, which invalidates the old link immediately
- voice sessions rate-limited per disclosure, so a leaked link cannot be used to
  burn the OpenAI budget

**What I did not build, and would in production:** an emailed one-time code in
front of the *signature* step specifically. That is the point where the link
stops being a convenience and becomes a legally binding act, and a bearer link is
not enough identity assurance for it. It is a known gap, not a missing one.

---

## Mapping answers to form fields

### The PDF is not flat

The brief says the attached PDF is flat with no fillable fields. It is not — it
ships with a complete AcroForm: **159 placed widgets** across three pages. That
turned out to matter more than a trivia point, because the AcroForm has two
defects that make the form **impossible to fill correctly through field values**:

1. **`Solar` is one field owning two widgets** — one on the Pool/Spa Heater line,
   one on the Water Heater line. An AcroForm value is per *field*, so setting
   `Solar` checks both boxes. A solar pool heater and a gas water heater cannot
   be expressed at all.
2. **`Other2Describe` owns two widgets** that need *different* text (a
   15-character inline stub and a full-width continuation line).

So bindings address a **widget** — name plus an occurrence index assigned in
reading order — rather than a field name. `Solar#0` is always the pool heater box
and `Solar#1` is always the water heater box, regardless of how the PDF stores
its annotations. Both the DocuSeal template and the local renderer work from that
addressing, and there is a test that pins it.

### Coverage

Every one of the 159 widgets is accounted for, checked on every boot and in CI:

| | |
|---|---|
| Bound to seller/agent questions | 133 |
| Owned by a signer role (signatures, initials, dates, party names) | 26 |
| **Unhandled** | **0** |

`app/tds/fieldmap.py::validate()` fails the app's startup if any binding
addresses a widget that does not exist, or writes text into a checkbox. A
mistyped field name is the one failure that would otherwise be *silent* — you get
a form that looks filled and is not.

### Compound and shared-explanation fields

- "Water Heater: Gas / Solar / Electric" is **one question with a multi-select**,
  not three checkboxes. Same for garage, water supply, gas supply, pool heater.
  A parent box like `PoolSpaHeater` uses `CheckAny`, so it lights up only when a
  real fuel was chosen — not merely because the question was answered.
- Section C's sixteen questions share **one explanation area made of five ruled
  lines of differing widths**. Sellers do not think in ruled lines, so the app
  collects one narrative and `WrappedText` lays it out with a variable-width
  greedy wrapper.
- **Nothing is silently truncated.** If the text does not fit, the last visible
  line gets a continuation marker and the full answer is carried onto a generated
  **addendum page** — the form's own "attach additional sheets if necessary"
  escape hatch. The addendum *restates the answer in full* under a section
  heading rather than continuing mid-sentence, because a continuation sheet that
  opens halfway through a word is not a usable disclosure.

### Gating is enforced server-side

`resolve()` refuses to print an answer whose question is not currently visible.
A pool heater answer cannot survive the seller going back and saying there is no
pool. The browser evaluates the same `dependsOn` strings from the same spec for
instant show/hide, but the server is authoritative.

---

## DocuSeal

**Verified end to end against the live sandbox** — template created, submission
prefilled, signed, and the completed PDF downloaded.

- Template `5507359`, 168 fields, six signer roles.
- Account is on the free **Developer Sandbox** tier: `$0/document`, free
  unlimited API for testing. Nothing was paid for.

### Building the template from coordinates, not from the AcroForm

Uploading the PDF and letting DocuSeal inherit its form fields would reproduce
both defects above. Declaring fields from measured geometry gives every box its
own name and its own value — the only way this form can be filled correctly — and
`flatten: true` removes the source AcroForm so there is no second, broken set of
inputs behind the real ones.

Widget rectangles are PDF points with a bottom-left origin; DocuSeal areas are
fractions of the page with a top-left origin:

```python
x = x0 / 612                  w = (x1 - x0) / 612
y = (792 - y1) / 792          h = (y1 - y0) / 792
```

### The three signature lines the PDF forgot

The form prints "Agent (Broker Representing Seller) ___ By ___ Date ___" twice
and "Agent (Broker Obtaining the Offer)" once — **with no fields behind any of
them**. Because the template is built from coordinates, those are simply declared
from rules measured out of the page's own vector content, and come out as real
signable fields.

**On the count of signer roles:** the brief says five. I modelled **six** —
Seller, Co-Seller, Buyer, Co-Buyer, Listing Agent, Selling Agent. The difference
is whether the second Seller and Buyer lines are read as one role signing twice
or as distinct parties. They are distinct: a co-owner is a separate legal
signatory with their own date. A submission simply omits the roles a given deal
does not need.

### Answers go in read-only

By the time a submission is created the seller has already answered everything
here and reviewed it. DocuSeal is a signing ceremony, not a second chance to
retype a legal disclosure, so every prefilled field is pushed with
`readonly: true`.

### It works without DocuSeal too

With no API key the app falls back to rendering the filled PDF locally
(`app/tds/fill.py`) and says so. Clone this with no credentials at all and you
still get a working seller flow and a correct completed form. The local renderer
*stamps* values using the AcroForm purely as a coordinate source — the same
approach, and the same reason.

---

## Voice

Browser-only, over WebRTC to the OpenAI realtime API. No phone, no phone number,
no server in the audio path.

- **The standing API key never reaches the browser.** The server mints a
  short-lived client secret scoped to one session; that is the only credential
  the page holds.
- **The model proposes, the server validates.** `record_answer` arguments are
  checked against the question graph server-side: unknown ids are rejected, ids
  that are not *currently visible* are rejected, and values are coerced into the
  shape the question can hold. Invalid multi-select options are dropped rather
  than stored. The browser is not a trusted writer just because a model is
  driving it.
- **Hedging becomes `unknown`, never `no`.** "Not sure", "maybe", "I don't know"
  all land as UNKNOWN. The prompt explicitly forbids talking a seller into a yes
  or a no.
- **Cost is bounded**: a hard ceiling on session length, a per-disclosure hourly
  cap, and `gpt-realtime-2.1-mini` by default. A public demo with an unbounded
  realtime socket is an unbounded bill, and a seller link is by design shareable.
- Turn detection is semantic VAD with **low eagerness**, because sellers pause
  mid-sentence while they remember, and cutting them off at the pause is the
  fastest way to make a voice UI feel hostile.

---

## Architecture

FastAPI + SQLAlchemy + Postgres (SQLite locally), React + TypeScript + Vite built
into `app/static` and served by the same process — one service, no CORS, no
second origin to secure.

```
app/
  tds/          the form model, and the only place that knows what a TDS is
    routing.py    lane decisions + the rationale behind each, as data
    questions.py  the question graph: 81 questions, 9 chapters
    bindings.py   answer value -> widget writes (incl. the collision handling)
    fieldmap.py   widget geometry, validation, resolution
    gating.py     the dependsOn grammar, mirrored in the browser
    rules.py      16 consistency rules specific to this form
    fill.py       local PDF renderer + addendum
    roles.py      who signs what, incl. the three lines the PDF omits
  services.py   the single write path for both lanes
  auth.py       agent sessions and seller links
  docuseal.py   template construction and submissions
web/src/        the two views
```

The question graph is **sent to the browser**, not re-declared there. One
definition of what the questions are, what gates them and which lane they default
to means the form UI, the voice agent's tool schema and the PDF renderer cannot
disagree about the form.

---

## Setup

```bash
git clone https://github.com/Noctilucenty/loqol-tds && cd loqol-tds

python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd web && npm install && npm run build && cd ..

.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000, click **Use the demo account**, create a deal, hit
**Create link**, open it. That is the seller flow.

Everything is optional in `.env`:

```bash
OPENAI_API_KEY=sk-...          # without it, voice turns itself off and says so
DOCUSEAL_API_KEY=...           # without it, the PDF renders locally instead
DOCUSEAL_TEMPLATE_ID=5507359   # or run scripts/create_docuseal_template.py
SECRET_KEY=...                 # required in production
DATABASE_URL=postgresql://...  # defaults to SQLite
```

```bash
.venv/bin/python -m pytest -q                       # 42 tests
.venv/bin/python scripts/create_docuseal_template.py  # build the template
.venv/bin/python scripts/extract_widgets.py           # re-extract PDF geometry
```

---

## What I'd build next, and what I knowingly left out

**Next, in order:**

1. **Email OTP before signature.** The one real gap in the auth story. See above.
2. **Resumable voice.** Today a voice session starts fresh each time. It should
   pick up mid-sentence with the transcript of what was already covered.
3. **Agent-side reconciliation.** Agents see flags but resolve them by editing
   answers. They should get the seller's own reconciliation cards, and be able to
   send one question back rather than reopening the whole disclosure.
4. **The other California forms.** The question-graph/bindings split is form-
   agnostic; only `questions.py` and `tds_widgets.json` are TDS-specific. SPQ and
   the NHD would reuse everything else.
5. **Real-time cross-lane sync.** The two lanes reconcile on write, but a form
   open in one tab does not live-update when the voice agent answers in another.

**Knowingly left out:**

- **Buyer and agent signing.** The template defines all six roles, but a
  submission only invites the seller side. Buyer acknowledgment is a later event
  in the transaction and modelling it properly means modelling the transaction.
- **Section D** (smoke detector / water heater bracing affirmations) has no form
  fields — it is printed text the seller affirms by signing. Nothing to collect.
- **Multi-unit TDS.** The duplex/triplex checkbox and unit list are captured in
  the agent view, but a genuine four-unit filing needs four forms and a
  per-unit answer set.
- **Emailing the seller their link.** The agent copies it. Wiring transactional
  email is plumbing, and it is the least interesting thing here.
- **Offline drafts.** Answers save per-change over the network. A seller in a
  basement with no signal loses the current question, not the session.
- **Signature images on API auto-sign.** Auto-signing via the API marks a
  submission complete without drawing a signature; the browser flow draws it.
  Used only to verify the loop.
