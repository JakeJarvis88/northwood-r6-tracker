"""SQLite backend for the Northwood R6 stat tracker.

The database (data/siege.db) is the single source of truth.
Excel is only an export/report format (see export_excel.py).
"""
import os
import sqlite3
from datetime import date

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "siege.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    team_id     INTEGER PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    is_us       INTEGER NOT NULL DEFAULT 0,
    notes       TEXT
);
CREATE TABLE IF NOT EXISTS team_aliases (
    id          INTEGER PRIMARY KEY,
    team_id     INTEGER NOT NULL REFERENCES teams(team_id),
    alt_name    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS players (
    player_id   INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,               -- canonical/real name
    team_id     INTEGER REFERENCES teams(team_id),
    role        TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    active_from TEXT,
    active_to   TEXT
);
CREATE TABLE IF NOT EXISTS aliases (
    alias_id    INTEGER PRIMARY KEY,
    player_id   INTEGER NOT NULL REFERENCES players(player_id),
    gamertag    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'confirmed',   -- confirmed | pending
    first_seen  TEXT,
    last_seen   TEXT,
    UNIQUE(player_id, gamertag)
);
CREATE TABLE IF NOT EXISTS series (
    series_id   INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,               -- ISO yyyy-mm-dd (stored as text: no Excel-style date mangling)
    season      TEXT,
    competition TEXT,
    opponent_id INTEGER REFERENCES teams(team_id),
    format      TEXT NOT NULL DEFAULT 'BO3', -- BO1 | BO3 | other
    result      TEXT,                        -- W | L | T (ours)
    maps_won    INTEGER DEFAULT 0,
    maps_lost   INTEGER DEFAULT 0,
    vod         TEXT,
    notes       TEXT
);
CREATE TABLE IF NOT EXISTS maps_played (
    map_game_id     INTEGER PRIMARY KEY,
    series_id       INTEGER NOT NULL REFERENCES series(series_id),
    map_number      INTEGER DEFAULT 1,
    map_name        TEXT NOT NULL,
    result          TEXT,                    -- W | L | T (ours)
    rounds_won      INTEGER DEFAULT 0,       -- ours
    rounds_lost     INTEGER DEFAULT 0,
    siege_match_id  TEXT,                    -- Match ID from the scoreboard, if visible
    source_file     TEXT,                    -- screenshot filename(s)
    import_status   TEXT DEFAULT 'confirmed',-- confirmed | in_progress | needs_review
    notes           TEXT
);
CREATE TABLE IF NOT EXISTS player_map_stats (
    id              INTEGER PRIMARY KEY,
    map_game_id     INTEGER NOT NULL REFERENCES maps_played(map_game_id) ON DELETE CASCADE,
    team_id         INTEGER REFERENCES teams(team_id),
    player_id       INTEGER REFERENCES players(player_id),  -- NULL for unmapped opponent players
    gamertag_displayed TEXT NOT NULL,
    score           INTEGER,
    kills           INTEGER,
    deaths          INTEGER,
    assists         INTEGER,
    rounds_played   INTEGER,
    UNIQUE(map_game_id, gamertag_displayed)
);
-- Stats a scoreboard screenshot cannot provide. Kept separate & optional on purpose.
-- Cumulative snapshot of a player's line at the end of a phase, taken from a
-- halftime screenshot. Second-half numbers are derived as (final - halftime),
-- never read separately, so the two always reconcile with the map total.
CREATE TABLE IF NOT EXISTS player_phase_stats (
    id              INTEGER PRIMARY KEY,
    map_game_id     INTEGER NOT NULL REFERENCES maps_played(map_game_id) ON DELETE CASCADE,
    phase           TEXT NOT NULL,            -- H1 | OT (cumulative at end of that phase)
    gamertag_displayed TEXT NOT NULL,
    player_id       INTEGER REFERENCES players(player_id),
    team_id         INTEGER REFERENCES teams(team_id),
    kills           INTEGER, deaths INTEGER, assists INTEGER, score INTEGER,
    source_file     TEXT,
    UNIQUE(map_game_id, phase, gamertag_displayed)
);
-- Round-by-round results read from the strip above the scoreboard. Each marker
-- sits on the winning team's line and carries an icon for how the round ended;
-- the ATK/DEF labels over each half give the side. Stored per round so side
-- splits, win conditions and momentum all come from one source.
CREATE TABLE IF NOT EXISTS round_results (
    id            INTEGER PRIMARY KEY,
    map_game_id   INTEGER NOT NULL REFERENCES maps_played(map_game_id) ON DELETE CASCADE,
    round_number  INTEGER NOT NULL,
    won           INTEGER,               -- 1 = Northwood won the round, 0 = lost
    side          TEXT,                  -- ATK | DEF (our side that round)
    win_condition TEXT,                  -- elimination | time | defuse | disabled | unknown
    confidence    REAL,
    source        TEXT DEFAULT 'screenshot',
    UNIQUE(map_game_id, round_number)
);
CREATE TABLE IF NOT EXISTS manual_stats (
    id          INTEGER PRIMARY KEY,
    map_game_id INTEGER NOT NULL REFERENCES maps_played(map_game_id) ON DELETE CASCADE,
    player_id   INTEGER NOT NULL REFERENCES players(player_id),
    kost        REAL,
    clutches    INTEGER,
    plants      INTEGER,
    defuses     INTEGER,
    notes       TEXT,
    UNIQUE(map_game_id, player_id)
);
CREATE TABLE IF NOT EXISTS veto_events (
    id          INTEGER PRIMARY KEY,
    series_id   INTEGER NOT NULL REFERENCES series(series_id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    team_id     INTEGER REFERENCES teams(team_id),  -- NULL for 'decider' with no acting team
    action      TEXT NOT NULL,               -- Ban | Pick | Decider
    map_name    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operator_bans (
    id          INTEGER PRIMARY KEY,
    map_game_id INTEGER NOT NULL REFERENCES maps_played(map_game_id) ON DELETE CASCADE,
    team_id     INTEGER REFERENCES teams(team_id),
    side        TEXT,                        -- ATK | DEF | ''
    operator    TEXT NOT NULL,
    source      TEXT DEFAULT 'manual'        -- manual | screenshot
);
CREATE TABLE IF NOT EXISTS map_pool (
    id          INTEGER PRIMARY KEY,
    map_name    TEXT NOT NULL,
    season      TEXT NOT NULL DEFAULT 'ALL',
    active      INTEGER NOT NULL DEFAULT 1,
    active_from TEXT,
    active_to   TEXT,
    UNIQUE(map_name, season)
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def get_conn(path: str = None) -> sqlite3.Connection:
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(series)")]
    if "finalized" not in cols:
        conn.execute("ALTER TABLE series ADD COLUMN finalized INTEGER DEFAULT 0")
        conn.commit()
    mcols = [r["name"] for r in conn.execute("PRAGMA table_info(maps_played)")]
    for col, ddl in (("starting_side", "TEXT"),          # Northwood's side in round 1: ATK | DEF
                     ("ot_starting_side", "TEXT"),       # Northwood's side in the first OT round
                     ("half1_won", "INTEGER"),           # our rounds won in the first half
                     ("half1_lost", "INTEGER")):
        if col not in mcols:
            conn.execute(f"ALTER TABLE maps_played ADD COLUMN {col} {ddl}")
            conn.commit()
    obcols = [r["name"] for r in conn.execute("PRAGMA table_info(operator_bans)")]
    if "slot" not in obcols:
        conn.execute("ALTER TABLE operator_bans ADD COLUMN slot TEXT DEFAULT 'first2'")
        conn.execute("UPDATE operator_bans SET slot='first2' WHERE slot IS NULL")
        conn.commit()
    if "match_type" not in cols:
        # Gameday = official/league match, Scrim = practice. Existing rows default to Gameday.
        conn.execute("ALTER TABLE series ADD COLUMN match_type TEXT DEFAULT 'Gameday'")
        conn.execute("UPDATE series SET match_type='Gameday' WHERE match_type IS NULL")
        conn.commit()
    return conn


# ---------------------------------------------------------------- settings
def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()


# ---------------------------------------------------------------- teams
def our_team_id(conn):
    row = conn.execute("SELECT team_id FROM teams WHERE is_us=1").fetchone()
    return row["team_id"] if row else None


def get_or_create_team(conn, name, is_us=0):
    name = (name or "").strip()
    if not name:
        return None
    row = conn.execute("SELECT team_id FROM teams WHERE lower(name)=lower(?)", (name,)).fetchone()
    if row:
        return row["team_id"]
    row = conn.execute(
        "SELECT team_id FROM team_aliases ta JOIN teams t USING(team_id) WHERE lower(ta.alt_name)=lower(?)",
        (name,)).fetchone()
    if row:
        return row["team_id"]
    cur = conn.execute("INSERT INTO teams(name,is_us) VALUES(?,?)", (name, is_us))
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------- roster / aliases
def roster(conn, team_id=None, active_only=False):
    q = "SELECT p.*, t.name AS team_name FROM players p LEFT JOIN teams t USING(team_id) WHERE 1=1"
    args = []
    if team_id:
        q += " AND p.team_id=?"; args.append(team_id)
    if active_only:
        q += " AND p.active=1"
    return conn.execute(q + " ORDER BY p.name", args).fetchall()


def alias_table(conn, team_id=None):
    q = """SELECT a.*, p.name AS player_name, p.team_id FROM aliases a
           JOIN players p USING(player_id) WHERE 1=1"""
    args = []
    if team_id:
        q += " AND p.team_id=?"; args.append(team_id)
    return conn.execute(q + " ORDER BY p.name, a.gamertag", args).fetchall()


def add_alias(conn, player_id, gamertag, status="confirmed", seen=None):
    seen = seen or date.today().isoformat()
    conn.execute(
        """INSERT INTO aliases(player_id, gamertag, status, first_seen, last_seen)
           VALUES(?,?,?,?,?)
           ON CONFLICT(player_id, gamertag)
           DO UPDATE SET status=excluded.status, last_seen=excluded.last_seen""",
        (player_id, gamertag, status, seen, seen))
    conn.commit()


# ---------------------------------------------------------------- map pool
def active_maps(conn, season=None):
    rows = conn.execute("SELECT DISTINCT map_name FROM map_pool WHERE active=1 ORDER BY map_name").fetchall()
    return [r["map_name"] for r in rows]


def all_pool_maps(conn):
    rows = conn.execute("SELECT DISTINCT map_name FROM map_pool ORDER BY map_name").fetchall()
    return [r["map_name"] for r in rows]


# ---------------------------------------------------------------- series / maps
def recalc_series(conn, series_id):
    """Recompute maps won/lost + result from maps_played rows."""
    rows = conn.execute("SELECT result FROM maps_played WHERE series_id=?", (series_id,)).fetchall()
    w = sum(1 for r in rows if r["result"] == "W")
    l = sum(1 for r in rows if r["result"] == "L")
    res = "W" if w > l else ("L" if l > w else ("T" if rows else None))
    conn.execute("UPDATE series SET maps_won=?, maps_lost=?, result=? WHERE series_id=?", (w, l, res, series_id))
    conn.commit()


def save_map_with_stats(conn, map_row: dict, stat_rows: list, replace_map_game_id=None):
    """Insert (or replace) one map + its player rows atomically. Returns map_game_id.
    Any failure rolls the whole thing back, so a half-written map can't exist."""
    try:
        return _save_map_with_stats(conn, map_row, stat_rows, replace_map_game_id)
    except Exception:
        conn.rollback()
        raise


def _save_map_with_stats(conn, map_row, stat_rows, replace_map_game_id=None):
    cur = conn.cursor()
    if replace_map_game_id:
        cur.execute("DELETE FROM player_map_stats WHERE map_game_id=?", (replace_map_game_id,))
        cur.execute(
            """UPDATE maps_played SET map_number=?, map_name=?, result=?, rounds_won=?, rounds_lost=?,
               siege_match_id=?, source_file=?, import_status=?, notes=?,
               starting_side=?, ot_starting_side=?, half1_won=?, half1_lost=? WHERE map_game_id=?""",
            (map_row.get("map_number", 1), map_row["map_name"], map_row.get("result"),
             map_row.get("rounds_won", 0), map_row.get("rounds_lost", 0), map_row.get("siege_match_id"),
             map_row.get("source_file"), map_row.get("import_status", "confirmed"), map_row.get("notes"),
             map_row.get("starting_side"), map_row.get("ot_starting_side"),
             map_row.get("half1_won"), map_row.get("half1_lost"),
             replace_map_game_id))
        mg_id = replace_map_game_id
    else:
        cur.execute(
            """INSERT INTO maps_played(series_id, map_number, map_name, result, rounds_won, rounds_lost,
               siege_match_id, source_file, import_status, notes,
               starting_side, ot_starting_side, half1_won, half1_lost)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (map_row["series_id"], map_row.get("map_number", 1), map_row["map_name"], map_row.get("result"),
             map_row.get("rounds_won", 0), map_row.get("rounds_lost", 0), map_row.get("siege_match_id"),
             map_row.get("source_file"), map_row.get("import_status", "confirmed"), map_row.get("notes"),
             map_row.get("starting_side"), map_row.get("ot_starting_side"),
             map_row.get("half1_won"), map_row.get("half1_lost")))
        mg_id = cur.lastrowid
    for s in stat_rows:
        cur.execute(
            """INSERT INTO player_map_stats(map_game_id, team_id, player_id, gamertag_displayed,
               score, kills, deaths, assists, rounds_played) VALUES(?,?,?,?,?,?,?,?,?)""",
            (mg_id, s.get("team_id"), s.get("player_id"), s["gamertag_displayed"],
             s.get("score"), s.get("kills"), s.get("deaths"), s.get("assists"), s.get("rounds_played")))
    conn.commit()
    recalc_series(conn, map_row.get("series_id") or conn.execute(
        "SELECT series_id FROM maps_played WHERE map_game_id=?", (mg_id,)).fetchone()["series_id"])
    return mg_id


def delete_series(conn, series_id):
    for mg in conn.execute("SELECT map_game_id FROM maps_played WHERE series_id=?", (series_id,)).fetchall():
        conn.execute("DELETE FROM player_map_stats WHERE map_game_id=?", (mg["map_game_id"],))
        conn.execute("DELETE FROM operator_bans WHERE map_game_id=?", (mg["map_game_id"],))
        conn.execute("DELETE FROM manual_stats WHERE map_game_id=?", (mg["map_game_id"],))
    conn.execute("DELETE FROM maps_played WHERE series_id=?", (series_id,))
    conn.execute("DELETE FROM veto_events WHERE series_id=?", (series_id,))
    conn.execute("DELETE FROM series WHERE series_id=?", (series_id,))
    conn.commit()


def delete_map(conn, map_game_id):
    row = conn.execute("SELECT series_id FROM maps_played WHERE map_game_id=?", (map_game_id,)).fetchone()
    conn.execute("DELETE FROM player_map_stats WHERE map_game_id=?", (map_game_id,))
    conn.execute("DELETE FROM operator_bans WHERE map_game_id=?", (map_game_id,))
    conn.execute("DELETE FROM manual_stats WHERE map_game_id=?", (map_game_id,))
    conn.execute("DELETE FROM maps_played WHERE map_game_id=?", (map_game_id,))
    conn.commit()
    if row:
        recalc_series(conn, row["series_id"])


def save_phase_snapshot(conn, map_game_id, phase, rows, source_file=None):
    """Store a cumulative halftime (or end-of-regulation) snapshot for one map."""
    conn.execute("DELETE FROM player_phase_stats WHERE map_game_id=? AND phase=?", (map_game_id, phase))
    for r in rows:
        conn.execute(
            """INSERT INTO player_phase_stats(map_game_id, phase, gamertag_displayed, player_id,
               team_id, kills, deaths, assists, score, source_file)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (map_game_id, phase, r["gamertag_displayed"], r.get("player_id"), r.get("team_id"),
             r.get("kills"), r.get("deaths"), r.get("assists"), r.get("score"), source_file))
    conn.commit()


def has_phase(conn, map_game_id, phase="H1"):
    return conn.execute("SELECT COUNT(*) c FROM player_phase_stats WHERE map_game_id=? AND phase=?",
                        (map_game_id, phase)).fetchone()["c"] > 0


def save_round_results(conn, map_game_id, rounds, source="screenshot"):
    """Replace the round-by-round record for one map. rounds: list of dicts with
    round_number, won, side, win_condition, confidence."""
    conn.execute("DELETE FROM round_results WHERE map_game_id=?", (map_game_id,))
    for r in rounds:
        conn.execute(
            """INSERT INTO round_results(map_game_id, round_number, won, side,
               win_condition, confidence, source) VALUES(?,?,?,?,?,?,?)""",
            (map_game_id, int(r["round_number"]), None if r.get("won") is None else int(r["won"]),
             r.get("side"), r.get("win_condition"), r.get("confidence"), source))
    conn.commit()


def has_rounds(conn, map_game_id):
    return conn.execute("SELECT COUNT(*) c FROM round_results WHERE map_game_id=?",
                        (map_game_id,)).fetchone()["c"] > 0
