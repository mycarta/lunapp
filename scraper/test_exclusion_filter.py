"""Tests for the title-based exclusion filter in scrape.py.

No pytest dependency — the project has no existing test runner, so this
stays a plain-assert script consistent with that. Run directly:

    python scraper/test_exclusion_filter.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scrape import apply_exclusion_rules, load_exclusion_rules


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


if __name__ == "__main__":
    test_real_events_survive_the_seeded_exclusion_list()
    test_no_terms_is_a_no_op()
    print("OK: all exclusion-filter tests passed")
