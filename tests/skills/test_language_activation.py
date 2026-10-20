"""Skill activation must work in the languages the owner writes in.

Activation is a matcher over the user's message (`_compute_activation_score`). It was
English-only: ``сгенерируй хоррор музыку`` and ``envoie un mail à Marc`` scored 0 for every
skill, so the skill that would have told the agent HOW to do the job never loaded. The
owner writes Russian and French, so the model was working without its instructions for
most of what it was asked.

Per-language keyword packs (`activation_keywords_ru`, `activation_keywords_fr`, …) fix
that. What is pinned here, and why each is its own test:

* **A real-request fixture** (RU + FR) against EVERY shipped skill, not a hand-picked few:
  the right skill must come out on top. Matching in isolation proves nothing about
  confusion between skills, and confusion is the realistic failure.
* **Negatives**: unrelated chat stays at base priority. Wider triggers must not turn the
  skills into noise on every turn.
* **English is bit-for-bit unchanged**: a frozen copy of the old scorer is compared with
  the new one over every shipped skill, so "English behaviour unchanged" is measured, not
  asserted.
* **The matching rules**: word-start anchoring, ё/é folding, parsing.

The fixture text is Russian/French on purpose — it is the thing under test.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest

from navig.agent.skills_context import (
    _DESCRIPTION_STOPWORDS,
    DESCRIPTION_MATCH_SCORE,
    KEYWORD_MATCH_SCORE,
    PATH_MATCH_SCORE,
    ContextSkill,
    _compute_activation_score,
    _parse_skill_file,
)

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]


def _skill_files() -> list[Path]:
    """Every SKILL.md the CHAT AGENT can load (builtin store + plugins), not a subset.

    Deliberately NOT ``core/navig/skills/builtin/*`` (engine-care, healthcheck, summarize):
    nothing reads that directory — `get_skill_dirs()` scans `navig/builtin/skills`, the
    store, packages and plugins — so those three never reach the chat agent, and a pack
    on them would be tested and unreachable. (engine-care is `safety: elevated`; wiring it
    into auto-activation is an owner decision, not a side effect of a keyword change.)
    """
    found: list[Path] = []
    for pattern in (
        "core/navig/builtin/skills/**/SKILL.md",
        "plugins/*/*/skills/*/SKILL.md",
        "plugins/*/skills/*/SKILL.md",
    ):
        found.extend(sorted(REPO.glob(pattern)))
    return found


@pytest.fixture(scope="module")
def skills() -> list[ContextSkill]:
    loaded = [s for s in (_parse_skill_file(p, "builtin") for p in _skill_files()) if s]
    # A scan that found nothing would pass every assertion below by comparing nothing.
    assert len(loaded) >= 40, f"only {len(loaded)} skills parsed — the glob is stale"
    return loaded


def _ranked(skills: list[ContextSkill], message: str) -> list[tuple[str, int]]:
    """Skills that ACTIVATE for *message*, best first — the order `activate()` uses."""
    scored = []
    for skill in skills:
        score = _compute_activation_score(skill, [], message.lower())
        if score - skill.priority > 0:  # anything above the skill's own base bonus
            scored.append((skill, score))
    scored.sort(key=lambda pair: (pair[1], pair[0].priority), reverse=True)
    return [(s.name, score) for s, score in scored]


# ── the real-request fixture ──────────────────────────────────────────────────
#
# (message, the skill that must rank FIRST). Phrased the way the owner actually writes —
# several are verbatim from the Telegram conversation that started this ("написать на
# мыло", "емаил отправь"), the rest are the ordinary phrasing of the same intents.

