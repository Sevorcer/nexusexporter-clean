import secrets
from collections import defaultdict
from typing import DefaultDict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlmodel import Session, select

from app.auth import build_discord_auth_url, get_csrf_token, get_current_user
from app.config import DISCORD_BOT_CLIENT_ID
from app.db import get_session
from app.deps import get_league_or_404
from app.models import League, Player, PlayerStats, Schedule, Standing, Team
from app.stats import build_stat_leaders
from app.templates import templates


def _bot_invite_url(client_id: Optional[str]) -> Optional[str]:
    if not client_id:
        return None
    return (
        "https://discord.com/oauth2/authorize?"
        f"client_id={client_id}"
        "&scope=bot+applications.commands"
        "&permissions=8"
    )

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: Session = Depends(get_session)):
    user = get_current_user(request, session)
    error = request.session.pop("flash_error", None)
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    discord_auth_url = build_discord_auth_url(state)
    if not user:
        return templates.TemplateResponse(
            "home.html",
            {
                "request": request,
                "user": None,
                "discord_auth_url": discord_auth_url,
                "error": error,
            },
        )

    flash_msg = request.session.pop("flash_msg", None)
    leagues = session.exec(select(League).where(League.user_id == user.id)).all()

    league_meta = {}
    for league in leagues:
        lid = league.id
        teams_count = session.exec(
            select(func.count()).select_from(Team).where(Team.league_id == lid)
        ).one()
        players_count = session.exec(
            select(func.count()).select_from(Player).where(Player.league_id == lid)
        ).one()
        completed_weeks = session.exec(
            select(func.max(Schedule.week_number)).where(
                Schedule.league_id == lid, Schedule.is_complete
            )
        ).one()
        has_standings = (
            session.exec(
                select(func.count()).select_from(Standing).where(Standing.league_id == lid)
            ).one()
            > 0
        )
        league_meta[lid] = {
            "teams_count": teams_count or 0,
            "players_count": players_count or 0,
            "current_week": completed_weeks or 0,
            "has_data": (teams_count or 0) > 0,
            "has_standings": has_standings,
        }

    effective_bot_client_id = user.bot_client_id or DISCORD_BOT_CLIENT_ID
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "leagues": leagues,
            "league_meta": league_meta,
            "error": error,
            "flash_msg": flash_msg,
            "csrf_token": get_csrf_token(request),
            "bot_client_id": effective_bot_client_id,
            "bot_client_id_user_set": bool(user.bot_client_id),
            "bot_invite_url": _bot_invite_url(effective_bot_client_id),
        },
    )


@router.get("/home", response_class=HTMLResponse)
def public_home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@router.get("/league/{league_id}", response_class=HTMLResponse)
def league_detail_page(
    league_id: int, request: Request, session: Session = Depends(get_session)
):
    league = get_league_or_404(league_id, session)
    teams_count = len(session.exec(select(Team).where(Team.league_id == league_id)).all())
    players_count = len(session.exec(select(Player).where(Player.league_id == league_id)).all())
    return templates.TemplateResponse(
        "league_detail.html",
        {
            "request": request,
            "league": league,
            "teams_count": teams_count,
            "players_count": players_count,
        },
    )


@router.get("/league/{league_id}/standings", response_class=HTMLResponse)
def league_standings_page(
    league_id: int, request: Request, session: Session = Depends(get_session)
):
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


@router.get("/league/{league_id}/roster", response_class=HTMLResponse)
def league_roster_page(
    league_id: int, request: Request, session: Session = Depends(get_session)
):
    league = get_league_or_404(league_id, session)
    teams = session.exec(select(Team).where(Team.league_id == league_id)).all()
    players = session.exec(select(Player).where(Player.league_id == league_id)).all()
    players_by_team: DefaultDict[Optional[int], List[Player]] = defaultdict(list)
    for player in players:
        players_by_team[player.team_id].append(player)

    sorted_team_players: List[Tuple[Team, List[Player]]] = []
    for team in sorted(teams, key=lambda t: t.team_name or ""):
        team_players = sorted(
            players_by_team.get(team.id, []),
            key=lambda p: (p.last_name or "", p.first_name or ""),
        )
        sorted_team_players.append((team, team_players))

    return templates.TemplateResponse(
        "league_roster.html",
        {"request": request, "league": league, "team_players": sorted_team_players},
    )


@router.get("/league/{league_id}/schedule", response_class=HTMLResponse)
def league_schedule_page(
    league_id: int, request: Request, session: Session = Depends(get_session)
):
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
        {
            "request": request,
            "league": league,
            "week_groups": week_groups,
            "team_map": team_map,
        },
    )


@router.get("/league/{league_id}/leaders", response_class=HTMLResponse)
def league_leaders_page(
    league_id: int, request: Request, session: Session = Depends(get_session)
):
    league = get_league_or_404(league_id, session)
    leaders = build_stat_leaders(session, league_id, season_number=None, limit=10)
    return templates.TemplateResponse(
        "league_leaders.html",
        {"request": request, "league": league, "leaders": leaders},
    )


@router.get("/league/{league_id}/player/{player_id}", response_class=HTMLResponse)
def player_profile_page(
    league_id: int,
    player_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
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
        {
            "request": request,
            "league": league,
            "player": player,
            "team": team,
            "stats_rows": stats_rows,
        },
    )
