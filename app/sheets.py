"""
The master sheet link.

Bookings also live in a Google Sheet that people edit by hand, and those
bookings hold venues and commit students just as firmly as the ones made here.
The conflict engine has to see them, or it will happily double-book against a
row somebody typed into the sheet this morning.

Two rules shape the design:

* **The conflict engine never makes a network call.** Sheet rows are pulled
  into `external_bookings` and checked with plain SQL like everything else. A
  booking must not fail, or silently pass, because Google was slow or down.

* **The backend is swappable.** `MasterSheet` is the interface; the CSV backend
  works today with no credentials, and the Google backend takes over once a
  service account exists. Nothing above this module knows which is in use.

Configuration, all via environment:

    MASTER_SHEET_BACKEND   none (default) | csv | google
    MASTER_SHEET_CSV       path to the CSV, for the csv backend
    MASTER_SHEET_ID        spreadsheet id, for the google backend
    MASTER_SHEET_TAB       worksheet name (default "Bookings")
    MASTER_SHEET_CREDENTIALS  path to a service-account JSON key

The default is `none`, so the system behaves exactly as before until somebody
opts in. A misconfigured sheet is reported loudly rather than treated as empty
— "the sheet has no bookings" and "I could not read the sheet" must never be
the same answer, for the same reason a malformed match is never "conflict-free".
"""
import csv
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.conflicts import MatchValidationError, normalize_instant

CSV_COLUMNS = ["ref", "venue", "start", "end", "description", "players"]


class SheetUnavailable(RuntimeError):
    """The sheet is configured but cannot be reached or read."""


@dataclass
class ExternalBooking:
    """One row of the master sheet, as this system understands it."""
    external_ref: str
    venue_name: str
    start_time: str
    end_time: str
    description: str = ""
    roll_numbers: list[str] = field(default_factory=list)

    def normalized(self) -> "ExternalBooking":
        """Canonical UTC times, so sheet rows compare against our own rows.

        Raises MatchValidationError for a row whose times cannot be parsed —
        the caller decides whether to skip it, but it is never silently
        treated as a booking covering no time at all.
        """
        return ExternalBooking(
            external_ref=self.external_ref,
            venue_name=self.venue_name,
            start_time=normalize_instant(self.start_time, "sheet start"),
            end_time=normalize_instant(self.end_time, "sheet end"),
            description=self.description,
            roll_numbers=[r.strip() for r in self.roll_numbers if r.strip()],
        )


class MasterSheet:
    """What the rest of the system may assume about a master sheet."""

    name = "none"
    writable = False

    def pull(self) -> list[ExternalBooking]:
        return []

    def push(self, booking: ExternalBooking) -> None:
        """Record one of our bookings in the sheet. No-op when not writable."""
        return None


class NullMasterSheet(MasterSheet):
    """No sheet configured. Every call is a no-op, by design."""


