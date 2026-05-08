import secrets
from typing import Optional
from urllib.parse import urlencode

from fastapi import Depends, Request
from sqlmodel import Session, select

from app.config import DISCORD_CLIENT_ID, DISCORD_REDIRECT_URI
from app.db import get_session
from app.models import User


def get_current_user(request: Request, session: Session = Depends(get_session)) -> Optional[User]:
    discord_id = request.session.get("discord_id")
    if not discord_id:
        return None
    return session.exec(select(User).where(User.discord_id == discord_id)).first()


def get_csrf_token(request: Request) -> str:
    """Return the per-session CSRF token, generating one on first access."""
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def validate_csrf(request: Request, submitted: Optional[str]) -> bool:
    expected = request.session.get("csrf_token")
    if not expected or not submitted:
        return False
    return secrets.compare_digest(expected, submitted)


def build_discord_auth_url(state: str) -> str:
    return "https://discord.com/api/oauth2/authorize?" + urlencode({
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "state": state,
    })
