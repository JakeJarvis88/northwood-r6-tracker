"""Shared context for every UI page: the database connection, the home team,
cached data loaders, and the small helpers each page reaches for.

Caching: every loader is keyed on `data_version()`, a cheap signature of the
tables that changes whenever anything is written. Pages call `bump()` after a
write so the next render reloads; nothing is ever served stale, and nothing is
re-queried when the data hasn't moved.
"""

import pandas as pd
import streamlit as st

from siegestats import db, gsheets, opbans, stats, store

UNASSIGNED = "(unassigned / opponent)"
MATCH_TYPES = ["Gameday", "Scrim"]


@st.cache_resource
def get_conn():
    return db.get_conn()


conn = get_conn()


def our_id():
    """Home team id, resolved at call time so a fresh cloud restore is seen."""
    return db.our_team_id(conn)


# ------------------------------------------------------------------ versions
def data_version() -> str:
    """Signature of the writable tables; changes on any insert/update/delete."""
    parts = []
    for t in ("series", "maps_played", "player_map_stats", "veto_events", "operator_bans",
              "manual_stats", "round_results", "aliases", "players", "teams", "map_pool", "settings"):
        r = conn.execute(f"SELECT COUNT(*) c, COALESCE(MAX(rowid),0) m FROM {t}").fetchone()
        parts.append(f"{t}:{r['c']}:{r['m']}")
    # updates in place don't change count/max rowid, so fold in a write counter
    parts.append(f"w:{st.session_state.get('_writes', 0)}")
    return "|".join(parts)


def bump():
    """Call after any write so cached loaders refresh on the next render."""
    st.session_state["_writes"] = st.session_state.get("_writes", 0) + 1


@st.cache_data(show_spinner=False)
def load_frame(version: str) -> pd.DataFrame:
    return stats.load_frame(conn)


@st.cache_data(show_spinner=False)
def load_vetoes(version: str) -> pd.DataFrame:
    return stats.veto_frame(conn)


@st.cache_data(show_spinner=False)
def load_bans(version: str) -> pd.DataFrame:
    return opbans.ban_frame(conn)


def frame():
    return load_frame(data_version())


def vetoes():
    return load_vetoes(data_version())


def bans():
    return load_bans(data_version())


# ------------------------------------------------------------------- helpers
def series_label(row):
    done = " ✔" if ("finalized" in row.keys() and row["finalized"]) else ""
    mt = row["match_type"] if "match_type" in row.keys() and row["match_type"] else "Gameday"
    tag = "🔴 Scrim" if mt == "Scrim" else "🏆 Gameday"
    return f"#{row['series_id']}  {row['date']}  vs {row['opponent'] or '?'}  ({row['format']}, {tag}){done}"


def list_series():
    return conn.execute(
        """SELECT s.*, t.name AS opponent FROM series s
           LEFT JOIN teams t ON s.opponent_id=t.team_id ORDER BY s.date DESC, s.series_id DESC""").fetchall()


def roster_names():
    return [r["name"] for r in db.roster(conn, team_id=our_id(), active_only=True)]


def player_id_by_name(name):
    r = conn.execute("SELECT player_id FROM players WHERE name=? AND team_id=?", (name, our_id())).fetchone()
    return r["player_id"] if r else None


def map_options():
    pool = db.all_pool_maps(conn)
    return pool if pool else ["Bank"]


def to_int(v):
    try:
        return int(v) if pd.notna(v) else None
    except (TypeError, ValueError):
        return None


def setting_int(key, default):
    try:
        return int(db.get_setting(conn, key, str(default)) or default)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------- feedback
def toast(msg, icon="✅"):
    try:
        st.toast(msg, icon=icon)
    except Exception:
        st.success(msg)


def try_autosync(quiet=False):
    """Push to Google Sheets after a write if auto-sync is on. Never blocks the save."""
    bump()
    if db.get_setting(conn, "gs_autosync") != "1":
        return
    url, creds = db.get_setting(conn, "gs_sheet"), db.get_setting(conn, "gs_creds")
    if not (url and creds):
        return
    try:
        with st.spinner("Syncing to Google Sheets…"):
            gsheets.sync(conn, url, creds)
            store.push(conn, url, creds)
        db.set_setting(conn, "gs_last_sync", pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"))
        if not quiet:
            toast("Synced to Google Sheets", "☁️")
    except Exception as e:  # sync problems must never lose an import
        st.warning(f"Saved locally, but the Google Sheets sync failed: {e}")


def full_sync():
    """Manual sync: reports + full-table backup. Returns (url, tables) or raises."""
    url, creds = db.get_setting(conn, "gs_sheet"), db.get_setting(conn, "gs_creds")
    link = gsheets.sync(conn, url, creds)
    n = store.push(conn, url, creds)
    db.set_setting(conn, "gs_last_sync", pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"))
    bump()
    return link, n


def selected_rows(event):
    """Row indices a user clicked in a selectable st.dataframe, or []."""
    try:
        return list(event.selection.rows)
    except Exception:
        return []
