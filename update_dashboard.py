import os
from datetime import datetime, timedelta, date
from garminconnect import Garmin

# =====================================================================
# Config
# =====================================================================
HISTORY_DAYS = 180          # how far back to pull runs for charts/log
RHR_TREND_DAYS = 7          # resting HR sparkline window
VO2_TREND_DAYS = 84         # ~12 weeks, for the VO2 max trend sparkline

RACE_DATE = date(2026, 11, 8)
RACE_NAME = "Half Marathon"

MI_PER_M = 1 / 1609.344
FT_PER_M = 3.28084

# =====================================================================
# Unit + formatting helpers
# =====================================================================
def m_to_mi(m):
    return (m or 0) * MI_PER_M

def m_to_ft(m):
    return (m or 0) * FT_PER_M

def pace_sec_per_mile(distance_m, duration_s):
    mi = m_to_mi(distance_m)
    if not mi or mi <= 0 or not duration_s:
        return None
    return duration_s / mi

def fmt_pace(sec_per_mile):
    if sec_per_mile is None:
        return "—"
    total = int(round(sec_per_mile))
    mm, ss = divmod(total, 60)
    return f"{mm}:{ss:02d}"

def fmt_duration(seconds):
    if seconds is None:
        return "—"
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def safe_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None

def safe_method_call(obj, method_name, *args, **kwargs):
    # Like safe_call, but for methods we're not 100% sure exist on the
    # installed garminconnect version — getattr (rather than obj.method_name
    # directly) means a missing method degrades to None instead of an
    # AttributeError that would crash the whole script before safe_call's
    # own try/except ever gets a chance to run.
    fn = getattr(obj, method_name, None)
    if fn is None:
        return None
    return safe_call(fn, *args, **kwargs)

def dig(d, *keys, default=None):
    cur = d
    try:
        for k in keys:
            cur = cur[k]
        return cur
    except Exception:
        return default

# =====================================================================
# Parsing raw Garmin activity dicts into a normalized shape
# =====================================================================
def parse_run(a):
    try:
        dt = datetime.strptime(a["startTimeLocal"][:10], "%Y-%m-%d").date()
    except Exception:
        return None
    return {
        "id": a.get("activityId"),
        "name": a.get("activityName") or "Run",
        "date": dt,
        "distance": a.get("distance") or 0,
        "duration": a.get("duration") or 0,
        "avg_hr": a.get("averageHR"),
        "elev_gain": a.get("elevationGain"),
    }

def week_start(d):
    return d - timedelta(days=d.weekday())

# =====================================================================
# Pure computation (no network) — weekly aggregation + insights
# =====================================================================
def build_weekly(runs):
    buckets = {}
    for r in runs:
        wk = week_start(r["date"])
        b = buckets.setdefault(wk, {"miles": 0.0, "sec": 0.0, "count": 0})
        b["miles"] += m_to_mi(r["distance"])
        b["sec"] += r["duration"]
        b["count"] += 1
    weeks = []
    for wk in sorted(buckets):
        b = buckets[wk]
        avg_pace = (b["sec"] / b["miles"]) if b["miles"] > 0 else None
        weeks.append({"week": wk, "miles": b["miles"], "count": b["count"], "avg_pace": avg_pace})
    return weeks

def fmt_pace_diff(sec_per_mile):
    sec = abs(sec_per_mile)
    if sec < 60:
        return f"{sec:.0f} sec/mi"
    return f"{fmt_pace(sec)}/mi"

def build_insights(weeks, runs, today=None):
    insights = []
    if not weeks or not runs:
        return ["Not enough run history yet to generate insights — check back after a few more runs."]

    # Don't compare an in-progress week against a full 4-week average — that
    # always reads as "way down" until Sunday night. Use the last *completed*
    # week instead, and only fall back to the current one if it's the only
    # data point available.
    this_week_start = week_start(today) if today else None
    if this_week_start is not None and weeks[-1]["week"] == this_week_start and len(weeks) >= 2:
        comparison, comparison_label, prior = weeks[-2], "Last week's", weeks[:-2][-4:]
    else:
        comparison, comparison_label, prior = weeks[-1], "This week's", weeks[:-1][-4:]
    if prior:
        avg4 = sum(w["miles"] for w in prior) / len(prior)
        if avg4 > 0.1:
            pct = (comparison["miles"] - avg4) / avg4 * 100
            direction = "up" if pct >= 0 else "down"
            insights.append(
                f"{comparison_label} mileage ({comparison['miles']:.1f} mi) is {abs(pct):.0f}% {direction} "
                f"vs the trailing 4-week average ({avg4:.1f} mi)."
            )

    paced_weeks = [w for w in weeks if w["avg_pace"]]
    if len(paced_weeks) >= 5:
        recent4 = paced_weeks[-4:]
        prior4 = paced_weeks[-8:-4] if len(paced_weeks) >= 8 else paced_weeks[:-4]
        if prior4:
            r_avg = sum(w["avg_pace"] for w in recent4) / len(recent4)
            p_avg = sum(w["avg_pace"] for w in prior4) / len(prior4)
            diff = p_avg - r_avg
            if abs(diff) >= 3:
                word = "faster" if diff > 0 else "slower"
                insights.append(
                    f"Your last 4 weeks have averaged {fmt_pace_diff(diff)} {word} "
                    f"than the 4 weeks before that."
                )

    longest = max(runs, key=lambda r: r["distance"])
    insights.append(
        f"Longest run in this period: {m_to_mi(longest['distance']):.2f} mi "
        f"({longest['name']}, {longest['date'].strftime('%b %-d')})."
    )

    last12 = weeks[-12:]
    active_weeks = sum(1 for w in last12 if w["count"] > 0)
    insights.append(f"You ran in {active_weeks} of the last {len(last12)} weeks.")

    total_miles = sum(w["miles"] for w in weeks)
    insights.append(f"Total distance: {total_miles:.1f} mi across {len(runs)} runs over this period.")

    return insights