class CsvMasterSheet(MasterSheet):
    """A local CSV standing in for the sheet.

    Real enough to develop and test the whole sync path — the same rows, the
    same columns, the same conflict behaviour — without any credentials.
    """

    writable = True

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.name = f"csv:{self.path.name}"

    def pull(self) -> list[ExternalBooking]:
        if not self.path.exists():
            raise SheetUnavailable(f"master sheet CSV not found: {self.path}")
        with self.path.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        missing = [c for c in ("ref", "start", "end") if rows and c not in rows[0]]
        if missing:
            raise SheetUnavailable(
                f"master sheet CSV is missing required column(s): {', '.join(missing)}"
            )
        return [
            ExternalBooking(
                external_ref=r.get("ref", "").strip(),
                venue_name=(r.get("venue") or "").strip(),
                start_time=(r.get("start") or "").strip(),
                end_time=(r.get("end") or "").strip(),
                description=(r.get("description") or "").strip(),
                roll_numbers=[p for p in (r.get("players") or "").split(";")],
            )
            for r in rows
            if r.get("ref", "").strip()
        ]

    def push(self, booking: ExternalBooking) -> None:
        exists = self.path.exists()
        with self.path.open("a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
            if not exists:
                writer.writeheader()
            writer.writerow(
                {
                    "ref": booking.external_ref,
                    "venue": booking.venue_name,
                    "start": booking.start_time,
                    "end": booking.end_time,
                    "description": booking.description,
                    "players": ";".join(booking.roll_numbers),
                }
            )


class GoogleMasterSheet(MasterSheet):
    """The real thing, once a service account exists.

    Deliberately imports gspread lazily: the dependency is optional, and the
    rest of the system must run without it.
    """

    writable = True

    def __init__(self, sheet_id: str, credentials_path: str, tab: str = "Bookings"):
        self.sheet_id = sheet_id
        self.credentials_path = credentials_path
        self.tab = tab
        self.name = f"google:{sheet_id}"

    def _worksheet(self):
        try:
            import gspread
        except ImportError as exc:
            raise SheetUnavailable(
                "the google backend needs gspread: pip install gspread"
            ) from exc
        if not Path(self.credentials_path).exists():
            raise SheetUnavailable(
                f"service-account key not found: {self.credentials_path}"
            )
        try:
            client = gspread.service_account(filename=self.credentials_path)
            return client.open_by_key(self.sheet_id).worksheet(self.tab)
        except Exception as exc:  # noqa: BLE001 - any failure here means unusable
            raise SheetUnavailable(
                f"could not open sheet {self.sheet_id} tab '{self.tab}': {exc}. "
                "Share the sheet with the service account's client_email as Editor."
            ) from exc

    def pull(self) -> list[ExternalBooking]:
        return [
            ExternalBooking(
                external_ref=str(r.get("ref", "")).strip(),
                venue_name=str(r.get("venue", "")).strip(),
                start_time=str(r.get("start", "")).strip(),
                end_time=str(r.get("end", "")).strip(),
                description=str(r.get("description", "")).strip(),
                roll_numbers=[p for p in str(r.get("players", "")).split(";")],
            )
            for r in self._worksheet().get_all_records()
            if str(r.get("ref", "")).strip()
        ]

    def push(self, booking: ExternalBooking) -> None:
        self._worksheet().append_row(
            [
                booking.external_ref,
                booking.venue_name,
                booking.start_time,
                booking.end_time,
                booking.description,
                ";".join(booking.roll_numbers),
            ]
        )


def get_master_sheet() -> MasterSheet:
    """Build the configured backend. Never guesses — unset means none."""
    backend = os.environ.get("MASTER_SHEET_BACKEND", "none").strip().lower()
    if backend in ("", "none", "off"):
        return NullMasterSheet()
    if backend == "csv":
        path = os.environ.get("MASTER_SHEET_CSV")
        if not path:
            raise SheetUnavailable("MASTER_SHEET_BACKEND=csv but MASTER_SHEET_CSV is unset")
        return CsvMasterSheet(path)
    if backend == "google":
        sheet_id = os.environ.get("MASTER_SHEET_ID")
        creds = os.environ.get("MASTER_SHEET_CREDENTIALS")
        if not sheet_id or not creds:
            raise SheetUnavailable(
                "MASTER_SHEET_BACKEND=google needs MASTER_SHEET_ID and "
                "MASTER_SHEET_CREDENTIALS"
            )
        return GoogleMasterSheet(
            sheet_id, creds, os.environ.get("MASTER_SHEET_TAB", "Bookings")
        )
    raise SheetUnavailable(f"unknown MASTER_SHEET_BACKEND: {backend!r}")


def sync_from_sheet(conn: sqlite3.Connection, sheet: MasterSheet | None = None) -> dict:
    """Mirror the sheet into `external_bookings`, and report what happened.

    Upserts on (source, external_ref) so a resync updates rows in place, and
    deletes local rows whose sheet row has gone. Rows that cannot be parsed are
    returned under `rejected` rather than dropped quietly — a row the engine
    cannot read is a booking it cannot protect.
    """
    sheet = sheet or get_master_sheet()
    pulled = sheet.pull()
    now = datetime.now(timezone.utc).isoformat()

    seen, rejected, unmapped_venues = [], [], []
    for raw in pulled:
        try:
            booking = raw.normalized()
        except MatchValidationError as exc:
            rejected.append({"ref": raw.external_ref, "reason": str(exc)})
            continue

        venue = conn.execute(
            "SELECT id FROM venues WHERE name = ?", (booking.venue_name,)
        ).fetchone()
        venue_id = venue["id"] if venue else None
        if booking.venue_name and venue_id is None:
            unmapped_venues.append({"ref": booking.external_ref, "venue": booking.venue_name})

        conn.execute(
            """
            INSERT INTO external_bookings
                (source, external_ref, venue_id, venue_name, start_time, end_time,
                 description, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, external_ref) DO UPDATE SET
                venue_id = excluded.venue_id,
                venue_name = excluded.venue_name,
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                description = excluded.description,
                synced_at = excluded.synced_at
            """,
            (
                sheet.name,
                booking.external_ref,
                venue_id,
                booking.venue_name,
                booking.start_time,
                booking.end_time,
                booking.description,
                now,
            ),
        )
        booking_id = conn.execute(
            "SELECT id FROM external_bookings WHERE source = ? AND external_ref = ?",
            (sheet.name, booking.external_ref),
        ).fetchone()["id"]

        conn.execute(
            "DELETE FROM external_booking_players WHERE booking_id = ?", (booking_id,)
        )
        for roll in set(booking.roll_numbers):
            conn.execute(
                "INSERT INTO external_booking_players (booking_id, roll_number) VALUES (?, ?)",
                (booking_id, roll),
            )
        seen.append(booking.external_ref)

    # Rows withdrawn from the sheet must stop blocking bookings here.
    removed = 0
    for row in conn.execute(
        "SELECT id, external_ref FROM external_bookings WHERE source = ?", (sheet.name,)
    ).fetchall():
        if row["external_ref"] not in seen:
            conn.execute(
                "DELETE FROM external_booking_players WHERE booking_id = ?", (row["id"],)
            )
            conn.execute("DELETE FROM external_bookings WHERE id = ?", (row["id"],))
            removed += 1

    return {
        "source": sheet.name,
        "synced": len(seen),
        "removed": removed,
        "rejected": rejected,
        "unmapped_venues": unmapped_venues,
    }


def booking_for_match(conn: sqlite3.Connection, match_id: int) -> ExternalBooking:
    """Render one of our matches in the sheet's own shape."""
    match = conn.execute(
        """
        SELECT m.*, v.name AS venue_name,
               ht.name AS home_name, at.name AS away_name
        FROM matches m
        JOIN venues v ON v.id = m.venue_id
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        WHERE m.id = ?
        """,
        (match_id,),
    ).fetchone()
    if match is None:
        raise ValueError(f"match {match_id} does not exist")

    rolls = [
        r["roll_number"]
        for r in conn.execute(
            """
            SELECT DISTINCT p.roll_number FROM team_members tm
            JOIN players p ON p.id = tm.player_id
            WHERE tm.team_id IN (?, ?)
            """,
            (match["home_team_id"], match["away_team_id"]),
        ).fetchall()
    ]
    return ExternalBooking(
        external_ref=f"scheduler-match-{match_id}",
        venue_name=match["venue_name"],
        start_time=match["start_time"],
        end_time=match["end_time"],
        description=f"{match['sport']}: {match['home_name']} vs {match['away_name']}",
        roll_numbers=rolls,
    )


def push_match(
    conn: sqlite3.Connection, match_id: int, sheet: MasterSheet | None = None
) -> bool:
    """Publish a confirmed match to the sheet. False when no sheet is writable.

    Never raises into the request path: a booking that is already committed
    here must not be reported as failed because the sheet was unreachable.
    """
    sheet = sheet or get_master_sheet()
    if not sheet.writable:
        return False
    try:
        sheet.push(booking_for_match(conn, match_id))
        return True
    except Exception:  # noqa: BLE001 - sheet trouble must not undo a local commit
        return False
