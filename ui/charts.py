"""Chart builders. Every chart has tooltips and, where a baseline makes sense,
a reference line - so a bar's height is never the only thing carrying meaning.
"""
import altair as alt
import pandas as pd

WIN, LOSS, NEUTRAL, ATK, DEF = "#3FB68B", "#E05C5C", "#7A8BA3", "#E86C2B", "#3E8DD9"
alt.data_transformers.disable_max_rows()


def _base(df, height=260):
    return alt.Chart(df).properties(height=height)


def round_diff(recent: pd.DataFrame, height=260):
    """Round differential per map, colored by result, most recent on the right."""
    d = recent.copy().sort_values(["date", "map_name"])
    d["Match"] = d["date"].astype(str) + " " + d["map_name"] + " vs " + d["opponent"].fillna("?")
    d["Round diff"] = d["rounds_won"] - d["rounds_lost"]
    d["Score"] = d["rounds_won"].astype(int).astype(str) + "-" + d["rounds_lost"].astype(int).astype(str)
    bars = _base(d, height).mark_bar().encode(
        x=alt.X("Match:N", sort=None, title=None, axis=alt.Axis(labelAngle=-30)),
        y=alt.Y("Round diff:Q", title="Round differential"),
        color=alt.condition(alt.datum["Round diff"] >= 0, alt.value(WIN), alt.value(LOSS)),
        tooltip=["date", "opponent", "map_name", alt.Tooltip("Score", title="Score"),
                 alt.Tooltip("map_result", title="Result"), "Round diff"])
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=NEUTRAL).encode(y="y:Q")
    return bars + zero


