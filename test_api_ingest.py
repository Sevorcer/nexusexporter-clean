import os
import tempfile
import unittest
from unittest.mock import patch

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

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(DB_FILE.name):
            os.unlink(DB_FILE.name)

    def setUp(self):
        with Session(main.engine) as session:
            for model in [main.PlayerStats, main.Standing, main.Schedule, main.Player, main.Team, main.League, main.User]:
                for row in session.exec(select(model)).all():
                    session.delete(row)
            session.commit()

    def create_user(self, discord_id: str, username: str) -> int:
        with Session(main.engine) as session:
            user = main.User(discord_id=discord_id, username=username)
            session.add(user)
            session.commit()
            session.refresh(user)
            return user.id

    def create_league(
        self,
        league_id: int = 1,
        api_key: str = "valid-key",
        madden_league_id: str | None = None,
        user_id: int | None = None,
    ):
        with Session(main.engine) as session:
            league = main.League(
                id=league_id,
                name="Test League",
                api_key=api_key,
                madden_league_id=madden_league_id,
                user_id=user_id,
            )
            session.add(league)
            session.commit()
            session.refresh(league)
            return league.id

    def seed_sample_league_data(self):
        self.create_league(api_key="sample-key")
        self.client.post(
            "/api/1/teams?key=sample-key",
            json=[
                {"id": 10, "team_name": "Lions"},
                {"id": 20, "team_name": "Bears"},
            ],
        )
        self.client.post(
            "/api/1/rosters?key=sample-key",
            json=[
                {"id": 100, "team_id": 10, "first_name": "Jared", "last_name": "Goff", "position": "QB"},
                {"id": 101, "team_id": 20, "first_name": "DJ", "last_name": "Moore", "position": "WR"},
            ],
        )
        self.client.post(
            "/api/1/standings?key=sample-key",
            json=[
                {"team_id": 20, "wins": 8, "losses": 2, "ties": 0},
                {"team_id": 10, "wins": 6, "losses": 4, "ties": 0},
            ],
        )
        self.client.post(
            "/api/1/schedules?key=sample-key",
            json=[
                {"week_number": 1, "season_number": 1, "home_team_id": 10, "away_team_id": 20, "home_score": 21, "away_score": 24, "is_complete": True},
                {"week_number": 2, "season_number": 1, "home_team_id": 20, "away_team_id": 10, "is_complete": False},
            ],
        )
        self.client.post(
            "/api/1/stats?key=sample-key",
            json=[
                {"player_id": 100, "week_number": 1, "season_number": 1, "pass_yards": 300, "pass_tds": 2, "rush_tds": 0, "rec_tds": 0},
                {"player_id": 100, "week_number": 2, "season_number": 1, "pass_yards": 250, "pass_tds": 1, "rush_tds": 0, "rec_tds": 0},
                {"player_id": 101, "week_number": 1, "season_number": 1, "rec_yards": 125, "rec_tds": 1, "sacks": 0, "defensive_ints": 0},
            ],
        )

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

    def test_get_endpoints_require_valid_api_key(self):
        self.create_league(api_key="valid-key")
        response = self.client.get("/api/1/teams?key=bad-key")
        self.assertEqual(response.status_code, 403)

    def test_get_rosters_and_stats_support_filters(self):
        self.seed_sample_league_data()

        roster_response = self.client.get("/api/1/rosters?key=sample-key&team_id=10")
        self.assertEqual(roster_response.status_code, 200)
        roster = roster_response.json()
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster[0]["id"], 100)

        stats_response = self.client.get("/api/1/stats?key=sample-key&player_id=100&week_number=2&season_number=1")
        self.assertEqual(stats_response.status_code, 200)
        stats = stats_response.json()
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["pass_yards"], 250)

    def test_get_schedules_filters_by_week_and_season(self):
        self.seed_sample_league_data()
        response = self.client.get("/api/1/schedules?key=sample-key&week_number=1&season_number=1")
        self.assertEqual(response.status_code, 200)
        schedules = response.json()
        self.assertEqual(len(schedules), 1)
        self.assertTrue(schedules[0]["is_complete"])

    def test_stat_leaders_returns_ranked_categories(self):
        self.seed_sample_league_data()
        response = self.client.get("/api/1/stat_leaders?key=sample-key&season_number=1&limit=1")
        self.assertEqual(response.status_code, 200)
        leaders = response.json()
        self.assertEqual(leaders["pass_yards"][0]["player_id"], 100)
        self.assertEqual(leaders["rec_yards"][0]["player_id"], 101)
        self.assertEqual(len(leaders["pass_yards"]), 1)

    def test_get_standings_sorted_by_wins_desc(self):
        self.seed_sample_league_data()
        response = self.client.get("/api/1/standings?key=sample-key")
        self.assertEqual(response.status_code, 200)
        standings = response.json()
        self.assertEqual(standings[0]["team_id"], 20)
        self.assertEqual(standings[1]["team_id"], 10)

    def test_companion_routes_map_to_existing_ingest_logic(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")

        teams_response = self.client.post(
            "/xbsx/22006264/teams",
            json=[{"id": 10, "team_name": "Lions"}],
        )
        self.assertEqual(teams_response.status_code, 200)
        self.assertEqual(teams_response.json()["inserted"], 1)

        roster_response = self.client.post(
            "/xbsx/22006264/freeagents/roster",
            json=[{"id": 100, "team_id": 10, "first_name": "A", "last_name": "B", "position": "QB"}],
        )
        self.assertEqual(roster_response.status_code, 200)
        self.assertEqual(roster_response.json()["inserted"], 1)

        standings_response = self.client.post(
            "/xbsx/22006264/standings",
            json=[{"team_id": 10, "wins": 10, "losses": 2, "ties": 0}],
        )
        self.assertEqual(standings_response.status_code, 200)
        self.assertEqual(standings_response.json()["inserted"], 1)

        schedules_response = self.client.post(
            "/xbsx/22006264/schedules",
            json=[{"week_number": 1, "season_number": 1, "home_team_id": 10, "away_team_id": 10}],
        )
        self.assertEqual(schedules_response.status_code, 200)
        self.assertEqual(schedules_response.json()["inserted"], 1)

        stats_response = self.client.post(
            "/xbsx/22006264/week/reg/1/defense",
            json=[{"player_id": 100, "week_number": 1, "season_number": 1, "sacks": 2}],
        )
        self.assertEqual(stats_response.status_code, 200)
        self.assertEqual(stats_response.json()["inserted"], 1)

        with Session(main.engine) as session:
            self.assertEqual(
                len(session.exec(select(main.Team).where(main.Team.league_id == 1)).all()),
                1,
            )
            self.assertEqual(
                len(session.exec(select(main.Player).where(main.Player.league_id == 1)).all()),
                1,
            )
            self.assertEqual(
                len(session.exec(select(main.Standing).where(main.Standing.league_id == 1)).all()),
                1,
            )
            self.assertEqual(
                len(session.exec(select(main.Schedule).where(main.Schedule.league_id == 1)).all()),
                1,
            )
            self.assertEqual(
                len(session.exec(select(main.PlayerStats).where(main.PlayerStats.league_id == 1)).all()),
                1,
            )

    def test_companion_routes_transform_madden_payloads_and_merge_weekly_stats(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")

        standings_response = self.client.post(
            "/xbsx/22006264/standings",
            json={
                "content": {
                    "teamStandingInfoList": [
                        {
                            "teamId": 10,
                            "teamName": "Lions",
                            "conferenceName": "NFC",
                            "divisionName": "NFC North",
                            "teamOvr": 84,
                            "totalWins": 9,
                            "totalLosses": 2,
                            "totalTies": 1,
                            "seed": 1,
                        }
                    ]
                }
            },
        )
        self.assertEqual(standings_response.status_code, 200)
        self.assertEqual(standings_response.json()["inserted"], 1)

        roster_response = self.client.post(
            "/xbsx/22006264/freeagents/roster",
            json={
                "content": {
                    "rosterInfoList": [
                        {
                            "rosterId": 100,
                            "teamId": 10,
                            "firstName": "Jared",
                            "lastName": "Goff",
                            "position": "QB",
                            "age": 30,
                            "playerSchemeOvr": 85,
                            "jerseyNum": 16,
                            "contractSalary": 45000000,
                            "contractYearsLeft": 2,
                            "devTraitLabel": "Star",
                            "signatureSlotList": ["set-feet-lead"],
                        }
                    ]
                }
            },
        )
        self.assertEqual(roster_response.status_code, 200)
        self.assertEqual(roster_response.json()["inserted"], 1)

        schedule_response = self.client.post(
            "/xbsx/22006264/week/reg/1/team",
            json={
                "content": {
                    "gameScheduleInfoList": [
                        {
                            "scheduleId": 700,
                            "seasonIndex": 1,
                            "stageIndex": 1,
                            "weekIndex": 1,
                            "awayTeamId": 20,
                            "homeTeamId": 10,
                            "awayScore": 17,
                            "homeScore": 24,
                            "status": "Final",
                            "isGameOfTheWeek": True,
                        }
                    ]
                }
            },
        )
        self.assertEqual(schedule_response.status_code, 200)
        self.assertEqual(schedule_response.json()["inserted"], 1)

        passing_response = self.client.post(
            "/xbsx/22006264/week/reg/1/passing",
            json={
                "content": {
                    "playerPassingStatInfoList": [
                        {
                            "statId": 9001,
                            "rosterId": 100,
                            "teamId": 10,
                            "scheduleId": 700,
                            "seasonIndex": 1,
                            "stageIndex": 1,
                            "weekIndex": 1,
                            "fullName": "Jared Goff",
                            "passAtt": 30,
                            "passComp": 22,
                            "passInts": 1,
                            "passTDs": 2,
                            "passYds": 312,
                        }
                    ]
                }
            },
        )
        self.assertEqual(passing_response.status_code, 200)
        self.assertEqual(passing_response.json()["inserted"], 1)

        rushing_response = self.client.post(
            "/xbsx/22006264/week/reg/1/rushing",
            json={
                "content": {
                    "playerRushingStatInfoList": [
                        {
                            "statId": 9002,
                            "rosterId": 100,
                            "teamId": 10,
                            "scheduleId": 700,
                            "seasonIndex": 1,
                            "stageIndex": 1,
                            "weekIndex": 1,
                            "fullName": "Jared Goff",
                            "rushAtt": 3,
                            "rushTDs": 1,
                            "rushYds": 16,
                        }
                    ]
                }
            },
        )
        self.assertEqual(rushing_response.status_code, 200)
        self.assertEqual(rushing_response.json()["updated"], 1)

        defense_response = self.client.post(
            "/xbsx/22006264/week/reg/1/defense",
            json={
                "content": {
                    "playerDefensiveStatInfoList": [
                        {
                            "statId": 9003,
                            "rosterId": 100,
                            "teamId": 10,
                            "scheduleId": 700,
                            "seasonIndex": 1,
                            "stageIndex": 1,
                            "weekIndex": 1,
                            "fullName": "Jared Goff",
                            "defSacks": 1,
                            "defInts": 2,
                            "defTotalTackles": 5,
                            "tacklesForLoss": 1,
                        }
                    ]
                }
            },
        )
        self.assertEqual(defense_response.status_code, 200)
        self.assertEqual(defense_response.json()["updated"], 1)

        with Session(main.engine) as session:
            team = session.get(main.Team, 10)
            self.assertIsNotNone(team)
            self.assertEqual(team.team_name, "Lions")
            self.assertEqual(team.division, "NFC North")
            self.assertEqual(team.overall_rating, 84)
            self.assertEqual(team.wins, 9)

            players = session.exec(select(main.Player).where(main.Player.league_id == 1)).all()
            self.assertEqual(len(players), 1)
            self.assertEqual(players[0].overall_rating, 85)
            self.assertEqual(players[0].dev_trait, "Star")

            schedules = session.exec(select(main.Schedule).where(main.Schedule.league_id == 1)).all()
            self.assertEqual(len(schedules), 1)
            self.assertTrue(schedules[0].is_complete)

            stats = session.exec(select(main.PlayerStats).where(main.PlayerStats.league_id == 1)).all()
            self.assertEqual(len(stats), 1)
            self.assertEqual(stats[0].pass_yards, 312)
            self.assertEqual(stats[0].pass_tds, 2)
            self.assertEqual(stats[0].interceptions, 1)
            self.assertEqual(stats[0].rush_yards, 16)
            self.assertEqual(stats[0].rush_tds, 1)
            self.assertEqual(stats[0].sacks, 1)
            self.assertEqual(stats[0].defensive_ints, 2)
            self.assertEqual(stats[0].tackles, 5)

    def test_companion_payload_type_detection_prefers_content_keys_over_url(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
        response = self.client.post(
            "/xbsx/22006264/standings",
            json={
                "content": {
                    "playerPassingStatInfoList": [
                        {"rosterId": 100, "seasonIndex": 1, "weekIndex": 1, "passYds": 250}
                    ]
                }
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["inserted"], 1)

        with Session(main.engine) as session:
            standings = session.exec(select(main.Standing).where(main.Standing.league_id == 1)).all()
            stats = session.exec(select(main.PlayerStats).where(main.PlayerStats.league_id == 1)).all()
            self.assertEqual(len(standings), 0)
            self.assertEqual(len(stats), 1)

    def test_companion_double_slash_route_is_accepted(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
        response = self.client.post(
            "http://testserver//xbsx/22006264/standings",
            json=[{"team_id": 10, "wins": 9, "losses": 3, "ties": 0}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["inserted"], 1)

    def test_set_madden_id_updates_owned_league(self):
        user_id = self.create_user("owner-1", "owner")
        league_id = self.create_league(user_id=user_id)
        with patch(
            "main.get_current_user",
            return_value=main.User(id=user_id, discord_id="owner-1", username="owner"),
        ):
            response = self.client.post(
                "/set_madden_id",
                data={"league_id": str(league_id), "madden_league_id": "22006264"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")

        with Session(main.engine) as session:
            league = session.get(main.League, league_id)
            self.assertIsNotNone(league)
            self.assertEqual(league.madden_league_id, "22006264")

    def test_set_madden_id_rejects_non_owner(self):
        owner_id = self.create_user("owner-2", "owner")
        other_id = self.create_user("other-2", "other")
        league_id = self.create_league(user_id=owner_id)
        with patch(
            "main.get_current_user",
            return_value=main.User(id=other_id, discord_id="other-2", username="other"),
        ):
            response = self.client.post(
                "/set_madden_id",
                data={"league_id": str(league_id), "madden_league_id": "22006264"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")

        with Session(main.engine) as session:
            league = session.get(main.League, league_id)
            self.assertIsNotNone(league)
            self.assertIsNone(league.madden_league_id)

    def test_set_madden_id_allows_clearing_value(self):
        user_id = self.create_user("owner-3", "owner")
        league_id = self.create_league(user_id=user_id, madden_league_id="22006264")
        with patch(
            "main.get_current_user",
            return_value=main.User(id=user_id, discord_id="owner-3", username="owner"),
        ):
            response = self.client.post(
                "/set_madden_id",
                data={"league_id": str(league_id), "madden_league_id": "   "},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")

        with Session(main.engine) as session:
            league = session.get(main.League, league_id)
            self.assertIsNotNone(league)
            self.assertIsNone(league.madden_league_id)

    def test_set_madden_id_allows_clearing_with_empty_string(self):
        user_id = self.create_user("owner-4", "owner")
        league_id = self.create_league(user_id=user_id, madden_league_id="22006264")
        with patch(
            "main.get_current_user",
            return_value=main.User(id=user_id, discord_id="owner-4", username="owner"),
        ):
            response = self.client.post(
                "/set_madden_id",
                data={"league_id": str(league_id), "madden_league_id": ""},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")

        with Session(main.engine) as session:
            league = session.get(main.League, league_id)
            self.assertIsNotNone(league)
            self.assertIsNone(league.madden_league_id)

    def test_set_madden_id_rejects_over_length_value(self):
        user_id = self.create_user("owner-5", "owner")
        league_id = self.create_league(user_id=user_id, madden_league_id="22006264")
        too_long_madden_id = "1" * (main.MAX_MADDEN_LEAGUE_ID_LENGTH + 1)
        with patch(
            "main.get_current_user",
            return_value=main.User(id=user_id, discord_id="owner-5", username="owner"),
        ):
            response = self.client.post(
                "/set_madden_id",
                data={"league_id": str(league_id), "madden_league_id": too_long_madden_id},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")

        with Session(main.engine) as session:
            league = session.get(main.League, league_id)
            self.assertIsNotNone(league)
            self.assertEqual(league.madden_league_id, "22006264")

    def test_league_detail_page_shows_madden_league_id(self):
        league_id = self.create_league(madden_league_id="22006264")
        response = self.client.get(f"/league/{league_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Madden League ID: 22006264", response.text)

    def test_league_detail_page_shows_not_set_when_missing_madden_league_id(self):
        league_id = self.create_league()
        response = self.client.get(f"/league/{league_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Madden League ID: Not set", response.text)


if __name__ == "__main__":
    unittest.main()
