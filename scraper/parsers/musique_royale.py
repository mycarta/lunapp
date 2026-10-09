"""Musique Royale parser.

Musique Royale is a province-wide early-music / classical / jazz presenter.
It runs concerts across Nova Scotia (Halifax, Liverpool, Wolfville, Cape
Breton, Bell Island, Mahone Bay, ...). We only keep shows whose venue is in
Lunenburg, Riverport, Blue Rocks, or Stonehurst — Mahone Bay venues are
intentionally excluded because Mahone Bay will have its own app. The
allowlist below is venue-specific (we only enumerate venues we've actually
seen them use); if a new town-appropriate venue appears, add a tuple for it.

The site was redesigned in 2026; this parser targets the new markup. What
changed, in case it drifts again: the old listing used ``div.event-grid-item``
with a ``<span class="small">`` date and a ``<b>`` title, and detail URLs
looked like ``/event/<year>/<slug>/``.

Two-step scrape:

  1. https://musiqueroyale.com/events/ — listing page. Each event is an
     ``<article class="event-card">`` carrying ``data-event-date``
     (ISO yyyy-mm-dd) and ``data-event-url`` (date-prefixed, e.g.
     ``/event/2026/2026-10-08-divine-dowland/``), plus a
     ``span.event-date-label`` ("Thursday October 8 2026,  7:00 PM"), an
     ``h2.event-card__title`` and a ``p.event-card__excerpt``.

     The page has a "load more" control (``data-batch-size``) but it only
     reveals cards that are already in the HTML — every upcoming event is
     served in one response, and ``?page=2`` returns the same document. So
     there is no pagination to follow; if that ever changes, the giveaway
     is a listing that stops at exactly ``data-batch-size`` cards.

  2. The detail page, which carries the authoritative venue/price/address in
     one or more ``div.event-occurrence`` blocks:

       .event-occurrence__heading  "<Venue> <Weekday> <D> <Month> · <time>"
       .event-occurrence__body     "<price sentence> <street address>, <town>"

     One block per performance, so a programme toured to several venues
     yields several occurrences — each is emitted as its own event, and only
     the ones at allowed venues survive. Occurrence headings carry no year;
     it's taken from the listing card's ``data-event-date`` (adjusted if the
     occurrence month wraps into the next year).

Venue filter: we match a venue keyword AND a town keyword in the combined
``venue + location`` haystack so that "St. John's Anglican Church on Bell
Island" doesn't sneak in just because the venue-name substring matches the
Lunenburg church.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date as _date, datetime, timedelta
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

LOG = logging.getLogger(__name__)

BASE = "https://musiqueroyale.com"
LISTING_URL = f"{BASE}/events/"
SOURCE = "musique_royale"

UA = "Mozilla/5.0 (lunenburg-events scraper; +https://github.com/)"
TIMEOUT = 20
DETAIL_DELAY_SECONDS = 0.4  # be polite between detail-page fetches

# Don't fetch detail pages for cards far beyond the publishing window — the
# listing runs months ahead (next March, as of writing) and scrape.py only
# keeps the next 14 days. Generous enough that a seasonal announcement is
# still picked up well before it matters.
MAX_LOOKAHEAD_DAYS = 120

# (venue_substring, required_town_substring). Both must appear (case-insensitive,
# punctuation-insensitive) in the combined venue+location string for the event
# to be kept. Mahone Bay is matched as "mahone bay"; spaces survive normalization.
_ALLOWED_VENUE_TOWN: list[tuple[str, str]] = [
    ("school of the arts", "lunenburg"),
    ("st johns anglican", "lunenburg"),       # apostrophes/dots stripped before compare
    ("central united", "lunenburg"),
    ("lightship", "lunenburg"),
    ("old confidence", "riverport"),
    ("opera house", "lunenburg"),
]

# "Thursday October 8 2026,  7:00 PM" on the listing card.
_CARD_TIME_RE = re.compile(r"(\d{1,2}:\d{2}\s*[AP]\.?M\.?)", re.IGNORECASE)

# Occurrence heading: venue, then the weekday that starts the date portion.
_WEEKDAY_SPLIT_RE = re.compile(
    r"\s+(?:Sun(?:day)?|Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|"
    r"Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?)\s+\d",
    re.IGNORECASE,
)
# "Thursday 8 October" (day before month on occurrence headings).
_OCC_DAY_MONTH_RE = re.compile(
    r"\b(?P<day>\d{1,2})\s+(?P<month>January|February|March|April|May|June|July|"
    r"August|September|October|November|December)\b",
    re.IGNORECASE,
)

_PRICE_RE = re.compile(r"(\$[\d.,]+(?:\s*[-–]\s*\$?\d[\d.,]*)?[^.\n]{0,180})")
_PWYC_RE = re.compile(r"\bpay what you can\b[^.\n]{0,60}", re.IGNORECASE)
_FREE_RE = re.compile(r"\b(?:all events are free|admission is free|free admission)\b",
                      re.IGNORECASE)
# Trailing "6 Prince St, Lunenburg" in an occurrence body.
_ADDRESS_RE = re.compile(
    r"(\d{1,6}\s+[A-Za-z0-9.'\- ]{2,40},\s*[A-Za-z .'\-]{3,30})\s*$"
)

# Towns we may want to trim off the trailing end of a venue name pulled from
# the page (e.g. "St. John's Anglican Church Lunenburg" -> "St. John's Anglican
# Church"). Listed multi-word first so the longest match wins.
_TRAILING_TOWNS = ["Mahone Bay", "Bell Island", "Lunenburg", "Riverport",
                   "Halifax", "Wolfville", "Liverpool", "Chester"]


def _strip_punct_lower(s: str) -> str:
    """Lowercase and remove characters that vary across sources (apostrophes,
    dots, commas) so substring matching is robust."""
    return re.sub(r"[’'.,]", "", s or "").lower()


def _is_allowed(venue: str | None, location: str | None) -> bool:
    haystack = _strip_punct_lower(f"{venue or ''} {location or ''}")
    for venue_kw, town_kw in _ALLOWED_VENUE_TOWN:
        if venue_kw in haystack and town_kw in haystack:
            return True
    return False


def _trim_trailing_town(venue: str) -> str:
    for town in _TRAILING_TOWNS:
        if venue.lower().endswith(" " + town.lower()):
            return venue[: -(len(town) + 1)].rstrip()
    return venue


def _normalize_time(raw: str | None) -> str | None:
    """'7:00 pm' -> '7:00 PM'. Returns None when there's no time to show."""
    if not raw:
        return None
    return re.sub(r"\s+", " ", raw).strip().replace(".", "").upper()


