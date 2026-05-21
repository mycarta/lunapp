"""Main scraper entry point for the Lunenburg Events app.

Runs every configured source parser, merges and deduplicates the results,
filters to the next 14 days, and writes the unified ``events.json`` that
the frontend reads.
"""
from __future__ import annotations

import html
import json
import logging
import re
import sys
from datetime import date as _date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from parsers import ALL_PARSERS

LOG = logging.getLogger("scrape")

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "events.json"
INDEX_HTML_PATH = REPO_ROOT / "index.html"
HALIFAX = ZoneInfo("America/Halifax")
WINDOW_DAYS = 14

SITE_URL = "https://mycarta.github.io/lunapp/"

# Comment markers in index.html that bracket the regions the scraper rewrites
# on each run. Keep these in sync with the placeholders in index.html.
_JSONLD_RE = re.compile(
    r"(<!-- BEGIN:JSON-LD -->)(.*?)(<!-- END:JSON-LD -->)", re.DOTALL,
)
_NOSCRIPT_RE = re.compile(
    r"(<!-- BEGIN:NOSCRIPT-EVENTS -->)(.*?)(<!-- END:NOSCRIPT-EVENTS -->)",
    re.DOTALL,
)

_WEEKDAY_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday"]
_MONTH_LONG = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]

# --- Address normalization ----------------------------------------------------
# Squarespace gives us full mailing addresses like
#   "97 Kaulbach Street, PO Box 309 Lunenburg, NS, B0J 2C0 Canada"
# but the UI just needs "97 Kaulbach St, Lunenburg". This trims the noise.

_COUNTRY_RE = re.compile(r",?\s*\bCanada\b\.?", re.IGNORECASE)
_POSTAL_RE = re.compile(r"\b[A-Z]\d[A-Z]\s*\d[A-Z]\d\b")
_PROV_RE = re.compile(r",?\s*\b(NS|NB|PE|NL|ON|QC|BC|AB|SK|MB|YT|NT|NU)\b\.?")
_PO_BOX_RE = re.compile(r"\bP\.?\s*O\.?\s*Box\s+\d+\b", re.IGNORECASE)

_STREET_TYPES = {
    "street": "St",
    "avenue": "Ave",
    "road": "Rd",
    "boulevard": "Blvd",
    "drive": "Dr",
    "lane": "Ln",
    "crescent": "Cres",
    "court": "Ct",
    "place": "Pl",
    "highway": "Hwy",
}
_STREET_RE = re.compile(
    r"\b(" + "|".join(_STREET_TYPES) + r")\b\.?", re.IGNORECASE
)

# When the stripped address has no comma, fall back to inserting one before a
# recognized Lunenburg-area town name so "3831 Nova Scotia 332 Riverport"
# becomes "3831 Nova Scotia 332, Riverport".
_KNOWN_TOWNS = [
    "Mahone Bay",  # multi-word entries must come first
    "Lunenburg",
    "Riverport",
    "Chester",
    "Bridgewater",
    "Blue Rocks",
    "Indian Path",
    "Garden Lots",
]


def normalize_location(loc: str | None) -> str | None:
    if not loc:
        return None
    s = loc
    s = _COUNTRY_RE.sub("", s)
    s = _POSTAL_RE.sub("", s)
    s = _PROV_RE.sub("", s)
    s = _PO_BOX_RE.sub("", s)
    s = _STREET_RE.sub(lambda m: _STREET_TYPES[m.group(1).lower()], s)
    # Squeeze repeated commas/whitespace produced by the deletions above.
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"(\s*,\s*)+", ", ", s)
    s = s.strip().strip(",").strip()
    if "," not in s:
        for town in sorted(_KNOWN_TOWNS, key=len, reverse=True):
            suffix = " " + town.lower()
            if s.lower().endswith(suffix):
                s = s[: -len(town)].rstrip() + ", " + town
                break
    return s or None


def _dedupe_key(event: dict) -> tuple[str, str]:
    title = re.sub(r"[^a-z0-9]+", "", (event.get("title") or "").lower())
    return (event.get("date", ""), title)


