from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.routers import auth, jobs, players, schedules

app = FastAPI(title="Intramural Scheduling System")

# Dev-friendly CORS so a static frontend (opened as a file or served from
# any port) can call this API. Tighten to specific origins in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


app.include_router(auth.router)
app.include_router(players.router)
app.include_router(schedules.router)
app.include_router(jobs.router)


@app.get("/health")
def health():
    return {"status": "ok"}