def _parse_listing(html: str) -> list[dict]:
    """One dict per event card: ISO date, start time, title, detail URL."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for card in soup.select("article.event-card"):
        href = card.get("data-event-url") or ""
        iso = (card.get("data-event-date") or "").strip()
        if not href or not iso:
            # Fall back to the card's own link before giving up — the data-
            # attributes are new and could be dropped in a future redesign.
            a = card.find("a", href=True)
            href = href or (a["href"] if a and "/event/" in a["href"] else "")
            if not href or not iso:
                continue
        try:
            d = datetime.strptime(iso, "%Y-%m-%d").date()
        except ValueError:
            LOG.debug("musique_royale: unparseable data-event-date %r", iso)
            continue
        label = card.select_one(".event-date-label")
        m = _CARD_TIME_RE.search(label.get_text(" ", strip=True)) if label else None
        title_el = card.select_one(".event-card__title")
        out.append({
            "_detail_url": urljoin(BASE, href),
            "date": d.strftime("%Y-%m-%d"),
            "time": _normalize_time(m.group(1)) if m else None,
            "_listing_title": title_el.get_text(" ", strip=True) if title_el else None,
        })
    return out


def _venue_from_maplink(scope) -> tuple[str | None, str | None]:
    """If the block has a Google Maps link, derive (venue, location).

    Maps URLs are either ``/maps/place/St.+John's+Anglican+Church/@lat,lng``
    or ``/maps?q=lunenburg+school+of+the+arts``; the human-readable address
    is the link text either way.
    """
    for a in scope.find_all("a", href=True):
        if "/maps" not in a["href"]:
            continue
        location = a.get_text(" ", strip=True) or None
        m = re.search(r"/place/([^/@?]+)", a["href"]) or re.search(r"[?&]q=([^&]+)", a["href"])
        venue = None
        if m:
            venue = unquote(m.group(1)).replace("+", " ").strip()
        return venue, location
    return None, None


def _occurrence_date(heading_date_text: str, card_date: _date) -> _date | None:
    """Occurrence headings give "Thursday 8 October" with no year. Take the
    year from the listing card, bumping it when the occurrence month sits
    well before the card's (a tour crossing into January)."""
    m = _OCC_DAY_MONTH_RE.search(heading_date_text)
    if not m:
        return None
    for year in (card_date.year, card_date.year + 1):
        try:
            d = dateparser.parse(f"{m['month']} {m['day']} {year}").date()
        except (ValueError, OverflowError, dateparser.ParserError):
            return None
        if d >= card_date - timedelta(days=31):
            return d
    return d


def _parse_price(body_text: str) -> str | None:
    m = _PRICE_RE.search(body_text)
    if m:
        return m.group(1).strip().rstrip(".;, ")
    m = _PWYC_RE.search(body_text)
    if m:
        return m.group(0).strip().rstrip(".;, ")
    if _FREE_RE.search(body_text):
        return "Free"
    return None


