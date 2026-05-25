"""Lunenburg School of the Arts parser.

The School of the Arts publishes events as WordPress blog posts; dates and
times live in prose rather than structured metadata. The listing page does
give us one tidy field — ``<p class="dates">`` holds the canonical event
date (e.g. "May 21, 2026"). Everything else (time, venue overrides, price,
description) is extracted from the article body on the detail page.

We filter out multi-week workshop courses — the app is for one-off public
events, not course enrollments. Heuristic phrases like "workshop series",
"6-week course", "session 1 of 8", and "registration required" trip the
filter. We deliberately do NOT match "no registration required" (the
opposite signal — that's a public lecture, exhibition opening, etc.).
"""
from __future__ import annotations

import logging
import re
import time as _time
from datetime import date as _date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

LOG = logging.getLogger(__name__)

LISTING_URL = "https://lunenburgarts.org/events/"
SOURCE = "school_of_arts"
DEFAULT_VENUE = "Lunenburg School of the Arts"
DEFAULT_LOCATION = "6 Prince St, Lunenburg"

UA = "Mozilla/5.0 (lunenburg-events scraper; +https://github.com/)"
TIMEOUT = 20
DETAIL_DELAY_SECONDS = 0.4

# --- Workshop-course filter ---------------------------------------------------
# Phrases that indicate an ongoing course (drop) rather than a public event.
# Order matters only for clarity. Tested as case-insensitive regexes.
_WORKSHOP_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"\bworkshop series\b",
        r"\bweekly class(?:es)?\b",
        r"\b\d+\s*-?\s*(?:week|session)\s+(?:course|program|class|workshop)\b",
        r"\bsession\s+\d+\s+of\s+\d+\b",
        r"\bmulti[- ]?week\b",
        r"\bsemester\b",
        r"\bcourse fee\b",
    )
]
# "registration required" usually means a course (drop). We carve out the
# "no registration required" case since that's the explicit *public-event*
# signal on this site.
_REGISTRATION_REQUIRED_RE = re.compile(
    r"(?<!no )registration\s+required", re.IGNORECASE
)

# --- Time + address extraction ------------------------------------------------
_TIME_RANGE_RE = re.compile(
    r"\b(\d{1,2}(?::\d{2})?\s*(?:[AaPp]\.?\s*[Mm]\.?)?)"
    r"\s*[-–—]\s*"
    r"(\d{1,2}(?::\d{2})?\s*[AaPp]\.?\s*[Mm]\.?)"
)
_SINGLE_TIME_RE = re.compile(
    r"\bat\s+(\d{1,2}(?::\d{2})?\s*[AaPp]\.?\s*[Mm]\.?)",
    re.IGNORECASE,
)
_BARE_TIME_RE = re.compile(
    r"\b(\d{1,2}(?::\d{2})?\s*[AaPp]\.?\s*[Mm]\.?)\b",
    re.IGNORECASE,
)

# Towns whose names may legitimately follow an address as the city part.
_TOWNS_RE = r"Lunenburg|Mahone Bay|Riverport|Blue Rocks|Stonehurst|Chester|Bridgewater|Halifax"
_ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){0,3}\s+"
    r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|"
    r"Drive|Dr\.?|Lane|Ln\.?|Place|Pl\.?|Highway|Hwy\.?)"
    r"(?:\s*,?\s*(?:" + _TOWNS_RE + r"))?",
)

# Where price prose typically ends — we cut here so we don't gobble up the
# next sentence (e.g. "$10 at the door, youth free (18 and under)" should NOT
# carry on into "musiqueroyale1985@gmail.com for advance reservation").
_PRICE_STOPS = (
    ". ", "; ", "\n", " Email ", "Email:", " To register", " Doors",
    " Pre-concert", " Buy ", " Tickets ", " Pay ", " Reservations",
    " Please ", " RSVP", " Register",
)
_FREE_RE = re.compile(
    r"\b(free event|free admission|free of charge|no registration required|"
    r"no admission fee|free to attend)\b",
    re.IGNORECASE,
)
_PWYC_RE = re.compile(r"\b(PWYC|pay what you can|pay-what-you-can)\b", re.IGNORECASE)


def _normalize_time(raw: str) -> str:
    """Format a captured time string as "H:MM AM/PM" (or "H AM/PM" if no minutes)."""
    s = re.sub(r"\s+", "", raw).upper()
    s = s.replace(".", "")
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?(AM|PM)?$", s)
    if not m:
        return raw.strip()
    hh = int(m.group(1))
    mm = m.group(2)
    mer = m.group(3) or ""
    return f"{hh}:{mm} {mer}".strip() if mm else f"{hh} {mer}".strip()


def _extract_times(text: str) -> tuple[str | None, str | None]:
    """Return (start, end) human-readable times if findable; else (None, None)."""
    rng = _TIME_RANGE_RE.search(text)
    if rng:
        start, end = rng.group(1), rng.group(2)
        # If start has no AM/PM, borrow from end.
        if not re.search(r"[AaPp]", start) and re.search(r"[AaPp]", end):
            mer = re.search(r"[AaPp]\.?\s*[Mm]\.?", end).group(0)
            start = f"{start.strip()} {mer}"
        return _normalize_time(start), _normalize_time(end)

    at_match = _SINGLE_TIME_RE.search(text)
    if at_match:
        return _normalize_time(at_match.group(1)), None

    bare = _BARE_TIME_RE.search(text)
    if bare:
        return _normalize_time(bare.group(1)), None
    return None, None


def _extract_address(text: str) -> str | None:
    m = _ADDRESS_RE.search(text)
    if not m:
        return None
    addr = m.group(0).strip().rstrip(",")
    # Append town only if NO recognized town already appears anywhere in the
    # captured address (handles both "6 Prince St, Lunenburg" and the
    # comma-less "6 Prince St. Lunenburg" forms — we don't want to append twice).
    if not re.search(r"\b(?:" + _TOWNS_RE + r")\b", addr):
        town_m = re.search(r"\b(" + _TOWNS_RE + r")\b", text)
        if town_m:
            addr = f"{addr}, {town_m.group(1)}"
    return addr


def _extract_price(text: str) -> str | None:
    idx = text.find("$")
    if idx >= 0 and re.match(r"\$\d", text[idx:idx + 2]):
        chunk = text[idx:idx + 80]
        # Truncate at the first stop marker (whichever comes earliest).
        cut = len(chunk)
        for stop in _PRICE_STOPS:
            j = chunk.find(stop)
            if 0 <= j < cut:
                cut = j
        chunk = chunk[:cut]
        # Trim trailing email/URL/handle fragments that slipped past the stops.
        chunk = re.split(r"\s+\S+@\S+", chunk)[0]
        chunk = re.split(r"\s+(?:https?://|www\.)\S+", chunk)[0]
        chunk = re.split(r"\s+\S+\.(?:com|ca|org|net)\b", chunk)[0]
        chunk = chunk.strip().rstrip(".;, ")
        if chunk:
            return chunk
    if _PWYC_RE.search(text):
        return "PWYC"
    if _FREE_RE.search(text):
        return "Free"
    return None


def _is_workshop_course(text: str) -> bool:
    if _REGISTRATION_REQUIRED_RE.search(text):
        return True
    return any(p.search(text) for p in _WORKSHOP_PATTERNS)


def _guess_category(title: str, body: str) -> str:
    blob = f"{title} {body}".lower()
    if re.search(r"\b(concert|recital|musique|jazz|fiddle|orchestra|chamber|cookie concert|sound & sketch)\b", blob):
        return "music"
    if re.search(r"\b(film|screening|cinema)\b", blob):
        return "film"
    if re.search(r"\b(exhibition|exhibit|gallery|artist talk|lecture|opening reception|artists remarks)\b", blob):
        return "arts"
    return "community"


def _parse_event_date(p_dates_text: str | None, body_text: str) -> str | None:
    """Pick the best date string available.

    Priority:
      1. ``p.dates`` text on the listing card (e.g. "May 21, 2026")
      2. First full "Month Day, Year" found in the body
      3. First "Weekday, Month Day" found in the body (assume current year)
    """
    if p_dates_text:
        try:
            return dateparser.parse(p_dates_text, fuzzy=True).date().strftime("%Y-%m-%d")
        except (ValueError, dateparser.ParserError):
            pass

    full = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
        r"Dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?\s*,?\s*\d{4}\b",
        body_text, re.IGNORECASE,
    )
    if full:
        try:
            return dateparser.parse(full.group(0), fuzzy=True).date().strftime("%Y-%m-%d")
        except (ValueError, dateparser.ParserError):
            pass

    weekday = re.search(
        r"\b(?:Sun(?:day)?|Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|"
        r"Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?),?\s+"
        r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
        r"Dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?",
        body_text, re.IGNORECASE,
    )
    if weekday:
        candidate = weekday.group(0) + f" {_date.today().year}"
        try:
            return dateparser.parse(candidate, fuzzy=True).date().strftime("%Y-%m-%d")
        except (ValueError, dateparser.ParserError):
            return None
    return None


