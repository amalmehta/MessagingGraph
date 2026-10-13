"""Telling my own words apart from text I pasted in.

People paste things into a chat all the time -- a confirmation email, a notice,
a screenshot's text. iMessage records every one of them as sent by me, and the
app read them as mine. On this Mac one of them was an appraisal notice opening
"Hi Amal. Thank you for submitting your vehicle\'s details", which produced a
thank-you note to a friend saying "still really grateful for you submitting your
vehicle\'s details".

Length is no help -- people write long messages. The giveaway is that the text
greets *me*: nobody opens a message to a friend by saying hello to themselves.
"""
from __future__ import annotations

import re
from collections import Counter

from ..models import Corpus

SALUTATION = re.compile(
    r"^\s*(?:hi|hey|hello|dear|good (?:morning|afternoon|evening))[\s,]+([a-z][a-z\']{1,14})\b",
    re.I)
# Words that follow "hey" often enough to look like a name without being one.
NOT_A_NAME = {"man", "bro", "dude", "there", "all", "guys", "you", "yall", "y'all",
              "this", "that", "what", "can", "is", "are", "do", "so", "just", "i",
              "it", "how", "we", "they", "the", "and", "but", "mate", "buddy",
              "love", "babe", "sis", "bruh", "fam", "team", "everyone", "friend"}
MIN_GREETINGS = 8          # enough times to be a name and not a coincidence
CLEAR_OF_RUNNER_UP = 2.5


def my_name(corpus: Corpus) -> str | None:
    """What other people call me, learned from how they open messages to me.

    Only accepted when one name is well ahead of the runner-up, so the filler
    words people put after "hey" never win it.
    """
    greeted: Counter[str] = Counter()
    for thread in corpus.threads.values():
        for m in corpus.messages_in(thread.id):
            if m.is_from_me or not m.text:
                continue
            hit = SALUTATION.match(m.text)
            if hit:
                word = hit.group(1).lower()
                if word not in NOT_A_NAME:
                    greeted[word] += 1
    if not greeted:
        return None
    (name, n), *rest = greeted.most_common()
    runner_up = rest[0][1] if rest else 0
    if n < MIN_GREETINGS or n < runner_up * CLEAR_OF_RUNNER_UP:
        return None
    return name


def is_quoted(text: str, me: str | None) -> bool:
    """True when a message I supposedly sent greets me by name, so I did not
    write it -- I pasted it."""
    if not me or not text:
        return False
    hit = SALUTATION.match(text)
    return bool(hit and hit.group(1).lower() == me)
