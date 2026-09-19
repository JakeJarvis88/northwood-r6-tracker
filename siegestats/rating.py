"""Composite player rating.

A rating is only useful if you can see why it moved, so this one is deliberately
plain: each component is the player's value divided by a baseline, the components
are weighted, and the weights and baselines are editable under Manage -> Settings.
A rating of 1.00 means "exactly baseline across the board". 1.20 means 20% above.

  Component      Source                          Default baseline   Default weight
  KPR            screenshot                      0.75               0.30
  Survival %     screenshot                      35%                0.20
  K/D            screenshot                      1.00               0.20
  Plants/round   manual entry at import          0.12               0.18
  1vX/map        manual entry at import          0.30               0.12

Each component is capped at `cap` x baseline (default 2.5) before weighting. Over
two maps a single clutch is 3.3x the 1vX baseline, and without a cap that one
round would move the rating more than the other four components combined. The cap
is shown in the breakdown whenever it bites, and can be raised or removed (set it
high) under Settings.

Components with no data are DROPPED and the remaining weights are renormalized,
rather than being scored as zero. A player with no plants recorded is not
penalized for the missing entry - but their rating is then computed from fewer
components, and the table says so. Enter plants/clutches for everyone or no one
in a given map; mixing the two makes ratings non-comparable within that map.

Plants and 1vX cannot be read from a scoreboard, so they are always manual and
always kept in their own table (manual_stats) - never mixed into screenshot data.
"""
import pandas as pd

DEFAULTS = {
    "kpr_base": 0.75, "srv_base": 35.0, "kd_base": 1.00,
    "ppr_base": 0.12, "clutch_base": 0.30,
    "w_kpr": 0.30, "w_srv": 0.20, "w_kd": 0.20, "w_plants": 0.18, "w_clutch": 0.12,
    "cap": 2.5,
}


def load_config(conn):
    from . import db as dbm
    cfg = dict(DEFAULTS)
    for k in cfg:
        v = dbm.get_setting(conn, f"rating_{k}")
        if v not in (None, ""):
            try:
                cfg[k] = float(v)
            except ValueError:
                pass
    return cfg


def save_config(conn, cfg):
    from . import db as dbm
    for k, v in cfg.items():
        dbm.set_setting(conn, f"rating_{k}", str(v))


def manual_frame(conn) -> pd.DataFrame:
    """Per player per map manual stats (plants, 1vX)."""
    return pd.read_sql_query(
        """SELECT ms.map_game_id, ms.player_id, p.name AS display_name,
                  ms.clutches, ms.plants, ms.defuses
           FROM manual_stats ms JOIN players p USING(player_id)""", conn)


