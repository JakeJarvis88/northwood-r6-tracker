"""Import Match page: series, screenshots, review cards, veto, finalize."""
import os
from datetime import date

import pandas as pd
import streamlit as st

from siegestats import db, matching, reader, dedupe, gsheets, insights, sides, rating, opbans, operators
from ui.common import (conn, our_id, UNASSIGNED, MATCH_TYPES, series_label, list_series,
                       roster_names, player_id_by_name, map_options, to_int, toast, try_autosync, bump)

def edit_series_form(series):
    """Edit an existing series: opponent, competition, date, format, match type, etc."""
    sid = series["series_id"]
    with st.expander("✏️ Edit this series (opponent, competition, date, type…)"):
        teams = [r["name"] for r in conn.execute(
            "SELECT name FROM teams WHERE is_us=0 ORDER BY name").fetchall()]
        cur_opp = series["opponent"]
        opts = teams + ["➕ new team…"]
        idx = opts.index(cur_opp) if cur_opp in teams else 0
        with st.form(f"edit_series_{sid}"):
            c1, c2, c3 = st.columns(3)
            opp_pick = c1.selectbox("Opponent", opts, index=idx)
            new_opp = c1.text_input("New opponent name", value="",
                                    help="Only used when '➕ new team…' is selected above.")
            sdate = c2.date_input("Date", value=date.fromisoformat(str(series["date"])[:10]))
            fmt = c3.selectbox("Format", ["BO1", "BO3", "Other"],
                               index=["BO1", "BO3", "Other"].index(series["format"])
                               if series["format"] in ("BO1", "BO3", "Other") else 1)
            c4, c5, c6 = st.columns(3)
            cur_mt = series["match_type"] if "match_type" in series.keys() and series["match_type"] else "Gameday"
            mtype = c4.selectbox("Match type", MATCH_TYPES, index=MATCH_TYPES.index(cur_mt))
            season = c5.text_input("Season", value=series["season"] or "")
            comp = c6.text_input("Competition/League", value=series["competition"] or "")
            vod = st.text_input("VOD link", value=series["vod"] or "")
            notes = st.text_area("Notes", value=series["notes"] or "", height=70)
            st.caption("Renaming the opponent here re-points this series only. To merge two spellings "
                       "of the same team, add the alternate name under Manage → Teams.")
            if st.form_submit_button("💾 Save series details"):
                name = new_opp.strip() if opp_pick == "➕ new team…" else opp_pick
                if not name:
                    st.error("Pick an opponent or type a new team name.")
                else:
                    oid = db.get_or_create_team(conn, name)
                    conn.execute(
                        """UPDATE series SET opponent_id=?, date=?, format=?, match_type=?,
                           season=?, competition=?, vod=?, notes=? WHERE series_id=?""",
                        (oid, sdate.isoformat(), fmt, mtype, season, comp, vod, notes, sid))
                    conn.commit()
                    try_autosync()
                    toast("Series details saved", "💾")
                    st.rerun()


