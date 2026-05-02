import os
import json
import secrets
import logging
from typing import Optional, List, Any, Dict, Set, Tuple, Type, DefaultDict, Literal
from collections import defaultdict
from urllib.parse import urlencode, parse_qs

from fastapi import FastAPI, Request, Form, Depends, status, HTTPException, Body, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel, Field, Relationship, Session, select, create_engine, delete
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.postgresql import insert as pg_insert
from starlette.middleware.sessions import SessionMiddleware
import httpx

# ----------- Config -----------
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///database.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "very-secret-dev-key")
DISCORD_CLIENT_ID = os.environ["DISCORD_CLIENT_ID"]
DISCORD_CLIENT_SECRET = os.environ["DISCORD_CLIENT_SECRET"]
DISCORD_REDIRECT_URI = os.environ["DISCORD_REDIRECT_URI"]
MAX_MADDEN_LEAGUE_ID_LENGTH = 64
COMPANION_JSON_FORM_KEYS = ("payload", "data", "body", "json")
COMPANION_DEBUG_PREVIEW_LIMIT = 1000
COMPANION_DEBUG_LOG_ENABLED = os.environ.get("COMPANION_DEBUG_LOG", "").strip().lower() in {"1", "true"}
DEV_TRAIT_MAP = {0: "Normal", 1: "Star", 2: "Superstar", 3: "X-Factor"}

logging.basicConfig(level=logging.INFO)
companion_logger = logging.getLogger("companion_ingest")

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)

# ----------- FastAPI app & Templates -----------
app = FastAPI()
@app.get("/force_create_tables")
def force_create_tables():
    SQLModel.metadata.create_all(engine)
    return {"status": "tables created"}
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ----------- MODELS -----------

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    discord_id: str
    username: str
    avatar: Optional[str] = None
    leagues: List["League"] = Relationship(back_populates="user")

