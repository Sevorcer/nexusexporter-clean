import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

import httpx

from app.auth import build_discord_auth_url, get_current_user, validate_csrf
from app.config import (
    DISCORD_CLIENT_ID,
    DISCORD_CLIENT_SECRET,
    DISCORD_REDIRECT_URI,
    MAX_BOT_CLIENT_ID_LENGTH,
    MAX_MADDEN_LEAGUE_ID_LENGTH,
)
from app.db import get_session
from app.models import League, User
from app.templates import templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    error = request.session.pop("flash_error", None)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "discord_auth_url": build_discord_auth_url(state), "error": error},
    )


@router.get("/oauth-callback")
async def discord_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    session: Session = Depends(get_session),
):
    if not code:
        request.session["flash_error"] = "No code from Discord; please try again."
        return RedirectResponse("/login", status_code=303)

    expected_state = request.session.pop("oauth_state", None)
    if not expected_state or not state or not secrets.compare_digest(expected_state, state):
        request.session["flash_error"] = "Login session expired or tampered. Please try again."
        return RedirectResponse("/login", status_code=303)

    async with httpx.AsyncClient(timeout=10.0) as client:
        data = {
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": DISCORD_REDIRECT_URI,
            "scope": "identify",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        try:
            token_res = await client.post(
                "https://discord.com/api/oauth2/token", data=data, headers=headers
            )
        except httpx.HTTPError:
            request.session["flash_error"] = "Could not reach Discord. Try again in a moment."
            return RedirectResponse("/login", status_code=303)
        if token_res.status_code != 200:
            request.session["flash_error"] = "Discord OAuth failed."
            return RedirectResponse("/login", status_code=303)
        token_json = token_res.json()
        access_token = token_json.get("access_token") if isinstance(token_json, dict) else None
        if not access_token:
            request.session["flash_error"] = "Discord did not return an access token."
            return RedirectResponse("/login", status_code=303)

        try:
            user_res = await client.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError:
            request.session["flash_error"] = "Could not reach Discord. Try again in a moment."
            return RedirectResponse("/login", status_code=303)
        if user_res.status_code != 200:
            request.session["flash_error"] = "Failed to get user info from Discord."
            return RedirectResponse("/login", status_code=303)
        discord_info = (
            user_res.json()
            if user_res.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        discord_id = discord_info.get("id")
        username = discord_info.get("username")
        avatar = discord_info.get("avatar")
        if not discord_id or not username:
            request.session["flash_error"] = "Discord returned an unexpected response."
            return RedirectResponse("/login", status_code=303)

        user = session.exec(select(User).where(User.discord_id == discord_id)).first()
        if user is None:
            user = User(discord_id=discord_id, username=username, avatar=avatar)
            session.add(user)
            session.commit()
            session.refresh(user)
        else:
            user.avatar = avatar
            user.username = username
            session.add(user)
            session.commit()

        request.session["discord_id"] = discord_id

    return RedirectResponse("/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.post("/create_league")
def create_league(
    request: Request,
    league_name: str = Form(...),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
):
    if not validate_csrf(request, csrf_token):
        request.session["flash_error"] = "Session expired; please reload and try again."
        return RedirectResponse("/", status_code=303)
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


@router.post("/set_madden_id")
def set_madden_id(
    request: Request,
    league_id: int = Form(...),
    madden_league_id: Optional[str] = Form(default=""),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
):
    if not validate_csrf(request, csrf_token):
        request.session["flash_error"] = "Session expired; please reload and try again."
        return RedirectResponse("/", status_code=303)
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
        request.session["flash_error"] = (
            f"Madden league ID must be {MAX_MADDEN_LEAGUE_ID_LENGTH} characters or less."
        )
        return RedirectResponse("/", status_code=303)

    league.madden_league_id = cleaned_madden_id or None
    session.add(league)
    session.commit()
    request.session["flash_msg"] = f"Madden league ID saved for '{league.name}'."
    return RedirectResponse("/", status_code=303)


@router.post("/set_bot_client_id")
def set_bot_client_id(
    request: Request,
    bot_client_id: Optional[str] = Form(default=""),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
):
    if not validate_csrf(request, csrf_token):
        request.session["flash_error"] = "Session expired; please reload and try again."
        return RedirectResponse("/", status_code=303)
    user = get_current_user(request, session)
    if not user:
        request.session["flash_error"] = "Please log in to update bot settings."
        return RedirectResponse("/login", status_code=303)

    cleaned = (bot_client_id or "").strip()
    if cleaned and not cleaned.isdigit():
        request.session["flash_error"] = "Bot Client ID must be numeric (Discord application snowflake ID)."
        return RedirectResponse("/", status_code=303)
    if len(cleaned) > MAX_BOT_CLIENT_ID_LENGTH:
        request.session["flash_error"] = (
            f"Bot Client ID must be {MAX_BOT_CLIENT_ID_LENGTH} characters or less."
        )
        return RedirectResponse("/", status_code=303)

    user.bot_client_id = cleaned or None
    session.add(user)
    session.commit()
    request.session["flash_msg"] = (
        "Bot Client ID saved." if cleaned else "Bot Client ID cleared."
    )
    return RedirectResponse("/", status_code=303)
