import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get(
    "SCHEDULER_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "scheduler.db"),
))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('HEAD','REP','VIEWER')),
    sport_scope TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER
);

CREATE TABLE IF NOT EXISTS academic_terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL UNIQUE,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER
);

CREATE TABLE IF NOT EXISTS role_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK(role IN ('HEAD','REP')),
    sport_scope TEXT,
    term_id INTEGER NOT NULL REFERENCES academic_terms(id),
    nominated_user_id INTEGER NOT NULL REFERENCES users(id),
    nominated_by_user_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL CHECK(status IN ('PENDING','ACCEPTED','REVOKED')) DEFAULT 'PENDING',
    created_at TEXT NOT NULL,
    accepted_at TEXT,
    created_at_audit TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER
);

-- A season is one sport in one academic trimester, derived from a match's own
-- date rather than entered by hand. academic_year is the year the academic year
-- *started* in, so T3 of 2026-27 carries 2026 even though it runs in 2027. The
-- UNIQUE constraint is what makes find-or-create safe when two bookings for the
-- same sport and trimester race each other.
CREATE TABLE IF NOT EXISTS seasons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sport TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    academic_year INTEGER,
    term_code TEXT CHECK(term_code IN ('T1','T2','T3')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER,
    UNIQUE(sport, academic_year, term_code)
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    name TEXT NOT NULL,
    roll_number TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sport TEXT NOT NULL,
    season_id INTEGER NOT NULL REFERENCES seasons(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER
);

-- sport/season are denormalized onto team_members specifically so the
-- UNIQUE constraint below can enforce "a player may not join two teams
-- in the same sport + season" directly at the database level.
CREATE TABLE IF NOT EXISTS team_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    sport TEXT NOT NULL,
    season_id INTEGER NOT NULL REFERENCES seasons(id),
    joined_at TEXT NOT NULL,
    created_at_audit TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER,
    UNIQUE(player_id, sport, season_id)
);

CREATE TABLE IF NOT EXISTS venues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    location TEXT,
    capacity INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL CHECK(status IN ('QUEUED','RUNNING','COMPLETED','FAILED')) DEFAULT 'QUEUED',
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    result TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    venue_id INTEGER NOT NULL REFERENCES venues(id),
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('DRAFT','CONFIRMED','CANCELLED')) DEFAULT 'DRAFT',
    sport TEXT NOT NULL,
    season_id INTEGER NOT NULL REFERENCES seasons(id),
    version INTEGER NOT NULL DEFAULT 1,
    job_id INTEGER REFERENCES jobs(id),
    created_at_audit TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER
);

-- Bookings that live in the master sheet rather than in this system. They are
-- mirrored locally so the conflict engine can check them with plain SQL like
-- any other commitment — the engine must never depend on a network call.
-- external_ref is the sheet's own row identity, so a resync updates in place
-- instead of duplicating.
CREATE TABLE IF NOT EXISTS external_bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_ref TEXT NOT NULL,
    venue_id INTEGER REFERENCES venues(id),
    venue_name TEXT,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    description TEXT,
    synced_at TEXT NOT NULL,
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, external_ref)
);

-- Who is committed by an external booking, by roll number. Roll number rather
-- than player_id because the sheet is maintained by people, not by this system,
-- and may name a student who has no player row here yet.
CREATE TABLE IF NOT EXISTS external_booking_players (
    booking_id INTEGER NOT NULL REFERENCES external_bookings(id) ON DELETE CASCADE,
    roll_number TEXT NOT NULL,
    created_by_user_id INTEGER,
    PRIMARY KEY (booking_id, roll_number)
);

-- SQLite has no native RLS feature. These triggers provide the database-side
-- deny-by-default boundary: only a connection whose actor was established by
-- the authenticated API (HEAD/REP), or an internal SYSTEM connection used by
-- seed/migration/worker code, may mutate scheduling rows. A connection opened
-- directly with sqlite3 has no actor function and therefore cannot write.
CREATE TRIGGER IF NOT EXISTS rls_users_insert BEFORE INSERT ON users BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_users_update BEFORE UPDATE ON users BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_users_delete BEFORE DELETE ON users BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;

