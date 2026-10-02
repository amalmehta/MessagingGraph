# How Messaging Graph works

The design notes behind the app: what it measures, what it deliberately refuses to do, and how your data is protected.

A local-first app that reads a message store, maps the plans you have in flight,
builds a profile of each relationship, and suggests small, concrete things you
could do about any of it.

Everything runs on your machine. Pass 1 makes **zero network calls**, and the only
route to the network is a consent gate that refuses by default.

```bash
./run.sh          # http://127.0.0.1:8077
.venv/bin/python validate.py   # check the analysis against known ground truth
```

## What's here (pass 1)

| Piece | Where | What it does |
|---|---|---|
| Ingestion | `app/ingest/` | `SyntheticSource` generates a realistic 9-month corpus. `IMessageSource` implements the same interface against `chat.db`, opened `mode=ro&immutable=1`. |
| Privacy | `app/privacy.py` | Redaction, per-contact exclusion, retention cutoff, and `NetworkGate` — the single chokepoint anything must pass to leave the machine. |
| Relationships | `app/analysis/relationships.py` | Seven signals composing into a descriptive connection state, each with its ordinary explanation. |
| Suggestions | `app/analysis/suggestions.py` | Turns observations into small, optional, concrete things you could do. |
| Plans | `app/analysis/plans.py` | Plan extraction from raw text and the state machine that finds the ones that stalled. |
| Graph | `app/graph.py` | People + live plans as nodes; participation and shared threads as edges. |
| Rate series | `app/analysis/series.py` | Rolling 7-day buckets of messages sent/received, per contact and overall. |
| UI | `app/static/` | Four views, no dependencies, hand-rolled force layout. |
| Validation | `validate.py` | Checks the analyzer recovers the generator's hidden parameters. |

## Messaging rate & chat history

Three places show the rate, all from the same rolling 7-day buckets:

- **Now** — messages a week across every thread, with a 20 / 40 / 80-week range.
- **People** — a 20-week sparkline per row, so the table shows trajectory next to
  verdict. Sofia's flat-then-climb and Marcus's drop-off are visible without
  opening either profile.
- **Person drawer** — the same weeks split into what you sent and what you
  received, which is where a one-sided thread becomes obvious.

Weeks before a contact's first message are dimmed and ruled off rather than drawn
as a flat zero — never having met is not the same as silence.

**Chat history** is the full thread, not a preview: paged backwards 60 at a time,
searchable with match highlighting, and broken into conversations on the same
six-hour rule the analysis uses. Paging is verified to reach every message with no
repeats — 548 messages over 14 pages for the test thread.

### Charts

Two series on the dark surface, validated rather than eyeballed:

```
#3987e5 (you) / #d95926 (them)  on #151a21
worst-pair CVD ΔE 26.8 · normal-vision ΔE 31.8 · both ≥ 3:1 contrast — all checks pass
```

Stacked columns (part-to-whole over time), ≤24px thick with a 2px surface gap
between segments and neighbours, 4px rounded data-end square at the baseline,
hairline solid gridlines, legend always present, and only the peak direct-labelled
— everything else lives in the hover tooltip. SVGs render at exact pixel width
under a `ResizeObserver` rather than scaling through a viewBox, so hairlines stay
hairline and 9.5px axis text stays 9.5px at any container size.

## What this app refuses to do

It does not grade your friends.

Message statistics can tell you someone has been slower to reply this quarter.
They cannot tell you that person is caring for a sick parent, changing jobs, or
having the worst year of their life — and any of those explains a cooling thread
far better than the thread does. An app that reads a reply-time gap and concludes
something about a friendship is reasoning from a keyhole.

So three rules run through the whole thing:

**States describe, they don't rank.** A connection is `close`, `steady`,
`practical`, `uneven`, `quiet`, `too early`, or `in groups only` — descriptions of how something is
running right now, which can change next week. There is no "bad" state, and no red
anywhere on a person; red reads as an alarm and nothing here justifies one.

