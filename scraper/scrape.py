"""Main scraper entry point for the Lunenburg Events app.

Runs every configured source parser, merges and deduplicates the results,
filters to the next 14 days, and writes the unified ``events.json`` that
the frontend reads.
"""
from __future__ import annotations

import html
import json
import logging
import re
import sys
from datetime import date as _date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from parsers import ALL_PARSERS

LOG = logging.getLogger("scrape")

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "events.json"
INDEX_HTML_PATH = REPO_ROOT / "index.html"
MANUAL_SEEDS_PATH = REPO_ROOT / "manual_seeds.json"
PARKING_RULES_PATH = REPO_ROOT / "parking_rules.json"
EXCLUSION_RULES_PATH = REPO_ROOT / "exclusion_rules.json"
HALIFAX = ZoneInfo("America/Halifax")
WINDOW_DAYS = 14

SITE_URL = "https://lunenburg.fingerpost.ca/"

# Fallback image baked into every event's JSON-LD because none of our
# parsers carry per-event imagery. Google's rich-result carousel uses
# the site icon when nothing better is available — better than no image
# at all, which Search Console flags as a recommended-field warning.
DEFAULT_EVENT_IMAGE = "https://lunenburg.fingerpost.ca/assets/icon-512.png"

# Comment markers in index.html that bracket the regions the scraper rewrites
# on each run. Keep these in sync with the placeholders in index.html.
_JSONLD_RE = re.compile(
    r"(<!-- BEGIN:JSON-LD -->)(.*?)(<!-- END:JSON-LD -->)", re.DOTALL,
)
_NOSCRIPT_RE = re.compile(
    r"(<!-- BEGIN:NOSCRIPT-EVENTS -->)(.*?)(<!-- END:NOSCRIPT-EVENTS -->)",
    re.DOTALL,
)

_WEEKDAY_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday"]
_MONTH_LONG = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]

# --- Address normalization ----------------------------------------------------
# Squarespace gives us full mailing addresses like
#   "97 Kaulbach Street, PO Box 309 Lunenburg, NS, B0J 2C0 Canada"
# but the UI just needs "97 Kaulbach St, Lunenburg". This trims the noise.

_COUNTRY_RE = re.compile(r",?\s*\bCanada\b\.?", re.IGNORECASE)
_POSTAL_RE = re.compile(r"\b[A-Z]\d[A-Z]\s*\d[A-Z]\d\b")
_PROV_RE = re.compile(r",?\s*\b(NS|NB|PE|NL|ON|QC|BC|AB|SK|MB|YT|NT|NU)\b\.?")
_PO_BOX_RE = re.compile(r"\bP\.?\s*O\.?\s*Box\s+\d+\b", re.IGNORECASE)

_STREET_TYPES = {
    "street": "St",
    "avenue": "Ave",
    "road": "Rd",
    "boulevard": "Blvd",
    "drive": "Dr",
    "lane": "Ln",
    "crescent": "Cres",
    "court": "Ct",
    "place": "Pl",
    "highway": "Hwy",
}
_STREET_RE = re.compile(
    r"\b(" + "|".join(_STREET_TYPES) + r")\b\.?", re.IGNORECASE
)

# When the stripped address has no comma, fall back to inserting one before a
# recognized Lunenburg-area town name so "3831 Nova Scotia 332 Riverport"
# becomes "3831 Nova Scotia 332, Riverport".
_KNOWN_TOWNS = [
    "Mahone Bay",  # multi-word entries must come first
    "Blue Rocks",
    "Indian Path",
    "Garden Lots",
    "Lunenburg",
    "Riverport",
    "Stonehurst",
    "Chester",
    "Bridgewater",
]


