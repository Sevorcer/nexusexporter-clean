import os
import json
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

    def test_rosters_endpoint_upserts_players_on_reexport(self):
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
        self.assertEqual(second_response.json()["cleared"], 0)
        self.assertEqual(second_response.json()["inserted"], 2)

        with Session(main.engine) as session:
            players = session.exec(select(main.Player).where(main.Player.league_id == 1)).all()
            self.assertEqual(len(players), 3)
            names = sorted(f"{p.first_name} {p.last_name}" for p in players)
            self.assertEqual(names, ["Chris Jones", "Patrick Mahomes", "Travis Kelce"])

    def test_rosters_endpoint_does_not_clear_player_stats(self):
        self.create_league(api_key="abc123")
        with Session(main.engine) as session:
            session.add(main.PlayerStats(league_id=1, player_id=1, week_number=1, season_number=1, pass_yards=321))
            session.commit()

        first_response = self.client.post("/api/1/rosters?key=abc123", json=[{"id": 1, "first_name": "Pat"}])
        self.assertEqual(first_response.status_code, 200)
        second_response = self.client.post("/api/1/rosters?key=abc123", json=[{"id": 2, "first_name": "Travis"}])
        self.assertEqual(second_response.status_code, 200)

        with Session(main.engine) as session:
            stats = session.exec(select(main.PlayerStats).where(main.PlayerStats.league_id == 1)).all()
            self.assertEqual(len(stats), 1)
            self.assertEqual(stats[0].pass_yards, 321)

    def test_rosters_reexport_same_ids_does_not_raise(self):
        """Re-posting the same player IDs must succeed with no UniqueViolation."""
        self.create_league(api_key="upsert-key")
        payload = [{"id": 553386506, "first_name": "Pat", "last_name": "Mahomes", "overall_rating": 99}]
        first = self.client.post("/api/1/rosters?key=upsert-key", json=payload)
        self.assertEqual(first.status_code, 200)
        second = self.client.post("/api/1/rosters?key=upsert-key", json=payload)
        self.assertEqual(second.status_code, 200)
        with Session(main.engine) as session:
            players = session.exec(select(main.Player).where(main.Player.league_id == 1)).all()
            self.assertEqual(len(players), 1)
            self.assertEqual(players[0].id, 553386506)

    def test_rosters_reexport_updates_player_fields(self):
        """Re-posting a player with updated data must update the existing row."""
        self.create_league(api_key="upsert-key")
        self.client.post("/api/1/rosters?key=upsert-key", json=[{"id": 1, "first_name": "Pat", "overall_rating": 99}])
        self.client.post("/api/1/rosters?key=upsert-key", json=[{"id": 1, "first_name": "Pat", "overall_rating": 85}])
        with Session(main.engine) as session:
            player = session.get(main.Player, 1)
            self.assertIsNotNone(player)
            self.assertEqual(player.overall_rating, 85)

    def test_schedules_reexport_same_ids_does_not_raise(self):
        """Re-posting the same schedule IDs must succeed with no UniqueViolation."""
        self.create_league(api_key="upsert-key")
        payload = [{"id": 700, "week_number": 1, "season_number": 1, "home_team_id": 10, "away_team_id": 20}]
        first = self.client.post("/api/1/schedules?key=upsert-key", json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["cleared"], 0)
        self.assertEqual(first.json()["inserted"], 1)
        second = self.client.post("/api/1/schedules?key=upsert-key", json=payload)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["cleared"], 0)
        with Session(main.engine) as session:
            schedules = session.exec(select(main.Schedule).where(main.Schedule.league_id == 1)).all()
            self.assertEqual(len(schedules), 1)
            self.assertEqual(schedules[0].id, 700)

    def test_schedules_reexport_updates_schedule_fields(self):
        """Re-posting a schedule with updated data must update the existing row."""
        self.create_league(api_key="upsert-key")
        self.client.post("/api/1/schedules?key=upsert-key", json=[{"id": 700, "week_number": 1, "season_number": 1, "is_complete": False}])
        self.client.post("/api/1/schedules?key=upsert-key", json=[{"id": 700, "week_number": 1, "season_number": 1, "is_complete": True}])
        with Session(main.engine) as session:
            schedule = session.get(main.Schedule, 700)
            self.assertIsNotNone(schedule)
            self.assertTrue(schedule.is_complete)

    def test_standings_reexport_same_ids_does_not_raise(self):
        """Re-posting standings with the same IDs must succeed with no UniqueViolation."""
        self.create_league(api_key="upsert-key")
        payload = [{"id": 1, "team_id": 10, "wins": 5, "losses": 3}]
        first = self.client.post("/api/1/standings?key=upsert-key", json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["cleared"], 0)
        self.assertEqual(first.json()["inserted"], 1)
        second = self.client.post("/api/1/standings?key=upsert-key", json=payload)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["cleared"], 0)
        with Session(main.engine) as session:
            standings = session.exec(select(main.Standing).where(main.Standing.league_id == 1)).all()
            self.assertEqual(len(standings), 1)
            self.assertEqual(standings[0].id, 1)

    def test_standings_reexport_updates_standing_fields(self):
        """Re-posting a standing with updated data must update the existing row."""
        self.create_league(api_key="upsert-key")
        self.client.post("/api/1/standings?key=upsert-key", json=[{"id": 1, "team_id": 10, "wins": 5}])
        self.client.post("/api/1/standings?key=upsert-key", json=[{"id": 1, "team_id": 10, "wins": 9}])
        with Session(main.engine) as session:
            standing = session.get(main.Standing, 1)
            self.assertIsNotNone(standing)
            self.assertEqual(standing.wins, 9)

    def test_teams_reexport_same_ids_does_not_raise(self):
        """Re-posting the same team IDs must succeed with no UniqueViolation."""
        self.create_league(api_key="upsert-key")
        payload = [{"id": 10, "team_name": "Lions"}]
        first = self.client.post("/api/1/teams?key=upsert-key", json=payload)
        self.assertEqual(first.status_code, 200)
        second = self.client.post("/api/1/teams?key=upsert-key", json=payload)
        self.assertEqual(second.status_code, 200)
        with Session(main.engine) as session:
            teams = session.exec(select(main.Team).where(main.Team.league_id == 1)).all()
            self.assertEqual(len(teams), 1)
            self.assertEqual(teams[0].id, 10)

    def test_teams_reexport_updates_team_fields(self):
        """Re-posting a team with updated data must update the existing row."""
        self.create_league(api_key="upsert-key")
        self.client.post("/api/1/teams?key=upsert-key", json=[{"id": 10, "team_name": "Lions", "wins": 5}])
        self.client.post("/api/1/teams?key=upsert-key", json=[{"id": 10, "team_name": "Lions", "wins": 9}])
        with Session(main.engine) as session:
            team = session.get(main.Team, 10)
            self.assertIsNotNone(team)
            self.assertEqual(team.wins, 9)

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

    def test_playerstats_player_id_has_no_foreign_key(self):
        self.assertEqual(len(main.PlayerStats.__table__.c.player_id.foreign_keys), 0)

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

        receiving_response = self.client.post(
            "/xbsx/22006264/week/reg/1/receiving",
            json={
                "content": {
                    "playerReceivingStatInfoList": [
                        {
                            "statId": 9004,
                            "rosterId": 100,
                            "teamId": 10,
                            "scheduleId": 700,
                            "seasonIndex": 1,
                            "stageIndex": 1,
                            "weekIndex": 1,
                            "fullName": "Jared Goff",
                            "recYds": 12,
                            "recTDs": 1,
                            "recCatches": 2,
                        }
                    ]
                }
            },
        )
        self.assertEqual(receiving_response.status_code, 200)
        self.assertEqual(receiving_response.json()["updated"], 1)

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
            self.assertEqual(schedules[0].week_number, 2)
            self.assertTrue(schedules[0].is_complete)

            stats = session.exec(select(main.PlayerStats).where(main.PlayerStats.league_id == 1)).all()
            self.assertEqual(len(stats), 1)
            self.assertEqual(stats[0].week_number, 2)
            self.assertEqual(stats[0].pass_yards, 312)
            self.assertEqual(stats[0].pass_tds, 2)
            self.assertEqual(stats[0].interceptions, 1)
            self.assertEqual(stats[0].rush_yards, 16)
            self.assertEqual(stats[0].rush_tds, 1)
            self.assertEqual(stats[0].sacks, 1)
            self.assertEqual(stats[0].defensive_ints, 2)
            self.assertEqual(stats[0].tackles, 5)
            self.assertEqual(stats[0].rec_yards, 12)
            self.assertEqual(stats[0].rec_tds, 1)
            self.assertEqual(stats[0].receptions, 2)

    def test_companion_roster_route_maps_integer_dev_trait(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
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
                            "devTrait": 2,
                        }
                    ]
                }
            },
        )
        self.assertEqual(roster_response.status_code, 200)
        self.assertEqual(roster_response.json()["inserted"], 1)

        with Session(main.engine) as session:
            player = session.get(main.Player, 100)
            self.assertIsNotNone(player)
            self.assertEqual(player.dev_trait, "Superstar")

    def test_transform_madden_schedule_handles_numeric_status_and_week_sources(self):
        schedule_from_week_index = main._transform_madden_schedule(
            [{"id": 1, "weekIndex": 2, "seasonIndex": 1, "status": 2}]
        )[0]
        self.assertEqual(schedule_from_week_index.week_number, 3)
        self.assertTrue(schedule_from_week_index.is_complete)

        schedule_from_week_number = main._transform_madden_schedule(
            [{"id": 2, "week_number": 2, "season_number": 1, "status": "scheduled"}]
        )[0]
        self.assertEqual(schedule_from_week_number.week_number, 2)
        self.assertFalse(schedule_from_week_number.is_complete)

    def test_transform_madden_stats_week_index_is_zero_based_only(self):
        stat_from_week_index = main._transform_madden_stats(
            [{"rosterId": 100, "weekIndex": 2, "seasonIndex": 1, "passYds": 123}],
            "passing",
        )[0]
        self.assertEqual(stat_from_week_index.week_number, 3)

        stat_from_week_number = main._transform_madden_stats(
            [{"player_id": 100, "week_number": 2, "season_number": 1, "pass_yards": 123}],
            "passing",
        )[0]
        self.assertEqual(stat_from_week_number.week_number, 2)

    def test_companion_team_roster_upserts_new_players_for_team(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
        self.client.post(
            "/api/1/rosters?key=companion-key",
            json=[
                {"id": 100, "team_id": 10, "first_name": "Old", "last_name": "Lion"},
                {"id": 200, "team_id": 20, "first_name": "Bear", "last_name": "Player"},
                {"id": 300, "team_id": None, "first_name": "Free", "last_name": "Agent"},
            ],
        )

        roster_response = self.client.post(
            "/xbsx/22006264/team/10/roster",
            json={"rosterInfoList": [{"rosterId": 101, "teamId": 10, "firstName": "New", "lastName": "Lion"}]},
        )
        self.assertEqual(roster_response.status_code, 200)
        self.assertEqual(roster_response.json()["cleared"], 0)
        self.assertEqual(roster_response.json()["inserted"], 1)

        with Session(main.engine) as session:
            players = session.exec(select(main.Player).where(main.Player.league_id == 1)).all()
            by_id = {player.id: player for player in players}
            self.assertEqual(len(players), 4)
            self.assertEqual(by_id[100].team_id, 10)
            self.assertEqual(by_id[101].team_id, 10)
            self.assertEqual(by_id[200].team_id, 20)
            self.assertIsNone(by_id[300].team_id)

    def test_companion_free_agents_roster_upserts_new_free_agents(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
        self.client.post(
            "/api/1/rosters?key=companion-key",
            json=[
                {"id": 100, "team_id": 10, "first_name": "Rostered", "last_name": "Player"},
                {"id": 300, "team_id": None, "first_name": "Old", "last_name": "FreeAgentA"},
                {"id": 301, "team_id": None, "first_name": "Old", "last_name": "FreeAgentB"},
            ],
        )

        roster_response = self.client.post(
            "/xbsx/22006264/freeagents/roster",
            json={"rosterInfoList": [{"rosterId": 302, "teamId": None, "firstName": "New", "lastName": "FreeAgent"}]},
        )
        self.assertEqual(roster_response.status_code, 200)
        self.assertEqual(roster_response.json()["cleared"], 0)
        self.assertEqual(roster_response.json()["inserted"], 1)

        with Session(main.engine) as session:
            players = session.exec(select(main.Player).where(main.Player.league_id == 1)).all()
            by_id = {player.id: player for player in players}
            self.assertEqual(len(players), 4)
            self.assertEqual(by_id[100].team_id, 10)
            self.assertIsNone(by_id[300].team_id)
            self.assertIsNone(by_id[301].team_id)
            self.assertIsNone(by_id[302].team_id)

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

    def test_companion_route_accepts_json_body_with_non_json_content_type(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
        payload = {"content": {"teamStandingInfoList": [{"teamId": 10, "totalWins": 9, "totalLosses": 3, "totalTies": 0}]}}
        response = self.client.post(
            "/xbsx/22006264/standings",
            content=json.dumps(payload),
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["inserted"], 1)

    def test_companion_route_accepts_form_encoded_payload_field(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
        form_payload = {"content": {"teamStandingInfoList": [{"teamId": 10, "totalWins": 8, "totalLosses": 4, "totalTies": 0}]}}
        response = self.client.post(
            "/xbsx/22006264/standings",
            data={"payload": json.dumps(form_payload)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["inserted"], 1)

    def test_companion_route_422_returns_actionable_parse_detail(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
        raw_body = b"\xff\xfe\xfd"
        response = self.client.post(
            "/xbsx/22006264/standings",
            content=raw_body,
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertEqual(detail["error"], "Unable to parse companion payload")
        self.assertEqual(detail["content_type"], "application/octet-stream")
        self.assertTrue(detail["body_preview"])
        self.assertEqual(detail["raw_body_bytes"], len(raw_body))
        self.assertIn("Expected JSON body or form-encoded data", detail["hint"])

    def test_companion_route_422_preview_is_truncated(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
        oversized_body = b"\xff" * (main.COMPANION_DEBUG_PREVIEW_LIMIT + 25)
        response = self.client.post(
            "/xbsx/22006264/standings",
            content=oversized_body,
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertEqual(detail["raw_body_bytes"], len(oversized_body))
        preview_prefix = detail["body_preview"].split("... (truncated", 1)[0]
        self.assertEqual(len(preview_prefix), main.COMPANION_DEBUG_PREVIEW_LIMIT)
        self.assertIn("... (truncated", detail["body_preview"])
        self.assertIn(f"total_bytes={len(oversized_body)}", detail["body_preview"])

    def test_companion_debug_logging_is_opt_in(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
        with patch.object(main, "COMPANION_DEBUG_LOG_ENABLED", False), patch.object(main.companion_logger, "info") as mock_info:
            response = self.client.post(
                "/xbsx/22006264/standings",
                json={"content": {"teamStandingInfoList": [{"teamId": 10, "totalWins": 8, "totalLosses": 4, "totalTies": 0}]}},
            )
            self.assertEqual(response.status_code, 200)
            self.assertFalse(mock_info.called)

    def test_companion_debug_logging_reports_parse_failures(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
        with patch.object(main, "COMPANION_DEBUG_LOG_ENABLED", True), patch.object(main.companion_logger, "info") as mock_info:
            response = self.client.post(
                "/xbsx/22006264/standings",
                content=b"\xff\xfe\xfd",
                headers={"Content-Type": "application/octet-stream"},
            )
            self.assertEqual(response.status_code, 422)
            messages = [call.args[0] for call in mock_info.call_args_list]
            self.assertTrue(any("Companion ingest request" in message for message in messages))
            self.assertTrue(any("Companion ingest parse failed for json:" in message for message in messages))
            self.assertTrue(any("no parseable JSON, querystring, or form payload found" in message for message in messages))

    def test_companion_leagueteams_transforms_and_ingests_teams(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
        response = self.client.post(
            "/xbsx/22006264/leagueteams",
            json={
                "content": {
                    "leagueTeamInfoList": [
                        {
                            "teamId": 10,
                            "teamName": "Lions",
                            "divisionName": "NFC North",
                            "teamOvr": 84,
                            "totalWins": 9,
                            "totalLosses": 2,
                            "totalTies": 1,
                        }
                    ]
                }
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["inserted"], 1)
        with Session(main.engine) as session:
            team = session.get(main.Team, 10)
            self.assertIsNotNone(team)
            self.assertEqual(team.team_name, "Lions")
            self.assertEqual(team.overall_rating, 84)

    def test_companion_route_accepts_root_level_standings_list(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
        response = self.client.post(
            "/xbsx/22006264/standings",
            json={
                "message": "",
                "success": True,
                "teamStandingInfoList": [
                    {"teamId": 10, "teamName": "Lions", "divisionName": "NFC North", "totalWins": 9, "totalLosses": 3, "totalTies": 0}
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["inserted"], 1)

    def test_companion_leagueteams_accepts_root_level_list(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
        response = self.client.post(
            "/xbsx/22006264/leagueteams",
            json={
                "leagueTeamInfoList": [
                    {
                        "teamId": 10,
                        "teamName": "Lions",
                        "divisionName": "NFC North",
                        "teamOvr": 84,
                        "totalWins": 9,
                        "totalLosses": 2,
                        "totalTies": 1,
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["inserted"], 1)
        with Session(main.engine) as session:
            team = session.get(main.Team, 10)
            self.assertIsNotNone(team)
            self.assertEqual(team.team_name, "Lions")
            self.assertEqual(team.overall_rating, 84)

    def test_companion_untracked_weekly_stat_types_acknowledge_success(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
        response = self.client.post(
            "/xbsx/22006264/week/reg/3/kicking",
            json=[{"some": "stat"}],
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertFalse(response.json()["tracked"])

    def test_companion_untracked_root_infolists_return_not_tracked_ok(self):
        self.create_league(api_key="companion-key", madden_league_id="22006264")
        cases = [
            ("/xbsx/22006264/week/reg/3/kicking", "playerKickingStatInfoList"),
            ("/xbsx/22006264/week/reg/3/punting", "playerPuntingStatInfoList"),
            ("/xbsx/22006264/week/reg/3/team", "teamStatInfoList"),
        ]
        for path, key in cases:
            with self.subTest(path=path, key=key):
                response = self.client.post(path, json={key: [{"playerId": 1}]})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(),
                    {"status": "ok", "tracked": False, "message": "Stat type not currently tracked"},
                )

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
