# Loqol TDS — seller disclosure app

Live: **https://loqol-tds.onrender.com**

A web app that interviews a home seller, then fills and sends the California
Transfer Disclosure Statement for signature.

---

## Try it in about two minutes

1. Open https://loqol-tds.onrender.com
2. Click **Try it without signing up**. You get your own private workspace with a
   sample deal already in it. Nothing is shared with other visitors.
3. Open the deal, click **Create link**, copy it.
4. Paste it in another tab (or your phone). That is what the seller sees.
5. Answer a few questions. Tick "Public Sewer System" *and* "Septic Tank" on the
   sewer screen if you want to see the contradiction handling.
6. Get to the end and you will hit the review screen. Then go back to the agent
   tab and hit **Open filled PDF**.

If you want to see the reasoning behind the voice/tap split without reading this
whole file, there is a page for it at `/design`. It is generated from the same
question definitions the app runs on.

---

## The decision I actually spent my time on

The brief asks which parts of the form should be spoken and which should be
tapped, and then to defend it. Here is the short version:

**Talk about the questions that are hard to understand. Tap the ones that are
just long.**

My first instinct was the obvious one: voice for the scary legal sections,
tapping for the easy stuff. Then I read the form properly.

Section A is fifty checkboxes. Range, oven, microwave, trash compactor, gazebo,
sauna. Not one of them is hard to understand — the work is just remembering what
your own house has, and there are a lot of them. Picture someone saying all fifty
out loud. It takes longer, you lose your place, and you can't glance back for the
one you skipped. So Section A is a grid.

Section C is sixteen questions like "any encroachments, easements or similar
matters that may affect your interest in the subject property". Nobody who owns a
house can answer that as written. The problem isn't typing — it's that the
question makes no sense until someone rephrases it and gives an example, which is
a conversation. And a bare yes is useless here anyway: the form wants what
happened, roughly when, and whether anyone fixed it. That's a paragraph, and
asking for a paragraph about someone's roof at ten at night is how you get an
empty box. So Section C is voice.

The split lands at 63 tap / 19 voice across 82 questions. Each question stores
its lane and the reason as data, and `/design` builds the table from that, so the
page can't drift from what the code actually does.

Two things I moved off the seller entirely:

- **Section I** (which inspection reports travel with the transfer) is on the
  agent's screen. Asking a homeowner that is asking them to do their agent's job.
- **The order.** The printed form opens with Section I and buries the two
  sections that carry real legal risk in tiny type on page two. I re-cut it into
  eight chapters in the order a person can actually answer them.

None of it is a lock. Every question still shows its tap controls, every question
can be answered out loud, and both the welcome screen and the welcome-back screen
offer to talk you through the whole form if that's what you'd rather do. Both
paths call the same function on the server, so an answer you said and an answer
you tapped are the same row — which is what makes "start one way, finish the
other" true rather than a claim. A conversation resumed the next evening picks
up from what is already answered rather than starting again at the top.

Spoken pacing is not uniform, because the questions are not. The thirty-nine
inventory items are asked a room at a time — "in the kitchen and laundry, do you
have a range, an oven, a microwave, a dishwasher..." — and one reply populates
the whole group, noes included. Asking those one at a time is technically
correct and unbearable: thirty-nine turns to establish what two sentences
establish. The questions that need care get the opposite treatment, one at a
time and with follow-ups, because that is where a wrong answer costs something.
The brief's compound items ("Water Heater: Gas / Solar / Electric") are one
question in both lanes for the same reason — it is one thing a person knows
about their house, not three.

---

## The seller experience

The person filling this in is stressed, not technical, and doing it after work.
Each of these is an answer to a specific way that goes wrong.

**They don't understand the question.** Every question has a "What does this
mean?" with three layers: plain English, a concrete example in a homeowner's
voice, then the actual statutory wording. The statutory wording is last but never
removed — they're signing that sentence. Putting it first is what makes people
quit.

**They don't know the answer.** "I'm not sure" is a real button, the same size as
Yes and No. This is the most consequential decision in the whole data model. On a
TDS, answering *No* when the truth is *I never checked* is how sellers get sued.
So an unknown is stored as its own thing, and when it reaches the form **both
boxes are left blank**, which is what a careful person would do on paper. There
are tests for this because I broke it once already (see below).