def compute_acwr(runs, today):
    # Acute:Chronic Workload Ratio — a standard, well-established way to flag
    # injury risk from ramping training volume too quickly. Acute = last 7
    # days of mileage; chronic = the weekly average over the last 28 days.
    # Computed directly from logged runs rather than a Garmin endpoint, so
    # there's no guessing about field names here — it's fully reliable.
    def miles_in(days_back):
        lo = today - timedelta(days=days_back - 1)
        return sum(m_to_mi(r["distance"]) for r in runs if lo <= r["date"] <= today)
    acute = miles_in(7)
    chronic_total = miles_in(28)
    chronic_weekly_avg = chronic_total / 4
    if chronic_weekly_avg <= 0:
        return None
    return acute / chronic_weekly_avg

# =====================================================================
# Race countdown + "what should I do today" recommendation
# =====================================================================
def race_phase(today, race_date):
    days_left = (race_date - today).days
    if days_left < 0:
        return "Post-Race", days_left
    if days_left <= 13:
        return "Taper", days_left
    if days_left <= 24:
        return "Peak", days_left
    if days_left <= 56:
        return "Build", days_left
    return "Base", days_left

def format_countdown(days_left):
    if days_left < 0:
        return "Race day has passed"
    if days_left == 0:
        return "Race day!"
    weeks, rem = divmod(days_left, 7)
    if weeks > 0 and rem > 0:
        return f"{weeks}w {rem}d to go"
    if weeks > 0:
        return f"{weeks} weeks to go"
    return f"{days_left} days to go"

def build_recommendation(readiness, hrv, acwr, rhr_today, rhr_baseline, sleep_hours, phase, days_left):
    notes = []
    flags_caution = []
    flags_good = []

    phase_context = {
        "Build": "You're in your build phase — a good window to gradually add mileage if recovery allows.",
        "Peak": "You're in peak training — this is when your biggest long runs happen, so treat recovery as part of the work.",
        "Post-Race": "Race complete — shift focus to recovery before starting your next block.",
    }.get(phase)
    if phase_context:
        notes.append(phase_context)

    if readiness and readiness.get("level"):
        lvl = str(readiness["level"]).upper()
        if lvl in ("LOW", "VERY_LOW"):
            flags_caution.append("readiness")
        elif lvl == "HIGH":
            flags_good.append("readiness")
        score_part = f" ({readiness['score']}/100)" if readiness.get("score") is not None else ""
        notes.append(f"Training readiness: {str(readiness['level']).replace('_', ' ').title()}{score_part}.")

    if hrv and hrv.get("status"):
        st = str(hrv["status"]).upper()
        if st in ("UNBALANCED", "LOW", "POOR"):
            flags_caution.append("hrv")
        elif st == "BALANCED":
            flags_good.append("hrv")
        notes.append(f"HRV status: {str(hrv['status']).title()}.")

    if acwr is not None:
        if acwr > 1.5:
            flags_caution.append("load")
            notes.append(f"Training load ratio: {acwr:.2f} — climbing faster than your body's adapted to recently.")
        elif acwr < 0.8:
            notes.append(f"Training load ratio: {acwr:.2f} — below your recent average.")
        else:
            flags_good.append("load")
            notes.append(f"Training load ratio: {acwr:.2f} — a sustainable range.")

    if isinstance(rhr_today, (int, float)) and isinstance(rhr_baseline, (int, float)) and rhr_baseline > 0:
        delta = rhr_today - rhr_baseline
        if delta >= 5:
            flags_caution.append("rhr")
            notes.append(f"Resting HR is {delta:.0f} bpm above your recent baseline — an early fatigue signal.")
        elif delta <= -3:
            flags_good.append("rhr")

    if isinstance(sleep_hours, (int, float)) and sleep_hours < 6:
        flags_caution.append("sleep")
        notes.append(f"Only {sleep_hours}h of sleep last night.")

    if phase == "Taper":
        headline = "Taper mode — hold your paces, cut your volume"
        tone = "neutral"
        notes.insert(0, f"{days_left} days to race day: this is the time to protect freshness over adding more work.")
    elif len(flags_caution) >= 2:
        headline = "Lean toward an easy day or rest"
        tone = "caution"
    elif len(flags_caution) == 1 and not flags_good:
        headline = "Moderate it today — listen to your body"
        tone = "caution"
    elif len(flags_good) >= 2 and not flags_caution:
        headline = "Green light — good day for your scheduled quality work"
        tone = "good"
    else:
        headline = "Steady as planned"
        tone = "neutral"

    if not notes:
        notes.append("Not enough recovery data today for a detailed read — mileage and pace trends are still tracked below.")

    return {"headline": headline, "notes": notes, "tone": tone}

# =====================================================================
# Parsing helpers for newer / less-certain Garmin endpoints — every one
# of these degrades to None (never raises) if the field names don't match
# what the account/library version actually returns.
# =====================================================================
def parse_readiness(raw):
    item = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, dict) else None)
    if not item:
        return None
    score = dig(item, "score")
    level = dig(item, "level")
    if score is None and level is None:
        return None
    return {"score": score, "level": level}

