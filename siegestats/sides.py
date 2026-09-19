"""Attack / defense splits (team level).

WHAT IS AND ISN'T RECOVERABLE
A final scoreboard shows map totals only, so a player's 9 kills cannot be split
into attack and defense kills from it. What CAN be recovered exactly is the
TEAM round record per side, from the half-1 score plus the side you started on.
That is two numbers of input per map and it works retroactively on maps already
imported.

(Per-player splits would need a halftime screenshot as well. The plumbing for
that is still here - player_side_stats() / summarize_player_sides() - but no UI
calls it, by choice. Wire it back up if that ever becomes wanted.)

LEAGUE FORMAT (Northwood's, and the defaults)
  First to 7 wins the map. Sides swap after round 6.
  At 6-6 the map goes to overtime and becomes first to 8, so overtime is at most
  3 rounds and the sides do NOT swap again inside it.
  Second-half side is chosen by the team that did NOT pick the map; if it goes to
  overtime, the team that DID pick the map chooses the OT side.
Both swap numbers are configurable under Manage -> Settings, so a league that
runs different halves or a longer overtime still computes correctly.

PHASES
  H1  rounds 1..rounds_per_half            - side = starting_side
  H2  the rest of regulation               - side = the other one
  OT  anything past regulation             - side = ot_starting_side
Overtime is its own bucket rather than folded into H1/H2, because it is a
separate side choice and mixing them would silently mislabel rounds.
"""
import pandas as pd

ATK, DEF = "ATK", "DEF"


def other(side):
    return DEF if side == ATK else (ATK if side == DEF else None)


def phase_sides(starting_side, ot_starting_side=None):
    """Map phase -> side we played. Unknown starting side yields an empty dict."""
    if starting_side not in (ATK, DEF):
        return {}
    out = {"H1": starting_side, "H2": other(starting_side)}
    if ot_starting_side in (ATK, DEF):
        out["OT"] = ot_starting_side
    return out


def regulation_rounds(rounds_per_half=6):
    return rounds_per_half * 2


def split_map_rounds(row, rounds_per_half=6, ot_rounds_per_half=3):
    """Team-level round record per phase for one map row (a dict/Row).

    Returns list of dicts: phase, side, rounds_won, rounds_lost, rounds.
    Needs half1_won/half1_lost and a known starting side; returns [] without them.

    The second half is derived, never guessed:
      - no overtime: H2 = (final - half 1), exactly.
      - overtime:    regulation must have ended tied at rounds_per_half each
                     (that is what sends a map to OT), so H2 = rounds_per_half
                     minus the half-1 tallies, and OT = whatever is left.
    """
    h1w, h1l = row.get("half1_won"), row.get("half1_lost")
    if h1w is None or h1l is None:
        return []
    sides = phase_sides(row.get("starting_side"), row.get("ot_starting_side"))
    if not sides:
        return []
    rw, rl = row.get("rounds_won") or 0, row.get("rounds_lost") or 0
    h1w, h1l = int(h1w), int(h1l)
    went_ot = (rw + rl) > regulation_rounds(rounds_per_half)

    if went_ot:
        h2w, h2l = rounds_per_half - h1w, rounds_per_half - h1l
        otw, otl = rw - rounds_per_half, rl - rounds_per_half
    else:
        h2w, h2l = rw - h1w, rl - h1l
        otw = otl = 0

    out = [{"phase": "H1", "side": sides["H1"], "rounds_won": h1w, "rounds_lost": h1l,
            "rounds": h1w + h1l},
           {"phase": "H2", "side": sides["H2"], "rounds_won": h2w, "rounds_lost": h2l,
            "rounds": h2w + h2l}]
    if otw or otl:
        # Sides swap again partway through a long overtime. Without per-round data
        # we can't attribute rounds past the first OT half, so we say so.
        ot_side = sides.get("OT") if (otw + otl) <= ot_rounds_per_half else "MIXED"
        out.append({"phase": "OT", "side": ot_side, "rounds_won": otw,
                    "rounds_lost": otl, "rounds": otw + otl})
    return out


