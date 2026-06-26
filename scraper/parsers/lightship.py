"""Lightship Brewery — Shopify products feed parser.

Live music events are sold as products in the 'events' collection at
``lightshipbrewery.ca/collections/events/products.json``. Each product
has the band name and date in the title, time/description in body_html,
and a per-product URL derived from its handle.

Allowlist: keep only products whose title contains "Live at Lightship"
or "live music" (case-insensitive). Everything else in the collection
(any future non-music items) is silently dropped.
"""
from __future__ import annotations

import html as _html
import logging
import re
from datetime import date as _date, datetime

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

LOG = logging.getLogger(__name__)

FEED_URL = (
    "https://lightshipbrewery.ca/collections/events/products.json?limit=250"
)
SOURCE = "lightship"
VENUE = "Lightship Brewery"
LOCATION = "93 Tannery Rd, Lunenburg"
EVENTS_PAGE_URL = "https://lightshipbrewery.ca/pages/events-calendar"
PRODUCT_BASE_URL = "https://lightshipbrewery.ca/products/"

UA = "Mozilla/5.0 (lunenburg-events scraper; +https://github.com/)"
TIMEOUT = 20

_ALLOWLIST_RE = re.compile(
    r"\blive\s+at\s+lightship\b|\blive\s+music\b",
    re.IGNORECASE,
)

# "7-10pm", "7:30-10pm" — start may lack am/pm, end always has it
_TIME_RANGE_RE = re.compile(
    r"\b(\d{1,2}(?::\d{2})?)\s*(am|pm)?\s*[-–]\s*(\d{1,2}(?::\d{2})?)\s*(am|pm)\b",
    re.IGNORECASE,
)
# "at 7pm" / "stars at 7pm" — but not "Doors at"
_AT_RE = re.compile(
    r"(?:show\s+(?:starts?|stars?)\s+)?at\s+(\d{1,2}(?::\d{2})?)\s*(am|pm)\b",
    re.IGNORECASE,
)
_DOORS_RE = re.compile(
    r"\bDoors?\s+(?:open\s+)?at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
    re.IGNORECASE,
)


def _normalize_hm(hhmm: str, mer: str) -> str:
    """'7', 'PM' → '7:00 PM'; '7:30', 'PM' → '7:30 PM'."""
    parts = hhmm.split(":")
    hh = int(parts[0])
    mm = parts[1] if len(parts) > 1 else "00"
    return f"{hh}:{mm} {mer.upper()}"


def _parse_times(body_text: str) -> tuple[str | None, str | None]:
    """Return (start_time, end_time) from cleaned description text."""
    # Strip "Doors at X" so it doesn't interfere with the "at" pattern.
    cleaned = _DOORS_RE.sub("", body_text)

    m = _TIME_RANGE_RE.search(cleaned)
    if m:
        start_hm, start_mer, end_hm, end_mer = m.group(1), m.group(2), m.group(3), m.group(4)
        # Start inherits end's am/pm when omitted (e.g. "7-10pm").
        effective_mer = start_mer if start_mer else end_mer
        return _normalize_hm(start_hm, effective_mer), _normalize_hm(end_hm, end_mer)

    m = _AT_RE.search(cleaned)
    if m:
        return _normalize_hm(m.group(1), m.group(2)), None

    return None, None


def _clean_body(body_html: str, max_chars: int = 400) -> str | None:
    """Strip HTML, unescape entities, collapse whitespace, truncate."""
    if not body_html:
        return None
    s = re.sub(r"<\s*(br|/p|/div|/li)\s*/?\s*>", " ", body_html, flags=re.IGNORECASE)
    s = BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    s = _html.unescape(s)
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_chars:
        s = s[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return s or None


def _parse_date(title: str) -> str | None:
    """Extract the event date from a product title like
    'Band Name - Live at Lightship - July 3, 2026'."""
    try:
        return dateparser.parse(title, fuzzy=True).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _format_price(variant: dict) -> str | None:
    try:
        amount = float(variant.get("price") or 0)
    except (ValueError, TypeError):
        return None
    if amount == 0:
        return "Free"
    return f"${int(amount)}" if amount == int(amount) else f"${amount:.2f}"


def fetch(session: requests.Session | None = None) -> list[dict]:
    sess = session or requests.Session()
    try:
        r = sess.get(FEED_URL, headers={"User-Agent": UA}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        LOG.error("lightship: feed fetch failed: %s", exc)
        return []

    products = data.get("products") or []
    out: list[dict] = []

    for p in products:
        title = (p.get("title") or "").strip()
        if not _ALLOWLIST_RE.search(title):
            LOG.debug("lightship: skipping (not live music) %r", title)
            continue

        date_iso = _parse_date(title)
        if not date_iso:
            LOG.debug("lightship: skipping %r (no parseable date)", title)
            continue

        handle = p.get("handle") or ""
        event_url = f"{PRODUCT_BASE_URL}{handle}" if handle else EVENTS_PAGE_URL

        body_text = _clean_body(p.get("body_html") or "")
        start_time, end_time = _parse_times(p.get("body_html") or "")

        variants = p.get("variants") or []
        price = _format_price(variants[0]) if variants else None

        event: dict = {
            "title": title,
            "date": date_iso,
            "venue": VENUE,
            "location": LOCATION,
            "url": event_url,
            "category": "music",
            "source": SOURCE,
        }
        if start_time:
            event["time"] = start_time
        if end_time:
            event["end_time"] = end_time
        if body_text:
            event["description"] = body_text
        if price:
            event["price"] = price

        out.append(event)

    return out
