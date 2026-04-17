import os
import secrets
from typing import List, Optional

from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel, Field, Relationship, Session, select, create_engine
from starlette.middleware.sessions import SessionMiddleware

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///database.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
templates = Jinja2Templates(directory="templates")

app.mount("/static", StaticFiles(directory="static"), name="static")

# --- MODELS ----

class League(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    api_key: str
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str
    leagues: List["League"] = Relationship(back_populates="user")

League.user = Relationship(back_populates="leagues")

# --- DATABASE INIT ----
def create_db():
    SQLModel.metadata.create_all(engine)
create_db()

# --- DEPENDENCIES ----
def get_session():
    with Session(engine) as session:
        yield session

def get_current_user(request: Request, session: Session = Depends(get_session)) -> User:
    username = request.session.get("username")
    if not username:
        # Not logged in
        raise RedirectResponse("/login", status_code=303)
    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        user = User(username=username)
        session.add(user)
        session.commit()
        session.refresh(user)
    return user

# ROUTES

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    username = request.session.get("username")
    if not username:
        return RedirectResponse("/login", status_code=303)
    user = session.exec(select(User).where(User.username == username)).first()
    leagues = []
    if user:
        leagues = session.exec(select(League).where(League.user_id == user.id)).all()
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user, "leagues": leagues})

@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
def login_post(request: Request, username: str = Form(...)):
    request.session["username"] = username
    return RedirectResponse("/", status_code=303)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

@app.post("/create_league")
def create_league(
    request: Request,
    league_name: str = Form(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    api_key = secrets.token_hex(16)
    league = League(name=league_name, api_key=api_key, user_id=user.id)
    session.add(league)
    session.commit()
    return RedirectResponse("/", status_code=303)
