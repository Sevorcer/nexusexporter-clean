import json
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session, select

from app.companion import ingest_companion_payload
from app.config import (
    COMPANION_DEBUG_LOG_ENABLED,
    COMPANION_DEBUG_PREVIEW_LIMIT,
    COMPANION_JSON_FORM_KEYS,
    companion_logger,
)
from app.db import engine, get_session
from app.deps import validate_api_key
from app.ingest import (
    apply_rosters,
    apply_schedules,
    apply_standings,
    apply_stats_clear_and_insert,
    apply_teams,
)
from app.models import PlayerStats, Schedule, Standing, Team, Player
from app.schemas import PlayerIn, PlayerStatsIn, ScheduleIn, StandingIn, TeamIn
from app.stats import build_stat_leaders

router = APIRouter()


@router.get("/healthz")
def healthz():
    """Liveness + DB-reachability probe used by Docker/Railway healthchecks."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok"}
    except Exception as exc:  # pragma: no cover - infra concern
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": "unreachable", "error": str(exc)},
        )


@router.post("/api/{league_id}/teams")
def ingest_teams(
    league_id: int,
    key: str = Query(...),
    teams: List[TeamIn] = Body(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    return apply_teams(league_id, teams, session)


@router.post("/api/{league_id}/rosters")
def ingest_rosters(
    league_id: int,
    key: str = Query(...),
    players: List[PlayerIn] = Body(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    return apply_rosters(league_id, players, session)


@router.post("/api/{league_id}/standings")
def ingest_standings(
    league_id: int,
    key: str = Query(...),
    standings: List[StandingIn] = Body(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    return apply_standings(league_id, standings, session)


@router.post("/api/{league_id}/schedules")
def ingest_schedules(
    league_id: int,
    key: str = Query(...),
    schedules: List[ScheduleIn] = Body(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    return apply_schedules(league_id, schedules, session)


@router.post("/api/{league_id}/stats")
def ingest_stats(
    league_id: int,
    key: str = Query(...),
    stats: List[PlayerStatsIn] = Body(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    return apply_stats_clear_and_insert(league_id, stats, session)


@router.post("//{platform}/{madden_league_id}/{companion_path:path}")
@router.post("/{platform}/{madden_league_id}/{companion_path:path}")
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
            return ingest_companion_payload(
                platform, madden_league_id, companion_path, payload, session
            )
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
                            _log_info(
                                "Companion ingest parse_path=querystring_embedded_json candidate_key=%s",
                                candidate_key,
                            )
                            return ingest_companion_payload(
                                platform, madden_league_id, companion_path, payload, session
                            )
                        except json.JSONDecodeError as exc:
                            _log_info(
                                "Companion ingest parse failed for querystring key '%s': %s (expected JSON string in known form field)",
                                candidate_key,
                                str(exc),
                            )
                            continue
                _log_info("Companion ingest parse_path=querystring")
                return ingest_companion_payload(
                    platform, madden_league_id, companion_path, parsed_form, session
                )

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
                    _log_info(
                        "Companion ingest parse_path=form_embedded_json candidate_key=%s",
                        candidate_key,
                    )
                    return ingest_companion_payload(
                        platform, madden_league_id, companion_path, payload, session
                    )
                except json.JSONDecodeError as exc:
                    _log_info(
                        "Companion ingest parse failed for form key '%s': %s (expected JSON string in known form field)",
                        candidate_key,
                        str(exc),
                    )
                    continue
        _log_info("Companion ingest parse_path=form")
        return ingest_companion_payload(
            platform, madden_league_id, companion_path, normalized_form, session
        )

    _log_info(
        "Companion ingest parse failed for request body: no parseable JSON, querystring, or form payload found"
    )
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


@router.get("/api/{league_id}/teams")
def get_teams(
    league_id: int,
    key: str = Query(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    teams = session.exec(select(Team).where(Team.league_id == league_id)).all()
    return [team.model_dump() for team in teams]


@router.get("/api/{league_id}/rosters")
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


@router.get("/api/{league_id}/standings")
def get_standings(
    league_id: int,
    key: str = Query(...),
    season_type: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    query = select(Standing).where(Standing.league_id == league_id)
    if season_type is not None:
        query = query.where(Standing.season_type == season_type)
    standings = session.exec(query).all()
    standings = sorted(standings, key=lambda s: (s.wins or 0), reverse=True)
    return [standing.model_dump() for standing in standings]


@router.get("/api/{league_id}/schedules")
def get_schedules(
    league_id: int,
    key: str = Query(...),
    week_number: Optional[int] = Query(default=None),
    season_number: Optional[int] = Query(default=None),
    season_type: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    query = select(Schedule).where(Schedule.league_id == league_id)
    if week_number is not None:
        query = query.where(Schedule.week_number == week_number)
    if season_number is not None:
        query = query.where(Schedule.season_number == season_number)
    if season_type is not None:
        query = query.where(Schedule.season_type == season_type)
    schedules = session.exec(query).all()
    return [schedule.model_dump() for schedule in schedules]


@router.get("/api/{league_id}/stats")
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


@router.get("/api/{league_id}/stat_leaders")
def get_stat_leaders(
    league_id: int,
    key: str = Query(...),
    season_number: Optional[int] = Query(default=None),
    limit: int = Query(default=10, ge=1),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    return build_stat_leaders(session, league_id, season_number=season_number, limit=limit)
