"""One-way publish to Google Sheets.

Design: the local SQLite DB stays the single source of truth (imports, alias
approval and dedupe need transactional writes - a live Sheet-as-database would
reintroduce the silent corruption the old workbook suffered, e.g. "3-7" turning
into March 7). Every sync pushes the COMPLETE data set - raw rows included -
so the Sheet doubles as an off-machine copy the team can read anywhere.
Anything typed into the Sheet is overwritten on the next sync.

Setup (once, ~5 min):
 1. console.cloud.google.com -> create a project -> enable "Google Sheets API"
    and "Google Drive API".
 2. IAM & Admin -> Service Accounts -> create one -> Keys -> add JSON key ->
    download it (e.g. to google_creds.json next to app.py).
 3. Create a Google Sheet, click Share, and share it (Editor) with the service
    account's ...@...iam.gserviceaccount.com email.
 4. In the app: Manage -> Settings -> paste the Sheet URL + creds file path.
"""
from datetime import datetime

import pandas as pd

from . import sides as sidemod
from . import opbans as opbansmod
from . import rating as ratingmod
from . import stats

SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive"]

DEFINITIONS = ("K/D = kills/deaths (kill count shown when deaths = 0) · KPR = kills/rounds · "
               "SRV% = (rounds-deaths)/rounds · +/- = kills-deaths · ping is never tracked")


def _py(v):
    """numpy/pandas scalar -> plain python for JSON."""
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
        return ""
    if hasattr(v, "item"):
        v = v.item()
    return v


def _rows(df: pd.DataFrame):
    return [list(df.columns)] + [[_py(v) for v in r] for r in df.itertuples(index=False)]


