import json
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import HTTPException
from sqlmodel import Session

from app.config import DEV_TRAIT_MAP
from app.deps import get_league_by_madden_id_or_404
from app.ingest import (
    apply_rosters,
    apply_schedules,
    apply_standings,
    apply_teams,
    ingest_companion_stats,
    upsert_teams_from_standings,
)
from app.schemas import (
    PlayerIn,
    PlayerStatsIn,
    ScheduleIn,
    StandingIn,
    TeamIn,
)


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


CompanionPayloadType = Optional[
    Literal["standings", "roster", "schedule", "passing", "rushing", "defense", "receiving", "teams", "untracked"]
]


def _extract_companion_rows(payload: Any) -> Tuple[CompanionPayloadType, List[Dict[str, Any]]]:
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


def _transform_madden_standings(
    rows: List[Dict[str, Any]], week_type: Optional[str] = None
) -> Tuple[List[StandingIn], List[TeamIn]]:
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
                season_type=week_type,
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


def _transform_madden_schedule(
    rows: List[Dict[str, Any]], week_type: Optional[str] = None
) -> List[ScheduleIn]:
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
                season_type=week_type,
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


def _transform_madden_stats(
    rows: List[Dict[str, Any]],
    payload_type: Literal["passing", "rushing", "defense"],
    week_type: Optional[str] = None,
) -> List[PlayerStatsIn]:
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


def _transform_madden_receiving_stats(
    rows: List[Dict[str, Any]], week_type: Optional[str] = None
) -> List[PlayerStatsIn]:
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
        standings, teams_from_standings = _transform_madden_standings(rows, week_type=week_type)
        upsert_teams_from_standings(league.id, teams_from_standings, session)
        return apply_standings(league.id, standings, session)
    if payload_type == "roster":
        players = _transform_madden_roster(rows)
        return apply_rosters(
            league.id,
            players,
            session,
            team_id_scope=team_roster_id_scope,
            free_agents_only=is_free_agents_roster_path,
        )
    if payload_type == "schedule":
        schedules = _transform_madden_schedule(rows, week_type=week_type)
        return apply_schedules(league.id, schedules, session)
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
        teams = (
            _transform_madden_teams(rows)
            if should_transform_teams
            else [TeamIn.model_validate(row) for row in rows]
        )
        return apply_teams(league.id, teams, session)
    if normalized_path == "standings":
        standings = [StandingIn.model_validate(row) for row in rows]
        return apply_standings(league.id, standings, session)
    if normalized_path in {"schedules", "schedule"}:
        schedules = [ScheduleIn.model_validate(row) for row in rows]
        return apply_schedules(league.id, schedules, session)
    if is_free_agents_roster_path or is_team_roster_path:
        players = [PlayerIn.model_validate(row) for row in rows]
        return apply_rosters(
            league.id,
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
