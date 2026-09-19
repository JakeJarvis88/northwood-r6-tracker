# Northwood R6 Stat Tracker

Screenshot → review → database → dashboards. One SQLite database (`data/siege.db`)
is the source of truth; Excel is just an export you can regenerate any time.

## Sharing it with the team

**See `DEPLOY.md`** for the full free setup. Short version:

- **Just you importing?** Run it locally and give teammates the Google Sheet link.
- **Everyone using it?** Deploy free to Streamlit Community Cloud. Teammates open a
  URL and log in with a shared password: the **viewer** password gives read-only
  dashboards, the **editor** password allows importing and editing. Set both under
  Streamlit's Secrets; with neither set (local use) the app runs wide open.

Free hosting has no permanent disk, so the Google Sheet doubles as the durable
store: every sync writes a complete `_db_` copy of every table, and the app
rebuilds from it on boot. Manage → Data has both **Sync to Sheets** and
**Restore from Sheets**.

No part of this stack has a subscription. The only thing that can cost anything is
the screenshot reader, billed pay-as-you-go per request from prepaid credit —
about two cents a scoreboard on the default Sonnet model, so roughly $1 for a
40-map season against the $5 of free credit a new API account starts with. Nothing
auto-renews, idle months cost $0, and without a key the app still works with
manual entry.

## Setup (once)

```bash
pip install -r requirements.txt
streamlit run app.py
```

The included `data/siege.db` already contains the roster, aliases, 2026-27 map
pool, and the 9/16/2026 Cumberland BO3 (Bank L 3-7, Fortress L 3-7) imported
from your two screenshots, plus the veto data migrated from your old sheet.
To rebuild from scratch: delete `data/siege.db` and run `python seed.py`.

**Screenshot reading** uses the Anthropic vision API. In the app go to
**Manage → Settings** and paste an API key (from console.anthropic.com), or set
the `ANTHROPIC_API_KEY` environment variable. Without a key everything still
works — you just type the numbers into the same review table yourself.

## Google Sheets (team-facing copy)

The local database stays the source of truth (imports, alias approval and
duplicate checks need transactional writes — and a Sheet-as-database is how
"3-7" became March 7 in the old workbook). Every sync **pushes the complete
data set** — dashboards *and* raw rows — to one spreadsheet your teammates can
open read-only from anywhere. Anything typed into the Sheet is overwritten on
the next sync. Six tabs: Team · Players · Player Splits · Scouting · Matches ·
Raw Data.

One-time setup (~5 min):
1. [console.cloud.google.com](https://console.cloud.google.com) → create a project.
2. APIs & Services → enable **Google Sheets API** and **Google Drive API**.
3. IAM & Admin → Service Accounts → create one → Keys → **Add key (JSON)** →
   save the file next to `app.py` (e.g. `google_creds.json`).
4. Create a Google Sheet and **Share** it (Editor) with the service account's
   `...@...iam.gserviceaccount.com` email. Share the same link view-only with the team.
5. In the app: **Manage → Settings** → paste the Sheet URL and the JSON path,
   and (recommended) tick *Auto-sync after every confirmed import*.
   Test with **Manage → Data → Sync now**.

## After a match (the whole workflow)

1. **Import Match** page → pick the series or click *New series* (opponent,
   date, BO1/BO3, and **Gameday or Scrim**).
   *Already created it wrong?* **✏️ Edit this series** under the picker changes
   the opponent, competition, season, date, format, match type, VOD and notes
   at any time — including after finalizing.
2. Drag in the scoreboard screenshot(s) → **Read screenshots**.
3. Review each map: detected map / Match ID / score, which block is Northwood
   (auto-detected from gamertags, overridable), and a fully editable player
   table with per-row confidence and notes like *unknown gamertag* or *low OCR
   confidence*. Fix anything, then **Confirm import**.
4. Add the veto (`Northwood – Ban – Villa`, one row per action). Operator
   bans can be typed right in each map's review card (they save with the
   import) or in the form below after saving.
5. Optional but high value: add the **starting side + half-1 score**, and attach a
   **halftime screenshot** for per-player attack/defense stats.
6. Hit **🏁 Finalized — everything is in**. It shows a checklist (maps saved,
   veto recorded, anything still in-progress), locks the series with a ✔ in
   the series list, and pushes to Google Sheets if configured. Importing or
   editing later reopens it automatically.
7. Dashboards update immediately. Need to start over? *Delete this entire
   series* sits right under the series picker (checkbox-guarded).

### Gamertag aliases
- A tag you assign during an import is remembered as a confirmed alias, so it
  auto-matches next time. Players can hold any number of historical tags.
