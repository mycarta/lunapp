"""Musique Royale parser.

Musique Royale is a province-wide early-music / classical / jazz presenter.
It runs concerts across Nova Scotia (Halifax, Wolfville, Cape Breton, Bell
Island, Mahone Bay, ...). We only keep shows whose venue is in Lunenburg,
Riverport, Blue Rocks, or Stonehurst — Mahone Bay venues are intentionally
excluded because Mahone Bay will have its own app. The allowlist below is
venue-specific (we only enumerate venues we've actually seen them use); if
a new town-appropriate venue appears, add a tuple for it.

Two-step scrape:

  1. http://musiqueroyale.com/events/  — listing page. Each event card is a
     ``div.event-grid-item`` with a ``<span class="small">`` date/time line,
     a ``<b>`` title, and an ``<a href="/event/...">`` to the detail page.
  2. http://musiqueroyale.com/event/<year>/<slug>/  — detail page. After the
     title ``<h2>``, the following leaf divs/paragraphs are (in order):
       div   "<Venue Name> <Pretty Date – Time>"   (concatenated)
       p     "$<price> ..."
       p     "BUY TICKETS HERE"                    (links to canadahelps.org)
       p     "<street address>, <town>"
     The long description is inside ``div.eventbody``.

Venue filter: events that don't sit at one of the allowed Lunenburg-area
venues (per the spec) are dropped. We match a venue keyword AND a town
keyword in the combined ``venue + location`` haystack so that
"St. John's Anglican Church on Bell Island" doesn't sneak in just because
the venue-name substring matches the Lunenburg church.
"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

LOG = logging.getLogger(__name__)

BASE = "http://musiqueroyale.com"
LISTING_URL = f"{BASE}/events/"
SOURCE = "musique_royale"

UA = "Mozilla/5.0 (lunenburg-events scraper; +https://github.com/)"
TIMEOUT = 20
DETAIL_DELAY_SECONDS = 0.4  # be polite between detail-page fetches

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

_LISTING_DATETIME_RE = re.compile(
    r"^(?:[A-Za-z]+day)\s+(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2})\s+"
    r"(?P<year>\d{4})\s*,\s*(?P<time>\d{1,2}:\d{2}\s*[AP]M)\s*$"
)

_WEEKDAY_SPLIT_RE = re.compile(
    r"\s+(?:Sun(?:day)?|Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|"
    r"Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?)\s+\d",
    re.IGNORECASE,
)

_PRICE_RE = re.compile(r"(\$[\d.,]+(?:\s*[-–]\s*\$?\d[\d.,]*)?[^.\n]{0,180})")

# Towns we may want to trim off the trailing end of a venue name pulled from
# the page (e.g. "St. John's Anglican Church Lunenburg" -> "St. John's Anglican
# Church"). Listed multi-word first so the longest match wins.
_TRAILING_TOWNS = ["Mahone Bay", "Bell Island", "Lunenburg", "Riverport",
                   "Halifax", "Wolfville"]


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


def _parse_listing(html: str) -> list[dict]:
    """Return one dict per event card with date, time, listing title, detail URL."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for card in soup.select("div.event-grid-item"):
        a = card.find("a", href=True)
        if not a or "/event/" not in a["href"]:
            continue
        detail_url = urljoin(BASE, a["href"])
        span = card.find("span", class_="small")
        if not span:
            continue
        m = _LISTING_DATETIME_RE.match(span.get_text(" ", strip=True))
        if not m:
            continue
        try:
            d = dateparser.parse(f"{m['month']} {m['day']} {m['year']}").date()
        except (ValueError, dateparser.ParserError):
            continue
        title_b = card.find("b")
        listing_title = title_b.get_text(" ", strip=True) if title_b else None
        out.append({
            "_detail_url": detail_url,
            "date": d.strftime("%Y-%m-%d"),
            "time": re.sub(r"\s+", " ", m["time"]).strip(),
            "_listing_title": listing_title,
        })
    return out


def _has_block_children(el) -> bool:
    return any(
        getattr(c, "name", None) in ("div", "p", "h2", "h3", "h4", "ul", "ol", "table")
        for c in el.children
    )


def _trim_trailing_town(venue: str) -> str:
    for town in _TRAILING_TOWNS:
        if venue.lower().endswith(" " + town.lower()):
            return venue[: -(len(town) + 1)].rstrip()
    return venue