def render_import():
    st.title("📥 Import Match")

    # ---- 1. series ----------------------------------------------------------
    st.subheader("1 · Series")
    srs = list_series()
    choices = ["➕ New series…"] + [series_label(r) for r in srs]
    pick = st.selectbox("Add these screenshots to:", choices)
    if pick == "➕ New series…":
        with st.form("new_series"):
            c1, c2, c3 = st.columns(3)
            opp_name = c1.text_input("Opponent team")
            sdate = c2.date_input("Date", value=date.today())
            fmt = c3.selectbox("Format", ["BO1", "BO3", "Other"])
            c4, c5, c6 = st.columns(3)
            mtype = c4.selectbox("Match type", MATCH_TYPES,
                                 help="Scrims are tracked separately so practice doesn't skew gameday stats.")
            season = c5.text_input("Season", value=db.get_setting(conn, "season", "2026-27"))
            comp = c6.text_input("Competition/League", value=db.get_setting(conn, "competition", ""))
            vod = st.text_input("VOD link", value="")
            if st.form_submit_button("Create series") and opp_name.strip():
                oid = db.get_or_create_team(conn, opp_name)
                conn.execute(
                    """INSERT INTO series(date, season, competition, opponent_id, format, vod, match_type)
                       VALUES(?,?,?,?,?,?,?)""",
                    (sdate.isoformat(), season, comp, oid, fmt, vod, mtype))
                conn.commit()
                db.set_setting(conn, "season", season)
                if comp:
                    db.set_setting(conn, "competition", comp)
                bump()
                toast(f"Series vs {opp_name.strip()} created", "➕")
                st.rerun()
        st.stop()
    series = srs[choices.index(pick) - 1]
    sid, opp_id, opp_name = series["series_id"], series["opponent_id"], series["opponent"]
    if "finalized" in series.keys() and series["finalized"]:
        st.info("✔ This series is finalized. Importing another map or editing it reopens it automatically.")
    edit_series_form(series)
    with st.expander("🗑️ Delete this entire series"):
        st.warning(f"Permanently removes this series vs {opp_name or '?'} — all its maps, "
                   "player stats, vetoes and operator bans.")
        sure = st.checkbox("Yes, delete it", key=f"delsure_{sid}")
        if st.button("Delete series permanently", type="secondary", disabled=not sure, key=f"delser_{sid}"):
            db.delete_series(conn, sid)
            st.session_state["extractions"] = {}
            bump()
            toast("Series deleted", "🗑️")
            st.rerun()

    # ---- 2. screenshots ------------------------------------------------------
    st.subheader("2 · Screenshots")
    api_key = db.get_setting(conn, "api_key") or os.environ.get("ANTHROPIC_API_KEY")
    model = db.get_setting(conn, "model", reader.DEFAULT_MODEL)
    if not api_key:
        st.info("No Anthropic API key configured (Manage → Settings), so automatic reading is off. "
                "You can still add maps manually below.")
    files = st.file_uploader("Drop scoreboard screenshot(s)", type=["png", "jpg", "jpeg", "webp"],
                             accept_multiple_files=True)
    st.session_state.setdefault("extractions", {})
    cols = st.columns(2)
    pending_files = [f for f in (files or []) if f.name not in st.session_state["extractions"]]
    if files and api_key and cols[0].button(
            f"🔍 Read {len(pending_files)} screenshot{'s' if len(pending_files) != 1 else ''}",
            type="primary", disabled=not pending_files):
        with st.status(f"Reading {len(pending_files)} screenshot(s) with {model}…", expanded=True) as status:
            ok = 0
            for i, f in enumerate(pending_files, 1):
                st.write(f"{i}/{len(pending_files)} · {f.name}")
                try:
                    ex = reader.read_screenshot(f.getvalue(), f.name, api_key=api_key, model=model)
                    st.session_state["extractions"][f.name] = ex
                    n_players = sum(len(t.get("players", [])) for t in ex.get("teams", []))
                    n_rounds = len((ex.get("round_strip") or {}).get("rounds") or [])
                    st.write(f"   ✓ {ex.get('map') or '?'} · {n_players} players · {n_rounds} rounds read")
                    ok += 1
                except reader.ReaderError as e:
                    st.write(f"   ✗ {e}")
            status.update(label=f"Read {ok}/{len(pending_files)} — review below", state="complete",
                          expanded=False)
        if ok:
            toast(f"{ok} screenshot(s) read — scroll down to review", "🔍")
    if cols[1].button("✍️ Add a manual (blank) map entry"):
        n = sum(1 for k in st.session_state["extractions"] if k.startswith("manual-"))
        st.session_state["extractions"][f"manual-{n + 1}"] = reader.blank_extraction()
        toast("Blank map added below — fill in the scoreboard", "✍️")
    if st.session_state["extractions"] and st.button("Clear all review cards", key="clear_ex"):
        st.session_state["extractions"] = {}
        st.rerun()

    # ---- 3. review & confirm -------------------------------------------------
    for fname, ex in list(st.session_state["extractions"].items()):
        if ex.get("_imported"):
            mg = ex["_imported"]
            cI, cU = st.columns([5, 1])
            cI.success(f"✅ {fname} imported (map #{mg}).")
            if isinstance(mg, int) and cU.button("↩️ Undo", key=f"undo_{fname}",
                                                 help="Deletes this map and its stats"):
                db.delete_map(conn, mg)
                del st.session_state["extractions"][fname]
                bump()
                toast(f"Map #{mg} removed", "↩️")
                st.rerun()
            continue
        with st.expander(f"Review: {fname}", expanded=True):
            review_one(fname, ex, sid, opp_id, opp_name)

    # ---- 4. vetoes & operator bans ------------------------------------------
    st.subheader("3 · Map veto for this series")
    veto_editor(sid, opp_id, opp_name)
    st.subheader("4 · Operator bans (optional)")
    opban_editor(sid, opp_id, opp_name)
    st.subheader("5 · Finalize match")
    finalize_section(sid, series)


