"""Lightship Brewery — public Google Calendar iCal feed parser.

Lightship publishes events through a public Google Calendar; the iCal
feed at ``calendar.google.com/calendar/ical/<id>/public/basic.ics``
returns all VEVENTs in standard ICS form including recurring-event
RRULEs.

Two implementation notes:

  - The feed includes recurring events (weekly trivia, daily happy
    hour, etc.). ``recurring-ical-events`` expands those into discrete
    occurrences inside our 14-day window. Without expansion we'd only
    see the original RRULE master rows, which the frontend can't
    render usefully.
  - Routine items like the daily "Happy Hour" are excluded by title
    keyword — they're the brewery's standing operating hours, not the
    one-off "what's on this week" stuff the app exists to surface.
    Live music, trivia nights, and one-off events stay in.

Per project policy, the venue is hard-coded to Lightship in Lunenburg,
so no per-event geo filter is needed (everything in this feed is
at the brewery itself).
"""
from __future__ import annotations

import logging
import re
from datetime import date as _date, datetime, timedelta

import requests

LOG = logging.getLogger(__name__)

FEED_URL = (
    "https://calendar.google.com/calendar/ical/"
    "saltboxbrewingcompany.ca_70er6l41fcrskulncurr5k5rfc"
    "%40group.calendar.google.com/public/basic.ics"
)
SOURCE = "lightship"
VENUE = "Lightship Brewery"
LOCATION = "93 Tannery Rd, Lunenburg"
SITE_URL = "https://lightshipbrewery.ca/"

UA = "Mozilla/5.0 (lunenburg-events scraper; +https://github.com/)"
TIMEOUT = 20
WINDOW_DAYS = 14

# Title keywords (case-insensitive substring) that mark a routine/
# operational item rather than a discrete event. Extend as needed.
_ROUTINE_TITLE_KEYWORDS = (
    "happy hour",
)


def _is_routine(title: str) -> bool:
    low = title.lower()
    return any(kw in low for kw in _ROUTINE_TITLE_KEYWORDS)


def _format_time(dt) -> str | None:
    """Datetime → 'H:MM AM/PM'. All-day events (date only) return None."""
    if isinstance(dt, datetime):
        return dt.strftime("%I:%M %p").lstrip("0")
    return None


def _format_date(dt) -> str:
    if isinstance(dt, datetime):
        return dt.date().strftime("%Y-%m-%d")
    if isinstance(dt, _date):
        return dt.strftime("%Y-%m-%d")
    return ""


def _clean_description(raw: str, max_chars: int = 500) -> str | None:
    if not raw:
        return None
    # icalendar already decoded escape sequences; squash whitespace and cap.
    s = re.sub(r"\s+", " ", raw).strip()
    if len(s) > max_chars:
        s = s[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return s or None


def _guess_category(title: str, description: str | None) -> str:
    blob = f"{title} {description or ''}".lower()
    if re.search(
        r"\b(live music|live at lightship|band|concert|singer|songwriter|"
        r"jazz|fiddle|guitarist|trio|quartet|duo)\b",
        blob,
    ):
        return "music"
    if "trivia" in blob:
        return "community"
    if re.search(r"\b(film|movie|cinema|screening)\b", blob):
        return "film"
    return "community"


def fetch(session: requests.Session | None = None) -> list[dict]:
    # Imported lazily so a missing optional dep degrades to "0 events"
    # rather than breaking the whole scraper pipeline at import time.
    try:
        import icalendar
        import recurring_ical_events
    except ImportError as exc:
        LOG.error("lightship: required deps not installed (%s); "
                  "add icalendar + recurring-ical-events", exc)
        return []

    sess = session or requests.Session()
    try:
        r = sess.get(FEED_URL, headers={"User-Agent": UA}, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as exc:
        LOG.error("lightship: feed fetch failed: %s", exc)
        return []

    try:
        cal = icalendar.Calendar.from_ical(r.text)
    except ValueError as exc:
        LOG.error("lightship: iCal parse failed: %s", exc)
        return []

    today = _date.today()
    end = today + timedelta(days=WINDOW_DAYS)
    try:
        occurrences = recurring_ical_events.of(cal).between(today, end)
    except Exception as exc:
        LOG.error("lightship: rrule expansion failed: %s", exc)
        return []

    out: list[dict] = []
    for ev in occurrences:
        title = str(ev.get("SUMMARY", "")).strip()
        if not title:
            continue
        if _is_routine(title):
            LOG.debug("lightship: skipping routine %r", title)
            continue

        dtstart_prop = ev.get("DTSTART")
        if not dtstart_prop:
            continue
        date_iso = _format_date(dtstart_prop.dt)
        if not date_iso:
            continue

        dtend_prop = ev.get("DTEND")
        description = _clean_description(str(ev.get("DESCRIPTION", "")))

        event = {
            "title": title,
            "date": date_iso,
            "time": _format_time(dtstart_prop.dt),
            "end_time": _format_time(dtend_prop.dt) if dtend_prop else None,
            "venue": VENUE,
            "location": LOCATION,
            "description": description,
            "url": SITE_URL,
            "category": _guess_category(title, description),
            "source": SOURCE,
        }
        out.append({k: v for k, v in event.items() if v is not None})

    return out
