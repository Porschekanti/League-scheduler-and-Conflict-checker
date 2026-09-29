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

SQLite does not implement PostgreSQL-style native RLS.  This project installs
BEFORE INSERT/UPDATE/DELETE triggers on every scheduling table.  The triggers
allow only an actor set by the authenticated FastAPI dependency (`HEAD` or
`REP`) or the internal `SYSTEM` actor used by migrations and the worker.  A
plain sqlite3 connection has no actor and is denied by default.  API routes
still enforce sport scope, so a REP can only change rows for their sport.
