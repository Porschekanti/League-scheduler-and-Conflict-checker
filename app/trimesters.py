"""
Academic trimesters, and the season each match belongs to.

A season is no longer something an organizer types in. It is derived from the
match's own start time: the calendar decides which trimester a date falls in,
and the season row is found (or created) from that. One less field to get
wrong, and a match can never be filed under a trimester it does not occur in.

The academic year runs July -> April, so T1 and T2 sit in calendar year Y and
T3 sits in Y+1. Academic year 2026-27 means T1/T2 in 2026 and T3 in 2027.

Boundaries are the literal week-based ones from the academic calendar, which
leaves real gaps between trimesters (late September, late December, early
January) plus the summer break in May and June. A date in a gap is not
rejected — it is attached to the trimester that just ended, so pre-season
friendlies and catch-up fixtures still land somewhere sensible.
"""
import sqlite3
from dataclasses import dataclass
from datetime import date

# (month, day) inclusive bounds within the trimester's own calendar year.
T1_START, T1_END = (7, 1), (9, 24)    # July -> last week of September
T2_START, T2_END = (10, 1), (12, 24)  # first week of October -> last week of December
T3_START, T3_END = (1, 8), (4, 21)    # second week of January -> third week of April

TERM_CODES = ("T1", "T2", "T3")


@dataclass(frozen=True)
class Term:
    """Which trimester a date belongs to.

    `academic_year` is the calendar year the academic year *started* in, so
    T3 of 2026-27 carries academic_year 2026 even though it runs in 2027.
    `in_break` records that the date fell in a gap and was attached to the
    trimester that just ended — the season is still definite, but callers may
    want to surface it.
    """
    academic_year: int
    code: str
    in_break: bool

    @property
    def label(self) -> str:
        return f"{self.academic_year}-{str(self.academic_year + 1)[2:]} {self.code}"

    @property
    def start_date(self) -> date:
        month, day = {"T1": T1_START, "T2": T2_START, "T3": T3_START}[self.code]
        year = self.academic_year + (1 if self.code == "T3" else 0)
        return date(year, month, day)

    @property
    def end_date(self) -> date:
        month, day = {"T1": T1_END, "T2": T2_END, "T3": T3_END}[self.code]
        year = self.academic_year + (1 if self.code == "T3" else 0)
        return date(year, month, day)


def _md(d: date) -> tuple[int, int]:
    return (d.month, d.day)


def resolve_term(d: date) -> Term:
    """The trimester a date belongs to. Never fails — every date resolves."""
    y, md = d.year, _md(d)

    if T1_START <= md <= T1_END:
        return Term(y, "T1", False)
    if T2_START <= md <= T2_END:
        return Term(y, "T2", False)
    if T3_START <= md <= T3_END:
        # T3 runs in the second calendar year of its academic year.
        return Term(y - 1, "T3", False)

    # Gaps, each attached to the trimester that just ended.
    if T1_END < md < T2_START:               # late September
        return Term(y, "T1", True)
    if md > T2_END:                          # late December
        return Term(y, "T2", True)
    if md < T3_START:                        # early January
        return Term(y - 1, "T2", True)
    return Term(y - 1, "T3", True)           # late April, May, June


def resolve_season(conn: sqlite3.Connection, sport: str, on: date) -> int:
    """The season id for this sport in the trimester containing `on`.

    Creates the season row the first time a sport is scheduled in a trimester,
    so organizers never have to set one up in advance. Callers are expected to
    already hold a transaction where that matters.
    """
    term = resolve_term(on)
    row = conn.execute(
        "SELECT id FROM seasons WHERE sport = ? AND academic_year = ? AND term_code = ?",
        (sport, term.academic_year, term.code),
    ).fetchone()
    if row is not None:
        return row["id"]

    cur = conn.execute(
        """
        INSERT INTO seasons (sport, start_date, end_date, is_active, academic_year, term_code)
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (
            sport,
            term.start_date.isoformat(),
            term.end_date.isoformat(),
            term.academic_year,
            term.code,
        ),
    )
    return cur.lastrowid
