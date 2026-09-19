"""Dashboards page.

Layout: sidebar filters apply to every tab. Tables that list players, maps or
opponents are clickable — selecting a row jumps to that thing's detail view.
"""
import streamlit as st

from siegestats import insights, opbans, rating, sides, stats
from ui import charts
from ui.common import (conn, frame, vetoes, bans, setting_int, selected_rows)

TABS = ["🏠 Team", "👥 Players", "🔎 Player Detail", "🕵️ Opponents", "🗺️ Maps", "🚫 Op Bans"]
SCOPES = {"Gameday only": ["Gameday"], "Scrims only": ["Scrim"], "Both (total)": None}


def _side_config():
    return setting_int("rounds_per_half", 6), setting_int("ot_rounds_per_half", 3)


VIEW_KEY = "dash_view"


def _jump(key, value, target_view):
    """Drill down: remember the selection, switch to the target view, rerun.
    The view widget can't be changed after it's drawn, so the target is parked
    in _goto and applied at the top of the next run."""
    st.session_state[key] = value
    st.session_state["_goto"] = target_view
    st.session_state["_trail"] = (st.session_state.get(VIEW_KEY), value)
    st.toast(f"{value} → {target_view}", icon="👉")
    st.rerun()


def _pct(v):
    return f"{v}%" if v is not None else "—"


def _filters(df):
    with st.sidebar:
        st.subheader("Filters")
        scope = st.radio("Match type", list(SCOPES), index=0,
                         help="Gameday = official/league matches. Scrims are practice and are kept "
                              "out of gameday stats by default.")
        f_season = st.multiselect("Season", sorted(df["season"].dropna().unique()))
        f_opp = st.multiselect("Opponent", sorted(df["opponent"].dropna().unique()))
        f_map = st.multiselect("Map", sorted(df["map_name"].dropna().unique()))
        f_fmt = st.multiselect("Format", sorted(df["format"].dropna().unique()))
        f_comp = st.multiselect("Competition", sorted(df["competition"].dropna().unique()))
        dates = sorted(df["date"].unique())
        if len(dates) > 1:
            f_from, f_to = st.select_slider("Date range", options=dates, value=(dates[0], dates[-1]))
        else:
            f_from = f_to = dates[0]
            st.caption(f"Date: {dates[0]}")
    fdf = stats.apply_filters(df, seasons=f_season, opponents=f_opp, maps=f_map,
                              match_types=SCOPES[scope], formats=f_fmt, competitions=f_comp,
                              date_from=f_from, date_to=f_to)
    return fdf, scope


def render_dashboards():
    st.title("📊 Dashboards")
    df = frame()
    if df.empty:
        st.info("No data yet — import a match first.")
        return
    fdf, scope = _filters(df)
    ours = fdf[fdf["is_us"] == 1]
    st.caption(f"Showing **{scope}** · {ours['map_game_id'].nunique()} maps · "
               f"{ours['series_id'].nunique()} series · click a row in any table to jump to it")
    if fdf.empty:
        st.warning("No matches fit these filters.")
        return

    # navigation: a segmented control we *can* drive from code (unlike st.tabs)
    if "_goto" in st.session_state:
        st.session_state[VIEW_KEY] = st.session_state.pop("_goto")
    st.session_state.setdefault(VIEW_KEY, TABS[0])
    nav_col, back_col = st.columns([6, 1])
    view = nav_col.segmented_control("View", TABS, key=VIEW_KEY, label_visibility="collapsed")
    trail = st.session_state.get("_trail")
    if trail and trail[0] and trail[0] != view:
        if back_col.button("← Back", help=f"Return to {trail[0]}"):
            st.session_state["_goto"] = trail[0]
            st.session_state.pop("_trail", None)
            st.rerun()
    if view is None:  # segmented control can be deselected; fall back to Team
        view = TABS[0]

    {TABS[0]: lambda: team_tab(ours),
     TABS[1]: lambda: players_tab(ours),
     TABS[2]: lambda: player_detail_tab(ours, scope),
     TABS[3]: lambda: opponents_tab(df, fdf, scope),
     TABS[4]: lambda: maps_tab(df, fdf),
     TABS[5]: lambda: opban_tab(ours)}[view]()