def map_record(mb: pd.DataFrame, height=260):
    """Rounds won vs lost per map as a diverging bar, sorted by round win %."""
    d = mb.copy()
    long = pd.concat([
        pd.DataFrame({"map_name": d["map_name"], "Side": "Rounds won", "n": d["RW"], "pct": d["Round Win%"]}),
        pd.DataFrame({"map_name": d["map_name"], "Side": "Rounds lost", "n": -d["RL"], "pct": d["Round Win%"]}),
    ])
    order = d.sort_values("Round Win%", ascending=False)["map_name"].tolist()
    return _base(long, height).mark_bar().encode(
        y=alt.Y("map_name:N", sort=order, title=None),
        x=alt.X("n:Q", title="Rounds (lost ← → won)"),
        color=alt.Color("Side:N", scale=alt.Scale(domain=["Rounds won", "Rounds lost"], range=[WIN, LOSS]),
                        legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["map_name", alt.Tooltip("Side"), alt.Tooltip("n:Q", title="Rounds"),
                 alt.Tooltip("pct:Q", title="Round win %")])


def side_split(sbm: pd.DataFrame, height=260):
    """Attack vs defense round win % per map, grouped bars with a 50% line."""
    d = sbm.copy()
    long = pd.concat([
        pd.DataFrame({"map_name": d["map_name"], "Side": "ATK", "win%": d.get("ATK win%")}),
        pd.DataFrame({"map_name": d["map_name"], "Side": "DEF", "win%": d.get("DEF win%")}),
    ]).dropna(subset=["win%"])
    bars = _base(long, height).mark_bar().encode(
        x=alt.X("map_name:N", title=None),
        xOffset="Side:N",
        y=alt.Y("win%:Q", title="Round win %", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("Side:N", scale=alt.Scale(domain=["ATK", "DEF"], range=[ATK, DEF]),
                        legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["map_name", "Side", alt.Tooltip("win%:Q", title="Round win %")])
    half = alt.Chart(pd.DataFrame({"y": [50]})).mark_rule(color=NEUTRAL, strokeDash=[4, 4]).encode(y="y:Q")
    return bars + half


def player_bars(pt: pd.DataFrame, metric: str, baseline: float = None, height=240, name_col="display_name"):
    d = pt.dropna(subset=[metric]).copy()
    order = d.sort_values(metric, ascending=False)[name_col].tolist()
    tooltips = [alt.Tooltip(name_col, title="Player"), alt.Tooltip(metric)]
    for extra in ("Maps", "Rounds", "K", "D", "A"):
        if extra in d.columns:
            tooltips.append(alt.Tooltip(extra))
    bars = _base(d, height).mark_bar(color="#5B8DEF").encode(
        x=alt.X(f"{name_col}:N", sort=order, title=None),
        y=alt.Y(f"{metric}:Q", title=metric),
        tooltip=tooltips)
    if baseline is not None:
        rule = alt.Chart(pd.DataFrame({"y": [baseline]})).mark_rule(
            color=NEUTRAL, strokeDash=[4, 4]).encode(y="y:Q")
        return bars + rule
    return bars


def kpr_vs_srv(pt: pd.DataFrame, height=280, name_col="display_name"):
    """Kills per round against survival, sized by rounds - the fragger/anchor map."""
    d = pt.dropna(subset=["KPR", "SRV%"]).copy()
    pts = _base(d, height).mark_circle(opacity=0.85).encode(
        x=alt.X("KPR:Q", title="Kills per round"),
        y=alt.Y("SRV%:Q", title="Survival %"),
        size=alt.Size("Rounds:Q", legend=None, scale=alt.Scale(range=[80, 600])),
        color=alt.Color(f"{name_col}:N", legend=None),
        tooltip=[alt.Tooltip(name_col, title="Player"), "KPR", "SRV%", "K/D", "Maps", "Rounds"])
    labels = _base(d, height).mark_text(dy=-14, fontSize=11).encode(
        x="KPR:Q", y="SRV%:Q", text=f"{name_col}:N")
    return pts + labels


def form_line(trend: pd.DataFrame, height=260):
    """Per-map K/D and KPR over time for one player."""
    d = trend.copy()
    d["Match"] = d["date"].astype(str) + " " + d["map_name"]
    d["K/D"] = (d["kills"] / d["deaths"].clip(lower=1)).round(2)
    d["KPR"] = (d["kills"] / d["rounds_played"].clip(lower=1)).round(2)
    long = d.melt(id_vars=["Match", "date", "map_name", "opponent", "map_result", "kills", "deaths"],
                  value_vars=["K/D", "KPR"], var_name="Metric", value_name="Value")
    line = _base(long, height).mark_line(point=True).encode(
        x=alt.X("Match:N", sort=None, title=None, axis=alt.Axis(labelAngle=-30)),
        y=alt.Y("Value:Q", title=None),
        color=alt.Color("Metric:N", legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["date", "opponent", "map_name", alt.Tooltip("map_result", title="Result"),
                 "kills", "deaths", "Metric", "Value"])
    one = alt.Chart(pd.DataFrame({"y": [1.0]})).mark_rule(color=NEUTRAL, strokeDash=[4, 4]).encode(y="y:Q")
    return line + one


def rating_bars(rt: pd.DataFrame, height=240):
    d = rt.dropna(subset=["Rating"]).copy()
    order = d.sort_values("Rating", ascending=False)["Player"].tolist()
    bars = _base(d, height).mark_bar().encode(
        x=alt.X("Player:N", sort=order, title=None),
        y=alt.Y("Rating:Q"),
        color=alt.condition(alt.datum.Rating >= 1.0, alt.value(WIN), alt.value(LOSS)),
        tooltip=["Player", "Rating", "K/D", "KPR", "SRV%", "Plants", "1vX", "Components"])
    rule = alt.Chart(pd.DataFrame({"y": [1.0]})).mark_rule(color=NEUTRAL, strokeDash=[4, 4]).encode(y="y:Q")
    return bars + rule


def win_conditions(wc: pd.DataFrame, height=240):
    """How rounds end, stacked by outcome, one bar per side."""
    d = wc.copy()
    return _base(d, height).mark_bar().encode(
        x=alt.X("Side:N", title=None),
        y=alt.Y("Rounds:Q", stack="zero"),
        color=alt.Color("How:N", legend=alt.Legend(orient="bottom", title=None)),
        column=alt.Column("Result:N", title=None),
        tooltip=["Side", "Result", "How", "Rounds"]).resolve_scale(y="shared")


def ban_bars(t: pd.DataFrame, height=260):
    """Times each operator was banned, colored by the side it belongs to, tooltip with our results."""
    d = t.copy()
    d["Label"] = d["Operator"] + " (" + d["Side"].fillna("?") + ")"
    order = d.sort_values(["Times", "Round win %"], ascending=[False, False])["Label"].tolist()
    return _base(d, height).mark_bar().encode(
        y=alt.Y("Label:N", sort=order, title=None),
        x=alt.X("Times:Q", title="Times banned"),
        color=alt.Color("Side:N", scale=alt.Scale(domain=["ATK", "DEF"], range=[ATK, DEF]), legend=None),
        tooltip=["Operator", "Side", "Times", "Maps", alt.Tooltip("Map W-L"),
                 alt.Tooltip("Round win %"), alt.Tooltip("Side round win %"), alt.Tooltip("Slots")])


def veto_bars(vt: pd.DataFrame, height=240):
    """Ban/pick counts per map for one team."""
    d = vt.copy()
    return _base(d, height).mark_bar().encode(
        y=alt.Y("map_name:N", sort="-x", title=None),
        x=alt.X("times:Q", title="Times"),
        color=alt.Color("action:N", legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["map_name", "action", "times"])
