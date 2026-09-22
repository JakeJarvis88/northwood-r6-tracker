"""Manage page: roster, map pool, teams, data, settings."""
import os

import pandas as pd
import streamlit as st

from siegestats import db, ghstore, reader, stats, export_excel, sides, rating, store, operators
from ui.common import (conn, our_id, series_label, list_series,
                       roster_names, player_id_by_name, toast, try_autosync, full_sync, bump,
                       github_config, push_to_github)

def render_manage():
    st.title("⚙️ Manage")
    t = st.tabs(["Roster & Aliases", "Map Pool", "Teams", "Data", "Settings"])

    with t[0]:
        st.markdown("**Northwood roster**")
        pr = db.roster(conn, team_id=our_id())
        st.dataframe(pd.DataFrame([dict(r) for r in pr])[["player_id", "name", "role", "active"]],
                     width="stretch", hide_index=True)
        with st.form("addp", clear_on_submit=True):
            c1, c2, c3 = st.columns([2, 2, 1])
            nm = c1.text_input("New player name")
            rl = c2.text_input("Role")
            if c3.form_submit_button("Add player") and nm.strip():
                conn.execute("INSERT INTO players(name, team_id, role) VALUES(?,?,?)", (nm.strip(), our_id(), rl))
                conn.commit()
                bump()
                st.rerun()
        pick = st.selectbox("Toggle active/inactive", ["—"] + [f"{r['player_id']}: {r['name']}" for r in pr])
        if pick != "—" and st.button("Toggle"):
            pid = int(pick.split(":")[0])
            conn.execute("UPDATE players SET active = 1-active WHERE player_id=?", (pid,))
            conn.commit()
            bump()
            st.rerun()

        st.markdown("---")
        st.markdown("**Aliases** (a player can hold any number of historical gamertags)")
        al = db.alias_table(conn, team_id=our_id())
        st.dataframe(pd.DataFrame([dict(r) for r in al])[["player_name", "gamertag", "status", "first_seen", "last_seen"]],
                     width="stretch", hide_index=True)
        pending = [r for r in al if r["status"] == "pending"]
        for r in pending:
            c1, c2, c3 = st.columns([3, 1, 1])
            c1.write(f"⏳ `{r['gamertag']}` → **{r['player_name']}**")
            if c2.button("Approve", key=f"ap{r['alias_id']}"):
                conn.execute("UPDATE aliases SET status='confirmed' WHERE alias_id=?", (r["alias_id"],))
                conn.commit()
                bump()
                st.rerun()
            if c3.button("Reject", key=f"rj{r['alias_id']}"):
                conn.execute("DELETE FROM aliases WHERE alias_id=?", (r["alias_id"],))
                conn.commit()
                bump()
                st.rerun()
        with st.form("adda", clear_on_submit=True):
            c1, c2, c3 = st.columns([2, 2, 1])
            who = c1.selectbox("Player", roster_names())
            tag = c2.text_input("Gamertag")
            if c3.form_submit_button("Add alias") and tag.strip():
                db.add_alias(conn, player_id_by_name(who), tag.strip())
                st.rerun()

    with t[1]:
        mp = pd.read_sql_query("SELECT id, map_name, season, active FROM map_pool ORDER BY map_name", conn)
        edited = st.data_editor(mp, num_rows="dynamic", width="stretch", hide_index=True,
                                column_config={"id": st.column_config.NumberColumn(disabled=True)})
        if st.button("Save map pool"):
            conn.execute("DELETE FROM map_pool")
            for _, r in edited.iterrows():
                if str(r["map_name"]).strip():
                    conn.execute("INSERT OR IGNORE INTO map_pool(map_name, season, active) VALUES(?,?,?)",
                                 (str(r["map_name"]).strip(), r["season"] or "ALL", int(r["active"] or 0)))
            conn.commit()
            bump()
            toast("Saved", "💾")

    with t[2]:
        tm = pd.read_sql_query("SELECT team_id, name, is_us FROM teams ORDER BY is_us DESC, name", conn)
        st.dataframe(tm, width="stretch", hide_index=True)
        with st.form("alt", clear_on_submit=True):
            c1, c2, c3 = st.columns([2, 2, 1])
            team = c1.selectbox("Team", tm["name"].tolist())
            alt = c2.text_input("Alternate name (e.g. 'Cumberland Uni')")
            if c3.form_submit_button("Add alternate name") and alt.strip():
                tid = int(tm[tm["name"] == team]["team_id"].iloc[0])
                conn.execute("INSERT INTO team_aliases(team_id, alt_name) VALUES(?,?)", (tid, alt.strip()))
                conn.commit()
                bump()
                toast("Alternate name added", "➕")

    with t[3]:
        st.markdown("**GitHub storage** — what keeps your data between restarts")
        gh_token, gh_repo = github_config()
        if not ghstore.configured(gh_token, gh_repo):
            st.warning("Not configured. Without it, everything you import is lost whenever the "
                       "app restarts or sleeps. Add `github_token` and `github_repo` to the app's "
                       "Secrets — see NO_PYTHON_SETUP.md.", icon="⚠️")
        else:
            ok, why = ghstore.check(gh_token, gh_repo)
            (st.success if ok else st.error)(why)
            exists, size, _ = (False, 0, None)
            if ok:
                try:
                    exists, size, _ = ghstore.remote_info(gh_token, gh_repo)
                except Exception:
                    pass
            st.caption(f"Repo: `{gh_repo}` · stored copy: "
                       + (f"{size // 1024} KB" if exists else "none yet")
                       + f" · last save: {db.get_setting(conn, 'gh_last_push') or 'never'}")
            gc1, gc2 = st.columns(2)
            if gc1.button("💾 Save database to GitHub now", disabled=not ok):
                if push_to_github():
                    st.success("Saved. It will be restored automatically after a restart.")
            if gc2.button("⬇️ Load database from GitHub", disabled=not (ok and exists),
                          help="Replaces this session's data with the stored copy."):
                try:
                    n = ghstore.pull(gh_token, gh_repo, db.DB_PATH)
                    st.cache_resource.clear()
                    st.cache_data.clear()
                    bump()
                    toast(f"Loaded {n // 1024} KB from GitHub", "⬇️")
                    st.rerun()
                except Exception as e:
                    st.error(f"Load failed: {e}")
        st.markdown("---")
        st.markdown("**Google Sheets** (optional — a readable copy for the team)")
        gs_url, gs_creds = db.get_setting(conn, "gs_sheet"), db.get_setting(conn, "gs_creds")
        last = db.get_setting(conn, "gs_last_sync")
        st.caption(f"Last sync: {last or 'never'}" + ("" if gs_url else " · configure it under Settings first"))
        st.caption("Sync writes both the readable report tabs and a complete `_db_` backup of "
                   "every table — that backup is what a cloud deployment restores from on boot.")
        cbtn1, cbtn2 = st.columns(2)
        if cbtn2.button("⬇️ Restore database FROM Sheets", disabled=not (gs_url and gs_creds),
                        help="Replaces everything local with the spreadsheet's _db_ backup."):
            try:
                loaded = store.pull(conn, gs_url, gs_creds)
                bump()
                toast(f"Restored {sum(loaded.values())} rows from the spreadsheet", "⬇️")
                st.rerun()
            except Exception as e:
                st.error(f"Restore failed: {e}")
        if cbtn1.button("🔄 Sync to Google Sheets now", disabled=not (gs_url and gs_creds)):
            try:
                with st.status("Syncing to Google Sheets…", expanded=True) as status:
                    st.write("Writing report tabs…")
                    url, ntab = full_sync()
                    st.write(f"Wrote {ntab} backup tables.")
                    status.update(label="Synced", state="complete", expanded=False)
                st.success(f"Synced reports + {ntab}-table backup. Team link: {url}")
                toast("Google Sheet updated", "☁️")
            except Exception as e:
                st.error(f"Sync failed: {e}")
        st.markdown("---")
        st.markdown("**Export Excel report**")
        if st.button("📤 Build northwood_stats.xlsx"):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "northwood_stats.xlsx")
            export_excel.export(conn, path)
            with open(path, "rb") as f:
                st.download_button("Download northwood_stats.xlsx", f.read(), "northwood_stats.xlsx")
        st.markdown("---")
        st.markdown("**Side data** — fill in starting side + half-1 score for maps already imported")
        sd = pd.read_sql_query(
            """SELECT mp.map_game_id, s.date, t.name AS opponent, mp.map_name,
                      mp.rounds_won, mp.rounds_lost, mp.starting_side, mp.half1_won,
                      mp.half1_lost, mp.ot_starting_side
               FROM maps_played mp JOIN series s USING(series_id)
               LEFT JOIN teams t ON s.opponent_id=t.team_id ORDER BY s.date, mp.map_number""", conn)
        if sd.empty:
            st.caption("No maps stored yet.")
        else:
            ed = st.data_editor(
                sd, width="stretch", hide_index=True, key="sidedata",
                column_config={
                    "map_game_id": st.column_config.NumberColumn(disabled=True),
                    "date": st.column_config.TextColumn(disabled=True),
                    "opponent": st.column_config.TextColumn(disabled=True),
                    "map_name": st.column_config.TextColumn(disabled=True),
                    "rounds_won": st.column_config.NumberColumn(disabled=True),
                    "rounds_lost": st.column_config.NumberColumn(disabled=True),
                    "starting_side": st.column_config.SelectboxColumn(
                        "Northwood started", options=["ATK", "DEF"]),
                    "ot_starting_side": st.column_config.SelectboxColumn(
                        "OT started", options=["ATK", "DEF"]),
                    "half1_won": st.column_config.NumberColumn("H1 won", min_value=0, max_value=15),
                    "half1_lost": st.column_config.NumberColumn("H1 lost", min_value=0, max_value=15)})
            if st.button("💾 Save side data"):
                rph_s = int(db.get_setting(conn, "rounds_per_half", "6"))
                errs = []
                for _, r in ed.iterrows():
                    h1w_, h1l_ = r["half1_won"], r["half1_lost"]
                    h1w_ = None if pd.isna(h1w_) else int(h1w_)
                    h1l_ = None if pd.isna(h1l_) else int(h1l_)
                    err = sides.check_half_scores(r["rounds_won"], r["rounds_lost"], h1w_, h1l_, rph_s)
                    if err:
                        errs.append(f"{r['map_name']} ({r['date']}): {err}")
                        continue
                    conn.execute(
                        """UPDATE maps_played SET starting_side=?, ot_starting_side=?,
                           half1_won=?, half1_lost=? WHERE map_game_id=?""",
                        (None if pd.isna(r["starting_side"]) else r["starting_side"],
                         None if pd.isna(r["ot_starting_side"]) else r["ot_starting_side"],
                         h1w_, h1l_, int(r["map_game_id"])))
                conn.commit()
                for e in errs:
                    st.error(e)
                if not errs:
                    toast("Side data saved", "🗡️")
                try_autosync()
        st.markdown("---")
        st.markdown("### ✏️ Editing past matches")
        st.info("Everything about a saved match — map, score, sides, player lines, rounds, "
                "1vX/plants, operator bans, veto — is now on the **✏️ Edit Matches** page in the "
                "sidebar, one tab per map.", icon="👉")
        st.markdown("---")
        st.markdown("**Backup**")
        st.caption("The whole tracker lives in one file. Download it before a big change; "
                   "restoring is just dropping it back into data/.")
        dbp = db.DB_PATH
        cbk1, cbk2 = st.columns(2)
        with open(dbp, "rb") as f:
            cbk1.download_button("💾 Download siege.db backup", f.read(),
                                 f"siege_backup_{pd.Timestamp.now():%Y%m%d}.db")
        raw = stats.load_frame(conn)
        if not raw.empty:
            cbk2.download_button("📄 Download all rows (CSV)", raw.to_csv(index=False).encode(),
                                 "northwood_player_map_stats.csv", "text/csv")
        st.markdown("---")
        st.markdown("**Restore from a backup file**")
        st.caption("Upload a siege.db downloaded above — useful for moving data into a cloud "
                   "deployment, or rolling back. Replaces everything currently stored.")
        up = st.file_uploader("siege.db", type=["db"], key="dbrestore")
        if up is not None and st.button("⬆️ Restore from this file", type="secondary"):
            import shutil, sqlite3, tempfile
            try:
                with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                    tmp.write(up.getvalue())
                    tmp_path = tmp.name
                probe = sqlite3.connect(tmp_path)
                n = probe.execute("SELECT COUNT(*) FROM series").fetchone()[0]
                probe.close()
                conn.commit()
                shutil.copyfile(tmp_path, db.DB_PATH)
                st.cache_resource.clear()
                st.cache_data.clear()
                bump()
                st.success(f"Restored {n} series. Reloading…")
                st.rerun()
            except Exception as e:
                st.error(f"That file isn't a valid tracker database: {e}")
        st.markdown("---")
        st.markdown("**Delete a map or series** (permanent)")
        srs = list_series()
        spick = st.selectbox("Series", ["—"] + [series_label(r) for r in srs])
        if spick != "—":
            srow = srs[[series_label(r) for r in srs].index(spick)]
            maps = conn.execute("SELECT map_game_id, map_name, rounds_won, rounds_lost FROM maps_played "
                                "WHERE series_id=? ORDER BY map_number", (srow["series_id"],)).fetchall()
            mpick = st.selectbox("Map (or whole series)", ["Whole series"] +
                                 [f"#{m['map_game_id']} {m['map_name']} ({m['rounds_won']}-{m['rounds_lost']})" for m in maps])
            if st.button("🗑️ Delete", type="secondary"):
                if mpick == "Whole series":
                    db.delete_series(conn, srow["series_id"])
                else:
                    db.delete_map(conn, int(mpick.split(" ")[0][1:]))
                st.rerun()

    with t[4]:
        st.markdown("**Screenshot reader** (Anthropic API)")
        key = st.text_input("API key", value=db.get_setting(conn, "api_key", ""), type="password",
                            help="Stored in plain text inside data/siege.db on this machine. "
                                 "Leave blank to use the ANTHROPIC_API_KEY environment variable instead.")
        model_opts = ["claude-sonnet-5", "claude-haiku-4-5", "claude-opus-5"]
        cur_model = db.get_setting(conn, "model", reader.DEFAULT_MODEL)
        if cur_model not in model_opts:
            model_opts.insert(0, cur_model)
        model = st.selectbox("Model", model_opts, index=model_opts.index(cur_model))
        st.caption("Sonnet is the default — about two cents a scoreboard, so a full season is "
                   "roughly a dollar. Billing is pay-as-you-go per request; there is no "
                   "subscription and no monthly minimum. Haiku is cheaper if you ever want it.")
        if st.button("Save settings"):
            db.set_setting(conn, "api_key", key.strip())
            db.set_setting(conn, "model", model.strip() or reader.DEFAULT_MODEL)
            st.success("Saved.")
        st.markdown("---")
        st.markdown("**Player rating** — baselines and weights")
        rcfg = rating.load_config(conn)
        rc1, rc2 = st.columns(2)
        with rc1:
            st.caption("Baselines (what counts as a 1.00 performance)")
            b_kpr = st.number_input("KPR baseline", 0.1, 3.0, rcfg["kpr_base"], 0.05)
            b_srv = st.number_input("Survival % baseline", 1.0, 90.0, rcfg["srv_base"], 1.0)
            b_kd = st.number_input("K/D baseline", 0.1, 3.0, rcfg["kd_base"], 0.05)
            b_ppr = st.number_input("Plants per round baseline", 0.01, 1.0, rcfg["ppr_base"], 0.01)
            b_cl = st.number_input("1vX per map baseline", 0.05, 3.0, rcfg["clutch_base"], 0.05)
            cap_v = st.number_input("Component cap (× baseline)", 1.0, 10.0,
                                    float(rcfg.get("cap", 2.5)), 0.5,
                                    help="Stops one huge component (a lone clutch in a small "
                                         "sample) from dominating. Set high to disable.")
        with rc2:
            st.caption("Weights (they get normalized, so relative size is what matters)")
            w_kpr = st.number_input("KPR weight", 0.0, 1.0, rcfg["w_kpr"], 0.01)
            w_srv = st.number_input("Survival weight", 0.0, 1.0, rcfg["w_srv"], 0.01)
            w_kd = st.number_input("K/D weight", 0.0, 1.0, rcfg["w_kd"], 0.01)
            w_pl = st.number_input("Plants weight", 0.0, 1.0, rcfg["w_plants"], 0.01)
            w_cl = st.number_input("1vX weight", 0.0, 1.0, rcfg["w_clutch"], 0.01)
        if st.button("Save rating settings"):
            rating.save_config(conn, {"kpr_base": b_kpr, "srv_base": b_srv, "kd_base": b_kd,
                                      "ppr_base": b_ppr, "clutch_base": b_cl,
                                      "w_kpr": w_kpr, "w_srv": w_srv, "w_kd": w_kd,
                                      "w_plants": w_pl, "w_clutch": w_cl, "cap": cap_v})
            st.success("Saved.")
        if st.button("Reset rating settings to defaults"):
            rating.save_config(conn, rating.DEFAULTS)
            st.rerun()
        st.markdown("---")
        st.markdown("**Operator list** — what the ban dropdowns offer")
        st.caption("Ubisoft adds operators every season. Edit either list and the ban grids "
                   "update; the grids also keep any custom name already saved on a map.")
        ops_now = operators.load(conn)
        oc1, oc2 = st.columns(2)
        atk_txt = oc1.text_area("Attackers (one per line)", "\n".join(ops_now["ATK"]), height=200)
        def_txt = oc2.text_area("Defenders (one per line)", "\n".join(ops_now["DEF"]), height=200)
        ob1, ob2 = st.columns(2)
        if ob1.button("Save operator list"):
            operators.save(conn, atk_txt.splitlines(), def_txt.splitlines())
            bump()
            toast("Operator list saved", "🎭")
            st.rerun()
        if ob2.button("Reset to built-in list"):
            operators.reset(conn)
            bump()
            st.rerun()
        st.markdown("---")
        st.markdown("**Side-swap rules** (used for attack/defense splits)")
        cs1, cs2 = st.columns(2)
        rph_v = cs1.number_input("Rounds per half (regulation)", 1, 15,
                                 int(db.get_setting(conn, "rounds_per_half", "6")))
        orph_v = cs2.number_input("Rounds per overtime half", 1, 10,
                                  int(db.get_setting(conn, "ot_rounds_per_half", "3")))
        if st.button("Save side-swap rules"):
            db.set_setting(conn, "rounds_per_half", str(int(rph_v)))
            db.set_setting(conn, "ot_rounds_per_half", str(int(orph_v)))
            st.success("Saved.")
        st.markdown("---")
        st.markdown("**Google Sheets** (one-way publish; the local database stays the source of truth)")
        st.caption("Setup: Google Cloud project → enable Sheets + Drive APIs → create a service account "
                   "→ download its JSON key → share your Sheet (Editor) with the service account's email. "
                   "Full steps are in the README.")
        gs_sheet = st.text_input("Spreadsheet URL (or key)", value=db.get_setting(conn, "gs_sheet", ""))
        gs_creds = st.text_input("Service-account JSON path", value=db.get_setting(conn, "gs_creds", "google_creds.json"))
        gs_auto = st.checkbox("Auto-sync after every confirmed import",
                              value=db.get_setting(conn, "gs_autosync") == "1")
        if st.button("Save Google Sheets settings"):
            db.set_setting(conn, "gs_sheet", gs_sheet.strip())
            db.set_setting(conn, "gs_creds", gs_creds.strip())
            db.set_setting(conn, "gs_autosync", "1" if gs_auto else "0")
            st.success("Saved. Use Data → Sync now to test the connection.")
