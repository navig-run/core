"""One card definition, two renders — and the classic one must parse.

The regression this file exists for: a rich-only tag leaking into the classic
projection. ``<cite>`` inside ``<blockquote>`` is legal in a rich message and
illegal in ``parse_mode=HTML``, where Telegram answers 400 "can't parse entities:
Unsupported start tag" and rejects the whole send — the operator's morning card
simply never arrived. Every rich-only construct therefore has a plain-HTML twin,
and the tests below check both projections of the same card.
"""

from __future__ import annotations

import pytest

from navig.telegram.card_doc import (
    CLASSIC_LIMIT,
    Anchor,
    Bullets,
    Button,
    Card,
    Code,
    Details,
    Divider,
    Gallery,
    Heading,
    Link,
    Map,
    Math,
    Media,
    Numbered,
    Para,
    PullQuote,
    Quote,
    Raw,
    Stat,
    Table,
    Tasks,
    Time,
    esc,
    link,
    mark,
    spoiler,
    utf16_len,
)


def _card() -> Card:
    card = Card(title="Briefing", glyph="☀️", meta="Friday, 9 October", footer="Ship it.")
    card.add(Heading("First moves", level=3))
    card.add(Bullets(["look at the list", "pick three"]))
    card.add(Divider())
    return card


# ── escaping ─────────────────────────────────────────────────────────────────


def test_the_title_is_escaped_in_both_renders():
    card = Card(title="a <script> & b")
    assert "&lt;script&gt;" in card.rich_html()
    assert "&lt;script&gt;" in card.classic_html()
    assert "<script>" not in card.rich_html() + card.classic_html()


def test_esc_covers_the_three_entities():
    assert esc("a & b < c > d") == "a &amp; b &lt; c &gt; d"


def test_raw_markup_passes_through_unescaped():
    card = Card()
    card.add(Para(Raw("<b>bold</b>")))
    assert "<p><b>bold</b></p>" in card.rich_html()
    assert "<b>bold</b>" in card.classic_html()


# ── structure ────────────────────────────────────────────────────────────────


def test_the_rich_render_uses_real_blocks():
    html = _card().rich_html()
    # A card title is a section heading, not the display size (see Card.big).
    assert "<h2>☀️ Briefing</h2>" in html
    assert "<h3>First moves</h3>" in html
    assert "<ul><li>look at the list</li><li>pick three</li></ul>" in html
    assert "<hr/>" in html
    assert html.rstrip().endswith("</footer>")


def test_the_classic_render_degrades_to_text():
    html = _card().classic_html()
    assert "<b>First moves</b>" in html
    assert "• look at the list\n• pick three" in html
    assert "———" in html
    assert "<i>Ship it.</i>" in html


def test_checkboxes_are_state_not_input():
    card = Card()
    card.add(Tasks([("done thing", True), ("open thing", False)]))
    rich = card.rich_html()
    assert '<input type="checkbox" checked>done thing' in rich
    assert '<input type="checkbox">open thing' in rich
    classic = card.classic_html()
    assert "☑ done thing" in classic
    assert "☐ open thing" in classic


def test_a_table_is_a_grid_in_rich_and_aligned_text_in_classic():
    card = Card()
    card.add(
        Table(
            header=["card", "today"], rows=[["todo", "flat"], ["reminder", "title"]], caption="grid"
        )
    )
    rich = card.rich_html()
    assert "<table bordered striped>" in rich
    assert "<caption>grid</caption>" in rich
    assert "<th>card</th>" in rich and "<td>flat</td>" in rich
    classic = card.classic_html()
    assert "<i>grid</i>" in classic and "<pre>" in classic
    grid = classic.split("<pre>", 1)[1].split("</pre>", 1)[0].splitlines()
    cells = [line.split("  ") for line in grid]
    assert cells[0][0].startswith("<b>card")
    assert cells[1][0] == "todo" and cells[2][0] == "reminder"
    assert "<table" not in classic


def test_details_fold_natively_or_inside_a_quote():
    card = Card()
    card.add(Details("Yesterday", [Para("four tasks closed")]))
    assert (
        "<details><summary>Yesterday</summary><p>four tasks closed</p></details>"
        in card.rich_html()
    )
    classic = card.classic_html()
    assert "<b>▸ Yesterday</b>" in classic
    assert "<blockquote expandable><p>" not in classic  # blocks, not tags, in classic
    assert "<blockquote expandable>four tasks closed</blockquote>" in classic


def test_opened_details_carry_the_attribute():
    card = Card()
    card.add(Details("Now", [Para("x")], open=True))
    assert "<details open>" in card.rich_html()


def test_a_quote_credits_the_author_without_cite_in_classic():
    card = Card()
    card.add(Quote("main thing", cite="Plan", expandable=True))
    assert "<cite>Plan</cite>" in card.rich_html()
    classic = card.classic_html()
    assert "<cite>" not in classic  # rich-only tag: 400 in parse_mode=HTML
    assert "— <i>Plan</i>" in classic
    assert "<blockquote expandable>" in classic