def _venue_from_maplink(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """If the detail page has a Google Maps link, derive (venue, location).

    Maps URL path looks like  ``/maps/place/St.+John's+Anglican+Church/@lat,lng,...``
    so we can read the venue name from the path, and the human-readable address
    is just the link text.
    """
    for a in soup.find_all("a", href=True):
        if "/maps/" not in a["href"]:
            continue
        location = a.get_text(" ", strip=True) or None
        m = re.search(r"/place/([^/@]+)", a["href"])
        venue = None
        if m:
            venue = unquote(m.group(1)).replace("+", " ").strip()
        return venue, location
    return None, None


def _parse_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    skip_titles = {"Featuring", "About", "Events", "About the Artists",
                   "Past Events"}
    title_h2 = next(
        (h for h in soup.find_all("h2")
         if h.get_text(strip=True) and h.get_text(strip=True) not in skip_titles),
        None,
    )
    if not title_h2:
        return {}
    title = title_h2.get_text(" ", strip=True)

    # Collect leaf-ish blocks between the title and the next 'Featuring'/'About'.
    leaf_lines: list[str] = []
    for el in title_h2.find_all_next():
        if el.name == "h2" and el.get_text(strip=True) in ("Featuring", "About"):
            break
        if el.name not in ("div", "p"):
            continue
        if _has_block_children(el):
            continue
        txt = el.get_text(" ", strip=True)
        if txt:
            leaf_lines.append(txt)

    venue = None
    location = None
    price = None
    for line in leaf_lines:
        if venue is None:
            # First leaf is "Venue + Pretty Date" concatenated. Split on the
            # weekday that begins the date portion.
            m = _WEEKDAY_SPLIT_RE.search(line)
            venue = _trim_trailing_town((line[:m.start()] if m else line).strip())
            continue
        if "$" in line and price is None:
            m = _PRICE_RE.search(line)
            price = (m.group(1) if m else line).strip().rstrip(".;, ")
            continue
        if location is None and re.match(r"^\d+\s+\w+", line):
            location = line
            continue
        if line.strip().upper() == "BUY TICKETS HERE":
            continue

    # Maps link is a more reliable address source when present (and gives us
    # the clean venue name from the URL path).
    map_venue, map_location = _venue_from_maplink(soup)
    if map_venue:
        venue = _trim_trailing_town(map_venue)
    if map_location:
        location = map_location

    # Ticket URL: prefer CanadaHelps, otherwise any "BUY TICKETS HERE" link.
    ticket_url = None
    for a in soup.find_all("a", href=True):
        host = urlparse(a["href"]).netloc.lower()
        if "canadahelps.org" in host:
            ticket_url = a["href"]
            break
    if not ticket_url:
        for a in soup.find_all("a", href=True):
            if a.get_text(" ", strip=True).upper() == "BUY TICKETS HERE":
                ticket_url = a["href"]
                break

    # Description from the eventbody container (if present).
    description = None
    body = soup.find("div", class_="eventbody")
    if body:
        paragraphs = [p.get_text(" ", strip=True) for p in body.find_all("p") if p.get_text(strip=True)]
        joined = " ".join(paragraphs) if paragraphs else body.get_text(" ", strip=True)
        joined = re.sub(r"\s+", " ", joined).strip()
        if len(joined) > 600:
            joined = joined[:599].rsplit(" ", 1)[0] + "…"
        description = joined or None

    return {
        "title": title,
        "venue": venue,
        "location": location,
        "description": description,
        "ticket_url": ticket_url,
        "price": price,
    }


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
    partial_events = _parse_listing(r.text)
    LOG.debug("musique_royale: %d events on listing page", len(partial_events))

    out: list[dict] = []
    for partial in partial_events:
        detail_url = partial["_detail_url"]
        try:
            dr = sess.get(detail_url, headers=headers, timeout=TIMEOUT)
            dr.raise_for_status()
        except Exception as exc:
            LOG.warning("musique_royale detail fetch failed (%s): %s", detail_url, exc)
            continue
        dr.encoding = "utf-8"
        detail = _parse_detail(dr.text)
        time.sleep(DETAIL_DELAY_SECONDS)

        venue = detail.get("venue")
        location = detail.get("location")
        if not _is_allowed(venue, location):
            LOG.debug(
                "musique_royale: dropping %r at %r / %r (outside Lunenburg area)",
                detail.get("title") or partial.get("_listing_title"),
                venue, location,
            )
            continue

        title = detail.get("title") or partial.get("_listing_title")
        event = {
            "title": title,
            "date": partial["date"],
            "time": partial["time"],
            "venue": venue,
            "location": location,
            "description": detail.get("description"),
            "url": detail_url,
            "ticket_url": detail.get("ticket_url"),
            "price": detail.get("price"),
            "category": "music",
            "source": SOURCE,
        }
        out.append({k: v for k, v in event.items() if v is not None})

    return out