def rate_players(conn, ours: pd.DataFrame, cfg=None) -> pd.DataFrame:
    """Rating table for the filtered Northwood rows in `ours`.

    Returns one row per player with the rating, its components, and how many of
    the five components actually had data.
    """
    if ours is None or ours.empty:
        return pd.DataFrame()
    cfg = cfg or load_config(conn)
    man = manual_frame(conn)
    ids = set(ours["map_game_id"].unique())
    man = man[man["map_game_id"].isin(ids)] if not man.empty else man

    rows = []
    for name, g in ours.groupby("display_name"):
        k, d = g["kills"].sum(), g["deaths"].sum()
        rounds = g["rounds_played"].sum()
        maps = g["map_game_id"].nunique()
        kpr = k / rounds if rounds else None
        srv = 100 * (rounds - d) / rounds if rounds else None
        kd = k / max(d, 1)

        mm = man[man["display_name"] == name] if not man.empty else pd.DataFrame()
        plants = mm["plants"].sum() if not mm.empty and mm["plants"].notna().any() else None
        clutch = mm["clutches"].sum() if not mm.empty and mm["clutches"].notna().any() else None
        # only count maps that actually have manual data, so rates aren't diluted
        man_maps = mm["map_game_id"].nunique() if not mm.empty else 0
        man_rounds = (g[g["map_game_id"].isin(mm["map_game_id"])]["rounds_played"].sum()
                      if not mm.empty else 0)
        ppr = (plants / man_rounds) if (plants is not None and man_rounds) else None
        cpm = (clutch / man_maps) if (clutch is not None and man_maps) else None

        parts = [
            ("KPR", kpr, cfg["kpr_base"], cfg["w_kpr"]),
            ("SRV", srv, cfg["srv_base"], cfg["w_srv"]),
            ("K/D", kd, cfg["kd_base"], cfg["w_kd"]),
            ("Plants", ppr, cfg["ppr_base"], cfg["w_plants"]),
            ("1vX", cpm, cfg["clutch_base"], cfg["w_clutch"]),
        ]
        cap = cfg.get("cap") or 99
        used = [(n, v, b, w) for n, v, b, w in parts if v is not None and b]
        wsum = sum(w for _, _, _, w in used)
        rating = (round(sum(min(v / b, cap) * w for _, v, b, w in used) / wsum, 2)
                  if wsum else None)

        rows.append({
            "Player": name, "Rating": rating,
            "Maps": maps, "Rounds": int(rounds) if pd.notna(rounds) else 0,
            "K/D": round(kd, 2), "KPR": round(kpr, 2) if kpr else None,
            "SRV%": round(srv, 1) if srv else None,
            "Plants": int(plants) if plants is not None else None,
            "1vX": int(clutch) if clutch is not None else None,
            "Plants/rnd": round(ppr, 3) if ppr is not None else None,
            "1vX/map": round(cpm, 2) if cpm is not None else None,
            "Components": f"{len(used)}/5",
            "Manual maps": man_maps,
        })
    out = pd.DataFrame(rows)
    return out.sort_values("Rating", ascending=False, na_position="last")


def component_breakdown(conn, ours: pd.DataFrame, player: str, cfg=None) -> pd.DataFrame:
    """Show exactly how one player's rating is built."""
    cfg = cfg or load_config(conn)
    t = rate_players(conn, ours, cfg)
    t = t[t["Player"] == player]
    if t.empty:
        return pd.DataFrame()
    r = t.iloc[0]
    spec = [("KPR", r["KPR"], cfg["kpr_base"], cfg["w_kpr"]),
            ("Survival %", r["SRV%"], cfg["srv_base"], cfg["w_srv"]),
            ("K/D", r["K/D"], cfg["kd_base"], cfg["w_kd"]),
            ("Plants/round", r["Plants/rnd"], cfg["ppr_base"], cfg["w_plants"]),
            ("1vX per map", r["1vX/map"], cfg["clutch_base"], cfg["w_clutch"])]
    used = [x for x in spec if x[1] is not None]
    wsum = sum(w for _, _, _, w in used) or 1
    rows = []
    for n, v, b, w in spec:
        if v is None:
            rows.append({"Component": n, "Value": None, "Baseline": b, "vs baseline": None,
                         "Weight": 0, "Contribution": None, "Note": "no data — dropped"})
        else:
            cap = cfg.get("cap") or 99
            raw = v / b
            capped = min(raw, cap)
            rows.append({"Component": n, "Value": v, "Baseline": b,
                         "vs baseline": round(raw, 2), "Weight": round(w / wsum, 3),
                         "Contribution": round(capped * (w / wsum), 3),
                         "Note": f"capped at {cap}x" if capped < raw else ""})
    return pd.DataFrame(rows)


def save_manual(conn, map_game_id, entries):
    """entries: list of {player_id, clutches, plants}. Blank values clear the row."""
    for e in entries:
        pid = e.get("player_id")
        if not pid:
            continue
        c, p = e.get("clutches"), e.get("plants")
        if c is None and p is None:
            conn.execute("DELETE FROM manual_stats WHERE map_game_id=? AND player_id=?",
                         (map_game_id, pid))
            continue
        conn.execute(
            """INSERT INTO manual_stats(map_game_id, player_id, clutches, plants)
               VALUES(?,?,?,?)
               ON CONFLICT(map_game_id, player_id)
               DO UPDATE SET clutches=excluded.clutches, plants=excluded.plants""",
            (map_game_id, pid, c, p))
    conn.commit()