def parse_hrv(raw):
    summary = dig(raw, "hrvSummary") if isinstance(raw, dict) else None
    if not summary and isinstance(raw, dict) and "status" in raw:
        summary = raw
    if not summary:
        return None
    last_night = dig(summary, "lastNightAvg")
    status = dig(summary, "status")
    if last_night is None and status is None:
        return None
    return {"last_night": last_night, "status": status}

def parse_body_battery_now(raw):
    day = raw[-1] if isinstance(raw, list) and raw else (raw if isinstance(raw, dict) else None)
    if not day:
        return None
    values = dig(day, "bodyBatteryValuesArray", default=[]) or []
    if values:
        try:
            return values[-1][1]
        except Exception:
            pass
    return dig(day, "charged")

def parse_score(raw):
    if not raw:
        return None
    return dig(raw, "overallScore") or dig(raw, "score")

def parse_race_predictions(raw):
    if not isinstance(raw, dict):
        return None
    candidates = {
        "5K": ["time5K", "raceTime5K", "predictedTime5K"],
        "10K": ["time10K", "raceTime10K", "predictedTime10K"],
        "Half Marathon": ["timeHalfMarathon", "raceTimeHalfMarathon", "predictedTimeHalfMarathon"],
        "Marathon": ["timeMarathon", "raceTimeMarathon", "predictedTimeMarathon"],
    }
    out = {}
    for label, keys in candidates.items():
        val = None
        for k in keys:
            val = raw.get(k)
            if val:
                break
        out[label] = val
    return out if any(out.values()) else None

def parse_vo2_trend(raw):
    entries = []
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        items = [(dig(e, "calendarDate") or dig(e, "date"), e) for e in raw]
    else:
        items = []
    for key, val in items:
        v = None
        if isinstance(val, list):
            v = dig(val, 0, "generic", "vo2MaxPreciseValue") or dig(val, 0, "generic", "vo2MaxValue")
        elif isinstance(val, dict):
            v = dig(val, "generic", "vo2MaxPreciseValue") or dig(val, "generic", "vo2MaxValue") or dig(val, "vo2MaxPreciseValue")
        try:
            d = datetime.strptime(str(key)[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        entries.append((d, v if isinstance(v, (int, float)) else None))
    entries.sort(key=lambda t: t[0])
    return entries

def summarize_intensity(raw):
    entry = raw[-1] if isinstance(raw, list) and raw else (raw if isinstance(raw, dict) else None)
    if not entry:
        return None
    moderate = dig(entry, "moderateValue") or dig(entry, "moderateIntensityMinutes") or dig(entry, "moderateMinutes")
    vigorous = dig(entry, "vigorousValue") or dig(entry, "vigorousIntensityMinutes") or dig(entry, "vigorousMinutes")
    if moderate is None and vigorous is None:
        return None
    return {"moderate": moderate or 0, "vigorous": vigorous or 0}

# =====================================================================
# Rendering — small design system (shared with the Claude-artifact
# version of this dashboard so both look and read the same way)
# =====================================================================
SEQ_STEPS = 5  # matches the --seq-0..--seq-4 custom properties in the CSS below

def seq_step(frac):
    # Index into the 5-step sequential ramp — theme-aware color is applied
    # via CSS class (see .vol-step-N), not baked in inline, so it can swap
    # under prefers-color-scheme rather than being pinned to one theme.
    return min(int(frac * (SEQ_STEPS - 1) + 0.5), SEQ_STEPS - 1)

BAR_W = 22
GAP = 6
STEP = BAR_W + GAP

def render_weekly_bars(weeks):
    if not weeks:
        return "<p class='empty'>No weekly data yet.</p>"
    max_miles = max((w["miles"] for w in weeks), default=0) or 1
    bars = []
    for w in weeks:
        frac = w["miles"] / max_miles
        h = max(4, round(frac * 100))
        step = seq_step(frac)
        label = w["week"].strftime("%-m/%-d")
        title = f"Week of {w['week'].isoformat()}: {w['miles']:.1f} mi over {w['count']} run(s)"
        bars.append(
            f'<div class="vol-bar" style="--h:{h}px" title="{title}">'
            f'<div class="vol-bar-fill vol-step-{step}"></div><span class="vol-bar-label">{label}</span></div>'
        )
    return f'<div class="vol-chart" style="width:{len(weeks)*STEP}px">{"".join(bars)}</div>'

def render_pace_svg(weeks):
    paced = [(i, w) for i, w in enumerate(weeks) if w["avg_pace"]]
    if len(paced) < 2:
        return "<p class='empty'>Not enough paced runs yet for a trend line.</p>"

    total_w = len(weeks) * STEP
    height = 70
    pad_top, pad_bottom = 14, 16

    paces = [w["avg_pace"] for _, w in paced]
    lo, hi = min(paces), max(paces)
    if hi == lo:
        hi = lo + 30  # avoid a zero-height range

    def x_of(i):
        return i * STEP + STEP / 2

    def y_of(pace):
        # inverted: faster (lower seconds/mile) sits higher on the chart
        frac = (pace - lo) / (hi - lo)
        return pad_top + frac * (height - pad_top - pad_bottom)

    points = [(x_of(i), y_of(w["avg_pace"])) for i, w in paced]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)

    area_path = f"M{points[0][0]:.1f},{height - pad_bottom} "
    area_path += " ".join(f"L{x:.1f},{y:.1f}" for x, y in points)
    area_path += f" L{points[-1][0]:.1f},{height - pad_bottom} Z"

    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" class="pace-dot"></circle>'
        for x, y in points
    )

    last_x, last_y = points[-1]
    last_label = fmt_pace(paced[-1][1]["avg_pace"])
    label_y = min(max(last_y - 8, 10), height - 4)
    label = (
        f'<text x="{last_x - 8:.1f}" y="{label_y:.1f}" class="pace-label" '
        f'text-anchor="end">{last_label}/mi</text>'
    )

    return (
        f'<svg width="{total_w}" height="{height}" class="pace-svg">'
        f'<path d="{area_path}" class="pace-area"></path>'
        f'<polyline points="{poly}" class="pace-line"></polyline>'
        f"{dots}{label}"
        f"</svg>"
    )

