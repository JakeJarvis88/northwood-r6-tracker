"""Northwood R6 Stat Tracker - Streamlit app.

Run with:  streamlit run app.py
Pages: Import Match | Dashboards | Manage
"""
import json
import os

import streamlit as st

from siegestats import db, store
from ui.common import conn


st.set_page_config(page_title="Northwood R6 Tracker", page_icon="🎯", layout="wide")

# Tactile button feedback: buttons dip and flash green on press, and the toast that
# follows a save slides in rather than appearing abruptly.
st.markdown("""
<style>
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
    transition: transform .06s ease, background-color .15s ease, border-color .15s ease,
                box-shadow .15s ease;
}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {
    border-color: #3FB68B; box-shadow: 0 0 0 1px rgba(63,182,139,.35);
}
.stButton > button:active, .stDownloadButton > button:active, .stFormSubmitButton > button:active {
    transform: translateY(2px) scale(.985);
    background-color: #3FB68B !important; border-color: #3FB68B !important;
    color: #0b1b15 !important; box-shadow: 0 0 0 3px rgba(63,182,139,.28);
}
.stButton > button:focus:not(:active) { border-color: #3FB68B; }
div[data-testid="stToast"] { animation: nwslide .22s ease-out; }
@keyframes nwslide { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
div[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
</style>
""", unsafe_allow_html=True)

UNASSIGNED = "(unassigned / opponent)"
MATCH_TYPES = ["Gameday", "Scrim"]


def _secret(key, default=None):
    """Read from Streamlit secrets (cloud) or fall back to the local database."""
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return default


def login_gate():
    """Optional password gate. With no passwords configured (running locally)
    everything is open; on a shared deployment set them in Streamlit secrets."""
    editor_pw = _secret("editor_password")
    viewer_pw = _secret("viewer_password")
    if not editor_pw and not viewer_pw:
        st.session_state["role"] = "editor"
        return "editor"
    if st.session_state.get("role"):
        return st.session_state["role"]
    st.title("🎯 Northwood R6 Tracker")
    st.caption("Enter the team password.")
    pw = st.text_input("Password", type="password")
    if st.button("Enter", type="primary"):
        if editor_pw and pw == editor_pw:
            st.session_state["role"] = "editor"
            st.rerun()
        elif viewer_pw and pw == viewer_pw:
            st.session_state["role"] = "viewer"
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()


@st.cache_resource
def bootstrap_creds():
    """Streamlit Cloud has no file uploads, so materialize the service-account
    JSON from secrets into a file gspread can open."""
    try:
        blob = st.secrets.get("gcp_service_account")
    except Exception:
        blob = None
    if not blob:
        return None
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "google_creds.json")
    try:
        with open(path, "w") as f:
            json.dump(dict(blob), f)
        return path
    except OSError:
        return None


@st.cache_resource
def cloud_bootstrap():
    """A fresh container has an empty disk. Restore from the Sheets backup if one
    exists; otherwise lay down the roster/map pool so the app is usable at once."""
    sheet = _secret("gs_sheet") or db.get_setting(conn, "gs_sheet")
    creds = bootstrap_creds() or _secret("gs_creds_path") or db.get_setting(conn, "gs_creds")
    if sheet and creds:  # make them available to the rest of the app too
        db.set_setting(conn, "gs_sheet", str(sheet))
        db.set_setting(conn, "gs_creds", str(creds))
    msg = None
    if sheet and creds and os.path.exists(str(creds)):
        try:
            if store.is_empty(conn) and store.has_backup(sheet, creds):
                loaded = store.pull(conn, sheet, creds)
                return f"Restored from the team spreadsheet ({sum(loaded.values())} rows)."
        except Exception as e:
            msg = f"Could not restore from the spreadsheet: {e}"
    if db.our_team_id(conn) is None:
        try:
            import seed
            seed.main()
            return (msg + " " if msg else "") + "First run — roster and map pool created."
        except Exception as e:
            return (msg + " " if msg else "") + f"Could not seed the roster: {e}"
    return msg


ROLE = login_gate()
_boot = cloud_bootstrap()
if _boot:
    st.sidebar.caption(_boot)
if db.our_team_id(conn) is None:
    st.warning("No home team found - run `python seed.py` first (or add your team under Manage).")

pages = ["📥 Import Match", "📊 Dashboards", "⚙️ Manage"] if ROLE == "editor" else ["📊 Dashboards"]
if "_gopage" in st.session_state:
    st.session_state["page_nav"] = st.session_state.pop("_gopage")
page = st.sidebar.radio("Page", pages, key="page_nav")
if ROLE == "viewer":
    st.sidebar.caption("Viewing as a teammate — dashboards are read-only. "
                       "Ask Jon for the editor password to import matches.")
if _secret("editor_password") or _secret("viewer_password"):
    if st.sidebar.button("Log out"):
        st.session_state.pop("role", None)
        st.rerun()
st.sidebar.markdown("---")

from ui import import_page, dashboards, manage  # noqa: E402  (after auth + bootstrap)

if page.startswith("📥"):
    import_page.render_import()
elif page.startswith("📊"):
    dashboards.render_dashboards()
else:
    manage.render_manage()