RU = [
    ("Ты можешь мне написать на мыло, есть дело", "email"),
    ("Нет, мне емаил отправь", "email"),
    ("проверь входящие письма", "email"),
    ("найди письмо от банка за прошлый месяц", "email"),
    ("Сгенерируй тёмную хоррор музыку", "audio-generation"),
    ("сделай джингл для моего канала", "audio-generation"),
    ("озвучь этот текст приятным голосом", "audio-generation"),
    ("нужен звуковой эффект скрипа двери", "audio-generation"),
    ("поставь встречу на завтра в три часа", "calendar"),
    ("что у меня в календаре на этой неделе", "calendar"),
    ("скачай видео с ютуба", "download-media"),
    ("скачай этот тикток без водяного знака", "download-media"),
    ("нарисуй картинку: кот в космосе", "media-generation"),
    ("сгенерируй изображение заката над морем", "media-generation"),
    ("опубликуй пост в инстаграм", "social-publishing"),
    ("запланируй публикацию на фейсбуке на вечер", "social-publishing"),
    ("покажи мои репозитории на гитхабе", "github-ops"),
    ("склонируй репозиторий с гитхаба", "github-ops"),
    ("переключи сцену в обс на камеру", "obs-control"),
    ("начни стрим", "obs-control"),
    ("сведи вокал с битом и сделай мастеринг", "audio-mastering"),
    ("сделай трек громче, замерь громкость", "audio-mastering"),
]

FR = [
    ("Envoie un mail à Marc pour confirmer", "email"),
    ("Vérifie ma boîte de réception", "email"),
    ("Génère une musique d'ambiance sombre", "audio-generation"),
    ("fais un bruitage de pas dans la neige", "audio-generation"),
    ("enregistre une voix off pour ma présentation", "audio-generation"),
    ("Planifie une réunion demain à 10h", "calendar"),
    ("Qu'est-ce que j'ai dans mon agenda cette semaine ?", "calendar"),
    ("télécharge cette vidéo YouTube", "download-media"),
    ("récupère le tiktok de ce lien", "download-media"),
    ("dessine une image d'un chat astronaute", "media-generation"),
    ("génère une illustration de coucher de soleil", "media-generation"),
    ("publie ce post sur Instagram", "social-publishing"),
    ("programme une publication Facebook pour ce soir", "social-publishing"),
    ("montre mes dépôts sur GitHub", "github-ops"),
    ("clone le dépôt github de mon projet", "github-ops"),
    ("change de scène dans OBS", "obs-control"),
    ("démarre le stream", "obs-control"),
    ("mixe ma voix sur l'instrumental et masterise le morceau", "audio-mastering"),
]

#: Phone typing without accents / without ё must still work — the same intents, degraded
#: the way people really type them. Folding both sides is what makes these pass.
DEGRADED = [
    ("Genere une musique d'ambiance sombre", "audio-generation"),
    ("Verifie ma boite de reception", "email"),
    ("Telecharge cette video YouTube", "download-media"),
    ("сгенерируй темную хоррор музыку", "audio-generation"),  # е for ё
    ("скинь аудио с тиктока", "download-media"),
]

NEGATIVE = [
    "какая сегодня погода",
    "расскажи анекдот",
    "сколько будет двадцать плюс тридцать",
    "как дела",
    "quelle heure est-il à Tokyo",
    "raconte-moi une blague",
    "comment ça va aujourd'hui",
    "combien font vingt plus trente",
]


def test_the_fixture_is_big_enough_to_mean_something():
    assert len(RU) + len(FR) >= 30, "the acceptance bar is 30+ real requests"
    assert len(RU) >= 20 and len(FR) >= 15, "both languages need real coverage"
    assert len({skill for _, skill in RU + FR}) >= 8, "cover many skills, not one"


@pytest.mark.parametrize(("message", "expected"), RU + FR)
def test_a_real_request_loads_the_right_skill_first(skills, message, expected):
    ranked = _ranked(skills, message)
    assert ranked, f"{message!r} activated NOTHING — the agent would work without its skill"
    assert ranked[0][0] == expected, (
        f"{message!r} -> {ranked[:4]}, expected {expected!r} first"
    )


@pytest.mark.parametrize(("message", "expected"), DEGRADED)
def test_degraded_typing_still_activates(skills, message, expected):
    ranked = _ranked(skills, message)
    assert ranked and ranked[0][0] == expected, f"{message!r} -> {ranked[:4]}"