**Every number comes with its innocent explanation.** Each signal shows the
measurement, what it means, and the ordinary reading of the same number — printed
right underneath, not buried in a footnote:

> **Reply speed, them and you** — they reply in 3.7h, you in 6m
> *Typical time to answer, in each direction.*
> — Reply speed mostly measures someone's relationship with their phone. Slow
> repliers are often the same with everyone, and a busy season can stretch this
> for months without anything being wrong.

**Good news is surfaced too.** An app that only reports problems teaches you to
read your friendships as a list of problems, which is both miserable and
inaccurate. Half the suggestions are warm ones.

## The measurement worth explaining

**Self-editing** asks how much you soften your language with a person — apologies,
hedges, pre-emptive excuses, "no worries if not". Measured absolutely this is
useless, because some people are just polite. So it is measured **against your own
baseline**: 33 means you write to them as you write to anyone; 100 means you soften
three times as much.

This is the one measure pointed at the user rather than the other person, which is
what the original brief asked for — *"if you are being overly cautious/editing
yourself"*. It is offered as something to notice, never as a fault, and never as
evidence against the other person. Writing more carefully to a colleague, or to
someone you have known three weeks, is ordinary good manners. When self-editing is
what's driving a reading, the suggestion is addressed to you and says so.

Below ~45% confidence the app says **too early** and declines to characterise the
friendship at all. A dozen messages cannot support a claim about a person.

## Does the analysis actually work?

The synthetic corpus is generated from persona *behaviours* — how warm someone is,
how much you soften with them, how often plans land. The analyzer never sees those
parameters; it reads text and timestamps only. `validate.py` checks that what comes
out recovers what went in:

```
self-editing        r = 0.97        message volume      r = 0.99
warmth              r = 0.94        reply latency       r = 1.00
depth               r = 0.86        plan follow-through r = 0.61
```

Plus behavioural checks: the thread the user over-edits in reads as uneven, the
logistics-only thread is not mistaken for closeness, the thin thread declines to
characterise the friendship, and the friend who went silent is described rather
than judged.

The tone is tested too, because tone was a real defect here and not a matter of
taste: every signal must carry a benign reading, no state may pass judgement on a
person, no headline may contain any of a list of loaded phrasings, at least one
suggestion must be warm, and no single person may appear twice in the suggestion
list — a page that mentions the same friend four times reads as a case being built
against them.

The rate series and history are checked too — every message lands in exactly one
bucket, a new contact's pre-history is empty and marked, paging reaches the whole
thread without repeats, and search is case-insensitive and scoped to the thread.
251 checks total.

Follow-through at 0.61 is the weakest of these and carries the lowest weight in
the verdict. Reading intent from text is genuinely hard — enthusiasm is easy to
detect, a date that quietly never got set is not.

## Privacy, concretely

- **Local only** is on by default; while it is on, `NetworkGate.authorize` raises
  for any purpose.
- Consent is **per purpose**. Granting `draft` does not grant `tone`.
- Callers must send the string the gate *returns*, not their own payload, so
  redaction cannot be skipped by accident.
- Excluding a contact drops them before analysis — including any group thread they
  belong to, so they cannot leak back through group statistics.
- Every crossing, allowed or blocked, is written to an audit log the UI shows.
- The Privacy screen renders the exact payload a Claude call would contain, in
  full, before anything is sent.

## Switching to real messages

`app/ingest/imessage.py` already implements `MessageSource` against the real store.
To use it, grant Full Disk Access to your terminal (System Settings → Privacy &
Security), then construct `AppState("imessage")` in `app/main.py`. Nothing in the
analysis layer changes — it only ever sees the `Corpus` type.

## Pass 2

Drafting and the reminder queue. The Claude seam (`app/analysis/llm.py`) and the
consent gate are already built and tested; what's missing is voice-matched draft
generation and the accept/decline queue on top of the stale plans and cooling
relationships this pass identifies.

## Using your own messages

About two minutes, and Full Disk Access is only on for the first of them.

