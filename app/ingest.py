from typing import Any, Dict, List, Optional, Set, Tuple, Type

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, SQLModel, delete, select

from app.db import engine
from app.models import Player, PlayerStats, Schedule, Standing, Team
from app.schemas import PlayerIn, PlayerStatsIn, ScheduleIn, StandingIn, TeamIn


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


def apply_teams(league_id: int, teams: List[TeamIn], session: Session) -> Dict[str, Any]:
    for team_data in teams:
        payload = team_data.model_dump(exclude_unset=True)
        payload["league_id"] = league_id
        _upsert(session, Team, payload)
    session.commit()
    return {"success": True, "cleared": 0, "inserted": len(teams)}


def apply_rosters(
    league_id: int,
    players: List[PlayerIn],
    session: Session,
    team_id_scope: Optional[int] = None,
    free_agents_only: bool = False,
) -> Dict[str, Any]:
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


def apply_standings(league_id: int, standings: List[StandingIn], session: Session) -> Dict[str, Any]:
    for standing_data in standings:
        payload = standing_data.model_dump(exclude_unset=True)
        payload["league_id"] = league_id
        _upsert(session, Standing, payload)
    session.commit()
    return {"success": True, "cleared": 0, "inserted": len(standings)}


def apply_schedules(league_id: int, schedules: List[ScheduleIn], session: Session) -> Dict[str, Any]:
    for schedule_data in schedules:
        payload = schedule_data.model_dump(exclude_unset=True)
        payload["league_id"] = league_id
        _upsert(session, Schedule, payload)
    session.commit()
    return {"success": True, "cleared": 0, "inserted": len(schedules)}


def apply_stats_clear_and_insert(
    league_id: int, stats: List[PlayerStatsIn], session: Session
) -> Dict[str, Any]:
    """Clear-then-insert variant used by the explicit `/api/{league_id}/stats` route."""
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


def upsert_teams_from_standings(league_id: int, teams: List[TeamIn], session: Session) -> int:
    for team_data in teams:
        if team_data.id is None:
            continue
        payload = team_data.model_dump(exclude_unset=True)
        payload["league_id"] = league_id
        _upsert(session, Team, payload)
    session.commit()
    return len(teams)


def ingest_companion_stats(
    league_id: int, stats: List[PlayerStatsIn], session: Session
) -> Dict[str, Any]:
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
            update_fields = {
                k: stmt.excluded[k]
                for k in payload
                if k not in ("id", "player_id", "week_number", "season_number", "league_id")
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["league_id", "player_id", "week_number", "season_number"],
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
