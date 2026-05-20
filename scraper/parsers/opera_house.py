"""Lunenburg Opera House / Folk Harbour Society parser.

Two source pages per the spec:

  1. https://folkharbour.ca/concerts        — Folk Harbour Society's own
     "Weekends at the Opera House" concert series. Each event sits in an
     <h4> with text like "May 22: Pretty Archie", followed (as sibling
     divs) by an artist website link and a "Get Tickets" link pointing to
     TicketPro. Time (7:30pm, doors 6:45pm) and price range
     ("$15–$55; 50% off ages 25 & under") are stated once globally on the
     page, not per event.

  2. https://www.folkharbour.com/other-events/  — third-party events at the
     Opera House (groups renting the venue). As of May 2026 this URL 404s
     because folkharbour.com has been consolidated into folkharbour.ca and
     the "other events" listing was retired. We still attempt the fetch
     and log a warning so the parser surfaces the page coming back without
     code changes.
"""
from __future__ import annotations

import logging
import re
from datetime import date as _date
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

LOG = logging.getLogger(__name__)

CONCERTS_URL = "https://folkharbour.ca/concerts"
OTHER_EVENTS_URL = "https://www.folkharbour.com/other-events/"
SOURCE = "opera_house"
VENUE = "Lunenburg Opera House"
LOCATION = "290 Lincoln St, Lunenburg"

CONCERT_TIME = "7:30 PM"
CONCERT_DESC = "Weekends at the Opera House concert series. Doors open at 6:45pm."
CONCERT_PRICE = "$15–$55; 50% off ages 25 & under"

UA = "Mozilla/5.0 (lunenburg-events scraper; +https://github.com/)"
TIMEOUT = 20

# "May 22: Pretty Archie"  /  "June 5: Neon Dreams"
_DATE_TITLE_RE = re.compile(
    r"^\s*(?P<month>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+(?P<day>\d{1,2})\s*[:\-–]\s*(?P<title>.+?)\s*$",
    re.IGNORECASE,
)


def _infer_year(soup: BeautifulSoup) -> int:
    """Find the season year on the page (e.g. "2026" mentioned near 'season'
    or 'concert'); fall back to a calendar-wrap heuristic if not found."""
    text = soup.get_text(" ", strip=True)
    m = re.search(
        r"(20\d{2})(?:\s*(?:/\s*20\d{2})?)\s*(?:Season|Concert|Festival)",
        text,
        re.IGNORECASE,
    )
    if m:
        return int(m.group(1))
    m = re.search(r"(20\d{2})", text)
    if m:
        return int(m.group(1))
    return _date.today().year


def _resolve_year(month: int, day: int, season_year: int) -> int:
    """If the dated event in `season_year` is already long past, the page is
    probably advertising next season — bump the year. Conservative: only
    bump when the calendar gap is more than 60 days in the past."""
    try:
        candidate = _date(season_year, month, day)
    except ValueError:
        return season_year
    if (_date.today() - candidate).days > 60:
        return season_year + 1
    return season_year


def _walk_event_links(h4) -> tuple[str | None, str | None]:
    """Walk forward in the DOM until the next H4, collecting the first
    TicketPro ticket URL and the first non-TicketPro external URL (artist
    site). Walks following siblings of the H4 and then any descendants."""
    ticket_url: str | None = None
    artist_url: str | None = None

    sib = h4
    for _ in range(20):
        sib = sib.find_next_sibling()
        if sib is None:
            break
        if getattr(sib, "name", None) == "h4":
            break
        if not hasattr(sib, "find_all"):
            continue
        for a in sib.find_all("a", href=True):
            href = a["href"]
            host = urlparse(href).netloc.lower()
            if "ticketpro" in host:
                ticket_url = ticket_url or href
            elif href.startswith("http") and "folkharbour" not in host:
                artist_url = artist_url or href
    return ticket_url, artist_url


