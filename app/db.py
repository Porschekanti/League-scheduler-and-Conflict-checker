import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "scheduler.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('HEAD','REP','VIEWER')),
    sport_scope TEXT
);

CREATE TABLE IF NOT EXISTS academic_terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL UNIQUE,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 0
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
    accepted_at TEXT
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
    UNIQUE(sport, academic_year, term_code)
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    name TEXT NOT NULL,
    roll_number TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sport TEXT NOT NULL,
    season_id INTEGER NOT NULL REFERENCES seasons(id)
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
    UNIQUE(player_id, sport, season_id)
);

CREATE TABLE IF NOT EXISTS venues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    location TEXT,
    capacity INTEGER
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL CHECK(status IN ('QUEUED','RUNNING','COMPLETED','FAILED')) DEFAULT 'QUEUED',
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    result TEXT
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
    job_id INTEGER REFERENCES jobs(id)
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
    UNIQUE(source, external_ref)
);

-- Who is committed by an external booking, by roll number. Roll number rather
-- than player_id because the sheet is maintained by people, not by this system,
-- and may name a student who has no player row here yet.
CREATE TABLE IF NOT EXISTS external_booking_players (
    booking_id INTEGER NOT NULL REFERENCES external_bookings(id) ON DELETE CASCADE,
    roll_number TEXT NOT NULL,
    PRIMARY KEY (booking_id, roll_number)
);

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


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # SQLite deployment guardrails, per the design doc.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
