"""Synthetic message corpus.

The point of this generator is not to look pretty in a demo -- it is to give the
analysis something real to find. Each persona is defined by the *behaviours* the
metrics are supposed to detect (someone who has quietly gone cold, someone I
over-apologize to, someone who floats plans and never lands them), and the text
is then generated from those parameters. The analyzer never sees the parameters,
so agreement between the two is a genuine result rather than a lookup.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..models import Contact, Corpus, Message, Thread, SELF_ID

SEED = 20260826
WEEKS = 40


@dataclass
class Persona:
    id: str
    name: str
    handle: str
    label: str                  # a neutral description of the behaviour pattern
                                # this persona generates; never seen by the analyzer
    rate: float                 # conversation bursts per week at baseline
    trend: float                # multiplier applied across the window, 1.0 = flat
    their_warmth: float         # 0..1
    my_warmth: float            # 0..1
    my_share: float             # fraction of messages in a burst that are mine
    my_initiation: float        # share of bursts I start
    their_latency: float        # median reply seconds, scaled
    my_latency: float
    my_hedging: float           # how much I soften/qualify with this person
    depth: float                # long messages + late-night share
    plan_rate: float            # chance a burst carries a plan
    followthrough: float        # chance a floated plan gets confirmed
    dormant_after: int | None = None   # week index after which they go quiet
    starts_at_week: int | None = None  # met partway through the window


PERSONAS: list[Persona] = [
    Persona("p_priya", "Priya Raman", "+14155550142", "highest warmth, balanced",
            rate=4.2, trend=1.1, their_warmth=.92, my_warmth=.9, my_share=.5,
            my_initiation=.48, their_latency=380, my_latency=420, my_hedging=.05,
            depth=.75, plan_rate=.3, followthrough=.85),
    Persona("p_marcus", "Marcus Webb", "+14155550188", "volume declines across window",
            rate=3.6, trend=0.18, their_warmth=.55, my_warmth=.7, my_share=.6,
            my_initiation=.72, their_latency=9000, my_latency=900, my_hedging=.22,
            depth=.45, plan_rate=.22, followthrough=.25),
    Persona("p_dani", "Dani Okafor", "+14155550117", "user carries the thread",
            rate=1.9, trend=0.9, their_warmth=.35, my_warmth=.75, my_share=.72,
            my_initiation=.85, their_latency=28000, my_latency=600, my_hedging=.3,
            depth=.3, plan_rate=.25, followthrough=.2),
    Persona("p_jordan", "Jordan Feld", "+14155550163", "user hedges heavily here",
            rate=3.1, trend=1.0, their_warmth=.3, my_warmth=.6, my_share=.63,
            my_initiation=.7, their_latency=14000, my_latency=300, my_hedging=.72,
            depth=.35, plan_rate=.3, followthrough=.3),
    Persona("p_sofia", "Sofia Marino", "+14155550129", "starts late, ramps up",
            rate=1.1, trend=5.0, their_warmth=.85, my_warmth=.82, my_share=.5,
            my_initiation=.5, their_latency=500, my_latency=520, my_hedging=.12,
            depth=.6, plan_rate=.35, followthrough=.75, starts_at_week=24),
    Persona("p_theo", "Theo Blackwood", "+14155550194", "practical, low warmth",
            rate=5.0, trend=1.0, their_warmth=.2, my_warmth=.25, my_share=.5,
            my_initiation=.5, their_latency=1100, my_latency=1000, my_hedging=.08,
            depth=.12, plan_rate=.12, followthrough=.6),
    Persona("p_nadia", "Nadia Osei", "+14155550171", "long messages, low volume",
            rate=0.85, trend=1.0, their_warmth=.8, my_warmth=.85, my_share=.5,
            my_initiation=.5, their_latency=20000, my_latency=19000, my_hedging=.07,
            depth=.95, plan_rate=.15, followthrough=.6),
    Persona("p_leo", "Leo Tran", "+14155550155", "plans rarely get a date",
            rate=2.6, trend=1.0, their_warmth=.65, my_warmth=.6, my_share=.5,
            my_initiation=.45, their_latency=2400, my_latency=1500, my_hedging=.1,
            depth=.3, plan_rate=.6, followthrough=.12),
    Persona("p_amara", "Amara Ellis", "+14155550136", "warm, steady volume",
            rate=2.4, trend=1.05, their_warmth=.78, my_warmth=.76, my_share=.5,
            my_initiation=.5, their_latency=1600, my_latency=1500, my_hedging=.08,
            depth=.55, plan_rate=.25, followthrough=.7),
    Persona("p_chris", "Chris Vandermeer", "+14155550109", "work register, hedged",
            rate=2.0, trend=1.0, their_warmth=.3, my_warmth=.3, my_share=.5,
            my_initiation=.5, their_latency=2000, my_latency=1800, my_hedging=.35,
            depth=.25, plan_rate=.15, followthrough=.5),
    Persona("p_hana", "Hana Kobayashi", "+14155550183", "stops mid-window",
            rate=2.8, trend=1.0, their_warmth=.7, my_warmth=.7, my_share=.5,
            my_initiation=.5, their_latency=1400, my_latency=1300, my_hedging=.1,
            depth=.5, plan_rate=.2, followthrough=.6, dormant_after=16),
    Persona("p_rafa", "Rafa Duarte", "+14155550147", "high volume, balanced",
            rate=3.4, trend=1.0, their_warmth=.72, my_warmth=.74, my_share=.5,
            my_initiation=.5, their_latency=700, my_latency=750, my_hedging=.05,
            depth=.35, plan_rate=.25, followthrough=.65),
    Persona("p_iris", "Iris Solomon", "+14155550122", "warm, low volume",
            rate=1.6, trend=1.0, their_warmth=.88, my_warmth=.7, my_share=.45,
            my_initiation=.35, their_latency=3000, my_latency=5200, my_hedging=.15,
            depth=.6, plan_rate=.18, followthrough=.7),
    Persona("p_owen", "Owen Pike", "+14155550198", "very light contact",
            rate=0.5, trend=1.0, their_warmth=.4, my_warmth=.4, my_share=.5,
            my_initiation=.5, their_latency=6000, my_latency=5000, my_hedging=.2,
            depth=.2, plan_rate=.2, followthrough=.4),
]

GROUPS = [
    ("g_dinner", "Dinner Club", ["p_priya", "p_amara", "p_rafa"], 2.2, .55, .8, .45),
    ("g_cabin", "Cabin Trip", ["p_sofia", "p_leo", "p_marcus", "p_nadia"], 1.5, .7, .45, .3),
    ("g_climb", "Climbing Crew", ["p_theo", "p_leo", "p_rafa"], 2.0, .35, .6, .5),
    ("g_fam", "Family", ["p_iris", "p_owen"], 1.4, .3, .85, .5),
]

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

OPENERS = ["hey", "yo", "hi", "hey hey", "morning", "ok so", "quick one", "random but"]
CHECKINS = [
    "how are you doing", "how's your week going", "how'd that thing go",
    "how was the weekend", "you good", "what's new with you",
    "did you survive that deadline", "how's the new place treating you",
]
BANTER = [
    "that is genuinely unhinged", "no way", "stop", "this is why we're friends",
    "I cannot believe that happened", "you're kidding", "absolutely not",
    "I'm crying", "screaming", "the audacity", "incredible scenes",
    "I would not have survived that", "that's so you",
]
WARM = [
    "miss you", "love you", "thinking about you today", "so proud of you",
    "you're the best", "genuinely so glad you're in my life",
    "this made my whole day", "I'm always in your corner",
    "you handled that so well", "I mean it, you did great",
]
QUESTIONS = [
    "tell me everything", "wait what happened", "how are you feeling about it",
    "what did you end up deciding", "do you want to talk it through",
    "how's your mom doing", "did you hear back", "are you okay though",
    "what's the plan there",
]
DEEP = [
    "I've been thinking a lot about whether I'm actually happy in this job or just comfortable, and I can't tell the difference anymore",
    "there's this thing I've been sitting with for weeks and I think I only know how to say it to you",
    "I keep noticing I'm the one holding a lot of my friendships together and I don't know what to do with that feeling",
    "honestly the last few months have been harder than I've let on to most people",
    "I think I've been confusing being needed with being close, and it's making me tired",
    "it took me a long time to realize I was performing being fine",
]
FLAT = [
    "ok", "sounds good", "cool", "got it", "sure", "np", "yep", "k", "will do",
    "makes sense", "fine by me", "yeah",
]
LOGISTICS = [
    "did the package come", "trash goes out tonight", "can you grab milk",
    "the wifi's down again", "I'll be back around 8", "rent's due friday",
    "landlord is coming tuesday", "I paid the electric", "someone left the door unlocked",
    "I'm running late", "can you feed the cat",
]
WORK = [
    "did you see the deck", "pushing that to next sprint", "standup moved to 10",
    "can you review the doc", "I'll take the action item", "that meeting could've been an email",
    "any word from the client", "numbers look off in the tab",
]
HEDGES = [
    "sorry to bother you but", "no worries at all if not, but", "totally fine either way,",
    "just wondering,", "hope this isn't annoying but", "sorry, one more thing --",
    "feel free to ignore this,", "not a big deal at all but", "sorry for the double text,",
    "hope that's ok,", "don't want to be a pain but", "apologies if I'm being weird about this,",
]
CLOSERS = ["ok gotta run", "talk soon", "ttyl", "ok bye", "alright later", "ok signing off"]
AFFECT = ["haha", "hahaha", "lol", "lmao", "!!", " :)", " <3", " 🙂", " ❤️", " 😂", " 🥺"]

ACTIVITIES = [
    "dinner", "drinks", "coffee", "brunch", "that hike", "the climbing gym",
    "a movie", "the farmers market", "trivia night", "the pottery class",
    "that new ramen place", "a picnic", "the museum thing", "karaoke",
    "the show at the Fillmore", "a bike ride", "board game night",
]
DAYS = ["friday", "saturday", "sunday", "next thursday", "tuesday", "next weekend",
        "the 14th", "this weekend", "monday night", "next friday", "wednesday"]
TIMES = ["7pm", "8pm", "6:30", "noon", "1pm", "9am", "7:30pm"]
PLACES = ["that place on Valencia", "my place", "the usual spot", "downtown",
          "the park entrance", "yours", "the spot by the bridge"]

FLOAT = ["we should do {a} sometime", "we're overdue for {a}", "ok when are we doing {a}",
         "I want to do {a} with you soon", "been thinking we should get {a}"]
PROPOSE = ["are you free {d}", "how about {d}", "does {d} work", "{d}?",
           "thinking {d} if you're around", "{d} any good for you"]
LOCK = ["{t} at {p}?", "let's say {t}", "{t} works, {p}?", "ok {t} then"]
YES = ["I'm in", "yes please", "down", "that works", "perfect", "yep let's do it",
       "count me in", "book it", "yes finally", "sounds great"]
MAYBE = ["might work, let me check", "tentatively yes", "I'll try", "let me see how the week goes",
         "probably? I'll confirm"]
NO = ["ah I can't that day", "rain check?", "something came up", "I have to bail sorry",
      "can we push it", "I'm out of town then"]
CONFIRM = ["see you {d}", "locked in", "confirmed", "it's in the calendar", "see you then"]


class SyntheticSource:
    name = "synthetic"

    def __init__(self, seed: int = SEED, weeks: int = WEEKS) -> None:
        self.rng = random.Random(seed)
        self.weeks = weeks
        self._n = 0

    # -- helpers ----------------------------------------------------------
    def _mid(self) -> str:
        self._n += 1
        return f"s{self._n:06d}"

    def _pick(self, seq):
        return self.rng.choice(seq)

    def _affect(self, warmth: float, text: str) -> str:
        if self.rng.random() < warmth * 0.55:
            text = f"{text.rstrip()} {self._pick(AFFECT).strip()}"
        if self.rng.random() < warmth * 0.25:
            text = text.replace(".", "!")
        return text.strip()

    def _my_line(self, p: Persona, body: str) -> str:
        """Apply my own voice toward this person, including how much I soften."""
        if self.rng.random() < p.my_hedging:
            body = f"{self._pick(HEDGES)} {body}"
        body = self._affect(p.my_warmth, body)
        if self.rng.random() < p.my_hedging * 0.4:
            body = f"{body} {self.rng.choice(['sorry!', 'anyway no pressure', 'ignore me if busy',
                                              'no rush at all', "sorry, I'll stop"])}"
        return body

    def _their_line(self, p: Persona, body: str) -> str:
        return self._affect(p.their_warmth, body)

    def _content(self, p: Persona, mine: bool, group: bool) -> str:
        r = self.rng.random()
        # Continuous in depth rather than gated on a threshold: a somewhat-close
        # friend should get occasional long messages, not none at all.
        if not group and r < (p.depth ** 2) * 0.2:
            return self._pick(DEEP)
        if p.label == "practical, low warmth" and r < 0.6:
            return self._pick(LOGISTICS)
        if p.label == "work register, hedged" and r < 0.55:
            return self._pick(WORK)
        warmth = p.my_warmth if mine else p.their_warmth
        if r < 0.18 + warmth * 0.22:
            return self._pick(WARM if warmth > 0.6 else FLAT)
        if r < 0.5:
            return self._pick(BANTER if warmth > 0.45 else FLAT)
        if r < 0.72:
            return self._pick(QUESTIONS if warmth > 0.4 else FLAT)
        return self._pick(CHECKINS)

    # -- plan scripting ---------------------------------------------------
    def _plan_script(self, p: Persona, group: bool) -> list[tuple[bool, str]]:
        """Return (is_mine, text) turns for one plan negotiation."""
        a, d, t, pl = (self._pick(ACTIVITIES), self._pick(DAYS),
                       self._pick(TIMES), self._pick(PLACES))
        starter = self.rng.random() < p.my_initiation
        turns: list[tuple[bool, str]] = [(starter, self._pick(FLOAT).format(a=a))]
        turns.append((not starter, self._pick(YES if self.rng.random() < .8 else MAYBE)))

        if self.rng.random() < 0.75:                       # gets a proposed time
            turns.append((starter, self._pick(PROPOSE).format(d=d)))
            landed = self.rng.random() < p.followthrough
            if landed:
                turns.append((not starter, self._pick(YES)))
                turns.append((starter, self._pick(LOCK).format(t=t, p=pl)))
                turns.append((not starter, self._pick(CONFIRM).format(d=d)))
            else:
                turns.append((not starter, self._pick(NO if self.rng.random() < .6 else MAYBE)))
                if self.rng.random() < 0.4:
                    turns.append((starter, self._pick(["ok let's find another time",
                                                       "no worries, next time",
                                                       "we'll figure it out"])))
        return turns

    # -- burst ------------------------------------------------------------
    def _burst(self, p: Persona, thread: Thread, start: datetime,
               out: list[Message], group_members: list[Persona] | None = None) -> None:
        group = group_members is not None
        turns: list[tuple[bool, str]] = []

        if self.rng.random() < p.plan_rate:
            turns = self._plan_script(p, group)
        else:
            n = self.rng.randint(3, round(6 + p.depth * 8))
            mine = self.rng.random() < p.my_initiation
            if self.rng.random() < .6:
                turns.append((mine, f"{self._pick(OPENERS)}, {self._content(p, mine, group)}"))
                n -= 1
            for _ in range(max(1, n)):
                turns.append((mine, self._content(p, mine, group)))
                # stickiness: sometimes the same person sends twice
                if self.rng.random() > (0.22 if mine else 0.28):
                    mine = not mine
                # bias the mix toward the configured share
                if self.rng.random() < abs(p.my_share - .5) * 2:
                    mine = p.my_share > .5
            if self.rng.random() < .35:
                turns.append((mine, self._pick(CLOSERS)))

        ts = start
        for is_mine, body in turns:
            gap = p.my_latency if is_mine else p.their_latency
            ts += timedelta(seconds=max(20, self.rng.lognormvariate(math.log(gap), 0.85)))
            text = self._my_line(p, body) if is_mine else self._their_line(p, body)
            sender = SELF_ID if is_mine else (
                self._pick(group_members).id if group else p.id)
            if group and is_mine:
                sender = SELF_ID
            reacts: tuple[str, ...] = ()
            warmth = p.their_warmth if is_mine else p.my_warmth
            if self.rng.random() < warmth * 0.16:
                reacts = (self._pick(["love", "laugh", "like", "emphasize"]),)
            out.append(Message(id=self._mid(), thread_id=thread.id, sender_id=sender,
                               ts=ts, text=text, is_from_me=is_mine, reactions=reacts))

    # -- main -------------------------------------------------------------
    def load(self) -> Corpus:
        rng = self.rng
        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        start = now - timedelta(weeks=self.weeks)

        contacts = {p.id: Contact(id=p.id, name=p.name, handle=p.handle,
                                  tags=(p.label,)) for p in PERSONAS}
        by_id = {p.id: p for p in PERSONAS}

        threads: dict[str, Thread] = {}
        for p in PERSONAS:
            tid = f"t_{p.id}"
            threads[tid] = Thread(id=tid, name=p.name, participant_ids=(p.id,), is_group=False)
        for gid, gname, members, rate, warmth, follow, plan in GROUPS:
            threads[gid] = Thread(id=gid, name=gname,
                                  participant_ids=tuple(members), is_group=True)

        messages: list[Message] = []

        for week in range(self.weeks):
            week_start = start + timedelta(weeks=week)
            progress = week / max(1, self.weeks - 1)

            for p in PERSONAS:
                if p.dormant_after is not None and week > p.dormant_after:
                    continue
                if p.starts_at_week is not None and week < p.starts_at_week:
                    continue          # you had not met yet
                # Geometric interpolation toward the trend multiplier, measured
                # across the span this person was actually around for.
                span_start = p.starts_at_week or 0
                span = max(1, (self.weeks - 1) - span_start)
                local = (week - span_start) / span
                rate = p.rate * (p.trend ** local)
                bursts = max(0, int(rng.gauss(rate, max(.4, rate * .35))))
                thread = threads[f"t_{p.id}"]
                for _ in range(bursts):
                    day = rng.randrange(7)
                    late = rng.random() < p.depth * .35
                    hour = rng.randrange(22, 26) % 24 if late else rng.randrange(8, 22)
                    when = week_start + timedelta(days=day, hours=hour,
                                                  minutes=rng.randrange(60))
                    if when > now:
                        continue
                    self._burst(p, thread, when, messages)

            for gid, gname, members, rate, warmth, follow, plan in GROUPS:
                bursts = max(0, int(rng.gauss(rate, rate * .4)))
                mem = [by_id[m] for m in members]
                proxy = Persona(id=gid, name=gname, handle="", label="group",
                                rate=rate, trend=1.0, their_warmth=warmth, my_warmth=warmth,
                                my_share=.35, my_initiation=.35, their_latency=1500,
                                my_latency=1800, my_hedging=.08, depth=.3,
                                plan_rate=plan, followthrough=follow)
                for _ in range(bursts):
                    when = week_start + timedelta(days=rng.randrange(7),
                                                  hours=rng.randrange(9, 23),
                                                  minutes=rng.randrange(60))
                    if when > now:
                        continue
                    self._burst(proxy, threads[gid], when, messages, group_members=mem)

        messages.sort(key=lambda m: m.ts)
        return Corpus(contacts=contacts, threads=threads, messages=messages,
                      source_name="synthetic")