**They contradict themselves.** Sixteen rules specific to this form — septic tank
*and* public sewer, a pool barrier with no pool, a gas water heater with no gas
supply, an HOA with no CC&Rs. Nothing blocks at the moment it happens; arguing
with someone mid-recall about something they said forty minutes ago is how you
lose them. Conflicts get queued and come back at the review screen, phrased as a
question with both answers shown and neither pre-selected:

> You told us the house is on both a public sewer and a septic tank. Almost every
> home has one or the other. Which is it?

Soft ones also offer "Both are right, leave them", because sometimes they are.
And once you've settled one, it stays settled — it doesn't pop back on the next
answer you type.

**How much is left.** Chapters plus a time estimate, not "38 of 150 fields". That
number is frightening and also a lie, because most of those fields don't apply to
any given house. Progress is measured over the questions *this* seller will
actually be asked.

**They close the tab.** Every answer saves the moment it changes. There is no
save button, because nobody should have to know that saving is a thing. The
server tracks where they were, so closing the tab costs you the question you were
on and nothing else.

---

## Data model

```
agents ─< deals ─< disclosure_sessions ─< answers          current value, one row per question
                                       ├─< answer_events   append-only, every write ever made
                                       ├─< access_tokens   the seller's link, stored hashed
                                       ├─< flags           contradictions waiting on a decision
                                       └─< voice_sessions  cost ceiling and audit
```

The split between `answers` and `answer_events` is the part worth explaining. Two
questions get asked of a disclosure and they pull opposite ways. "What is the
answer" gets asked on every page render and every PDF — cheap against a current
value table, expensive against an event log. "What did the seller say, when, and
through which channel" gets asked once, in a dispute, and is impossible against a
current-value table. So I keep both. The agent's History tab is just that second
table.

### What happens when someone answers the same question twice

- The `answers` row is upserted, so there's one current value and no ambiguity
  about what prints. `revision` goes up.
- An event row is appended with the old value next to the new one.
- If the new answer *disagrees* with the old one and came from the other lane, a
  flag is raised instead of the last write silently winning.

That last one matters. Someone who taps No and then tells the voice agent "well,
actually, yes" hasn't made a mistake — they've remembered something. The later
answer stands because it's later, but the disagreement surfaces at review so a
human decides. Overwriting silently loses a real signal; blocking punishes them
for remembering.

---

## Auth

**Agents** get email and password, Argon2id hashed. Login creates a row in
`agent_sessions` and sets an HttpOnly, SameSite=Lax, Secure cookie holding a
256-bit random token; only the SHA-256 of it is stored. It's a server-side
session rather than a JWT on purpose — you can't revoke a stateless token before
it expires, and "log this person out now" is table stakes for something that
opens legal documents.

**Sellers get no password at all.** Making a stressed homeowner create an account
at 10pm to fill in a form their agent sent them is how you lose them before
question one. The link *is* the credential: 256 bits of randomness, stored only
as a hash, scoped to one disclosure, expiring in 14 days, and revocable from the
deal page.

### "What if someone guesses a seller's URL?"

They don't. 2^256 isn't a guessable space, and since only the hash is stored, a
dump of the token table doesn't give you working links either. Wrong, revoked and
expired all return the same 404, so you can't probe for whether a token was real.

The honest risk isn't guessing, it's the link leaking — a forwarded text, a
shared screen — because anyone holding it is treated as the seller. What I do
about that: no PII in the URL, `Referrer-Policy: no-referrer`, `Cache-Control:
no-store` on seller routes, every use logged with IP and shown to the agent as
"last opened at", one-click revoke and reissue, and voice sessions rate-limited
per disclosure so a leaked link can't burn the API budget.

What I didn't build: an emailed one-time code in front of the *signature* step.
That's where the link stops being a convenience and becomes a legal act, and a
bearer link isn't enough identity for it. It's a gap, and I'd rather say so than
have it look like an oversight.

---

## Mapping answers onto the form

### The PDF isn't flat

The brief says the attached PDF is flat with no fillable fields. It isn't — it
has a full AcroForm with 159 placed widgets. That turned out to matter, because
the AcroForm has two defects that make the form **impossible to fill correctly
by field value**:

