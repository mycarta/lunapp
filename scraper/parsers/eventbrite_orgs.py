"""Eventbrite organizer-feed parser.

Some Lunenburg-area events live only on Eventbrite — small organizations,
visiting acts, one-off fundraisers — and aren't covered by any of the
venue-specific parsers. Rather than hand-curate each one in
``manual_seeds.json``, we pull them straight from Eventbrite.

The catch: Eventbrite's *documented* public API (``eventbriteapi.com/v3``)
requires an OAuth token. But the *web-frontend* API at
``eventbrite.com/api/v3``, which Eventbrite's own site uses for its
organizer pages, returns the same JSON unauthenticated. That's what we
use here. The response shape is stable and includes venue + ticket
pricing in a single round trip.

How it works
------------

  1. Read a list of Eventbrite organizer IDs from
     ``eventbrite_organizers.json`` at the repo root. An empty file (or
     missing file) makes this parser a no-op.
  2. For each organizer, GET
     ``eventbrite.com/api/v3/organizers/{id}/events/?status=live&expand=venue,ticket_classes``.
     Pages until ``pagination.has_more_items`` is false (capped at
     ``PAGE_LIMIT`` as a safety net against runaway iteration).
  3. Filter to Lunenburg-area venues — currently Lunenburg, Riverport,
     Blue Rocks, and Stonehurst. Mahone Bay is intentionally excluded —
     that town is getting its own dedicated app.
  4. Map to our event-dict schema, the same shape the other parsers
     return.

Adding an organizer
-------------------

Open any of their event pages, view source, find the
``<script type="application/ld+json">`` block. The ``organizer.url``
ends with ``/o/{numeric_id}`` — that's the ID to add to
``eventbrite_organizers.json``.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

import requests

LOG = logging.getLogger(__name__)

SOURCE = "eventbrite"
CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "eventbrite_organizers.json"
)
API_URL_TEMPLATE = (
    "https://www.eventbrite.com/api/v3/organizers/{id}/events/"
    "?status=live&order_by=start_asc&expand=venue,ticket_classes"
)
UA = "Mozilla/5.0 (lunenburg-events scraper; +https://github.com/)"
TIMEOUT = 20
PAGE_LIMIT = 5  # 250 future events per org is plenty; this is a runaway guard

# Lunenburg-area towns only — Lunenburg, Riverport, Blue Rocks, Stonehurst.
# Mahone Bay is excluded by project policy (separate app planned for that
# town). Match is case-insensitive against the venue's address.city field.
_ALLOWED_CITIES = {"lunenburg", "riverport", "blue rocks", "stonehurst"}

# Eventbrite category IDs → Lunapp categories. IDs from
# https://www.eventbrite.com/platform/api#/reference/category. Anything
# not listed falls through to keyword inference in _guess_category.
_CATEGORY_MAP = {
    "103": "music",
    "105": "theater",   # Performing & Visual Arts
    "108": "film",      # Film, Media & Entertainment
    "109": "festival",
    "113": "community", # Community & Culture
    "116": "community", # Religion & Spirituality
    "119": "community", # Family & Education
}


def _load_organizer_ids() -> list[dict]:
    if not CONFIG_PATH.exists():
        LOG.debug("eventbrite_organizers.json not found; parser is a no-op")
        return []
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        LOG.error("eventbrite_organizers.json failed to load: %s", exc)
        return []
    organizers = data.get("organizers", []) if isinstance(data, dict) else []
    return [o for o in organizers if isinstance(o, dict) and o.get("id")]


def _is_lunenburg_area(venue: dict | None) -> bool:
    if not isinstance(venue, dict):
        return False
    addr = venue.get("address") or {}
    if not isinstance(addr, dict):
        return False
    city = (addr.get("city") or "").strip().lower()
    return city in _ALLOWED_CITIES


def _format_time(local_iso: str | None) -> str | None:
    """``2026-05-30T19:00:00`` → ``7:00 PM``. Matches the time format other
    parsers emit so the frontend's sort key works uniformly."""
    if not local_iso:
        return None
    try:
        dt = datetime.fromisoformat(local_iso)
    except ValueError:
        return None
    return dt.strftime("%I:%M %p").lstrip("0")