def render_ladder(laps):
    if not laps:
        return "", ""
    total_dur = sum((l.get("duration") or 0) for l in laps) or 1
    segs = []
    rows = []
    for i, l in enumerate(laps, start=1):
        dur = l.get("duration") or 0
        # Widths are each lap's share of total time, so segments sum to a
        # full 100%-wide bar rather than each being sized off the longest
        # lap (which would overflow the container and get clipped).
        w = max(1.5, dur / total_dur * 100)
        itype = (l.get("intensityType") or "ACTIVE").upper()
        cls = {"ACTIVE": "seg-active", "RECOVERY": "seg-recovery", "REST": "seg-recovery"}.get(itype, "seg-neutral")
        title = f"Lap {i}: {itype.title()}"
        segs.append(f'<div class="seg {cls}" style="--w:{w}%" title="{title}"></div>')

        dist_mi = m_to_mi(l.get("distance"))
        pace = pace_sec_per_mile(l.get("distance"), l.get("duration"))
        hr = l.get("averageHR")
        tag_cls = itype.lower()
        rows.append(
            f'<div class="lap-row">'
            f'<div class="lap-n">{i}</div>'
            f'<div class="lap-tag tag-{tag_cls}">{itype.title()}</div>'
            f'<div class="lap-dist">{dist_mi:.2f} mi</div>'
            f'<div class="lap-pace">{fmt_pace(pace)}/mi</div>'
            f'<div class="lap-hr">{int(hr) if hr else "—"} bpm</div>'
            f"</div>"
        )
    ladder_html = f'<div class="ladder">{"".join(segs)}</div>'
    rows_html = "".join(rows)
    return ladder_html, rows_html

def render_log(runs_desc):
    rows = []
    cur_month = None
    for r in runs_desc:
        month_label = r["date"].strftime("%B %Y")
        if month_label != cur_month:
            rows.append(f'<div class="log-month">{month_label}</div>')
            cur_month = month_label
        pace = pace_sec_per_mile(r["distance"], r["duration"])
        elev_ft = m_to_ft(r["elev_gain"]) if r["elev_gain"] is not None else None
        rows.append(
            f'<div class="log-row">'
            f'<div class="log-date">{r["date"].strftime("%b %-d")}</div>'
            f'<div class="log-name">{r["name"]}</div>'
            f'<div class="log-stats">'
            f'<span class="log-stat"><b>{m_to_mi(r["distance"]):.2f}</b> mi</span>'
            f'<span class="log-stat"><b>{fmt_pace(pace)}</b>/mi</span>'
            f'<span class="log-stat"><b>{int(r["avg_hr"]) if r["avg_hr"] else "—"}</b> bpm</span>'
            f'<span class="log-stat"><b>{round(elev_ft) if elev_ft is not None else "—"}</b> ft</span>'
            f"</span></div>"
        )
    return "".join(rows)

def render_sparkline(values, cls_prefix="rhr"):
    # values: list of (date, metric) oldest -> newest, metric may be None
    pts = [(i, v) for i, (_, v) in enumerate(values) if v]
    if len(pts) < 2:
        return ""
    w, h = 90, 28
    step = w / max(1, len(values) - 1)
    lo = min(v for _, v in pts)
    hi = max(v for _, v in pts)
    if hi == lo:
        hi = lo + 1

    def y_of(v):
        return h - 4 - (v - lo) / (hi - lo) * (h - 8)

    poly = " ".join(f"{i*step:.1f},{y_of(v):.1f}" for i, v in pts)
    last_i, last_v = pts[-1]
    dot = f'<circle cx="{last_i*step:.1f}" cy="{y_of(last_v):.1f}" r="2.2" class="{cls_prefix}-dot"></circle>'
    return f'<svg width="{w}" height="{h}" class="{cls_prefix}-spark"><polyline points="{poly}" class="{cls_prefix}-line"></polyline>{dot}</svg>'

