"""One-time seed: roster + map pool + the 9/16/2026 Cumberland BO3.

Data migrated from STAT_SHEET_NU_R6.xlsx:
  - Roster & roles ("Blank Sheet" tab)
  - Veto row vs Cumberland ("Map Bans Data" tab): their bans Villa/Chalet/Lair,
    their pick Bank, our pick Fortress, decider Nighthaven. Northwood's own bans
    were never recorded in the old sheet, so they are absent here (add them in
    the app if you remember them). Note: the old sheet had turned both "3-7"
    map scores into the date 2026-03-07 - a classic Excel mangling, and part of
    why the new system stores scores as numbers in a database.
Player stats below were extracted from the two supplied screenshots.
Map order (Bank = map 1, Fortress = map 2) follows the old sheet's gameday
columns; edit in the app if that's wrong.

The alias "vogez.um" is linked to Vogel but saved as status=pending -
approve or reassign it under Manage -> Roster & Aliases.

Run:  python seed.py          (safe to re-run: skips if the series already exists)
"""
from siegestats import db

SEASON = "2026-27"
COMPETITION = "CR6"  # from the old sheet's league list; edit in the app if wrong

ROSTER = [
    # name, role, confirmed gamertags
    ("Jarvis", "FLEX (IGL)", ["Jarviz."]),
    ("Fish",   "E2",         ["HokiesHi"]),
    ("Adam",   "E1",         ["Carlow-"]),
    ("Lexi",   "S1",         ["lexilemonhead"]),
    ("Vogel",  "S2",         ["Vogel.NU"]),
    ("Payton", "Sub",        ["Pay.ton"]),
]

MAP_POOL = ["Bank", "Border", "Chalet", "Club", "Consulate", "Fortress",
            "Kafe", "Lair", "Nighthaven", "Skyscraper", "Villa"]

BANK = {  # Match Replay screenshot, Match ID d636cdcd-...  Northwood (orange) 3 - 7
    "map_name": "Bank", "map_number": 1, "rounds_won": 3, "rounds_lost": 7, "result": "L",
    "siege_match_id": "d636cdcd-4c6e-4c68-b86e-b903082bb324",
    "source_file": "Tom_Clancy_s_Rainbow_Six__Siege2026-9-16-22-15-48.jpg",
    "ours": [  # gamertag, score, K, D, A
        ("Jarviz.", 3085, 9, 8, 1), ("Carlow-", 2940, 5, 9, 4), ("HokiesHi", 2672, 8, 9, 0),
        ("lexilemonhead", 2090, 3, 9, 2), ("vogez.um", 2030, 3, 7, 0)],
    "theirs": [
        ("Fluancyyy", 6160, 10, 5, 6), ("Wakko", 5928, 13, 5, 4), ("ProneStarz", 5555, 11, 6, 3),
        ("Scarinho.", 5042, 5, 6, 4), ("SPOOMAN.", 4881, 3, 6, 3)],
}
FORTRESS = {  # Live screenshot, Match ID 0c971272-...  Northwood 3 - 7
    "map_name": "Fortress", "map_number": 2, "rounds_won": 3, "rounds_lost": 7, "result": "L",
    "siege_match_id": "0c971272-9308-457a-a642-35ccc859c3d7",
    "source_file": "Tom_Clancy_s_Rainbow_Six__Siege2026-9-16-21-49-42.jpg",
    "ours": [
        ("HokiesHi", 2665, 10, 7, 1), ("Jarviz.", 2469, 8, 7, 1), ("vogez.um", 2245, 3, 9, 2),
        ("Carlow-", 2146, 6, 8, 1), ("lexilemonhead", 2084, 4, 9, 3)],
    "theirs": [
        ("SPOOMAN.", 6188, 12, 7, 5), ("Scarinho.", 5895, 11, 6, 5), ("Wakko", 5717, 7, 6, 4),
        ("Fluancyyy", 5405, 4, 7, 3), ("ProneStarz", 5140, 6, 5, 4)],
}
VETO = [  # seq, team ('us'/'opp'/None), action, map
    (1, "opp", "Ban", "Villa"), (2, "opp", "Ban", "Chalet"),
    (3, "opp", "Pick", "Bank"), (4, "us", "Pick", "Fortress"),
    (5, "opp", "Ban", "Lair"), (6, None, "Decider", "Nighthaven"),
]


def main():
    conn = db.get_conn()
    nu = db.get_or_create_team(conn, "Northwood", is_us=1)
    cu = db.get_or_create_team(conn, "Cumberland", is_us=0)
    conn.execute("INSERT INTO team_aliases(team_id, alt_name) SELECT ?, 'Cumberland Uni' "
                 "WHERE NOT EXISTS (SELECT 1 FROM team_aliases WHERE team_id=? AND alt_name='Cumberland Uni')",
                 (cu, cu))

    for name, role, tags in ROSTER:
        row = conn.execute("SELECT player_id FROM players WHERE name=? AND team_id=?", (name, nu)).fetchone()
        pid = row["player_id"] if row else conn.execute(
            "INSERT INTO players(name, team_id, role) VALUES(?,?,?)", (name, nu, role)).lastrowid
        for t in tags:
            db.add_alias(conn, pid, t, status="confirmed", seen="2026-09-16")
    vogel = conn.execute("SELECT player_id FROM players WHERE name='Vogel'").fetchone()["player_id"]
    db.add_alias(conn, vogel, "vogez.um", status="confirmed", seen="2026-09-16")  # confirmed by Jon 9/17

    for m in MAP_POOL:
        conn.execute("INSERT OR IGNORE INTO map_pool(map_name, season, active) VALUES(?,?,1)", (m, SEASON))
    conn.commit()

    if conn.execute("SELECT 1 FROM series WHERE date='2026-09-16' AND opponent_id=?", (cu,)).fetchone():
        print("Cumberland 9/16/2026 series already present - nothing to do.")
        return

    sid = conn.execute(
        "INSERT INTO series(date, season, competition, opponent_id, format, notes) VALUES(?,?,?,?,?,?)",
        ("2026-09-16", SEASON, COMPETITION, cu, "BO3",
         "Imported from screenshots. Northwood bans not recorded in old sheet.")).lastrowid

    def tag_to_pid(tag):
        r = conn.execute("SELECT player_id FROM aliases WHERE gamertag=?", (tag,)).fetchone()
        return r["player_id"] if r else None

    for game in (BANK, FORTRESS):
        rounds = game["rounds_won"] + game["rounds_lost"]
        stat_rows = [
            {"team_id": nu, "player_id": tag_to_pid(g), "gamertag_displayed": g,
             "score": sc, "kills": k, "deaths": d, "assists": a, "rounds_played": rounds}
            for g, sc, k, d, a in game["ours"]
        ] + [
            {"team_id": cu, "player_id": None, "gamertag_displayed": g,
             "score": sc, "kills": k, "deaths": d, "assists": a, "rounds_played": rounds}
            for g, sc, k, d, a in game["theirs"]
        ]
        db.save_map_with_stats(conn, {**{k: v for k, v in game.items() if k not in ("ours", "theirs")},
                                      "series_id": sid, "import_status": "confirmed"}, stat_rows)

    team_of = {"us": nu, "opp": cu, None: None}
    for seq, who, action, mp in VETO:
        conn.execute("INSERT INTO veto_events(series_id, seq, team_id, action, map_name) VALUES(?,?,?,?,?)",
                     (sid, seq, team_of[who], action, mp))
    conn.commit()
    print("Seeded: roster, map pool, and the Cumberland BO3 (Bank L 3-7, Fortress L 3-7).")


if __name__ == "__main__":
    main()
