"""External-data lookups for business-chat commands: weather, crypto, currency,
whois. Each returns a formatted, ready-to-send string (or a friendly error) and
is fully self-contained (free public APIs, short timeouts, no keys).

Used by navig.telegram.biz_commands. Network calls are async (aiohttp) or threaded
(TCP whois) so they never block the gateway loop.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "curl/8.4.0"}  # wttr.in serves JSON to curl-like agents


async def _get_json(url: str, *, timeout: float = 8.0, headers: dict | None = None):
    try:
        import aiohttp

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.get(url, headers=headers or {}) as r:
                if r.status != 200:
                    return None
                return await r.json(content_type=None)
    except Exception:  # noqa: BLE001
        return None


def _money(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if v >= 1000:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:,.2f}"
    return f"{v:.6f}".rstrip("0").rstrip(".")  # sub-dollar coins keep precision


# ── weather (wttr.in) ────────────────────────────────────────────────────────


async def weather(city: str) -> str:
    spec = (city or "").strip()
    url = f"https://wttr.in/{spec}?format=j1" if spec else "https://wttr.in/?format=j1"
    data = await _get_json(url, timeout=10, headers=_UA)
    if not data:
        return "🌧 Couldn't fetch the weather right now."
    cur = (data.get("current_condition") or [{}])[0]
    area = (data.get("nearest_area") or [{}])[0]
    name = ((area.get("areaName") or [{}])[0].get("value") or spec or "Here")
    country = (area.get("country") or [{}])[0].get("value") or ""
    desc = (cur.get("weatherDesc") or [{}])[0].get("value") or ""
    loc = f"{name}, {country}" if country else name
    t, f = cur.get("temp_C"), cur.get("FeelsLikeC")
    return (
        f"🌤 <b>{loc}</b>\n"
        f"{desc} · <b>{t}°C</b> (feels {f}°C)\n"
        f"💨 {cur.get('windspeedKmph')} km/h {cur.get('winddir16Point', '')} · "
        f"💧 {cur.get('humidity')}%"
    )


# ── crypto (CoinGecko) ───────────────────────────────────────────────────────

_COIN_IDS = {
    "btc": "bitcoin", "eth": "ethereum", "sol": "solana", "bnb": "binancecoin",
    "xrp": "ripple", "ada": "cardano", "doge": "dogecoin", "dot": "polkadot",
    "matic": "matic-network", "ltc": "litecoin", "trx": "tron", "avax": "avalanche-2",
    "link": "chainlink", "ton": "the-open-network", "shib": "shiba-inu",
    "usdt": "tether", "usdc": "usd-coin", "xmr": "monero", "bch": "bitcoin-cash",
    "near": "near", "atom": "cosmos", "uni": "uniswap", "etc": "ethereum-classic",
}


async def crypto(sym: str, vs: str = "usd") -> str:
    s = (sym or "").strip().lower() or "btc"
    vs = (vs or "usd").lower()
    cid = _COIN_IDS.get(s)
    if not cid:
        sr = await _get_json(f"https://api.coingecko.com/api/v3/search?query={s}", timeout=8)
        coins = (sr or {}).get("coins") or []
        cid = coins[0]["id"] if coins else None
    if not cid:
        return f"₿ Unknown coin <b>{s.upper()}</b>."
    d = await _get_json(
        f"https://api.coingecko.com/api/v3/simple/price?ids={cid}"
        f"&vs_currencies={vs}&include_24hr_change=true",
        timeout=8,
    )
    row = (d or {}).get(cid) or {}
    price = row.get(vs)
    if price is None:
        return f"₿ No {vs.upper()} price for <b>{s.upper()}</b>."
    chg = row.get(f"{vs}_24h_change")
    chg_s = ""
    if chg is not None:
        chg_s = f"\n{'🟢▲' if chg >= 0 else '🔴▼'} {chg:+.2f}% (24h)"
    return f"₿ <b>{cid.replace('-', ' ').title()}</b> ({s.upper()})\n<b>{_money(price)} {vs.upper()}</b>{chg_s}"


# ── currency (open.er-api.com) ───────────────────────────────────────────────


async def currency(text: str) -> str:
    tokens = re.findall(r"\d+(?:\.\d+)?|[a-zA-Z]{3}", text or "")
    amount = 1.0
    codes: list[str] = []
    for tok in tokens:
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            amount = float(tok)
        elif tok.lower() not in ("to", "and", "the"):
            codes.append(tok.upper())
    if len(codes) < 2:
        return "💱 Usage: <code>currency 100 usd eur</code>"
    frm, to = codes[0], codes[1]
    d = await _get_json(f"https://open.er-api.com/v6/latest/{frm}", timeout=8)
    rates = (d or {}).get("rates") or {}
    rate = rates.get(to)
    if not rate:
        return f"💱 Couldn't convert <b>{frm}→{to}</b> (check the codes)."
    return (
        f"💱 <b>{amount:g} {frm}</b> = <b>{_money(amount * rate)} {to}</b>\n"
        f"<i>1 {frm} = {rate:.4f} {to}</i>"
    )


# ── whois (TCP, IANA → TLD referral) ─────────────────────────────────────────


def _clean_domain(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"^https?://", "", s).split("/")[0].split("?")[0]
    return s.strip().strip(".")


def _whois_query(server: str, domain: str) -> str:
    with socket.create_connection((server, 43), timeout=5) as sock:
        sock.sendall((domain + "\r\n").encode())
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk or len(data) > 65536:
                break
            data += chunk
    return data.decode(errors="replace")


def _whois_raw(domain: str) -> str:
    raw = _whois_query("whois.iana.org", domain)
    refer = next((ln.split(":", 1)[1].strip() for ln in raw.splitlines()
                  if ln.lower().startswith("refer:")), None)
    if refer:
        try:
            return _whois_query(refer, domain)
        except Exception:  # noqa: BLE001
            pass
    return raw


_WHOIS_FIELDS = {
    "registrar": ("registrar:",),
    "created": ("creation date:", "created:", "registered on:"),
    "expires": ("registry expiry date:", "expiry date:", "expires:", "paid-till:"),
    "status": ("domain status:", "status:"),
}


def _parse_whois(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in raw.splitlines():
        low = line.lower().strip()
        for key, prefixes in _WHOIS_FIELDS.items():
            if key in out:
                continue
            for p in prefixes:
                if low.startswith(p):
                    out[key] = line.split(":", 1)[1].strip()
                    break
    return out


async def whois(domain: str) -> str:
    d = _clean_domain(domain)
    if not d or "." not in d:
        return "🌐 Usage: <code>whois example.com</code>"
    try:
        raw = await asyncio.to_thread(_whois_raw, d)
    except Exception:  # noqa: BLE001
        return f"🌐 whois lookup failed for <b>{d}</b>."
    if not raw:
        return f"🌐 No whois data for <b>{d}</b>."
    f = _parse_whois(raw)
    lines = [f"🌐 <b>{d}</b>"]
    if f.get("registrar"):
        lines.append(f"🏢 {f['registrar']}")
    if f.get("created"):
        lines.append(f"📅 Created: {f['created'][:10]}")
    if f.get("expires"):
        lines.append(f"⏳ Expires: {f['expires'][:10]}")
    if f.get("status"):
        lines.append(f"🔖 {f['status'].split()[0]}")
    return "\n".join(lines) if len(lines) > 1 else f"🌐 <b>{d}</b> — registered (no public details)."