def _time_sort_key(event: dict) -> tuple[str, str]:
    # Within a day, sort by 24h-converted start time; events with no time go last.
    t = (event.get("time") or "").strip().upper()
    m = re.match(r"^(\d{1,2}):(\d{2})\s*(AM|PM)?", t)
    if not m:
        return (event.get("date", ""), "99:99")
    hh, mm, mer = int(m.group(1)), m.group(2), m.group(3)
    if mer == "PM" and hh != 12:
        hh += 12
    elif mer == "AM" and hh == 12:
        hh = 0
    return (event.get("date", ""), f"{hh:02d}:{mm}")


def collect_events(session: requests.Session) -> list[dict]:
    all_events: list[dict] = []
    for parser in ALL_PARSERS:
        name = parser.__name__.rsplit(".", 1)[-1]
        try:
            got = parser.fetch(session=session)
            LOG.info("[%s] %d events", name, len(got))
            all_events.extend(got)
        except Exception as exc:
            LOG.error("[%s] failed: %s", name, exc, exc_info=True)
    return all_events


def filter_to_window(events: list[dict], today: datetime) -> list[dict]:
    start = today.date()
    end = start + timedelta(days=WINDOW_DAYS)
    kept = []
    for e in events:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        if start <= d <= end:
            kept.append(e)
    return kept


def deduplicate(events: list[dict]) -> list[dict]:
    seen: dict[tuple, dict] = {}
    for e in events:
        key = _dedupe_key(e)
        if key not in seen:
            seen[key] = e
    return list(seen.values())


# --- SEO: schema.org JSON-LD + noscript fallback ------------------------------
# Both are written into index.html by the scraper so search crawlers see real
# event content without needing to execute JavaScript. JSON-LD goes in <head>
# for Google's structured-data parser; the noscript block sits in <main> for
# any crawler that ignores JSON-LD but reads visible body text.

_TIME_PARSE_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?\s*$", re.IGNORECASE)
_PRICE_NUM_RE = re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)")
_FREE_RE = re.compile(r"\bfree\b", re.IGNORECASE)
_PWYC_RE = re.compile(r"\b(pwyc|pay[- ]what[- ]you[- ]can)\b", re.IGNORECASE)


