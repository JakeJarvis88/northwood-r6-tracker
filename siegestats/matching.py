"""Gamertag -> canonical player matching.

Rules:
- Exact match (after normalization) to a confirmed alias => auto-assign.
- Fuzzy match above SUGGEST threshold => suggested, must be confirmed in the review screen.
- Below threshold => unknown, user assigns (or leaves as opponent/unmapped).
Nothing is permanently associated until the user confirms an import; on confirm,
the gamertag is saved as a (confirmed) alias for that player so it auto-matches next time.
"""
import re
from difflib import SequenceMatcher

AUTO_THRESHOLD = 0.97      # effectively exact-after-normalization
SUGGEST_THRESHOLD = 0.55   # below this we don't even suggest


def normalize(tag: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (tag or "").lower())


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    # OCR errors and tag tweaks usually keep the start of the name intact -> prefix bonus
    prefix = 0
    for x, y in zip(na, nb):
        if x != y:
            break
        prefix += 1
    prefix_score = prefix / max(len(na), len(nb))
    return max(ratio, 0.5 * ratio + 0.5 * prefix_score)


def match_gamertag(conn, tag: str, team_id=None):
    """Return dict(player_id, player_name, confidence, kind) or None.

    kind: 'exact' (confirmed alias), 'pending' (previously seen, awaiting approval),
          'fuzzy' (suggestion only).
    """
    q = """SELECT a.player_id, a.gamertag, a.status, p.name FROM aliases a
           JOIN players p USING(player_id) WHERE p.active=1"""
    args = []
    if team_id:
        q += " AND p.team_id=?"; args.append(team_id)
    best = None
    for row in conn.execute(q, args).fetchall():
        sim = similarity(tag, row["gamertag"])
        if sim >= AUTO_THRESHOLD:
            kind = "exact" if row["status"] == "confirmed" else "pending"
            return {"player_id": row["player_id"], "player_name": row["name"],
                    "confidence": round(sim, 2), "kind": kind, "matched_alias": row["gamertag"]}
        if sim >= SUGGEST_THRESHOLD and (best is None or sim > best["confidence"]):
            best = {"player_id": row["player_id"], "player_name": row["name"],
                    "confidence": round(sim, 2), "kind": "fuzzy", "matched_alias": row["gamertag"]}
    return best


def identify_our_side(conn, team_blocks, our_team_id):
    """team_blocks: list of dicts with 'players': [{'gamertag':...}].
    Returns (index_of_our_block, hits_per_block). Works regardless of
    blue/orange, top/bottom, or side labels."""
    hits = []
    for block in team_blocks:
        n = 0
        for p in block.get("players", []):
            m = match_gamertag(conn, p.get("gamertag", ""), team_id=our_team_id)
            if m and m["confidence"] >= SUGGEST_THRESHOLD:
                n += m["confidence"]
        hits.append(n)
    if not hits or max(hits) == 0:
        return None, hits
    return hits.index(max(hits)), hits
