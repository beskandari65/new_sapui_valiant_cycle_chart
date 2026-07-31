"""Standalone FastAPI entry point for the combined Cycle Chart + PM app.

The existing Cycle Chart HTTP implementation is mounted through FastAPI's WSGI
compatibility layer. This preserves every endpoint already used by
``cycle_chart.html`` while allowing this project to run from one folder and one
port. The PM endpoints registered by ``server.py`` are included automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from a2wsgi import WSGIMiddleware

import server as cycle_chart_server
from pm_database import PMDatabase


PROJECT_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(
    os.environ.get("DB_PATH") or PROJECT_DIR / "vccm.db"
).resolve()

# The legacy-compatible Flask handlers read this module-level setting at
# request time. Pointing it at the local copy makes this folder self-contained.
cycle_chart_server.DB_PATH = str(DATABASE_PATH)

# Create only the separate pm_* tables. Existing Cycle Chart tables and rows
# remain untouched.
PMDatabase(str(DATABASE_PATH)).create_or_migrate()

app = FastAPI(
    title="ValiantTMS Cycle Chart and Project Management API",
    version="1.0.0",
    description=(
        "Combined API. Cycle Chart compatibility routes and PM routes use the "
        "same local SQLite file with separate domain tables."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["system"])
def health():
    return {
        "status": "ok",
        "database": str(DATABASE_PATH),
        "cycle_chart": True,
        "project_management": True,
    }


# Keep this mount last. It exposes the existing Cycle Chart endpoints, static
# app, reports, uploads, and the PM Flask blueprint at their original paths.
app.mount("/", WSGIMiddleware(cycle_chart_server.app))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "fast_api_server:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )
