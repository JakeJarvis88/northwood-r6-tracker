"""Scoreboard screenshot reader.

Uses the Anthropic vision API (a multimodal model) rather than classical OCR:
Siege's stylized UI, translucent panels and varying resolutions defeat
Tesseract-style OCR far too often, and reliability was the requirement.
Needs an API key (Manage -> Settings, or the ANTHROPIC_API_KEY env var).
If no key is configured the app still works: every field can be typed in
the same review screen the reader fills out.

Verified column order on the Siege scoreboard (both live and Match Replay):
    SCORE | KILLS | DEATHS | ASSISTS | PING
Ping is displayed but is NOT a gameplay stat and is never stored.
"""
import base64
import json
import mimetypes
import os

# Sonnet reads these scoreboards more reliably than Haiku and still costs about
# two cents a screenshot. Pay-as-you-go per request - no subscription.
# Switch models under Manage -> Settings.
DEFAULT_MODEL = "claude-sonnet-5"

# A full read is 10 player rows + up to ~15 round-strip entries + bans, which runs
# well past a small cap. Too low and the JSON is silently truncated mid-array.
MAX_TOKENS = 8000

PROMPT = """You are reading a Tom Clancy's Rainbow Six Siege scoreboard screenshot (live match or Match Replay).

COLUMNS. Left to right after the gamertag there are exactly FIVE numeric columns:
  1 SCORE   (star icon)      - hundreds or thousands, e.g. 2665, 5606
  2 KILLS   (crosshair icon) - single digits, rarely above 20
  3 DEATHS  (skull icon)     - single digits, never more than the rounds played
  4 ASSISTS (wing icon)      - small, typically 0-6, almost never above 10
  5 PING    (signal bars)    - THE LAST COLUMN. Usually 15-90. DISCARD IT.

The single most common mistake is reporting PING as assists. Guard against it:
 - assists is the FOURTH number, ping is the FIFTH and last.
 - if a value you are about to call "assists" is above 15, you have almost certainly
   grabbed the ping column - recount the columns from the left and fix it.
 - assists across all ten players usually total under 30. Ping values look like
   22, 26, 31, 41, 43 and vary independently of performance.
If a row has only four numbers visible, decide which one is missing rather than
shifting the others over; report null for what you cannot see.
In Match Replay the ping column is usually 0. Replay screenshots may also show a bottom
row of operator cards reading K/D/A per player - use those to cross-check the table.

Transcribe EXACTLY what is visible. Never guess: if a value is unreadable, use null.

Return ONLY a JSON object (no markdown fences, no commentary) with this shape:
{
  "map": string|null,               // e.g. "FORTRESS" header next to the game mode
  "game_mode": string|null,         // e.g. "BOMB"
  "match_id": string|null,          // the full "Match ID: ..." string value if visible
  "is_replay": boolean,             // true if REPLAY watermark / replay controls are visible
  "round_strip": {                  // the row of round markers above the player table
     "blue_block": "top"|"bottom",   // which PLAYER-TABLE block is the BLUE team (see rules)
     "blue_team_first_half_side": "ATK"|"DEF"|null,  // the ATK/DEF label ABOVE the first group
     "rounds": [                     // one entry per round that has been played, in order
       {"round": int,
        "winner": "blue"|"red"|null,        // color of the filled marker for that column
        "win_condition": "elimination"|"objective"|"time"|"unknown",
        "confidence": float}
     ],
     "confidence": float
  },
  "score_banner": {                 // left-edge team banners if present
     "top_label": string|null, "top_score": int|null,
     "bottom_label": string|null, "bottom_score": int|null
  },
  "teams": [                        // exactly two, in top-to-bottom scoreboard order
    {"label": string|null,          // team name or "BLUE TEAM"/"ORANGE TEAM" if that's all that's shown
     "score": int|null,             // that team's round score if visible
     "players": [
        {"gamertag": string, "score": int|null, "kills": int|null,
         "deaths": int|null, "assists": int|null, "confidence": float}  // 0-1 per row
     ]}
  ],
  "operator_bans": [                // only if a ban banner/icons are clearly identifiable
     {"operator": string, "banned_by_gamertag": string|null, "confidence": float}
  ],
  "notes": string|null,             // anything ambiguous worth flagging
  "overall_confidence": float
}

READING THE ROUND STRIP (the row of markers between the map name and the player table).

STEP 1 - ROUND COUNT FROM THE SCORE. The two big team scores on the left edge (or the
banner across the top in a replay) are the source of truth: their SUM is the number of
rounds played. 7-1 means 8 rounds; 3-7 means 10. There must be that many filled markers.
Never report more rounds than the score allows. If you can see fewer, some are hidden
behind the kill feed/chat/player cards - report the ones you can see and say so in notes.

STEP 2 - THE GRID. The strip is a grid of columns, one per round, grouped 6 | 6 | 3 by thin
vertical dividers (first half, second half, overtime). Column 1 is the leftmost. Each column
has an UPPER slot and a LOWER slot. Unplayed columns show a small plain outlined diamond in
both slots - skip them. A played column has exactly ONE filled, colored marker: in the
upper slot or the lower slot.

STEP 3 - WHO WON: BY COLOR, NOT POSITION.
  - The UPPER row belongs to the BLUE team. Filled markers there are blue.
  - The LOWER row belongs to the RED/ORANGE team. Filled markers there are red or orange.
Which player-table block is blue? Look at the colored team banners on the left edge: the
block whose banner is blue is the blue team. In a live match your own team is always the
blue banner and listed on TOP. In a "BLUE TEAM / ORANGE TEAM" replay the ORANGE banner can
be on top - then blue_block is "bottom" and the upper strip row belongs to the team listed
SECOND. Report blue_block honestly; do not assume top.

STEP 4 - HOW THE ROUND ENDED (icon inside the filled marker):
  - crosshair / scope / target        => "elimination"  (all five opponents killed)
  - double chevron / checkmark shape  => "objective"    (defuser planted and completed, defused, or
                                                         objective otherwise secured/defended)
  - hourglass / clock                 => "time"         (round timer ran out)
  - unreadable                        => "unknown"

STEP 5 - SIDES. Small ATK/DEF labels sit above and below the strip, once for the first group
of six and once for the second. The label ABOVE belongs to the upper row (the blue team);
the label BELOW belongs to the lower row. Report the label ABOVE the first group as
"blue_team_first_half_side". The two labels in a group are always opposites.

WORKED EXAMPLES (real screenshots):
 A) Live match, left banners "NORTHWOOD 7" (blue, top) / "IOWA 1" (red, bottom). Upper labels
    DEF then ATK. Columns 1-3 upper blue crosshairs, column 4 lower red chevron, columns 5-7
    upper blue crosshairs, column 8 upper blue chevron. Report: blue_block "top",
    blue_team_first_half_side "DEF", rounds 1-8 with winner blue except round 4 red; conditions
    elimination for 1,2,3,5,6,7, objective for 4 and 8. Sum check: 7 blue + 1 red = 7-1. OK.
 B) Replay, top banner "BLUE TEAM 7 - 3 ORANGE TEAM", left banners "ORANGE TEAM 3" on top and
    "BLUE TEAM 7" below; the orange team's players are listed first. Upper labels ATK then DEF.
    Lower row (orange): hourglass at columns 1 and 4, chevron at column 7. Upper row (blue):
    chevron 2, crosshair 3, chevron 5, crosshair 6, hourglass 8, crosshairs 9 and 10. Report:
    blue_block "bottom", blue_team_first_half_side "ATK", winners: 1 red, 2 blue, 3 blue,
    4 red, 5 blue, 6 blue, 7 red, 8 blue, 9 blue, 10 blue. Sum check: 7 blue + 3 red = 7-3. OK.

Work column by column, left to right, keep round numbers sequential from 1, give each round its
own confidence, and use below 0.5 for any marker partly covered by an overlay.

Confidence rules: 1.0 only for crisp, unambiguous text. Anything blurry, occluded by chat/UI,
or inferred from partial characters must be < 0.8 and mentioned in notes. Do not identify
operator bans from portrait icons alone unless you are genuinely certain; omit them otherwise."""


