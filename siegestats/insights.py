"""Integrity checks and derived insights.

Two jobs:
 1. validate_scoreboard() - catch bad imports BEFORE they reach the database.
    The strongest check is the kill/death identity: in a 5v5 match every kill
    by one team is a death for the other, so each team's kills must equal the
    other team's deaths. An OCR digit error almost always breaks that identity,
    which is how you find it without re-reading the screenshot.
 2. scout_report() / veto_advice() - turn stored history into the pre-match
    answers ("what do they ban, what should we ban") the raw tables imply but
    don't state.

Everything here is computed from stored data. Nothing is invented or inferred
beyond what the numbers support, and every suggestion says what it is based on.
"""
import pandas as pd

from . import sides as sidemod
from . import stats


# --------------------------------------------------------------- validation
def validate_scoreboard(rows, rounds_won, rounds_lost, our_label="Northwood"):
    """rows: list of dicts with Team, Gamertag, K, D, A, Rounds.
    Returns list of (level, message): level in {'error','warn','ok'}."""
    out = []
    total_rounds = (rounds_won or 0) + (rounds_lost or 0)
    ours = [r for r in rows if r.get("Team") == our_label and str(r.get("Gamertag") or "").strip()]
    theirs = [r for r in rows if r.get("Team") != our_label and str(r.get("Gamertag") or "").strip()]

    def _n(v):
        try:
            return int(v) if pd.notna(v) else None
        except (TypeError, ValueError):
            return None

    for label, side in (("Northwood", ours), ("Opponent", theirs)):
        if len(side) != 5:
            out.append(("warn", f"{label} has {len(side)} players, not 5 — "
                                "fine for a sub-5 scrim, otherwise check the read."))

    tags = [str(r["Gamertag"]).strip().lower() for r in rows if str(r.get("Gamertag") or "").strip()]
    dupes = {t for t in tags if tags.count(t) > 1}
    if dupes:
        out.append(("error", f"Duplicate gamertag(s) on this scoreboard: {', '.join(sorted(dupes))}"))

    ok_k = all(_n(r.get("K")) is not None and _n(r.get("D")) is not None for r in ours + theirs)
    if ok_k and ours and theirs:
        ok = sum(_n(r["K"]) for r in ours)
        od = sum(_n(r["D"]) for r in ours)
        tk = sum(_n(r["K"]) for r in theirs)
        td = sum(_n(r["D"]) for r in theirs)
        if ok == td and tk == od:
            out.append(("ok", f"Kill/death identity checks out ({ok}-{od} vs {tk}-{td}) — "
                              "the numbers are internally consistent."))
        else:
            out.append(("error",
                        f"Kills don't match deaths: Northwood {ok}K/{od}D vs opponent {tk}K/{td}D. "
                        f"Our kills should equal their deaths ({ok} vs {td}) and vice versa ({tk} vs {od}). "
                        "Usually one misread digit — check the columns before saving."))
    else:
        out.append(("warn", "Some K/D values are blank, so the kill/death cross-check was skipped."))

    if total_rounds:
        for r in ours + theirs:
            k, d = _n(r.get("K")), _n(r.get("D"))
            if d is not None and d > total_rounds:
                out.append(("error", f"{r['Gamertag']} has {d} deaths in {total_rounds} rounds — impossible."))
            if k is not None and k > total_rounds * 5:
                out.append(("warn", f"{r['Gamertag']} has {k} kills in {total_rounds} rounds — verify."))
        if max(rounds_won or 0, rounds_lost or 0) < 4:
            out.append(("warn", f"Score is {rounds_won}-{rounds_lost} — that looks mid-map. "
                                "Mark it in-progress if the map wasn't finished."))
    else:
        out.append(("warn", "No round score entered — rounds played, KPR and survival % need it."))
    return out


