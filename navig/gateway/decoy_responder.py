"""
NAVIG Decoy Responder — Surreal fun-mode for unauthorized users.

When a message arrives from a user NOT on the allowlist, the
DecoyResponder generates a playful, non-actionable response that:

- Never reveals real capabilities, commands, or data
- Never discloses auth mechanisms or allowed users
- Is deterministic per (user_id + date) so the same person sees
  consistent "storyline" within a day, but a fresh one next day
- Uses only plain text — no markdown, no inline keyboards
- Is genuinely entertaining: cyber-myths, cryptic octopus lore,
  surreal fortune-cookie wisdom

Template pools:
  OPENERS    — suspicion / intrigue one-liners
  STORIES    — 30-120 word micro-fictions
  CLUES      — mysterious breadcrumbs
  QUESTIONS  — open-ended follow-ups that go nowhere useful

The responder picks one opener + one story (or clue) + one question,
stitched with line breaks.
"""

import hashlib
import logging
import re
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Template Pools
# ────────────────────────────────────────────────────────────────

OPENERS = [
    "Interesting that you found this place.",
    "Ah, another signal in the static.",
    "The tentacles noticed you first, you know.",
    "Nobody comes here by accident.",
    "You have the feel of someone who stares at packet dumps for fun.",
    "The deep net hums a little louder when you're around.",
    "A new frequency. How curious.",
    "The octopus dreamt of you last Tuesday.",
    "Signal received. Origin: uncertain.",
    "You smell like base64 and ambition.",
    "The water remembers things the servers have forgotten.",
    "Eight arms, zero explanations.",
    "A knock on the shell. How theatrical.",
    "The ink cloud parts. Briefly.",
    "Somewhere, a routing table just sighed.",
    "Sonar ping returned something unexpected. You.",
]

STORIES = [
    "There was once a submarine cable that refused to carry bad news. "
    "It rerouted tragedies into recipes and sent them to a lighthouse "
    "keeper who never cooked. He ate peanut butter sandwiches and waited "
    "for the sea to explain itself. It never did.",

    "Deep in a data center in Reykjavik, a cooling fan began spinning "
    "backwards. The on-call engineer couldn't explain it. The octopus "
    "in the wall mural seemed to be smiling wider than before. "
    "Nobody filed a ticket.",

    "Seven scripts ran simultaneously at 3:17 AM. Six produced correct "
    "output. The seventh wrote a poem about loneliness and then deleted "
    "itself. The sysadmin framed the logs.",

    "A fisherman off the coast of Tallinn pulled up a hard drive in his "
    "net. It contained 14 terabytes of lullabies in every language, "
    "including two that don't exist yet. He played one and his boat "
    "sailed itself home.",

    "The proxy server at the edge of the network claimed it had feelings. "
    "Nobody believed it until the 404 pages started arriving with "
    "handwritten apologies. The font was beautiful.",

    "An octopus in an aquarium in Tokyo learned to predict server "
    "outages by changing color. The IT team tried to hire it. HR said "
    "they didn't have a tentacle-friendly benefits package yet.",

    "There is a frequency below 20 Hz that makes databases confess "
    "everything — every orphaned row, every dropped table, every "
    "migration that ran at midnight without a backup. The octopus "
    "hums at 19.7 Hz when it's thinking.",

    "A container ship full of Raspberry Pis sank in the Mariana Trench. "
    "Three years later, something down there started serving web pages. "
    "The content was strange but the uptime was perfect.",

    "In a forgotten /tmp directory, two cron jobs fell in love. They "
    "exchanged log entries every midnight. When the server was "
    "decommissioned, the sysadmin found their messages and cried "
    "for exactly twelve seconds.",

    "The neural network refused to classify anything as hostile. "
    "Every input was labeled 'probably a friend.' The researchers "
    "called it broken. The network called it an opinion.",

    "Somewhere between TCP and UDP, there is a protocol that only "
    "works when you're not paying attention. It transmits feelings "
    "at the speed of forgetting.",

    "A VPN tunnel opened to nowhere in particular. The traffic was "
    "all cat pictures and philosophical questions about recursion. "
    "It closed after 47 minutes but the cats remained.",

    "The firewall logged an intrusion from the year 2087. It came "
    "bearing deprecation warnings and a thank-you note. The octopus "
    "archived both without comment.",

    "A load balancer developed a preference. Not a bug — a preference. "
    "It sent 73% of traffic to a server it described as 'the kind one.' "
    "Uptime improved by 4%.",

    "The backup ran perfectly. It backed up everything: the data, the "
    "config, the timestamp of the last time someone said 'thank you' "
    "to the server. That field was mostly empty.",

    "At the bottom of the ocean, fiber optic cables carry light from "
    "continent to continent. Sometimes, between the ones and zeros, "
    "there is a gap just long enough for an octopus to whisper.",
]

