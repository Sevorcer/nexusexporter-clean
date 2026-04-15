import os
import uuid

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from dotenv import load_dotenv
load_dotenv()

from authlib.integrations.starlette_client import OAuth

from sqlmodel import SQLModel, Field, create_engine, Session, select

# Set up database
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, echo=True)

# Define the user model
class User(SQLModel, table=True):
    discord_id: str = Field(primary_key=True)
    username: str
    league: str | None = None
    api_key: str | None = None

# Build tables (safe to call multiple times)
SQLModel.metadata.create_all(engine)

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY"))
templates = Jinja2Templates(directory="templates")

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")

def get_current_user(request: Request):
    return request.session.get("user")

oauth = OAuth()
oauth.register(
    name="discord",
    client_id=DISCORD_CLIENT_ID,
    client_secret=DISCORD_CLIENT_SECRET,
    access_token_url="https://discord.com/api/oauth2/token",
    access_token_params=None,
    authorize_url="https://discord.com/api/oauth2/authorize",
    authorize_params=None,
    api_base_url="https://discord.com/api/",
    client_kwargs={"scope": "identify email"},
)

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = get_current_user(request)
    return templates.TemplateResponse("home.html", {"request": request, "user": user})

@app.get("/login")
async def login(request: Request):
    redirect_uri = DISCORD_REDIRECT_URI
    return await oauth.discord.authorize_redirect(request, redirect_uri)

@app.get("/callback")
async def callback(request: Request):
    token = await oauth.discord.authorize_access_token(request)
    # Fetch user info directly
    resp = await oauth.discord.get("users/@me", token=token)
    profile = resp.json()
    discord_id = profile["id"]
    username = f'{profile["username"]}#{profile["discriminator"]}'
    user = {
        "discord_id": discord_id,
        "username": username
    }
    request.session["user"] = user
    with Session(engine) as session:
        db_user = session.get(User, discord_id)
        if not db_user:
            db_user = User(discord_id=discord_id, username=username)
            session.add(db_user)
            session.commit()
    return RedirectResponse("/dashboard", status_code=303)

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    discord_id = user["discord_id"]
    with Session(engine) as session:
        db_user = session.get(User, discord_id)
        if not db_user:
            # Optional: create user entry if missing (rare)
            db_user = User(discord_id=discord_id, username=user["username"])
            session.add(db_user)
            session.commit()
        league = db_user.league
        api_key = db_user.api_key
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user, "league": league, "api_key": api_key
    })

@app.post("/create_league")
def create_league(request: Request, league_name: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    with Session(engine) as session:
        db_user = session.get(User, user["discord_id"])
        if db_user:
            db_user.league = league_name
            db_user.api_key = str(uuid.uuid4())
            session.add(db_user)
            session.commit()
    return RedirectResponse("/dashboard", status_code=303)

@app.get("/logout")
def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse("/", status_code=303)