# =====================================================================
# Main
# =====================================================================
def main():
    email = os.environ["GARMIN_EMAIL"]
    password = os.environ["GARMIN_PASSWORD"]

    client = Garmin(email, password)
    client.login()

    today = datetime.now().date()
    start_history = today - timedelta(days=HISTORY_DAYS)

    # ---- Runs ----
    raw_activities = safe_method_call(
        client, "get_activities_by_date", start_history.isoformat(), today.isoformat(), "running"
    )
    if raw_activities is None:
        raw_activities = safe_call(client.get_activities, 0, 200) or []
        raw_activities = [
            a for a in raw_activities
            if "running" in (dig(a, "activityType", "typeKey", default="") or "")
        ]

    runs = [r for r in (parse_run(a) for a in (raw_activities or [])) if r and r["date"] >= start_history]
    runs_asc = sorted(runs, key=lambda r: r["date"])
    runs_desc = sorted(runs, key=lambda r: r["date"], reverse=True)

    weeks = build_weekly(runs_asc)
    insights = build_insights(weeks, runs_asc, today=today)
    bars_html = render_weekly_bars(weeks)
    pace_svg = render_pace_svg(weeks)
    log_html = render_log(runs_desc)

    # ---- Most recent run + splits ----
    ladder_html, laps_rows = "", ""
    latest_block = "<p class='empty'>No recent run found.</p>"
    if runs_desc:
        latest = runs_desc[0]
        splits = safe_call(client.get_activity_splits, latest["id"])
        laps = dig(splits, "lapDTOs", default=[]) or []
        ladder_html, laps_rows = render_ladder(laps)
        pace = pace_sec_per_mile(latest["distance"], latest["duration"])
        latest_block = f"""
        <div class="run-summary">
          <div class="name">{latest['name']}</div>
          <div class="meta">{latest['date'].strftime('%B %-d, %Y')}</div>
        </div>
        {ladder_html}
        <div class="run-summary" style="margin-top:-4px;margin-bottom:10px;">
          <div class="meta">{m_to_mi(latest['distance']):.2f} mi · {fmt_duration(latest['duration'])} ·
          avg {fmt_pace(pace)}/mi · {int(latest['avg_hr']) if latest['avg_hr'] else '—'} bpm</div>
        </div>
        {laps_rows}
        """

    # ---- Today's health snapshot ----
    sleep_data = safe_call(client.get_sleep_data, str(today))
    sleep_seconds = dig(sleep_data, "dailySleepDTO", "sleepTimeSeconds")
    sleep_hours = round(sleep_seconds / 3600, 1) if isinstance(sleep_seconds, (int, float)) else "—"

    rhr_data = safe_call(client.get_rhr_day, str(today))
    resting_hr = dig(rhr_data, "allMetrics", "metricsMap", "WELLNESS_RESTING_HEART_RATE", 0, "value", default="—")

    stress_data = safe_call(client.get_all_day_stress, str(today))
    avg_stress = dig(stress_data, "avgStressLevel", default="—")

    status = safe_method_call(client, "get_training_status", str(today))
    vo2max = dig(status, "vo2_max_precise") or dig(status, "vo2_max")
    training_feedback = dig(status, "training_status_feedback")
    if vo2max is None:
        max_metrics = safe_call(client.get_max_metrics, str(today))
        vo2max = dig(max_metrics, 0, "generic", "vo2MaxPreciseValue", default="—") if isinstance(max_metrics, list) else "—"
    if training_feedback:
        training_feedback = str(training_feedback).replace("_", " ").title()

    # ---- 7-day resting HR sparkline + a slightly longer recovery baseline ----
    rhr_series = []
    for i in range(RHR_TREND_DAYS - 1, -1, -1):
        d = today - timedelta(days=i)
        day_rhr = safe_call(client.get_rhr_day, str(d))
        v = dig(day_rhr, "allMetrics", "metricsMap", "WELLNESS_RESTING_HEART_RATE", 0, "value")
        rhr_series.append((d, v if isinstance(v, (int, float)) else None))
    rhr_spark = render_sparkline(rhr_series, "rhr")
    rhr_baseline_pool = [v for d, v in rhr_series[:-1] if v is not None]
    rhr_baseline = sum(rhr_baseline_pool) / len(rhr_baseline_pool) if rhr_baseline_pool else None
    rhr_today_val = rhr_series[-1][1] if rhr_series else None

    # ---- VO2 max trend (best-effort; falls back to a handful of sampled dates) ----
    vo2_trend_raw = safe_method_call(client, "get_max_metrics_range", (today - timedelta(days=VO2_TREND_DAYS)).isoformat(), today.isoformat())
    vo2_series = parse_vo2_trend(vo2_trend_raw)
    if not vo2_series:
        vo2_series = []
        for d_ago in (VO2_TREND_DAYS, 56, 28, 14, 0):
            d = today - timedelta(days=d_ago)
            m = safe_call(client.get_max_metrics, str(d))
            v = dig(m, 0, "generic", "vo2MaxPreciseValue") if isinstance(m, list) else None
            vo2_series.append((d, v if isinstance(v, (int, float)) else None))
    vo2_spark = render_sparkline(vo2_series, "vo2")

    # ---- Recovery: training readiness, HRV, body battery (best-effort) ----
    readiness = parse_readiness(safe_method_call(client, "get_training_readiness", str(today)))
    hrv = parse_hrv(safe_method_call(client, "get_hrv_data", str(today)))
    body_battery_now = parse_body_battery_now(safe_method_call(client, "get_body_battery", today.isoformat(), today.isoformat()))

    # ---- Fitness trend: race predictions + endurance/hill scores (best-effort) ----
    race_pred = parse_race_predictions(safe_method_call(client, "get_race_predictions"))
    score_window_start = (today - timedelta(days=27)).isoformat()
    endurance_score = parse_score(safe_method_call(client, "get_endurance_score", score_window_start, today.isoformat()))
    hill_score = parse_score(safe_method_call(client, "get_hill_score", score_window_start, today.isoformat()))

    # ---- Weekly intensity minutes (folded into Insights as one extra line) ----
    intensity = summarize_intensity(safe_method_call(
        client, "get_weekly_intensity_minutes", (today - timedelta(days=6)).isoformat(), today.isoformat()
    ))
    if intensity:
        insights.append(
            f"This week's intensity minutes: {int(intensity['moderate'])} moderate + {int(intensity['vigorous'])} vigorous "
            f"(general guideline: 150 moderate or 75 vigorous per week)."
        )

    # ---- Race countdown + recommendation ----
    phase, days_left = race_phase(today, RACE_DATE)
    acwr = compute_acwr(runs_asc, today)
    recommendation = build_recommendation(
        readiness, hrv, acwr, rhr_today_val, rhr_baseline, sleep_hours, phase, days_left
    )
    countdown_str = format_countdown(days_left)
    race_date_fmt = RACE_DATE.strftime("%B %-d, %Y")
    rec_notes_html = "".join(f"<li>{n}</li>" for n in recommendation["notes"])

    insights_html = "".join(f"<li>{s}</li>" for s in insights)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    # ---- Fitness trend section markup ----
    def predict_row(label, seconds, highlight=False):
        cls = "predict-row highlight" if highlight else "predict-row"
        return f'<div class="{cls}"><span class="predict-label">{label}</span><span class="predict-time">{fmt_duration(seconds)}</span></div>'

    if race_pred:
        predict_html = (
            predict_row("5K", race_pred.get("5K"))
            + predict_row("10K", race_pred.get("10K"))
            + predict_row("Half Marathon", race_pred.get("Half Marathon"), highlight=True)
            + predict_row("Marathon", race_pred.get("Marathon"))
        )
    else:
        predict_html = "<p class='empty'>Race predictions aren't available from Garmin right now.</p>"

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Training Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Sans+Condensed:wght@600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {{
  --paper: #EFF1EC; --paper-raised: #FBFCFA; --ink: #1B2420; --ink-muted: #5B655F;
  --line: rgba(27,36,32,0.12); --accent: #B0501F;
  --good: #15876F; --warning: #B8862A; --critical: #B23B33;
  --seq-0: #DCEAE1; --seq-1: #B8D8C6; --seq-2: #8CC0A6; --seq-3: #54A17E; --seq-4: #1F8955;
  --font-display: 'IBM Plex Sans Condensed', 'Arial Narrow', sans-serif;
  --font-body: 'IBM Plex Sans', -apple-system, sans-serif;
  --font-mono: 'IBM Plex Mono', ui-monospace, 'SF Mono', monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --paper: #141B18; --paper-raised: #1C2420; --ink: #E7ECE7; --ink-muted: #93A199;
    --line: rgba(231,236,231,0.14); --accent: #D9814F;
    --good: #29A37A; --warning: #C99A2C; --critical: #D3554C;
    --seq-0: #1E2B25; --seq-1: #2A4536; --seq-2: #3B6B4F; --seq-3: #4F9670; --seq-4: #6FC79B;
  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--paper); color:var(--ink); font-family:var(--font-body); padding:0 0 48px; -webkit-font-smoothing:antialiased; }}
