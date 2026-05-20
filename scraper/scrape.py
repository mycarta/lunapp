"""Main scraper entry point for the Lunenburg Events app.

Runs every configured source parser, merges and deduplicates the results,
filters to the next 14 days, and writes the unified ``events.json`` that
the frontend reads.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from parsers import ALL_PARSERS

LOG = logging.getLogger("scrape")

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "events.json"
HALIFAX = ZoneInfo("America/Halifax")
WINDOW_DAYS = 14

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