CREATE TRIGGER IF NOT EXISTS rls_terms_insert BEFORE INSERT ON academic_terms BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_terms_update BEFORE UPDATE ON academic_terms BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_terms_delete BEFORE DELETE ON academic_terms BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;

CREATE TRIGGER IF NOT EXISTS rls_role_assignments_insert BEFORE INSERT ON role_assignments BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_role_assignments_update BEFORE UPDATE ON role_assignments BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_role_assignments_delete BEFORE DELETE ON role_assignments BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;

CREATE TRIGGER IF NOT EXISTS rls_seasons_insert BEFORE INSERT ON seasons BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_seasons_update BEFORE UPDATE ON seasons BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_seasons_delete BEFORE DELETE ON seasons BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;

CREATE TRIGGER IF NOT EXISTS rls_players_insert BEFORE INSERT ON players BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_players_update BEFORE UPDATE ON players BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_players_delete BEFORE DELETE ON players BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;

CREATE TRIGGER IF NOT EXISTS rls_teams_insert BEFORE INSERT ON teams BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_teams_update BEFORE UPDATE ON teams BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_teams_delete BEFORE DELETE ON teams BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;

CREATE TRIGGER IF NOT EXISTS rls_team_members_insert BEFORE INSERT ON team_members BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_team_members_update BEFORE UPDATE ON team_members BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_team_members_delete BEFORE DELETE ON team_members BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;

CREATE TRIGGER IF NOT EXISTS rls_venues_insert BEFORE INSERT ON venues BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_venues_update BEFORE UPDATE ON venues BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_venues_delete BEFORE DELETE ON venues BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;

CREATE TRIGGER IF NOT EXISTS rls_jobs_insert BEFORE INSERT ON jobs BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_jobs_update BEFORE UPDATE ON jobs BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_jobs_delete BEFORE DELETE ON jobs BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;

CREATE TRIGGER IF NOT EXISTS rls_matches_insert BEFORE INSERT ON matches BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_matches_update BEFORE UPDATE ON matches BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_matches_delete BEFORE DELETE ON matches BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;

CREATE TRIGGER IF NOT EXISTS rls_external_bookings_insert BEFORE INSERT ON external_bookings BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_external_bookings_update BEFORE UPDATE ON external_bookings BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_external_bookings_delete BEFORE DELETE ON external_bookings BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;

CREATE TRIGGER IF NOT EXISTS rls_external_booking_players_insert BEFORE INSERT ON external_booking_players BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_external_booking_players_update BEFORE UPDATE ON external_booking_players BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;
CREATE TRIGGER IF NOT EXISTS rls_external_booking_players_delete BEFORE DELETE ON external_booking_players BEGIN
    SELECT CASE WHEN current_actor_role() NOT IN ('HEAD','REP','SYSTEM')
        THEN RAISE(ABORT, 'RLS: authenticated sports staff required') END;
END;

-- The conflict engine runs these lookups on every single proposed
-- match, so they are the only queries whose shape is worth indexing for.
CREATE INDEX IF NOT EXISTS idx_matches_venue ON matches(venue_id, status);
CREATE INDEX IF NOT EXISTS idx_matches_home_team ON matches(home_team_id);
CREATE INDEX IF NOT EXISTS idx_matches_away_team ON matches(away_team_id);
CREATE INDEX IF NOT EXISTS idx_team_members_team ON team_members(team_id);
CREATE INDEX IF NOT EXISTS idx_team_members_player ON team_members(player_id);
CREATE INDEX IF NOT EXISTS idx_external_bookings_venue ON external_bookings(venue_id);
CREATE INDEX IF NOT EXISTS idx_external_booking_players_roll
    ON external_booking_players(roll_number);
"""

# Populate the audit actor on rows written through an authenticated API
# connection.  The RLS triggers above decide whether a write is permitted;
# these AFTER triggers record who performed it without changing any handler's
# SQL.  SYSTEM migrations intentionally leave the user columns NULL.
_AUDIT_TABLES = {
    "users": "id",
    "academic_terms": "id",
    "role_assignments": "id",
    "seasons": "id",
    "players": "id",
    "teams": "id",
    "team_members": "id",
    "venues": "id",
    "jobs": "id",
    "matches": "id",
    "external_bookings": "id",
}

_AUDIT_TRIGGERS = "\n".join(
    f"""CREATE TRIGGER IF NOT EXISTS audit_{table}_insert