.wrap {{ max-width:680px; margin:0 auto; padding:20px 16px 0; }}
header.top {{ display:flex; justify-content:space-between; align-items:baseline; padding:4px 2px 18px; border-bottom:1px solid var(--line); margin-bottom:22px; }}
h1 {{ font-family:var(--font-display); font-weight:700; font-size:1.5rem; margin:0; text-wrap:balance; }}
.updated {{ font-family:var(--font-mono); font-size:0.7rem; color:var(--ink-muted); text-align:right; }}
h2 {{ font-family:var(--font-display); font-weight:600; font-size:0.78rem; text-transform:uppercase; letter-spacing:0.09em; color:var(--ink-muted); margin:0 0 12px; }}
section {{ margin-bottom:34px; }}
.empty {{ color:var(--ink-muted); font-size:0.85rem; }}

.race-card {{ background:var(--paper-raised); border:1px solid var(--line); border-left:4px solid var(--accent); border-radius:10px; padding:16px 16px 14px; }}
.race-card.tone-good {{ border-left-color:var(--good); }}
.race-card.tone-caution {{ border-left-color:var(--warning); }}
.race-card.tone-neutral {{ border-left-color:var(--accent); }}
.race-head {{ display:flex; justify-content:space-between; align-items:baseline; margin-bottom:10px; flex-wrap:wrap; gap:6px; }}
.race-title {{ font-family:var(--font-display); font-weight:600; font-size:0.95rem; }}
.race-phase {{ display:inline-block; font-size:0.62rem; text-transform:uppercase; letter-spacing:0.05em; padding:2px 7px; border-radius:10px; background:var(--line); color:var(--ink-muted); margin-left:8px; }}
.race-days {{ font-family:var(--font-mono); font-size:0.8rem; color:var(--ink-muted); }}
.rec-headline {{ font-size:1rem; font-weight:600; margin-bottom:8px; }}
.tone-good .rec-headline {{ color:var(--good); }}
.tone-caution .rec-headline {{ color:var(--warning); }}
.tone-neutral .rec-headline {{ color:var(--ink); }}
.rec-notes {{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:6px; }}
.rec-notes li {{ font-size:0.85rem; color:var(--ink-muted); line-height:1.4; padding-left:14px; position:relative; }}
.rec-notes li::before {{ content:""; position:absolute; left:0; top:0.5em; width:5px; height:5px; border-radius:50%; background:var(--accent); }}
.tone-good .rec-notes li::before {{ background:var(--good); }}
.tone-caution .rec-notes li::before {{ background:var(--warning); }}
.rec-disclaimer {{ font-size:0.68rem; color:var(--ink-muted); margin-top:10px; font-style:italic; }}