def _format_price(event: dict) -> str | None:
    """Pick a representative price from the event's ticket tiers.

    Eventbrite lists "Additional Donation" entries as ticket classes, which
    would inflate a naive min/max range. We skip donation-flavored entries
    when picking the canonical price; if every tier looks donation-y, we
    fall back to the first tier.
    """
    if event.get("is_free"):
        return "Free"
    tcs = event.get("ticket_classes") or []
    if not tcs:
        return None
    candidates = [
        tc for tc in tcs
        if "donation" not in (tc.get("name") or "").lower()
        and "contribution" not in (tc.get("name") or "").lower()
    ]
    if not candidates:
        candidates = tcs
    cost = (candidates[0].get("cost") or {})
    disp = cost.get("display") or ""
    # "CA$35.00" → "$35.00". Other currencies prefix similarly (US$, AU$).
    disp = re.sub(r"^[A-Z]{2}", "", disp).strip()
    return disp or None


def _guess_category(event: dict) -> str:
    cid = str(event.get("category_id") or "")
    if cid in _CATEGORY_MAP:
        return _CATEGORY_MAP[cid]
    blob = (
        ((event.get("name") or {}).get("text") or "") + " "
        + (event.get("summary") or "")
    ).lower()
    if re.search(r"\b(concert|jazz|band|orchestra|fiddle|recital|singer|choir)\b", blob):
        return "music"
    if re.search(r"\b(theatre|theater|play|opera|musical)\b", blob):
        return "theater"
    if re.search(r"\b(film|movie|cinema|screening)\b", blob):
        return "film"
    if "festival" in blob:
        return "festival"
    if re.search(r"\b(exhibition|exhibit|gallery|artist talk|lecture)\b", blob):
        return "arts"
    return "community"


def _format_address(venue: dict) -> str | None:
    addr = venue.get("address") or {}
    display = addr.get("localized_address_display")
    if display:
        return display
    parts = [addr.get("address_1"), addr.get("city"),
             addr.get("region"), addr.get("postal_code")]
    return ", ".join(p for p in parts if p) or None


def _to_event_dict(eb: dict, include_all_venues: bool = False) -> dict | None:
    venue = eb.get("venue")
    # The default city filter keeps the app focused on Lunenburg-area venues.
    # Organizers can opt out by setting "include_all_venues": true in
    # eventbrite_organizers.json — used for county-wide community events
    # (e.g. Lunenburg PRIDE) whose schedule intentionally spans the whole
    # county and would otherwise be silently dropped.
    if not include_all_venues and not _is_lunenburg_area(venue):
        return None
    start = eb.get("start") or {}
    local_start = start.get("local")
    if not local_start:
        return None
    name = ((eb.get("name") or {}).get("text") or "").strip()
    if not name:
        return None

    description = ((eb.get("description") or {}).get("text") or "").strip()
    description = re.sub(r"\s+", " ", description)
    if len(description) > 500:
        description = description[:499].rsplit(" ", 1)[0] + "…"

    end_local = (eb.get("end") or {}).get("local")
    event = {
        "title": name,
        "date": local_start[:10],
        "time": _format_time(local_start),
        "end_time": _format_time(end_local) if not eb.get("hide_end_date") else None,
        "venue": (venue.get("name") or "").strip() or None,
        "location": _format_address(venue),
        "description": description or None,
        "url": eb.get("url"),
        "ticket_url": eb.get("url"),  # Eventbrite buying flow lives on the event page
        "price": _format_price(eb),
        "category": _guess_category(eb),
        "source": SOURCE,
    }
    return {k: v for k, v in event.items() if v is not None}


def _fetch_organizer_events(
    session: requests.Session, org: dict
) -> list[dict]:
    org_id = org["id"]
    org_name = org.get("name") or org_id
    headers = {"User-Agent": UA, "Accept": "application/json"}
    raw: list[dict] = []
    for page in range(1, PAGE_LIMIT + 1):
        url = f"{API_URL_TEMPLATE.format(id=org_id)}&page={page}"
        try:
            r = session.get(url, headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as exc:
            LOG.error("eventbrite [%s]: page %d failed: %s",
                      org_name, page, exc)
            break
        raw.extend(data.get("events", []))
        if not (data.get("pagination") or {}).get("has_more_items"):
            break
    LOG.debug("eventbrite [%s]: %d raw events", org_name, len(raw))
    return raw


def fetch(session: requests.Session | None = None) -> list[dict]:
    organizers = _load_organizer_ids()
    if not organizers:
        return []
    sess = session or requests.Session()
    out: list[dict] = []
    for org in organizers:
        raw = _fetch_organizer_events(sess, org)
        include_all = bool(org.get("include_all_venues"))
        kept_for_org = 0
        for eb in raw:
            mapped = _to_event_dict(eb, include_all_venues=include_all)
            if mapped:
                out.append(mapped)
                kept_for_org += 1
        LOG.info("eventbrite [%s]: %d %s events of %d raw",
                 org.get("name") or org["id"], kept_for_org,
                 "county-wide" if include_all else "in-area", len(raw))
    return out