def _parse_detail(html: str, card_date: _date) -> dict:
    """Return {"title", "description", "occurrences": [...]} for a detail page.

    Each occurrence is {"date", "time", "venue", "location", "price"}.
    """
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one(".event-detail-title") or soup.find("h1")
    title = title_el.get_text(" ", strip=True) if title_el else None

    description = None
    body = soup.find("div", class_="eventbody")
    if body:
        paragraphs = [p.get_text(" ", strip=True) for p in body.find_all("p")
                      if p.get_text(strip=True)]
        joined = " ".join(paragraphs) if paragraphs else body.get_text(" ", strip=True)
        joined = re.sub(r"\s+", " ", joined).strip()
        # The body opens with an "About" heading on most pages.
        joined = re.sub(r"^About\s+", "", joined)
        if len(joined) > 600:
            joined = joined[:599].rsplit(" ", 1)[0] + "…"
        description = joined or None

    occurrences: list[dict] = []
    for occ in soup.select(".event-occurrence"):
        heading_el = occ.select_one(".event-occurrence__heading")
        body_el = occ.select_one(".event-occurrence__body")
        heading = heading_el.get_text(" ", strip=True) if heading_el else ""
        body_text = re.sub(r"\s+", " ", body_el.get_text(" ", strip=True)) if body_el else ""
        if not heading:
            continue

        split = _WEEKDAY_SPLIT_RE.search(heading)
        venue = _trim_trailing_town((heading[:split.start()] if split else heading).strip())
        date_part = heading[split.start():] if split else ""
        d = _occurrence_date(date_part, card_date)
        tm = _CARD_TIME_RE.search(date_part)

        location = None
        m = _ADDRESS_RE.search(body_text)
        if m:
            location = m.group(1).strip()
        map_venue, map_location = _venue_from_maplink(occ)
        if map_location:
            location = map_location
        if map_venue and not venue:
            venue = _trim_trailing_town(map_venue)

        occurrences.append({
            "date": d.strftime("%Y-%m-%d") if d else None,
            "time": _normalize_time(tm.group(1)) if tm else None,
            "venue": venue or None,
            "location": location,
            "price": _parse_price(body_text),
        })

    return {"title": title, "description": description, "occurrences": occurrences}


def _ticket_url(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        if "canadahelps.org" in urlparse(a["href"]).netloc.lower():
            return a["href"]
    for a in soup.find_all("a", href=True):
        if "TICKET" in a.get_text(" ", strip=True).upper():
            return urljoin(BASE, a["href"])
    return None


def fetch(session: requests.Session | None = None) -> list[dict]:
    sess = session or requests.Session()
    headers = {"User-Agent": UA}

    try:
        r = sess.get(LISTING_URL, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as exc:
        LOG.error("musique_royale listing fetch failed: %s", exc)
        return []

    # Force UTF-8 — musiqueroyale.com serves UTF-8 content but its
    # Content-Type lacks a charset, so requests defaults to ISO-8859-1
    # and curly quotes / em-dashes come back as mojibake ("centuryâ€™s").
    r.encoding = "utf-8"
    cards = _parse_listing(r.text)
    LOG.debug("musique_royale: %d events on listing page", len(cards))
    if not cards:
        LOG.warning("musique_royale: listing page yielded no event cards — "
                    "the site markup may have changed again")
        return []

    today = datetime.now().date()
    out: list[dict] = []
    for card in cards:
        card_date = datetime.strptime(card["date"], "%Y-%m-%d").date()
        if card_date > today + timedelta(days=MAX_LOOKAHEAD_DAYS):
            LOG.debug("musique_royale: skipping %r on %s (beyond lookahead)",
                      card.get("_listing_title"), card["date"])
            continue

        detail_url = card["_detail_url"]
        try:
            dr = sess.get(detail_url, headers=headers, timeout=TIMEOUT)
            dr.raise_for_status()
        except Exception as exc:
            LOG.warning("musique_royale detail fetch failed (%s): %s", detail_url, exc)
            continue
        dr.encoding = "utf-8"
        detail = _parse_detail(dr.text, card_date)
        ticket_url = _ticket_url(dr.text)
        time.sleep(DETAIL_DELAY_SECONDS)

        title = detail.get("title") or card.get("_listing_title")
        # No occurrence blocks (layout drift): fall back to the listing card,
        # which has no venue — so it can only be dropped by the filter, but
        # log it loudly rather than failing silently.
        occurrences = detail.get("occurrences") or []
        if not occurrences:
            LOG.warning("musique_royale: no occurrence blocks on %s — "
                        "cannot determine venue for %r", detail_url, title)
            continue

        for occ in occurrences:
            if not _is_allowed(occ.get("venue"), occ.get("location")):
                LOG.debug("musique_royale: dropping %r at %r / %r "
                          "(outside Lunenburg area)",
                          title, occ.get("venue"), occ.get("location"))
                continue
            event = {
                "title": title,
                "date": occ.get("date") or card["date"],
                "time": occ.get("time") or card.get("time"),
                "venue": occ.get("venue"),
                "location": occ.get("location"),
                "description": detail.get("description"),
                "url": detail_url,
                "ticket_url": ticket_url,
                "price": occ.get("price"),
                "category": "music",
                "source": SOURCE,
            }
            out.append({k: v for k, v in event.items() if v is not None})

    return out