# ================================================================ TEAM
def team_tab(ours):
    rec = stats.team_map_record(conn, ours)
    c = st.columns(6)
    c[0].metric("Series", rec["series"])
    c[1].metric("Maps", rec["maps"])
    c[2].metric("Rounds", rec["rounds"])
    c[3].metric("Team K/D", rec["team_kd"])
    c[4].metric("Map win %", _pct(rec["map_winpct"]))
    c[5].metric("Round win %", _pct(rec["round_winpct"]))

    ids = ours["map_game_id"].unique().tolist()
    rph, orph = _side_config()

    st.markdown("### Attack vs defense")
    sr = sides.side_record_from_rounds(conn, ids)
    exact = not sr.empty
    if sr.empty:
        sr = sides.team_side_record(conn, ids, rph, orph)
    pending = conn.execute(
        """SELECT mp.map_name, s.date, t.name AS opponent FROM maps_played mp
           JOIN series s USING(series_id) LEFT JOIN teams t ON s.opponent_id=t.team_id
           WHERE mp.starting_side IS NOT NULL AND mp.half1_won IS NULL
             AND NOT EXISTS (SELECT 1 FROM round_results rr WHERE rr.map_game_id=mp.map_game_id)""").fetchall()
    if pending:
        st.info("Side is recorded but the half-1 score is missing for: "
                + ", ".join(f"{p['map_name']} vs {p['opponent']} ({p['date']})" for p in pending)
                + " — add it under Manage → Data → Side data.", icon="➕")
    if sr.empty:
        st.caption("No side data yet. The round strip fills this in automatically on import; for "
                   "older maps add the starting side and half-1 score under Manage → Data.")
    else:
        cs = st.columns(len(sr) + 1)
        for col, (_, r) in zip(cs, sr.iterrows()):
            col.metric(f"{r['Side']} round win %", f"{r['Round win %']}%",
                       help=f"{int(r['RW'])}-{int(r['RL'])} across {int(r['Maps'])} map(s)")
        if len(sr) == 2:
            by = sr.set_index("Side")["Round win %"]
            gap = float(by.get("ATK", 0) - by.get("DEF", 0))
            cs[-1].metric("ATK − DEF gap", f"{gap:+.1f} pts",
                          help="Positive = stronger on attack. Big negatives are ban/practice signals.")
        st.caption(("Round-by-round data from the scoreboard strip." if exact else "Derived from half scores.")
                   + " Gap = attack round win % minus defense.")
        sbm = sides.side_by_map_from_rounds(conn, ids)
        if sbm.empty:
            sbm = sides.side_by_map(conn, ids, rph, orph)
        if not sbm.empty:
            cA, cB = st.columns([3, 2])
            cA.altair_chart(charts.side_split(sbm), width="stretch")
            cB.dataframe(sbm, width="stretch", hide_index=True)
        wc = sides.win_conditions(conn, ids)
        if not wc.empty:
            st.markdown("**How rounds are won and lost**")
            cA, cB = st.columns([3, 2])
            cA.altair_chart(charts.win_conditions(wc), width="stretch")
            cB.dataframe(wc, width="stretch", hide_index=True)
            cB.caption("Losing defense rounds to *defuse* or *time* points at site play; "
                       "losing them to *elimination* points at fights.")

    st.markdown("### Form")
    recent = (ours.drop_duplicates("map_game_id")
              [["date", "opponent", "match_type", "map_name", "map_result", "rounds_won",
                "rounds_lost", "format"]].sort_values("date", ascending=False))
    if not recent.empty:
        cA, cB = st.columns([3, 2])
        cA.altair_chart(charts.round_diff(recent.head(15)), width="stretch")
        cB.markdown("**Last 5 maps**")
        cB.dataframe(insights.rolling_form(ours, n=5), width="stretch", hide_index=True)

    st.markdown("### Map record")
    mb = stats.map_breakdown(ours)
    if not mb.empty:
        cA, cB = st.columns([3, 2])
        cA.altair_chart(charts.map_record(mb), width="stretch")
        ev = cB.dataframe(mb, width="stretch", hide_index=True,
                          on_select="rerun", selection_mode="single-row", key="team_maps")
        rows = selected_rows(ev)
        if rows:
            _jump("sel_map", mb.iloc[rows[0]]["map_name"], "🗺️ Maps")

    st.markdown("**All maps**")
    ev2 = st.dataframe(recent, width="stretch", hide_index=True,
                       on_select="rerun", selection_mode="single-row", key="team_recent")
    rows = selected_rows(ev2)
    if rows:
        _jump("sel_opp", recent.iloc[rows[0]]["opponent"], "🕵️ Opponents")