def check_half_scores(rounds_won, rounds_lost, h1w, h1l, rounds_per_half=6):
    """Validate half-1 entry against the final score. Returns an error string or None."""
    if h1w is None or h1l is None:
        return None
    rw, rl = rounds_won or 0, rounds_lost or 0
    if h1w + h1l > rounds_per_half:
        return (f"First half is {h1w}-{h1l} = {h1w + h1l} rounds, but a half is "
                f"{rounds_per_half} rounds (change it under Manage → Settings if your league differs).")
    if h1w > rw or h1l > rl:
        return f"First half ({h1w}-{h1l}) can't exceed the final score ({rw}-{rl})."
    if (rw + rl) > regulation_rounds(rounds_per_half):
        if max(rw, rl) - min(rw, rl) > 0 and min(rw, rl) < rounds_per_half:
            return (f"{rw}-{rl} is past regulation, which means the map went to overtime — "
                    f"but that requires regulation to have ended {rounds_per_half}-{rounds_per_half}. "
                    "Check the final score.")
    elif h1w + h1l < rounds_per_half and (rw + rl) > (h1w + h1l) + rounds_per_half:
        return "First half plus second half doesn't add up to the final score."
    return None


def team_side_record(conn, map_game_ids=None, rounds_per_half=6, ot_rounds_per_half=3) -> pd.DataFrame:
    """Our attack vs defense round record across maps that have side data."""
    q = """SELECT map_game_id, map_name, rounds_won, rounds_lost, starting_side,
                  ot_starting_side, half1_won, half1_lost FROM maps_played"""
    rows = [dict(r) for r in conn.execute(q).fetchall()]
    if map_game_ids is not None:
        ids = set(map_game_ids)
        rows = [r for r in rows if r["map_game_id"] in ids]
    recs = []
    for r in rows:
        for part in split_map_rounds(r, rounds_per_half, ot_rounds_per_half):
            if part["side"]:
                recs.append({"map_name": r["map_name"], "map_game_id": r["map_game_id"],
                             "phase": part["phase"], **part})
    if not recs:
        return pd.DataFrame()
    df = pd.DataFrame(recs)
    g = (df.groupby("side").agg(Maps=("map_game_id", "nunique"),
                                RW=("rounds_won", "sum"), RL=("rounds_lost", "sum")).reset_index())
    g["Rounds"] = g["RW"] + g["RL"]
    g["Round win %"] = (100 * g["RW"] / g["Rounds"].clip(lower=1)).round(1)
    return g.rename(columns={"side": "Side"})[["Side", "Maps", "Rounds", "RW", "RL", "Round win %"]]


def side_by_map(conn, map_game_ids=None, rounds_per_half=6, ot_rounds_per_half=3) -> pd.DataFrame:
    """Per map: attack round win% vs defense round win% - the ban/pick view."""
    q = """SELECT map_game_id, map_name, rounds_won, rounds_lost, starting_side,
                  ot_starting_side, half1_won, half1_lost FROM maps_played"""
    rows = [dict(r) for r in conn.execute(q).fetchall()]
    if map_game_ids is not None:
        ids = set(map_game_ids)
        rows = [r for r in rows if r["map_game_id"] in ids]
    recs = []
    for r in rows:
        for part in split_map_rounds(r, rounds_per_half, ot_rounds_per_half):
            if part["side"]:
                recs.append({"map_name": r["map_name"], "map_game_id": r["map_game_id"], **part})
    if not recs:
        return pd.DataFrame()
    df = pd.DataFrame(recs)
    piv = df.groupby(["map_name", "side"]).agg(RW=("rounds_won", "sum"),
                                               RL=("rounds_lost", "sum")).reset_index()
    piv["win%"] = (100 * piv["RW"] / (piv["RW"] + piv["RL"]).clip(lower=1)).round(1)
    out = piv.pivot(index="map_name", columns="side", values=["RW", "RL", "win%"])
    out.columns = [f"{s} {m}" for m, s in out.columns]
    out = out.reset_index()
    for s in (ATK, DEF):
        if f"{s} win%" not in out.columns:
            out[f"{s} win%"] = None
    out["Gap (ATK-DEF)"] = (pd.to_numeric(out[f"{ATK} win%"], errors="coerce")
                            - pd.to_numeric(out[f"{DEF} win%"], errors="coerce")).round(1)
    cols = ["map_name"] + [c for c in out.columns if c not in ("map_name", "Gap (ATK-DEF)")] + ["Gap (ATK-DEF)"]
    return out[cols].sort_values("map_name")


