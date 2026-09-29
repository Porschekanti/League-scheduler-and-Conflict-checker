# MVP demo walkthrough

This walkthrough assumes the deterministic presentation database. It is safe to
repeat: the seed recreates `presentation.db` each time.

## 1. Prepare and start the demo

Run the commands for the terminal you are actually using. Do not mix
PowerShell syntax (`$env:`), Command Prompt syntax (`set "NAME=value"`), and
macOS/Linux syntax (`export NAME=value`). Most importantly, set the variables
in the same terminal session that starts Uvicorn; a child seed process cannot
change the environment of the terminal that launched it.

Run `presentation_seed.py`, not only `seed.py`. The smaller `seed.py` is for
backend development and intentionally has no confirmed fixtures.

### Windows PowerShell

Run from the repository root:

```powershell
Set-Location "C:\Studies\undergrad\year_3\tri_1\software\League-scheduler-and-Conflict-checker"

# Only if .\venv does not exist:
# py -m venv venv
# & .\venv\Scripts\python.exe -m pip install -r .\requirements.txt

& .\venv\Scripts\python.exe .\presentation_seed.py --database .\presentation.db

$env:SCHEDULER_DB_PATH = (Get-Item -LiteralPath .\presentation.db).FullName
$env:MASTER_SHEET_BACKEND = "csv"
$env:MASTER_SHEET_CSV = (Get-Item -LiteralPath .\fixtures\master_bookings.csv).FullName

& .\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Windows Command Prompt (`cmd.exe`)

Open `cmd.exe`, then run:

```cmd
cd /d C:\Studies\undergrad\year_3\tri_1\software\League-scheduler-and-Conflict-checker

rem Only if venv does not exist:
rem py -m venv venv
rem venv\Scripts\python.exe -m pip install -r requirements.txt

venv\Scripts\python.exe presentation_seed.py --database presentation.db

set "SCHEDULER_DB_PATH=%CD%\presentation.db"
set "MASTER_SHEET_BACKEND=csv"
set "MASTER_SHEET_CSV=%CD%\fixtures\master_bookings.csv"

venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The quotes in `set "NAME=value"` are intentional. Do not write
`set NAME = value`, and do not use `$env:` in Command Prompt.

### macOS

Run from the repository root. If the project does not already contain a
virtual environment, create it and install dependencies first.

```bash
cd /path/to/League-scheduler-and-Conflict-checker
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt

./venv/bin/python presentation_seed.py --database presentation.db

export SCHEDULER_DB_PATH="$PWD/presentation.db"
export MASTER_SHEET_BACKEND="csv"
export MASTER_SHEET_CSV="$PWD/fixtures/master_bookings.csv"

./venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Linux

Run from the repository root. If needed, create the environment and install
dependencies first:

```bash
cd /path/to/League-scheduler-and-Conflict-checker
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
```

Then start the demo:

```bash
./venv/bin/python presentation_seed.py --database presentation.db

export SCHEDULER_DB_PATH="$PWD/presentation.db"
export MASTER_SHEET_BACKEND="csv"
export MASTER_SHEET_CSV="$PWD/fixtures/master_bookings.csv"

./venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Start the frontend

Keep the API terminal running. Open a second terminal in the repository root
and serve the frontend with the matching command for your platform:

```powershell
& .\venv\Scripts\python.exe -m http.server 8080 --bind 127.0.0.1 --directory frontend
```

```cmd
venv\Scripts\python.exe -m http.server 8080 --bind 127.0.0.1 --directory frontend
```

```bash
./venv/bin/python -m http.server 8080 --bind 127.0.0.1 --directory frontend
```