# ============================================================= PLAYERS
def players_tab(ours):
    pt = stats.player_table(ours)
    if pt.empty:
        st.caption("No player rows in this filter.")
        return
    rt = rating.rate_players(conn, ours)
    if not rt.empty:
        st.markdown("### Rating")
        cA, cB = st.columns([2, 3])
        cA.altair_chart(charts.rating_bars(rt), width="stretch")
        ev = cB.dataframe(rt[["Player", "Rating", "Maps", "Rounds", "K/D", "KPR", "SRV%",
                              "Plants", "1vX", "Components"]],
                          width="stretch", hide_index=True,
                          on_select="rerun", selection_mode="single-row", key="players_rating")
        rows = selected_rows(ev)
        if rows:
            _jump("sel_player", rt.iloc[rows[0]]["Player"], "🔎 Player Detail")
        if (rt["Components"] != "5/5").any():
            cB.caption("Components < 5/5 = plants or 1vX not recorded for those maps; missing parts are "
                       "dropped and the rest reweighted, not scored as zero.")

    st.markdown("### Stats")
    cA, cB = st.columns([3, 2])
    ev = cA.dataframe(pt, width="stretch", hide_index=True,
                      on_select="rerun", selection_mode="single-row", key="players_stats")
    rows = selected_rows(ev)
    if rows:
        _jump("sel_player", pt.iloc[rows[0]]["display_name"], "🔎 Player Detail")
    cB.altair_chart(charts.kpr_vs_srv(pt), width="stretch")
    cB.caption("Up-and-right is the ideal: high kills per round *and* high survival. Bubble size = rounds.")

    cA2, cB2 = st.columns(2)
    cA2.altair_chart(charts.player_bars(pt, "K/D", baseline=1.0), width="stretch")
    cB2.altair_chart(charts.player_bars(pt, "KPR", baseline=rating.load_config(conn)["kpr_base"]),
                     width="stretch")
    st.caption("K/D shows the kill count when a player has 0 deaths. Dashed lines are the rating baselines.")

    with st.expander("⚖️ Compare players head-to-head"):
        names = sorted(ours["display_name"].dropna().unique())
        picked = st.multiselect("Players", names, default=names[:2], max_selections=4)
        cmp_df = insights.compare_players(ours, picked)
        if not cmp_df.empty:
            st.dataframe(cmp_df, width="stretch", hide_index=True)