# ------------------------------------------------------- player-level splits
def player_side_stats(conn, map_game_ids=None, rounds_per_half=6, ot_rounds_per_half=3) -> pd.DataFrame:
    """One row per player per side, built from halftime snapshots.

    Only maps that have BOTH a halftime snapshot and a known starting side are
    included; everything else is silently skipped rather than guessed at.
    """
    maps = {r["map_game_id"]: dict(r) for r in conn.execute(
        """SELECT map_game_id, map_name, rounds_won, rounds_lost, starting_side,
                  ot_starting_side, half1_won, half1_lost FROM maps_played""").fetchall()}
    if map_game_ids is not None:
        ids = set(map_game_ids)
        maps = {k: v for k, v in maps.items() if k in ids}
    if not maps:
        return pd.DataFrame()

    snaps = pd.read_sql_query(
        "SELECT * FROM player_phase_stats WHERE phase='H1'", conn)
    if snaps.empty:
        return pd.DataFrame()
    finals = pd.read_sql_query(
        """SELECT pms.map_game_id, pms.gamertag_displayed, pms.player_id, pms.team_id,
                  pms.kills, pms.deaths, pms.assists, t.is_us, p.name AS player_name
           FROM player_map_stats pms
           LEFT JOIN teams t ON pms.team_id=t.team_id
           LEFT JOIN players p ON pms.player_id=p.player_id""", conn)

    out = []
    for mg_id, m in maps.items():
        sides = phase_sides(m["starting_side"], m["ot_starting_side"])
        if not sides or m["half1_won"] is None:
            continue
        parts = {p["phase"]: p for p in split_map_rounds(m, rounds_per_half, ot_rounds_per_half)}
        h1 = snaps[snaps["map_game_id"] == mg_id]
        fin = finals[finals["map_game_id"] == mg_id]
        if h1.empty or fin.empty:
            continue
        for _, f in fin.iterrows():
            s = h1[h1["gamertag_displayed"] == f["gamertag_displayed"]]
            if s.empty:
                continue
            s = s.iloc[0]
            base = {"map_game_id": mg_id, "map_name": m["map_name"],
                    "gamertag_displayed": f["gamertag_displayed"],
                    "display_name": f["gamertag_displayed"] if pd.isna(f["player_name"]) else f["player_name"],
                    "is_us": f["is_us"], "team_id": f["team_id"]}
            # first half = the snapshot itself
            if "H1" in parts:
                out.append({**base, "phase": "H1", "side": sides["H1"],
                            "kills": _i(s["kills"]), "deaths": _i(s["deaths"]),
                            "assists": _i(s["assists"]), "rounds": parts["H1"]["rounds"]})
            # everything after halftime = final minus the snapshot
            rest_rounds = sum(parts[p]["rounds"] for p in ("H2", "OT") if p in parts)
            if rest_rounds:
                k = _i(f["kills"]) - _i(s["kills"])
                d = _i(f["deaths"]) - _i(s["deaths"])
                a = _i(f["assists"]) - _i(s["assists"])
                if "OT" in parts:
                    # OT swaps sides again; without an end-of-regulation snapshot we
                    # can't tell H2 from OT, so label the block honestly.
                    out.append({**base, "phase": "H2+OT", "side": "MIXED",
                                "kills": k, "deaths": d, "assists": a, "rounds": rest_rounds})
                else:
                    out.append({**base, "phase": "H2", "side": sides["H2"],
                                "kills": k, "deaths": d, "assists": a, "rounds": rest_rounds})
    return pd.DataFrame(out)