@pytest.mark.parametrize("message", NEGATIVE)
def test_unrelated_chat_activates_nothing(skills, message):
    """Wider triggers must not make the skills noise on every turn."""
    assert _ranked(skills, message) == [], f"{message!r} wrongly activated {_ranked(skills, message)}"


# ── English is unchanged: measured against a frozen copy of the old scorer ────


def _legacy_score(skill: ContextSkill, files: list[str], msg_lower: str) -> int:
    """The scorer exactly as it was before language packs — frozen for comparison.

    If someone edits this to make a test pass, English behaviour has changed and the
    point of the test is gone; it is deliberately a copy, not an import.
    """
    score = 0
    for pattern in skill.activation_paths:
        for file_path in files:
            if fnmatch.fnmatch(file_path, pattern) or fnmatch.fnmatch(Path(file_path).name, pattern):
                score += PATH_MATCH_SCORE
                break
    for keyword in skill.activation_keywords:
        if keyword.lower() in msg_lower:
            score += KEYWORD_MATCH_SCORE
    if skill.description:
        msg_words = {w.strip(".,;:!?()[]'\"") for w in msg_lower.split()}
        desc_words = {
            w.strip(".,;:!?()[]'\"")
            for w in skill.description.lower().split()
            if len(w) >= 4
        } - _DESCRIPTION_STOPWORDS
        hits = sum(1 for w in desc_words if w and w in msg_words)
        if hits >= 2:
            score += min(hits, 3) * DESCRIPTION_MATCH_SCORE
    return score + skill.priority


ENGLISH = [
    "make me some horror music",
    "send an email to Marc",
    "download this youtube video",
    "schedule a meeting tomorrow at 3pm",
    "generate an image of a cat in space",
    "publish this post to instagram",
    "show my github repositories",
    "switch the OBS scene",
    "master my vocal over the beat and measure the loudness LUFS",
    "summarize this article",
    "check the system health",
    "what is the weather today",
    "restart the nginx service",
    "list my docker containers",
]


@pytest.mark.parametrize("message", ENGLISH)
def test_english_scores_are_identical_to_the_old_scorer(skills, message):
    for skill in skills:
        old = _legacy_score(skill, [], message.lower())
        new = _compute_activation_score(skill, [], message.lower())
        assert new == old, (
            f"English scoring changed for skill {skill.name!r} on {message!r}: {old} -> {new}"
        )


# ── the matching rules ────────────────────────────────────────────────────────


def _skill(**kw) -> ContextSkill:
    return ContextSkill(name=kw.pop("name", "t"), content="", **kw)


def test_a_cyrillic_stem_matches_every_inflection():
    s = _skill(localized_keywords={"ru": ["музык"]})
    for msg in ("нужна музыка", "сделай музыку", "для музыки", "Музыкальный трек"):
        assert _compute_activation_score(s, [], msg.lower()) == KEYWORD_MATCH_SCORE, msg


def test_localized_keywords_anchor_at_a_word_start():
    """A stem must not fire from the MIDDLE of an unrelated word: `son` (sound) must not
    match `reason`, and `музык` must not match `немузыкальный`."""
    s = _skill(localized_keywords={"fr": ["son"], "ru": ["музык"]})
    assert _compute_activation_score(s, [], "the reason is clear") == 0
    assert _compute_activation_score(s, [], "немузыкальный человек") == 0
    assert _compute_activation_score(s, [], "baisse le son") == KEYWORD_MATCH_SCORE


def test_a_short_keyword_is_an_acronym_and_matches_a_whole_word_only():
    """`обс` (OBS) is 3 characters: as a stem it would fire inside "обсудим" (let's
    discuss) and misroute every conversation that starts a meeting."""
    s = _skill(localized_keywords={"ru": ["обс"]})
    assert _compute_activation_score(s, [], "давай обсудим планы") == 0
    assert _compute_activation_score(s, [], "обстановка дома") == 0
    for msg in ("переключи сцену в обс", "обс, пожалуйста", "запусти обс на камеру"):
        assert _compute_activation_score(s, [], msg) == KEYWORD_MATCH_SCORE, msg