def build_sections(conn):
    """OrderedDict tab -> list of (section_title, DataFrame). 6 tabs, no more."""
    df = stats.load_frame(conn)
    ours = df[df["is_us"] == 1] if not df.empty else df
    theirs = df[df["is_us"] != 1] if not df.empty else df

    def _rec_row(label, sub):
        r = stats.team_map_record(conn, sub)
        return {"Scope": label, "Series": r["series"], "Maps": r["maps"], "Rounds": r["rounds"],
                "Team K/D": r["team_kd"], "Map win %": r["map_winpct"], "Round win %": r["round_winpct"]}

    mt = ours["match_type"].fillna("Gameday") if not ours.empty else ours
    rec_rows = [_rec_row("Gameday", ours[mt == "Gameday"] if not ours.empty else ours)]
    if not ours.empty and (mt == "Scrim").any():
        rec_rows.append(_rec_row("Scrim", ours[mt == "Scrim"]))
        rec_rows.append(_rec_row("Total", ours))
    rec_df = pd.DataFrame(rec_rows)
    recent = (ours.drop_duplicates("map_game_id")
              [["date", "opponent", "match_type", "map_name", "map_result", "rounds_won", "rounds_lost", "format"]]
              .sort_values("date", ascending=False)) if not ours.empty else pd.DataFrame()

    series = pd.read_sql_query(
        """SELECT s.date, s.match_type, s.season, s.competition, t.name AS opponent, s.format, s.result,
                  s.maps_won, s.maps_lost, s.vod, s.notes FROM series s
           LEFT JOIN teams t ON s.opponent_id=t.team_id ORDER BY s.date""", conn)
    maps = pd.read_sql_query(
        """SELECT s.date, s.match_type, t.name AS opponent, mp.map_number, mp.map_name, mp.result,
                  mp.rounds_won, mp.rounds_lost, mp.siege_match_id, mp.import_status
           FROM maps_played mp JOIN series s USING(series_id)
           LEFT JOIN teams t ON s.opponent_id=t.team_id ORDER BY s.date, mp.map_number""", conn)

    vf = stats.veto_frame(conn)
    veto_hist = vf[["date", "opponent", "seq", "acting_team", "action", "map_name"]] if not vf.empty else pd.DataFrame()
    veto_tend = (vf.groupby(["acting_team", "action", "map_name"]).size().reset_index(name="times")
                 .sort_values(["acting_team", "action", "times"], ascending=[True, True, False])
                 ) if not vf.empty else pd.DataFrame()
    try:
        _b = opbansmod.ban_frame(conn)
        _ids = ours["map_game_id"].unique().tolist() if not ours.empty else []
        ob_vs = opbansmod.summarize(conn, _b, by_us=False, map_ids=_ids)
        ob_by = opbansmod.summarize(conn, _b, by_us=True, map_ids=_ids)
    except Exception:
        ob_vs = ob_by = pd.DataFrame()
    obans = pd.read_sql_query(
        """SELECT s.date, o.name AS opponent, mp.map_name, t.name AS banned_by, ob.side,
                  ob.slot, ob.operator
           FROM operator_bans ob JOIN maps_played mp USING(map_game_id)
           JOIN series s USING(series_id) LEFT JOIN teams t ON ob.team_id=t.team_id
           LEFT JOIN teams o ON s.opponent_id=o.team_id ORDER BY s.date""", conn)

    raw_cols = ["date", "match_type", "opponent", "map_name", "team_name", "display_name", "gamertag_displayed",
                "score", "kills", "deaths", "assists", "rounds_played", "map_result"]
    raw = df[raw_cols].sort_values(["date", "map_name"]) if not df.empty else pd.DataFrame(columns=raw_cols)

    from . import db as dbm
    try:
        rph = int(dbm.get_setting(conn, "rounds_per_half", "6") or 6)
        orph = int(dbm.get_setting(conn, "ot_rounds_per_half", "3") or 3)
        ids = ours["map_game_id"].unique().tolist() if not ours.empty else []
        side_rec = sidemod.side_record_from_rounds(conn, ids)
        if side_rec.empty:
            side_rec = sidemod.team_side_record(conn, ids, rph, orph)
        side_map = sidemod.side_by_map_from_rounds(conn, ids)
        if side_map.empty:
            side_map = sidemod.side_by_map(conn, ids, rph, orph)
        wconds = sidemod.win_conditions(conn, ids)
    except Exception:
        side_rec = side_map = wconds = pd.DataFrame()

    return {
        "Team": [("NORTHWOOD OVERVIEW", rec_df),
                 ("ATTACK VS DEFENSE (team rounds)", side_rec),
                 ("ATTACK VS DEFENSE BY MAP", side_map),
                 ("HOW ROUNDS ARE WON AND LOST", wconds),
                 ("MAP RECORD", stats.map_breakdown(ours)),
                 ("RECENT MAPS", recent)],
        "Players": [("RATING (1.00 = baseline; manual 1vX/plants included where recorded)",
                     ratingmod.rate_players(conn, ours) if not ours.empty else pd.DataFrame()),
                    ("PLAYER OVERVIEW - GAMEDAY", stats.player_table(
                        ours[ours["match_type"].fillna("Gameday") == "Gameday"] if not ours.empty else ours)),
                    ("PLAYER OVERVIEW - SCRIMS", stats.player_table(
                        ours[ours["match_type"].fillna("Gameday") == "Scrim"] if not ours.empty else ours)),
                    ("PLAYER OVERVIEW - TOTAL (gameday + scrims)", stats.player_table(ours))],
        "Player Splits": [("BY MAP", stats.player_table(ours, extra_group="map_name")),
                          ("BY OPPONENT", stats.player_table(ours, extra_group="opponent")),
                          ("BY SEASON", stats.player_table(ours, extra_group="season")),
                          ("GAMEDAY VS SCRIM", stats.player_table(ours, extra_group="match_type"))],
        "Scouting": [("OPPONENT PLAYERS (from their scoreboards)",
                      stats.player_table(theirs, by="team_name", extra_group="gamertag_displayed")),
                     ("VETO TENDENCIES (times each team banned/picked each map)", veto_tend),
                     ("FULL VETO HISTORY", veto_hist),
                     ("OPERATOR BANS AGAINST US (how we do when it's gone)", ob_vs),
                     ("OPERATOR BANS WE MAKE", ob_by),
                     ("EVERY OPERATOR BAN", obans)],
        "Matches": [("SERIES", series), ("MAPS", maps)],
        "Raw Data": [("ONE ROW PER PLAYER PER MAP", raw)],
    }


def sync(conn, sheet_ref: str, creds_path: str) -> str:
    """Push everything. Returns the spreadsheet URL."""
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_url(sheet_ref) if sheet_ref.startswith("http") else gc.open_by_key(sheet_ref)

    stamp = f"Northwood R6 Tracker · synced {datetime.now():%Y-%m-%d %H:%M} · {DEFINITIONS}"
    for tab, sections in build_sections(conn).items():
        values = [[stamp], []]
        title_rows = []
        for title, sdf in sections:
            if sdf is None or sdf.empty:
                continue
            title_rows.append(len(values) + 1)          # 1-based row of the section title
            values.append([title])
            values.extend(_rows(sdf))
            values.append([])
        try:
            ws = sh.worksheet(tab)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(tab, rows=max(len(values) + 20, 60), cols=30)
        ws.clear()
        ws.update(values=values, range_name="A1", value_input_option="RAW")
        try:  # cosmetic only - never fail a sync over formatting
            ws.format("A1:A1", {"backgroundColor": {"red": 0.12, "green": 0.22, "blue": 0.39},
                                "textFormat": {"bold": True, "fontSize": 9,
                                               "foregroundColor": {"red": 1, "green": 1, "blue": 1}}})
            for r in title_rows:
                ws.format(f"A{r}:Z{r}", {"textFormat": {"bold": True}})
        except Exception:
            pass
    return sh.url
