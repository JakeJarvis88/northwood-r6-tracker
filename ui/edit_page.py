"""Edit Matches page.

Pick a series, then a map. Everything about that map — score, sides, every
player's line, the round-by-round record, 1vX/plants, operator bans — is edited
and saved here, independently of the other maps in the same series. The only
things shared across a BO3 are the series details and the map veto, which sit
at the top.
"""
import pandas as pd
import streamlit as st

from siegestats import db, rating, sides
from ui.common import (conn, UNASSIGNED, series_label, list_series, map_options,
                       roster_names, player_id_by_name, to_int, setting_int, toast,
                       try_autosync, bump, last_action_line, reset_widgets)
from ui.import_page import edit_series_form, veto_editor, opban_grid


def render_edit():
    st.title("✏️ Edit Matches")
    last_action_line()
    srs = list_series()
    if not srs:
        st.info("No series yet — import a match first.")
        return
    labels = [series_label(r) for r in srs]
    default = st.session_state.get("edit_series_label")
    pick = st.selectbox("Series", labels, index=labels.index(default) if default in labels else 0,
                        key="edit_series_pick")
    st.session_state["edit_series_label"] = pick
    series = srs[labels.index(pick)]
    sid, opp_id, opp_name = series["series_id"], series["opponent_id"], series["opponent"]

    st.markdown("### Series (shared by every map)")
    edit_series_form(series)
    with st.expander("🗺️ Map veto (ban / pick order)"):
        veto_editor(sid, opp_id, opp_name)

    st.markdown("### Maps (each one saves on its own)")
    maps = [dict(r) for r in conn.execute(
        """SELECT * FROM maps_played WHERE series_id=? ORDER BY map_number, map_game_id""",
        (sid,)).fetchall()]
    if not maps:
        st.caption("No maps in this series yet.")
        return
    tab_labels = [f"Map {m['map_number']} · {m['map_name']} ({m['rounds_won']}-{m['rounds_lost']}) #{m['map_game_id']}"
                  for m in maps]
    tabs = st.tabs(tab_labels)
    for tab, m in zip(tabs, maps):
        with tab:
            map_editor(m, opp_id, opp_name)


