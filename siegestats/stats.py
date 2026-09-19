"""All stat math lives here.

Definitions (documented per the spec):
  K/D          = kills / deaths.  If deaths == 0 the ratio is undefined, so it is
                 displayed as the kill count (i.e. kills / 1) - the common esports
                 convention - and such rows are exact only in the K and D columns.
  KPR          = kills / rounds played
  Survival %   = (rounds played - deaths) / rounds played
  DPR          = deaths / rounds played
  APR          = assists / rounds played
  +/- (K diff) = kills - deaths
Derived stats are always computed from raw K/D/A/rounds at query time and are
never stored, so edits and filters can't leave stale numbers behind.
"""
import pandas as pd


def load_frame(conn) -> pd.DataFrame:
    """One flat row per player per map, with series/team context joined in."""
    q = """
    SELECT pms.id, pms.map_game_id, pms.gamertag_displayed,
           pms.score, pms.kills, pms.deaths, pms.assists, pms.rounds_played,
           pms.team_id, t.name AS team_name, t.is_us,
           pms.player_id, p.name AS player_name,
           mp.series_id, mp.map_number, mp.map_name, mp.result AS map_result,
           mp.rounds_won, mp.rounds_lost, mp.import_status, mp.siege_match_id,
           s.date, s.season, s.competition, s.format, s.match_type,
           opp.name AS opponent
    FROM player_map_stats pms
    LEFT JOIN teams t   ON pms.team_id = t.team_id
    LEFT JOIN players p ON pms.player_id = p.player_id
    JOIN maps_played mp ON pms.map_game_id = mp.map_game_id
    JOIN series s       ON mp.series_id = s.series_id
    LEFT JOIN teams opp ON s.opponent_id = opp.team_id
    """
    df = pd.read_sql_query(q, conn)
    if df.empty:
        return df
    df["display_name"] = df["player_name"].fillna(df["gamertag_displayed"])
    for c in ("score", "kills", "deaths", "assists", "rounds_played"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def apply_filters(df, seasons=None, opponents=None, maps=None, players=None,
                  formats=None, competitions=None, date_from=None, date_to=None,
                  series_ids=None, ours_only=None, match_types=None):
    if df.empty:
        return df
    m = pd.Series(True, index=df.index)
    if seasons:      m &= df["season"].isin(seasons)
    if opponents:    m &= df["opponent"].isin(opponents)
    if maps:         m &= df["map_name"].isin(maps)
    if players:      m &= df["display_name"].isin(players)
    if formats:      m &= df["format"].isin(formats)
    if competitions: m &= df["competition"].isin(competitions)
    if match_types:  m &= df["match_type"].fillna("Gameday").isin(match_types)
    if series_ids:   m &= df["series_id"].isin(series_ids)
    if date_from:    m &= df["date"] >= str(date_from)
    if date_to:      m &= df["date"] <= str(date_to)
    if ours_only is True:  m &= df["is_us"] == 1
    if ours_only is False: m &= df["is_us"] != 1
    return df[m]


def _derive(g: pd.DataFrame) -> pd.Series:
    k, d, a = g["kills"].sum(), g["deaths"].sum(), g["assists"].sum()
    rounds = g["rounds_played"].sum()
    return pd.Series({
        "Maps": g["map_game_id"].nunique(),
        "Series": g["series_id"].nunique(),
        "Rounds": int(rounds) if pd.notna(rounds) else 0,
        "K": int(k), "D": int(d), "A": int(a) if pd.notna(a) else 0,
        "K/D": round(k / max(d, 1), 2),
        "KPR": round(k / rounds, 2) if rounds else None,
        "SRV%": round(100 * (rounds - d) / rounds, 1) if rounds else None,
        "DPR": round(d / rounds, 2) if rounds else None,
        "APR": round(a / rounds, 2) if rounds and pd.notna(a) else None,
        "+/-": int(k - d),
        "Avg Score": round(g["score"].mean(), 0) if g["score"].notna().any() else None,
    })


def player_table(df, by="display_name", extra_group=None) -> pd.DataFrame:
    """Aggregate per player (optionally per player x map / opponent / season...)."""
    if df.empty:
        return pd.DataFrame()
    keys = [by] + ([extra_group] if extra_group else [])
    out = df.groupby(keys, dropna=False).apply(_derive, include_groups=False).reset_index()
    return out.sort_values("K/D", ascending=False)


def team_map_record(conn, df_ours) -> dict:
    """Team-level record computed from maps_played (restricted to maps in df_ours)."""
    if df_ours.empty:
        return {"series": "0-0", "maps": "0-0", "rounds": "0-0",
                "team_kd": None, "map_winpct": None, "round_winpct": None}
    maps = df_ours.drop_duplicates("map_game_id")
    mw = (maps["map_result"] == "W").sum(); ml = (maps["map_result"] == "L").sum()
    rw = maps["rounds_won"].sum(); rl = maps["rounds_lost"].sum()
    ser = maps.drop_duplicates("series_id")
    sids = list(ser["series_id"])
    q = ",".join("?" * len(sids))
    srows = conn.execute(f"SELECT result FROM series WHERE series_id IN ({q})", sids).fetchall()
    sw = sum(1 for r in srows if r["result"] == "W"); sl = sum(1 for r in srows if r["result"] == "L")
    k, d = df_ours["kills"].sum(), df_ours["deaths"].sum()
    return {
        "series": f"{sw}-{sl}", "maps": f"{int(mw)}-{int(ml)}",
        "rounds": f"{int(rw)}-{int(rl)}",
        "team_kd": round(k / max(d, 1), 2),
        "map_winpct": round(100 * mw / max(mw + ml, 1), 1),
        "round_winpct": round(100 * rw / max(rw + rl, 1), 1),
    }


def map_breakdown(df_ours) -> pd.DataFrame:
    """Per-map team record from our rows."""
    if df_ours.empty:
        return pd.DataFrame()
    maps = df_ours.drop_duplicates("map_game_id")
    g = maps.groupby("map_name").agg(
        Played=("map_game_id", "nunique"),
        W=("map_result", lambda s: (s == "W").sum()),
        L=("map_result", lambda s: (s == "L").sum()),
        RW=("rounds_won", "sum"), RL=("rounds_lost", "sum")).reset_index()
    g["Map Win%"] = (100 * g["W"] / (g["W"] + g["L"]).clip(lower=1)).round(1)
    g["Round Win%"] = (100 * g["RW"] / (g["RW"] + g["RL"]).clip(lower=1)).round(1)
    return g.sort_values(["Played", "Map Win%"], ascending=False)


def veto_frame(conn) -> pd.DataFrame:
    q = """SELECT v.*, s.date, s.season, s.format, t.name AS acting_team,
                  opp.name AS opponent, s.opponent_id
           FROM veto_events v
           JOIN series s USING(series_id)
           LEFT JOIN teams t ON v.team_id = t.team_id
           LEFT JOIN teams opp ON s.opponent_id = opp.team_id
           ORDER BY s.date, v.series_id, v.seq"""
    return pd.read_sql_query(q, conn)
