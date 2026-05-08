from typing import Optional

from sqlmodel import SQLModel


class TeamIn(SQLModel):
    id: Optional[int] = None
    team_name: Optional[str] = None
    abbreviation: Optional[str] = None
    division: Optional[str] = None
    overall_rating: Optional[int] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    ties: Optional[int] = None
    city_name: Optional[str] = None


class PlayerIn(SQLModel):
    id: Optional[int] = None
    team_id: Optional[int] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    position: Optional[str] = None
    overall_rating: Optional[int] = None
    age: Optional[int] = None
    jersey_number: Optional[int] = None
    dev_trait: Optional[str] = None
    contract_years: Optional[int] = None
    contract_salary: Optional[float] = None


class StandingIn(SQLModel):
    id: Optional[int] = None
    team_id: Optional[int] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    ties: Optional[int] = None
    division_name: Optional[str] = None
    seed: Optional[int] = None
    season_type: Optional[str] = None


class ScheduleIn(SQLModel):
    id: Optional[int] = None
    week_number: int
    season_number: int
    home_team_id: Optional[int] = None
    away_team_id: Optional[int] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    is_complete: bool = False
    season_type: Optional[str] = None


class PlayerStatsIn(SQLModel):
    id: Optional[int] = None
    player_id: Optional[int] = None
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
