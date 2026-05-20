"""Old Confidence Lodge — Squarespace event-list parser.

The Lodge occasionally cross-lists shows at the Bus Stop Theatre in Halifax;
we only want their Riverport/Lunenburg-area shows, so we whitelist by
address substring.
"""
from __future__ import annotations

import requests

from ._squarespace import parse_squarespace_events

PAGE_URL = "https://www.oldconfidence.ca/events"
SOURCE = "old_confidence_lodge"
VENUE_DISPLAY = "Old Confidence Lodge"
ALLOWED_VENUE_SUBSTRINGS = ["riverport", "lunenburg"]


def fetch(session: requests.Session | None = None) -> list[dict]:
    return parse_squarespace_events(
        page_url=PAGE_URL,
        source=SOURCE,
        venue_display=VENUE_DISPLAY,
        allowed_venue_substrings=ALLOWED_VENUE_SUBSTRINGS,
        session=session,
    )