class ReaderError(Exception):
    pass


def _parse_json(text: str):
    """Parse the model's reply, repairing a truncated tail if needed.

    A cut-off reply is still mostly useful - the map, match ID and most player
    rows are already there - so rather than throwing it away we discard the
    incomplete tail and close the still-open brackets in the right order.
    Anything lost shows up as a missing row in the review table, which the user
    can see and fix.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    stack, in_str, esc, safe = [], False, False, []
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
        # a complete element ends here, so this is a safe place to cut
        if not in_str and ch in "}]":
            safe.append((i, list(stack)))

    for cut, open_stack in reversed(safe):
        candidate = text[: cut + 1]
        candidate += "".join("}" if c == "{" else "]" for c in reversed(open_stack))
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _media_type(filename):
    mt, _ = mimetypes.guess_type(filename or "")
    return mt if mt in ("image/png", "image/jpeg", "image/webp", "image/gif") else "image/jpeg"


def _call(client, image_bytes, filename, model):
    return client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": _media_type(filename),
                            "data": base64.b64encode(image_bytes).decode()}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    )


def read_screenshot(image_bytes: bytes, filename: str = "", api_key: str = None,
                    model: str = None) -> dict:
    """Send one screenshot to the vision model, return the parsed extraction dict."""
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ReaderError("No Anthropic API key configured (Manage -> Settings).")
    try:
        import anthropic
    except ImportError as e:
        raise ReaderError("The 'anthropic' package is not installed: pip install anthropic") from e

    client = anthropic.Anthropic(api_key=api_key, max_retries=2)
    try:
        msg = _call(client, image_bytes, filename, model or DEFAULT_MODEL)
    except anthropic.AuthenticationError as e:
        raise ReaderError("The API key was rejected — check it under Manage → Settings.") from e
    except anthropic.RateLimitError as e:
        raise ReaderError("Rate-limited by the API; wait a moment and retry.") from e
    except anthropic.APIConnectionError as e:
        raise ReaderError(f"Couldn't reach the API: {e}") from e
    except anthropic.APIStatusError as e:
        raise ReaderError(f"API error {e.status_code}: {getattr(e, 'message', e)}") from e
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    truncated = getattr(msg, "stop_reason", None) == "max_tokens"
    data = _parse_json(text)
    if data is None:
        if truncated:
            raise ReaderError(
                "The model ran out of output space before finishing the scoreboard. "
                "Retry; if it keeps happening, raise MAX_TOKENS in reader.py or switch "
                "to a screenshot without the round strip visible.")
        raise ReaderError(f"Model did not return valid JSON. Start of the reply: {text[:300]}")
    if truncated:
        data.setdefault("notes", "")
        data["notes"] = ((data["notes"] or "") +
                         " ⚠️ The reply was cut off, so some rows may be missing — "
                         "check the player table and round list before saving.").strip()
        data["overall_confidence"] = min(float(data.get("overall_confidence") or 1.0), 0.6)
    data.setdefault("teams", [])
    for t in data["teams"]:
        t.setdefault("players", [])
    _guard_assists(data)
    return data


def _guard_assists(data):
    """Catch the classic ping-as-assists slip before it reaches the review table.

    Assists above 15 are implausible on a Siege scoreboard, while ping routinely
    sits in the 20s-40s. When that shows up we blank the value and flag the row
    rather than silently storing a ping as a stat.
    """
    hits = []
    for team in data.get("teams", []):
        for p in team.get("players", []):
            a = p.get("assists")
            try:
                a = int(a) if a is not None else None
            except (TypeError, ValueError):
                a = None
            if a is not None and a > 15:
                hits.append(f"{p.get('gamertag', '?')} ({a})")
                p["assists"] = None
                p["confidence"] = min(float(p.get("confidence") or 1.0), 0.4)
    if hits:
        note = ("⚠️ Assist values above 15 look like the ping column, so they were cleared for: "
                + ", ".join(hits) + ". Re-enter them from the screenshot if they were real.")
        data["notes"] = ((data.get("notes") or "") + " " + note).strip()


def blank_extraction(n_per_team=5) -> dict:
    """Empty template used for fully-manual entry when no API key is set."""
    return {
        "map": None, "game_mode": None, "match_id": None, "is_replay": False,
        "round_strip": {}, "score_banner": {}, "teams": [
            {"label": "Northwood", "score": None,
             "players": [{"gamertag": "", "score": None, "kills": None, "deaths": None,
                          "assists": None, "confidence": 1.0} for _ in range(n_per_team)]},
            {"label": "", "score": None,
             "players": [{"gamertag": "", "score": None, "kills": None, "deaths": None,
                          "assists": None, "confidence": 1.0} for _ in range(n_per_team)]},
        ],
        "operator_bans": [], "notes": "manual entry", "overall_confidence": 1.0,
    }