AFTER INSERT ON {table}
WHEN current_actor_id() IS NOT NULL
BEGIN
    UPDATE {table}
       SET created_by_user_id = current_actor_id(),
           updated_by_user_id = current_actor_id()
     WHERE {key} = NEW.{key};
END;
CREATE TRIGGER IF NOT EXISTS audit_{table}_update
AFTER UPDATE ON {table}
WHEN current_actor_id() IS NOT NULL
 AND NEW.updated_by_user_id IS NOT current_actor_id()
BEGIN
    UPDATE {table}
       SET updated_by_user_id = current_actor_id(),
           updated_at = CURRENT_TIMESTAMP
     WHERE {key} = NEW.{key};
END;"""
    for table, key in _AUDIT_TABLES.items()
)

_AUDIT_TRIGGERS += """
CREATE TRIGGER IF NOT EXISTS audit_external_booking_players_insert
AFTER INSERT ON external_booking_players
WHEN current_actor_id() IS NOT NULL
BEGIN
    UPDATE external_booking_players
       SET created_by_user_id = current_actor_id()
     WHERE booking_id = NEW.booking_id AND roll_number = NEW.roll_number;
END;
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    actor = {"role": "ANONYMOUS", "id": None}
    conn.create_function("current_actor_role", 0, lambda: actor["role"])
    conn.create_function("current_actor_id", 0, lambda: actor["id"])
    conn.create_function(
        "set_db_actor", 2,
        lambda role, user_id=None: actor.update(
            role=str(role or "ANONYMOUS"),
            id=int(user_id) if user_id is not None else None,
        ) or 1,
    )
    # SQLite deployment guardrails, per the design doc.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def set_actor(conn: sqlite3.Connection, role: str, user_id: int | None = None) -> None:
    """Set the authenticated actor used by the SQLite RLS triggers."""
    conn.execute("SELECT set_db_actor(?, ?)", (role, user_id))


def _add_audit_columns(conn: sqlite3.Connection) -> None:
    """Backfill columns on databases created before the SQLite migration."""
    columns = {
        "users": ("created_at", "updated_at", "created_by_user_id", "updated_by_user_id"),
        "academic_terms": ("created_at", "updated_at", "created_by_user_id", "updated_by_user_id"),
        "role_assignments": ("created_at_audit", "updated_at", "created_by_user_id", "updated_by_user_id"),
        "seasons": ("created_at", "updated_at", "created_by_user_id", "updated_by_user_id"),
        "players": ("created_at", "updated_at", "created_by_user_id", "updated_by_user_id"),
        "teams": ("created_at", "updated_at", "created_by_user_id", "updated_by_user_id"),
        "team_members": ("created_at_audit", "updated_at", "created_by_user_id", "updated_by_user_id"),
        "venues": ("created_at", "updated_at", "created_by_user_id", "updated_by_user_id"),
        "jobs": ("updated_at", "created_by_user_id", "updated_by_user_id"),
        "matches": ("created_at_audit", "updated_at", "created_by_user_id", "updated_by_user_id"),
        "external_bookings": ("created_by_user_id", "updated_by_user_id", "updated_at"),
        "external_booking_players": ("created_by_user_id",),
    }
    for table, wanted in columns.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column in wanted:
            if column in existing:
                continue
            column_type = "INTEGER" if column.endswith("_user_id") else "TEXT"
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
        # Nullable audit fields are intentionally safe for legacy rows. New
        # rows receive timestamps through table defaults in the fresh schema.
        if "created_at" in wanted and "created_at" not in existing:
            conn.execute(f"UPDATE {table} SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
        if "created_at_audit" in wanted and "created_at_audit" not in existing:
            conn.execute(f"UPDATE {table} SET created_at_audit = CURRENT_TIMESTAMP WHERE created_at_audit IS NULL")
        if "updated_at" in wanted and "updated_at" not in existing:
            conn.execute(f"UPDATE {table} SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")


def init_db() -> None:
    conn = get_connection()
    set_actor(conn, "SYSTEM")
    conn.executescript(SCHEMA)
    _add_audit_columns(conn)
    conn.executescript(_AUDIT_TRIGGERS)
    conn.commit()
    conn.close()