class League(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    api_key: str
    madden_league_id: Optional[str] = Field(default=None, index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    user: Optional[User] = Relationship(back_populates="leagues")

class Team(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("league_id", "id", name="uq_team_league_id"),
    )
    id: int = Field(primary_key=True)
    league_id: int = Field(primary_key=True, foreign_key="league.id")
    team_name: Optional[str] = None
    abbreviation: Optional[str] = None
    division: Optional[str] = None
    overall_rating: Optional[int] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    ties: Optional[int] = None
    city_name: Optional[str] = None

class Player(SQLModel, table=True):
    id: int = Field(primary_key=True)
    league_id: int = Field(primary_key=True, foreign_key="league.id")
    team_id: Optional[int] = Field(default=None, foreign_key="team.id")
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    position: Optional[str] = None
    overall_rating: Optional[int] = None
    age: Optional[int] = None
    jersey_number: Optional[int] = None
    dev_trait: Optional[str] = None
    contract_years: Optional[int] = None
    contract_salary: Optional[float] = None

class Schedule(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("league_id", "id", name="uq_schedule_league_id"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    league_id: int = Field(foreign_key="league.id")
    week_number: int
    season_number: int
    home_team_id: Optional[int] = Field(default=None, foreign_key="team.id")
    away_team_id: Optional[int] = Field(default=None, foreign_key="team.id")
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    is_complete: bool = False

class Standing(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("league_id", "id", name="uq_standing_league_id"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    league_id: int = Field(foreign_key="league.id")
    team_id: Optional[int] = Field(default=None, foreign_key="team.id")
    wins: Optional[int] = None
    losses: Optional[int] = None
    ties: Optional[int] = None
    division_name: Optional[str] = None
    seed: Optional[int] = None

class PlayerStats(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("league_id", "player_id", "week_number", "season_number", name="uq_playerstats_league_player_week_season"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    league_id: int = Field(foreign_key="league.id")
    player_id: Optional[int] = Field(default=None)
    week_number: int
    season_number: int
    season_type: Optional[str] = None
    pass_yards: Optional[int] = None
    pass_tds: Optional[int] = None
    interceptions: Optional[int] = None
    rush_yards: Optional[int] = None
    rush_tds: Optional[int] = None
    rec_yards: Optional[int] = None
    rec_tds: Optional[int] = None
    receptions: Optional[int] = None
    tackles: Optional[int] = None
    sacks: Optional[int] = None
    fumbles_forced: Optional[int] = None
    defensive_ints: Optional[int] = None

class TeamIn(SQLModel):
    id: Optional[int] = None
    team_name: Optional[str] = None
    abbreviation: Optional[str] = None
    division: Optional[str] = None
    overall_rating: Optional[int] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    ties: Optional[int] = None
    city_name: Optional[str] = None

class PlayerIn(SQLModel):
    id: Optional[int] = None
    team_id: Optional[int] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    position: Optional[str] = None
    overall_rating: Optional[int] = None
    age: Optional[int] = None
    jersey_number: Optional[int] = None
    dev_trait: Optional[str] = None
    contract_years: Optional[int] = None
    contract_salary: Optional[float] = None

class StandingIn(SQLModel):
    id: Optional[int] = None
    team_id: Optional[int] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    ties: Optional[int] = None
    division_name: Optional[str] = None
    seed: Optional[int] = None

class ScheduleIn(SQLModel):
    id: Optional[int] = None
    week_number: int
    season_number: int
    home_team_id: Optional[int] = None
    away_team_id: Optional[int] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    is_complete: bool = False

class PlayerStatsIn(SQLModel):
    id: Optional[int] = None
    player_id: Optional[int] = None
    week_number: int
    season_number: int
    season_type: Optional[str] = None
    pass_yards: Optional[int] = None
    pass_tds: Optional[int] = None
    interceptions: Optional[int] = None
    rush_yards: Optional[int] = None
    rush_tds: Optional[int] = None
    rec_yards: Optional[int] = None
    rec_tds: Optional[int] = None
    receptions: Optional[int] = None
    tackles: Optional[int] = None
    sacks: Optional[int] = None
    fumbles_forced: Optional[int] = None
    defensive_ints: Optional[int] = None

def create_db():
    SQLModel.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE playerstats DROP CONSTRAINT IF EXISTS playerstats_player_id_fkey"
            )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_playerstats_league_player_week_season "
                "ON playerstats (league_id, player_id, week_number, season_number)"
            )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_team_league_id ON team (league_id, id)"
            )
            # Migrate team table to composite primary key (id, league_id) — same pattern
            # as the player table migration below, so that ON CONFLICT (league_id, id)
            # in _upsert can target the PK constraint instead of falling through to
            # team_pkey on (id) alone.
            #
            # Step 1: Drop FK constraints that reference team_pkey before dropping it.
            # PostgreSQL won't allow dropping the PK while FK constraints depend on it.
            connection.exec_driver_sql(
                "ALTER TABLE player DROP CONSTRAINT IF EXISTS player_team_id_fkey"
            )
            connection.exec_driver_sql(
                "ALTER TABLE schedule DROP CONSTRAINT IF EXISTS schedule_home_team_id_fkey"
            )
            connection.exec_driver_sql(
                "ALTER TABLE schedule DROP CONSTRAINT IF EXISTS schedule_away_team_id_fkey"
            )
            connection.exec_driver_sql(
                "ALTER TABLE standing DROP CONSTRAINT IF EXISTS standing_team_id_fkey"
            )
            # Step 2: Drop and re-add team PK as composite.
            connection.exec_driver_sql(
                "ALTER TABLE team DROP CONSTRAINT IF EXISTS team_pkey"
            )
            connection.exec_driver_sql(
                "ALTER TABLE team ADD PRIMARY KEY (id, league_id)"
            )
            # Migrate player table to composite primary key (id, league_id) so that
            # ON CONFLICT (league_id, id) in _upsert can target the PK and the same
            # player id is allowed to exist in multiple leagues.
            # This migration is safe on existing data: the previous PK on (id) alone
            # already guarantees unique id values, so (id, league_id) combinations are
            # trivially unique and the ADD PRIMARY KEY will never fail.
            connection.exec_driver_sql(
                "ALTER TABLE player DROP CONSTRAINT IF EXISTS player_pkey"
            )
            connection.exec_driver_sql(
                "ALTER TABLE player DROP CONSTRAINT IF EXISTS uq_player_league_id"
            )
            connection.exec_driver_sql(
                "DROP INDEX IF EXISTS uq_player_league_id"
            )
            connection.exec_driver_sql(
                "ALTER TABLE player ADD PRIMARY KEY (id, league_id)"
            )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_league_id ON schedule (league_id, id)"
            )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_standing_league_id ON standing (league_id, id)"
            )
            # Add season_type column to playerstats for pre/reg/post season filtering.
            # Existing rows will remain NULL; new rows will be populated from the URL path.
            connection.exec_driver_sql(
                "ALTER TABLE playerstats ADD COLUMN IF NOT EXISTS season_type VARCHAR"
            )
create_db()

# ----------- DEPENDENCIES -----------

def get_session():
    with Session(engine) as session:
        yield session

def get_current_user(request: Request, session: Session = Depends(get_session)) -> Optional[User]:
    discord_id = request.session.get("discord_id")
    if not discord_id:
        return None
    user = session.exec(select(User).where(User.discord_id == discord_id)).first()
    return user

def validate_api_key(league_id: int, key: str, session: Session) -> League:
    league = session.get(League, league_id)
    if league is None:
        raise HTTPException(status_code=404, detail="League not found")
    if key != league.api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return league

def clear_league_records(session: Session, model: Type[SQLModel], league_id: int) -> int:
    """Bulk delete rows for models that include a `league_id` column."""
    result = session.exec(delete(model).where(model.league_id == league_id))
    return result.rowcount or 0

def clear_teams_and_dependencies(session: Session, league_id: int) -> int:
    """Delete team-dependent rows in FK-safe order and return only Team rows deleted."""
    cleared_team_records = 0
    models_in_fk_safe_order: List[Type[SQLModel]] = [PlayerStats, Standing, Schedule, Player, Team]
    for model in models_in_fk_safe_order:
        cleared = clear_league_records(session, model, league_id)
        if model is Team:
            cleared_team_records = cleared
    return cleared_team_records


def _upsert(session: Session, model: Type[SQLModel], payload: Dict[str, Any]) -> None:
    """Upsert a row using a composite ``(league_id, id)`` conflict target.

    Uses a PostgreSQL ``INSERT ... ON CONFLICT (league_id, id) DO UPDATE`` so
    re-exports of the same league never produce a ``UniqueViolation``, and
    rows that belong to different leagues are never overwritten.

    Falls back to a manual select-then-update strategy for SQLite (tests).
    """
    row_id = payload.get("id")
    if row_id is None:
        session.add(model(**payload))
        return
    if engine.dialect.name == "postgresql":
        stmt = pg_insert(model).values(**payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["league_id", "id"],
            set_={k: stmt.excluded[k] for k in payload if k not in ("id", "league_id")},
        )
        session.execute(stmt)
    else:
        league_id = payload.get("league_id")
        existing = session.exec(
            select(model).where(model.id == row_id, model.league_id == league_id)
        ).first()
        if existing is None:
            session.add(model(**payload))
        else:
            for field, value in payload.items():
                if field != "id":
                    setattr(existing, field, value)
            session.add(existing)


def get_league_or_404(league_id: int, session: Session) -> League:
    league = session.get(League, league_id)
    if league is None:
        raise HTTPException(status_code=404, detail="League not found")
    return league


def get_league_by_madden_id_or_404(madden_league_id: str, session: Session) -> League:
    league = session.exec(select(League).where(League.madden_league_id == madden_league_id)).first()
    if league is not None:
        return league
    if madden_league_id.isdigit():
        league = session.get(League, int(madden_league_id))
        if league is not None:
            return league
    raise HTTPException(status_code=404, detail="League not found")


def _pick(row: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _madden_week_number(row: Dict[str, Any]) -> int:
    week_index = _to_int(row.get("weekIndex"))
    if week_index is not None:
        return week_index + 1
    return _to_int(_pick(row, "week_number")) or 0


def _extract_companion_rows(payload: Any) -> Tuple[Optional[Literal["standings", "roster", "schedule", "passing", "rushing", "defense", "receiving", "teams", "untracked"]], List[Dict[str, Any]]]:
    if isinstance(payload, dict):
        mapping: List[Tuple[str, Literal["standings", "roster", "schedule", "passing", "rushing", "defense", "receiving", "teams"]]] = [
            ("teamStandingInfoList", "standings"),
            ("rosterInfoList", "roster"),
            ("gameScheduleInfoList", "schedule"),
            ("playerPassingStatInfoList", "passing"),
            ("playerRushingStatInfoList", "rushing"),
            ("playerDefensiveStatInfoList", "defense"),
            ("playerReceivingStatInfoList", "receiving"),
            # Companion payloads have been seen with both singular and plural variants.
            ("leagueTeamInfoList", "teams"),
            ("leagueTeamsInfoList", "teams"),
        ]
        content = payload.get("content")
        payload_sources: List[Dict[str, Any]] = []
        if isinstance(content, dict):
            payload_sources.append(content)
        payload_sources.append(payload)

        for source in payload_sources:
            for key, payload_type in mapping:
                rows = source.get(key)
                if isinstance(rows, list):
                    return payload_type, [row for row in rows if isinstance(row, dict)]
        for source in payload_sources:
            for key, rows in source.items():
                if key.endswith("InfoList") and isinstance(rows, list):
                    return "untracked", [row for row in rows if isinstance(row, dict)]
        raise HTTPException(status_code=422, detail="Invalid companion payload format")

    if isinstance(payload, list):
        return None, [row for row in payload if isinstance(row, dict)]

    raise HTTPException(status_code=422, detail="Invalid companion payload format")


def _transform_madden_standings(rows: List[Dict[str, Any]]) -> Tuple[List[StandingIn], List[TeamIn]]:
    standings: List[StandingIn] = []
    teams: List[TeamIn] = []
    for row in rows:
        team_id = _to_int(_pick(row, "teamId", "team_id"))
        standings.append(
            StandingIn(
                team_id=team_id,
                wins=_to_int(_pick(row, "totalWins", "wins")),
                losses=_to_int(_pick(row, "totalLosses", "losses")),
                ties=_to_int(_pick(row, "totalTies", "ties")),
                division_name=_pick(row, "divisionName", "division_name"),
                seed=_to_int(_pick(row, "seed")),
            )
        )
        if team_id is not None:
            teams.append(
                TeamIn(
                    id=team_id,
                    team_name=_pick(row, "teamName", "team_name"),
                    division=_pick(row, "divisionName", "division_name"),
                    overall_rating=_to_int(_pick(row, "teamOvr", "overall_rating")),
                    wins=_to_int(_pick(row, "totalWins", "wins")),
                    losses=_to_int(_pick(row, "totalLosses", "losses")),
                    ties=_to_int(_pick(row, "totalTies", "ties")),
                )
            )
    return standings, teams


def _transform_madden_roster(rows: List[Dict[str, Any]]) -> List[PlayerIn]:
    players: List[PlayerIn] = []
    for row in rows:
        signature_slots = _pick(row, "signatureSlotList")
        dev_trait = _pick(row, "devTraitLabel", "dev_trait")
        if dev_trait is not None and not isinstance(dev_trait, str):
            dev_trait = str(dev_trait)
        if dev_trait is None:
            raw_dev_trait = _pick(row, "devTrait")
            raw_dev_trait_int = _to_int(raw_dev_trait)
            if raw_dev_trait_int is not None:
                dev_trait = DEV_TRAIT_MAP.get(raw_dev_trait_int, str(raw_dev_trait_int))
            elif raw_dev_trait is not None:
                dev_trait = str(raw_dev_trait)
        if dev_trait is None and isinstance(signature_slots, list):
            dev_trait = json.dumps(signature_slots)
        players.append(
            PlayerIn(
                id=_to_int(_pick(row, "rosterId", "id")),
                team_id=_to_int(_pick(row, "teamId", "team_id")),
                first_name=_pick(row, "firstName", "first_name"),
                last_name=_pick(row, "lastName", "last_name"),
                position=_pick(row, "position"),
                overall_rating=_to_int(_pick(row, "playerSchemeOvr", "overallRating", "playerBestOvr", "overall_rating")),
                age=_to_int(_pick(row, "age")),
                jersey_number=_to_int(_pick(row, "jerseyNum", "jersey_number")),
                dev_trait=dev_trait,
                contract_years=_to_int(_pick(row, "contractYearsLeft", "contract_years")),
                contract_salary=_to_float(_pick(row, "contractSalary", "contract_salary")),
            )
        )
    return players


def _transform_madden_schedule(rows: List[Dict[str, Any]]) -> List[ScheduleIn]:
    schedules: List[ScheduleIn] = []
    for row in rows:
        status = _pick(row, "status")
        status_text = str(status).lower() if status is not None else ""
        is_complete = status_text in {"final", "played", "complete", "completed"} or _to_int(status) == 2
        schedules.append(
            ScheduleIn(
                id=_to_int(_pick(row, "scheduleId", "id")),
                week_number=_madden_week_number(row),
                season_number=_to_int(_pick(row, "seasonIndex", "season_number")) or 0,
                home_team_id=_to_int(_pick(row, "homeTeamId", "home_team_id")),
                away_team_id=_to_int(_pick(row, "awayTeamId", "away_team_id")),
                home_score=_to_int(_pick(row, "homeScore", "home_score")),
                away_score=_to_int(_pick(row, "awayScore", "away_score")),
                is_complete=is_complete or bool(_pick(row, "is_complete")),
            )
        )
    return schedules


def _transform_madden_teams(rows: List[Dict[str, Any]]) -> List[TeamIn]:
    teams: List[TeamIn] = []
    for row in rows:
        teams.append(
            TeamIn(
                id=_to_int(_pick(row, "teamId", "team_id", "id")),
                team_name=_pick(row, "teamName", "team_name"),
                abbreviation=_pick(row, "teamAbbreviation", "abbreviation"),
                division=_pick(row, "divisionName", "division"),
                overall_rating=_to_int(_pick(row, "teamOvr", "overall_rating")),
                wins=_to_int(_pick(row, "totalWins", "wins")),
                losses=_to_int(_pick(row, "totalLosses", "losses")),
                ties=_to_int(_pick(row, "totalTies", "ties")),
                city_name=_pick(row, "cityName", "city_name"),
            )
        )
    return teams


def _transform_madden_stats(rows: List[Dict[str, Any]], payload_type: Literal["passing", "rushing", "defense"], week_type: Optional[str] = None) -> List[PlayerStatsIn]:
    stats: List[PlayerStatsIn] = []
    for row in rows:
        stat = PlayerStatsIn(
            player_id=_to_int(_pick(row, "rosterId", "player_id")),
            week_number=_madden_week_number(row),
            season_number=_to_int(_pick(row, "seasonIndex", "season_number")) or 0,
            season_type=week_type,
        )
        if payload_type == "passing":
            stat.pass_yards = _to_int(_pick(row, "passYds", "pass_yards"))
            stat.pass_tds = _to_int(_pick(row, "passTDs", "pass_tds"))
            stat.interceptions = _to_int(_pick(row, "passInts", "interceptions"))
        elif payload_type == "rushing":
            stat.rush_yards = _to_int(_pick(row, "rushYds", "rush_yards"))
            stat.rush_tds = _to_int(_pick(row, "rushTDs", "rush_tds"))
        else:
            stat.sacks = _to_int(_pick(row, "defSacks", "sacks"))
            stat.defensive_ints = _to_int(_pick(row, "defInts", "defensive_ints"))
            stat.tackles = _to_int(_pick(row, "defTotalTackles", "tackles"))
        stats.append(stat)
    return stats


def _transform_madden_receiving_stats(rows: List[Dict[str, Any]], week_type: Optional[str] = None) -> List[PlayerStatsIn]:
    stats: List[PlayerStatsIn] = []
    for row in rows:
        week_number = _madden_week_number(row)
        season_number = _to_int(_pick(row, "seasonIndex", "season_index")) or 0
        stats.append(
            PlayerStatsIn(
                player_id=_to_int(_pick(row, "rosterId", "roster_id")),
                week_number=week_number,
                season_number=season_number,
                season_type=week_type,
                rec_yards=_to_int(_pick(row, "recYds", "rec_yds", "rec_yards")),
                rec_tds=_to_int(_pick(row, "recTDs", "rec_tds")),
                receptions=_to_int(_pick(row, "recCatches", "receptions", "rec")),
            )
        )
    return stats


def _upsert_teams_from_standings(league_id: int, teams: List[TeamIn], session: Session) -> int:
    for team_data in teams:
        if team_data.id is None:
            continue
        payload = team_data.model_dump(exclude_unset=True)
        payload["league_id"] = league_id
        _upsert(session, Team, payload)
    session.commit()
    return len(teams)


def ingest_companion_stats(league_id: int, stats: List[PlayerStatsIn], session: Session) -> Dict[str, Any]:
    inserted = 0
    updated = 0
    for stat_data in stats:
        payload = stat_data.model_dump(exclude_unset=True)
        player_id = payload.get("player_id")
        week_number = payload.get("week_number")
        season_number = payload.get("season_number")
        if week_number is None or season_number is None:
            continue

        if engine.dialect.name == "postgresql" and player_id is not None:
            stmt = pg_insert(PlayerStats).values(league_id=league_id, **payload)
            update_fields = {k: stmt.excluded[k] for k in payload if k not in ("id", "player_id", "week_number", "season_number", "league_id")}
            stmt = stmt.on_conflict_do_update(
                constraint="uq_playerstats_league_player_week_season",
                set_=update_fields,
                where=(PlayerStats.__table__.c.league_id == stmt.excluded.league_id),
            )
            session.execute(stmt)
            inserted += 1
            continue

        existing = None
        if player_id is not None:
            existing = session.exec(
                select(PlayerStats).where(
                    PlayerStats.league_id == league_id,
                    PlayerStats.player_id == player_id,
                    PlayerStats.week_number == week_number,
                    PlayerStats.season_number == season_number,
                )
            ).first()

        if existing is None:
            session.add(PlayerStats(league_id=league_id, **payload))
            inserted += 1
            continue

        for field, value in payload.items():
            if field in {"id", "player_id", "week_number", "season_number"}:
                continue
            if value is not None:
                setattr(existing, field, value)
        session.add(existing)
        updated += 1

    session.commit()
    return {"success": True, "cleared": 0, "inserted": inserted, "updated": updated}


def ingest_companion_payload(
    platform: str,
    madden_league_id: str,
    companion_path: str,
    payload: Any,
    session: Session,
):
    supported_platforms = {"xbsx", "xone", "ps5", "ps4", "pc"}
    if platform not in supported_platforms:
        raise HTTPException(status_code=404, detail="Companion platform not supported")

    league = get_league_by_madden_id_or_404(madden_league_id, session)
    normalized_path = companion_path.strip("/")
    is_free_agents_roster_path = normalized_path == "freeagents/roster"
    is_team_roster_path = normalized_path.startswith("team/") and normalized_path.endswith("/roster")
    team_roster_id_scope: Optional[int] = None
    if is_team_roster_path:
        path_parts = normalized_path.split("/")
        if len(path_parts) == 3:
            team_roster_id_scope = _to_int(path_parts[1])
        if team_roster_id_scope is None:
            raise HTTPException(status_code=422, detail="Invalid team roster path")

    payload_type, rows = _extract_companion_rows(payload)
    if payload_type == "untracked":
        return {"status": "ok", "tracked": False, "message": "Stat type not currently tracked"}

    # Extract week_type ("pre", "reg", "post") from companion paths like
    # "week/{week_type}/{week_num}/{stat_type}".
    path_segments = normalized_path.split("/")
    week_type: Optional[str] = (
        path_segments[1]
        if len(path_segments) == 4 and path_segments[0] == "week"
        else None
    )

    if payload_type == "standings":
        standings, teams_from_standings = _transform_madden_standings(rows)
        _upsert_teams_from_standings(league.id, teams_from_standings, session)
        return ingest_standings(league.id, league.api_key, standings, session)
    if payload_type == "roster":
        players = _transform_madden_roster(rows)
        return ingest_rosters(
            league.id,
            league.api_key,
            players,
            session,
            team_id_scope=team_roster_id_scope,
            free_agents_only=is_free_agents_roster_path,
        )
    if payload_type == "schedule":
        schedules = _transform_madden_schedule(rows)
        return ingest_schedules(league.id, league.api_key, schedules, session)
    if payload_type in {"passing", "rushing", "defense", "receiving"}:
        stats = (
            _transform_madden_receiving_stats(rows, week_type=week_type)
            if payload_type == "receiving"
            else _transform_madden_stats(rows, payload_type, week_type=week_type)
        )
        return ingest_companion_stats(league.id, stats, session)
    should_transform_teams = payload_type == "teams" or normalized_path == "leagueteams"
    should_ingest_teams = should_transform_teams or normalized_path == "teams"
    if should_ingest_teams:
        teams = _transform_madden_teams(rows) if should_transform_teams else [TeamIn.model_validate(row) for row in rows]
        return ingest_teams(league.id, league.api_key, teams, session)
    if normalized_path == "standings":
        standings = [StandingIn.model_validate(row) for row in rows]
        return ingest_standings(league.id, league.api_key, standings, session)
    if normalized_path in {"schedules", "schedule"}:
        schedules = [ScheduleIn.model_validate(row) for row in rows]
        return ingest_schedules(league.id, league.api_key, schedules, session)
    if is_free_agents_roster_path or is_team_roster_path:
        players = [PlayerIn.model_validate(row) for row in rows]
        return ingest_rosters(
            league.id,
            league.api_key,
            players,
            session,
            team_id_scope=team_roster_id_scope,
            free_agents_only=is_free_agents_roster_path,
        )

    if week_type is not None:
        if path_segments[3] in {"team", "kicking", "punting"}:
            return {
                "success": True,
                "tracked": False,
                "message": f"Companion stat type '{path_segments[3]}' received but not tracked",
            }
        stats = [PlayerStatsIn.model_validate(row) for row in rows]
        for stat in stats:
            if stat.season_type is None:
                stat.season_type = week_type
        return ingest_companion_stats(league.id, stats, session)

    raise HTTPException(status_code=404, detail="Companion endpoint not supported")


def build_stat_leaders(
    session: Session,
    league_id: int,
    season_number: Optional[int] = None,
    limit: int = 10,
) -> Dict[str, List[Dict[str, Any]]]:
    stats_query = select(PlayerStats).where(PlayerStats.league_id == league_id)
    if season_number is not None:
        stats_query = stats_query.where(PlayerStats.season_number == season_number)
    stats_rows = session.exec(stats_query).all()

    aggregates: DefaultDict[int, Dict[str, int]] = defaultdict(
        lambda: {
            "pass_yards": 0,
            "rush_yards": 0,
            "rec_yards": 0,
            "total_tds": 0,
            "sacks": 0,
            "ints": 0,
        }
    )
    for row in stats_rows:
        if row.player_id is None:
            continue
        values = aggregates[row.player_id]
        values["pass_yards"] += row.pass_yards or 0
        values["rush_yards"] += row.rush_yards or 0
        values["rec_yards"] += row.rec_yards or 0
        values["total_tds"] += (row.pass_tds or 0) + (row.rush_tds or 0) + (row.rec_tds or 0)
        values["sacks"] += row.sacks or 0
        values["ints"] += row.defensive_ints or 0

    players = session.exec(select(Player).where(Player.league_id == league_id)).all()
    teams = session.exec(select(Team).where(Team.league_id == league_id)).all()
    player_map = {p.id: p for p in players if p.id is not None}
    team_map = {t.id: t for t in teams if t.id is not None}

    def leader_list(metric: str) -> List[Dict[str, Any]]:
        sorted_items = sorted(aggregates.items(), key=lambda item: item[1][metric], reverse=True)[:limit]
        results: List[Dict[str, Any]] = []
        for player_id, values in sorted_items:
            player = player_map.get(player_id)
            if player is None:
                continue
            team = team_map.get(player.team_id)
            results.append(
                {
                    "player_id": player_id,
                    "player_name": f"{player.first_name or ''} {player.last_name or ''}".strip(),
                    "position": player.position,
                    "team_id": player.team_id,
                    "team_name": team.team_name if team else None,
                    "value": values[metric],
                }
            )
        return results

    return {
        "pass_yards": leader_list("pass_yards"),
        "rush_yards": leader_list("rush_yards"),
        "rec_yards": leader_list("rec_yards"),
        "total_tds": leader_list("total_tds"),
        "sacks": leader_list("sacks"),
        "ints": leader_list("ints"),
    }

# ----------- ROUTES -----------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    user = get_current_user(request, session)
    leagues = []
    error = request.session.pop("flash_error", None)
    flash_msg = request.session.pop("flash_msg", None)
    if not user:
        return RedirectResponse("/login", status_code=303)
    leagues = session.exec(select(League).where(League.user_id == user.id)).all()
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user, "leagues": leagues, "error": error, "flash_msg": flash_msg
    })

@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    discord_auth_url = "https://discord.com/api/oauth2/authorize?" + urlencode({
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify"
    })
    error = request.session.pop("flash_error", None)
    return templates.TemplateResponse("login.html", {"request": request, "discord_auth_url": discord_auth_url, "error": error})

@app.get("/oauth-callback")
async def discord_callback(request: Request, code: str = None, session: Session = Depends(get_session)):
    if code is None:
        request.session["flash_error"] = "No code from Discord; please try again."
        return RedirectResponse("/login", status_code=303)

    # Exchange code for token
    async with httpx.AsyncClient() as client:
        data = {
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": DISCORD_REDIRECT_URI,
            "scope": "identify"
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        token_res = await client.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
        if token_res.status_code != 200:
            request.session["flash_error"] = "Discord OAuth failed."
            return RedirectResponse("/login", status_code=303)
        token_json = token_res.json()
        access_token = token_json["access_token"]

        # Get user info
        user_res = await client.get("https://discord.com/api/users/@me", headers={
            "Authorization": f"Bearer {access_token}"
        })
        if user_res.status_code != 200:
            request.session["flash_error"] = "Failed to get user info from Discord."
            return RedirectResponse("/login", status_code=303)
        discord_info = user_res.json()
        discord_id = discord_info["id"]
        username = discord_info["username"]
        avatar = discord_info.get("avatar")

        user = session.exec(select(User).where(User.discord_id == discord_id)).first()
        if user is None:
            user = User(discord_id=discord_id, username=username, avatar=avatar)
            session.add(user)
            session.commit()
            session.refresh(user)
        else:
            # Update avatar/username (in case it changed)
            user.avatar = avatar
            user.username = username
            session.add(user)
            session.commit()

        request.session["discord_id"] = discord_id

    return RedirectResponse("/", status_code=303)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

@app.post("/create_league")
def create_league(
    request: Request,
    league_name: str = Form(...),
    session: Session = Depends(get_session)
):
    user = get_current_user(request, session)
    if not user:
        request.session["flash_error"] = "Please log in to create a league."
        return RedirectResponse("/login", status_code=303)
    api_key = secrets.token_hex(16)
    league = League(name=league_name, api_key=api_key, user_id=user.id)
    session.add(league)
    session.commit()
    request.session["flash_msg"] = f"League '{league_name}' created!"
    return RedirectResponse("/", status_code=303)


@app.post("/set_madden_id")
def set_madden_id(
    request: Request,
    league_id: int = Form(...),
    madden_league_id: Optional[str] = Form(default=""),
    session: Session = Depends(get_session),
):
    user = get_current_user(request, session)
    if not user:
        request.session["flash_error"] = "Please log in to update Madden league IDs."
        return RedirectResponse("/login", status_code=303)

    league = session.get(League, league_id)
    if league is None:
        request.session["flash_error"] = "League not found."
        return RedirectResponse("/", status_code=303)
    if league.user_id != user.id:
        request.session["flash_error"] = "You can only update your own leagues."
        return RedirectResponse("/", status_code=303)

    cleaned_madden_id = (madden_league_id or "").strip()
    if len(cleaned_madden_id) > MAX_MADDEN_LEAGUE_ID_LENGTH:
        request.session["flash_error"] = f"Madden league ID must be {MAX_MADDEN_LEAGUE_ID_LENGTH} characters or less."
        return RedirectResponse("/", status_code=303)

    league.madden_league_id = cleaned_madden_id or None
    session.add(league)
    session.commit()
    request.session["flash_msg"] = f"Madden league ID saved for '{league.name}'."
    return RedirectResponse("/", status_code=303)

@app.post("/api/{league_id}/teams")
def ingest_teams(
    league_id: int,
    key: str = Query(...),
    teams: List[TeamIn] = Body(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    for team_data in teams:
        payload = team_data.model_dump(exclude_unset=True)
        payload["league_id"] = league_id
        _upsert(session, Team, payload)
    session.commit()
    return {"success": True, "cleared": 0, "inserted": len(teams)}

@app.post("/api/{league_id}/rosters")
def ingest_rosters(
    league_id: int,
    key: str = Query(...),
    players: List[PlayerIn] = Body(...),
    session: Session = Depends(get_session),
    team_id_scope: Optional[int] = None,
    free_agents_only: bool = False,
):
    validate_api_key(league_id, key, session)
    for player_data in players:
        payload = player_data.model_dump(exclude_unset=True)
        if free_agents_only:
            payload["team_id"] = None
        elif team_id_scope is not None:
            payload["team_id"] = team_id_scope
        payload["league_id"] = league_id
        _upsert(session, Player, payload)
    session.commit()
    return {"success": True, "cleared": 0, "inserted": len(players)}

@app.post("/api/{league_id}/standings")
def ingest_standings(
    league_id: int,
    key: str = Query(...),
    standings: List[StandingIn] = Body(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    for standing_data in standings:
        payload = standing_data.model_dump(exclude_unset=True)
        payload["league_id"] = league_id
        _upsert(session, Standing, payload)
    session.commit()
    return {"success": True, "cleared": 0, "inserted": len(standings)}

@app.post("/api/{league_id}/schedules")
def ingest_schedules(
    league_id: int,
    key: str = Query(...),
    schedules: List[ScheduleIn] = Body(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    for schedule_data in schedules:
        payload = schedule_data.model_dump(exclude_unset=True)
        payload["league_id"] = league_id
        _upsert(session, Schedule, payload)
    session.commit()
    return {"success": True, "cleared": 0, "inserted": len(schedules)}

@app.post("/api/{league_id}/stats")
def ingest_stats(
    league_id: int,
    key: str = Query(...),
    stats: List[PlayerStatsIn] = Body(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    week_season_pairs: Set[Tuple[int, int]] = set()
    for stat_data in stats:
        week_season_pairs.add((stat_data.week_number, stat_data.season_number))

    cleared = 0
    for week_number, season_number in week_season_pairs:
        result = session.exec(
            delete(PlayerStats).where(
                PlayerStats.league_id == league_id,
                PlayerStats.week_number == week_number,
                PlayerStats.season_number == season_number,
            )
        )
        cleared += result.rowcount or 0

    for stat_data in stats:
        payload = stat_data.model_dump(exclude_unset=True)
        session.add(PlayerStats(league_id=league_id, **payload))
    session.commit()
    return {"success": True, "cleared": cleared, "inserted": len(stats)}


@app.post("//{platform}/{madden_league_id}/{companion_path:path}")
@app.post("/{platform}/{madden_league_id}/{companion_path:path}")
async def ingest_madden_companion(
    request: Request,
    platform: str,
    madden_league_id: str,
    companion_path: str,
    session: Session = Depends(get_session),
):
    def _body_preview(raw: bytes) -> str:
        decoded = raw.decode("utf-8", errors="replace")
        if len(decoded) <= COMPANION_DEBUG_PREVIEW_LIMIT:
            return decoded
        return (
            f"{decoded[:COMPANION_DEBUG_PREVIEW_LIMIT]}"
            f"... (truncated to {COMPANION_DEBUG_PREVIEW_LIMIT} chars, total_decoded_chars={len(decoded)}, total_bytes={len(raw)})"
        )

    def _log_info(message: str, *args: Any):
        if COMPANION_DEBUG_LOG_ENABLED:
            companion_logger.info(message, *args)

    content_type_header = request.headers.get("content-type")
    content_length_header = request.headers.get("content-length")
    raw_body = await request.body()
    body_preview = _body_preview(raw_body)
    _log_info(
        "Companion ingest request method=%s path=%s content_type=%s content_length=%s raw_body_bytes=%s body_preview=%r",
        request.method,
        request.url.path,
        content_type_header,
        content_length_header,
        len(raw_body),
        body_preview,
    )
    if raw_body:
        try:
            payload = json.loads(raw_body)
            _log_info("Companion ingest parse_path=json")
            return ingest_companion_payload(platform, madden_league_id, companion_path, payload, session)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            _log_info(
                "Companion ingest parse failed for json: %s (expected JSON body, falling back to querystring/form parsing)",
                str(exc),
            )
            try:
                parsed_query = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
            except UnicodeDecodeError as decode_exc:
                _log_info(
                    "Companion ingest parse failed for querystring decode: %s (expected UTF-8 querystring/form body)",
                    str(decode_exc),
                )
                parsed_query = {}
            if parsed_query:
                parsed_form: Dict[str, Any] = {}
                for key, values in parsed_query.items():
                    parsed_form[key] = values if len(values) > 1 else values[0]
                for candidate_key in COMPANION_JSON_FORM_KEYS:
                    candidate = parsed_form.get(candidate_key)
                    if isinstance(candidate, str):
                        try:
                            payload = json.loads(candidate)
                            _log_info("Companion ingest parse_path=querystring_embedded_json candidate_key=%s", candidate_key)
                            return ingest_companion_payload(platform, madden_league_id, companion_path, payload, session)
                        except json.JSONDecodeError as exc:
                            _log_info(
                                "Companion ingest parse failed for querystring key '%s': %s (expected JSON string in known form field)",
                                candidate_key,
                                str(exc),
                            )
                            continue
                _log_info("Companion ingest parse_path=querystring")
                return ingest_companion_payload(platform, madden_league_id, companion_path, parsed_form, session)

    form = await request.form()
    if form:
        normalized_form: Dict[str, Any] = {}
        for key, value in form.multi_items():
            existing = normalized_form.get(key)
            if existing is None:
                normalized_form[key] = value
            elif isinstance(existing, list):
                existing.append(value)
            else:
                normalized_form[key] = [existing, value]
        for candidate_key in COMPANION_JSON_FORM_KEYS:
            candidate = normalized_form.get(candidate_key)
            if isinstance(candidate, str):
                try:
                    payload = json.loads(candidate)
                    _log_info("Companion ingest parse_path=form_embedded_json candidate_key=%s", candidate_key)
                    return ingest_companion_payload(platform, madden_league_id, companion_path, payload, session)
                except json.JSONDecodeError as exc:
                    _log_info(
                        "Companion ingest parse failed for form key '%s': %s (expected JSON string in known form field)",
                        candidate_key,
                        str(exc),
                    )
                    continue
        _log_info("Companion ingest parse_path=form")
        return ingest_companion_payload(platform, madden_league_id, companion_path, normalized_form, session)

    _log_info("Companion ingest parse failed for request body: no parseable JSON, querystring, or form payload found")
    raise HTTPException(
        status_code=422,
        detail={
            "error": "Unable to parse companion payload",
            "content_type": content_type_header,
            "body_preview": body_preview,
            "raw_body_bytes": len(raw_body),
            "hint": "Expected JSON body or form-encoded data (possibly with JSON in payload/data/body/json field).",
        },
    )


@app.get("/api/{league_id}/teams")
def get_teams(
    league_id: int,
    key: str = Query(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    teams = session.exec(select(Team).where(Team.league_id == league_id)).all()
    return [team.model_dump() for team in teams]


@app.get("/api/{league_id}/rosters")
def get_rosters(
    league_id: int,
    key: str = Query(...),
    team_id: Optional[int] = Query(default=None),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    query = select(Player).where(Player.league_id == league_id)
    if team_id is not None:
        query = query.where(Player.team_id == team_id)
    players = session.exec(query).all()
    return [player.model_dump() for player in players]


@app.get("/api/{league_id}/standings")
def get_standings(
    league_id: int,
    key: str = Query(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    standings = session.exec(select(Standing).where(Standing.league_id == league_id)).all()
    standings = sorted(standings, key=lambda s: (s.wins or 0), reverse=True)
    return [standing.model_dump() for standing in standings]


@app.get("/api/{league_id}/schedules")
def get_schedules(
    league_id: int,
    key: str = Query(...),
    week_number: Optional[int] = Query(default=None),
    season_number: Optional[int] = Query(default=None),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    query = select(Schedule).where(Schedule.league_id == league_id)
    if week_number is not None:
        query = query.where(Schedule.week_number == week_number)
    if season_number is not None:
        query = query.where(Schedule.season_number == season_number)
    schedules = session.exec(query).all()
    return [schedule.model_dump() for schedule in schedules]


@app.get("/api/{league_id}/stats")
def get_stats(
    league_id: int,
    key: str = Query(...),
    week_number: Optional[int] = Query(default=None),
    season_number: Optional[int] = Query(default=None),
    player_id: Optional[int] = Query(default=None),
    season_type: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    query = select(PlayerStats).where(PlayerStats.league_id == league_id)
    if week_number is not None:
        query = query.where(PlayerStats.week_number == week_number)
    if season_number is not None:
        query = query.where(PlayerStats.season_number == season_number)
    if player_id is not None:
        query = query.where(PlayerStats.player_id == player_id)
    if season_type is not None:
        query = query.where(PlayerStats.season_type == season_type)
    stats = session.exec(query).all()
    return [stat.model_dump() for stat in stats]


@app.get("/api/{league_id}/stat_leaders")
def get_stat_leaders(
    league_id: int,
    key: str = Query(...),
    season_number: Optional[int] = Query(default=None),
    limit: int = Query(default=10, ge=1),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    return build_stat_leaders(session, league_id, season_number=season_number, limit=limit)


@app.get("/league/{league_id}", response_class=HTMLResponse)
def league_detail_page(league_id: int, request: Request, session: Session = Depends(get_session)):
    league = get_league_or_404(league_id, session)
    teams_count = len(session.exec(select(Team).where(Team.league_id == league_id)).all())
    players_count = len(session.exec(select(Player).where(Player.league_id == league_id)).all())
    return templates.TemplateResponse(
        "league_detail.html",
        {"request": request, "league": league, "teams_count": teams_count, "players_count": players_count},
    )


@app.get("/league/{league_id}/standings", response_class=HTMLResponse)
def league_standings_page(league_id: int, request: Request, session: Session = Depends(get_session)):
    league = get_league_or_404(league_id, session)
    standings = session.exec(select(Standing).where(Standing.league_id == league_id)).all()
    team_map = {
        team.id: team
        for team in session.exec(select(Team).where(Team.league_id == league_id)).all()
        if team.id is not None
    }
    standings = sorted(standings, key=lambda s: (s.wins or 0), reverse=True)
    return templates.TemplateResponse(
        "league_standings.html",
        {"request": request, "league": league, "standings": standings, "team_map": team_map},
    )


@app.get("/league/{league_id}/roster", response_class=HTMLResponse)
def league_roster_page(league_id: int, request: Request, session: Session = Depends(get_session)):
    league = get_league_or_404(league_id, session)
    teams = session.exec(select(Team).where(Team.league_id == league_id)).all()
    players = session.exec(select(Player).where(Player.league_id == league_id)).all()
    players_by_team: DefaultDict[Optional[int], List[Player]] = defaultdict(list)
    for player in players:
        players_by_team[player.team_id].append(player)

    sorted_team_players: List[Tuple[Team, List[Player]]] = []
    for team in sorted(teams, key=lambda t: t.team_name or ""):
        team_players = sorted(players_by_team.get(team.id, []), key=lambda p: (p.last_name or "", p.first_name or ""))
        sorted_team_players.append((team, team_players))

    return templates.TemplateResponse(
        "league_roster.html",
        {"request": request, "league": league, "team_players": sorted_team_players},
    )


@app.get("/league/{league_id}/schedule", response_class=HTMLResponse)
def league_schedule_page(league_id: int, request: Request, session: Session = Depends(get_session)):
    league = get_league_or_404(league_id, session)
    schedules = session.exec(select(Schedule).where(Schedule.league_id == league_id)).all()
    team_map = {
        team.id: team
        for team in session.exec(select(Team).where(Team.league_id == league_id)).all()
        if team.id is not None
    }
    games_by_week: DefaultDict[int, List[Schedule]] = defaultdict(list)
    for game in schedules:
        games_by_week[game.week_number].append(game)
    week_groups = sorted(games_by_week.items(), key=lambda item: item[0])
    return templates.TemplateResponse(
        "league_schedule.html",
        {"request": request, "league": league, "week_groups": week_groups, "team_map": team_map},
    )


@app.get("/league/{league_id}/leaders", response_class=HTMLResponse)
def league_leaders_page(league_id: int, request: Request, session: Session = Depends(get_session)):
    league = get_league_or_404(league_id, session)
    leaders = build_stat_leaders(session, league_id, season_number=None, limit=10)
    return templates.TemplateResponse(
        "league_leaders.html",
        {"request": request, "league": league, "leaders": leaders},
    )


@app.get("/league/{league_id}/player/{player_id}", response_class=HTMLResponse)
def player_profile_page(league_id: int, player_id: int, request: Request, session: Session = Depends(get_session)):
    league = get_league_or_404(league_id, session)
    player = session.exec(
        select(Player).where(Player.league_id == league_id, Player.id == player_id)
    ).first()
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    team = None
    if player.team_id is not None:
        team = session.get(Team, (player.team_id, league_id))
    stats_rows = session.exec(
        select(PlayerStats).where(
            PlayerStats.league_id == league_id,
            PlayerStats.player_id == player_id,
        )
    ).all()
    stats_rows = sorted(stats_rows, key=lambda s: (s.season_number, s.week_number))
    return templates.TemplateResponse(
        "player_profile.html",
        {"request": request, "league": league, "player": player, "team": team, "stats_rows": stats_rows},
    )

@app.get("/home", response_class=HTMLResponse)
def home(request: Request):
    # Optional public info
    return templates.TemplateResponse("home.html", {"request": request})

@app.exception_handler(Exception)
def global_exception_handler(request: Request, exc: Exception):
    # Nice error screen for debugging/demo
    print("Unhandled exception:", exc)
    return HTMLResponse(
        f"<h1>Internal Error</h1><pre>{exc}</pre><p><a href='/'>Back to dashboard</a></p>",
        status_code=500,
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
