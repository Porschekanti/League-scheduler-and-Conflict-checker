from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.routers import auth, jobs, players, schedules, teams, role_assignments

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="Intramural Scheduling System", lifespan=lifespan)

# Dev-friendly CORS so a static frontend (opened as a file or served from
# any port) can call this API. Tighten to specific origins in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth.router)
app.include_router(players.router)
app.include_router(schedules.router)
app.include_router(jobs.router)
app.include_router(teams.router)
app.include_router(role_assignments.router)


@app.get("/health")
def health():
    return {"status": "ok"}