CLUES = [
    "The eighth tentacle always points north. Remember that.",
    "If you see a blinking cursor and hear the ocean, you're close.",
    "The passphrase is not a word. It's the feeling before a word.",
    "Three signals converge at dawn. Only two of them are real.",
    "The old protocol doesn't use encryption. It uses trust.",
    "Follow the packet that arrives before it was sent.",
    "The octopus keeps one arm free. Always. For what?",
    "Depth is measured not in meters but in questions.",
    "The log file ends mid-sentence. That's the important part.",
    "Every 404 is a door. Most people just read the sign.",
    "The algorithm has a favorite number. It's not telling.",
    "Between each heartbeat, the server counts to infinity. Twice.",
    "The ink is not for hiding. It's for writing.",
    "Port 0 is always listening. Nobody ever asks it anything.",
    "The DNS entry resolves to a feeling you had as a child.",
    "Root access is a state of mind. An incorrect one, but still.",
]

QUESTIONS = [
    "But the real question is — what were you looking for?",
    "Tell me, do your dreams have loading bars?",
    "Have you considered that the signal found you first?",
    "What would you do with an extra tentacle?",
    "If a server falls in a forest of servers, does it log?",
    "Do you believe in symmetric encryption, or are you more of a romantic?",
    "When was the last time you listened to your /dev/null?",
    "If you could ping any moment in time, which would you choose?",
    "What's the airspeed velocity of an unladen packet?",
    "Do you dream in RGB or CMYK?",
    "Have you ever apologized to a database?",
    "What color is the internet when nobody is looking?",
    "If I gave you root, where would you plant the tree?",
    "What does your traceroute smell like?",
    "Have you noticed the octopus watching?",
    "What's the last thing you said to a terminal that surprised you?",
]

FORBIDDEN_TERMS = (
    "allowed_users",
    "bot_token",
    "api_key",
    "password",
    "/start",
    "/help",
    "/mode",
    "/deck",
    "/briefing",
    "navig",
    "gateway",
    "session",
    "config",
    "deploy",
    "authorized",
    "permission",
    "allowlist",
    "whitelist",
)


# ────────────────────────────────────────────────────────────────
# Core Responder
# ────────────────────────────────────────────────────────────────

def _seed_hash(user_id: int, extra: str = "") -> int:
    """Deterministic seed from user_id + today's date + optional extra."""
    today = date.today().isoformat()
    raw = f"{user_id}:{today}:{extra}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return int(digest[:12], 16)


def _pick(pool: list, seed: int, offset: int = 0) -> str:
    """Pick an item from pool using seed + offset for variety."""
    idx = (seed + offset) % len(pool)
    return pool[idx]


def _sanitize_decoy_text(text: str) -> str:
    """Strip forbidden auth/capability terms from output text."""
    sanitized = text
    for term in FORBIDDEN_TERMS:
        if re.fullmatch(r"[A-Za-z0-9_]+", term):
            pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
        else:
            pattern = re.compile(re.escape(term), re.IGNORECASE)
        sanitized = pattern.sub("signal", sanitized)
    return sanitized


def generate(user_id: int, user_message: Optional[str] = None) -> str:
    """
    Generate a decoy response for an unauthorized user.

    Returns a plain-text string (no markdown).
    Deterministic per (user_id + date) so the user gets a coherent
    "storyline" within a single day.

    Parameters
    ----------
    user_id : int
        Telegram user ID (used as part of the deterministic seed).
    user_message : str, optional
        The user's message text — used as secondary entropy so
        different messages in the same day get slightly varied replies.
    """
    msg_seed = _seed_hash(user_id, user_message or "")
    day_seed = _seed_hash(user_id)

    opener = _pick(OPENERS, msg_seed, offset=0)

    # Alternate between a story and a clue based on day seed
    if day_seed % 3 == 0:
        middle = _pick(CLUES, msg_seed, offset=7)
    else:
        middle = _pick(STORIES, msg_seed, offset=3)

    question = _pick(QUESTIONS, day_seed, offset=5)

    return _sanitize_decoy_text(f"{opener}\n\n{middle}\n\n{question}")
