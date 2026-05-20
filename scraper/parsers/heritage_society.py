"""Lunenburg Heritage Society — Shopify atom-feed parser.

The Heritage Society publishes events as Shopify blog posts. The atom feed
at ``/blogs/events.atom`` gives us up to 12 entries with title, link,
published timestamp, and HTML body in structured XML — but the *event*
date lives in the body prose, not in the timestamps (those are when the
article was *posted*).

Three-stage extraction:

  1. Parse XML to entries (title, link, published, body HTML).
  2. Strip HTML, then pull the event date with a regex pipeline:
       a. "Month Day, Year" — explicit, no ambiguity ("June 7, 2025")
       b. "Weekday, Month Day [ordinal]" — borrow the year from <published>
       c. "Month Day [ordinal]" — same year-disambiguation rule
     The body is searched first; the title is consulted only if the body
     has no parseable date (some entries put the year only in the title).
  3. Drop past events. The feed routinely carries 2023/2024 archive posts
     that ``scrape.py``'s 14-day window would also filter out, but
     filtering here keeps logs clean.

Year disambiguation: when only "Month Day" is present, we use the
published year unless the resulting date lands more than 60 days *before*
the post was published, in which case the post is almost certainly
announcing an event in the following year (e.g. December post about a
January event).

Venue extraction is also prose-based. We keep a short list of known
Lunenburg-area venues and pick the one mentioned closest in the text to
the date match (so "War Memorial Arena on When: Sunday, August 3" wins
over a "Knaut-Rhuland House" mention further down the same post).
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date as _date, datetime

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

LOG = logging.getLogger(__name__)

FEED_URL = "https://lunenburgheritagesociety.ca/blogs/events.atom"
SOURCE = "heritage_society"
DEFAULT_VENUE = "Lunenburg Heritage Society"
DEFAULT_LOCATION = "Lunenburg"

UA = "Mozilla/5.0 (lunenburg-events scraper; +https://github.com/)"
TIMEOUT = 20

ATOM_NS = "{http://www.w3.org/2005/Atom}"

# --- Venue extraction ---------------------------------------------------------
# (search_substring, display_label, fallback_location). The substring is
# matched case-insensitively against the body. When multiple entries match,
# the one mentioned closest to the event-date text wins (see _extract_venue).
_VENUE_HEURISTICS: list[tuple[str, str, str | None]] = [
    ("knaut-rhuland house", "Knaut-Rhuland House Museum", "125 Pelham St, Lunenburg"),
    ("war memorial arena", "Lunenburg War Memorial Arena", "Lunenburg"),
    ("tin roof distillery", "Tin Roof Distillery", "15 Lincoln St, Lunenburg"),
    ("school of the arts", "Lunenburg School of the Arts", "6 Prince St, Lunenburg"),
    ("school for the arts", "Lunenburg School of the Arts", "6 Prince St, Lunenburg"),
    ("lennox inn", "Lennox Inn", "69 Fox St, Lunenburg"),
    ("lunenburg academy", "Lunenburg Academy", "97 Kaulbach St, Lunenburg"),
    ("lunenburg waterfront", "Lunenburg Waterfront", "Lunenburg"),
]

# --- Date extraction ----------------------------------------------------------
_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)"
)
_DAY = r"\d{1,2}(?:st|nd|rd|th)?"
_WEEKDAY = (
    r"(?:Sun(?:day)?|Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|"
    r"Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?)"
)

_FULL_DATE_RE = re.compile(
    rf"\b{_MONTH}\s+{_DAY}\s*,?\s*(\d{{4}})\b", re.IGNORECASE,
)
_WEEKDAY_DATE_RE = re.compile(
    rf"\b{_WEEKDAY},?\s+{_MONTH}\s+{_DAY}\b", re.IGNORECASE,
)
_BARE_DATE_RE = re.compile(rf"\b{_MONTH}\s+{_DAY}\b", re.IGNORECASE)

_MONTH_INDEX = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12,
    "december": 12,
}

# --- Time extraction ----------------------------------------------------------
_TIME_RANGE_RE = re.compile(
    r"\b(\d{1,2}(?::\d{2})?\s*(?:[AaPp]\.?\s*[Mm]\.?)?)"
    r"\s*[-–—]\s*"
    r"(\d{1,2}(?::\d{2})?\s*[AaPp]\.?\s*[Mm]\.?)"
)
_AT_TIME_RE = re.compile(
    r"\bat\s+(\d{1,2}(?::\d{2})?\s*[AaPp]\.?\s*[Mm]\.?)", re.IGNORECASE,
)
# "Noon to 4pm" — one end of the range is a word, not a digit.
_NOON_TO_RE = re.compile(
    r"\b(Noon|Midnight)\s*(?:to|[-–—])\s*"
    r"(\d{1,2}(?::\d{2})?\s*[AaPp]\.?\s*[Mm]\.?)\b",
    re.IGNORECASE,
)

# --- Price extraction ---------------------------------------------------------
_PRICE_RE = re.compile(r"\$\d[\d.,]*")
_FREE_RE = re.compile(
    r"\b(free admission|free event|free of charge|free to attend|no admission fee)\b",
    re.IGNORECASE,
)


def _normalize_time(raw: str) -> str:
    """Format a captured time as 'H:MM AM/PM' (or 'H AM/PM' when no minutes)."""
    s = re.sub(r"\s+", "", raw).upper().replace(".", "")
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?(AM|PM)?$", s)
    if not m:
        return raw.strip()
    hh = int(m.group(1))
    mm = m.group(2)
    mer = m.group(3) or ""
    return f"{hh}:{mm} {mer}".strip() if mm else f"{hh} {mer}".strip()


def _extract_times(text: str) -> tuple[str | None, str | None]:
    noon = _NOON_TO_RE.search(text)
    if noon:
        start = "12:00 PM" if noon.group(1).lower() == "noon" else "12:00 AM"
        return start, _normalize_time(noon.group(2))

    rng = _TIME_RANGE_RE.search(text)
    if rng:
        start, end = rng.group(1), rng.group(2)
        # If start has no AM/PM marker, inherit from end ("7-9pm" -> "7pm-9pm").
        if not re.search(r"[AaPp]", start) and re.search(r"[AaPp]", end):
            mer = re.search(r"[AaPp]\.?\s*[Mm]\.?", end).group(0)
            start = f"{start.strip()} {mer}"
        return _normalize_time(start), _normalize_time(end)

    at = _AT_TIME_RE.search(text)
    if at:
        return _normalize_time(at.group(1)), None
    return None, None


def _parse_iso_date(s: str | None) -> _date | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _adjust_year(month: int, day: int, published: _date | None) -> int:
    """Pick a year for a month-day that had no explicit year in the prose.

    Use the published year; if that lands more than 60 days *before* the
    article posting, bump to the following year — the post is forward-
    looking, not retrospective.
    """
    base = (published or _date.today()).year
    try:
        candidate = _date(base, month, day)
    except ValueError:
        return base
    if published and (published - candidate).days > 60:
        return base + 1
    return base


def _date_from_match(match: re.Match | None, published: _date | None) -> tuple[_date | None, int]:
    """Pull month/day out of a regex match and resolve the year."""
    if not match:
        return None, -1
    sub = re.search(rf"({_MONTH})\s+(\d{{1,2}})", match.group(0), re.IGNORECASE)
    if not sub:
        return None, -1
    month = _MONTH_INDEX.get(sub.group(1).lower())
    try:
        day = int(sub.group(2))
    except ValueError:
        return None, -1
    if not month:
        return None, -1
    year = _adjust_year(month, day, published)
    try:
        return _date(year, month, day), match.start()
    except ValueError:
        return None, -1


def _extract_event_date(
    title: str, body_text: str, published: _date | None
) -> tuple[_date | None, int | None]:
    """Return (event_date, position_in_body). Position is None if the date
    came from the title — the caller treats that as 'no anchor'."""
    # 1. Explicit "Month Day, Year" in body — most reliable.
    m = _FULL_DATE_RE.search(body_text)
    if m:
        try:
            return dateparser.parse(m.group(0), fuzzy=True).date(), m.start()
        except (ValueError, dateparser.ParserError):
            pass

    # 2. Weekday-anchored body match — borrow year from published.
    m = _WEEKDAY_DATE_RE.search(body_text)
    d, pos = _date_from_match(m, published)
    if d:
        return d, pos

    # 3. Bare "Month Day" body match — same year rule.
    m = _BARE_DATE_RE.search(body_text)
    d, pos = _date_from_match(m, published)
    if d:
        return d, pos

    # 4. Fallback to title — some entries put "August 3, 2025" only in the title.
    m = _FULL_DATE_RE.search(title)
    if m:
        try:
            return dateparser.parse(m.group(0), fuzzy=True).date(), None
        except (ValueError, dateparser.ParserError):
            pass
    m = _WEEKDAY_DATE_RE.search(title) or _BARE_DATE_RE.search(title)
    d, _ = _date_from_match(m, published)
    if d:
        return d, None
    return None, None


def _strip_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _extract_venue(
    body_text: str, date_pos: int | None
) -> tuple[str, str | None]:
    """Pick the known venue whose mention sits closest to the date in the body.

    When no date anchor is available (date came from title), the first
    listed heuristic that matches wins — the list is ordered by specificity.
    """
    low = body_text.lower()
    candidates: list[tuple[int, str, str | None]] = []
    for needle, display, location in _VENUE_HEURISTICS:
        idx = low.find(needle)
        if idx >= 0:
            candidates.append((idx, display, location))
    if not candidates:
        return DEFAULT_VENUE, DEFAULT_LOCATION
    if date_pos is None:
        # No anchor — keep the heuristic-list order (first match wins).
        return candidates[0][1], candidates[0][2]
    idx, display, location = min(candidates, key=lambda c: abs(c[0] - date_pos))
    return display, location


def _guess_category(title: str, body: str) -> str:
    blob = f"{title} {body}".lower()
    if re.search(r"\b(folk art festival|heritage house tour|heritage festival)\b", blob):
        return "festival"
    if re.search(
        r"\b(exhibition|exhibit|gallery|museum|artist talk|lecture|talk|tour|walk)\b",
        blob,
    ):
        return "arts"
    return "community"


def _extract_price(body_text: str) -> str | None:
    m = _PRICE_RE.search(body_text)
    if m:
        return m.group(0).strip().rstrip(".,;")
    if _FREE_RE.search(body_text):
        return "Free"
    return None


def _build_description(body_text: str, max_chars: int = 500) -> str | None:
    if not body_text:
        return None
    snippet = body_text.strip()
    if len(snippet) > max_chars:
        snippet = snippet[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return snippet or None


def parse_atom_feed(xml_text: str, today: _date | None = None) -> list[dict]:
    """Parse an atom-feed XML string into event dicts.

    Exposed (rather than inlined into ``fetch``) so the unit tests can drive
    it from a saved fixture and pass an explicit ``today`` to control the
    past-event filter.
    """
    today = today or _date.today()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        LOG.error("heritage_society: atom feed parse failed: %s", exc)
        return []

    events: list[dict] = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        title = (entry.findtext(f"{ATOM_NS}title") or "").strip()
        if not title:
            continue

        url = None
        for link in entry.findall(f"{ATOM_NS}link"):
            if link.get("rel", "alternate") == "alternate":
                url = link.get("href")
                break

        published = _parse_iso_date(entry.findtext(f"{ATOM_NS}published"))
        body_html = (
            entry.findtext(f"{ATOM_NS}content")
            or entry.findtext(f"{ATOM_NS}summary")
            or ""
        )
        body_text = _strip_html(body_html)
        if not body_text:
            LOG.debug("heritage_society: dropping %r (no body)", title)
            continue

        event_date, date_pos = _extract_event_date(title, body_text, published)
        if not event_date:
            LOG.debug("heritage_society: dropping %r (no parseable date)", title)
            continue
        if event_date < today:
            LOG.debug(
                "heritage_society: dropping past event %r (%s)", title, event_date
            )
            continue

        start_time, end_time = _extract_times(body_text)
        venue, location = _extract_venue(body_text, date_pos)
        category = _guess_category(title, body_text)
        price = _extract_price(body_text)
        description = _build_description(body_text)

        event = {
            "title": title,
            "date": event_date.strftime("%Y-%m-%d"),
            "time": start_time,
            "end_time": end_time,
            "venue": venue,
            "location": location,
            "description": description,
            "url": url,
            "price": price,
            "category": category,
            "source": SOURCE,
        }
        events.append({k: v for k, v in event.items() if v is not None})

    return events


def fetch(session: requests.Session | None = None) -> list[dict]:
    sess = session or requests.Session()
    try:
        r = sess.get(FEED_URL, headers={"User-Agent": UA}, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as exc:
        LOG.error("heritage_society feed fetch failed: %s", exc)
        return []
    return parse_atom_feed(r.text)
