"""Run once after init: python seed.py"""
from datetime import date

from app.db import get_connection, init_db, set_actor
from app.security import hash_password
from app.trimesters import resolve_season, resolve_term

init_db()
conn = get_connection()
set_actor(conn, "SYSTEM")

conn.execute(
    "INSERT INTO users (name, email, password_hash, role, sport_scope) VALUES (?, ?, ?, ?, ?)",
    ("College Sports Head", "head@example.edu", hash_password("head-pass"), "HEAD", None),
)
conn.execute(
    "INSERT INTO users (name, email, password_hash, role, sport_scope) VALUES (?, ?, ?, ?, ?)",
    ("Basketball Rep", "bball-rep@example.edu", hash_password("rep-pass"), "REP", "Basketball"),
)
conn.execute(
    "INSERT INTO users (name, email, password_hash, role, sport_scope) VALUES (?, ?, ?, ?, ?)",
    ("Cricket Rep", "cricket-rep@example.edu", hash_password("rep-pass"), "REP", "Cricket"),
)

# One-time bootstrap exception: the first seeded HEAD starts with an accepted
# ledger row because nobody exists yet to nominate them.
head_id = conn.execute("SELECT id FROM users WHERE email = ?", ("head@example.edu",)).fetchone()[0]
term_cur = conn.execute(
    "INSERT INTO academic_terms (label, start_date, end_date, is_current) VALUES (?, ?, ?, 1)",
    ("2026-27", "2026-07-01", "2027-06-30"),
)
conn.execute(
    """INSERT INTO role_assignments
       (role, sport_scope, term_id, nominated_user_id, nominated_by_user_id,
        status, created_at, accepted_at)
       VALUES ('HEAD', NULL, ?, ?, ?, 'ACCEPTED', datetime('now'), datetime('now'))""",
    (term_cur.lastrowid, head_id, head_id),
)
conn.execute("UPDATE users SET role = 'HEAD', sport_scope = NULL WHERE id = ?", (head_id,))

# Seasons are no longer invented by hand — they follow the academic calendar.
# Teams are registered in whichever trimester today falls in; a match scheduled
# in a later trimester creates that season by itself on first booking.
today = date.today()
current_term = resolve_term(today)
basketball_season = resolve_season(conn, "Basketball", today)
cricket_season = resolve_season(conn, "Cricket", today)

conn.execute("INSERT INTO venues (name, location, capacity) VALUES (?, ?, ?)",
             ("Main Court", "Sports Complex", 200))
conn.execute("INSERT INTO venues (name, location, capacity) VALUES (?, ?, ?)",
             ("Cricket Ground", "East Campus", 500))

conn.execute("INSERT INTO teams (name, sport, season_id) VALUES (?, 'Basketball', ?)",
             ("Goon Squad", basketball_season))
conn.execute("INSERT INTO teams (name, sport, season_id) VALUES (?, 'Basketball', ?)",
             ("Rebels", basketball_season))
conn.execute("INSERT INTO teams (name, sport, season_id) VALUES (?, 'Cricket', ?)",
             ("Strikers", cricket_season))
conn.execute("INSERT INTO teams (name, sport, season_id) VALUES (?, 'Cricket', ?)",
             ("Chargers", cricket_season))

conn.commit()
conn.close()
print("Seeded: head@example.edu / head-pass, bball-rep@example.edu / rep-pass, cricket-rep@example.edu / rep-pass")
print("Teams: 1=Goon Squad (Basketball), 2=Rebels (Basketball), 3=Strikers (Cricket), 4=Chargers (Cricket)")
print(f"Venues: 1=Main Court, 2=Cricket Ground")
print(f"Current trimester: {current_term.label} "
      f"({current_term.start_date} .. {current_term.end_date})"
      f"{' — today is in a break, attached to this term' if current_term.in_break else ''}")
print(f"Seasons: {basketball_season}=Basketball {current_term.label}, "
      f"{cricket_season}=Cricket {current_term.label}")
