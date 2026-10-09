"""Tests for the parser staleness warning and issue hand-off in scrape.py.

Same plain-assert style as the other test files — no pytest dependency.
Run directly:

    python scraper/test_parser_staleness.py
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scrape
from scrape import (
    RUN_HISTORY_RUNS,
    STALE_RUNS,
    _as_record,
    parsers_needing_issue,
    update_run_history,
    warn_on_stale_parsers,
    write_stale_notice,
    zero_streak,
)

TODAY = date(2026, 10, 9)


def _record(counts: list[int], notified: bool = False,
            last_nonzero: str | None = None) -> dict:
    return {"counts": counts, "notified": notified, "last_nonzero": last_nonzero}


class _CaptureWarnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _warn(history: dict[str, dict]) -> tuple[list[str], list[str]]:
    """Run the warning pass with log output captured instead of printed."""
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


# --- streak detection ---------------------------------------------------------

def test_zero_streak_counts_back_from_the_latest_run() -> None:
    assert zero_streak([]) == 0
    assert zero_streak([4, 3, 0, 0, 0]) == 3
    assert zero_streak([0, 0, 3, 0, 0]) == 2
    assert zero_streak([0, 0, 0, 0, 5]) == 0
    assert zero_streak([0, 0, 0]) == 3


def test_parser_that_went_quiet_is_flagged() -> None:
    """The musique_royale case: it worked, the site changed, nothing since."""
    stale, messages = _warn({"musique_royale": _record([4, 3, 0, 0, 0])})
    assert stale == ["musique_royale"], stale
    assert messages == [
        "STALE: [musique_royale] has returned 0 events for 3 consecutive "
        "runs — check if the source has changed."
    ], messages


def test_healthy_parser_is_silent() -> None:
    stale, messages = _warn({"lamp": _record([11, 11, 12, 11, 11])})
    assert stale == [] and messages == [], (stale, messages)


def test_one_good_run_inside_the_streak_resets_it() -> None:
    assert _warn({"lightship": _record([0, 0, 0, 0, 3])})[0] == []
    assert _warn({"lightship": _record([0, 0, 3, 0, 0])})[0] == []


def test_always_quiet_parser_is_flagged_too() -> None:
    """Deliberate behaviour change: a parser with no events in living memory
    used to be exempt, to avoid warning forever with nothing to act on. Now
    that an issue fires once per streak, there's no "forever" — and a source
    that never produces is exactly what someone should look at once."""
    stale, messages = _warn({"opera_house": _record([0, 0, 0, 0, 0])})
    assert stale == ["opera_house"], stale
    assert len(messages) == 1, messages


def test_too_little_history_is_silent() -> None:
    assert _warn({"new_parser": _record([0, 0])})[0] == []


# --- history bookkeeping ------------------------------------------------------

def test_history_is_appended_and_capped() -> None:
    updated = update_run_history({"lamp": _record([1, 2, 3, 4, 5])},
                                 {"lamp": 6}, TODAY)
    assert updated["lamp"]["counts"] == [2, 3, 4, 5, 6], updated
    assert len(updated["lamp"]["counts"]) == RUN_HISTORY_RUNS, updated


def test_new_and_removed_parsers_are_handled() -> None:
    updated = update_run_history({"gone": _record([1, 1, 1])}, {"fresh": 2}, TODAY)
    assert list(updated) == ["fresh"], updated
    assert updated["fresh"]["counts"] == [2], updated


def test_events_reset_the_notified_flag_and_stamp_the_date() -> None:
    """Recovery ends the incident: the next streak is a new one and must be
    allowed to open its own issue."""
    history = {"lightship": _record([0, 0, 0], notified=True)}
    updated = update_run_history(history, {"lightship": 3}, TODAY)
    assert updated["lightship"]["notified"] is False, updated
    assert updated["lightship"]["last_nonzero"] == "2026-10-09", updated


def test_zero_run_preserves_notified_and_last_nonzero() -> None:
    history = {"lightship": _record([3, 0, 0], notified=True,
                                    last_nonzero="2026-09-01")}
    updated = update_run_history(history, {"lightship": 0}, TODAY)
    assert updated["lightship"]["notified"] is True, updated
    assert updated["lightship"]["last_nonzero"] == "2026-09-01", updated


def test_old_list_format_migrates_in_place() -> None:
    """The file on disk (and in the CI cache) predates these fields, so the
    old shape must load rather than being discarded — same path, same cache
    key, no lost history."""
    migrated = _as_record([4, 0, 0])
    assert migrated == {"counts": [4, 0, 0], "notified": False,
                        "last_nonzero": None}, migrated
    assert _as_record("nonsense") is None
    assert _as_record({"counts": "nope"}) is None


# --- issue hand-off -----------------------------------------------------------

def test_issue_fires_once_per_streak() -> None:
    history = {"lightship": _record([3, 0, 0, 0], last_nonzero="2026-09-20")}
    first = parsers_needing_issue(history)
    assert first == [{"parser": "lightship", "streak": 3,
                      "last_nonzero": "2026-09-20"}], first
    assert history["lightship"]["notified"] is True, history
    # Streak continues on the next run: still stale, but no second issue.
    nxt = update_run_history(history, {"lightship": 0}, TODAY)
    assert parsers_needing_issue(nxt) == [], nxt
    assert zero_streak(nxt["lightship"]["counts"]) == 4, nxt


def test_recovery_then_relapse_opens_a_second_issue() -> None:
    history = {"p": _record([0, 0, 0], notified=True)}
    history = update_run_history(history, {"p": 5}, TODAY)      # recovers
    for _ in range(STALE_RUNS):                                  # goes quiet again
        history = update_run_history(history, {"p": 0}, TODAY)
    needing = parsers_needing_issue(history)
    assert [p["parser"] for p in needing] == ["p"], needing
    assert needing[0]["streak"] == STALE_RUNS, needing


def test_below_threshold_opens_nothing() -> None:
    history = {"p": _record([5, 0, 0])}
    assert parsers_needing_issue(history) == [], history
    assert history["p"]["notified"] is False, history


def test_notice_file_lists_only_new_parsers(tmp: Path | None = None) -> None:
    """The workflow reads this file; it must be rewritten every run so a
    stale copy can't trigger a duplicate issue."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "stale_parsers.json"
        original = scrape.STALE_NOTICE_PATH
        scrape.STALE_NOTICE_PATH = path
        try:
            write_stale_notice([{"parser": "lightship", "streak": 3,
                                 "last_nonzero": "2026-09-20"}])
            payload = json.loads(path.read_text(encoding="utf-8"))
            assert [p["parser"] for p in payload["parsers"]] == ["lightship"], payload
            assert payload["stale_runs"] == STALE_RUNS, payload
            assert payload["generated_at"], payload
            # A later run with nothing stale must clear it.
            write_stale_notice([])
            payload = json.loads(path.read_text(encoding="utf-8"))
            assert payload["parsers"] == [], payload
        finally:
            scrape.STALE_NOTICE_PATH = original


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"{len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