# ======================================================== PLAYER DETAIL
def player_detail_tab(ours, scope):
    names = sorted(ours["display_name"].dropna().unique())
    if not names:
        st.caption("No players in this filter.")
        return
    default = st.session_state.get("sel_player")
    who = st.selectbox("Player", names, index=names.index(default) if default in names else 0,
                       key="pd_player")
    pdf = ours[ours["display_name"] == who]
    t = stats.player_table(pdf).iloc[0]
    c = st.columns(7)
    for col, key in zip(c, ["Maps", "Rounds", "K/D", "KPR", "SRV%", "APR", "+/-"]):
        col.metric(key, t[key])

    brk = rating.component_breakdown(conn, ours, who)
    if not brk.empty:
        rt_p = rating.rate_players(conn, ours)
        rv = rt_p[rt_p["Player"] == who]["Rating"].iloc[0] if not rt_p.empty else None
        with st.expander(f"⭐ Rating {rv if rv is not None else '—'} — how it's built"):
            st.dataframe(brk, width="stretch", hide_index=True)
            st.caption("Contribution = (value ÷ baseline) × reweighted weight. They sum to the rating.")

    trend = pdf.sort_values(["date", "map_number"])
    if len(trend) > 1:
        st.markdown("**Form over time (per map)**")
        st.altair_chart(charts.form_line(trend), width="stretch")

    cA, cB = st.columns(2)
    with cA:
        st.markdown("**By map**")
        bm = stats.player_table(pdf, extra_group="map_name")
        ev = st.dataframe(bm, width="stretch", hide_index=True,
                          on_select="rerun", selection_mode="single-row", key="pd_bymap")
        rows = selected_rows(ev)
        if rows:
            _jump("sel_map", bm.iloc[rows[0]]["map_name"], "🗺️ Maps")
        st.markdown("**By season**")
        st.dataframe(stats.player_table(pdf, extra_group="season"), width="stretch", hide_index=True)
    with cB:
        st.markdown("**By opponent**")
        bo = stats.player_table(pdf, extra_group="opponent")
        ev = st.dataframe(bo, width="stretch", hide_index=True,
                          on_select="rerun", selection_mode="single-row", key="pd_byopp")
        rows = selected_rows(ev)
        if rows:
            _jump("sel_opp", bo.iloc[rows[0]]["opponent"], "🕵️ Opponents")
        if pdf["match_type"].nunique() > 1 or scope == "Both (total)":
            st.markdown("**Gameday vs scrim**")
            st.dataframe(stats.player_table(pdf, extra_group="match_type"),
                         width="stretch", hide_index=True)

    st.markdown("**Match history**")
    st.dataframe(pdf[["date", "opponent", "map_name", "map_result", "kills", "deaths", "assists",
                      "rounds_played", "score"]].sort_values("date", ascending=False),
                 width="stretch", hide_index=True)


