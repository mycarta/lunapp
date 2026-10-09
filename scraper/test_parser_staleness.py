"""Tests for the parser staleness warning in scrape.py.

Same plain-assert style as the other test files — no pytest dependency.
Run directly:

    python scraper/test_parser_staleness.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scrape import (
    RUN_HISTORY_RUNS,
    STALE_RUNS,
    update_run_history,
    warn_on_stale_parsers,
)


class _CaptureWarnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _warn(history: dict[str, list[int]]) -> tuple[list[str], list[str]]:
    """Run the check with warnings captured instead of printed."""
    log = logging.getLogger("scrape")
    handler = _CaptureWarnings()
    log.addHandler(handler)
    previous, log.propagate = log.propagate, False
    try:
        stale = warn_on_stale_parsers(history)
    finally:
        log.removeHandler(handler)
        log.propagate = previous
    return stale, handler.messages


def test_parser_that_went_quiet_is_flagged() -> None:
    """The musique_royale case: it worked, the site changed, and it has
    returned nothing since."""
    stale, messages = _warn({"musique_royale": [4, 3, 0, 0, 0]})
    assert stale == ["musique_royale"], stale
    assert len(messages) == 1, messages
    assert messages[0] == (
        "STALE: [musique_royale] has returned 0 events for 3 consecutive "
        "runs — check if the source has changed."
    ), messages[0]


def test_healthy_parser_is_silent() -> None:
    stale, messages = _warn({"lamp": [11, 11, 12, 11, 11]})
    assert stale == [] and messages == [], (stale, messages)


def test_one_good_run_inside_the_streak_resets_it() -> None:
    """Three consecutive zeros means the last three runs — a parser that
    produced events last run is not stale, however quiet it was before."""
    stale, _ = _warn({"lightship": [0, 0, 0, 0, 3]})
    assert stale == [], stale
    stale, _ = _warn({"lightship": [0, 0, 3, 0, 0]})
    assert stale == [], stale


def test_parser_with_no_events_in_living_memory_is_silent() -> None:
    """opera_house has returned 0 every run we remember (its listing page was
    retired). Warning every run would be noise with nothing to act on."""
    stale, messages = _warn({"opera_house": [0, 0, 0, 0, 0]})
    assert stale == [] and messages == [], (stale, messages)


def test_too_little_history_is_silent() -> None:
    """A brand-new parser shouldn't be called stale before it has run
    STALE_RUNS times."""
    stale, _ = _warn({"new_parser": [0, 0]})
    assert stale == [], stale


def test_history_is_appended_and_capped() -> None:
    history = {"lamp": [1, 2, 3, 4, 5]}
    updated = update_run_history(history, {"lamp": 6})
    assert updated["lamp"] == [2, 3, 4, 5, 6], updated
    assert len(updated["lamp"]) == RUN_HISTORY_RUNS, updated


def test_new_and_removed_parsers_are_handled() -> None:
    """A parser added to ALL_PARSERS starts a history; one removed from it
    stops being tracked rather than lingering forever."""
    updated = update_run_history({"gone": [1, 1, 1]}, {"fresh": 2})
    assert updated == {"fresh": [2]}, updated


def test_threshold_boundary() -> None:
    """Exactly STALE_RUNS zeros after a non-zero run trips it; one fewer
    does not."""
    below = [1] + [0] * (STALE_RUNS - 1)
    at = [1] + [0] * STALE_RUNS
    assert _warn({"p": below})[0] == [], below
    assert _warn({"p": at})[0] == ["p"], at


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"{len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