- Near-miss tags (e.g. `vogez.um` vs `Vogel.NU`) are only *suggested* and must
  be confirmed; `vogez.um` is currently sitting in **Manage → Roster & Aliases**
  as *pending* — approve it there if that really is Vogel's new tag.

### Duplicates
Re-uploading the same map (same Siege Match ID, or same map+opponent+date with
overlapping gamertags) triggers a conflict prompt: save as new / **update the
existing map** (use this when a final board follows an in-progress shot) /
skip. Nothing is ever overwritten silently. Mark mid-map screenshots as
`in_progress` so the final one can cleanly replace them.

## Built-in safety net: the kill/death cross-check

Every review card runs integrity checks before you can save. The strongest one
is arithmetic: in a 5v5 map, one team's kills **must** equal the other team's
deaths. A single misread digit breaks that identity, so the app catches OCR
errors without you re-reading the screenshot. It also flags impossible values
(more deaths than rounds), duplicate gamertags, rosters that aren't 5-a-side,
and scores that look mid-map. Hard errors disable the Confirm button until you
fix them or tick *Import anyway*.

## Scout report

Opponents tab → **📋 Scout report**. Generates a paste-ready pre-match sheet:
record against them, every map played with round splits, their bans and picks,
our bans and picks, pool maps never seen against them, their top players by
K/D from our own scoreboards, and veto suggestions that each cite the evidence
behind them. Small samples are labeled as such rather than dressed up as reads.

## Attack / defense splits (team level)

The round strip above the scoreboard is a full round-by-round record, and the
reader now parses it: each marker sits on the winning team's line, its icon says
how the round ended (elimination / time / defuse / disabled), and the ATK/DEF
labels over each half give the sides. So the import figures out the starting
side and the half-1 score itself — you confirm rather than type.

What you get:
- **ATK vs DEF round win %** for the team, overall and per map, with a **Gap**
  column (ATK% − DEF%). A big negative gap means the map is costing you on
  attack — a ban argument, or a practice target.
- **How rounds are won and lost**, split by side. Losing defense rounds to
  *defuse* or *time* points at site play; losing them to *elimination* points at
  fights. Same map, different fix.
- A **round-by-round table** in the review card so you can correct any marker the
  kill feed covered before saving.

Two safeguards: the strip's round count is checked against the final score and a
mismatch blocks the import until you resolve it, and if no strip is readable the
card falls back to typing the half-1 score (or skipping side data entirely — the
map still imports).

**Your league's format is the default:** first to 7 wins the map; sides swap after
round 6; at 6-6 it goes to overtime as first to 8, so overtime is at most 3 rounds
and sides don't swap inside it. Side choice follows your rules — the team that
didn't pick the map chooses the second-half side, the team that did chooses the OT
side — and the review card reads your stored veto to remind you which applies.
Both swap numbers are configurable under Manage → Settings.

Per-player attack/defense splits aren't in the UI (they'd need a halftime
screenshot every map). The groundwork sits in `sides.py` if that changes.

## Operator bans

Your league's structure is built in: **6 bans per team per map** — two attackers
before round 1 plus one more after round 3 of the attack half, and the same on
defense. The review card has a 12-slot grid (both teams, both sides, both
phases); `Side` means the *banned operator's* side, not the banning team's.

The **🚫 Op Bans** dashboard tab answers two different questions side by side:

- **Bans against us** — how Northwood performs when an operator is taken away
  from us.
- **Our bans** — how we perform when we take one away from them.

Filter by phase (all / opening 2 / final only), and the sidebar's map, opponent,
season and gameday-vs-scrim filters apply throughout. Each row carries maps,
times banned, map W-L, round win %, and a **side-specific** round win % — a
banned attacker is missing from whoever is attacking, so an opponent's attacker
ban bites in *our attack rounds*, and that column tracks exactly those rounds
(it needs round-by-round data from the strip).

There's also a **by ban set** view, because bans interact — losing two site
anchors together isn't the sum of losing each alone — and a coverage table
showing which maps have all 12 slots filled.

Everything here is observational: a ban that coincided with a loss isn't proof it
caused the loss. Rows covering fewer than 3 maps are flagged ⚠️.

## Editing past matches

**Manage → Data → ✏️ Edit a past match** — pick any series and edit its **map
bans (veto)** or, per map, its **operator bans**. Below that, **1vX & plants** and
**side data** have their own per-map editors, and series details (opponent,
competition, date, format, match type) are editable from the Import page. Nothing
is write-once; finalized series reopen automatically when edited.

## Player rating