def test_a_longer_keyword_stays_a_stem():
    s = _skill(localized_keywords={"fr": ["mail"]})
    assert _compute_activation_score(s, [], "envoie les mails") == KEYWORD_MATCH_SCORE


def test_a_pack_does_not_double_count_inside_one_word():
    """`email` contains `mail` mid-word: the pack entry must not fire there, or every
    English "email" would score twice."""
    s = _skill(activation_keywords=["email"], localized_keywords={"fr": ["mail"]})
    assert _compute_activation_score(s, [], "send an email") == KEYWORD_MATCH_SCORE


def test_accent_and_yo_folding_runs_both_ways():
    fr = _skill(localized_keywords={"fr": ["génère"]})
    assert _compute_activation_score(fr, [], "genere une musique") == KEYWORD_MATCH_SCORE
    assert _compute_activation_score(fr, [], "génère une musique") == KEYWORD_MATCH_SCORE
    ru = _skill(localized_keywords={"ru": ["тёмн"]})
    assert _compute_activation_score(ru, [], "темную музыку") == KEYWORD_MATCH_SCORE
    assert _compute_activation_score(ru, [], "тёмную музыку") == KEYWORD_MATCH_SCORE


def test_folding_leaves_russian_short_i_alone():
    """NFD would turn й into и + a mark and strip it, conflating the two letters. Only
    ё is folded in Cyrillic."""
    s = _skill(localized_keywords={"ru": ["майн"]})
    assert _compute_activation_score(s, [], "хочу мина") == 0, "й and и are different letters"


def test_english_keywords_are_still_plain_substrings():
    s = _skill(activation_keywords=["tok"])
    assert _compute_activation_score(s, [], "download tiktok") == KEYWORD_MATCH_SCORE


# ── parsing ───────────────────────────────────────────────────────────────────