.stat-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:10px; }}
.stat-tile {{ background:var(--paper-raised); border:1px solid var(--line); border-radius:10px; padding:14px 14px 12px; }}
.stat-value {{ font-family:var(--font-mono); font-size:1.7rem; font-weight:500; font-variant-numeric:tabular-nums; line-height:1.1; }}
.stat-label {{ font-size:0.72rem; color:var(--ink-muted); margin-top:4px; text-transform:uppercase; letter-spacing:0.06em; }}
.stat-sub {{ font-size:0.72rem; color:var(--ink-muted); margin-top:2px; }}
.stat-tile.rhr-tile {{ display:flex; flex-direction:column; }}
.stat-tile.rhr-tile .rhr-row {{ display:flex; align-items:flex-end; justify-content:space-between; gap:8px; }}
.rhr-line {{ fill:none; stroke:var(--good); stroke-width:1.6; }}
.rhr-dot {{ fill:var(--good); }}
.vo2-line {{ fill:none; stroke:var(--accent); stroke-width:1.6; }}
.vo2-dot {{ fill:var(--accent); }}

.pill {{ display:inline-flex; align-items:center; gap:5px; font-size:0.68rem; font-weight:600; text-transform:uppercase; letter-spacing:0.05em; padding:3px 8px; border-radius:20px; margin-top:6px; color:var(--good); background:color-mix(in srgb, var(--good) 14%, transparent); }}
.pill::before {{ content:""; width:6px; height:6px; border-radius:50%; background:currentColor; }}

.insights {{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:9px; }}
.insights li {{ position:relative; padding-left:16px; font-size:0.88rem; line-height:1.45; }}
.insights li::before {{ content:""; position:absolute; left:0; top:0.55em; width:6px; height:6px; border-radius:50%; background:var(--accent); }}

.vol-scroll {{ overflow-x:auto; padding-bottom:6px; }}
.chart-stack {{ display:flex; flex-direction:column; gap:2px; width:max-content; }}
.vol-chart {{ display:flex; align-items:flex-end; gap:6px; height:110px; padding:0 2px; }}
.vol-bar {{ width:{BAR_W}px; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; height:100%; flex-shrink:0; }}
.vol-bar-fill {{ width:100%; height:var(--h); border-radius:3px 3px 1px 1px; }}
.vol-step-0 {{ background:var(--seq-0); }}
.vol-step-1 {{ background:var(--seq-1); }}
.vol-step-2 {{ background:var(--seq-2); }}
.vol-step-3 {{ background:var(--seq-3); }}
.vol-step-4 {{ background:var(--seq-4); }}
.vol-bar-label {{ font-family:var(--font-mono); font-size:0.56rem; color:var(--ink-muted); margin-top:5px; }}
.pace-svg {{ display:block; }}
.pace-line {{ fill:none; stroke:var(--accent); stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }}
.pace-area {{ fill:color-mix(in srgb, var(--accent) 10%, transparent); stroke:none; }}
.pace-dot {{ fill:var(--paper-raised); stroke:var(--accent); stroke-width:1.6; }}
.pace-label {{ font-family:var(--font-mono); font-size:9px; fill:var(--ink-muted); }}
.chart-caption {{ font-family:var(--font-mono); font-size:0.64rem; color:var(--ink-muted); margin-top:6px; }}

.predict-list {{ display:flex; flex-direction:column; gap:2px; margin-bottom:16px; }}
.predict-row {{ display:flex; justify-content:space-between; align-items:center; padding:8px 10px; border-bottom:1px solid var(--line); font-family:var(--font-mono); font-size:0.85rem; }}
.predict-row.highlight {{ background:color-mix(in srgb, var(--accent) 8%, transparent); border-radius:6px; font-weight:600; border-bottom-color:transparent; }}
.predict-label {{ color:var(--ink-muted); font-family:var(--font-body); text-transform:uppercase; font-size:0.7rem; letter-spacing:0.05em; }}
.predict-row.highlight .predict-label {{ color:var(--accent); }}
.predict-time {{ font-variant-numeric:tabular-nums; }}
.score-row {{ display:flex; gap:22px; }}
.score-item b {{ font-family:var(--font-mono); font-size:1.2rem; font-variant-numeric:tabular-nums; }}
.score-item span {{ display:block; font-family:var(--font-body); font-size:0.65rem; color:var(--ink-muted); text-transform:uppercase; letter-spacing:0.05em; margin-top:2px; }}

.ladder {{ display:flex; height:30px; border-radius:6px; overflow:hidden; gap:2px; margin-bottom:14px; }}
.seg {{ flex:0 0 var(--w); }}
.seg-active {{ background:var(--accent); }}
.seg-recovery {{ background:var(--ink-muted); opacity:0.35; }}
.seg-neutral {{ background:var(--ink-muted); opacity:0.18; }}

.lap-row {{ display:grid; grid-template-columns:20px 76px 1fr 1fr 1fr; align-items:center; gap:8px; padding:7px 2px; border-bottom:1px solid var(--line); font-family:var(--font-mono); font-size:0.8rem; }}
.lap-n {{ color:var(--ink-muted); }}
.lap-tag {{ font-family:var(--font-body); font-size:0.62rem; text-transform:uppercase; letter-spacing:0.04em; padding:2px 6px; border-radius:4px; text-align:center; background:var(--line); color:var(--ink-muted); }}
.tag-active {{ background:color-mix(in srgb, var(--accent) 18%, transparent); color:var(--accent); }}
.lap-dist, .lap-pace, .lap-hr {{ text-align:right; font-variant-numeric:tabular-nums; }}