1vX clutches and plants can't be read from a scoreboard, so the review card has a
**✋ 1vX and plants** box for Northwood players at import time (blank = not
recorded, which is different from zero). Old maps can be backfilled under
Manage → Data. These stay in their own table and never mix with screenshot data.

The rating combines five components, each as *value ÷ baseline*, weighted:

| Component | Source | Baseline | Weight |
|---|---|---|---|
| KPR | screenshot | 0.75 | 0.30 |
| Survival % | screenshot | 35% | 0.20 |
| K/D | screenshot | 1.00 | 0.20 |
| Plants per round | manual | 0.12 | 0.18 |
| 1vX per map | manual | 0.30 | 0.12 |

**1.00 = baseline across the board.** Every baseline, weight, and the component
cap is editable under Manage → Settings, and Player Detail shows the full
breakdown — value, baseline, ratio, weight, contribution — so any rating traces
back to its parts.

Two deliberate behaviours: components with no data are **dropped and the rest
reweighted**, not scored as zero (the table shows 3/5 vs 5/5 so you know), and each
component is **capped at 2.5× baseline** — over two maps a single clutch is 3.3×
baseline and would otherwise outweigh everything else. The cap is flagged in the
breakdown whenever it bites.

Record 1vX and plants for every Northwood player in a map or none of them; mixing
within one map makes those ratings non-comparable.

### Reader accuracy fixtures

`tests/strip_fixtures.json` holds hand-verified ground truth for five real
scoreboards (round winners, how each round ended, starting side, half scores),
including a Blue/Orange replay where the orange team is listed first. The
parser is unit-tested against all five. `tests/check_reader.py` runs the *live*
reader on those screenshots and prints a scorecard — run it after changing the
prompt or model, before trusting a new configuration on a real match.

## Gameday vs scrim

Every series is tagged **Gameday** (official/league) or **Scrim** (practice).
The dashboard sidebar has a *Match type* switch — **Gameday only** (the
default, so practice never inflates your league numbers), **Scrims only**, or
**Both (total)**. Player Detail gains a gameday-vs-scrim split table whenever
both exist, and the Google Sheet publishes all three views (Gameday / Scrim /
Total) so the team can compare practice form against match form.

## Stat definitions
- **K/D** = kills/deaths; with 0 deaths it displays the kill count (kills/1).
- **KPR** = kills / rounds played · **SRV%** = (rounds played − deaths) / rounds played
- **DPR**, **APR**, **+/-** = deaths per round, assists per round, kills − deaths.
- Rounds played defaults to the map's total rounds; edit per player if someone subbed mid-map.
- Only Score/K/D/A/rounds come from screenshots. KOST, clutches, plants, defuses
  **cannot** be read from a scoreboard and live in a separate optional
  `manual_stats` table — they are never fabricated.
- Ping is never stored.

## Other tools

- **Last 5 maps** on the Team tab — per-map team K/D and round differential, the
  quickest read on current form.
- **⚖️ Compare players** on the Players tab — up to 4 players side by side across
  every metric.
- **Backups** under Manage → Data — download the whole `siege.db`, or all rows as
  CSV. Restoring is just dropping the file back into `data/`.

## Files

| File | Purpose |
|---|---|
| `app.py` | entry point: page config, login, cloud bootstrap, navigation |
| `ui/common.py` | shared context (connection, home team), cached data loaders, feedback helpers |
| `ui/import_page.py` | Import Match page: series, screenshots, review cards, veto, finalize |
| `ui/dashboards.py` | Dashboards page (six tabs, click-to-drill) |
| `ui/manage.py` | Manage page: roster, map pool, teams, data, settings |
| `ui/charts.py` | Altair chart builders |
| `siegestats/db.py` | schema, migrations, transactional saves |
| `siegestats/reader.py` | vision-API screenshot reader, truncation repair, manual fallback |
| `siegestats/matching.py` | gamertag ↔ player fuzzy matching |
| `siegestats/dedupe.py` | duplicate detection |
| `siegestats/stats.py` | stat math and filtering |
| `siegestats/sides.py` | attack/defense splits, round-strip parsing |
| `siegestats/insights.py` | integrity checks, scout report, form, comparisons |
| `siegestats/rating.py` | manual 1vX/plants + composite player rating |
| `siegestats/opbans.py` | operator ban tracking and analysis |
| `siegestats/gsheets.py` | Google Sheets report publish |
| `siegestats/store.py` | full-database backup/restore on Google Sheets |
| `siegestats/export_excel.py` | Excel report export |
| `seed.py` | rebuilds roster/map pool/Cumberland series |

Back up by copying `data/siege.db`. Map pool, roster, opponents and seasons are
all data, not code — edit them under **Manage** as rosters and pools rotate.