def _combine_iso(date_str: str, time_str: str | None) -> str:
    """Combine a "YYYY-MM-DD" date and a "7:30 PM"-style time into an ISO 8601
    string in America/Halifax. Returns the bare date if time is unparseable —
    that's still valid schema.org/Event.startDate."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return date_str
    if not time_str:
        return date_str
    m = _TIME_PARSE_RE.match(time_str.strip())
    if not m:
        return date_str
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    mer = (m.group(3) or "").upper()
    if mer == "PM" and hh != 12:
        hh += 12
    elif mer == "AM" and hh == 12:
        hh = 0
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return date_str
    dt = datetime(d.year, d.month, d.day, hh, mm, tzinfo=HALIFAX)
    return dt.isoformat()


def _parse_offers(price: str | None, ticket_url: str | None) -> dict | None:
    """Best-effort price → schema.org Offer / AggregateOffer.

    Returns None when the price string has no extractable amount and isn't
    a recognized "Free" or "PWYC" — better to emit no offer than a wrong one.
    """
    base: dict = {"priceCurrency": "CAD"}
    if ticket_url:
        base["url"] = ticket_url

    if price:
        if _FREE_RE.search(price):
            return {"@type": "Offer", "price": "0", **base}
        if _PWYC_RE.search(price):
            return {"@type": "Offer", "price": "0", "name": "Pay what you can", **base}
        nums = [float(m) for m in _PRICE_NUM_RE.findall(price)]
        if nums:
            lo, hi = min(nums), max(nums)
            fmt = lambda n: str(int(n)) if n.is_integer() else f"{n:.2f}"
            if lo == hi:
                return {"@type": "Offer", "price": fmt(lo), **base}
            return {
                "@type": "AggregateOffer",
                "lowPrice": fmt(lo),
                "highPrice": fmt(hi),
                **base,
            }
    return None


def _event_jsonld(event: dict) -> dict:
    start = _combine_iso(event["date"], event.get("time"))
    obj: dict = {
        "@type": "Event",
        "name": event["title"],
        "startDate": start,
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
    }
    if event.get("end_time"):
        obj["endDate"] = _combine_iso(event["date"], event["end_time"])

    place: dict = {"@type": "Place", "name": event.get("venue") or "Lunenburg"}
    if event.get("location"):
        place["address"] = event["location"]
    obj["location"] = place

    if event.get("url"):
        obj["url"] = event["url"]
    if event.get("description"):
        obj["description"] = event["description"]

    offers = _parse_offers(event.get("price"), event.get("ticket_url"))
    if offers:
        obj["offers"] = offers
    return obj


def render_jsonld_script(events: list[dict]) -> str:
    """Render the <script type='application/ld+json'> tag content. Always emits
    a single ItemList wrapping the events so the whole page is one structured
    record — easier for Google to attribute than a bare array."""
    items = [_event_jsonld(e) for e in events]
    payload = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "Upcoming events in Lunenburg, Nova Scotia",
        "url": SITE_URL,
        "numberOfItems": len(items),
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "item": item}
            for i, item in enumerate(items)
        ],
    }
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    return f'<script type="application/ld+json">\n{body}\n  </script>'


def _format_noscript_date(date_str: str) -> str:
    try:
        d = _date.fromisoformat(date_str)
    except ValueError:
        return date_str
    return f"{_WEEKDAY_LONG[d.weekday()]}, {_MONTH_LONG[d.month - 1]} {d.day}"


def render_noscript_html(events: list[dict]) -> str:
    """Plain semantic markup of the event list — what a crawler without JS
    sees. Grouped by date with weekday headings."""
    if not events:
        return (
            "<p>No upcoming events in the next two weeks. "
            "Check back soon — new events are added regularly.</p>"
        )

    # Group events by date in original (already date-sorted) order.
    by_date: dict[str, list[dict]] = {}
    for e in events:
        by_date.setdefault(e["date"], []).append(e)

    lines = ['<h2>Upcoming events in Lunenburg</h2>']
    for date_str, day_events in by_date.items():
        lines.append(f'<h3>{html.escape(_format_noscript_date(date_str))}</h3>')
        lines.append('<ul>')
        for ev in day_events:
            title = html.escape(ev.get("title") or "Untitled event")
            venue = html.escape(ev.get("venue") or "")
            time = html.escape(ev.get("time") or "")
            bits = []
            if time:
                bits.append(f"<strong>{time}</strong>")
            bits.append(title)
            if venue:
                bits.append(f"at {venue}")
            lines.append(f"  <li>{' — '.join(bits)}</li>")
        lines.append('</ul>')
    return "\n      ".join(lines)


def update_index_html(events: list[dict]) -> bool:
    """Rewrite the JSON-LD and noscript regions in index.html. Returns True
    when the file actually changed (so the caller can short-circuit a no-op
    write that would just confuse the workflow's git-diff check)."""
    if not INDEX_HTML_PATH.exists():
        LOG.warning("index.html not found at %s — skipping HTML update",
                    INDEX_HTML_PATH)
        return False
    original = INDEX_HTML_PATH.read_text(encoding="utf-8")

    jsonld_script = render_jsonld_script(events)
    noscript_body = render_noscript_html(events)

    def jsonld_sub(m: re.Match) -> str:
        return f"{m.group(1)}\n  {jsonld_script}\n  {m.group(3)}"

    def noscript_sub(m: re.Match) -> str:
        return f"{m.group(1)}\n      {noscript_body}\n      {m.group(3)}"

    updated = _JSONLD_RE.sub(jsonld_sub, original, count=1)
    updated = _NOSCRIPT_RE.sub(noscript_sub, updated, count=1)

    if updated == original:
        return False
    INDEX_HTML_PATH.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    session = requests.Session()

    events = collect_events(session)
    LOG.info("collected %d total events from %d sources", len(events), len(ALL_PARSERS))

    now = datetime.now(HALIFAX)
    events = filter_to_window(events, now)
    events = deduplicate(events)
    events.sort(key=_time_sort_key)

    for e in events:
        normalized = normalize_location(e.get("location"))
        if normalized:
            e["location"] = normalized
        else:
            e.pop("location", None)

    payload = {
        "events": events,
        "last_updated": now.replace(microsecond=0).isoformat(),
    }
    OUTPUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    LOG.info("wrote %d events to %s", len(events), OUTPUT_PATH)

    html_changed = update_index_html(events)
    LOG.info("index.html %s", "rewritten" if html_changed else "unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
