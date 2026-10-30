"""A shared location, answered with a map instead of "I can't do much with locations".

Telegram hands the bot the coordinates of a shared pin — and, for a venue, its name and
address. The old reply to a location was the sentence *"noted — but I can't do much with
locations yet"*, which was two admissions in one line: it claimed to have noted something
nothing stores, and it declined a message whose whole content is a place.

The card uses the kit's ``Map`` node (rich draws a real map; the classic projection links to
OpenStreetMap), states the numbers that came with the pin, and gives the two buttons that
need no callback: open it in a map app, and copy the coordinates.
"""

from __future__ import annotations

from navig.telegram.card_doc import Button, Card, Map, Para, Raw, Table

_FALLBACK = {
    "location.card.title": "Shared location",
    "location.card.lat": "Latitude",
    "location.card.long": "Longitude",
    "location.card.accuracy": "Accuracy",
    "location.card.accuracy_m": "{n} m",
    "location.card.live": "Live for",
    "location.card.live_min": "{n} min",
    "location.card.venue": "Place",
    "location.card.address": "Address",
    "location.card.open": "🗺 Open in Maps",
    "location.card.copy": "📋 Copy coordinates",
    "location.card.footer": "The pin came from Telegram; the map opens in any app.",
    "location.card.no_numbers": "No coordinates came with that message.",
}


def _t(key: str, **fields: object) -> str:
    text = _FALLBACK.get(key, "")
    for name, value in fields.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def _esc(value: object) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _coord(value: float) -> str:
    """Six decimals is what Telegram sends; more would be false precision on a phone pin."""
    return f"{value:.6f}"


def location_card(
    *,
    lat: float | None = None,
    long: float | None = None,
    accuracy: int | float = 0,
    live_period: int = 0,
    venue_title: str = "",
    venue_address: str = "",
    horizontal_accuracy: int | float = 0,
    zoom: int = 15,
) -> Card:
    """A shared pin: the map, the numbers, and the two buttons that work for anyone."""
    has_point = lat is not None and long is not None
    meta = ""
    if venue_title:
        meta = venue_title
    elif has_point:
        meta = f"{_coord(float(lat))}, {_coord(float(long))}"

    card = Card(_t("location.card.title"), glyph="📍", meta=meta)

    if not has_point:
        card.add(Para(_t("location.card.no_numbers"), style="italic"))
        return card

    caption = venue_title or _t("location.card.title")
    card.add(Map(lat=float(lat), long=float(long), zoom=zoom, caption=caption))

    rows: list[list[object]] = []
    if venue_title:
        rows.append([Raw(_t("location.card.venue")), Raw(_esc(venue_title))])
    if venue_address:
        rows.append([Raw(_t("location.card.address")), Raw(_esc(venue_address))])
    rows.append([Raw(_t("location.card.lat")), Raw(f"<code>{_coord(float(lat))}</code>")])
    rows.append([Raw(_t("location.card.long")), Raw(f"<code>{_coord(float(long))}</code>")])
    reported_accuracy = horizontal_accuracy or accuracy
    if reported_accuracy:
        rows.append(
            [
                Raw(_t("location.card.accuracy")),
                Raw(_t("location.card.accuracy_m", n=int(reported_accuracy))),
            ]
        )
    if live_period:
        rows.append(
            [
                Raw(_t("location.card.live")),
                Raw(_t("location.card.live_min", n=int(live_period) // 60 or 1)),
            ]
        )
    card.add(Table(rows=rows, bordered=False))

    map_url = (
        "https://www.openstreetmap.org/"
        f"?mlat={float(lat)}&mlon={float(long)}#map={zoom}/{float(lat)}/{float(long)}"
    )
    card.buttons(Button(_t("location.card.open"), url=map_url, style="primary"))
    card.buttons(
        Button(_t("location.card.copy"), copy=f"{_coord(float(lat))}, {_coord(float(long))}")
    )
    card.footer = _t("location.card.footer")
    return card


__all__ = ["location_card"]
