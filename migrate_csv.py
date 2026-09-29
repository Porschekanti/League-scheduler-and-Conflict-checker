"""Import the legacy master-sheet CSV into the SQLite mirror.

The repository's CSV representation is the master booking sheet.  Its stable
columns are ``ref,venue,start,end,description,players`` and ``players`` is a
semicolon-separated list of roll numbers.  The importer is deliberately
repeatable: rows are upserted by ``(source, ref)`` and rows removed from the
    CSV are removed from the local mirror on the next import.

Usage::

    python migrate_csv.py --csv bookings.csv --database scheduler.db

The SQLite file is created when it does not exist.  The importer uses the
SYSTEM actor because it is an offline migration, not an end-user API request.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path, help="master-sheet CSV")
    parser.add_argument(
        "--database", default="scheduler.db", type=Path,
        help="destination SQLite file (default: scheduler.db)",
    )
    args = parser.parse_args()

    # app.db resolves the path at import time, so set it before importing the
    # application modules.
    os.environ["SCHEDULER_DB_PATH"] = str(args.database)
    from app.db import get_connection, init_db, set_actor
    from app.sheets import CsvMasterSheet, sync_from_sheet

    init_db()
    conn = get_connection()
    set_actor(conn, "SYSTEM")
    try:
        report = sync_from_sheet(conn, CsvMasterSheet(args.csv))
        conn.commit()
    finally:
        conn.close()

    print(f"Imported {report['synced']} booking row(s) into {args.database}")
    if report["removed"]:
        print(f"Removed {report['removed']} row(s) withdrawn from the CSV")
    if report["rejected"]:
        print(f"Rejected {len(report['rejected'])} malformed row(s): {report['rejected']}")
    if report["unmapped_venues"]:
        print(f"Unmapped venue name(s): {report['unmapped_venues']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
