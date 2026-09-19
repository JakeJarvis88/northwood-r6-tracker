"""Operator ban analysis.

The league's ban structure, per team per map:
  ATK: two opening bans ('first2') + one after round 3 of the attack half ('final')
  DEF: two opening bans ('first2') + one after round 3 of the defense half ('final')
  = 6 bans per team, 12 across a map.

Two questions this answers, and they are NOT the same question:
  "when WE ban X, how do we do?"      - our ban removes the operator from THEM
  "when THEY ban X against us, how do we do?" - their ban removes it from US

Side matters for reading the result. A banned ATTACKER is unavailable to whoever
is attacking, so an opponent banning an attacker bites in OUR attack rounds. The
tables therefore report the side-specific round win rate next to the overall one,
using round-by-round data where it exists.

Everything here is observational. A map where a ban coincided with a loss is not
evidence the ban caused the loss, and with a handful of maps it is barely evidence
of anything - so every table carries the sample size and small ones are flagged.
"""
import pandas as pd

SLOTS = ["first2", "final"]
SLOT_LABELS = {"first2": "Opening 2", "final": "Final (after round 3)"}


def ban_frame(conn) -> pd.DataFrame:
    """Every ban with its map, series and result context."""
    return pd.read_sql_query(
        """SELECT ob.id, ob.map_game_id, ob.operator, ob.side, ob.slot,
                  ob.team_id, t.name AS banned_by, t.is_us AS banned_by_us,
                  mp.map_name, mp.result AS map_result, mp.rounds_won, mp.rounds_lost,
                  s.date, s.season, s.match_type, s.format, opp.name AS opponent
           FROM operator_bans ob
           JOIN maps_played mp USING(map_game_id)
           JOIN series s USING(series_id)
           LEFT JOIN teams t ON ob.team_id = t.team_id
           LEFT JOIN teams opp ON s.opponent_id = opp.team_id""", conn)


def _side_rounds(conn) -> pd.DataFrame:
    """Per map per side round tallies from round-by-round data, when available."""
    df = pd.read_sql_query(
        """SELECT map_game_id, side, won FROM round_results
           WHERE won IS NOT NULL AND side IS NOT NULL""", conn)
    if df.empty:
        return pd.DataFrame(columns=["map_game_id", "side", "RW", "Rounds"])
    return (df.groupby(["map_game_id", "side"])
            .agg(RW=("won", "sum"), Rounds=("won", "size")).reset_index())


def summarize(conn, bans: pd.DataFrame, by_us: bool, slots=None, map_ids=None) -> pd.DataFrame:
    """One row per operator: how often it was banned and how we did on those maps.

    by_us=True  -> bans Northwood made.
    by_us=False -> bans made against Northwood.
    """
    if bans is None or bans.empty:
        return pd.DataFrame()
    df = bans[bans["banned_by_us"] == (1 if by_us else 0)]
    if slots:
        df = df[df["slot"].isin(slots)]
    if map_ids is not None:
        df = df[df["map_game_id"].isin(list(map_ids))]
    if df.empty:
        return pd.DataFrame()

    sr = _side_rounds(conn)
    rows = []
    for (op, side), g in df.groupby(["operator", "side"], dropna=False):
        maps = g.drop_duplicates("map_game_id")
        w = (maps["map_result"] == "W").sum()
        l = (maps["map_result"] == "L").sum()
        rw, rl = maps["rounds_won"].sum(), maps["rounds_lost"].sum()
        # the side the ban actually bites on: a banned attacker affects attack rounds
        side_rw = side_rounds = None
        if not sr.empty and side in ("ATK", "DEF"):
            sub = sr[(sr["map_game_id"].isin(maps["map_game_id"])) & (sr["side"] == side)]
            if not sub.empty:
                side_rw, side_rounds = sub["RW"].sum(), sub["Rounds"].sum()
        rows.append({
            "Operator": op, "Side": side, "Maps": int(maps["map_game_id"].nunique()),
            "Times": int(len(g)),
            "Map W-L": f"{int(w)}-{int(l)}",
            "Map win %": round(100 * w / max(w + l, 1), 1),
            "Rounds": f"{int(rw)}-{int(rl)}",
            "Round win %": round(100 * rw / max(rw + rl, 1), 1),
            f"{side or '?'} round win %": (round(100 * side_rw / side_rounds, 1)
                                           if side_rounds else None),
            "Slots": ", ".join(sorted({SLOT_LABELS.get(x, x) for x in g["slot"].dropna()})),
            "Small sample": "⚠️" if maps["map_game_id"].nunique() < 3 else "",
        })
    out = pd.DataFrame(rows)
    # merge the two side-specific columns into one readable column
    side_cols = [c for c in out.columns if c.endswith("round win %") and not c.startswith("Round")]
    if side_cols:
        out["Side round win %"] = out[side_cols].bfill(axis=1).iloc[:, 0]
        out = out.drop(columns=side_cols)
    return out.sort_values(["Maps", "Times"], ascending=False)