.run-summary {{ display:flex; justify-content:space-between; align-items:baseline; margin-bottom:10px; }}
.run-summary .name {{ font-family:var(--font-display); font-weight:600; font-size:1rem; }}
.run-summary .meta {{ font-family:var(--font-mono); font-size:0.78rem; color:var(--ink-muted); }}

.log-month {{ font-family:var(--font-display); font-size:0.72rem; text-transform:uppercase; letter-spacing:0.08em; color:var(--ink-muted); margin:18px 0 6px; padding-top:4px; }}
.log-month:first-child {{ margin-top:0; }}
.log-row {{ padding:9px 0; border-bottom:1px solid var(--line); }}
.log-date {{ font-family:var(--font-mono); font-size:0.68rem; color:var(--ink-muted); text-transform:uppercase; }}
.log-name {{ font-size:0.92rem; margin:2px 0 5px; }}
.log-stats {{ display:flex; gap:14px; font-family:var(--font-mono); font-size:0.76rem; color:var(--ink-muted); flex-wrap:wrap; }}
.log-stats b {{ color:var(--ink); font-weight:500; }}

footer {{ text-align:center; font-size:0.68rem; color:var(--ink-muted); margin-top:30px; font-family:var(--font-mono); }}
</style>
</head>
<body>
<div class="wrap">
<header class="top">
  <h1>Training Dashboard</h1>
  <div class="updated">Updated<br>{now_str}</div>
</header>

<section>
  <div class="race-card tone-{recommendation['tone']}">
    <div class="race-head">
      <div class="race-title">{RACE_NAME} · {race_date_fmt}<span class="race-phase">{phase}</span></div>
      <div class="race-days">{countdown_str}</div>
    </div>
    <div class="rec-headline">{recommendation['headline']}</div>
    <ul class="rec-notes">{rec_notes_html}</ul>
    <div class="rec-disclaimer">Generated from your Garmin metrics — not a substitute for how you actually feel or a coach's judgment.</div>
  </div>
</section>

<section>
  <h2>Today's Snapshot</h2>
  <div class="stat-grid">
    <div class="stat-tile">
      <div class="stat-value">{sleep_hours}<span style="font-size:0.9rem;color:var(--ink-muted)">h</span></div>
      <div class="stat-label">Sleep</div>
    </div>
    <div class="stat-tile rhr-tile">
      <div class="rhr-row">
        <div>
          <div class="stat-value">{resting_hr}</div>
          <div class="stat-label">Resting HR</div>
        </div>
        {rhr_spark}
      </div>
      <div class="stat-sub">last {RHR_TREND_DAYS} days</div>
    </div>
    <div class="stat-tile">
      <div class="stat-value">{avg_stress}</div>
      <div class="stat-label">Avg Stress</div>
    </div>
    <div class="stat-tile rhr-tile">
      <div class="rhr-row">
        <div>
          <div class="stat-value">{vo2max}</div>
          <div class="stat-label">VO2 Max</div>
          {f'<span class="pill">{training_feedback}</span>' if training_feedback else ''}
        </div>
        {vo2_spark}
      </div>
    </div>
  </div>
</section>

<section>
  <h2>Recovery</h2>
  <div class="stat-grid">
    <div class="stat-tile">
      <div class="stat-value">{readiness['score'] if readiness and readiness.get('score') is not None else '—'}</div>
      <div class="stat-label">Training Readiness</div>
      <div class="stat-sub">{str(readiness['level']).replace('_',' ').title() if readiness and readiness.get('level') else ''}</div>
    </div>
    <div class="stat-tile">
      <div class="stat-value">{hrv['last_night'] if hrv and hrv.get('last_night') is not None else '—'}</div>
      <div class="stat-label">HRV (ms)</div>
      <div class="stat-sub">{str(hrv['status']).title() if hrv and hrv.get('status') else ''}</div>
    </div>
    <div class="stat-tile">
      <div class="stat-value">{body_battery_now if body_battery_now is not None else '—'}</div>
      <div class="stat-label">Body Battery</div>
    </div>
  </div>
</section>

<section>
  <h2>Insights</h2>
  <ul class="insights">{insights_html}</ul>
</section>

<section>
  <h2>Training Load · last {len(weeks)} weeks</h2>
  <div class="vol-scroll">
    <div class="chart-stack">
      {bars_html}
      {pace_svg}
    </div>
  </div>
  <div class="chart-caption">Bars: weekly mileage · Line: weekly average pace (higher = faster)</div>
</section>

<section>
  <h2>Fitness Trend</h2>
  <div class="predict-list">{predict_html}</div>
  <div class="score-row">
    <div class="score-item"><b>{endurance_score if endurance_score is not None else '—'}</b><span>Endurance Score</span></div>
    <div class="score-item"><b>{hill_score if hill_score is not None else '—'}</b><span>Hill Score</span></div>
  </div>
</section>

<section>
  <h2>Most Recent Session</h2>
  {latest_block}
</section>

<section>
  <h2>Run Log · last {HISTORY_DAYS} days ({len(runs)} runs)</h2>
  {log_html if log_html else "<p class='empty'>No runs in this window yet.</p>"}
</section>

<footer>Garmin Connect → GitHub Actions → GitHub Pages · runs daily</footer>
</div>
</body>
</html>
"""

    with open("index.html", "w") as f:
        f.write(html)
    print("Dashboard generated successfully.")


if __name__ == "__main__":
    main()