def test_a_table_cell_may_carry_markup():
    """A key/value sheet has an emoji column and a value column that bolds or
    italicises — the cell must accept Raw instead of printing ``Raw(html=...)``."""
    card = Card()
    card.add(Table(rows=[["🗂", Raw("<b>work</b> <i>monthly</i>")]], bordered=False, striped=False))
    rich = card.rich_html()
    assert "<td><b>work</b> <i>monthly</i></td>" in rich
    assert "<table>" in rich  # no rules, no stripes: a sheet, not a grid
    grid = card.classic_html().split("<pre>", 1)[1].split("</pre>", 1)[0]
    assert "work monthly" in grid  # tags stripped for the aligned grid
    assert "Raw(" not in grid


def test_a_stat_is_a_key_value_line():
    card = Card()
    card.add(Stat("Disk", "82%", glyph="💽"))
    assert "💽 <b>Disk</b> · 82%" in card.rich_html()
    assert "💽 <b>Disk</b> · 82%" in card.classic_html()


# ── buttons ──────────────────────────────────────────────────────────────────


def test_buttons_live_in_the_bubble_when_rich_and_in_the_keyboard_otherwise():
    card = Card()
    card.buttons(
        Button("Open", data="td:v", style="primary"),
        Button("Done", data="td:d", style="success"),
        Button("Site", url="https://navig.run"),
    )
    rich = card.rich_html()
    assert '<tg-button-row align="left">' in rich
    assert '<tg-button type="callback_data" data="td:v" style="primary">Open</tg-button>' in rich
    assert '<tg-button type="url" url="https://navig.run">Site</tg-button>' in rich
    rows = card.keyboard()
    assert rows == [
        [
            {"text": "Open", "callback_data": "td:v", "style": "primary"},
            {"text": "Done", "callback_data": "td:d", "style": "success"},
            {"text": "Site", "url": "https://navig.run"},
        ]
    ]


def test_no_buttons_means_no_keyboard():
    assert Card(title="x").keyboard() is None


def test_a_disabled_button_cannot_be_pressed():
    card = Card()
    card.buttons(Button("Already done", data="td:d", disabled=True))
    assert '<tg-button type="disabled">Already done</tg-button>' in card.rich_html()
    cell = card.keyboard()[0][0]
    assert cell["callback_data"] == "noop:disabled"
    # Bot API 10.3 marker: a current server greys it and never calls back.
    assert cell["disabled"] == {}


def test_a_card_title_is_a_section_size_not_a_display_size():
    # A screen of <h1>s shouts, and on a phone the display size eats the first
    # screenful before any content (owner report, 2026-10-26).
    assert Card(title="Tasks").rich_html().startswith("<h2>Tasks</h2>")
    assert Card(title="Tasks", glyph="📋").rich_html().startswith("<h2>📋 Tasks</h2>")


def test_a_card_that_is_the_screen_asks_for_the_display_size():
    # A briefing owns the screen, so it earns the biggest heading.
    assert Card(title="Good morning", big=True).rich_html().startswith("<h1>Good morning</h1>")


def test_a_pull_quote_is_an_aside_and_a_classic_quote():
    block = PullQuote("One card, two renders.", cite="NAVIG · card review")
    card = Card().add(block)
    assert (
        "<aside>One card, two renders.<cite>NAVIG · card review</cite></aside>" in card.rich_html()
    )
    classic = card.classic_html()
    assert (
        "<blockquote>One card, two renders.\n— <i>NAVIG · card review</i></blockquote>" in classic
    )
    # `<aside>`/`<cite>` are rich-only; classic HTML rejects them with a 400.
    assert "<aside>" not in classic and "<cite>" not in classic


@pytest.mark.parametrize("style", ["primary", "success", "danger", "link"])
def test_every_documented_style_is_accepted(style):
    button = Button("x", data="a:b", style=style)
    assert f'style="{style}"' in button.rich()


def test_an_unknown_style_is_refused_at_construction():
    with pytest.raises(ValueError):
        Button("x", data="a:b", style="purple")


def test_link_style_is_only_for_callback_buttons():
    with pytest.raises(ValueError):
        Button("x", url="https://navig.run", style="link")


# ── the rest of the rich vocabulary ──────────────────────────────────────────


def test_an_ordered_list_keeps_its_start_type_and_direction():
    block = Numbered(["one", "two"], start=3, type="a", reversed=True)
    card = Card().add(block)
    assert '<ol start="3" type="a" reversed>' in card.rich_html()
    # Classic has no ordered list: the numbers are written out.
    assert "3. one" in card.classic_html() and "4. two" in card.classic_html()