1. `Solar` is one field with two widgets on it — one on the Pool/Spa Heater line
   and one on the Water Heater line. An AcroForm value is per field, so setting
   `Solar` ticks both. A solar pool heater with a gas water heater literally
   cannot be expressed.
2. `Other2Describe` owns two widgets that need different text.

So bindings address a *widget* — name plus an occurrence index in reading order —
not a field name. `Solar#0` is always the pool heater box, `Solar#1` is always
the water heater box. There's a test pinning it, and you can see it come out
right on the live DocuSeal document.

### Coverage

All 159 widgets are accounted for, and it's checked on every boot:

| | |
|---|---|
| Bound to questions | 133 |
| Owned by a signer role | 26 |
| Unhandled | 0 |

`validate()` fails startup if a binding points at a widget that doesn't exist, or
writes text into a checkbox. A mistyped field name is the one failure that would
otherwise be silent — you get a form that looks filled and isn't.

### Compound fields and the shared explanation

"Water Heater: Gas / Solar / Electric" is one question with a multi-select, not
three checkboxes. Same for garage, water supply, gas supply, pool heater.

Section C's sixteen questions share one explanation area made of five ruled lines
of different widths. Sellers don't think in ruled lines, so the app takes one
narrative and lays it out. Nothing gets silently cut: if it doesn't fit, the last
line gets a continuation marker and the full answer goes onto a generated
addendum page, restated in full under a section heading rather than continuing
mid-sentence.

---

## DocuSeal

Verified end to end against the free sandbox — template built, submission
prefilled, signed, completed PDF downloaded. Nothing was paid for.

I build the template from measured coordinates rather than letting DocuSeal
inherit the PDF's form fields, because inheriting them would reproduce both
defects above. It also means the three agent signature lines — which the form
prints with no fields behind them at all — can be declared from the page's own
vector geometry and come out as real signable fields.

Widget rects are PDF points from the bottom-left; DocuSeal wants fractions from
the top-left:

```python
x = x0 / 612                  w = (x1 - x0) / 612
y = (792 - y1) / 792          h = (y1 - y0) / 792
```

On signer roles: the brief says five, I modelled six (Seller, Co-Seller, Buyer,
Co-Buyer, Listing Agent, Selling Agent). The difference is whether the second
Seller and Buyer lines are one role signing twice or two parties. I read them as
two — a co-owner is a separate signatory with their own date — and a submission
just omits the roles a deal doesn't need.

Answers go into DocuSeal read-only. By then the seller has answered and reviewed
everything here; DocuSeal is where they sign, not a second chance to retype a
legal disclosure.

**It works without DocuSeal too.** No API key and the app renders the filled PDF
locally and tells you so. Clone this with no credentials and you still get a
working seller flow and a correct completed form.

---

## Voice

Runs in the browser over WebRTC to the OpenAI realtime API. No phone, no phone
number, no server in the audio path.

- The real API key never reaches the browser. The server mints a short-lived
  client secret scoped to one session.
- The model proposes, the server decides. Tool call arguments are re-checked
  server-side: unknown question ids rejected, questions that aren't currently
  being asked rejected, values coerced into what the question can hold. The
  browser isn't trusted just because a model is driving it.
- Hedging becomes "unknown", never "no". The prompt explicitly forbids talking
  someone into a yes or a no.
- Cost is bounded: hard session cap, per-disclosure hourly limit,
  `gpt-realtime-2.1-mini` by default. A public demo with an unbounded realtime
  socket is an unbounded bill, and a seller link is shareable by design.

---

## What I got wrong

I had this reviewed before submitting and it found real problems. The ones worth
admitting, because they're the interesting ones:

- **"I don't know" was printing as a tick.** I stored unknowns as the string
  `"unknown"`, and `bool("unknown")` is `True`, so on the ~43 plain checkbox
  questions it checked the box. The exact failure the design was built to
  prevent, in the code meant to prevent it.
- **The voice agent went silent after one answer.** The realtime API doesn't
  generate a turn off the back of a tool result, and I never sent
  `response.create`. It recorded the first answer and stopped. This is why the
  fix is now in the code and why there's a note about it here rather than a
  claim that voice always worked.