def _parse_listing(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards: list[dict] = []
    for a in soup.select("a.news-events-item"):
        href = a.get("href")
        if not href:
            continue
        h2 = a.find("h2")
        title = h2.get_text(" ", strip=True) if h2 else None
        if not title:
            continue
        title = title.replace("“", '"').replace("”", '"')

        date_p = a.select_one("p.dates")
        date_text = date_p.get_text(" ", strip=True) if date_p else None
        excerpt_p = a.select_one("p.excerpt")
        excerpt = excerpt_p.get_text(" ", strip=True) if excerpt_p else ""

        cards.append({
            "_detail_url": urljoin(LISTING_URL, href),
            "title": title,
            "_listing_date_text": date_text,
            "_listing_excerpt": excerpt,
        })
    return cards


def _parse_detail_body(html: str) -> tuple[str, str]:
    """Return (h1_title, plain_body_text). Body skips header/nav/footer."""
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article")
    if not article:
        return "", ""
    h1 = article.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    paragraphs = [p.get_text(" ", strip=True) for p in article.find_all("p")]
    paragraphs = [p for p in paragraphs if p]
    body = " ".join(paragraphs)
    body = re.sub(r"\s+", " ", body).strip()
    return title, body


def _build_description(body: str, max_chars: int = 500) -> str | None:
    if not body:
        return None
    # First paragraph (split on sentence boundary if too long).
    snippet = body.strip()
    if len(snippet) > max_chars:
        snippet = snippet[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return snippet or None


def fetch(session: requests.Session | None = None) -> list[dict]:
    sess = session or requests.Session()
    headers = {"User-Agent": UA}

    try:
        r = sess.get(LISTING_URL, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as exc:
        LOG.error("school_of_arts listing fetch failed: %s", exc)
        return []

    cards = _parse_listing(r.text)
    LOG.debug("school_of_arts: %d cards on listing page", len(cards))

    out: list[dict] = []
    for card in cards:
        url = card["_detail_url"]
        body = ""
        title = card["title"]
        try:
            dr = sess.get(url, headers=headers, timeout=TIMEOUT)
            dr.raise_for_status()
            detail_title, body = _parse_detail_body(dr.text)
            if detail_title:
                title = detail_title.replace("“", '"').replace("”", '"')
        except Exception as exc:
            LOG.warning("school_of_arts detail fetch failed (%s): %s", url, exc)
            body = card["_listing_excerpt"]
        _time.sleep(DETAIL_DELAY_SECONDS)

        # Combine listing excerpt + detail body so we don't miss anything.
        full_text = f"{card['_listing_excerpt']} {body}".strip()

        if _is_workshop_course(full_text):
            LOG.debug("school_of_arts: dropping workshop/course %r", title)
            continue

        date_iso = _parse_event_date(card["_listing_date_text"], full_text)
        if not date_iso:
            LOG.debug("school_of_arts: dropping %r (no parseable date)", title)
            continue

        start_time, end_time = _extract_times(full_text)
        price = _extract_price(full_text)
        address = _extract_address(full_text)
        category = _guess_category(title, full_text)

        # Venue/location: use detected address when present, else defaults.
        if address:
            venue = DEFAULT_VENUE  # leave venue label alone — address tells the story
            location = address
        else:
            venue = DEFAULT_VENUE
            location = DEFAULT_LOCATION

        event = {
            "title": title,
            "date": date_iso,
            "time": start_time,
            "end_time": end_time,
            "venue": venue,
            "location": location,
            "description": _build_description(body or card["_listing_excerpt"]),
            "url": url,
            "price": price,
            "category": category,
            "source": SOURCE,
        }
        out.append({k: v for k, v in event.items() if v is not None})

    return out
