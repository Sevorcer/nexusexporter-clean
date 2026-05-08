from sqlmodel import Session, SQLModel, create_engine

from app.config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)


def get_session():
    with Session(engine) as session:
        yield session


def create_db():
    SQLModel.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE playerstats DROP CONSTRAINT IF EXISTS playerstats_player_id_fkey"
            )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_playerstats_league_player_week_season "
                "ON playerstats (league_id, player_id, week_number, season_number)"
            )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_team_league_id ON team (league_id, id)"
            )
            connection.exec_driver_sql(
                "ALTER TABLE player DROP CONSTRAINT IF EXISTS player_team_id_fkey"
            )
            connection.exec_driver_sql(
                "ALTER TABLE schedule DROP CONSTRAINT IF EXISTS schedule_home_team_id_fkey"
            )
            connection.exec_driver_sql(
                "ALTER TABLE schedule DROP CONSTRAINT IF EXISTS schedule_away_team_id_fkey"
            )
            connection.exec_driver_sql(
                "ALTER TABLE standing DROP CONSTRAINT IF EXISTS standing_team_id_fkey"
            )
            connection.exec_driver_sql(
                "ALTER TABLE team DROP CONSTRAINT IF EXISTS team_pkey"
            )
            connection.exec_driver_sql(
                "ALTER TABLE team ADD PRIMARY KEY (id, league_id)"
            )
            connection.exec_driver_sql(
                "ALTER TABLE player DROP CONSTRAINT IF EXISTS player_pkey"
            )
            connection.exec_driver_sql(
                "ALTER TABLE player DROP CONSTRAINT IF EXISTS uq_player_league_id"
            )
            connection.exec_driver_sql(
                "DROP INDEX IF EXISTS uq_player_league_id"
            )
            connection.exec_driver_sql(
                "ALTER TABLE player ADD PRIMARY KEY (id, league_id)"
            )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_league_id ON schedule (league_id, id)"
            )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_standing_league_id ON standing (league_id, id)"
            )
            connection.exec_driver_sql(
                "ALTER TABLE playerstats ADD COLUMN IF NOT EXISTS season_type VARCHAR"
            )
            connection.exec_driver_sql(
                "ALTER TABLE standing ADD COLUMN IF NOT EXISTS season_type VARCHAR"
            )
            connection.exec_driver_sql(
                "ALTER TABLE schedule ADD COLUMN IF NOT EXISTS season_type VARCHAR"
            )
