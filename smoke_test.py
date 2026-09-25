from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def login(email, password):
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]

print("=== 1. Login as Basketball Rep ===")
bball_token = login("bball-rep@example.edu", "rep-pass")
print("OK, got token")

print("\n=== 1b. Create team, add/remove roster player ===")
r = client.post("/teams", headers={"Authorization": f"Bearer {bball_token}"},
                json={"name": "Smoke Team", "sport": "Basketball", "season_id": 1})
print(r.status_code, r.json())
assert r.status_code == 201
smoke_team_id = r.json()["team_id"]
r = client.post("/players", headers={"Authorization": f"Bearer {bball_token}"},
                json={"name": "Roster Smoke", "roll_number": "R-SMOKE", "team_id": smoke_team_id,
                      "sport": "Basketball", "season_id": 1})
assert r.status_code == 201
smoke_player_id = r.json()["player_id"]
r = client.get(f"/teams/{smoke_team_id}/roster")
assert r.status_code == 200 and any(p["id"] == smoke_player_id for p in r.json())
r = client.delete(f"/players/{smoke_player_id}/teams/{smoke_team_id}",
                  headers={"Authorization": f"Bearer {bball_token}"})
assert r.status_code == 200
assert not client.get(f"/teams/{smoke_team_id}/roster").json()

print("\n=== 2. Scope check: Basketball rep touches Cricket -> expect 403 ===")
r = client.post("/players", headers={"Authorization": f"Bearer {bball_token}"},
                 json={"name": "Test", "roll_number": "R100", "team_id": 3,
                       "sport": "Cricket", "season_id": 2})
print(r.status_code, r.json())
assert r.status_code == 403

print("\n=== 3. Add player to Basketball -> expect 201 ===")
r = client.post("/players", headers={"Authorization": f"Bearer {bball_token}"},
                 json={"name": "Pashi", "roll_number": "R101", "team_id": 1,
                       "sport": "Basketball", "season_id": 1})
print(r.status_code, r.json())
assert r.status_code == 201

print("\n=== 4. Same player, second Basketball team, same season -> expect 409 (duplication) ===")
r = client.post("/players", headers={"Authorization": f"Bearer {bball_token}"},
                 json={"name": "Pashi", "roll_number": "R101", "team_id": 2,
                       "sport": "Basketball", "season_id": 1})
print(r.status_code, r.json())
assert r.status_code == 409

print("\n=== 5. Same player joins Cricket (different sport) via Cricket rep -> expect 201 (allowed) ===")
cricket_token = login("cricket-rep@example.edu", "rep-pass")
r = client.post("/players", headers={"Authorization": f"Bearer {cricket_token}"},
                 json={"name": "Pashi", "roll_number": "R101", "team_id": 3,
                       "sport": "Cricket", "season_id": 2})
print(r.status_code, r.json())
assert r.status_code == 201

print("\n=== 6. Book a Basketball match, Main Court, no conflicts -> expect 201 DRAFT ===")
r = client.post("/schedules", headers={"Authorization": f"Bearer {bball_token}"},
                 json={"home_team_id": 1, "away_team_id": 2, "venue_id": 1,
                       "start_time": "2026-10-01T18:00:00", "end_time": "2026-10-01T19:00:00",
                       "sport": "Basketball", "season_id": 1})
print(r.status_code, r.json())
assert r.status_code == 201
match1 = r.json()

print("\n=== 7. Book a SECOND match, same venue, overlapping time -> expect 409 venue conflict ===")
r = client.post("/schedules", headers={"Authorization": f"Bearer {bball_token}"},
                 json={"home_team_id": 1, "away_team_id": 2, "venue_id": 1,
                       "start_time": "2026-10-01T18:30:00", "end_time": "2026-10-01T19:30:00",
                       "sport": "Basketball", "season_id": 1})
print(r.status_code, r.json())
assert r.status_code == 409
assert r.json()["detail"]["conflicts"][0]["type"] == "venue"

print("\n=== 8. Cricket rep tries to book Pashi (on Basketball roster, overlapping time), diff venue -> expect 409 PLAYER conflict ===")
r = client.post("/schedules", headers={"Authorization": f"Bearer {cricket_token}"},
                 json={"home_team_id": 3, "away_team_id": 3, "venue_id": 2,
                       "start_time": "2026-10-01T18:15:00", "end_time": "2026-10-01T20:00:00",
                       "sport": "Cricket", "season_id": 2})
print(r.status_code, r.json())
assert r.status_code == 409
assert any(c["type"] == "player" for c in r.json()["detail"]["conflicts"])
print(">>> THIS is the cross-league player conflict the whole project is about <<<")

print("\n=== 9. Publish match1 with correct version -> expect 200 CONFIRMED ===")
r = client.post(f"/schedules/{match1['match_id']}/publish",
                 headers={"Authorization": f"Bearer {bball_token}"},
                 json={"expected_version": match1["version"]})
print(r.status_code, r.json())
assert r.status_code == 200

print("\n=== 10. Publish AGAIN with the now-stale version -> expect 409 (optimistic concurrency) ===")
r = client.post(f"/schedules/{match1['match_id']}/publish",
                 headers={"Authorization": f"Bearer {bball_token}"},
                 json={"expected_version": match1["version"]})
print(r.status_code, r.json())
assert r.status_code == 409

print("\n=== 11. Viewer read path (no auth) -> confirmed match should appear ===")
r = client.get("/schedules?season=active")
print(r.status_code, r.json())
assert r.status_code == 200
assert any(m["id"] == match1["match_id"] for m in r.json())

print("\n=== 12. Async schedule generation job -> expect QUEUED, then poll to COMPLETED ===")
r = client.post("/schedule-generations", headers={"Authorization": f"Bearer {bball_token}"},
                 json={"sport": "Basketball", "season_id": 1, "fixtures": [
                     {"home_team_id": 1, "away_team_id": 2, "venue_id": 1,
                      "start_time": "2026-10-05T18:00:00", "end_time": "2026-10-05T19:00:00"},
                     {"home_team_id": 1, "away_team_id": 2, "venue_id": 1,
                      "start_time": "2026-10-01T18:00:00", "end_time": "2026-10-01T19:00:00"},
                 ]})
print(r.status_code, r.json())
job_id = r.json()["job_id"]

print("\n=== 13. Job status requires authentication ===")
r = client.get(f"/schedule-generations/{job_id}")
assert r.status_code == 401

import time
for _ in range(10):
    r = client.get(f"/schedule-generations/{job_id}", headers={"Authorization": f"Bearer {bball_token}"})
    status = r.json()["status"]
    print("  poll:", status)
    if status in ("COMPLETED", "FAILED"):
        break
    time.sleep(0.5)
print(r.json())
assert r.json()["status"] == "COMPLETED"
assert len(r.json()["result"]["created_draft_match_ids"]) == 1
assert len(r.json()["result"]["skipped"]) == 1

print("\n=== 14. Cancelled booking immediately frees venue/team slot ===")
r = client.post("/schedules", headers={"Authorization": f"Bearer {bball_token}"}, json={
    "home_team_id": 1, "away_team_id": 2, "venue_id": 1,
    "start_time": "2026-10-10T18:00:00", "end_time": "2026-10-10T19:00:00",
    "sport": "Basketball", "season_id": 1})
assert r.status_code == 201
cancel_match = r.json()
r = client.post(f"/schedules/{cancel_match['match_id']}/cancel",
                headers={"Authorization": f"Bearer {bball_token}"},
                json={"expected_version": cancel_match["version"]})
assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
r = client.post("/schedules", headers={"Authorization": f"Bearer {bball_token}"}, json={
    "home_team_id": 1, "away_team_id": 2, "venue_id": 1,
    "start_time": "2026-10-10T18:00:00", "end_time": "2026-10-10T19:00:00",
    "sport": "Basketball", "season_id": 1})
assert r.status_code == 201, r.text
confirmed_again = r.json()
r = client.post(f"/schedules/{confirmed_again['match_id']}/publish",
                headers={"Authorization": f"Bearer {bball_token}"},
                json={"expected_version": confirmed_again["version"]})
assert r.status_code == 200
r = client.post(f"/schedules/{confirmed_again['match_id']}/cancel",
                headers={"Authorization": f"Bearer {bball_token}"},
                json={"expected_version": r.json()["version"]})
assert r.status_code == 200 and r.json()["status"] == "CANCELLED"

print("\n\n=== ALL CHECKS PASSED ===")
