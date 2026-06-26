"""Fisheries Museum of the Atlantic — Drupal 10 event listing parser.

Two-pass scrape:
  1. Listing page (/whats-on) — teaser cards give type, title, date, short
     description, and detail URL.
  2. Detail page — yields descriptive date (with time), pricing, and full
     description from the featured-summary field.

Filter: keep only "Event" and "Performance" types. Everything else
(Exhibition, Tour, Display, Recurring event, Course, Workshop, …) is
dropped unless the title clearly signals a one-off community event (which
the type filter already handles — nothing is added back).

Category mapping:
  Performance → "music"
  Event       → "community" (overridden to "festival" when title contains
                "festival" or "oktoberfest")
"""
from __future__ import annotations

import logging
import re
from datetime import date as _date, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

LOG = logging.getLogger(__name__)

BASE_URL = "https://fisheriesmuseum.novascotia.ca"
LISTING_URL = f"{BASE_URL}/whats-on"
SOURCE = "fisheries_museum"
VENUE = "Fisheries Museum of the Atlantic"
LOCATION = "68 Bluenose Drive, Lunenburg"

UA = "Mozilla/5.0 (lunenburg-events scraper; +https://github.com/)"
TIMEOUT = 20

_ALLOWED_TYPES = {"event", "performance"}

# "from 10:30am to 3pm" or "from 10am to 3pm"
_FROM_TO_RE = re.compile(
    r"\bfrom\s+(\d{1,2}(?::\d{2})?(?:am|pm))\s+to\s+(\d{1,2}(?::\d{2})?(?:am|pm))\b",
    re.IGNORECASE,
)
# "at 1pm"
_AT_RE = re.compile(
    r"\bat\s+(\d{1,2}(?::\d{2})?(?:am|pm))\b",
    re.IGNORECASE,
)
_FESTIVAL_RE = re.compile(r"\b(festival|oktoberfest)\b", re.IGNORECASE)


def _normalize_time(raw: str) -> str:
    """'10:30am' / '3pm' → '10:30 AM' / '3:00 PM'."""
    s = raw.strip().upper()
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?(AM|PM)$", s)
    if not m:
        return raw.strip()
    hh = int(m.group(1))
    mm = m.group(2) or "00"
    mer = m.group(3)
    return f"{hh}:{mm} {mer}"


def _parse_times(desc_date: str) -> tuple[str | None, str | None]:
    m = _FROM_TO_RE.search(desc_date)
    if m:
        return _normalize_time(m.group(1)), _normalize_time(m.group(2))
    m = _AT_RE.search(desc_date)
    if m:
        return _normalize_time(m.group(1)), None
    return None, None


def _parse_date(raw: str) -> str | None:
    """'1 Jul 2026' → '2026-07-01'. Returns None on failure."""
    try:
        return dateparser.parse(raw.strip(), fuzzy=True).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _category(event_type: str, title: str) -> str:
    if event_type.lower() == "performance":
        return "music"
    if _FESTIVAL_RE.search(title):
        return "festival"
    return "community"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _field_text(soup: BeautifulSoup, field_name: str) -> str | None:
    """Extract text from a Drupal field by its --name-- suffix."""
    el = soup.select_one(f"[class*='field--name-{field_name}']")
    if not el:
        return None
    return el.get_text(" ", strip=True) or None


def _fetch(session: requests.Session, url: str) -> str | None:
    try:
        r = session.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception as exc:
        LOG.warning("fisheries_museum: fetch failed for %s: %s", url, exc)
        return None


def _scrape_detail(session: requests.Session, url: str) -> dict:
    """Fetch a detail page and return time, end_time, price, description."""
    html = _fetch(session, url)
    if not html:
        return {}
    s = _soup(html)

    desc_date = _field_text(s, "field-descriptive-date") or ""
    start_time, end_time = _parse_times(desc_date)

    raw_price = _field_text(s, "field-pricing")
    price = raw_price.strip() if raw_price else None

    description = _field_text(s, "field-featured-summary")
    if description and len(description) > 500:
        description = description[:499].rsplit(" ", 1)[0] + "…"

    result: dict = {}
    if start_time:
        result["time"] = start_time
    if end_time:
        result["end_time"] = end_time
    if price:
        result["price"] = price
    if description:
        result["description"] = description
    return result


def fetch(session: requests.Session | None = None) -> list[dict]:
    sess = session or requests.Session()
    html = _fetch(sess, LISTING_URL)
    if not html:
        return []

    s = _soup(html)
    cards = s.select(".teaser-card")
    if not cards:
        LOG.warning("fisheries_museum: no teaser cards found on listing page")
        return []

    out: list[dict] = []
    for card in cards:
        type_el = card.select_one(".teaser-card__tags li")
        event_type = type_el.get_text(strip=True) if type_el else ""
        if event_type.lower() not in _ALLOWED_TYPES:
            LOG.debug("fisheries_museum: skipping type %r", event_type)
            continue

        title_el = card.select_one(".teaser-card__title-link a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        href = title_el.get("href", "")
        detail_url = urljoin(BASE_URL, href)

        date_el = card.select_one(".teaser-card__date")
        date_iso = _parse_date(date_el.get_text(strip=True) if date_el else "")
        if not date_iso:
            LOG.debug("fisheries_museum: skipping %r (no parseable date)", title)
            continue

        detail = _scrape_detail(sess, detail_url)

        event = {
            "title": title,
            "date": date_iso,
            "venue": VENUE,
            "location": LOCATION,
            "url": detail_url,
            "category": _category(event_type, title),
            "source": SOURCE,
            **detail,
        }
        out.append(event)
        LOG.debug("fisheries_museum: +%r (%s)", title, date_iso)

    return out