def _i(v):
    try:
        return int(v) if v is not None and not pd.isna(v) else 0
    except (TypeError, ValueError):
        return 0


def summarize_player_sides(psd: pd.DataFrame, ours_only=True) -> pd.DataFrame:
    """Aggregate player_side_stats into per-player ATK/DEF lines."""
    if psd is None or psd.empty:
        return pd.DataFrame()
    df = psd[psd["is_us"] == 1] if ours_only else psd
    df = df[df["side"].isin([ATK, DEF])]
    if df.empty:
        return pd.DataFrame()
    g = df.groupby(["display_name", "side"]).agg(
        Maps=("map_game_id", "nunique"), Rounds=("rounds", "sum"),
        K=("kills", "sum"), D=("deaths", "sum"), A=("assists", "sum")).reset_index()
    g["K/D"] = (g["K"] / g["D"].clip(lower=1)).round(2)
    g["KPR"] = (g["K"] / g["Rounds"].clip(lower=1)).round(2)
    g["SRV%"] = (100 * (g["Rounds"] - g["D"]) / g["Rounds"].clip(lower=1)).round(1)
    g["+/-"] = g["K"] - g["D"]
    return g.rename(columns={"display_name": "Player", "side": "Side"})


def side_gap(summary: pd.DataFrame) -> pd.DataFrame:
    """Per player: how much better they are on one side than the other."""
    if summary is None or summary.empty:
        return pd.DataFrame()
    piv = summary.pivot(index="Player", columns="Side", values=["K/D", "KPR", "SRV%"])
    piv.columns = [f"{s} {m}" for m, s in piv.columns]
    piv = piv.reset_index()
    if f"{ATK} KPR" in piv.columns and f"{DEF} KPR" in piv.columns:
        piv["KPR gap (ATK-DEF)"] = (piv[f"{ATK} KPR"] - piv[f"{DEF} KPR"]).round(2)
    return piv


# ===================================================================== round strip
def rounds_from_strip(strip: dict, our_block_is_top: bool, starting_side=None,
                      ot_starting_side=None, rounds_per_half=6):
    """Turn the reader's round-strip output into per-round rows for one map.

    strip: {"top_team_first_half_side": "ATK"/"DEF", "rounds": [{round, winner, ...}]}
    our_block_is_top: True when Northwood is the team listed first in the player table.

    Returns (rows, detected_starting_side). Each row: round_number, won, side,
    win_condition, confidence. The side for each round comes from the half it falls
    in, so a round's side is never guessed independently of the swap rules.
    """
    if not strip:
        return [], None
    raw = strip.get("rounds") or []
    top_side = strip.get("top_team_first_half_side")
    detected = None
    if top_side in (ATK, DEF):
        detected = top_side if our_block_is_top else other(top_side)
    start = starting_side if starting_side in (ATK, DEF) else detected
    rows = []
    for r in raw:
        try:
            n = int(r.get("round"))
        except (TypeError, ValueError):
            continue
        winner = r.get("winner")
        won = None
        if winner in ("top", "bottom"):
            won = 1 if ((winner == "top") == bool(our_block_is_top)) else 0
        rows.append({"round_number": n, "won": won,
                     "side": side_for_round(n, start, ot_starting_side, rounds_per_half),
                     "win_condition": r.get("win_condition") or "unknown",
                     "confidence": r.get("confidence")})
    rows.sort(key=lambda x: x["round_number"])
    return rows, detected


def side_for_round(n, starting_side, ot_starting_side=None, rounds_per_half=6):
    """Which side we played in round n, given the swap rules."""
    if starting_side not in (ATK, DEF):
        return None
    reg = regulation_rounds(rounds_per_half)
    if n <= rounds_per_half:
        return starting_side
    if n <= reg:
        return other(starting_side)
    return ot_starting_side if ot_starting_side in (ATK, DEF) else None


def halves_from_rounds(rows, rounds_per_half=6):
    """Derive (half1_won, half1_lost) from round rows - what you'd otherwise type in."""
    h1 = [r for r in rows if r["round_number"] <= rounds_per_half and r["won"] is not None]
    if not h1:
        return None, None
    return sum(r["won"] for r in h1), sum(1 - r["won"] for r in h1)


