"""Lightship Brewery — public Google Calendar iCal feed parser.

Strict allowlist: only events whose title OR description contains the
phrase "live music" (case-insensitive) are kept. Trivia, open mic,
happy hour, tap takeovers, and anything else that doesn't say "live
music" explicitly are dropped — this is an allowlist, not a denylist.

The feed URL is the public Google Calendar iCal export for the brewery.
``recurring-ical-events`` expands RRULE masters into discrete occurrences
inside the 14-day window.
"""
from __future__ import annotations

import html
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
EVENTS_PAGE_URL = "https://lightshipbrewery.ca/pages/events"

UA = "Mozilla/5.0 (lunenburg-events scraper; +https://github.com/)"
TIMEOUT = 20
WINDOW_DAYS = 14

_LIVE_MUSIC_RE = re.compile(
    r"\blive\s+music\b|\blive\s+at\s+lightship\b",
    re.IGNORECASE,
)


def _is_live_music(title: str, description: str | None) -> bool:
    return bool(
        _LIVE_MUSIC_RE.search(title)
        or (description and _LIVE_MUSIC_RE.search(description))
    )


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
    # Google Calendar stores HTML in DESCRIPTION; turn block-level tags
    # into spaces so adjacent words don't merge, then strip all tags.
    s = re.sub(r"<\s*(br|/p|/div)\s*/?\s*>", " ", raw, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    # Strip Google Meet boilerplate injected automatically by Calendar.
    s = re.sub(
        r"-*\s*Join with Google Meet:\s*https?://\S+",
        "", s, flags=re.IGNORECASE,
    )
    s = re.sub(
        r"Learn more about Meet at:\s*https?://\S+",
        "", s, flags=re.IGNORECASE,
    )
    # Strip bare URLs — pasted links survive HTML-stripping as noise,
    # and the event already carries its own `url` field.
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_chars:
        s = s[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return s or None


def fetch(session: requests.Session | None = None) -> list[dict]:
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

        dtstart_prop = ev.get("DTSTART")
        if not dtstart_prop:
            continue
        date_iso = _format_date(dtstart_prop.dt)
        if not date_iso:
            continue

        dtend_prop = ev.get("DTEND")
        description = _clean_description(str(ev.get("DESCRIPTION", "")))

        if not _is_live_music(title, description):
            LOG.debug("lightship: skipping (no 'live music') %r", title)
            continue

        raw_url = str(ev.get("URL", "")).strip()
        event_url = raw_url if raw_url.startswith("http") else EVENTS_PAGE_URL

        event = {
            "title": title,
            "date": date_iso,
            "time": _format_time(dtstart_prop.dt),
            "end_time": _format_time(dtend_prop.dt) if dtend_prop else None,
            "venue": VENUE,
            "location": LOCATION,
            "description": description,
            "url": event_url,
            "category": "music",
            "source": SOURCE,
        }
        out.append({k: v for k, v in event.items() if v is not None})

    return out
