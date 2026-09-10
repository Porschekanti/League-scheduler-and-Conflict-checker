"""Run once after init: python seed.py"""
from app.db import get_connection, init_db
from app.security import hash_password

init_db()
conn = get_connection()

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

conn.execute(
    "INSERT INTO seasons (sport, start_date, end_date, is_active) VALUES (?, ?, ?, 1)",
    ("Basketball", "2026-01-01", "2026-12-31"),
)
conn.execute(
    "INSERT INTO seasons (sport, start_date, end_date, is_active) VALUES (?, ?, ?, 1)",
    ("Cricket", "2026-01-01", "2026-12-31"),
)

conn.execute("INSERT INTO venues (name, location, capacity) VALUES (?, ?, ?)",
             ("Main Court", "Sports Complex", 200))
conn.execute("INSERT INTO venues (name, location, capacity) VALUES (?, ?, ?)",
             ("Cricket Ground", "East Campus", 500))

conn.execute("INSERT INTO teams (name, sport, season_id) VALUES (?, 'Basketball', 1)", ("Goon Squad",))
conn.execute("INSERT INTO teams (name, sport, season_id) VALUES (?, 'Basketball', 1)", ("Rebels",))
conn.execute("INSERT INTO teams (name, sport, season_id) VALUES (?, 'Cricket', 2)", ("Strikers",))

conn.commit()
conn.close()
print("Seeded: head@example.edu / head-pass, bball-rep@example.edu / rep-pass, cricket-rep@example.edu / rep-pass")
print("Teams: 1=Goon Squad (Basketball), 2=Rebels (Basketball), 3=Strikers (Cricket)")
print("Venues: 1=Main Court, 2=Cricket Ground | Seasons: 1=Basketball 2026, 2=Cricket 2026")
