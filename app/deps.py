from fastapi import HTTPException
from sqlmodel import Session, select

from app.models import League


def validate_api_key(league_id: int, key: str, session: Session) -> League:
    league = session.get(League, league_id)
    if league is None:
        raise HTTPException(status_code=404, detail="League not found")
    if key != league.api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return league


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
