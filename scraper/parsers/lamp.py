"""LAMP (Lunenburg Academy of Music Performance) — Squarespace event-list parser."""
from __future__ import annotations

import requests

from ._squarespace import parse_squarespace_events

PAGE_URL = "https://www.lampns.ca/concert-schedule"
SOURCE = "lamp"
# Squarespace lists the full business name; the app shows the short label.
VENUE_DISPLAY = "LAMP"


def fetch(session: requests.Session | None = None) -> list[dict]:
    return parse_squarespace_events(
        page_url=PAGE_URL,
        source=SOURCE,
        venue_display=VENUE_DISPLAY,
        session=session,
    )