# ========================================================== OPPONENTS
def opponents_tab(df, fdf, scope):
    opps = sorted(df.loc[df["opponent"].notna(), "opponent"].unique())
    if not opps:
        st.caption("No opponents yet.")
        return
    default = st.session_state.get("sel_opp")
    opp = st.selectbox("Opponent", opps, index=opps.index(default) if default in opps else 0,
                       key="opp_pick")
    odf = fdf[fdf["opponent"] == opp]
    if odf.empty:
        st.warning("No maps against this opponent fit the current filters.")
        return
    o_ours = odf[odf["is_us"] == 1]
    rec = stats.team_map_record(conn, o_ours)
    c = st.columns(4)
    c[0].metric("Series vs them", rec["series"])
    c[1].metric("Maps vs them", rec["maps"])
    c[2].metric("Rounds vs them", rec["rounds"])
    c[3].metric("Our K/D vs them", rec["team_kd"])

    with st.expander("📋 Scout report — copy/paste before the rematch"):
        rep = insights.scout_report(conn, opp, match_types=SCOPES[scope])
        if rep:
            for kind, tip in rep["advice"]:
                st.markdown(f"- **{kind.upper()}** · {tip}")
            if rep["never_seen"]:
                st.caption("Never seen against them: " + ", ".join(rep["never_seen"]))
            st.code(insights.report_text(rep), language=None)
        else:
            st.caption("No history against this opponent yet.")

    st.markdown("**Maps played against them**")
    mb = stats.map_breakdown(o_ours)
    if not mb.empty:
        cA, cB = st.columns([3, 2])
        cA.altair_chart(charts.map_record(mb), width="stretch")
        ev = cB.dataframe(mb, width="stretch", hide_index=True,
                          on_select="rerun", selection_mode="single-row", key="opp_maps")
        rows = selected_rows(ev)
        if rows:
            _jump("sel_map", mb.iloc[rows[0]]["map_name"], "🗺️ Maps")

    vf = vetoes()
    vf = vf[vf["opponent"] == opp] if not vf.empty else vf
    if not vf.empty:
        st.markdown("**Veto tendencies**")
        colA, colB = st.columns(2)
        for col, team, title in ((colA, opp, f"{opp} — what they ban and pick"),
                                 (colB, "Northwood", "Northwood — what we ban and pick against them")):
            sub = vf[vf["acting_team"] == team]
            col.caption(title)
            if sub.empty:
                col.caption("Nothing recorded.")
                continue
            vt = sub.groupby(["action", "map_name"]).size().reset_index(name="times")
            col.altair_chart(charts.veto_bars(vt), width="stretch")
        with st.expander("Full veto history"):
            st.dataframe(vf[["date", "seq", "acting_team", "action", "map_name"]],
                         width="stretch", hide_index=True)

    cA, cB = st.columns(2)
    with cA:
        st.markdown("**Our players vs them**")
        opt_ours = stats.player_table(o_ours)
        ev = st.dataframe(opt_ours, width="stretch", hide_index=True,
                          on_select="rerun", selection_mode="single-row", key="opp_ourplayers")
        rows = selected_rows(ev)
        if rows:
            _jump("sel_player", opt_ours.iloc[rows[0]]["display_name"], "🔎 Player Detail")
    with cB:
        st.markdown("**Their players (scouted from scoreboards)**")
        opt = stats.player_table(odf[odf["is_us"] != 1], by="gamertag_displayed")
        st.dataframe(opt, width="stretch", hide_index=True)
        if not opt.empty:
            st.altair_chart(charts.player_bars(opt, "K/D", baseline=1.0, name_col="gamertag_displayed"),
                            width="stretch")

    b = bans()
    if not b.empty:
        vs = opbans.summarize(conn, b, by_us=False, map_ids=o_ours["map_game_id"].unique().tolist())
        if not vs.empty:
            st.markdown("**What they ban against us**")
            st.dataframe(vs, width="stretch", hide_index=True)