- **...and then asked for six turns at once.** The fix above asked for a turn
  after every tool result. That was fine while answers arrived one at a time,
  and wrong the moment a whole room could be answered in one breath: seven
  `record_answer` calls became seven `response.create` calls, the realtime API
  allows one active response, and the six rejections were rendered to the seller
  as an error banner. Turns are coalesced now. Both halves of this only showed
  up by driving a live session in a real browser — the unit tests and the
  headless smoke pass either way, which is the honest limit of both.
- **A local SQLite file was being pushed to this public repo.** `*.db` in
  .gitignore doesn't match `loqol.db-wal`, and in WAL mode the sidecar is where
  the rows live. Purged from history, and the ignore rule is fixed.
- **Section I had no UI at all.** The route existed, nothing called it, so every
  form would have gone out with Section I blank.
- **A dismissed contradiction came back on the next keystroke** and permanently
  deadlocked sending for signature.
- And while fixing all that I shipped a React hooks-order bug that blanked the
  whole seller flow. That one is why `scripts/smoke.mjs` now exists: the Python
  tests never rendered a page, so nothing caught it. It walks the real flow in a
  browser and fails on any page error.

All of those have regression tests now (`tests/test_audit_regressions.py`), one
per defect, named after what it would have done to a seller.

There are three checks, because each one catches things the others cannot:

- `pytest` — 73 unit tests over the form model, the API and the defects above.
- `scripts/smoke.mjs` — walks the seller flow in a real browser and fails on any
  page error. It exists because the Python tests never render a page.
- `scripts/audit.py` — drives the whole workflow against a running instance:
  create a deal, issue a link, answer every question, trip a contradiction,
  submit, download the PDF, push it to DocuSeal, and confirm every write path is
  frozen afterwards. 34 checks. Point it at production and it tells you whether
  the thing actually works, which is the only question that matters.

`scripts/check_hooks.py` exists for one reason: I shipped the same React
rules-of-hooks bug twice, and both times it blanked the whole seller flow.

---

## Running it

```bash
git clone https://github.com/Noctilucenty/loqol-tds && cd loqol-tds

python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd web && npm install && npm run build && cd ..

.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Then http://localhost:8000 and click "Try it without signing up".

Everything in `.env` is optional:

```bash
OPENAI_API_KEY=sk-...          # without it voice turns itself off and says so
DOCUSEAL_API_KEY=...           # without it the PDF renders locally instead
DOCUSEAL_TEMPLATE_ID=5507359   # or run scripts/create_docuseal_template.py
SECRET_KEY=...                 # required in production
DATABASE_URL=postgresql://...  # defaults to SQLite
```

```bash
.venv/bin/python -m pytest -q                         # 84 tests
.venv/bin/python scripts/check_hooks.py               # rules-of-hooks check
.venv/bin/python scripts/audit.py <url>               # drive the real workflow
npm --prefix web i -D playwright && npx playwright install chromium
node scripts/smoke.mjs                                # browser walk-through
.venv/bin/python scripts/create_docuseal_template.py  # build the template
.venv/bin/python scripts/extract_widgets.py           # re-extract PDF geometry
```

Deployment notes are in [DEPLOY.md](DEPLOY.md). It's one Docker service — the
React app builds into `app/static` and FastAPI serves it, so there's no CORS and
no second origin.

---

## What I'd do next, and what I left out

Next, in order:

1. **Email OTP before signing.** The one real hole in the auth story.
2. **Addendum on the signed document.** Right now, if an explanation is too long
   for the ruled lines, the local preview gets an addendum page but sending for
   signature refuses rather than shipping something truncated. The fix is a
   per-submission template that includes the addendum page.
3. **The other California forms.** Only `questions.py` and the widget map are
   TDS-specific. SPQ and NHD would reuse everything else.

Knowingly left out:

- **Buyer and agent signing.** All six roles are in the template, but a
  submission only invites the seller side. Buyer acknowledgment is a later event
  and modelling it properly means modelling the transaction.
- **Section D** has no fields — it's printed text the seller affirms by signing.
  Nothing to collect.
- **Multi-unit filings.** The duplex checkbox is captured, but a real four-unit
  filing needs four forms and four answer sets.
- **Emailing the seller their link.** The agent copies it. Wiring transactional
  email is plumbing and the least interesting thing here.
- **Offline drafts.** Answers save over the network per change. No signal means
  you lose the current question, not the session.
