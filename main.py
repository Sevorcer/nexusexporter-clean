"""FastAPI entry point.

This module is intentionally thin — it re-exports the FastAPI app, ORM models,
and engine from the ``app`` package so that ``uvicorn main:app`` keeps working
and existing tests/scripts that import ``main.User`` etc. don't break.
"""

from app.app import app
from app.auth import get_current_user
from app.companion import _transform_madden_schedule, _transform_madden_stats
from app.config import (
    COMPANION_DEBUG_LOG_ENABLED,
    COMPANION_DEBUG_PREVIEW_LIMIT,
    MAX_MADDEN_LEAGUE_ID_LENGTH,
    companion_logger,
)
from app.db import engine, get_session
from app.models import (
    League,
    Player,
    PlayerStats,
    Schedule,
    Standing,
    Team,
    User,
)
from app.schemas import (
    PlayerIn,
    PlayerStatsIn,
    ScheduleIn,
    StandingIn,
    TeamIn,
)

__all__ = [
    "app",
    "engine",
    "get_session",
    "get_current_user",
    "User",
    "League",
    "Team",
    "Player",
    "Schedule",
    "Standing",
    "PlayerStats",
    "TeamIn",
    "PlayerIn",
    "StandingIn",
    "ScheduleIn",
    "PlayerStatsIn",
    "MAX_MADDEN_LEAGUE_ID_LENGTH",
    "COMPANION_DEBUG_PREVIEW_LIMIT",
    "COMPANION_DEBUG_LOG_ENABLED",
    "companion_logger",
    "_transform_madden_schedule",
    "_transform_madden_stats",
]


if __name__ == "__main__":
    import os

    import uvicorn

    reload_flag = os.environ.get("UVICORN_RELOAD", "true").strip().lower() in {"1", "true", "yes"}
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=reload_flag)