def normalize_location(loc: str | None) -> str | None:
    if not loc:
        return None
    s = loc
    s = _COUNTRY_RE.sub("", s)
    s = _POSTAL_RE.sub("", s)
    s = _PROV_RE.sub("", s)
    s = _PO_BOX_RE.sub("", s)
    s = _STREET_RE.sub(lambda m: _STREET_TYPES[m.group(1).lower()], s)
    # Squeeze repeated commas/whitespace produced by the deletions above.
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"(\s*,\s*)+", ", ", s)
    s = s.strip().strip(",").strip()
    if "," not in s:
        for town in sorted(_KNOWN_TOWNS, key=len, reverse=True):
            suffix = " " + town.lower()
            if s.lower().endswith(suffix):
                s = s[: -len(town)].rstrip() + ", " + town
                break
    return s or None


def _dedupe_key(event: dict) -> tuple[str, str]:
    title = re.sub(r"[^a-z0-9]+", "", (event.get("title") or "").lower())
    return (event.get("date", ""), title)


def _time_sort_key(event: dict) -> tuple[str, str]:
    # Within a day, sort by 24h-converted start time; events with no time go last.
    t = (event.get("time") or "").strip().upper()
    m = re.match(r"^(\d{1,2}):(\d{2})\s*(AM|PM)?", t)
    if not m:
        return (event.get("date", ""), "99:99")
    hh, mm, mer = int(m.group(1)), m.group(2), m.group(3)
    if mer == "PM" and hh != 12:
        hh += 12
    elif mer == "AM" and hh == 12:
        hh = 0
    return (event.get("date", ""), f"{hh:02d}:{mm}")


def collect_events(session: requests.Session) -> list[dict]:
    all_events: list[dict] = []
    for parser in ALL_PARSERS:
        name = parser.__name__.rsplit(".", 1)[-1]
        try:
            got = parser.fetch(session=session)
            LOG.info("[%s] %d events", name, len(got))
            all_events.extend(got)
        except Exception as exc:
            LOG.error("[%s] failed: %s", name, exc, exc_info=True)
    return all_events


def _expand_recurring_seed(
    seed: dict, today: _date, window_days: int
) -> list[dict]:
    """Expand a recurring seed into concrete dated occurrences within
    ``[today, today + window_days]``. Non-recurring seeds pass through
    unchanged as a single-item list.

    Currently only ``"recurrence": "weekly"`` is supported. The seed
    must specify ``day_of_week`` (e.g. ``"Thursday"``); recurrence and
    day_of_week are stripped from the expanded entries so they look
    identical to per-date seeds downstream.
    """
    if seed.get("recurrence") != "weekly":
        return [seed]
    dow_name = (seed.get("day_of_week") or "").strip().title()
    if dow_name not in _WEEKDAY_LONG:
        LOG.warning("recurring seed %r has invalid/missing day_of_week %r",
                    seed.get("title"), seed.get("day_of_week"))
        return []
    target_dow = _WEEKDAY_LONG.index(dow_name)
    template = {k: v for k, v in seed.items()
                if k not in ("recurrence", "day_of_week")}
    out: list[dict] = []
    for offset in range(window_days + 1):
        d = today + timedelta(days=offset)
        if d.weekday() == target_dow:
            entry = dict(template)
            entry["date"] = d.strftime("%Y-%m-%d")
            out.append(entry)
    return out


def load_manual_seeds() -> list[dict]:
    """Load human-curated events from manual_seeds.json at the repo root.

    These cover sources that no parser handles — Instagram-only posts,
    bulletin-board notices, Eventbrite listings without a scrapable home
    page, etc. Seeds use the same event schema as parser output and go
    through the same windowing + dedupe pipeline. The merge order in
    ``main`` places them *before* parser output so they win the dedupe
    against any parser entry that happens to overlap on (date, title) —
    which is the contract: a manual seed persists until it's removed
    from the file, regardless of what the parsers say.

    Recurring seeds (``"recurrence": "weekly"`` + ``"day_of_week"``)
    are expanded here into one entry per matching day inside the 14-day
    window, so per-date dedupe and windowing work identically for
    recurring and one-off events.
    """
    if not MANUAL_SEEDS_PATH.exists():
        return []
    try:
        data = json.loads(MANUAL_SEEDS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        LOG.error("manual_seeds.json failed to load: %s", exc)
        return []
    events = data.get("events", []) if isinstance(data, dict) else []
    valid = [e for e in events if isinstance(e, dict)]
    today = datetime.now(HALIFAX).date()
    expanded: list[dict] = []
    for seed in valid:
        expanded.extend(_expand_recurring_seed(seed, today, WINDOW_DAYS))
    return expanded


def filter_to_window(events: list[dict], today: datetime) -> list[dict]:
    start = today.date()
    end = start + timedelta(days=WINDOW_DAYS)
    kept = []
    for e in events:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        if start <= d <= end:
            kept.append(e)
    return kept


# --- Title-based exclusion filter ----------------------------------------------
# exclusion_rules.json (repo root, tracked in git — unlike the local-only
# parking_rules.json) is a config-driven list of title substrings that mark
# an item as administrative noise rather than a real event, e.g. a venue's
# blog/post feed picking up "Office Closure: Monday, July 27 – Friday, July
# 31" as if it were a listing. Applied to every manual-seeded + parsed event
# before dedup, so excluded items never influence which duplicate wins.
# TITLE only, case-insensitive substring match. Terms are kept narrow and
# multi-word on purpose — a bare "closed" or "closure" would also catch real
# event titles like "Closing Reception" or "Season Closing Concert".

def load_exclusion_rules() -> list[str]:
    """Load title-substring exclusion terms from exclusion_rules.json.

    Returns an empty list when the file is absent, unreadable, or
    malformed — exclusion is strictly opt-in, so any problem degrades to
    "exclude nothing" rather than failing the scrape.
    """
    if not EXCLUSION_RULES_PATH.exists():
        return []
    try:
        data = json.loads(EXCLUSION_RULES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        LOG.error("exclusion_rules.json failed to load: %s", exc)
        return []
    terms = data.get("title_contains", []) if isinstance(data, dict) else []
    return [t for t in terms if isinstance(t, str) and t.strip()]


def apply_exclusion_rules(events: list[dict], terms: list[str]) -> list[dict]:
    """Drop events whose title contains any exclusion term (case-insensitive
    substring match). Logs each drop as "EXCLUDED: 'Title' [source]
    (matched 'term')" so filtered items stay visible in the workflow logs.
    No terms => events pass through untouched."""
    if not terms:
        return events
    lowered_terms = [t.lower() for t in terms]
    kept: list[dict] = []
    for e in events:
        title = (e.get("title") or "").lower()
        hit = next((t for t in lowered_terms if t in title), None)
        if hit is not None:
            LOG.info(
                "EXCLUDED: %r [%s] (matched %r)",
                e.get("title"), e.get("source"), hit,
            )
            continue
        kept.append(e)
    return kept


# --- Parking lot: per-source suppression rules --------------------------------
# parking_rules.json (repo root, gitignored) is an optional local tuning knob
# for muting noisy sources without touching parser code. Each rule keys on a
# source name and uses EXACTLY ONE of three mutually exclusive rule types:
#   suppress_all  — park every event from this source
#   suppress_if   — park events matching ANY listed condition (OR)
#   allow_only    — park every event that does NOT match the listed conditions
# Parked events are dropped from the output and logged as "PARKED: ..." so they
# remain visible in the Actions logs. Missing/empty file => nothing parked.

def load_parking_rules() -> list[dict]:
    """Load suppression rules from parking_rules.json at the repo root.

    Returns an empty list when the file is absent, unreadable, or malformed —
    parking is strictly opt-in, so any problem degrades to "park nothing"
    rather than failing the scrape.
    """
    if not PARKING_RULES_PATH.exists():
        return []
    try:
        data = json.loads(PARKING_RULES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        LOG.error("parking_rules.json failed to load: %s", exc)
        return []
    rules = data.get("rules", []) if isinstance(data, dict) else []
    return [r for r in rules if isinstance(r, dict)]


def _event_day_name(event: dict) -> str | None:
    """Long weekday name ("Monday"...) for the event's date, or None if the
    date is missing/unparseable."""
    try:
        d = datetime.strptime(event["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        return None
    return _WEEKDAY_LONG[d.weekday()]


def _matches_day(event: dict, days: list[str]) -> bool:
    name = _event_day_name(event)
    return name is not None and name in days


def _matches_title(event: dict, substrings: list[str]) -> bool:
    """Case-insensitive substring match: True if the title contains ANY of
    the listed substrings."""
    title = (event.get("title") or "").lower()
    return any(str(s).lower() in title for s in substrings)


def _should_park(event: dict, rule: dict) -> bool:
    """Decide whether ``event`` is parked by ``rule``. The three rule types are
    mutually exclusive; checked in priority order so a malformed rule with more
    than one type still behaves predictably (suppress_all wins, then
    suppress_if, then allow_only)."""
    if rule.get("suppress_all"):
        return True

    cond = rule.get("suppress_if")
    if isinstance(cond, dict):
        # OR across conditions: matching ANY one parks the event.
        days = cond.get("day_of_week")
        if days and _matches_day(event, days):
            return True
        titles = cond.get("title_contains")
        if titles and _matches_title(event, titles):
            return True
        return False

    allow = rule.get("allow_only")
    if isinstance(allow, dict):
        # AND across conditions: every listed condition must match, else park.
        days = allow.get("day_of_week")
        if days is not None and not _matches_day(event, days):
            return True
        titles = allow.get("title_contains")
        if titles is not None and not _matches_title(event, titles):
            return True
        return False

    return False


def apply_parking_rules(events: list[dict], rules: list[dict]) -> list[dict]:
    """Drop events suppressed by their source's parking rule. Logs each parked
    event as "PARKED: 'Title' [source] (reason)" so they surface in the
    workflow logs. No rules => events pass through untouched."""
    if not rules:
        return events
    by_source: dict[str, dict] = {}
    for r in rules:
        src = r.get("source")
        if src:
            by_source[src] = r  # last rule per source wins
    kept: list[dict] = []
    for e in events:
        rule = by_source.get(e.get("source"))
        if rule and _should_park(e, rule):
            LOG.info(
                "PARKED: %r [%s] (%s)",
                e.get("title"), e.get("source"),
                rule.get("reason", "no reason given"),
            )
            continue
        kept.append(e)
    return kept


def deduplicate(events: list[dict]) -> list[dict]:
    seen: dict[tuple, dict] = {}
    for e in events:
        key = _dedupe_key(e)
        if key not in seen:
            seen[key] = e
    return list(seen.values())


# --- Second-pass dedupe: substring title overlap at same date + venue --------
# The plain `deduplicate()` above keys on (date, alphanumeric-stripped title)
# so it only catches *exact* title matches. Two parsers commonly capture the
# same real-world event with different decoration — e.g. one emits "Tom
# Richards Trio" while another emits "Tom Richards Trio – Live at the Opera
# House – Tickets at the door!". This pass collapses those.

def _norm_title_loose(t: str | None) -> str:
    """Lowercase + collapse whitespace. Unlike _dedupe_key it keeps word
    boundaries so substring containment is meaningful."""
    if not t:
        return ""
    return re.sub(r"\s+", " ", t.lower().strip())


def _norm_venue(v: str | None) -> str:
    if not v:
        return ""
    return re.sub(r"\s+", " ", v.lower().strip())


def _title_substring_overlap(a: str | None, b: str | None) -> bool:
    """True iff one normalized title is a substring of the other and both
    are non-empty. Empty titles never match — we can't tell if they're
    the same event."""
    na, nb = _norm_title_loose(a), _norm_title_loose(b)
    if not na or not nb:
        return False
    return na in nb or nb in na


def _venue_overlap(a: str | None, b: str | None) -> bool:
    """True iff normalized venue names match or one contains the other.
    Handles cases like 'Lightship' vs 'Lightship Brewery'."""
    na, nb = _norm_venue(a), _norm_venue(b)
    if not na or not nb:
        return False
    return na in nb or nb in na


def _richness_score(e: dict) -> tuple:
    """Sort key (higher = richer) used to pick the winner between two
    substring-duplicate events. Per spec:
      1. has non-empty description
      2. has non-empty price
      3. shorter title (cleaner; less marketing decoration)
    """
    has_desc = bool((e.get("description") or "").strip())
    has_price = bool((e.get("price") or "").strip())
    neg_title_len = -len(e.get("title") or "")
    return (has_desc, has_price, neg_title_len)


def dedupe_substring_titles(events: list[dict]) -> list[dict]:
    """Collapse events that share a date + overlapping venue when one
    title is a substring of the other. Picks the richer event by
    `_richness_score` and logs the drop so duplicates surface in the
    workflow log.

    O(n²) inside each date — fine for our scale (~15 events/window).
    """
    kept: list[dict] = []
    for cand in events:
        cand_date = cand.get("date", "")
        dup_idx = None
        for i, ex in enumerate(kept):
            if ex.get("date") != cand_date:
                continue
            if not _venue_overlap(ex.get("venue"), cand.get("venue")):
                continue
            if not _title_substring_overlap(ex.get("title"), cand.get("title")):
                continue
            dup_idx = i
            break
        if dup_idx is None:
            kept.append(cand)
            continue

        ex = kept[dup_idx]
        if _richness_score(cand) > _richness_score(ex):
            winner, loser = cand, ex
            kept[dup_idx] = cand
        else:
            winner, loser = ex, cand
        LOG.info(
            "dedupe: dropping %r [%s] in favor of %r [%s] "
            "(same date+venue, substring-overlap titles)",
            loser.get("title"), loser.get("source"),
            winner.get("title"), winner.get("source"),
        )
    return kept


# --- Third-pass dedupe: shared distinctive word at same date+venue+time ------
# The substring pass above misses duplicates where the two titles decorate
# the same show differently enough that neither is a substring of the
# other — e.g. a sparse manual seed "Hedwig — Lunenburg Theatre Collective"
# vs a rich parser entry "Hedwig and the Angry Inch" for the same night at
# Old Confidence Lodge. They only share the word "Hedwig". Requiring a
# shared distinctive (non-boilerplate) word *plus* date+venue+time
# agreement is the signal — deliberately not a fuzzy/similarity match.

_KEYWORD_STOPWORDS = {
    "the", "and", "at", "in", "of", "a", "live", "show", "night", "presents",
    "an", "to", "for", "with", "on",
    # Recurring company/venue boilerplate — several distinct Lunenburg
    # Theatre Collective productions all carry this suffix, so these words
    # alone would produce false collisions between different shows.
    "lunenburg", "theatre", "theater", "collective",
}

_SIGNIFICANT_WORD_RE = re.compile(r"[a-zA-Z]+")


def _significant_words(title: str | None) -> set[str]:
    """Lowercased words of 4+ letters, minus `_KEYWORD_STOPWORDS`."""
    if not title:
        return set()
    return {
        w for w in (m.lower() for m in _SIGNIFICANT_WORD_RE.findall(title))
        if len(w) >= 4 and w not in _KEYWORD_STOPWORDS
    }


def _shared_significant_word(a: str | None, b: str | None) -> bool:
    return bool(_significant_words(a) & _significant_words(b))


_TIME_PARSE_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?\s*$", re.IGNORECASE)


def _parse_time_minutes(t: str | None) -> int | None:
    """"7:30 PM"-style string -> minutes since midnight, or None if
    unparseable (e.g. "TBA")."""
    if not t:
        return None
    m = _TIME_PARSE_RE.match(t.strip())
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    mer = (m.group(3) or "").upper()
    if mer == "PM" and hh != 12:
        hh += 12
    elif mer == "AM" and hh == 12:
        hh = 0
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh * 60 + mm


def _time_interval(e: dict) -> tuple[int, int] | None:
    """(start, end) in minutes-since-midnight, using `_TIME_PARSE_RE`. A
    missing/unparseable end (or one that precedes start) collapses to a
    zero-width interval at start. None if start itself doesn't parse —
    we can't compare what we can't read."""
    start = _parse_time_minutes(e.get("time"))
    if start is None:
        return None
    end = _parse_time_minutes(e.get("end_time"))
    if end is None or end < start:
        end = start
    return start, end


def _times_overlap(a: dict, b: dict) -> bool:
    """True iff both events have a parseable start time and their
    [start, end] intervals intersect. Covers exact-start matches (two
    points) and single-time-vs-range matches alike."""
    ia, ib = _time_interval(a), _time_interval(b)
    if ia is None or ib is None:
        return False
    return ia[0] <= ib[1] and ib[0] <= ia[1]


def dedupe_shared_keyword_titles(events: list[dict]) -> list[dict]:
    """Collapse events that share a date + overlapping venue + overlapping
    start time when their titles share at least one significant word.
    Run after `dedupe_substring_titles`, which only catches the substring
    case. Picks the richer event by the same `_richness_score` used there,
    and logs both events' titles *and* `source` so the actual parser pair
    producing the duplicate can be confirmed from the run log.

    O(n²) inside each date — fine for our scale (~15 events/window).
    """
    kept: list[dict] = []
    for cand in events:
        cand_date = cand.get("date", "")
        dup_idx = None
        for i, ex in enumerate(kept):
            if ex.get("date") != cand_date:
                continue
            if not _venue_overlap(ex.get("venue"), cand.get("venue")):
                continue
            if not _times_overlap(ex, cand):
                continue
            if not _shared_significant_word(ex.get("title"), cand.get("title")):
                continue
            dup_idx = i
            break
        if dup_idx is None:
            kept.append(cand)
            continue

        ex = kept[dup_idx]
        if _richness_score(cand) > _richness_score(ex):
            winner, loser = cand, ex
            kept[dup_idx] = cand
        else:
            winner, loser = ex, cand
        LOG.info(
            "dedupe: dropping %r [%s] in favor of %r [%s] "
            "(same date+venue+time, shared-keyword titles)",
            loser.get("title"), loser.get("source"),
            winner.get("title"), winner.get("source"),
        )
    return kept


# --- SEO: schema.org JSON-LD + noscript fallback ------------------------------
# Both are written into index.html by the scraper so search crawlers see real
# event content without needing to execute JavaScript. JSON-LD goes in <head>
# for Google's structured-data parser; the noscript block sits in <main> for
# any crawler that ignores JSON-LD but reads visible body text.

_PRICE_NUM_RE = re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)")
_FREE_RE = re.compile(r"\bfree\b", re.IGNORECASE)
_PWYC_RE = re.compile(r"\b(pwyc|pay[- ]what[- ]you[- ]can)\b", re.IGNORECASE)


def _combine_iso(date_str: str, time_str: str | None) -> str:
    """Combine a "YYYY-MM-DD" date and a "7:30 PM"-style time into an ISO 8601
    string in America/Halifax. Returns the bare date if time is unparseable —
    that's still valid schema.org/Event.startDate."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return date_str
    if not time_str:
        return date_str
    m = _TIME_PARSE_RE.match(time_str.strip())
    if not m:
        return date_str
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    mer = (m.group(3) or "").upper()
    if mer == "PM" and hh != 12:
        hh += 12
    elif mer == "AM" and hh == 12:
        hh = 0
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return date_str
    dt = datetime(d.year, d.month, d.day, hh, mm, tzinfo=HALIFAX)
    return dt.isoformat()


def _parse_offers(
    price: str | None, ticket_url: str | None, valid_from: str | None = None
) -> dict | None:
    """Best-effort price → schema.org Offer / AggregateOffer.

    Returns None when the price string has no extractable amount and isn't
    a recognized "Free" or "PWYC" — better to emit no offer than a wrong one.

    Adds ``availability`` (always InStock — we don't track sold-out state)
    and an optional ``validFrom`` timestamp so crawlers know how stale the
    pricing data is.
    """
    base: dict = {
        "priceCurrency": "CAD",
        "availability": "https://schema.org/InStock",
    }
    if ticket_url:
        base["url"] = ticket_url
    if valid_from:
        base["validFrom"] = valid_from

    if price:
        if _FREE_RE.search(price):
            return {"@type": "Offer", "price": "0", **base}
        if _PWYC_RE.search(price):
            return {"@type": "Offer", "price": "0", "name": "Pay what you can", **base}
        nums = [float(m) for m in _PRICE_NUM_RE.findall(price)]
        if nums:
            lo, hi = min(nums), max(nums)
            fmt = lambda n: str(int(n)) if n.is_integer() else f"{n:.2f}"
            if lo == hi:
                return {"@type": "Offer", "price": fmt(lo), **base}
            return {
                "@type": "AggregateOffer",
                "lowPrice": fmt(lo),
                "highPrice": fmt(hi),
                **base,
            }
    return None


def _event_jsonld(event: dict, valid_from: str | None = None) -> dict:
    start = _combine_iso(event["date"], event.get("time"))
    obj: dict = {
        "@type": "Event",
        "name": event["title"],
        "startDate": start,
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        # Google's rich-result preview wants an image. We don't have
        # per-event art, so every event falls back to the site icon.
        "image": DEFAULT_EVENT_IMAGE,
    }
    if event.get("end_time"):
        obj["endDate"] = _combine_iso(event["date"], event["end_time"])

    place: dict = {"@type": "Place", "name": event.get("venue") or "Lunenburg"}
    if event.get("location"):
        place["address"] = event["location"]
    obj["location"] = place

    # Use the venue as the organizer fallback — it's the closest thing we
    # know about who's putting the event on. Performer is intentionally
    # omitted: we don't reliably know who the performer is (the event
    # title sometimes IS the performer name, but not always), and a
    # fabricated value would be worse than none.
    if event.get("venue"):
        obj["organizer"] = {"@type": "Organization", "name": event["venue"]}

    if event.get("url"):
        obj["url"] = event["url"]
    if event.get("description"):
        obj["description"] = event["description"]

    offers = _parse_offers(
        event.get("price"), event.get("ticket_url"), valid_from=valid_from
    )
    if offers:
        obj["offers"] = offers
    return obj


def render_jsonld_script(
    events: list[dict], valid_from: str | None = None
) -> str:
    """Render the <script type='application/ld+json'> tag content. Always emits
    a single ItemList wrapping the events so the whole page is one structured
    record — easier for Google to attribute than a bare array.

    ``valid_from`` (typically the scrape run's ``last_updated``) is threaded
    into per-event offers so price data carries a freshness timestamp.
    """
    items = [_event_jsonld(e, valid_from=valid_from) for e in events]
    payload = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "Upcoming events in Lunenburg, Nova Scotia",
        "url": SITE_URL,
        "numberOfItems": len(items),
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "item": item}
            for i, item in enumerate(items)
        ],
    }
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    return f'<script type="application/ld+json">\n{body}\n  </script>'


def _format_noscript_date(date_str: str) -> str:
    try:
        d = _date.fromisoformat(date_str)
    except ValueError:
        return date_str
    return f"{_WEEKDAY_LONG[d.weekday()]}, {_MONTH_LONG[d.month - 1]} {d.day}"


def render_noscript_html(events: list[dict]) -> str:
    """Plain semantic markup of the event list — what a crawler without JS
    sees. Grouped by date with weekday headings."""
    if not events:
        return (
            "<p>No upcoming events in the next two weeks. "
            "Check back soon — new events are added regularly.</p>"
        )

    # Group events by date in original (already date-sorted) order.
    by_date: dict[str, list[dict]] = {}
    for e in events:
        by_date.setdefault(e["date"], []).append(e)

    lines = ['<h2>Upcoming events in Lunenburg</h2>']
    for date_str, day_events in by_date.items():
        lines.append(f'<h3>{html.escape(_format_noscript_date(date_str))}</h3>')
        lines.append('<ul>')
        for ev in day_events:
            title = html.escape(ev.get("title") or "Untitled event")
            venue = html.escape(ev.get("venue") or "")
            time = html.escape(ev.get("time") or "")
            bits = []
            if time:
                bits.append(f"<strong>{time}</strong>")
            bits.append(title)
            if venue:
                bits.append(f"at {venue}")
            lines.append(f"  <li>{' — '.join(bits)}</li>")
        lines.append('</ul>')
    return "\n      ".join(lines)


def update_index_html(
    events: list[dict], valid_from: str | None = None
) -> bool:
    """Rewrite the JSON-LD and noscript regions in index.html. Returns True
    when the file actually changed (so the caller can short-circuit a no-op
    write that would just confuse the workflow's git-diff check).

    ``valid_from`` is the scrape run's ``last_updated`` timestamp, threaded
    through to each Offer's validFrom field for crawler freshness signals.
    """
    if not INDEX_HTML_PATH.exists():
        LOG.warning("index.html not found at %s — skipping HTML update",
                    INDEX_HTML_PATH)
        return False
    original = INDEX_HTML_PATH.read_text(encoding="utf-8")

    jsonld_script = render_jsonld_script(events, valid_from=valid_from)
    noscript_body = render_noscript_html(events)

    def jsonld_sub(m: re.Match) -> str:
        return f"{m.group(1)}\n  {jsonld_script}\n  {m.group(3)}"

    def noscript_sub(m: re.Match) -> str:
        return f"{m.group(1)}\n      {noscript_body}\n      {m.group(3)}"

    updated = _JSONLD_RE.sub(jsonld_sub, original, count=1)
    updated = _NOSCRIPT_RE.sub(noscript_sub, updated, count=1)

    if updated == original:
        return False
    INDEX_HTML_PATH.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    session = requests.Session()

    parser_events = collect_events(session)
    LOG.info("collected %d parser events from %d sources",
             len(parser_events), len(ALL_PARSERS))

    manual = load_manual_seeds()
    LOG.info("loaded %d manual seed events", len(manual))

    # Manual seeds go FIRST so they win the dedupe step against any parser
    # entry that happens to share (date, title). They're still subject to the
    # 14-day window — seeds dated outside it just don't appear yet.
    events = manual + parser_events

    # Drop administrative noise (e.g. "Office Closure: ...") before anything
    # else touches the list — see load_exclusion_rules() docstring.
    exclusion_terms = load_exclusion_rules()
    before_excl = len(events)
    events = apply_exclusion_rules(events, exclusion_terms)
    if exclusion_terms:
        LOG.info("exclusion: %d event(s) excluded, %d remain",
                 before_excl - len(events), len(events))

    now = datetime.now(HALIFAX)
    events = filter_to_window(events, now)

    # Apply per-source parking rules before dedup so suppressed events never
    # influence which duplicate wins. Optional file — absent => nothing parked.
    parking_rules = load_parking_rules()
    before = len(events)
    events = apply_parking_rules(events, parking_rules)
    if parking_rules:
        LOG.info("parking: %d event(s) parked, %d remain",
                 before - len(events), len(events))

    events = deduplicate(events)
    events = dedupe_substring_titles(events)
    events = dedupe_shared_keyword_titles(events)
    events.sort(key=_time_sort_key)

    for e in events:
        normalized = normalize_location(e.get("location"))
        if normalized:
            e["location"] = normalized
        else:
            e.pop("location", None)

    last_updated = now.replace(microsecond=0).isoformat()
    payload = {
        "events": events,
        "last_updated": last_updated,
    }
    OUTPUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    LOG.info("wrote %d events to %s", len(events), OUTPUT_PATH)

    html_changed = update_index_html(events, valid_from=last_updated)
    LOG.info("index.html %s", "rewritten" if html_changed else "unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