def strip_consistency(rows, rounds_won, rounds_lost):
    """Check the strip against the final score. Returns an error string or None."""
    scored = [r for r in rows if r["won"] is not None]
    if not scored:
        return None
    w = sum(r["won"] for r in scored)
    l = len(scored) - w
    if (w, l) != (rounds_won or 0, rounds_lost or 0):
        return (f"The round strip reads {w}-{l} but the final score is "
                f"{rounds_won}-{rounds_lost}. Some markers were probably covered by the "
                "kill feed or a player card — fix the rounds below or the score above.")
    return None


def side_record_from_rounds(conn, map_game_ids=None) -> pd.DataFrame:
    """Our ATK/DEF round record straight from stored round results (exact)."""
    q = "SELECT map_game_id, side, won FROM round_results WHERE won IS NOT NULL AND side IS NOT NULL"
    df = pd.read_sql_query(q, conn)
    if df.empty:
        return pd.DataFrame()
    if map_game_ids is not None:
        df = df[df["map_game_id"].isin(list(map_game_ids))]
    if df.empty:
        return pd.DataFrame()
    g = df.groupby("side").agg(Maps=("map_game_id", "nunique"), Rounds=("won", "size"),
                               RW=("won", "sum")).reset_index()
    g["RL"] = g["Rounds"] - g["RW"]
    g["Round win %"] = (100 * g["RW"] / g["Rounds"].clip(lower=1)).round(1)
    return g.rename(columns={"side": "Side"})[["Side", "Maps", "Rounds", "RW", "RL", "Round win %"]]


def side_by_map_from_rounds(conn, map_game_ids=None) -> pd.DataFrame:
    q = """SELECT rr.map_game_id, mp.map_name, rr.side, rr.won FROM round_results rr
           JOIN maps_played mp USING(map_game_id)
           WHERE rr.won IS NOT NULL AND rr.side IS NOT NULL"""
    df = pd.read_sql_query(q, conn)
    if df.empty:
        return pd.DataFrame()
    if map_game_ids is not None:
        df = df[df["map_game_id"].isin(list(map_game_ids))]
    if df.empty:
        return pd.DataFrame()
    g = df.groupby(["map_name", "side"]).agg(RW=("won", "sum"), Rounds=("won", "size")).reset_index()
    g["RL"] = g["Rounds"] - g["RW"]
    g["win%"] = (100 * g["RW"] / g["Rounds"].clip(lower=1)).round(1)
    out = g.pivot(index="map_name", columns="side", values=["RW", "RL", "win%"])
    out.columns = [f"{s} {m}" for m, s in out.columns]
    out = out.reset_index()
    for s in (ATK, DEF):
        for m in ("RW", "RL", "win%"):
            if f"{s} {m}" not in out.columns:
                out[f"{s} {m}"] = None
    out["Gap (ATK-DEF)"] = (pd.to_numeric(out[f"{ATK} win%"], errors="coerce")
                            - pd.to_numeric(out[f"{DEF} win%"], errors="coerce")).round(1)
    return out.sort_values("map_name")


def win_conditions(conn, map_game_ids=None) -> pd.DataFrame:
    """How rounds are won and lost, by side - the 'why' behind a bad map."""
    df = pd.read_sql_query(
        "SELECT map_game_id, side, won, win_condition FROM round_results WHERE won IS NOT NULL", conn)
    if df.empty:
        return pd.DataFrame()
    if map_game_ids is not None:
        df = df[df["map_game_id"].isin(list(map_game_ids))]
    if df.empty:
        return pd.DataFrame()
    df["Result"] = df["won"].map({1: "Won", 0: "Lost"})
    df["side"] = df["side"].fillna("Unknown")
    g = (df.groupby(["side", "Result", "win_condition"]).size().reset_index(name="Rounds")
         .rename(columns={"side": "Side", "win_condition": "How"}))
    return g.sort_values(["Side", "Result", "Rounds"], ascending=[True, True, False])
