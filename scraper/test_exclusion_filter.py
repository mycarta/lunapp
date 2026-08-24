"""Tests for the title-based exclusion filter and the CMS-placeholder
guard in scrape.py.

No pytest dependency — the project has no existing test runner, so this
stays a plain-assert script consistent with that. Run directly:

    python scraper/test_exclusion_filter.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scrape import (
    apply_exclusion_rules,
    apply_placeholder_guard,
    load_exclusion_rules,
    load_placeholder_rules,
)


def test_real_events_survive_the_seeded_exclusion_list() -> None:
    """The seeded exclusion_rules.json terms are narrow, multi-word phrases
    on purpose — a bare "closed"/"closure" would also catch real event
    titles that happen to use the word "closing"."""
    terms = load_exclusion_rules()
    events = [
        {"title": "Closing Reception", "source": "school_of_arts"},
        {"title": "Season Closing Concert", "source": "musique_royale"},
        {
            "title": "Office Closure:\xa0Monday, July 27\xa0– Friday, July\xa031",
            "source": "school_of_arts",
        },
    ]
    kept_titles = {e["title"] for e in apply_exclusion_rules(events, terms)}

    assert "Closing Reception" in kept_titles, \
        "real event 'Closing Reception' was wrongly excluded"
    assert "Season Closing Concert" in kept_titles, \
        "real event 'Season Closing Concert' was wrongly excluded"
    assert not any(t.lower().startswith("office closure") for t in kept_titles), \
        "'Office Closure: ...' admin notice was not excluded"
    assert len(kept_titles) == 2


def test_no_terms_is_a_no_op() -> None:
    events = [{"title": "Anything at all", "source": "x"}]
    assert apply_exclusion_rules(events, []) == events


def test_wix_placeholder_descriptions_are_dropped() -> None:
    """The four demo shows Wix serves on an unedited /shows page all carry
    the same stock description; the seeded phrase list must catch them while
    leaving real listings — including LTC's own terse seed copy — alone."""
    phrases, horizon = load_placeholder_rules()
    today = date(2026, 8, 24)
    events = [
        {
            "title": "When Darkness Falls",
            "date": "2026-09-01",
            "description": (
                "I'm an event description. Click here to open up the Event "
                "Editor and describe your event."
            ),
            "source": "hypothetical_ltc",
        },
        {
            "title": "The Improvisation Story",
            "date": "2026-09-02",
            "description": "I'm a paragraph. Click here to add your own text.",
            "source": "hypothetical_ltc",
        },
        {
            "title": "Speakeasy — Lunenburg Theatre Collective",
            "date": "2026-08-25",
            "description": "Professional theatre on the South Shore.",
            "source": "manual_seed",
        },
    ]
    kept = apply_placeholder_guard(events, phrases, horizon, today)
    kept_titles = {e["title"] for e in kept}

    assert kept_titles == {"Speakeasy — Lunenburg Theatre Collective"}, \
        f"placeholder guard kept the wrong set: {kept_titles}"


def test_far_future_dates_are_dropped() -> None:
    """Wix stock listings are dated 2035. Anything past the configured
    horizon is filler or a typo'd year, never a real listing."""
    phrases, horizon = load_placeholder_rules()
    today = date(2026, 8, 24)
    events = [
        {"title": "Thirst", "date": "2035-11-29", "description": "",
         "source": "hypothetical_ltc"},
        {"title": "Real show next year", "date": "2027-06-01",
         "description": "", "source": "manual_seed"},
    ]
    kept_titles = {
        e["title"] for e in apply_placeholder_guard(events, phrases, horizon, today)
    }

    assert kept_titles == {"Real show next year"}, \
        f"far-future guard kept the wrong set: {kept_titles}"


def test_horizon_boundary_and_unparseable_dates() -> None:
    """Exactly at the horizon is kept (only strictly-further is dropped), and
    an unparseable date is left for filter_to_window() to deal with."""
    today = date(2026, 8, 24)
    events = [
        {"title": "At the limit", "date": (today + timedelta(days=730)).isoformat(),
         "description": "", "source": "x"},
        {"title": "One day past", "date": (today + timedelta(days=731)).isoformat(),
         "description": "", "source": "x"},
        {"title": "No date at all", "description": "", "source": "x"},
        {"title": "Garbage date", "date": "TBA", "description": "", "source": "x"},
    ]
    kept_titles = {
        e["title"] for e in apply_placeholder_guard(events, [], 730, today)
    }

    assert kept_titles == {"At the limit", "No date at all", "Garbage date"}, \
        f"boundary handling is wrong: {kept_titles}"


def test_disabled_guard_is_a_no_op() -> None:
    events = [{"title": "x", "date": "2099-01-01",
               "description": "I'm an event description.", "source": "x"}]
    assert apply_placeholder_guard(events, [], 0, date(2026, 8, 24)) == events


if __name__ == "__main__":
    test_real_events_survive_the_seeded_exclusion_list()
    test_no_terms_is_a_no_op()
    test_wix_placeholder_descriptions_are_dropped()
    test_far_future_dates_are_dropped()
    test_horizon_boundary_and_unparseable_dates()
    test_disabled_guard_is_a_no_op()
    print("OK: all exclusion-filter and placeholder-guard tests passed")