def ban_groups(conn, bans: pd.DataFrame, by_us: bool, slots=None, map_ids=None,
               min_maps=1) -> pd.DataFrame:
    """Performance by the full ban SET on a map, not just single operators.

    Bans interact - losing two site anchors together is not the sum of losing each
    alone - so this groups maps by the exact combination that was banned.
    """
    if bans is None or bans.empty:
        return pd.DataFrame()
    df = bans[bans["banned_by_us"] == (1 if by_us else 0)]
    if slots:
        df = df[df["slot"].isin(slots)]
    if map_ids is not None:
        df = df[df["map_game_id"].isin(list(map_ids))]
    if df.empty:
        return pd.DataFrame()
    combos = (df.groupby("map_game_id")["operator"]
              .apply(lambda x: " + ".join(sorted(set(x)))).reset_index(name="Ban set"))
    maps = df.drop_duplicates("map_game_id")[
        ["map_game_id", "map_name", "opponent", "map_result", "rounds_won", "rounds_lost"]]
    m = combos.merge(maps, on="map_game_id")
    g = m.groupby("Ban set").agg(
        Maps=("map_game_id", "nunique"),
        W=("map_result", lambda s: (s == "W").sum()),
        L=("map_result", lambda s: (s == "L").sum()),
        RW=("rounds_won", "sum"), RL=("rounds_lost", "sum")).reset_index()
    g = g[g["Maps"] >= min_maps]
    if g.empty:
        return pd.DataFrame()
    g["Map win %"] = (100 * g["W"] / (g["W"] + g["L"]).clip(lower=1)).round(1)
    g["Round win %"] = (100 * g["RW"] / (g["RW"] + g["RL"]).clip(lower=1)).round(1)
    g["Small sample"] = g["Maps"].apply(lambda n: "⚠️" if n < 3 else "")
    return g.sort_values(["Maps", "Round win %"], ascending=False)


def coverage(conn, map_ids=None) -> pd.DataFrame:
    """Which maps have a complete ban record (6 per team) and which don't."""
    q = """SELECT mp.map_game_id, s.date, o.name AS opponent, mp.map_name,
                  SUM(CASE WHEN t.is_us=1 THEN 1 ELSE 0 END) AS ours,
                  SUM(CASE WHEN t.is_us=1 THEN 0 ELSE 1 END) AS theirs
           FROM maps_played mp JOIN series s USING(series_id)
           LEFT JOIN teams o ON s.opponent_id=o.team_id
           LEFT JOIN operator_bans ob ON ob.map_game_id=mp.map_game_id
           LEFT JOIN teams t ON ob.team_id=t.team_id
           GROUP BY mp.map_game_id ORDER BY s.date"""
    df = pd.read_sql_query(q, conn)
    if df.empty:
        return df
    if map_ids is not None:
        df = df[df["map_game_id"].isin(list(map_ids))]
    df["Complete"] = df.apply(lambda r: "✅" if (r["ours"] == 6 and r["theirs"] == 6) else "—", axis=1)
    return df.rename(columns={"ours": "Our bans", "theirs": "Their bans"})


def seed_rows(our_team, opp_team):
    """The 12 ban slots of a map, ready to be filled in."""
    rows = []
    for team in (our_team, opp_team):
        for side in ("ATK", "DEF"):
            for slot, n in (("first2", 2), ("final", 1)):
                for _ in range(n):
                    rows.append({"Team": team, "Side": side,
                                 "Slot": SLOT_LABELS[slot], "Operator": ""})
    return pd.DataFrame(rows)


def label_to_slot(label):
    for k, v in SLOT_LABELS.items():
        if v == label:
            return k
    return label if label in SLOTS else "first2"