1. **System Settings → Privacy & Security → Full Disk Access** — add your terminal
   app and reopen it.
2. **`./mg import`** — takes a snapshot of `chat.db` and your Contacts, prints counts
   only (never a name, number or message), and writes an encrypted vault.
3. **Switch Full Disk Access back off**, then **`./mg serve`** and open the link it
   prints.

`./mg doctor` checks for anything left open. `./mg wipe` deletes the vault and its key.
Re-run `./mg import` whenever you want fresher data; ids stay stable across imports.
Exclusions, the other privacy settings and the audit log live only in the server's memory,
not in the vault: reloading data from the Mac app's menu keeps them, but they reset when the
server restarts, so set exclusions again on the Privacy page after a restart.

## Security model

| Risk | What handles it |
|---|---|
| Full Disk Access lets *every* program run from that terminal read everything | Needed only while `mg import` runs; the server never needs it. `mg doctor` flags it if you leave it on. |
| A copy of your messages on disk | Encrypted (Fernet: AES-128-CBC + HMAC-SHA256). Key in the login Keychain, not beside the data. Stored in `~/Library/Application Support/messaging-graph` — outside the project, folder `0700`, files `0600`, excluded from Spotlight and Time Machine. Plaintext is never written, not even temporarily. |
| Any website you visit can send requests to localhost | Host-header allowlist (stops DNS rebinding), Origin check on every state change plus a `SameSite=Strict` session cookie (stops cross-site requests), and a one-time launch token printed only to your terminal. |
| Message text and group names are written by other people | Escaped everywhere they render, backed by a Content-Security-Policy that forbids inline script, so a missed spot still can't run anything. |
| Leaks through side channels | Request logging off (URLs carry search terms), no API docs endpoint, and every id is a keyed hash — no phone number or email appears in a URL. |
| Sensitive senders | Short codes, business chats and numbers you've never replied to are dropped at import. Messages you unsent stay unsent. |
| Data leaving the machine | Local-only by default, enforced by the network gate. |

**What this doesn't protect against:** malware already running as you (it can
read the Keychain just as this app does), or someone with your unlocked Mac.
FileVault is what protects a lost laptop.

**Real-store details the reader handles:** rows still in the write-ahead log (an
`immutable=1` open silently misses the newest messages), bodies stored only in
`attributedBody`, tapbacks folded into reactions with removals honoured, one person
across SMS / iMessage / email merged, your own Apple ID excluded from groups, and
timestamps converted to local time.

## Plans, connections and drafts

