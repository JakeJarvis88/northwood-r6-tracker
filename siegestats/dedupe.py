"""Duplicate / re-upload detection.

A candidate map (from a new screenshot) is compared against every stored map:

1. Same Siege Match ID            -> definitely the same map ("exact").
2. Same map + same opponent + date within 1 day + >=3 shared gamertags
                                   -> probably the same map ("possible").

Nothing is ever overwritten automatically: the UI shows the conflict and the
user chooses "update existing" (e.g. an in-progress shot followed by the final
board) or "save as new map" or "skip".
"""
from datetime import datetime, timedelta

from .matching import normalize


def _tags(conn, map_game_id):
    rows = conn.execute("SELECT gamertag_displayed FROM player_map_stats WHERE map_game_id=?",
                        (map_game_id,)).fetchall()
    return {normalize(r["gamertag_displayed"]) for r in rows}


def find_candidates(conn, siege_match_id, map_name, opponent_id, date_iso, gamertags):
    """Return list of dicts {map_game_id, kind, reason, existing} sorted exact-first."""
    out = []
    rows = conn.execute(
        """SELECT mp.*, s.date, s.opponent_id, t.name AS opponent
           FROM maps_played mp JOIN series s USING(series_id)
           LEFT JOIN teams t ON s.opponent_id = t.team_id""").fetchall()
    new_tags = {normalize(g) for g in gamertags if g}
    for r in rows:
        if siege_match_id and r["siege_match_id"] and \
                siege_match_id.strip().lower() == r["siege_match_id"].strip().lower():
            out.append({"map_game_id": r["map_game_id"], "kind": "exact",
                        "reason": f"Same Siege Match ID as {r['map_name']} vs {r['opponent']} on {r['date']}",
                        "existing": dict(r)})
            continue
        same_map = map_name and r["map_name"] and map_name.strip().lower() == r["map_name"].strip().lower()
        same_opp = opponent_id and r["opponent_id"] == opponent_id
        close_date = False
        try:
            d1 = datetime.fromisoformat(str(date_iso)[:10])
            d2 = datetime.fromisoformat(str(r["date"])[:10])
            close_date = abs(d1 - d2) <= timedelta(days=1)
        except (ValueError, TypeError):
            pass
        overlap = len(new_tags & _tags(conn, r["map_game_id"]))
        if same_map and same_opp and close_date and overlap >= 3:
            out.append({"map_game_id": r["map_game_id"], "kind": "possible",
                        "reason": (f"Same map ({r['map_name']}), same opponent, same date, "
                                   f"{overlap} shared gamertags"),
                        "existing": dict(r)})
    out.sort(key=lambda x: 0 if x["kind"] == "exact" else 1)
    return out


def is_more_complete(candidate_rounds_total, existing_row):
    """True if the new screenshot looks like a more complete/final version."""
    existing_total = (existing_row.get("rounds_won") or 0) + (existing_row.get("rounds_lost") or 0)
    if existing_row.get("import_status") == "in_progress":
        return True
    return candidate_rounds_total >= existing_total