# ================================================================== one map
def map_editor(m, opp_id, opp_name):
    mg = m["map_game_id"]
    k = f"m{mg}"
    rph = setting_int("rounds_per_half", 6)

    # ---- 1. map & score ---------------------------------------------------------
    st.markdown("**Map & score**")
    c1, c2, c3, c4, c5, c6 = st.columns([2, 1, 1, 1, 1, 1])
    opts = map_options()
    if m["map_name"] not in opts:
        opts = [m["map_name"]] + opts
    map_name = c1.selectbox("Map", opts, index=opts.index(m["map_name"]), key=f"{k}_map")
    map_no = c2.number_input("Map #", 1, 5, int(m["map_number"] or 1), key=f"{k}_no")
    rw = c3.number_input("Won", 0, 20, int(m["rounds_won"] or 0), key=f"{k}_rw")
    rl = c4.number_input("Lost", 0, 20, int(m["rounds_lost"] or 0), key=f"{k}_rl")
    side_opts = ["Unknown", "ATK", "DEF"]
    ss = c5.selectbox("Started", side_opts, index=side_opts.index(m["starting_side"] or "Unknown"),
                      key=f"{k}_ss")
    ots = c6.selectbox("OT start", side_opts, index=side_opts.index(m["ot_starting_side"] or "Unknown"),
                       key=f"{k}_ots", disabled=(rw + rl) <= rph * 2)
    c7, c8 = st.columns([1, 3])
    status = c7.selectbox("Status", ["confirmed", "in_progress", "needs_review"],
                          index=["confirmed", "in_progress", "needs_review"].index(m["import_status"] or "confirmed"),
                          key=f"{k}_st")
    mid = c8.text_input("Siege Match ID", value=m["siege_match_id"] or "", key=f"{k}_mid")
    if st.button("💾 Save map & score", key=f"{k}_save1"):
        res = "W" if rw > rl else ("L" if rl > rw else "T")
        conn.execute(
            """UPDATE maps_played SET map_name=?, map_number=?, rounds_won=?, rounds_lost=?, result=?,
               starting_side=?, ot_starting_side=?, import_status=?, siege_match_id=? WHERE map_game_id=?""",
            (map_name, int(map_no), int(rw), int(rl), res,
             None if ss == "Unknown" else ss, None if ots == "Unknown" else ots, status,
             mid.strip() or None, mg))
        conn.execute("UPDATE player_map_stats SET rounds_played=? WHERE map_game_id=? "
                     "AND (rounds_played IS NULL OR rounds_played=?)",
                     (int(rw + rl), mg, int((m["rounds_won"] or 0) + (m["rounds_lost"] or 0))))
        if ss != "Unknown":  # relabel sides on the stored rounds
            for r in conn.execute("SELECT id, round_number FROM round_results WHERE map_game_id=?", (mg,)).fetchall():
                conn.execute("UPDATE round_results SET side=? WHERE id=?",
                             (sides.side_for_round(r["round_number"], ss, None if ots == "Unknown" else ots, rph),
                              r["id"]))
        conn.commit()
        db.recalc_series(conn, m["series_id"])
        toast(f"Map #{mg} saved", "🎯")
        try_autosync()
        st.rerun()

    # ---- 2. players ---------------------------------------------------------------
    st.markdown("**Players** — one row each; edit any number, or reassign a gamertag")
    prow = pd.read_sql_query(
        """SELECT pms.id, t.is_us, pms.gamertag_displayed AS Gamertag, p.name AS assigned,
                  pms.score AS Score, pms.kills AS K, pms.deaths AS D, pms.assists AS A,
                  pms.rounds_played AS Rounds
           FROM player_map_stats pms LEFT JOIN teams t ON pms.team_id=t.team_id
           LEFT JOIN players p ON pms.player_id=p.player_id
           WHERE pms.map_game_id=? ORDER BY t.is_us DESC, pms.score DESC""", conn, params=(mg,))
    if prow.empty:
        st.caption("No player rows on this map.")
    else:
        prow["Team"] = prow["is_us"].map({1: "Northwood", 0: opp_name or "Opponent"}).fillna("?")
        prow["Assign to"] = prow.apply(
            lambda r: (r["assigned"] if pd.notna(r["assigned"]) else UNASSIGNED) if r["is_us"] == 1 else UNASSIGNED, axis=1)
        show = prow[["id", "Team", "Assign to", "Gamertag", "Score", "K", "D", "A", "Rounds"]]
        ped = st.data_editor(
            show, key=f"{k}_players", width="stretch", hide_index=True,
            column_config={"id": None,
                           "Team": st.column_config.TextColumn(disabled=True),
                           "Assign to": st.column_config.SelectboxColumn(options=[UNASSIGNED] + roster_names()),
                           "Score": st.column_config.NumberColumn(min_value=0, max_value=20000, step=1),
                           "K": st.column_config.NumberColumn(min_value=0, max_value=60, step=1),
                           "D": st.column_config.NumberColumn(min_value=0, max_value=30, step=1),
                           "A": st.column_config.NumberColumn(min_value=0, max_value=30, step=1),
                           "Rounds": st.column_config.NumberColumn(min_value=0, max_value=30, step=1)})
        if st.button("💾 Save players", key=f"{k}_save2"):
            for _, r in ped.iterrows():
                is_ours = r["Team"] == "Northwood"
                pid = player_id_by_name(r["Assign to"]) if (is_ours and r["Assign to"] != UNASSIGNED) else None
                conn.execute(
                    """UPDATE player_map_stats SET gamertag_displayed=?, player_id=?, score=?, kills=?,
                       deaths=?, assists=?, rounds_played=? WHERE id=?""",
                    (str(r["Gamertag"]).strip(), pid, to_int(r["Score"]), to_int(r["K"]), to_int(r["D"]),
                     to_int(r["A"]), to_int(r["Rounds"]), int(r["id"])))
                if pid:
                    db.add_alias(conn, pid, str(r["Gamertag"]).strip(), status="confirmed")
            conn.commit()
            reset_widgets(f"{k}_players")
            toast("Player lines saved", "👥")
            try_autosync()
            st.rerun()

    # ---- 3. rounds ----------------------------------------------------------------
    st.markdown("**Round by round**")
    rr = pd.read_sql_query(
        """SELECT id, round_number AS Round, won, side AS Side, win_condition AS How, source AS Source
           FROM round_results WHERE map_game_id=? ORDER BY round_number""", conn, params=(mg,))
    if rr.empty:
        st.caption("No round data stored. Add rows below (or re-import the screenshot).")
        rr = pd.DataFrame(columns=["id", "Round", "won", "Side", "How", "Source"])
    rr["Result"] = rr["won"].map({1: "Won", 0: "Lost"}).fillna("?")
    red = st.data_editor(
        rr[["id", "Round", "Result", "Side", "How", "Source"]], key=f"{k}_rounds", width="stretch",
        hide_index=True, num_rows="dynamic",
        column_config={"id": None,
                       "Round": st.column_config.NumberColumn(min_value=1, max_value=15, step=1),
                       "Result": st.column_config.SelectboxColumn(options=["Won", "Lost", "?"]),
                       "Side": st.column_config.SelectboxColumn(options=["ATK", "DEF"]),
                       "How": st.column_config.SelectboxColumn(
                           options=["elimination", "objective", "time", "unknown"]),
                       "Source": st.column_config.TextColumn(disabled=True)})
    r1, r2, r3 = st.columns(3)
    if r1.button("💾 Save rounds", key=f"{k}_save3"):
        rows = []
        for _, r in red.iterrows():
            if pd.isna(r["Round"]):
                continue
            rows.append({"round_number": int(r["Round"]),
                         "won": 1 if r["Result"] == "Won" else (0 if r["Result"] == "Lost" else None),
                         "side": r["Side"] if r["Side"] in ("ATK", "DEF") else None,
                         "win_condition": sides.normalize_condition(r["How"]),
                         "confidence": 1.0, "source": r["Source"] if pd.notna(r["Source"]) else "manual"})
        db.save_round_results(conn, mg, rows, source="manual")
        reset_widgets(f"{k}_rounds")
        toast(f"{len(rows)} rounds saved", "📊")
        try_autosync()
        st.rerun()
    if r2.button("🔧 Fit rounds to the score", key=f"{k}_fit",
                 help="Adds/flips rounds so the record matches the saved score, then relabels sides"):
        rows = [{"round_number": int(r["Round"]),
                 "won": 1 if r["Result"] == "Won" else (0 if r["Result"] == "Lost" else None),
                 "side": r["Side"], "win_condition": r["How"], "confidence": 0.9,
                 "source": r["Source"] if pd.notna(r["Source"]) else "manual"}
                for _, r in red.iterrows() if pd.notna(r["Round"])]
        rows, notes = sides.reconcile_rounds(rows, m["rounds_won"], m["rounds_lost"])
        if m["starting_side"]:
            rows = sides.apply_sides(rows, m["starting_side"], m["ot_starting_side"], rph)
        db.save_round_results(conn, mg, rows)
        reset_widgets(f"{k}_rounds")
        bump()
        toast("; ".join(notes) if notes else "Rounds already match the score", "🔧")
        st.rerun()
    if r3.button("↔️ Relabel sides from 'Started'", key=f"{k}_relabel",
                 disabled=not m["starting_side"]):
        for r in conn.execute("SELECT id, round_number FROM round_results WHERE map_game_id=?", (mg,)).fetchall():
            conn.execute("UPDATE round_results SET side=? WHERE id=?",
                         (sides.side_for_round(r["round_number"], m["starting_side"], m["ot_starting_side"], rph),
                          r["id"]))
        conn.commit()
        bump()
        toast("Sides relabeled", "↔️")
        st.rerun()

    # ---- 4. 1vX & plants ----------------------------------------------------------
    st.markdown("**1vX & plants (Northwood)**")
    man = pd.read_sql_query(
        """SELECT p.player_id, p.name AS Player, ms.clutches AS "1vX", ms.plants AS Plants
           FROM player_map_stats pms JOIN players p ON pms.player_id=p.player_id
           LEFT JOIN manual_stats ms ON ms.map_game_id=pms.map_game_id AND ms.player_id=pms.player_id
           WHERE pms.map_game_id=? ORDER BY p.name""", conn, params=(mg,))
    if man.empty:
        st.caption("Assign Northwood players in the table above first.")
    else:
        med = st.data_editor(
            man[["player_id", "Player", "1vX", "Plants"]], key=f"{k}_man", width="stretch", hide_index=True,
            column_config={"player_id": None,
                           "Player": st.column_config.TextColumn(disabled=True),
                           "1vX": st.column_config.SelectboxColumn(options=list(range(0, 15))),
                           "Plants": st.column_config.SelectboxColumn(options=list(range(0, 15)))})
        if st.button("💾 Save 1vX & plants", key=f"{k}_save4"):
            rating.save_manual(conn, mg, [
                {"player_id": int(r["player_id"]), "clutches": to_int(r["1vX"]), "plants": to_int(r["Plants"])}
                for _, r in med.iterrows()])
            reset_widgets(f"{k}_man")
            toast("1vX and plants saved", "✋")
            try_autosync()
            st.rerun()

    # ---- 5. operator bans ----------------------------------------------------------
    st.markdown("**Operator bans**")
    opban_grid(mg, opp_id, opp_name, key=f"edit{mg}")

    # ---- 6. danger -------------------------------------------------------------------
    with st.expander("🗑️ Delete this map"):
        st.warning("Removes this map, its player lines, rounds, 1vX/plants and operator bans. "
                   "The series and its veto stay.")
        if st.checkbox("Yes, delete it", key=f"{k}_delsure") and st.button("Delete map", key=f"{k}_del"):
            db.delete_map(conn, mg)
            bump()
            toast(f"Map #{mg} deleted", "🗑️")
            try_autosync()
            st.rerun()
