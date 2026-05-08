from typing import List, Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    discord_id: str
    username: str
    avatar: Optional[str] = None
    bot_client_id: Optional[str] = Field(default=None)
    leagues: List["League"] = Relationship(back_populates="user")


class League(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    api_key: str
    madden_league_id: Optional[str] = Field(default=None, index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    user: Optional[User] = Relationship(back_populates="leagues")


class Team(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("league_id", "id", name="uq_team_league_id"),
    )
    id: int = Field(primary_key=True)
    league_id: int = Field(primary_key=True, foreign_key="league.id")
    team_name: Optional[str] = None
    abbreviation: Optional[str] = None
    division: Optional[str] = None
    overall_rating: Optional[int] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    ties: Optional[int] = None
    city_name: Optional[str] = None


class Player(SQLModel, table=True):
    id: int = Field(primary_key=True)
    league_id: int = Field(primary_key=True, foreign_key="league.id")
    team_id: Optional[int] = Field(default=None)
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
    __table_args__ = (
        UniqueConstraint("league_id", "id", name="uq_schedule_league_id"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    league_id: int = Field(foreign_key="league.id")
    week_number: int
    season_number: int
    home_team_id: Optional[int] = Field(default=None)
    away_team_id: Optional[int] = Field(default=None)
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    is_complete: bool = False
    season_type: Optional[str] = None


class Standing(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("league_id", "id", name="uq_standing_league_id"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    league_id: int = Field(foreign_key="league.id")
    team_id: Optional[int] = Field(default=None)
    wins: Optional[int] = None
    losses: Optional[int] = None
    ties: Optional[int] = None
    division_name: Optional[str] = None
    seed: Optional[int] = None
    season_type: Optional[str] = None


class PlayerStats(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    league_id: int = Field(foreign_key="league.id")
    player_id: Optional[int] = Field(default=None)
    week_number: int
    season_number: int
    season_type: Optional[str] = None
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