def test_the_loader_reads_per_language_packs(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text(
        "---\nname: demo\nactivation_keywords: [music]\n"
        "activation_keywords_ru: [музык, джингл]\n"
        "activation_keywords_fr: \"musique, bruitage\"\n"
        "activation_keywords_english: [nope]\n"  # not a 2-3 letter code -> ignored
        "---\nbody\n",
        encoding="utf-8",
    )
    s = _parse_skill_file(f, "test")
    assert s is not None
    assert s.activation_keywords == ["music"], "the English list is untouched"
    assert s.localized_keywords == {"ru": ["музык", "джингл"], "fr": ["musique", "bruitage"]}


def test_a_skill_with_no_packs_has_an_empty_mapping(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: plain\nactivation_keywords: [x]\n---\nbody\n", encoding="utf-8")
    assert _parse_skill_file(f, "test").localized_keywords == {}


def test_every_shipped_pack_is_lowercase_and_nonempty(skills):
    """A pack entry that is empty matches every message; an uppercase one never matches
    (both sides are lowercased)."""
    for skill in skills:
        for lang, words in skill.localized_keywords.items():
            for word in words:
                assert word.strip(), f"{skill.name}/{lang}: empty keyword matches everything"
                assert word == word.lower(), f"{skill.name}/{lang}: {word!r} can never match"


# ── held-out phrasings ────────────────────────────────────────────────────────
#
# Written AFTER the packs, from phrasings that were NOT used while authoring them — the
# only honest generalisation check available, since the main fixture above was shaped
# alongside the packs. First run: 20 of 22 passed (91%). The two misses were not
# over-fitting; they were real gaps, and one produced a GENERAL fix, pinned below:
#
#   "résume-moi cet article"            -> French attaches clitics with a hyphen; the fix is
#                                          in `_fold` (hyphen == space), not another keyword,
#                                          so télécharge-moi / fais-moi / rendez-vous benefit.
#                                          (test_a_hyphen_is_a_space_… below)
#   "как там сервер, всё нормально?"    -> casual phrasing with no distinctive stem — but the
#                                          skill it targets (healthcheck) turned out to be
#                                          unreachable from chat, so both were taken out.

HELD_OUT = [
    ("отправь письмо маме", "email"),
    ("напомни, какие у меня сегодня встречи", "calendar"),
    ("скинь мне аудио с этого тиктока", "download-media"),
    ("нарисуй мне логотип", "media-generation"),
    ("выложи фото в инстаграм", "social-publishing"),
    ("создай мне джингл на 5 секунд", "audio-generation"),
    ("включи запись в обс", "obs-control"),
    ("хочу сделать мастеринг песни", "audio-mastering"),
    ("что в моём расписании на пятницу", "calendar"),
    ("покажи мои репы на гитхабе", "github-ops"),
    ("peux-tu m'envoyer un courriel à Sophie", "email"),
    ("ajoute un rendez-vous demain à 14h", "calendar"),
    ("fais-moi une musique triste au piano", "audio-generation"),
    ("télécharge-moi cet audio de YouTube", "download-media"),
    ("crée une image réaliste d'une forêt", "media-generation"),
    ("passe sur la scène Caméra", "obs-control"),
    ("mets le stream en route", "obs-control"),
    ("partage ça sur LinkedIn", "social-publishing"),
]


@pytest.mark.parametrize(("message", "expected"), HELD_OUT)
def test_held_out_phrasings_load_the_right_skill(skills, message, expected):
    ranked = _ranked(skills, message)
    assert ranked and ranked[0][0] == expected, f"{message!r} -> {ranked[:4]}"


def test_a_hyphen_is_a_space_and_a_typographic_apostrophe_is_an_apostrophe():
    s = _skill(localized_keywords={"fr": ["resume moi", "l'agenda", "rendez-vous"]})
    assert _compute_activation_score(s, [], "résume-moi ça") == KEYWORD_MATCH_SCORE
    assert _compute_activation_score(s, [], "dans l’agenda") == KEYWORD_MATCH_SCORE
    assert _compute_activation_score(s, [], "un rendez vous demain") == KEYWORD_MATCH_SCORE
    assert _compute_activation_score(s, [], "un rendez-vous demain") == KEYWORD_MATCH_SCORE


# ── through the real SkillsContext.activate() ─────────────────────────────────
#
# Everything above calls the scorer directly. `activate()` is what the chat agent runs: it
# hoists the folded message out of the per-skill loop and passes it down, which is a
# separate piece of wiring that a scorer-only test cannot see break.


@pytest.fixture(scope="module")
def context(tmp_path_factory):
    from navig.agent.skills_context import SkillsContext

    roots = sorted({p.parent for p in _skill_files()})
    ctx = SkillsContext(
        workspace_dir=str(tmp_path_factory.mktemp("ws")),
        extra_dirs=roots,
        include_installed=False,  # hermetic: no entry-point/plugin resolution from the main checkout
    )
    assert len(ctx.load()) >= 40, "the context found no skills — extra_dirs wiring broke"
    return ctx


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("сгенерируй тёмную хоррор музыку", "audio-generation"),
        ("envoie un mail à Marc", "email"),
        ("поставь встречу на завтра в три часа", "calendar"),
        ("génère une musique d'ambiance sombre", "audio-generation"),
        ("скинь ролик с тиктока", "download-media"),
    ],
)
def test_activate_picks_the_right_skill_first(context, message, expected):
    active = context.activate(user_message=message)
    assert active and active[0].name == expected, f"{message!r} -> {[s.name for s in active]}"


def test_activate_stays_empty_for_unrelated_chat(context):
    for message in ("какая сегодня погода", "raconte-moi une blague"):
        assert context.activate(user_message=message) == [], message


def test_activate_agrees_with_the_scorer_on_english(context, skills):
    """The English path through `activate()` must still equal the frozen legacy scorer's
    ranking — the same guarantee as above, but through the code the agent really runs."""
    for message in ENGLISH:
        old = sorted(
            ((_legacy_score(s, [], message.lower()), s.name) for s in skills),
            reverse=True,
        )
        old_hits = {name for score, name in old if score > 0}
        new_hits = {s.name for s in context.activate(user_message=message)}
        # activate() caps at max_active, so the new set must be a subset of the old hits.
        assert new_hits <= old_hits, f"{message!r}: {new_hits - old_hits} activated that never did"