def test_a_code_block_carries_its_language_only_when_rich():
    card = Card().add(Code("print(1)", language="python"))
    assert '<pre><code class="language-python">print(1)</code></pre>' in card.rich_html()
    assert "<pre>print(1)</pre>" in card.classic_html()


def test_math_is_typeset_rich_and_readable_classic():
    card = Card().add(Math(r"E = mc^2", block=True))
    assert "<tg-math-block>E = mc^2</tg-math-block>" in card.rich_html()
    assert "<code>E = mc^2</code>" in card.classic_html()


def test_a_map_draws_rich_and_links_out_classic():
    card = Card().add(Map(lat=48.8566, long=2.3522, zoom=13, caption="Clinic"))
    rich = card.rich_html()
    assert '<tg-map lat="48.8566" long="2.3522" zoom="13"/>' in rich
    assert "<figcaption>Clinic</figcaption>" in rich
    classic = card.classic_html()
    assert "openstreetmap.org" in classic and "<tg-map" not in classic


def test_media_becomes_a_figure_rich_and_a_link_classic():
    card = Card().add(Media(src="https://x.test/a.mp4", kind="video", caption="Walkthrough"))
    rich = card.rich_html()
    assert "<video" in rich and "<figcaption>Walkthrough</figcaption>" in rich
    classic = card.classic_html()
    assert '<a href="https://x.test/a.mp4">Walkthrough</a>' in classic
    assert "<video" not in classic and "<figure>" not in classic


def test_a_gallery_is_a_collage_or_a_slideshow_and_a_link_list_classic():
    photos = ["https://x.test/a.jpg", "https://x.test/b.jpg"]
    collage = Card().add(Gallery(sources=photos, caption="Two stills"))
    assert "<tg-collage>" in collage.rich_html()
    assert collage.rich_html().count("<img") == 2
    slideshow = Card().add(Gallery(sources=photos, slideshow=True))
    assert "<tg-slideshow>" in slideshow.rich_html()
    classic = collage.classic_html()
    assert classic.count("<a href=") == 2 and "<tg-collage>" not in classic


def test_a_timestamp_localises_rich_and_prints_utc_classic():
    card = Card().add(Time(unix=1647531900, fmt="wDT", text="22:45 tomorrow"))
    assert '<tg-time unix="1647531900" format="wDT">22:45 tomorrow</tg-time>' in card.rich_html()
    assert "22:45 tomorrow" in card.classic_html()
    silent = Card().add(Time(unix=1647531900))
    assert "UTC" in silent.classic_html()


def test_a_link_and_an_anchor_work_in_both_renders():
    card = Card().add(Anchor("end"), Link(text="Jump", url="#end"))
    assert '<a name="end"></a>' in card.rich_html()
    assert '<a href="#end">Jump</a>' in card.rich_html()
    assert '<a href="#end">Jump</a>' in card.classic_html()
    # An anchor has nowhere to land in a classic message.
    assert 'name="end"' not in card.classic_html()


def test_the_inline_helpers_are_escaped():
    assert mark("a & b").html == "<mark>a &amp; b</mark>"
    assert spoiler("<x>").html == "<tg-spoiler>&lt;x&gt;</tg-spoiler>"
    assert link("https://x.test/?a=1&b=2", "go").html == (
        '<a href="https://x.test/?a=1&amp;b=2">go</a>'
    )


def test_a_copy_button_and_an_inline_query_button_carry_their_payload():
    copy_button = Button("Copy", copy="cd:x:y:z")
    assert copy_button.rich() == '<tg-button type="copy_text" text="cd:x:y:z">Copy</tg-button>'
    assert copy_button.classic() == {"text": "Copy", "copy_text": {"text": "cd:x:y:z"}}
    query_button = Button("Search", query="navig")
    assert query_button.classic()["switch_inline_query"] == "navig"


def test_a_button_does_one_thing():
    with pytest.raises(ValueError):
        Button("x", data="a:b", url="https://navig.run")


def test_a_button_row_carries_its_alignment_in_the_body():
    card = Card()
    card.buttons(Button("ok", data="a:b"), align="center")
    assert '<tg-button-row align="center">' in card.rich_html()
    with pytest.raises(ValueError):
        card.buttons(Button("ok", data="a:b"), align="middle")


def test_the_classic_render_fits_telegram_even_when_the_card_is_huge():
    card = Card(title="big")
    card.add(Para("word " * 4000))
    html = card.classic_html()
    assert utf16_len(html) <= CLASSIC_LIMIT
    assert "cut" in html


def test_a_card_under_the_limit_is_untouched():
    card = Card(title="small")
    card.add(Para("one line"))
    assert "cut" not in card.classic_html()


def test_truncation_never_leaves_a_tag_open():
    card = Card(title="t")
    card.add(Para(Raw("<b>" + "word " * 3000)))
    html = card.classic_html()
    assert html.rfind("<b>") < html.rfind(">") or "<b>" not in html
    assert utf16_len(html) <= CLASSIC_LIMIT