def finalize_section(sid, series):
    maps = conn.execute(
        """SELECT map_number, map_name, result, rounds_won, rounds_lost, import_status
           FROM maps_played WHERE series_id=? ORDER BY map_number""", (sid,)).fetchall()
    vcount = conn.execute("SELECT COUNT(*) c FROM veto_events WHERE series_id=?", (sid,)).fetchone()["c"]
    obcount = conn.execute("SELECT COUNT(*) c FROM operator_bans ob JOIN maps_played mp USING(map_game_id) "
                           "WHERE mp.series_id=?", (sid,)).fetchone()["c"]
    if maps:
        st.dataframe(pd.DataFrame([dict(m) for m in maps]), width="stretch", hide_index=True)
    inprog = [m["map_name"] for m in maps if m["import_status"] == "in_progress"]
    checks = [f"🗺️ Maps saved: **{len(maps)}**" + (" ⚠️ none yet" if not maps else ""),
              f"📋 Veto steps: **{vcount}**" + (" ⚠️ none recorded" if vcount == 0 else ""),
              f"🚫 Operator bans: **{obcount}**"]
    if inprog:
        checks.append(f"⚠️ Still marked in-progress: **{', '.join(inprog)}** — upload the final board or edit the map before finalizing")
    st.markdown("  ·  ".join(checks))
    already = "finalized" in series.keys() and series["finalized"]
    if st.button("🏁 Finalized — everything is in" if not already else "🏁 Re-finalize (after edits)",
                 type="primary", disabled=not maps, key=f"fin_{sid}"):
        db.recalc_series(conn, sid)
        conn.execute("UPDATE series SET finalized=1 WHERE series_id=?", (sid,))
        conn.commit()
        st.session_state["extractions"] = {}
        row = conn.execute("SELECT result, maps_won, maps_lost FROM series WHERE series_id=?", (sid,)).fetchone()
        msg = f"Series locked in: **{row['result'] or '?'} {row['maps_won']}-{row['maps_lost']}**."
        gs_url, gs_creds = db.get_setting(conn, "gs_sheet"), db.get_setting(conn, "gs_creds")
        if gs_url and gs_creds:
            try:
                gsheets.sync(conn, gs_url, gs_creds)
                db.set_setting(conn, "gs_last_sync", pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"))
                msg += " Google Sheet updated for the team."
            except Exception as e:
                msg += f" (Google Sheets sync failed: {e})"
        st.success(msg)
        toast("Match finalized", "🏁")
        st.balloons()


def review_one(fname, ex, sid, opp_id, opp_name):
    teams = ex.get("teams", [])
    while len(teams) < 2:
        teams.append({"label": "", "score": None, "players": []})
    guess, hits = matching.identify_our_side(conn, teams, our_id())

    c1, c2, c3, c4 = st.columns([2, 3, 1, 1])
    map_name = c1.selectbox("Map", map_options() + ["(other…)"],
                            index=_map_index(ex.get("map")), key=f"map_{fname}")
    if map_name == "(other…)":
        map_name = c1.text_input("Map name", key=f"mapother_{fname}")
    match_id = c2.text_input("Siege Match ID", value=ex.get("match_id") or "", key=f"mid_{fname}")
    map_no = c3.number_input("Map #", 1, 5, value=_next_map_no(sid), key=f"no_{fname}")
    status = c4.selectbox("Status", ["confirmed", "in_progress"],
                          index=1 if not ex.get("is_replay") and _looks_unfinished(teams) else 0,
                          key=f"st_{fname}",
                          help="in_progress = mid-map screenshot; a later final screenshot can update it")

    side_names = [t.get("label") or f"Team {i + 1} (top)" if i == 0 else t.get("label") or "Team 2 (bottom)"
                  for i, t in enumerate(teams)]
    default_side = guess if guess is not None else 0
    our_side = st.radio("Which scoreboard block is Northwood?", [0, 1], index=default_side,
                        format_func=lambda i: f"{side_names[i]}  (alias hits: {round(hits[i],1) if hits else 0})",
                        horizontal=True, key=f"side_{fname}")
    if guess is None:
        st.warning("Couldn't identify Northwood from gamertags - pick the correct block above.")

    ours, theirs = teams[our_side], teams[1 - our_side]
    cA, cB = st.columns(2)
    rw = cA.number_input("Rounds won (Northwood)", 0, 30, int(ours.get("score") or 0), key=f"rw_{fname}")
    rl = cB.number_input("Rounds lost", 0, 30, int(theirs.get("score") or 0), key=f"rl_{fname}")
    result = "W" if rw > rl else ("L" if rl > rw else "T")
    st.caption(f"Result: **{result}** · Total rounds: **{rw + rl}** (used as rounds played for every player - editable below)")

    # ---- attack / defense -------------------------------------------------
    rph = int(db.get_setting(conn, "rounds_per_half", "6"))
    orph = int(db.get_setting(conn, "ot_rounds_per_half", "3"))
    st.markdown("**Attack / defense** — two numbers here unlock side splits for this map")
    s1, s2, s3, s4 = st.columns([2, 1, 1, 2])
    picker = conn.execute(
        """SELECT t.name AS team, t.is_us FROM veto_events v LEFT JOIN teams t ON v.team_id=t.team_id
           WHERE v.series_id=? AND v.action='Pick' AND lower(v.map_name)=lower(?)""",
        (sid, map_name or "")).fetchone()
    if picker and picker["team"]:
        if picker["is_us"]:
            st.caption(f"Veto says **we** picked {map_name} → {opp_name or 'they'} chose the "
                       "second-half side, and we'd choose the OT side.")
        else:
            st.caption(f"Veto says **{picker['team']}** picked {map_name} → we chose the "
                       "second-half side, and they'd choose the OT side.")
    rs = ex.get("round_strip") or {}
    side_opts = ["Unknown", "ATK", "DEF"]
    strip_rows, det_side = sides.rounds_from_strip(rs, our_block_is_top=(our_side == 0))
    prefill = side_opts.index(det_side) if det_side in ("ATK", "DEF") else 0
    start_side = s1.selectbox("Northwood started on", side_opts, index=prefill, key=f"ss_{fname}",
                              help="Round 1 side. Sides swap after round %d." % rph)
    went_ot = (rw + rl) > rph * 2
    ot_side = s4.selectbox("OT started on", side_opts, key=f"ots_{fname}", disabled=not went_ot,
                           help="Only needed past regulation. Whoever picked the map chooses it.")

    # The score is the source of truth: reconcile the strip to it, then label sides.
    fix_notes = []
    if strip_rows:
        raw_n = len(strip_rows)
        raw_w = sum(1 for r in strip_rows if r.get("won") == 1)
        strip_rows, fix_notes = sides.reconcile_rounds(strip_rows, rw, rl)
        if start_side != "Unknown":
            strip_rows = sides.apply_sides(strip_rows, start_side,
                                           None if ot_side == "Unknown" else ot_side, rph)
    auto_h1 = sides.halves_from_rounds(strip_rows, rph) if strip_rows else (None, None)
    strip_err = None

    if strip_rows:
        won_n = sum(1 for r in strip_rows if r["won"] == 1)
        st.success(f"📊 Round strip: **{len(strip_rows)} rounds**, we won **{won_n}**"
                   + (f" · half 1 = **{auto_h1[0]}-{auto_h1[1]}**" if auto_h1[0] is not None else ""),
                   icon="✅")
        if fix_notes:
            st.warning("Adjusted to match the {}-{} scoreboard (it read {} rounds, {} wins): ".format(
                rw, rl, raw_n, raw_w) + "; ".join(fix_notes), icon="🔧")
        with st.expander(f"Round-by-round ({len(strip_rows)} rounds) — correct anything misread"):
            rdf = pd.DataFrame([{"Round": r["round_number"],
                                 "Result": "Won" if r["won"] == 1 else ("Lost" if r["won"] == 0 else "?"),
                                 "Side": r["side"] or "?", "How": r["win_condition"],
                                 "Source": r.get("source", "read"),
                                 "Conf": r["confidence"]} for r in strip_rows])
            rdf = st.data_editor(
                rdf, key=f"rounds_{fname}", width="stretch", hide_index=True, num_rows="dynamic",
                column_config={
                    "Result": st.column_config.SelectboxColumn(options=["Won", "Lost", "?"]),
                    "Side": st.column_config.SelectboxColumn(options=["ATK", "DEF", "?"]),
                    "How": st.column_config.SelectboxColumn(
                        options=["elimination", "objective", "time", "unknown"],
                        help="elimination = team wipe · objective = plant/defuse/secure · time = clock ran out"),
                    "Source": st.column_config.TextColumn(disabled=True,
                        help="read = from the screenshot · inferred/corrected = filled in to match the score"),
                    "Conf": st.column_config.NumberColumn(disabled=True, format="%.2f")})
            strip_rows = [{"round_number": int(r["Round"]),
                           "won": 1 if r["Result"] == "Won" else (0 if r["Result"] == "Lost" else None),
                           "side": None if r["Side"] == "?" else r["Side"],
                           "win_condition": r["How"], "confidence": r["Conf"],
                           "source": r.get("Source", "read")}
                          for _, r in rdf.iterrows() if pd.notna(r["Round"])]
            auto_h1 = sides.halves_from_rounds(strip_rows, rph)
        h1w = auto_h1[0] if auto_h1[0] is not None else 0
        h1l = auto_h1[1] if auto_h1[1] is not None else 0
        s2.metric("Half-1 won", h1w)
        s3.metric("Half-1 lost", h1l)
    else:
        h1w = s2.number_input("Half-1 won", 0, rph, 0, key=f"h1w_{fname}")
        h1l = s3.number_input("Half-1 lost", 0, rph, 0, key=f"h1l_{fname}")
        st.caption("No round strip was read from this screenshot — type the half-1 score here, "
                   "or leave it blank and the map still imports without side splits.")

    half_known = start_side != "Unknown" and (h1w + h1l) > 0
    half_err = sides.check_half_scores(rw, rl, h1w if half_known else None,
                                       h1l if half_known else None, rph) if half_known else None
    if half_err:
        st.error(half_err, icon="🚫")
    elif half_known:
        preview = sides.split_map_rounds(
            {"rounds_won": rw, "rounds_lost": rl, "starting_side": start_side,
             "ot_starting_side": None if ot_side == "Unknown" else ot_side,
             "half1_won": h1w, "half1_lost": h1l}, rph, orph)
        st.caption(" · ".join(f"{p['phase']} ({p['side']}): {p['rounds_won']}-{p['rounds_lost']}"
                              for p in preview))

    rows = []
    for is_ours, block in ((True, ours), (False, theirs)):
        for p in block.get("players", []):
            tag = p.get("gamertag", "") or ""
            assigned, note = UNASSIGNED, ""
            if is_ours and tag:
                m = matching.match_gamertag(conn, tag, team_id=our_id())
                if m:
                    assigned = m["player_name"]
                    note = {"exact": "✓ known alias",
                            "pending": f"⏳ pending alias ({int(m['confidence']*100)}%)",
                            "fuzzy": f"❓ looks like {m['matched_alias']} ({int(m['confidence']*100)}%) - confirm"}[m["kind"]]
                else:
                    note = "❗ unknown gamertag - assign a player"
            conf = p.get("confidence", 1.0)
            if conf is not None and conf < 0.8:
                note = (note + " · " if note else "") + "⚠️ low OCR confidence - verify numbers"
            rows.append({"Team": "Northwood" if is_ours else (opp_name or "Opponent"),
                         "Assign to": assigned if is_ours else UNASSIGNED,
                         "Gamertag": tag, "Score": p.get("score"), "K": p.get("kills"),
                         "D": p.get("deaths"), "A": p.get("assists"),
                         "Rounds": rw + rl, "Conf": conf, "Note": note})
    edited = st.data_editor(
        pd.DataFrame(rows), key=f"ed_{fname}", num_rows="dynamic", width="stretch",
        column_config={
            "Assign to": st.column_config.SelectboxColumn(options=[UNASSIGNED] + roster_names()),
            "Team": st.column_config.SelectboxColumn(options=["Northwood", opp_name or "Opponent"]),
            "Conf": st.column_config.NumberColumn(disabled=True, format="%.2f"),
            "Note": st.column_config.TextColumn(disabled=True),
        })

    if ex.get("notes"):
        note = str(ex["notes"])
        (st.warning if "⚠️" in note else st.caption)(f"Reader notes: {note}")
    with st.expander("🚫 Operator bans — 6 per team (2 ATK + final, 2 DEF + final)"):
        det = ", ".join(b.get("operator", "") for b in (ex.get("operator_bans") or []) if b.get("operator"))
        if det:
            st.caption(f"Reader thinks it saw: {det} — it can't tell whose ban is whose or which "
                       "slot it filled, so place them yourself below if they look right.")
        st.caption("Side = the banned operator's own side. Leave rows blank if you didn't record "
                   "them; they can be filled in later under Manage → Data.")
        seed = opbans.seed_rows("Northwood", opp_name or "Opponent")
        parts = []
        cATK, cDEF = st.columns(2)
        for col, side in ((cATK, "ATK"), (cDEF, "DEF")):
            with col:
                st.caption(f"**{side} bans** — {'attackers' if side == 'ATK' else 'defenders'} "
                           f"removed from whoever attacks" if side == "ATK" else
                           f"**{side} bans** — defenders removed from whoever defends")
                sub = seed[seed["Side"] == side].reset_index(drop=True)
                parts.append(st.data_editor(
                    sub, key=f"ob_{side}_{fname}", width="stretch", hide_index=True,
                    column_config={
                        "Team": st.column_config.TextColumn(disabled=True),
                        "Side": None,
                        "Slot": st.column_config.TextColumn(disabled=True),
                        "Operator": st.column_config.SelectboxColumn(
                            options=operators.for_side(conn, side),
                            help=f"{side} operators only")}).assign(Side=side))
        ob_edit = pd.concat(parts, ignore_index=True)

    # manual stats the scoreboard cannot provide ----------------------------
    with st.expander("✋ 1vX clutches and plants (manual — not on the scoreboard)"):
        st.caption("Northwood only. Leave blank if you didn't track them for this map; "
                   "blank is treated as 'not recorded', not as zero. These feed the player rating.")
        ours_rows = edited[edited["Team"] == "Northwood"]
        man_seed = pd.DataFrame({
            "Player": [r["Assign to"] if r["Assign to"] != UNASSIGNED else r["Gamertag"]
                       for _, r in ours_rows.iterrows()],
            "1vX": [None] * len(ours_rows), "Plants": [None] * len(ours_rows)})
        man_edit = st.data_editor(
            man_seed, key=f"man_{fname}", width="stretch", hide_index=True,
            column_config={"Player": st.column_config.TextColumn(disabled=True),
                           "1vX": st.column_config.SelectboxColumn(options=list(range(0, 15)),
                                                                   help="Whole numbers 0-14"),
                           "Plants": st.column_config.SelectboxColumn(options=list(range(0, 15)),
                                                                      help="Whole numbers 0-14")})

    # integrity checks ------------------------------------------------------
    checks = insights.validate_scoreboard(edited.to_dict("records"), rw, rl)
    blocking = [m for lvl, m in checks if lvl == "error"]
    if strip_err:
        blocking.append(strip_err)
    for lvl, msg in checks:
        (st.error if lvl == "error" else st.warning if lvl == "warn" else st.success)(msg, icon={
            "error": "🚫", "warn": "⚠️", "ok": "✅"}[lvl])

    # duplicate detection --------------------------------------------------
    tags = [r for r in edited["Gamertag"].tolist() if r]
    cands = dedupe.find_candidates(conn, match_id, map_name, opp_id, _series_date(sid), tags)
    action, target = "new", None
    if cands:
        c = cands[0]
        icon = "🟠" if c["kind"] == "exact" else "🟡"
        st.warning(f"{icon} Possible duplicate: {c['reason']} "
                   f"(existing map #{c['map_game_id']}, {c['existing']['rounds_won']}-{c['existing']['rounds_lost']}, "
                   f"status: {c['existing']['import_status']}).")
        more = dedupe.is_more_complete(rw + rl, c["existing"])
        opts = ["Save as a NEW map", "UPDATE the existing map with this data", "Skip / don't save"]
        default = 1 if (c["kind"] == "exact" and more) else 2
        action = st.radio("What should happen?", opts, index=default, key=f"dup_{fname}")
        action = {opts[0]: "new", opts[1]: "update", opts[2]: "skip"}[action]
        target = c["map_game_id"]

    override = False
    if blocking:
        override = st.checkbox("Import anyway (I've verified these numbers are right)",
                               key=f"ovr_{fname}")
    if st.button(f"✅ Confirm import ({fname})", type="primary", key=f"go_{fname}",
                 disabled=bool(blocking) and not override):
        if action == "skip":
            ex["_imported"] = "skipped"
            st.rerun()
        if not map_name:
            st.error("Map name is required.")
            st.stop()
        stat_rows, new_aliases = [], []
        for _, r in edited.iterrows():
            if not (r["Gamertag"] or "").strip():
                continue
            is_ours = r["Team"] == "Northwood"
            pid = player_id_by_name(r["Assign to"]) if (is_ours and r["Assign to"] != UNASSIGNED) else None
            if pid:
                new_aliases.append((pid, r["Gamertag"].strip()))
            stat_rows.append({"team_id": our_id() if is_ours else opp_id, "player_id": pid,
                              "gamertag_displayed": r["Gamertag"].strip(),
                              "score": to_int(r["Score"]), "kills": to_int(r["K"]),
                              "deaths": to_int(r["D"]), "assists": to_int(r["A"]),
                              "rounds_played": to_int(r["Rounds"]) or (rw + rl)})
        mg_id = db.save_map_with_stats(
            conn, {"series_id": sid, "map_number": int(map_no), "map_name": map_name,
                   "result": result, "rounds_won": int(rw), "rounds_lost": int(rl),
                   "siege_match_id": match_id.strip() or None, "source_file": fname,
                   "import_status": status,
                   "starting_side": None if start_side == "Unknown" else start_side,
                   "ot_starting_side": None if (ot_side == "Unknown" or not went_ot) else ot_side,
                   "half1_won": int(h1w) if (half_known and not half_err) else None,
                   "half1_lost": int(h1l) if (half_known and not half_err) else None},
            stat_rows, replace_map_game_id=target if action == "update" else None)
        if strip_rows:
            db.save_round_results(conn, mg_id, strip_rows)
        man_entries = []
        for _, r in man_edit.iterrows():
            pid = player_id_by_name(r["Player"])
            if pid:
                man_entries.append({"player_id": pid, "clutches": to_int(r["1vX"]),
                                    "plants": to_int(r["Plants"])})
        if man_entries:
            rating.save_manual(conn, mg_id, man_entries)
        for pid, tag in new_aliases:  # user confirmed the import => alias approved
            db.add_alias(conn, pid, tag, status="confirmed")
        ob_filled = [r for _, r in ob_edit.iterrows() if str(r["Operator"] or "").strip()]
        if ob_filled:
            conn.execute("DELETE FROM operator_bans WHERE map_game_id=?", (mg_id,))
            for r in ob_filled:
                conn.execute(
                    """INSERT INTO operator_bans(map_game_id, team_id, side, slot, operator)
                       VALUES(?,?,?,?,?)""",
                    (mg_id, our_id() if r["Team"] == "Northwood" else opp_id, r["Side"],
                     opbans.label_to_slot(r["Slot"]), str(r["Operator"]).strip()))
        conn.execute("UPDATE series SET finalized=0 WHERE series_id=?", (sid,))
        conn.commit()
        ex["_imported"] = mg_id
        toast(f"{map_name} {rw}-{rl} saved as map #{mg_id}" + (" (updated)" if action == "update" else ""),
              "🏆" if result == "W" else "💾")
        try_autosync()
        st.rerun()




def _map_index(detected):
    opts = map_options()
    if detected:
        for i, m in enumerate(opts):
            if matching.normalize(m) in matching.normalize(detected) or \
               matching.normalize(detected) in matching.normalize(m):
                return i
    return 0


def _next_map_no(sid):
    r = conn.execute("SELECT MAX(map_number) AS m FROM maps_played WHERE series_id=?", (sid,)).fetchone()
    return int(r["m"] or 0) + 1


def _series_date(sid):
    return conn.execute("SELECT date FROM series WHERE series_id=?", (sid,)).fetchone()["date"]


def _looks_unfinished(teams):
    scores = [t.get("score") for t in teams if t.get("score") is not None]
    return not scores or max(scores) < 7


def veto_editor(sid, opp_id, opp_name):
    rows = conn.execute(
        """SELECT v.id, v.seq, t.name AS team, v.action, v.map_name FROM veto_events v
           LEFT JOIN teams t ON v.team_id=t.team_id WHERE v.series_id=? ORDER BY v.seq""", (sid,)).fetchall()
    if rows:
        st.dataframe(pd.DataFrame([dict(r) for r in rows]).set_index("seq")[["team", "action", "map_name"]],
                     width="stretch", height=min(40 + 35 * len(rows), 300))
    with st.form(f"veto_{sid}", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
        team = c1.selectbox("Team", ["Northwood", opp_name or "Opponent", "(decider - no team)"])
        act = c2.selectbox("Action", ["Ban", "Pick", "Decider"])
        mp = c3.selectbox("Map", map_options())
        if c4.form_submit_button("Add"):
            tid = our_id() if team == "Northwood" else (opp_id if team != "(decider - no team)" else None)
            seq = (rows[-1]["seq"] + 1) if rows else 1
            conn.execute("INSERT INTO veto_events(series_id, seq, team_id, action, map_name) VALUES(?,?,?,?,?)",
                         (sid, seq, tid, act, mp))
            conn.commit()
            bump()
            toast(f"{seq}. {team} · {act} · {mp}", "📋")
            st.rerun()
    if rows and st.button("Delete last veto step"):
        conn.execute("DELETE FROM veto_events WHERE id=?", (rows[-1]["id"],))
        conn.commit()
        bump()
        toast("Last veto step removed", "↩️")
        st.rerun()


def opban_editor(sid, opp_id, opp_name):
    maps = [dict(r) for r in conn.execute(
        "SELECT map_game_id, map_name FROM maps_played WHERE series_id=? ORDER BY map_number",
        (sid,)).fetchall()]
    if not maps:
        st.caption("No maps saved for this series yet — the review card above has the ban grid, "
                   "or come back here after confirming a map.")
        return
    pick = st.selectbox("Map", maps, format_func=lambda r: r["map_name"], key=f"obmap_{sid}")
    opban_grid(pick["map_game_id"], opp_id, opp_name, key=f"series_{sid}")


def opban_grid(map_game_id, opp_id, opp_name, key=""):
    """The 12-slot ban grid for one map: seeded from stored rows, fully editable."""
    cur = pd.read_sql_query(
        """SELECT ob.side, ob.slot, ob.operator, t.is_us FROM operator_bans ob
           LEFT JOIN teams t ON ob.team_id=t.team_id WHERE ob.map_game_id=?""",
        conn, params=(map_game_id,))
    grid = opbans.seed_rows("Northwood", opp_name or "Opponent")
    if not cur.empty:  # drop stored bans into their slots, extras appended
        for team_label, is_us in (("Northwood", 1), (opp_name or "Opponent", 0)):
            for side in ("ATK", "DEF"):
                for slot in ("first2", "final"):
                    ops = cur[(cur["is_us"] == is_us) & (cur["side"] == side)
                              & (cur["slot"] == slot)]["operator"].tolist()
                    mask = ((grid["Team"] == team_label) & (grid["Side"] == side)
                            & (grid["Slot"] == opbans.SLOT_LABELS[slot]))
                    idx = list(grid[mask].index)
                    for i, op in zip(idx, ops):
                        grid.at[i, "Operator"] = op
    parts = []
    cA, cB = st.columns(2)
    for col, side in ((cA, "ATK"), (cB, "DEF")):
        with col:
            st.caption(f"**{side} bans**")
            sub = grid[grid["Side"] == side].reset_index(drop=True)
            picked = [o for o in sub["Operator"].tolist() if o]
            opts = operators.for_side(conn, side)
            opts = opts + [o for o in picked if o not in opts]   # keep custom entries selectable
            parts.append(st.data_editor(
                sub, key=f"obgrid_{key}_{side}_{map_game_id}", width="stretch", hide_index=True,
                column_config={"Team": st.column_config.TextColumn(disabled=True),
                               "Side": None,
                               "Slot": st.column_config.TextColumn(disabled=True),
                               "Operator": st.column_config.SelectboxColumn(
                                   options=opts, help=f"{side} operators only")}).assign(Side=side))
    ed = pd.concat(parts, ignore_index=True)
    if st.button("💾 Save operator bans", key=f"obsave_{key}_{map_game_id}"):
        conn.execute("DELETE FROM operator_bans WHERE map_game_id=?", (map_game_id,))
        for _, r in ed.iterrows():
            op = str(r["Operator"] or "").strip()
            if not op:
                continue
            conn.execute(
                """INSERT INTO operator_bans(map_game_id, team_id, side, slot, operator)
                   VALUES(?,?,?,?,?)""",
                (map_game_id, our_id() if r["Team"] == "Northwood" else opp_id, r["Side"],
                 opbans.label_to_slot(r["Slot"]), op))
        conn.commit()
        toast("Operator bans saved", "🚫")
        try_autosync()
