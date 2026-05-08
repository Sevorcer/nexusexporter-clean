from collections import defaultdict
from typing import Any, DefaultDict, Dict, List, Optional

from sqlmodel import Session, select

from app.models import Player, PlayerStats, Team


def build_stat_leaders(
    session: Session,
    league_id: int,
    season_number: Optional[int] = None,
    limit: int = 10,
) -> Dict[str, List[Dict[str, Any]]]:
    stats_query = select(PlayerStats).where(PlayerStats.league_id == league_id)
    if season_number is not None:
        stats_query = stats_query.where(PlayerStats.season_number == season_number)
    stats_rows = session.exec(stats_query).all()

    aggregates: DefaultDict[int, Dict[str, int]] = defaultdict(
        lambda: {
            "pass_yards": 0,
            "rush_yards": 0,
            "rec_yards": 0,
            "total_tds": 0,
            "sacks": 0,
            "ints": 0,
        }
    )
    for row in stats_rows:
        if row.player_id is None:
            continue
        values = aggregates[row.player_id]
        values["pass_yards"] += row.pass_yards or 0
        values["rush_yards"] += row.rush_yards or 0
        values["rec_yards"] += row.rec_yards or 0
        values["total_tds"] += (row.pass_tds or 0) + (row.rush_tds or 0) + (row.rec_tds or 0)
        values["sacks"] += row.sacks or 0
        values["ints"] += row.defensive_ints or 0

    players = session.exec(select(Player).where(Player.league_id == league_id)).all()
    teams = session.exec(select(Team).where(Team.league_id == league_id)).all()
    player_map = {p.id: p for p in players if p.id is not None}
    team_map = {t.id: t for t in teams if t.id is not None}

    def leader_list(metric: str) -> List[Dict[str, Any]]:
        sorted_items = sorted(
            aggregates.items(), key=lambda item: item[1][metric], reverse=True
        )[:limit]
        results: List[Dict[str, Any]] = []
        for player_id, values in sorted_items:
            player = player_map.get(player_id)
            if player is None:
                continue
            team = team_map.get(player.team_id)
            results.append(
                {
                    "player_id": player_id,
                    "player_name": f"{player.first_name or ''} {player.last_name or ''}".strip(),
                    "position": player.position,
                    "team_id": player.team_id,
                    "team_name": team.team_name if team else None,
                    "value": values[metric],
                }
            )
        return results

    return {
        "pass_yards": leader_list("pass_yards"),
        "rush_yards": leader_list("rush_yards"),
        "rec_yards": leader_list("rec_yards"),
        "total_tds": leader_list("total_tds"),
        "sacks": leader_list("sacks"),
        "ints": leader_list("ints"),
    }