# ----------------------------------------------------------------- scouting
def scout_report(conn, opponent: str, match_types=None) -> dict:
    """Everything worth knowing before facing this opponent again."""
    df = stats.load_frame(conn)
    if df.empty:
        return {}
    df = stats.apply_filters(df, match_types=match_types)
    odf = df[df["opponent"] == opponent]
    if odf.empty:
        return {}
    ours, theirs = odf[odf["is_us"] == 1], odf[odf["is_us"] != 1]
    maps = ours.drop_duplicates("map_game_id")

    vf = stats.veto_frame(conn)
    vf = vf[vf["opponent"] == opponent] if not vf.empty else vf
    their_bans = _counts(vf, opponent, "Ban")
    their_picks = _counts(vf, opponent, "Pick")
    our_bans = _counts(vf, "Northwood", "Ban")
    our_picks = _counts(vf, "Northwood", "Pick")

    rec = stats.team_map_record(conn, ours)
    by_map = stats.map_breakdown(ours)
    threats = stats.player_table(theirs, by="gamertag_displayed")
    ourp = stats.player_table(ours)

    try:
        from . import db as dbm
        rph = int(dbm.get_setting(conn, "rounds_per_half", "6") or 6)
        orph = int(dbm.get_setting(conn, "ot_rounds_per_half", "3") or 3)
        side_map = sidemod.side_by_map(conn, maps["map_game_id"].unique().tolist(), rph, orph)
        side_rec = sidemod.team_side_record(conn, maps["map_game_id"].unique().tolist(), rph, orph)
    except Exception:
        side_map, side_rec = pd.DataFrame(), pd.DataFrame()

    return {
        "opponent": opponent,
        "side_by_map": side_map,
        "side_record": side_rec,
        "record": rec,
        "series_count": int(maps["series_id"].nunique()),
        "maps_played": by_map,
        "their_bans": their_bans,
        "their_picks": their_picks,
        "our_bans": our_bans,
        "our_picks": our_picks,
        "threats": threats,
        "our_players": ourp,
        "never_seen": _never_seen(conn, maps, vf),
        "advice": veto_advice(conn, by_map, their_bans, their_picks),
    }


def _counts(vf, team, action):
    if vf is None or vf.empty:
        return pd.DataFrame(columns=["map_name", "times"])
    sub = vf[(vf["acting_team"] == team) & (vf["action"] == action)]
    if sub.empty:
        return pd.DataFrame(columns=["map_name", "times"])
    return (sub.groupby("map_name").size().reset_index(name="times")
            .sort_values("times", ascending=False))


def _never_seen(conn, maps, vf):
    """Pool maps this opponent has neither played nor touched in a veto against us."""
    from . import db as dbm
    pool = set(dbm.active_maps(conn))
    seen = set(maps["map_name"].unique())
    if vf is not None and not vf.empty:
        seen |= set(vf["map_name"].unique())
    return sorted(pool - seen)


def veto_advice(conn, by_map, their_bans, their_picks):
    """Suggestions, each with the evidence behind it. Small samples are labeled."""
    tips = []
    if by_map is not None and not by_map.empty:
        strong = by_map.sort_values(["Map Win%", "Round Win%"], ascending=False)
        best, worst = strong.iloc[0], strong.iloc[-1]
        tied = len(strong) < 2 or (best["Map Win%"] == worst["Map Win%"]
                                   and best["Round Win%"] == worst["Round Win%"])
        if tied:
            tips.append(("note", "Every map against them has gone the same way so far — "
                                 "no map-strength signal to veto on yet."))
        else:
            if best["Map Win%"] >= 50:
                tips.append(("pick", f"{best['map_name']} — our best result against them "
                                     f"({int(best['W'])}-{int(best['L'])}, {best['Round Win%']}% rounds, "
                                     f"{int(best['Played'])} map(s) played)"))
            if worst["Map Win%"] < 50:
                tips.append(("ban", f"{worst['map_name']} — our weakest map vs them "
                                    f"({int(worst['W'])}-{int(worst['L'])}, {worst['Round Win%']}% rounds)"))
        if strong["Played"].max() < 2:
            tips.append(("note", "⚠️ One map each — treat all of this as a hint, not a read."))
    if their_picks is not None and not their_picks.empty:
        p = their_picks.iloc[0]
        tips.append(("ban", f"{p['map_name']} — they've picked it {int(p['times'])}x against us, "
                            "so it's likely their comfort map"))
    if their_bans is not None and not their_bans.empty:
        top = their_bans[their_bans["times"] == their_bans["times"].max()]
        names = ", ".join(top["map_name"])
        tips.append(("note", f"They've banned {names} ({int(top.iloc[0]['times'])}x each) — "
                             "don't spend a pick there, they'll remove it"))
    if not tips:
        tips.append(("note", "Not enough history against this opponent yet — one series is a sample of one."))
    return tips


