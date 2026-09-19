"""Operator roster, split by side, for the ban dropdowns.

Ubisoft adds operators every season, so this is a starting list rather than a
fixed truth: Manage -> Settings lets you add or remove names, and the stored
list wins over the defaults. The ban grid also accepts free text, so a brand-new
operator can always be recorded even before the list is updated.

Which side to show: a ban removes an operator from whoever plays that side. When
you are recording bans of ATTACKERS, the attacking team loses them - so the ATK
rows list attackers, the DEF rows list defenders.
"""
import json

ATTACKERS = [
    "Ace", "Amaru", "Ash", "Blackbeard", "Blitz", "Brava", "Buck", "Capitão", "Deimos",
    "Dokkaebi", "Finka", "Flores", "Fuze", "Glaz", "Gridlock", "Grim", "Hibana", "IQ",
    "Iana", "Jackal", "Kali", "Lion", "Maverick", "Montagne", "Nomad", "Nøkk", "Osa",
    "Ram", "Sens", "Sledge", "Solid Snake", "Striker", "Thatcher", "Thermite", "Twitch",
    "Ying", "Zero", "Zofia",
]

DEFENDERS = [
    "Alibi", "Aruni", "Azami", "Bandit", "Caveira", "Castle", "Clash", "Denari", "Doc",
    "Echo", "Ela", "Fenrir", "Frost", "Goyo", "Jäger", "Kaid", "Kapkan", "Lesion",
    "Maestro", "Melusi", "Mira", "Mozzie", "Mute", "Oryx", "Pulse", "Rauora", "Rook",
    "Sentry", "Skopós", "Smoke", "Solis", "Tachanka", "Thorn", "Thunderbird", "Tubarão",
    "Valkyrie", "Vigil", "Wamai", "Warden",
]

SETTING_KEY = "operator_roster"


def load(conn):
    """{'ATK': [...], 'DEF': [...]} - stored override if present, else defaults."""
    from . import db as dbm
    raw = dbm.get_setting(conn, SETTING_KEY)
    if raw:
        try:
            data = json.loads(raw)
            atk = [x for x in data.get("ATK", []) if str(x).strip()]
            dfd = [x for x in data.get("DEF", []) if str(x).strip()]
            if atk and dfd:
                return {"ATK": sorted(atk, key=str.lower), "DEF": sorted(dfd, key=str.lower)}
        except (ValueError, AttributeError):
            pass
    return {"ATK": list(ATTACKERS), "DEF": list(DEFENDERS)}


def save(conn, atk, dfd):
    from . import db as dbm
    dbm.set_setting(conn, SETTING_KEY, json.dumps({
        "ATK": sorted({str(x).strip() for x in atk if str(x).strip()}, key=str.lower),
        "DEF": sorted({str(x).strip() for x in dfd if str(x).strip()}, key=str.lower)}))


def reset(conn):
    from . import db as dbm
    dbm.set_setting(conn, SETTING_KEY, "")


def for_side(conn, side):
    return load(conn).get(side, [])


def options_for(conn, side, include=None):
    """Dropdown options for a side, with any already-stored value kept even if
    it isn't on the list (so an old or custom entry never silently vanishes)."""
    opts = list(for_side(conn, side))
    if include and str(include).strip() and str(include) not in opts:
        opts.insert(0, str(include))
    return [""] + opts
