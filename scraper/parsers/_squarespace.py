"""Shared parser for Squarespace event-list pages.

Old Confidence Lodge and LAMP both publish events via Squarespace's stock
event-list block, so the DOM shape is identical. Each event is an
<article class="eventlist-event eventlist-event--upcoming ..."> containing:

  - h1.eventlist-title > a            -> title + event detail href
  - time.event-date[datetime]         -> ISO date (YYYY-MM-DD)
  - time.event-time-localized-start   -> start time (text, e.g. "7:00 p.m.")
  - time.event-time-localized-end     -> end time   (text)
  - li.eventlist-meta-address         -> venue text + (map) link with q=address
  - a.eventlist-meta-export-ical      -> ICS download (relative URL)
  - div.eventlist-excerpt | div.eventlist-description -> body HTML
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (lunenburg-events scraper; +https://github.com/)"
TIMEOUT = 20

TICKETING_DOMAINS = (
    "canadahelps.org",
    "onstagedirect.com",
    "ticketpro.ca",
    "showpass.com",
    "eventbrite.ca",
    "eventbrite.com",
    "ticketscene.ca",
    "tickettailor.com",
)

TICKET_TEXT_HINTS = ("ticket", "buy ", "rsvp", "register", "tix")

PRICE_RE = re.compile(r"(\$[\d][\d.,]*(?:\s*[-–]\s*\$?\d[\d.,]*)?[^\n<]{0,80})")
FREE_RE = re.compile(r"\b(free|no charge|pwyc|pay what you can)\b", re.IGNORECASE)


def _clean_time(text: str | None) -> str | None:
    if not text:
        return None
    # Squarespace renders "7:00 p.m." — normalize to "7:00 PM"
    t = text.replace(" ", " ").replace(" ", " ").strip()
    t = re.sub(r"\s+", " ", t)
    t = t.replace("a.m.", "AM").replace("p.m.", "PM")
    t = t.replace("a.m", "AM").replace("p.m", "PM")
    return t or None


def _address_from_maplink(addr_li) -> tuple[str | None, str | None]:
    """Return (venue_name, full_address) from an eventlist-meta-address <li>."""
    if not addr_li:
        return None, None
    link = addr_li.find("a", class_="eventlist-meta-address-maplink")
    full_address = None
    if link and link.get("href"):
        qs = parse_qs(urlparse(link["href"]).query)
        if "q" in qs and qs["q"]:
            full_address = qs["q"][0].strip()
    # Venue name is the text content of the <li> with the maplink removed
    clone = BeautifulSoup(str(addr_li), "html.parser").find()
    inner_link = clone.find("a")
    if inner_link:
        inner_link.decompose()
    venue_name = clone.get_text(" ", strip=True) or None
    return venue_name, full_address


def _find_ticket_url(desc_container, source_host: str) -> str | None:
    if not desc_container:
        return None
    candidates = desc_container.find_all("a", href=True)
    # First pass: external ticketing-domain hrefs.
    for a in candidates:
        href = a["href"]
        if not href.startswith("http"):
            continue
        host = urlparse(href).netloc.lower()
        if any(d in host for d in TICKETING_DOMAINS):
            return href
    # Second pass: external link whose visible text hints at tickets.
    for a in candidates:
        href = a["href"]
        if not href.startswith("http"):
            continue
        if source_host and source_host in urlparse(href).netloc.lower():
            continue
        txt = a.get_text(" ", strip=True).lower()
        if any(h in txt for h in TICKET_TEXT_HINTS):
            return href
    return None


def _extract_price(desc_container) -> str | None:
    if not desc_container:
        return None
    text = desc_container.get_text("\n", strip=True)
    for line in text.split("\n"):
        if "$" in line:
            m = PRICE_RE.search(line)
            if m:
                return m.group(1).strip().rstrip(".;,")
    if FREE_RE.search(text):
        return "Free"
    return None


def _excerpt_text(desc_container, max_chars: int = 600) -> str | None:
    if not desc_container:
        return None
    # Prefer the longest <p> as the human-readable blurb; fall back to all text.
    paragraphs = [p.get_text(" ", strip=True) for p in desc_container.find_all("p")]
    paragraphs = [p for p in paragraphs if p and not p.startswith("$") and len(p) > 30]
    body = max(paragraphs, key=len) if paragraphs else desc_container.get_text(" ", strip=True)
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) > max_chars:
        body = body[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return body or None


def _guess_category(title: str, description: str | None) -> str:
    title_low = title.lower()
    # Strip phrases that are music-genre descriptors but contain category keywords.
    desc_low = re.sub(
        r"\b(musical\s+theatre|musical\s+theater|art\s+song)\b",
        "",
        (description or "").lower(),
    )
    blob = f"{title_low} {desc_low}"
    if re.search(r"\b(silent film|film|movie|cinema|screening)\b", blob):
        return "film"
    # Theater only triggers on title or explicit production language in description.
    if re.search(r"\b(hedwig|drag show)\b", title_low) or re.search(
        r"\b(stage play|theatre production|theater production|live theatre|live theater)\b",
        desc_low,
    ):
        return "theater"
    if re.search(r"\b(exhibition|exhibit|gallery opening|gallery|artist talk|lecture)\b", blob):
        return "arts"
    if re.search(r"\bfestival\b", blob):
        return "festival"
    if re.search(r"\b(dance|ceili|ceilidh)\b", blob):
        return "dance"
    return "music"


def parse_squarespace_events(
    *,
    page_url: str,
    source: str,
    venue_display: str | None = None,
    allowed_venue_substrings: list[str] | None = None,
    session: requests.Session | None = None,
) -> list[dict]:
    """Fetch and parse a Squarespace event-list page.

    Args:
        page_url: Full URL of the events listing page.
        source: Source slug to put in each event's ``source`` field.
        venue_display: Override venue name in the output (e.g. "LAMP" instead
            of the full Squarespace business name). If None, the parser uses
            the venue name as it appears on the page.
        allowed_venue_substrings: If provided, drop events whose venue/address
            does not contain any of these substrings (case-insensitive).
            Lets us discard out-of-area shows that the same venue cross-lists.
    """
    sess = session or requests.Session()
    resp = sess.get(page_url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    base = page_url
    source_host = urlparse(page_url).netloc.lower()
    events: list[dict] = []

    for article in soup.select("article.eventlist-event--upcoming"):
        title_a = article.select_one("h1.eventlist-title a")
        if not title_a:
            continue
        title = title_a.get_text(" ", strip=True)
        event_url = urljoin(base, title_a.get("href", ""))

        date_el = article.select_one("time.event-date")
        date_iso = date_el.get("datetime") if date_el else None
        if not date_iso:
            continue

        start_el = article.select_one("time.event-time-localized-start")
        end_el = article.select_one("time.event-time-localized-end")
        start_time = _clean_time(start_el.get_text() if start_el else None)
        end_time = _clean_time(end_el.get_text() if end_el else None)

        addr_li = article.select_one("li.eventlist-meta-address")
        venue_name, full_address = _address_from_maplink(addr_li)

        # Drop events at out-of-area venues if a whitelist is configured.
        if allowed_venue_substrings:
            haystack = f"{venue_name or ''} {full_address or ''}".lower()
            if not any(s.lower() in haystack for s in allowed_venue_substrings):
                continue

        ics_a = article.select_one("a.eventlist-meta-export-ical")
        ics_url = urljoin(base, ics_a["href"]) if (ics_a and ics_a.get("href")) else None

        desc_container = article.select_one(".eventlist-excerpt") or article.select_one(
            ".eventlist-description"
        )
        description = _excerpt_text(desc_container)
        ticket_url = _find_ticket_url(desc_container, source_host)
        price = _extract_price(desc_container)
        category = _guess_category(title, description)

        event = {
            "title": title,
            "date": date_iso,
            "time": start_time,
            "end_time": end_time,
            "venue": venue_display or venue_name,
            "location": full_address,
            "description": description,
            "url": event_url,
            "ticket_url": ticket_url,
            "price": price,
            "category": category,
            "source": source,
            "ics_url": ics_url,
        }
        # Strip keys whose value is None to keep the JSON tidy.
        events.append({k: v for k, v in event.items() if v is not None})

    return events
