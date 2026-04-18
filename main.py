import os
import secrets
from typing import Optional, List, Any, Dict, Set, Tuple
from urllib.parse import urlencode

from fastapi import FastAPI, Request, Form, Depends, status, HTTPException, Body, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel, Field, Relationship, Session, select, create_engine
from starlette.middleware.sessions import SessionMiddleware
import httpx

# ----------- Config -----------
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///database.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "very-secret-dev-key")
DISCORD_CLIENT_ID = os.environ["DISCORD_CLIENT_ID"]
DISCORD_CLIENT_SECRET = os.environ["DISCORD_CLIENT_SECRET"]
DISCORD_REDIRECT_URI = os.environ["DISCORD_REDIRECT_URI"]

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
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    user: Optional[User] = Relationship(back_populates="leagues")

class Team(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    league_id: int = Field(foreign_key="league.id")
    team_name: Optional[str] = None
    abbreviation: Optional[str] = None
    division: Optional[str] = None
    overall_rating: Optional[int] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    ties: Optional[int] = None
    city_name: Optional[str] = None

class Player(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    league_id: int = Field(foreign_key="league.id")
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
    id: Optional[int] = Field(default=None, primary_key=True)
    league_id: int = Field(foreign_key="league.id")
    team_id: Optional[int] = Field(default=None, foreign_key="team.id")
    wins: Optional[int] = None
    losses: Optional[int] = None
    ties: Optional[int] = None
    division_name: Optional[str] = None
    seed: Optional[int] = None

class PlayerStats(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    league_id: int = Field(foreign_key="league.id")
    player_id: Optional[int] = Field(default=None, foreign_key="player.id")
    week_number: int
    season_number: int
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

def clear_league_records(session: Session, model: Any, league_id: int) -> int:
    records = session.exec(select(model).where(model.league_id == league_id)).all()
    for record in records:
        session.delete(record)
    return len(records)

def filter_payload(record: Dict[str, Any], allowed_fields: Set[str]) -> Dict[str, Any]:
    return {field: value for field, value in record.items() if field in allowed_fields}

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

@app.post("/api/{league_id}/teams")
def ingest_teams(
    league_id: int,
    key: str = Query(...),
    teams: List[Dict[str, Any]] = Body(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    clear_league_records(session, PlayerStats, league_id)
    clear_league_records(session, Standing, league_id)
    clear_league_records(session, Schedule, league_id)
    clear_league_records(session, Player, league_id)
    cleared = clear_league_records(session, Team, league_id)
    allowed_fields = {
        "id", "team_name", "abbreviation", "division", "overall_rating",
        "wins", "losses", "ties", "city_name",
    }
    for team_data in teams:
        payload = filter_payload(team_data, allowed_fields)
        session.add(Team(league_id=league_id, **payload))
    session.commit()
    return {"success": True, "cleared": cleared, "inserted": len(teams)}

@app.post("/api/{league_id}/rosters")
def ingest_rosters(
    league_id: int,
    key: str = Query(...),
    players: List[Dict[str, Any]] = Body(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    clear_league_records(session, PlayerStats, league_id)
    cleared = clear_league_records(session, Player, league_id)
    allowed_fields = {
        "id", "team_id", "first_name", "last_name", "position", "overall_rating",
        "age", "jersey_number", "dev_trait", "contract_years", "contract_salary",
    }
    for player_data in players:
        payload = filter_payload(player_data, allowed_fields)
        session.add(Player(league_id=league_id, **payload))
    session.commit()
    return {"success": True, "cleared": cleared, "inserted": len(players)}

@app.post("/api/{league_id}/standings")
def ingest_standings(
    league_id: int,
    key: str = Query(...),
    standings: List[Dict[str, Any]] = Body(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    cleared = clear_league_records(session, Standing, league_id)
    allowed_fields = {"id", "team_id", "wins", "losses", "ties", "division_name", "seed"}
    for standing_data in standings:
        payload = filter_payload(standing_data, allowed_fields)
        session.add(Standing(league_id=league_id, **payload))
    session.commit()
    return {"success": True, "cleared": cleared, "inserted": len(standings)}

@app.post("/api/{league_id}/schedules")
def ingest_schedules(
    league_id: int,
    key: str = Query(...),
    schedules: List[Dict[str, Any]] = Body(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    cleared = clear_league_records(session, Schedule, league_id)
    allowed_fields = {
        "id", "week_number", "season_number", "home_team_id", "away_team_id",
        "home_score", "away_score", "is_complete",
    }
    for schedule_data in schedules:
        payload = filter_payload(schedule_data, allowed_fields)
        session.add(Schedule(league_id=league_id, **payload))
    session.commit()
    return {"success": True, "cleared": cleared, "inserted": len(schedules)}

@app.post("/api/{league_id}/stats")
def ingest_stats(
    league_id: int,
    key: str = Query(...),
    stats: List[Dict[str, Any]] = Body(...),
    session: Session = Depends(get_session),
):
    validate_api_key(league_id, key, session)
    week_season_pairs: Set[Tuple[int, int]] = set()
    for stat_data in stats:
        if "week_number" in stat_data and "season_number" in stat_data:
            week_season_pairs.add((stat_data["week_number"], stat_data["season_number"]))

    cleared = 0
    for week_number, season_number in week_season_pairs:
        records = session.exec(
            select(PlayerStats).where(
                PlayerStats.league_id == league_id,
                PlayerStats.week_number == week_number,
                PlayerStats.season_number == season_number,
            )
        ).all()
        cleared += len(records)
        for record in records:
            session.delete(record)

    allowed_fields = {
        "id", "player_id", "week_number", "season_number", "pass_yards",
        "pass_tds", "interceptions", "rush_yards", "rush_tds", "rec_yards",
        "rec_tds", "receptions", "tackles", "sacks", "fumbles_forced",
        "defensive_ints",
    }
    for stat_data in stats:
        payload = filter_payload(stat_data, allowed_fields)
        session.add(PlayerStats(league_id=league_id, **payload))
    session.commit()
    return {"success": True, "cleared": cleared, "inserted": len(stats)}

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
