"""Tests for multi-day (``end_date``) expansion in scrape.py.

Same plain-assert style as test_exclusion_filter.py — no pytest
dependency. Run directly:

    python scraper/test_end_date_ranges.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scrape import WINDOW_DAYS, expand_date_ranges, filter_to_window

TODAY = date(2026, 10, 9)


def _dates(events: list[dict]) -> list[str]:
    return [e["date"] for e in events]


def test_single_day_events_are_untouched() -> None:
    """No end_date means no expansion — the overwhelming majority of
    events, so this must stay a pass-through."""
    events = [{"title": "Concert", "date": "2026-10-10"}]
    out = expand_date_ranges(events, TODAY)
    assert out == events, out


def test_long_run_in_progress_is_a_single_card_dated_today() -> None:
    """The "Making Sense of Chaos" case: an exhibition open Oct 1–25 must
    still be visible today (it used to vanish on Oct 2), but as one card
    rather than filling all 15 windowed day groups."""
    out = expand_date_ranges(
        [{"title": "Making Sense of Chaos", "date": "2026-10-01",
          "end_date": "2026-10-25"}], TODAY,
    )
    assert len(out) == 1, _dates(out)
    assert out[0]["date"] == "2026-10-09", out
    assert "end_date" not in out[0], out
    kept = filter_to_window(out, __import__("datetime").datetime(2026, 10, 9))
    assert len(kept) == 1, kept


def test_long_run_not_yet_started_shows_on_its_opening_day() -> None:
    """A long run still in the future advertises its opening day, so people
    can see it coming rather than only on the day it starts."""
    out = expand_date_ranges(
        [{"title": "Winter show", "date": "2026-10-14", "end_date": "2026-11-30"}],
        TODAY,
    )
    assert len(out) == 1, _dates(out)
    assert out[0]["date"] == "2026-10-14", out


def test_short_run_shows_on_every_day() -> None:
    """A festival-length run (<= LONG_RUN_DAYS) keeps one card per day, so
    each day's programme is visible in advance — the Doc Fest shape."""
    out = expand_date_ranges(
        [{"title": "Doc Fest", "date": "2026-10-12", "end_date": "2026-10-16"}],
        TODAY,
    )
    assert _dates(out) == ["2026-10-12", "2026-10-13", "2026-10-14",
                           "2026-10-15", "2026-10-16"], _dates(out)
    kept = filter_to_window(out, __import__("datetime").datetime(2026, 10, 9))
    assert len(kept) == len(out), (len(kept), len(out))


def test_end_date_is_stripped_from_expanded_entries() -> None:
    """Expanded copies are concrete single-day events; leaving end_date on
    them would re-expand if the pass ever ran twice."""
    out = expand_date_ranges(
        [{"title": "Run", "date": "2026-10-10", "end_date": "2026-10-12"}], TODAY,
    )
    assert _dates(out) == ["2026-10-10", "2026-10-11", "2026-10-12"], _dates(out)
    assert all("end_date" not in e for e in out), out


def test_short_run_is_clipped_to_the_window() -> None:
    """A short run straddling the window edge emits only the days inside it —
    no entries the window filter would immediately discard."""
    window_end = TODAY + timedelta(days=WINDOW_DAYS)
    out = expand_date_ranges(
        [{"title": "Festival", "date": "2026-10-20", "end_date": "2026-10-26"}], TODAY,
    )
    assert _dates(out)[0] == "2026-10-20", _dates(out)
    assert _dates(out)[-1] == window_end.strftime("%Y-%m-%d"), _dates(out)
    assert len(out) == 4, _dates(out)


def test_degenerate_ranges_fall_back_to_a_single_day() -> None:
    """end_date equal to, before, or unparseable relative to date must not
    produce zero entries — the event still happens on its start date."""
    same = expand_date_ranges(
        [{"title": "A", "date": "2026-10-10", "end_date": "2026-10-10"}], TODAY)
    backwards = expand_date_ranges(
        [{"title": "B", "date": "2026-10-10", "end_date": "2026-10-02"}], TODAY)
    garbage = expand_date_ranges(
        [{"title": "C", "date": "2026-10-10", "end_date": "soon"}], TODAY)
    for out in (same, backwards, garbage):
        assert len(out) == 1, out
        assert out[0]["date"] == "2026-10-10", out


def test_run_entirely_outside_the_window_is_left_for_the_window_filter() -> None:
    """Expansion doesn't drop events itself — a run that starts after the
    window stays as one entry and the window filter handles it."""
    out = expand_date_ranges(
        [{"title": "Spring show", "date": "2027-05-01", "end_date": "2027-05-10"}],
        TODAY,
    )
    assert len(out) == 1, out
    assert filter_to_window(out, __import__("datetime").datetime(2026, 10, 9)) == []


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"{len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