def report_text(rep: dict) -> str:
    """Plain-text scout sheet you can paste into Discord before a match."""
    if not rep:
        return "No history against this opponent yet."
    L = [f"SCOUT REPORT — {rep['opponent']}",
         f"Record: {rep['record']['series']} series · {rep['record']['maps']} maps · "
         f"{rep['record']['rounds']} rounds ({rep['record']['round_winpct']}% round win)", ""]
    if not rep["maps_played"].empty:
        L.append("MAPS PLAYED AGAINST THEM")
        for _, r in rep["maps_played"].iterrows():
            L.append(f"  {r['map_name']}: {int(r['W'])}-{int(r['L'])} "
                     f"({int(r['RW'])}-{int(r['RL'])} rounds, {r['Round Win%']}% )")
        L.append("")
    for title, key in (("THEIR BANS", "their_bans"), ("THEIR PICKS", "their_picks"),
                       ("OUR BANS VS THEM", "our_bans"), ("OUR PICKS VS THEM", "our_picks")):
        d = rep[key]
        if d is not None and not d.empty:
            L.append(f"{title}: " + ", ".join(f"{r['map_name']} x{int(r['times'])}" for _, r in d.iterrows()))
    sm = rep.get("side_by_map")
    if sm is not None and not sm.empty:
        L.append("")
        L.append("ATTACK / DEFENSE vs them (round win %)")
        for _, r in sm.iterrows():
            L.append(f"  {r['map_name']}: ATK {r.get('ATK win%')}% | DEF {r.get('DEF win%')}%")
    if rep["never_seen"]:
        L.append(f"NEVER SEEN vs them: {', '.join(rep['never_seen'])}")
    L.append("")
    if not rep["threats"].empty:
        L.append("THEIR PLAYERS (from our scoreboards)")
        for _, r in rep["threats"].head(5).iterrows():
            L.append(f"  {r['gamertag_displayed']}: {r['K/D']} K/D, {r['KPR']} KPR, "
                     f"{r['SRV%']}% survival over {int(r['Maps'])} map(s)")
        L.append("")
    L.append("VETO NOTES")
    for kind, tip in rep["advice"]:
        L.append(f"  [{kind.upper()}] {tip}")
    return "\n".join(L)


# -------------------------------------------------------------------- form
def rolling_form(ours: pd.DataFrame, n=5) -> pd.DataFrame:
    """Last n maps, most recent first, with per-map team K/D and round diff."""
    if ours.empty:
        return pd.DataFrame()
    g = (ours.groupby(["map_game_id", "date", "opponent", "map_name", "map_result",
                       "rounds_won", "rounds_lost", "match_type"], dropna=False)
         .agg(K=("kills", "sum"), D=("deaths", "sum")).reset_index())
    g["Team K/D"] = (g["K"] / g["D"].clip(lower=1)).round(2)
    g["Round diff"] = g["rounds_won"] - g["rounds_lost"]
    return g.sort_values("date", ascending=False).head(n)[
        ["date", "match_type", "opponent", "map_name", "map_result", "rounds_won",
         "rounds_lost", "Round diff", "Team K/D"]]


def compare_players(ours: pd.DataFrame, names) -> pd.DataFrame:
    """Side-by-side metric comparison; metrics as rows so it reads like a card."""
    if ours.empty or not names:
        return pd.DataFrame()
    t = stats.player_table(ours[ours["display_name"].isin(names)])
    if t.empty:
        return t
    keep = ["Maps", "Rounds", "K", "D", "A", "K/D", "KPR", "SRV%", "DPR", "APR", "+/-", "Avg Score"]
    return t.set_index("display_name")[keep].T.reset_index().rename(columns={"index": "Metric"})
