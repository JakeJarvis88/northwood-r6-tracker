"""Shared context for every UI page: the database connection, the home team,
cached data loaders, and the small helpers each page reaches for.

Caching: every loader is keyed on `data_version()`, a cheap signature of the
tables that changes whenever anything is written. Pages call `bump()` after a
write so the next render reloads; nothing is ever served stale, and nothing is
re-queried when the data hasn't moved.
"""

import os

import pandas as pd
import streamlit as st

from siegestats import db, ghstore, gsheets, opbans, stats, store

UNASSIGNED = "(unassigned / opponent)"

# Container-level state set once at boot (see app.cloud_bootstrap).
BOOT = {"gh_load_failed": None}


def local_tz():
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(db.get_setting(conn, "timezone", "America/New_York") or "America/New_York")
    except Exception:
        return ZoneInfo("America/New_York")


def now_local():
    """The server runs on UTC; matches are played in the evening US Eastern time,
    so 'today' would roll over to tomorrow after 8pm without this."""
    return pd.Timestamp.now(tz=local_tz())


def shot_time(filename):
    """Siege names screenshots '...Siege2026-9-16-21-49-42.jpg'. Returns that
    timestamp, or None for any other naming."""
    import re
    m = re.search(r"(20\d\d)-(\d{1,2})-(\d{1,2})-(\d{1,2})-(\d{1,2})-(\d{1,2})", filename or "")
    if not m:
        return None
    try:
        return pd.Timestamp(*[int(x) for x in m.groups()])
    except ValueError:
        return None
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
    """Transient toast + a persistent 'last action' line pages can show, so
    feedback never depends on catching a toast before it fades."""
    st.session_state["last_action"] = f"{icon} {msg} · {now_local():%H:%M:%S}"
    try:
        st.toast(msg, icon=icon)
    except Exception:
        st.success(msg)


def persistence_state():
    """(protected, label) — is anything saving this data outside the container?"""
    from siegestats import ghstore
    token, repo = github_config()
    if ghstore.configured(token, repo):
        return True, f"GitHub ({repo})"
    if db.get_setting(conn, "gs_autosync") == "1" and db.get_setting(conn, "gs_sheet"):
        return True, "Google Sheets"
    return False, None


def persistence_banner():
    """Shown at the top of every page. If nothing is persisting the database, say
    so in the strongest terms and hand over a download button - a rebuild of the
    hosting container erases everything otherwise."""
    protected, where = persistence_state()
    if st.session_state.get("_secret_nested_warning"):
        st.warning(f"`{st.session_state['_secret_nested_warning']}` was found inside a [section] of "
                   "your Secrets. It works, but move it above any [section] header to be safe.",
                   icon="⚠️")
    if protected and st.session_state.get("gh_error"):
        st.error(f"**The last GitHub save failed:** {st.session_state['gh_error']}\n\n"
                 "Your data is only in this session until a save succeeds. Fix the cause, then "
                 "Manage → Data → Save database to GitHub now.", icon="🚨")
        return
    if protected:
        last = st.session_state.get("gh_last_ok") or db.get_setting(conn, "gh_last_push") \
            or db.get_setting(conn, "gs_last_sync")
        st.caption(f"💾 Saving to {where}" + (f" · last save {last}" if last else " · no save yet"))
        return
    st.error(
        "**Nothing is saving your data.** This app runs on a server with no permanent disk: "
        "restarting it, pushing new code, or letting it sleep will erase everything you have "
        "imported. Set this up now — Manage → Data → GitHub storage, about 3 minutes — or "
        "download a backup every single time you import.", icon="🚨")
    c1, c2 = st.columns([1, 3])
    try:
        if True:
            c1.download_button("⬇️ Download backup now", db.snapshot_bytes(conn),
                               f"siege_backup_{now_local():%Y%m%d_%H%M}.db",
                               type="primary", key=f"dlnow_{st.session_state.get('page_nav', '')}")
    except OSError:
        pass
    c2.caption("Restore a downloaded file under Manage → Data → Restore from a backup file.")


def last_action_line():
    la = st.session_state.get("last_action")
    if la:
        st.caption(f"Last action: {la}")


def reset_widgets(*keys, prefix=None, suffix=None):
    """Forget stored widget state. Streamlit's data_editor keeps edits as a diff
    against the frame it was FIRST shown; if the underlying data changes (after a
    save) those stale edits re-apply on top of the new data and look like they
    landed somewhere else. Clearing the key after a save prevents that."""
    for k in list(st.session_state.keys()):
        if k in keys or (prefix and str(k).startswith(prefix)) or (suffix and str(k).endswith(suffix)):
            del st.session_state[k]