**Plans** are split three ways on the Now page: *confirmed* (someone said yes, or
it has a date), *still being discussed*, and *between groups* (live plans in group
chats, with who's in each group). *Worth reviving* only covers plans wanted in the
last 90 days; older stalled plans are counted as lapsed rather than put in front of
you, since "we should get coffee" from last spring isn't a to-do.

**Click any plan** — in the Plans panel, the plan graph, or a person's plan list — to see the
messages it came from, grouped by each time it came up, with the messages that name it
highlighted. The replies around them are how the app judged whether it's confirmed, still
being discussed, or stalled.

**Who's connected** comes in three kinds, each showing its evidence. *Group chats*:
two people share one. *Mentions*: someone comes up by name, at least twice, in your
chat with someone else ("mentioned in your chat with Priya (3×)"). Names that are also
everyday words (Will, May, Iris) are skipped, and a first name two contacts share only
counts as the full name. *Shared interests* group people by what stands out in their
threads compared with your others, and by activities you actually did together.
Generic plans like dinner or coffee don't count as interests.

**Might get along** suggests introductions between people who aren't connected yet
but share interests and a similar vibe, with a copyable introduction. It's only a
suggestion: introducing people tells each of them about the other.

**Propose a plan** drafts an invitation for a group from its own history, in this
order: revive something the group wanted recently and never dated, repeat what they
actually do together, or start simple. The day is whichever weekday has worked for
that group, at least three days out. Uninvite anyone and the draft adjusts ("Hey you
two" instead of "Hey all"). A one-on-one draft only revives plans from that
one-on-one thread, never a group's.

**Messages worth sending** ranks concrete drafts by how much they'd likely mean:
someone waiting on your reply, a follow-up on something they told you (an
interview, a move, a trip), a plan, a check-in when a close thread has gone quieter
than usual, or reconnecting with someone who went quiet. Each is written the way you
usually text that person — your greeting, lowercase or not, exclamation marks,
emoji. Health, grief and money never get an automatic follow-up. Copy, ask for
another version, or skip; nothing sends from the app.

## Vibes

Each profile gets a short description ("Background warmth — a consistent hum of care
running under your weeks") and a mood reading, both computed locally.

Mood uses VADER with a texting correction, because raw sentiment scores "miss you"
and "I'm dead 😭😭" as negative. Laughter and affection read warm, politeness and
short banter read neutral, slang praise ("that was sick") reads positive, and grief
always reads as heavy, even with a "lol" attached.

A person's defining traits are ranked against your other relationships, not in
absolute terms, so close friends don't all come out identical. Vibes never affect
connection state: a heavier stretch usually means someone trusted the other with
something hard. Descriptions avoid famous or romantic pairings, since they're read
about siblings and coworkers too.

## Real conversations only

Automated and marketing threads are set aside before any analysis runs, so they
never show up as a relationship, a plan, a draft or a connection. The import already
drops short codes, business chats and numbers you've never written to. What this
catches is the rest: a saved business card sending appointment reminders, a marketing
number you replied STOP to, delivery and account alerts from ordinary numbers.

A thread is set aside for reasons you can see on the Privacy page: marketing wording
("5 of 5 messages from them contain opt-out instructions"), mostly links, a repeating
template, replies that were only commands like STOP or C, or a business name. Real
back-and-forth counts against filtering, and anyone you've written 25 or more real
messages to is never filtered automatically. If the filter gets one wrong, **It's a
real person** restores it; **Set it aside as automated** on a profile hides one it
missed. Your call always wins.

## Naming people

Real conversations with numbers that aren't in your Contacts show a **Name them**
prompt. Each person shows their last few messages and any groups they're in, so you
can tell who it is. Names are cleaned (trimmed, no control characters, at most 60
characters) and don't change your Contacts app.

Until then, someone with no name shows as the last four digits of their number
("…0111"), not the area code, so two unnamed people never both read "(408)". Drafts to
them say just "Hey". A group chat that was never given a name is titled by its
members, and the title updates as soon as you name any of them, including titles
an earlier import saved.

Names and filter corrections are saved encrypted beside your data with the same
Keychain key, keyed by the stable ids, so they survive a fresh `./mg import`. Running
the import again also records which contacts are business cards, which sharpens the
filter; without it, business detection falls back to the contact's name.

**In groups only.** Someone you know only through group chats gets no one-on-one
profile: there's no one-on-one pattern to describe, and calling them "gone quiet"
would describe a conversation that never happened. They still appear in groups,
group plans and connections, but not in the gone-quiet or close counts, drafts, or
introductions.

## Where people live

Each person can have a location tag. The app suggests one only from things people
say about where they live: "finally moved to Oakland", "here in Brooklyn", "based in
London", or you asking "how's life in Seattle?". Trips don't count ("flying to Denver
this weekend"), your own move isn't pinned on the person you told, and a place has to
be one the app recognises, so "moved to a new team" can't become a location. The
newest firm statement wins; a single passing "here in" is kept only as a guess.

Guesses show with a question mark and the quote that suggested them. **Add
locations** asks about the people you talk to most, a dozen at a time: confirm the
guess, type a place, or say you don't know, which also hides the guess and stops
the question. Answers are saved encrypted with your other corrections. A place is
saved in one consistent spelling ("oakland" and "OAKLAND" become Oakland, "nyc" becomes
New York), and Who's connected groups people **By location** once two or more share
one.
