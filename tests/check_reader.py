"""Score the live screenshot reader against hand-verified fixtures.

Usage:  ANTHROPIC_API_KEY=sk-ant-... python tests/check_reader.py [model]
Reads each screenshot in data/screenshots/, compares the round strip, starting
side, and per-player K/D/A against tests/strip_fixtures.json, and prints a
scorecard. Costs a few cents per run. Run it after changing the prompt or the
model so regressions show up here instead of in a real import.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from siegestats import reader, sides  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, "..", "data", "screenshots")


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else reader.DEFAULT_MODEL
    fixtures = json.load(open(os.path.join(HERE, "strip_fixtures.json")))["fixtures"]
    total = hits = 0
    for f in fixtures:
        path = os.path.join(SHOTS, f["file"])
        if not os.path.exists(path):
            print(f"skip {f['file']} (not in data/screenshots)")
            continue
        ex = reader.read_screenshot(open(path, "rb").read(), f["file"], model=model)
        strip = ex.get("round_strip") or {}
        rows, det = sides.rounds_from_strip(strip, our_block_is_top=(f["our_block"] == "top"))
        raw_won = [r["round_number"] for r in rows if r["won"] == 1]
        e = f["expect"]
        checks = {
            "map": (ex.get("map") or "").upper() == f["map"],
            "blue_block": strip.get("blue_block") == f["round_strip"]["blue_block"],
            "start side": det == e["starting_side"],
            "round count": len(rows) == len(f["round_strip"]["rounds"]),
            "winners": raw_won == e["we_won"],
            "conditions": [r["win_condition"] for r in rows] ==
                          [x["win_condition"] for x in f["round_strip"]["rounds"]],
        }
        # after the app's reconciliation, does it land on the truth anyway?
        fixed, _ = sides.reconcile_rounds(rows, *f["score"])
        checks["winners after fix"] = [r["round_number"] for r in fixed if r["won"] == 1] == e["we_won"]
        n_ok = sum(checks.values())
        total += len(checks)
        hits += n_ok
        print(f"\n{f['map']} {f['score'][0]}-{f['score'][1]}  ({f['file']})  {n_ok}/{len(checks)}")
        for k, v in checks.items():
            print(f"   {'✓' if v else '✗'} {k}")
        if not checks["winners"]:
            print(f"     read winners: {raw_won}  expected: {e['we_won']}")
        if ex.get("notes"):
            print(f"   notes: {ex['notes'][:160]}")
    print(f"\nmodel {model}: {hits}/{total} checks passed")


if __name__ == "__main__":
    main()