SECRET_ALIASES = {"github_token": ("github_token", "GITHUB_TOKEN", "gh_token"),
                  "github_repo": ("github_repo", "GITHUB_REPO", "gh_repo"),
                  "ANTHROPIC_API_KEY": ("ANTHROPIC_API_KEY", "anthropic_api_key", "anthropic_key",
                                        "CLAUDE_API_KEY", "api_key")}


def _secret(key, default=None):
    """Read a secret. Also looks one level inside [tables], because a line added
    below a [section] header in TOML silently becomes part of that section."""
    names = SECRET_ALIASES.get(key, (key,))
    try:
        for n in names:
            if n in st.secrets:
                return st.secrets[n]
        for section in st.secrets.values():
            if hasattr(section, "keys"):
                for n in names:
                    if n in section:
                        st.session_state["_secret_nested_warning"] = n
                        return section[n]
    except Exception:
        pass
    return default


def github_config():
    return _secret("github_token"), _secret("github_repo")


def push_to_github(quiet=False):
    """Commit a consistent snapshot of the database to the repo.

    Failures are stored in session state and shown by persistence_banner() on
    every page, because the page reruns right after a save and an inline warning
    would vanish before anyone read it.
    """
    token, repo = github_config()
    if not ghstore.configured(token, repo):
        return False
    if BOOT.get("gh_load_failed"):
        # The stored copy couldn't be loaded at startup, so this session may be
        # running on an empty or seed database. Saving now would overwrite the
        # real data in the repo - refuse until a load succeeds.
        st.session_state["gh_error"] = ("Saving is paused: the app couldn't load the stored database "
                                        f"at startup ({BOOT['gh_load_failed']}), and saving now could "
                                        "overwrite it. Manage → Data → Retry loading from GitHub.")
        return False
    try:
        with st.spinner("Saving to GitHub…"):
            sha = ghstore.push_bytes(token, repo, db.snapshot_bytes(conn),
                                     message=f"Tracker update {now_local():%Y-%m-%d %H:%M}")
        stamp = now_local().strftime("%Y-%m-%d %H:%M")
        db.set_setting(conn, "gh_last_push", stamp)
        st.session_state.pop("gh_error", None)
        st.session_state["gh_last_ok"] = f"{stamp} · commit {str(sha)[:7]}"
        if not quiet:
            toast("Saved to GitHub", "💾")
        return True
    except Exception as e:
        st.session_state["gh_error"] = f"{now_local():%H:%M} — {e}"
        toast("GitHub save FAILED — see the red banner", "🚨")
        return False


def try_autosync(quiet=False):
    """Persist after a write. GitHub first (that's what survives a restart),
    then Google Sheets if it's configured. Never blocks the save itself."""
    bump()
    push_to_github(quiet=quiet)
    if db.get_setting(conn, "gs_autosync") != "1":
        return
    url, creds = db.get_setting(conn, "gs_sheet"), db.get_setting(conn, "gs_creds")
    if not (url and creds):
        return
    try:
        with st.spinner("Syncing to Google Sheets…"):
            gsheets.sync(conn, url, creds)
            store.push(conn, url, creds)
        db.set_setting(conn, "gs_last_sync", now_local().strftime("%Y-%m-%d %H:%M"))
        if not quiet:
            toast("Synced to Google Sheets", "☁️")
    except Exception as e:  # sync problems must never lose an import
        st.warning(f"Saved locally, but the Google Sheets sync failed: {e}")


def full_sync():
    """Manual sync: reports + full-table backup. Returns (url, tables) or raises."""
    url, creds = db.get_setting(conn, "gs_sheet"), db.get_setting(conn, "gs_creds")
    link = gsheets.sync(conn, url, creds)
    n = store.push(conn, url, creds)
    db.set_setting(conn, "gs_last_sync", now_local().strftime("%Y-%m-%d %H:%M"))
    bump()
    return link, n


def selected_rows(event):
    """Row indices a user clicked in a selectable st.dataframe, or []."""
    try:
        return list(event.selection.rows)
    except Exception:
        return []


def resolve_api_key():
    """(key, source). Secrets win, then the environment, then a key typed into
    Settings — a stale key saved in the database must never shadow the Secret."""
    from siegestats import reader
    k = reader.clean_key(_secret("ANTHROPIC_API_KEY"))
    if k:
        return k, "Secrets"
    k = reader.clean_key(os.environ.get("ANTHROPIC_API_KEY"))
    if k:
        return k, "environment"
    k = reader.clean_key(db.get_setting(conn, "api_key"))
    if k:
        return k, "Settings (stored in this session)"
    return "", None
