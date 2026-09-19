"""Export a clean Excel report workbook from the database.

The workbook is a REPORT SNAPSHOT (the database is the source of truth) but the
summary sheets use real formulas over the raw rows, so it keeps recalculating if
you hand-edit a number. 6 tabs, no more:
  Read Me | Series | Maps | Player Map Stats | Player Overview | Map Analysis
"""
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

from . import stats

HDR = Font(name="Arial", bold=True, color="FFFFFF")
BASE = Font(name="Arial")
FILL = PatternFill("solid", fgColor="1F3864")


def _write_df(ws, df, start_row=1):
    ws.append(list(df.columns))
    for c in ws[start_row]:
        c.font = HDR
        c.fill = FILL
        c.alignment = Alignment(horizontal="center")
    for _, row in df.iterrows():
        ws.append(["" if pd.isna(v) else v for v in row])
    for col_cells in ws.columns:
        width = max((len(str(c.value)) for c in col_cells if c.value is not None), default=8)
        ws.column_dimensions[col_cells[0].column_letter].width = min(max(width + 2, 9), 42)
    for row in ws.iter_rows(min_row=start_row + 1):
        for c in row:
            c.font = BASE


def export(conn, out_path: str) -> str:
    wb = Workbook()

    ws = wb.active
    ws.title = "Read Me"
    lines = [
        ["Northwood R6 stat export"],
        [""],
        ["This workbook is generated from data/siege.db by the tracker app (Manage -> Export)."],
        ["Edit data in the app, then re-export; hand edits here are overwritten next export."],
        [""],
        ["Definitions:"],
        ["K/D = kills/deaths (displayed as kill count when deaths = 0)"],
        ["KPR = kills / rounds played"],
        ["Survival % = (rounds played - deaths) / rounds played"],
        ["+/- = kills - deaths"],
        ["Screenshot-derived stats: Score, K, D, A, rounds. KOST/clutches/plants are manual-only."],
    ]
    for ln in lines:
        ws.append(ln)
    for row in ws.iter_rows():
        for c in row:
            c.font = Font(name="Arial", bold=(row[0].row == 1))

    series = pd.read_sql_query(
        """SELECT s.series_id, s.date, s.match_type, s.season, s.competition, t.name AS opponent,
                  s.format, s.result, s.maps_won, s.maps_lost, s.vod, s.notes
           FROM series s LEFT JOIN teams t ON s.opponent_id=t.team_id ORDER BY s.date""", conn)
    _write_df(wb.create_sheet("Series"), series)

    maps = pd.read_sql_query(
        """SELECT mp.map_game_id, mp.series_id, s.date, s.match_type, t.name AS opponent, mp.map_number,
                  mp.map_name, mp.result, mp.rounds_won, mp.rounds_lost,
                  mp.rounds_won + mp.rounds_lost AS total_rounds,
                  mp.siege_match_id, mp.source_file, mp.import_status
           FROM maps_played mp JOIN series s USING(series_id)
           LEFT JOIN teams t ON s.opponent_id=t.team_id ORDER BY s.date, mp.map_number""", conn)
    _write_df(wb.create_sheet("Maps"), maps)

    df = stats.load_frame(conn)
    raw_cols = ["map_game_id", "date", "match_type", "opponent", "map_name", "team_name", "display_name",
                "gamertag_displayed", "score", "kills", "deaths", "assists", "rounds_played"]
    raw = df[raw_cols] if not df.empty else pd.DataFrame(columns=raw_cols)
    ws_raw = wb.create_sheet("Player Map Stats")
    _write_df(ws_raw, raw)

    # Player Overview: raw sums per Northwood player + live formulas for derived stats
    ours = df[df["is_us"] == 1] if not df.empty else df
    ws_po = wb.create_sheet("Player Overview")
    headers = ["Player", "Maps", "Rounds", "K", "D", "A", "K/D", "KPR", "SRV %", "+/-"]
    ws_po.append(headers)
    for c in ws_po[1]:
        c.font = HDR; c.fill = FILL; c.alignment = Alignment(horizontal="center")
    r = 2
    if not ours.empty:
        for name, g in ours.groupby("display_name"):
            ws_po.append([name, int(g["map_game_id"].nunique()), int(g["rounds_played"].sum()),
                          int(g["kills"].sum()), int(g["deaths"].sum()), int(g["assists"].sum()),
                          None, None, None, None])
            ws_po[f"G{r}"] = f"=ROUND(D{r}/MAX(E{r},1),2)"
            ws_po[f"H{r}"] = f"=ROUND(D{r}/MAX(C{r},1),2)"
            ws_po[f"I{r}"] = f"=ROUND((C{r}-E{r})/MAX(C{r},1)*100,1)"
            ws_po[f"J{r}"] = f"=D{r}-E{r}"
            r += 1
    for row in ws_po.iter_rows(min_row=2):
        for c in row:
            c.font = BASE
    for col, w in zip("ABCDEFGHIJ", (16, 8, 9, 6, 6, 6, 8, 8, 8, 7)):
        ws_po.column_dimensions[col].width = w

    mb = stats.map_breakdown(ours) if not ours.empty else pd.DataFrame()
    _write_df(wb.create_sheet("Map Analysis"), mb if not mb.empty else pd.DataFrame(
        columns=["map_name", "Played", "W", "L", "RW", "RL", "Map Win%", "Round Win%"]))

    vet = stats.veto_frame(conn)
    if not vet.empty:
        vet = vet[["date", "opponent", "format", "seq", "acting_team", "action", "map_name"]]
    _write_df(wb.create_sheet("Vetoes"), vet if not vet.empty else pd.DataFrame(
        columns=["date", "opponent", "format", "seq", "acting_team", "action", "map_name"]))

    wb.save(out_path)
    return out_path