Open [http://127.0.0.1:8080/index.html?api=http://127.0.0.1:8000](http://127.0.0.1:8080/index.html?api=http://127.0.0.1:8000).
The API documentation is at
[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). Keep both terminals
visible when demonstrating the async worker.

Opening `frontend/index.html` directly can also work, but serving it over HTTP
avoids browser file-origin restrictions and is the recommended presentation
path.

Demo accounts:

| Account | Password | Demonstrates |
|---|---|---|
| `head@example.edu` | `head-pass` | Global access, Admin, master-sheet sync |
| `bball-rep@example.edu` | `rep-pass` | Basketball-only organizer access |
| `cricket-rep@example.edu` | `rep-pass` | Cricket-only organizer access |
| `nominee@example.edu` | `nominee-pass` | Pending role acceptance |

## 2. Public schedule and reference data

1. Before signing in, point out the public schedule on the left. It already
   contains confirmed Basketball and Cricket fixtures on 8, 9, and 11 October
   2026. Use Previous/Next month if the calendar opens on another month.
2. Click each fixture to show teams, venue, time, sport, and status.
3. In `/docs`, call `GET /health`, `GET /schedules?season=active`,
   `GET /teams`, `GET /venues`, and `GET /seasons` without authentication.
   This demonstrates the public read path and reference data.

## 3. Login, identity, and sport scope

1. Sign in as `bball-rep@example.edu` / `rep-pass`. Confirm the Basketball
   scope appears in the header and the Book, Roster, and Generate tools appear.
2. Sign out and sign in as `cricket-rep@example.edu` / `rep-pass`; only Cricket
   teams should be offered.
3. Use the API docs' Authorize button with a Basketball token and try a Cricket
   write. For example, `POST /teams` with `{"name":"Scope test","sport":"Cricket"}`
   must return `403`. This is the role-plus-sport-scope check.
4. Call `GET /auth/me` with a valid token, then repeat it without a token to
   show the `401` authentication boundary.

## 4. Roster and team management

1. Sign in as the Basketball Rep and open Roster. Select Goon Squad and show
   its seeded players; switch teams to show the other rosters.
2. Add a new player, such as `Demo Player` / `DEMO-001`, to Goon Squad, then
   refresh the roster. Remove that player to demonstrate the delete path.
3. Add `Roster Conflict` / `DEMO-002` to Goon Squad, then try the same roll
   number on Rebels. The second request must be rejected with `409` because a
   player cannot join two teams in one sport and season.
4. Sign in as the Cricket Rep and add the same `DEMO-002` roll number to
   Strikers. That request succeeds because cross-sport membership is allowed.

## 5. Direct booking, validation, publishing, cancellation

1. In Book, choose two Basketball teams and a free date/venue after the seeded
   fixtures, then click **Check and create draft**. The draft appears in the
   Drafts panel.
2. Click **Validate**, then **Publish**. The fixture moves into the public
   schedule. The CSV master-sheet backend also receives a copy.
3. Create a draft, click **Cancel**, and confirm it disappears from the public
   schedule. A cancelled booking no longer blocks that venue/time.
4. Demonstrate venue conflict: attempt a Basketball booking at **Main Court**
   on `2026-10-15 18:00`–`19:00`. It is blocked by the external master-sheet
   booking `DEMO-EXT-VENUE` and the response explains the conflict.
5. Demonstrate cross-league player conflict: attempt a Cricket match between
   Strikers and Chargers at `2026-10-08 18:15`–`19:00` on another venue. Aarav
   Sharma is already committed in the seeded Basketball match, so the booking
   is rejected even across sports.
6. Demonstrate the external-player conflict: attempt a match containing
   Strikers at **Indoor Hall** on `2026-10-16 18:00`–`19:00`. Roll `202401` is
   committed by `DEMO-EXT-PLAYER`, so this is rejected as an external player
   commitment.

## 6. Async schedule generation

1. Open Generate as the Basketball Rep. Leave one generated fixture on a free
   slot and add another fixture overlapping the confirmed 8 October match.
2. Click **Run generation**. Watch the status move through `QUEUED`,
   `RUNNING`, and `COMPLETED`. The result reports created drafts and skipped
   fixtures with their conflict reasons.
3. Publish the generated clean draft from the Drafts panel.

## 7. Master-sheet integration

1. Sign in as the Head and open Admin. Click **Check status** in Master sheet;
   it should report the CSV backend and two mirrored rows.
2. Click **Sync now**. The report should show two synced rows and no rejected
   rows. The two rows remain in `external_bookings` and continue to block
   conflicting proposals.
3. Explain that publishing a local match pushes a row to the CSV, while sync
   mirrors sheet rows locally so conflict checks do not need a network call.

## 8. Terms and role succession

1. As Head, open Admin and show the current `2026-27` term, the older inactive
   term, and assignment history.
2. Create a term such as `2027-28` with dates `2027-07-01` to `2028-06-30`,
   then click **Make current**. This demonstrates term creation and activation.
   You can reactivate `2026-27` afterward for a clean-looking demo.
3. The seed already includes a pending Basketball REP nomination for
   `nominee@example.edu`. Sign out, sign in as the nominee, and accept the
   pending role card. Sign out and sign in again so the fresh token carries the
   new REP role. Confirm Basketball tools are now available.
4. Sign back in as Head, open Admin, and revoke that accepted assignment. The
   nominee becomes a VIEWER again and the assignment history records REVOKED.

## 9. API-only reliability demonstrations

The UI intentionally keeps version details out of the main workflow. Use the
API docs for these two protocol behaviors:

1. The seed prints the known draft id and version 1. Call
   `PATCH /schedules/{draft_id}` and confirm `{"valid":true,"conflicts":[]}`.
   First publish it with the deliberately stale body `{"expected_version":0}`
   and show the `409` version-mismatch response. Then publish it with
   `{"expected_version":1}`. Repeat the publish with version 1; it must still
   return `409` because the match is no longer a draft.
2. For any fresh draft, call publish twice using the same expected version, or
   cancel it with an old version. The stale operation returns `409` rather than
   overwriting another organizer's change. This is optimistic concurrency.

For a clean rerun after experimenting, stop both servers, run
`presentation_seed.py --database presentation.db` again using the command for
your platform above, re-apply the environment variables in the API terminal,
and restart both servers.

## Troubleshooting the blank/empty screen

- If the page shows literal `—` placeholders and no month title, refresh the
  latest `frontend/index.html`. It now loads `app.js` as a classic script so it
  works from `file://` as well as from a static server. A static server remains
  the most predictable option.
- If the page shows `0 confirmed`, verify the backend before changing frontend
  code. In PowerShell run:

  ```powershell
  (Invoke-WebRequest http://127.0.0.1:8000/health).Content
  (Invoke-WebRequest 'http://127.0.0.1:8000/schedules?season=active').Content
  ```

  In macOS/Linux run:

  ```bash
  curl http://127.0.0.1:8000/health
  curl 'http://127.0.0.1:8000/schedules?season=active'
  ```

  In Command Prompt run:

  ```cmd
  curl http://127.0.0.1:8000/health
  curl "http://127.0.0.1:8000/schedules?season=active"
  ```

  The schedule response must contain three `CONFIRMED` matches. If it returns
  `[]`, the backend is connected to an empty or different database. Stop
  Uvicorn, regenerate `presentation.db`, set `SCHEDULER_DB_PATH` again in the
  same terminal, and restart it.
- If another checkout already owns port 8000, use port 8001 for the API and
  port 8081 for the frontend. Set the database variables exactly as shown in
  the platform section above, but start Uvicorn with `--port 8001`. Start the
  static server with `--directory frontend` and `8081`, then open
  `http://127.0.0.1:8081/index.html?api=http://127.0.0.1:8001`. The `api`
  query parameter makes the frontend use your API instead of the other
  checkout's API.
- If Uvicorn reports that the address is already in use, stop the old
  backend terminal/process first. Changing the database environment variables
  does not reconfigure a server that is already running; the server must be
  restarted.
