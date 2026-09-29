# SQLite data dictionary

The application uses one SQLite database.  The CSV master sheet is an input
format only; `migrate_csv.py` imports its rows into `external_bookings` and
`external_booking_players` so conflict checks never depend on a CSV file being
available at request time.

## Columns used by the application

| Table | Columns | Purpose |
|---|---|---|
| `users` | `id`, `name`, `email`, `password_hash`, `role`, `sport_scope`, `created_at`, `updated_at`, `created_by_user_id`, `updated_by_user_id` | Login identity and HEAD/REP authorization scope. |
| `academic_terms` | `id`, `label`, `start_date`, `end_date`, `is_current`, audit columns | Academic calendar used by trimester resolution. |
| `role_assignments` | `id`, `role`, `sport_scope`, `term_id`, `nominated_user_id`, `nominated_by_user_id`, `status`, `created_at`, `accepted_at`, audit columns | Role nomination, acceptance and revocation ledger. |
| `seasons` | `id`, `sport`, `start_date`, `end_date`, `is_active`, `academic_year`, `term_code`, audit columns | One sport in one academic trimester. |
| `players` | `id`, `user_id`, `name`, `roll_number`, `status`, audit columns | One student, unique by roll number. |
| `teams` | `id`, `name`, `sport`, `season_id`, audit columns | A registered team. |
| `team_members` | `id`, `team_id`, `player_id`, `sport`, `season_id`, `joined_at`, audit columns | Player/team membership; the denormalized sport/season pair enforces duplicate-roster rules. |
| `venues` | `id`, `name`, `location`, `capacity`, audit columns | Bookable facilities. |
| `matches` | `id`, `home_team_id`, `away_team_id`, `venue_id`, `start_time`, `end_time`, `status`, `sport`, `season_id`, `version`, `job_id`, audit columns | Draft/confirmed/cancelled booking and optimistic version. |
| `jobs` | `id`, `status`, `created_at`, `payload`, `result`, `updated_at`, audit columns | Asynchronous schedule-generation request. |
| `external_bookings` | `id`, `source`, `external_ref`, `venue_id`, `venue_name`, `start_time`, `end_time`, `description`, `synced_at`, audit columns | Local mirror of a master-sheet booking. |
| `external_booking_players` | `booking_id`, `roll_number`, audit column | Students committed by an external booking. |

The required CSV columns are `ref`, `venue`, `start`, `end`,
`description`, and `players`. `ref` is the stable row identity; `venue` is
matched to `venues.name`; `start` and `end` are ISO-8601 timestamps; and
`players` is a semicolon-separated list of roll numbers.

## Write protection

SQLite has no native row-level security. This project installs BEFORE
INSERT/UPDATE/DELETE triggers on every scheduling table, which abort unless the
connection's actor is `HEAD`, `REP` or `SYSTEM`. The actor is a per-connection
value set by `app.db.set_actor` — by `get_current_user` for API requests, and
explicitly by the worker, the seed script and the importers.

### What this does and does not protect

It is a **guardrail against accident, not a security boundary.** The
distinction matters, because calling it "RLS" invites over-trust:

| Scenario | Result | Verified |
|---|---|---|
| `sqlite3 scheduler.db` then `UPDATE`/`DELETE` | Aborted — `no such function: current_actor_role` | yes |
| A script that opens the file and defines `current_actor_role()` itself | **Full write access** | yes |
| Application code calling `set_db_actor('SYSTEM')` on its own connection | **Full write access** | yes |

The function is registered per connection, so anyone who can open the database
file can register their own and return whatever they like. This stops a
careless script, a stray REPL, or someone poking at the file by hand. It does
not stop anyone who wants in, and it is no substitute for file permissions.

Real authorization lives in the API: `require_role` decides whether a role may
perform an action at all, and `require_sport_scope` confines a REP to their own
sport. The triggers cannot express sport scope — they only know a role string.

### Audit columns

The audit triggers fire `WHEN current_actor_id() IS NOT NULL`. Internal work
uses the reserved id `SYSTEM_ACTOR_ID` (`0`), so seed, migration and worker
writes are attributable instead of indistinguishable from rows predating the
audit columns. `0` is not a real `users` row, and the audit columns carry no
foreign key, so nothing needs to exist for it.

Two column names are in play. The audit "created" column is `created_at`,
except where a table already owns a business `created_at` with its own meaning
— `role_assignments` (when the nomination was made), `team_members` (when the
player joined), `matches` (when the fixture was proposed). Those carry
`created_at_audit` instead, because overwriting the business value would
destroy real information. `jobs` and `external_bookings` track only
`updated_at`; their own `created_at`/`synced_at` already say when the row
appeared.

### Fresh versus migrated databases

A database created fresh declares the timestamp columns `NOT NULL DEFAULT
CURRENT_TIMESTAMP`. A database migrated by `init_db()` cannot: SQLite's
`ALTER TABLE ... ADD COLUMN` rejects `NOT NULL DEFAULT CURRENT_TIMESTAMP`, so
those columns are added nullable with no default. The declared constraints
differ between the two.

Row *behaviour* does not: the audit insert triggers `COALESCE` the timestamps
in, so a row written to a migrated database gets the same values a fresh one
would. Rows that existed before the migration keep `created_by_user_id` NULL,
which is accurate — nobody recorded who wrote them.

### Cost

The triggers are not free. Every insert fires a follow-up `UPDATE` to backfill
the actor columns, and every statement calls back into Python for
`current_actor_role`/`current_actor_id`. Measured at roughly **4x a bare
insert** (~1.1 ms versus ~0.3 ms for 300 rows) across 59 triggers. That is
irrelevant at intramural scale, and worth revisiting only if `matches` grows
large. Note a SQLite trigger in the main schema cannot read a `temp` table, so
moving the actor out of a Python callback is not a small change.
