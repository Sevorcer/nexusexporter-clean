import os
import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlmodel import Session, select

DB_FILE = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
DB_FILE.close()
os.environ["DATABASE_URL"] = f"sqlite:///{DB_FILE.name}"
os.environ.setdefault("DISCORD_CLIENT_ID", "test-client-id")
os.environ.setdefault("DISCORD_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("DISCORD_REDIRECT_URI", "http://localhost/oauth-callback")

import main  # noqa: E402


class ApiIngestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        with Session(main.engine) as session:
            for model in [main.PlayerStats, main.Standing, main.Schedule, main.Player, main.Team, main.League, main.User]:
                for row in session.exec(select(model)).all():
                    session.delete(row)
            session.commit()

    def create_league(self, league_id: int = 1, api_key: str = "valid-key"):
        with Session(main.engine) as session:
            league = main.League(id=league_id, name="Test League", api_key=api_key, user_id=None)
            session.add(league)
            session.commit()

    def test_invalid_api_key_returns_403(self):
        self.create_league()
        response = self.client.post("/api/1/teams?key=wrong-key", json=[])
        self.assertEqual(response.status_code, 403)

    def test_rosters_endpoint_replaces_previous_league_players(self):
        self.create_league(api_key="abc123")
        first_payload = [
            {"id": 1, "first_name": "Patrick", "last_name": "Mahomes", "position": "QB", "overall_rating": 99}
        ]
        second_payload = [
            {"id": 2, "first_name": "Travis", "last_name": "Kelce", "position": "TE", "overall_rating": 98},
            {"id": 3, "first_name": "Chris", "last_name": "Jones", "position": "DT", "overall_rating": 95},
        ]

        first_response = self.client.post("/api/1/rosters?key=abc123", json=first_payload)
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(first_response.json()["inserted"], 1)

        second_response = self.client.post("/api/1/rosters?key=abc123", json=second_payload)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.json()["cleared"], 1)
        self.assertEqual(second_response.json()["inserted"], 2)

        with Session(main.engine) as session:
            players = session.exec(select(main.Player).where(main.Player.league_id == 1)).all()
            self.assertEqual(len(players), 2)
            names = sorted(f"{p.first_name} {p.last_name}" for p in players)
            self.assertEqual(names, ["Chris Jones", "Travis Kelce"])

    def test_stats_endpoint_clears_only_matching_week_and_season(self):
        self.create_league(api_key="stats-key")
        with Session(main.engine) as session:
            session.add(main.PlayerStats(league_id=1, week_number=1, season_number=1, pass_yards=150))
            session.add(main.PlayerStats(league_id=1, week_number=1, season_number=1, pass_yards=250))
            session.add(main.PlayerStats(league_id=1, week_number=2, season_number=1, pass_yards=350))
            session.commit()

        response = self.client.post(
            "/api/1/stats?key=stats-key",
            json=[{"week_number": 1, "season_number": 1, "pass_yards": 410}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cleared"], 2)
        self.assertEqual(response.json()["inserted"], 1)

        with Session(main.engine) as session:
            week_1 = session.exec(
                select(main.PlayerStats).where(
                    main.PlayerStats.league_id == 1,
                    main.PlayerStats.week_number == 1,
                    main.PlayerStats.season_number == 1,
                )
            ).all()
            week_2 = session.exec(
                select(main.PlayerStats).where(
                    main.PlayerStats.league_id == 1,
                    main.PlayerStats.week_number == 2,
                    main.PlayerStats.season_number == 1,
                )
            ).all()
            self.assertEqual(len(week_1), 1)
            self.assertEqual(week_1[0].pass_yards, 410)
            self.assertEqual(len(week_2), 1)
            self.assertEqual(week_2[0].pass_yards, 350)


if __name__ == "__main__":
    unittest.main()
