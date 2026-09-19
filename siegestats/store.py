"""Durable storage on Google Sheets.

Why this exists: free hosting (Streamlit Community Cloud) gives you no permanent
disk. The container's filesystem resets on every reboot or redeploy, so a local
siege.db would quietly vanish. This module keeps a complete copy of every table
in the same spreadsheet the team already reads, and rebuilds the database from it
on boot. Free, durable, and the backup is something you can eyeball.

Two different things live in one spreadsheet:
  - REPORT tabs (Team, Players, ...) written by gsheets.py - for humans.
  - DATA tabs prefixed with `_db_` - one per table, an exact copy of the rows.
    Don't hand-edit those; they are overwritten on every sync and read back
    verbatim on boot.

Concurrency is last-write-wins. For a six-person team where one person imports a
match at a time that is fine; two people importing simultaneously is not
supported, and the app says so.
"""
import sqlite3

TABLES = [
    "teams", "team_aliases", "players", "aliases", "series", "maps_played",
    "player_map_stats", "player_phase_stats", "round_results", "manual_stats",
    "veto_events", "operator_bans", "map_pool", "settings",
]
PREFIX = "_db_"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive"]

# never leave an API key sitting in a shared spreadsheet
SECRET_SETTINGS = {"api_key"}


def _client(creds_path):
    import gspread
    from google.oauth2.service_account import Credentials
    return gspread.authorize(Credentials.from_service_account_file(creds_path, scopes=SCOPES))


def open_sheet(sheet_ref, creds_path):
    gc = _client(creds_path)
    return (gc.open_by_url(sheet_ref) if str(sheet_ref).startswith("http")
            else gc.open_by_key(sheet_ref))


def table_columns(conn, table):
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def dump_rows(conn, table):
    """[[header...], [row...]] for one table, with secrets filtered out."""
    cols = table_columns(conn, table)
    if not cols:
        return []
    rows = [list(cols)]
    for r in conn.execute(f"SELECT {', '.join(cols)} FROM {table}"):
        vals = ["" if r[c] is None else r[c] for c in cols]
        if table == "settings" and str(r["key"]) in SECRET_SETTINGS:
            continue
        rows.append(vals)
    return rows


def restore_table(conn, table, values):
    """Replace one table's contents from a dumped block. Unknown columns are
    ignored, so an older backup still loads after a schema change."""
    if not values or len(values) < 1:
        return 0
    header = [h for h in values[0] if h]
    live = set(table_columns(conn, table))
    use = [h for h in header if h in live]
    if not use:
        return 0
    idx = [header.index(h) for h in use]
    conn.execute(f"DELETE FROM {table}")
    n = 0
    for row in values[1:]:
        if not any(str(c).strip() for c in row):
            continue
        vals = [(row[i] if i < len(row) else None) for i in idx]
        vals = [None if (v == "" or v is None) else v for v in vals]
        conn.execute(f"INSERT INTO {table} ({', '.join(use)}) VALUES ({', '.join('?' * len(use))})",
                     vals)
        n += 1
    return n


def push(conn, sheet_ref, creds_path, sheet=None):
    """Write every table to its _db_ tab. Returns the number of tables written."""
    import gspread
    sh = sheet or open_sheet(sheet_ref, creds_path)
    written = 0
    for t in TABLES:
        try:
            values = dump_rows(conn, t)
        except sqlite3.Error:
            continue
        if not values:
            continue
        name = PREFIX + t
        try:
            ws = sh.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(name, rows=max(len(values) + 50, 100),
                                  cols=max(len(values[0]) + 2, 10))
        ws.clear()
        ws.update(values=values, range_name="A1", value_input_option="RAW")
        written += 1
    return written


def pull(conn, sheet_ref, creds_path, sheet=None):
    """Rebuild the database from the _db_ tabs. Returns {table: rows_loaded}."""
    import gspread
    sh = sheet or open_sheet(sheet_ref, creds_path)
    loaded = {}
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for t in TABLES:
            try:
                ws = sh.worksheet(PREFIX + t)
            except gspread.WorksheetNotFound:
                continue
            loaded[t] = restore_table(conn, t, ws.get_all_values())
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return loaded


def has_backup(sheet_ref, creds_path):
    """True when the spreadsheet already holds a _db_ copy."""
    sh = open_sheet(sheet_ref, creds_path)
    return any(ws.title.startswith(PREFIX) for ws in sh.worksheets())


def is_empty(conn):
    return conn.execute("SELECT COUNT(*) c FROM series").fetchone()["c"] == 0