def _parse_concerts(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    season_year = _infer_year(soup)
    events: list[dict] = []
    for h4 in soup.find_all("h4"):
        m = _DATE_TITLE_RE.match(h4.get_text(" ", strip=True))
        if not m:
            continue
        try:
            stub = dateparser.parse(f"{m['month']} {m['day']} 2000")
        except (ValueError, dateparser.ParserError):
            continue
        year = _resolve_year(stub.month, stub.day, season_year)
        date_iso = f"{year:04d}-{stub.month:02d}-{stub.day:02d}"
        title = re.sub(r"\s+", " ", m["title"]).strip()

        ticket_url, _artist = _walk_event_links(h4)

        events.append({
            "title": title,
            "date": date_iso,
            "time": CONCERT_TIME,
            "venue": VENUE,
            "location": LOCATION,
            "description": CONCERT_DESC,
            "url": CONCERTS_URL,
            "ticket_url": ticket_url,
            "price": CONCERT_PRICE,
            "category": "music",
            "source": SOURCE,
        })
    return events


def _parse_other_events(html: str) -> list[dict]:
    """Best-effort parser for the (currently retired) third-party events page.

    Structure here is unknown / volatile — when the page comes back, this
    will likely need source-specific tweaks. For now we extract anything
    that looks like an article/event card with a date and a heading, and
    use safe fallbacks for the rest of the schema."""
    soup = BeautifulSoup(html, "html.parser")
    events: list[dict] = []

    # WordPress event pages typically use <article> tags or div.event blocks.
    candidates = soup.select("article, .event, .tribe-events-event, .wp-block-post")
    for card in candidates:
        title_el = card.find(["h1", "h2", "h3", "h4"])
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        if not title:
            continue

        # Pull any ISO date from a <time datetime=...> or YYYY-MM-DD in text.
        date_iso = None
        time_el = card.find("time", attrs={"datetime": True})
        if time_el:
            dt = time_el["datetime"][:10]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", dt):
                date_iso = dt
        if not date_iso:
            m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", card.get_text(" ", strip=True))
            if m:
                date_iso = m.group(1)
        if not date_iso:
            continue

        description = None
        p = card.find("p")
        if p:
            description = re.sub(r"\s+", " ", p.get_text(" ", strip=True))[:500]

        # Category: light heuristic; default to "community" since rentals span
        # genres (book launches, talks, recitals, etc.)
        blob = title.lower()
        if re.search(r"film|movie|cinema|screening", blob):
            category = "film"
        elif re.search(r"theatre|theater|play|drag show", blob):
            category = "theater"
        elif re.search(r"concert|band|singer|quartet|orchestra|jazz|blues", blob):
            category = "music"
        else:
            category = "community"

        events.append({
            "title": title,
            "date": date_iso,
            "venue": VENUE,
            "location": LOCATION,
            "description": description,
            "url": OTHER_EVENTS_URL,
            "category": category,
            "source": SOURCE,
        })
    return events


def _dedupe(events: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for e in events:
        key = (e.get("date"), re.sub(r"[^a-z0-9]+", "", e.get("title", "").lower()))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def fetch(session: requests.Session | None = None) -> list[dict]:
    sess = session or requests.Session()
    headers = {"User-Agent": UA}

    all_events: list[dict] = []

    # Concert series (primary source).
    try:
        r = sess.get(CONCERTS_URL, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        all_events.extend(_parse_concerts(r.text))
    except Exception as exc:
        LOG.error("opera_house concerts fetch failed: %s", exc)

    # Third-party events (currently retired URL — warn and continue).
    try:
        r = sess.get(OTHER_EVENTS_URL, headers=headers, timeout=TIMEOUT)
        if r.status_code == 404:
            LOG.warning(
                "opera_house other-events page %s returned 404 — the listing "
                "appears to have been retired during the folkharbour.com → "
                ".ca consolidation. Skipping.", OTHER_EVENTS_URL,
            )
        else:
            r.raise_for_status()
            all_events.extend(_parse_other_events(r.text))
    except Exception as exc:
        LOG.warning("opera_house other-events fetch failed: %s", exc)

    # Drop entries where required fields are missing.
    cleaned = [
        {k: v for k, v in e.items() if v is not None}
        for e in all_events
        if e.get("title") and e.get("date")
    ]
    return _dedupe(cleaned)