# =============================================================== MAPS
def maps_tab(df, fdf):
    mps = sorted(df["map_name"].dropna().unique())
    if not mps:
        return
    default = st.session_state.get("sel_map")
    mp = st.selectbox("Map", mps, index=mps.index(default) if default in mps else 0, key="map_pick")
    mdf = fdf[fdf["map_name"] == mp]
    if mdf.empty:
        st.warning("No plays of this map fit the current filters.")
        return
    m_ours = mdf[mdf["is_us"] == 1]
    rec = stats.team_map_record(conn, m_ours)
    c = st.columns(4)
    c[0].metric("Our record", rec["maps"])
    c[1].metric("Rounds", rec["rounds"])
    c[2].metric("Round win %", _pct(rec["round_winpct"]))
    c[3].metric("Team K/D", rec["team_kd"])

    mids = m_ours["map_game_id"].unique().tolist()
    rph, orph = _side_config()
    sbm = sides.side_by_map_from_rounds(conn, mids)
    if sbm.empty:
        sbm = sides.side_by_map(conn, mids, rph, orph)
    wc = sides.win_conditions(conn, mids)
    if not sbm.empty or not wc.empty:
        st.markdown("**Attack vs defense on this map**")
        cA, cB = st.columns([1, 2])
        if not sbm.empty:
            r = sbm.iloc[0]
            cA.metric("ATK round win %", _pct(r.get("ATK win%")))
            cA.metric("DEF round win %", _pct(r.get("DEF win%")))
        if not wc.empty:
            cB.altair_chart(charts.win_conditions(wc), width="stretch")

    cA, cB = st.columns(2)
    with cA:
        st.markdown("**Our players on this map**")
        ptm = stats.player_table(m_ours)
        ev = st.dataframe(ptm, width="stretch", hide_index=True,
                          on_select="rerun", selection_mode="single-row", key="map_players")
        rows = selected_rows(ev)
        if rows:
            _jump("sel_player", ptm.iloc[rows[0]]["display_name"], "🔎 Player Detail")
    with cB:
        st.markdown("**Opponents faced here**")
        faced = m_ours.drop_duplicates("map_game_id")[
            ["date", "opponent", "map_result", "rounds_won", "rounds_lost"]]
        ev = st.dataframe(faced, width="stretch", hide_index=True,
                          on_select="rerun", selection_mode="single-row", key="map_faced")
        rows = selected_rows(ev)
        if rows:
            _jump("sel_opp", faced.iloc[rows[0]]["opponent"], "🕵️ Opponents")

    vf = vetoes()
    vmap = vf[vf["map_name"] == mp] if not vf.empty else vf
    if not vmap.empty:
        st.markdown("**Veto history for this map**")
        st.dataframe(vmap.groupby(["acting_team", "action"]).size().reset_index(name="times"),
                     width="stretch", hide_index=True)


# ============================================================ OP BANS
def opban_tab(ours):
    b = bans()
    ids = ours["map_game_id"].unique().tolist()
    cov = opbans.coverage(conn, ids)
    if b.empty or b[b["map_game_id"].isin(ids)].empty:
        st.info("No operator bans recorded yet. Fill in the ban grid on a map's review card, "
                "or backfill under Manage → Data → Edit a past match.")
        if not cov.empty:
            st.dataframe(cov[["date", "opponent", "map_name", "Our bans", "Their bans", "Complete"]],
                         width="stretch", hide_index=True)
        return

    slot_pick = st.radio("Ban phase", ["All bans", "Opening 2 only", "Final only"], horizontal=True,
                         help="Opening 2 = the pair before round 1. Final = the extra ban after round 3.")
    slots = {"All bans": None, "Opening 2 only": ["first2"], "Final only": ["final"]}[slot_pick]

    c1, c2 = st.columns(2)
    for col, by_us, title in ((c1, False, "Bans against us — what they take from Northwood"),
                              (c2, True, "Our bans — what we take from them")):
        with col:
            st.markdown(f"**{title}**")
            t = opbans.summarize(conn, b, by_us=by_us, slots=slots, map_ids=ids)
            if t.empty:
                st.caption("None recorded.")
                continue
            st.altair_chart(charts.ban_bars(t), width="stretch")
            st.dataframe(t, width="stretch", hide_index=True)
    st.caption("A banned attacker is missing from whoever is attacking, so an opponent's attacker ban "
               "bites in our attack rounds — that's what *Side round win %* tracks. ⚠️ = fewer than 3 maps.")

    st.markdown("**By ban set** — bans interact, so this groups maps by the whole combination")
    g1, g2 = st.columns(2)
    for col, by_us, title in ((g1, False, "Sets banned against us"), (g2, True, "Sets we banned")):
        with col:
            st.caption(title)
            gg = opbans.ban_groups(conn, b, by_us=by_us, slots=slots, map_ids=ids)
            st.dataframe(gg, width="stretch", hide_index=True) if not gg.empty else st.caption("None.")

    with st.expander("Ban record coverage — which maps have all 12 slots filled"):
        st.dataframe(cov[["date", "opponent", "map_name", "Our bans", "Their bans", "Complete"]],
                     width="stretch", hide_index=True)
