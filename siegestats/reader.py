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

Column order on the scoreboard, left to right after the gamertag, is:
SCORE (star icon), KILLS (crosshair icon), DEATHS (skull icon), ASSISTS, PING (signal bars).
Ping is not a stat - read it only to avoid confusing it with assists, then discard it.
In Match Replay the ping column is usually 0. Replay screenshots may also show a bottom
row of operator cards with K/D/A per player - use those to cross-check the table.

Transcribe EXACTLY what is visible. Never guess: if a value is unreadable, use null.

Return ONLY a JSON object (no markdown fences, no commentary) with this shape:
{
  "map": string|null,               // e.g. "FORTRESS" header next to the game mode
  "game_mode": string|null,         // e.g. "BOMB"
  "match_id": string|null,          // the full "Match ID: ..." string value if visible
  "is_replay": boolean,             // true if REPLAY watermark / replay controls are visible
  "round_strip": {                  // the row of round markers above the player table
     "top_team_first_half_side": "ATK"|"DEF"|null,  // ATK/DEF label over the FIRST group of rounds, TOP line
     "rounds": [                     // one entry per round that has been played, in order
       {"round": int,
        "winner": "top"|"bottom"|null,      // which scoreboard block won it (see rules below)
        "win_condition": "elimination"|"time"|"defuse"|"disabled"|"unknown",
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
It is a round-by-round record, left to right, one marker per round played:
 - Each round's marker sits on the line of the team that WON it: a marker on the UPPER line
   means the team listed first in the player table won that round, LOWER line means the other
   team won. The winner's marker is also filled/colored while unplayed rounds are plain
   outlined diamonds - do not report unplayed rounds at all.
 - The icon inside the marker says how the round ended: crosshair = all opponents eliminated,
   hourglass = time expired, chevrons/down-arrows = objective (defuser planted and run down,
   or defused), a struck-through or distinct icon = objective disabled. Use "unknown" rather
   than guessing when the icon is unclear.
 - ATK/DEF labels appear above and below the strip, once per half. The UPPER label belongs to
   the team listed first in the player table; the LOWER label to the other team. Read the label
   over the FIRST (leftmost) group of rounds for "top_team_first_half_side".
 - Count carefully and keep the rounds in order; the number of reported rounds should match the
   two teams' scores added together. Overlays (kill feed, chat, player cards) sometimes cover
   part of the strip - report only the rounds you can actually see and lower the confidence.

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
    return data


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
