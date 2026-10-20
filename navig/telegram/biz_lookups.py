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


_WEATHER_COPY = {
    "en": ("feels", "km/h", "🌧 Couldn't fetch the weather right now."),
    "ru": ("ощущается", "км/ч", "🌧 Не удалось получить погоду."),
    "fr": ("ressenti", "km/h", "🌧 Météo indisponible pour le moment."),
}


async def weather(city: str, lang: str = "en") -> str:
    """Current weather for *city*, in *lang* (en/ru/fr). "погода Москва" came back in
    English, and "weather Paris" was titled by whichever neighbourhood the service
    picked ("Saint-Merri") — the city now keeps the name it was asked by."""
    spec = (city or "").strip()
    lang = lang if lang in _WEATHER_COPY else "en"
    feels, kmh, failed = _WEATHER_COPY[lang]
    base = f"https://wttr.in/{spec}?format=j1" if spec else "https://wttr.in/?format=j1"
    url = base if lang == "en" else f"{base}&lang={lang}"
    data = await _get_json(url, timeout=10, headers=_UA)
    if not data:
        return failed
    cur = (data.get("current_condition") or [{}])[0]
    area = (data.get("nearest_area") or [{}])[0]
    name = spec.title() if spec else ((area.get("areaName") or [{}])[0].get("value") or "Here")
    country = (area.get("country") or [{}])[0].get("value") or ""
    desc = (cur.get(f"lang_{lang}") or cur.get("weatherDesc") or [{}])[0].get("value") or ""
    loc = f"{name}, {country}" if country else name
    t, f = cur.get("temp_C"), cur.get("FeelsLikeC")
    return (
        f"🌤 <b>{loc}</b>\n"
        f"{desc.strip()} · <b>{t}°C</b> ({feels} {f}°C)\n"
        f"💨 {cur.get('windspeedKmph')} {kmh} {cur.get('winddir16Point', '')} · "
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

# Crypto symbols recognised for a bare "N BTC to USD" conversion. The NL matcher in
# biz_commands validates against this so ordinary words never trigger a conversion.
CRYPTO_SYMBOLS = frozenset(_COIN_IDS)


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


# ── amount-aware conversion across fiat + crypto ("0.5 btc to usd") ──────────


async def _coin_price(sym: str, vs: str = "usd") -> tuple[str, float, float | None] | None:
    """``(coin_id, price, 24h_change%)`` for *sym* priced in *vs*, or None. Resolves
    the CoinGecko id from the known map, else a search."""
    s = (sym or "").strip().lower() or "btc"
    vs = (vs or "usd").lower()
    cid = _COIN_IDS.get(s)
    if not cid:
        sr = await _get_json(f"https://api.coingecko.com/api/v3/search?query={s}", timeout=8)
        coins = (sr or {}).get("coins") or []
        cid = coins[0]["id"] if coins else None
    if not cid:
        return None
    d = await _get_json(
        f"https://api.coingecko.com/api/v3/simple/price?ids={cid}"
        f"&vs_currencies={vs}&include_24hr_change=true",
        timeout=8,
    )
    row = (d or {}).get(cid) or {}
    price = row.get(vs)
    if price is None:
        return None
    return cid, float(price), row.get(f"{vs}_24h_change")


async def _usd_value(code: str) -> float | None:
    """USD value of ONE unit of *code* — a fiat ISO code or a crypto symbol."""
    code = (code or "").upper()
    if code == "USD":
        return 1.0
    if code.lower() in _COIN_IDS:
        r = await _coin_price(code, "usd")
        return r[1] if r else None
    d = await _get_json(f"https://open.er-api.com/v6/latest/{code}", timeout=8)
    rate = ((d or {}).get("rates") or {}).get("USD")
    return float(rate) if rate else None


async def smart_convert(amount: float, frm: str, to: str) -> str:
    """Convert *amount* of *frm* into *to* for ANY mix of fiat & crypto, bridging via
    USD. Used by the natural-language "N BTC to USD" path; plain fiat↔fiat stays on
    :func:`currency` (one call)."""
    frm, to = (frm or "").upper(), (to or "").upper()
    uf = await _usd_value(frm)
    ut = await _usd_value(to)
    if not uf or not ut:
        return f"💱 Couldn't convert <b>{frm}→{to}</b> right now (check the codes)."
    rate = uf / ut
    glyph = "₿" if (frm.lower() in _COIN_IDS or to.lower() in _COIN_IDS) else "💱"
    return (
        f"{glyph} <b>{amount:g} {frm}</b> = <b>{_money(amount * rate)} {to}</b>\n"
        f"<i>1 {frm} = {_money(rate)} {to}</i>"
    )


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


_RDAP_HEADERS = {**_UA, "Accept": "application/rdap+json"}  # rdap.org 403s aiohttp's UA


async def _rdap(domain: str) -> dict[str, str] | None:
    """Registration facts over RDAP — the only lookup many registries still serve.

    `.run` (and the other Identity Digital TLDs) publish no port-43 whois at all:
    IANA's record has an empty ``refer:``, and the old code then parsed IANA's
    record about the TLD itself, so `whois navig.run` answered "Created:
    2015-04-30" — the day `.run` was created."""
    data = await _get_json(f"https://rdap.org/domain/{domain}", timeout=10, headers=_RDAP_HEADERS)
    if not isinstance(data, dict) or not data.get("ldhName"):
        return None
    out: dict[str, str] = {}
    for ev in data.get("events") or []:
        action, when = ev.get("eventAction"), ev.get("eventDate") or ""
        if action == "registration":
            out["created"] = when
        elif action == "expiration":
            out["expires"] = when
    for ent in data.get("entities") or []:
        if "registrar" in (ent.get("roles") or []):
            for row in (ent.get("vcardArray") or [None, []])[1]:
                if isinstance(row, list) and len(row) > 3 and row[0] == "fn":
                    out["registrar"] = str(row[3])
    if status := data.get("status"):
        out["status"] = str(status[0])
    return out


_NOT_FOUND_RE = re.compile(
    r"no match for|not found|no entries found|no data found|status:\s*free|"
    r"is available for|no object found|domain not found",
    re.IGNORECASE,
)


_STATUS_URL_RE = re.compile(r"\s+https?://\S+")


def _is_tld_record(raw: str, domain: str) -> bool:
    """IANA's answer about the TLD, not about *domain* (an empty ``refer:``)."""
    first = next((ln.split(":", 1)[1].strip().lower() for ln in raw.splitlines()
                  if ln.lower().startswith("domain:")), "")
    return bool(first) and first != domain.lower() and "." not in first


async def lookup_domain(domain: str) -> dict[str, str]:
    """Registration facts for *domain*: RDAP first, then port-43 whois.

    Always returns ``domain`` and ``outcome`` — ``ok`` · ``not_registered`` ·
    ``no_data`` · ``failed`` · ``invalid`` — plus, when ``ok``, ``source`` and
    whichever of ``registrar`` / ``created`` / ``expires`` / ``status`` were found.
    Shared by the ``/whois`` command and the agent's ``domain_lookup`` tool, so
    neither shells out to a ``whois`` binary (on Windows that is Sysinternals
    whois, which prints its licence and exits 1 until its EULA is accepted).
    """
    d = _clean_domain(domain)
    if not d or "." not in d:
        return {"domain": d, "outcome": "invalid"}
    f = await _rdap(d)
    if f is not None:
        return {"domain": d, "outcome": "ok", "source": "rdap", **f}
    try:
        raw = await asyncio.to_thread(_whois_raw, d)
    except Exception:  # noqa: BLE001
        return {"domain": d, "outcome": "failed"}
    if not raw or _is_tld_record(raw, d):
        return {"domain": d, "outcome": "no_data"}
    if _NOT_FOUND_RE.search(raw):
        # It said "registered (no public details)" for a free domain.
        return {"domain": d, "outcome": "not_registered"}
    return {"domain": d, "outcome": "ok", "source": "whois", **_parse_whois(raw)}


async def whois(domain: str) -> str:
    f = await lookup_domain(domain)
    d, outcome = f["domain"], f["outcome"]
    if outcome == "invalid":
        return "🌐 Usage: <code>whois example.com</code>"
    if outcome == "failed":
        return f"🌐 whois lookup failed for <b>{d}</b>."
    if outcome == "no_data":
        return f"🌐 No whois data for <b>{d}</b>."
    if outcome == "not_registered":
        return f"🌐 <b>{d}</b> is not registered."
    lines = [f"🌐 <b>{d}</b>"]
    if f.get("registrar"):
        lines.append(f"🏢 {f['registrar']}")
    if f.get("created"):
        lines.append(f"📅 Created: {f['created'][:10]}")
    if f.get("expires"):
        lines.append(f"⏳ Expires: {f['expires'][:10]}")
    if f.get("status"):
        # whois: "clientDeleteProhibited https://icann.org/epp#…"; RDAP: "client
        # delete prohibited" — the first word alone read "client".
        lines.append(f"🔖 {_STATUS_URL_RE.sub('', f['status']).strip()}")
    return "\n".join(lines) if len(lines) > 1 else f"🌐 <b>{d}</b> — registered (no public details)."
