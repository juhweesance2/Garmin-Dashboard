import json
import math
import os
from datetime import datetime, timedelta, date
from garminconnect import Garmin

# =====================================================================
# Config
# =====================================================================
HISTORY_DAYS = 180
LONG_RUN_COUNT = 6          # how many recent long runs to pull mile splits for
DETAIL_RUN_COUNT = 30       # how many recent runs get full mile-splits for the click-to-expand modal
ROUTE_RUN_COUNT = 20        # of those, how many also get a GPS route fetch (heavier call)
HRV_SAMPLE_DAYS = 35        # HRV trend window
HRV_SAMPLE_STEP = 3         # sample every N days (keeps API calls reasonable)
VO2_TREND_DAYS = 120

CARTO_API_KEY = os.environ.get("CARTO_API_KEY", "")  # optional, see setup guide's v10 update — the route
                                                        # map uses CARTO Voyager (Google Maps–style) tiles
                                                        # when this is set, and falls back to plain
                                                        # OpenStreetMap tiles (the old look) when it's empty.
                                                        # This key ends up in the page's own client-side JS
                                                        # (the map is rendered in the viewer's browser, so it
                                                        # has to be), not just a server-side secret — fine for
                                                        # CARTO's free tier (no account or billing tied to it,
                                                        # 5M requests/month), but worth knowing if your repo
                                                        # is public.

RACE_DATE = date(2026, 11, 8)
RACE_NAME = "Monterey Bay Half Marathon"
GOAL_TIME_SEC = 2 * 3600 + 14 * 60        # sub-2:14 PR goal
GOAL_PACE_MIN_MI = 10 + 5 / 60             # 10:05/mi — the "(goal pace)" workouts converge on this
PRIOR_PR_SEC = 2 * 3600 + 18 * 60 + 46     # 2:18:46 — prior PR, for reference only

MI_PER_M = 1 / 1609.344
FT_PER_M = 3.28084

# =====================================================================
# Training plan — encoded from the 3-day/week Monterey Bay Half plan
# (intervals Mon / tempo Wed / long run Sat; strength+cross-training on the
# other days aren't tracked here since Garmin only gets pulled for "running"
# activities — see the setup guide for why that's a deliberate scope cut).
#
# targetMi per running day is a close ESTIMATE built from the plan's written
# workout structure (warmup + main set + between-rep recovery jog, converted
# to miles and rounded to the nearest 0.25mi) — not a value Garmin or the plan
# states directly as a single number, except for the long run, which the plan
# always gives as a plain mileage figure. Treat targetMi as a target band, not
# a number your actual GPS distance needs to match exactly — real runs never
# match a prescribed workout down to the hundredth of a mile anyway.
# =====================================================================
PLAN_START = date(2026, 8, 10)  # Monday of Week 1

TRAINING_PLAN = [
    {"phase": "Build Tolerance", "longRunTargetMi": 6.0, "weeklyTargetMi": 14.25, "sessions": {
        "mon": {"type": "Intervals", "label": "5×400m @ 8:40/mi", "targetMi": 4.25},
        "wed": {"type": "Tempo", "label": "2mi @ 10:50/mi", "targetMi": 4.0},
        "sat": {"type": "Long Run", "label": "6mi easy", "targetMi": 6.0}}},
    {"phase": "Build Tolerance", "longRunTargetMi": 6.5, "weeklyTargetMi": 11.0, "sessions": {
        "mon": {"type": "Rest", "label": "Optional 2-3mi shakeout (travel)", "targetMi": 0},
        "wed": {"type": "Tempo", "label": "2.5mi @ 10:40/mi", "targetMi": 4.5},
        "sat": {"type": "Long Run", "label": "6.5mi easy", "targetMi": 6.5}}},
    {"phase": "Build Tolerance", "longRunTargetMi": 9.0, "weeklyTargetMi": 19.5, "sessions": {
        "mon": {"type": "Intervals", "label": "5×800m @ 9:04/mi", "targetMi": 5.5},
        "wed": {"type": "Tempo", "label": "3mi @ 10:35/mi", "targetMi": 5.0},
        "sat": {"type": "Long Run", "label": "9mi, last 1mi @ 10:45/mi", "targetMi": 9.0}}},
    {"phase": "Build Tolerance", "longRunTargetMi": 10.0, "weeklyTargetMi": 21.0, "sessions": {
        "mon": {"type": "Intervals", "label": "5×800m @ 8:54/mi", "targetMi": 5.5},
        "wed": {"type": "Tempo", "label": "3.5mi @ 10:30/mi", "targetMi": 5.5},
        "sat": {"type": "Long Run", "label": "10mi, last 2mi @ 10:45/mi", "targetMi": 10.0}}},
    {"phase": "Recovery", "longRunTargetMi": 7.5, "weeklyTargetMi": 14.5, "sessions": {
        "mon": {"type": "Intervals", "label": "4×400m @ 8:40/mi (reduced)", "targetMi": 3.0},
        "wed": {"type": "Tempo", "label": "2mi @ 10:40/mi (reduced)", "targetMi": 4.0},
        "sat": {"type": "Long Run", "label": "7.5mi easy, down week", "targetMi": 7.5}}},
    {"phase": "Sharpen", "longRunTargetMi": 11.0, "weeklyTargetMi": 22.5, "sessions": {
        "mon": {"type": "Intervals", "label": "4×1000m @ 8:48/mi", "targetMi": 5.5},
        "wed": {"type": "Tempo", "label": "4mi @ 10:20/mi", "targetMi": 6.0},
        "sat": {"type": "Long Run", "label": "11mi, last 2mi @ 10:35/mi", "targetMi": 11.0}}},
    {"phase": "Sharpen", "longRunTargetMi": 12.0, "weeklyTargetMi": 25.5, "sessions": {
        "mon": {"type": "Intervals", "label": "4×1mi @ 9:40/mi", "targetMi": 7.0},
        "wed": {"type": "Tempo", "label": "4.5mi @ 10:10/mi", "targetMi": 6.5},
        "sat": {"type": "Long Run", "label": "12mi, last 3mi @ 10:25/mi", "targetMi": 12.0}}},
    {"phase": "Sharpen", "longRunTargetMi": 13.0, "weeklyTargetMi": 27.5, "sessions": {
        "mon": {"type": "Intervals", "label": "3×1.5mi @ 9:45/mi", "targetMi": 7.5},
        "wed": {"type": "Tempo", "label": "5mi @ 10:05/mi", "targetMi": 7.0},
        "sat": {"type": "Long Run", "label": "13mi, last 3mi @ 10:20/mi", "targetMi": 13.0}}},
    {"phase": "Peak", "longRunTargetMi": 14.0, "weeklyTargetMi": 27.75, "sessions": {
        "mon": {"type": "Intervals", "label": "2×2mi @ 10:00/mi (goal pace)", "targetMi": 6.75},
        "wed": {"type": "Tempo", "label": "5mi @ 10:05/mi (goal pace)", "targetMi": 7.0},
        "sat": {"type": "Long Run", "label": "14mi, last 4mi @ goal pace — key checkpoint", "targetMi": 14.0}}},
    {"phase": "Taper", "longRunTargetMi": 10.0, "weeklyTargetMi": 21.75, "sessions": {
        "mon": {"type": "Intervals", "label": "3×1mi @ 9:45/mi", "targetMi": 5.75},
        "wed": {"type": "Tempo", "label": "4mi @ 10:10/mi", "targetMi": 6.0},
        "sat": {"type": "Long Run", "label": "10mi, last 2mi @ goal pace", "targetMi": 10.0}}},
    {"phase": "Taper", "longRunTargetMi": 7.0, "weeklyTargetMi": 15.0, "sessions": {
        "mon": {"type": "Intervals", "label": "4×400m @ 8:00/mi fast (sharpeners)", "targetMi": 3.0},
        "wed": {"type": "Tempo", "label": "3mi @ 10:10/mi (short)", "targetMi": 5.0},
        "sat": {"type": "Long Run", "label": "7mi easy (reduced)", "targetMi": 7.0}}},
    {"phase": "Taper", "longRunTargetMi": 5.0, "weeklyTargetMi": 11.75, "sessions": {
        "mon": {"type": "Easy", "label": "4×300m strides (sharpeners)", "targetMi": 2.75},
        "wed": {"type": "Tempo", "label": "2mi @ 10:15/mi (short)", "targetMi": 4.0},
        "sat": {"type": "Easy", "label": "5mi easy — shakeout", "targetMi": 5.0}}},
    {"phase": "Race Week", "longRunTargetMi": 1.5, "weeklyTargetMi": 6.75, "sessions": {
        "mon": {"type": "Easy", "label": "3mi easy", "targetMi": 3.0},
        "wed": {"type": "Easy", "label": "2mi easy + strides", "targetMi": 2.25},
        "sat": {"type": "Easy", "label": "1-2mi shakeout + strides", "targetMi": 1.5}}},
]

# =====================================================================
# Unit helpers
# =====================================================================
def m_to_mi(m):
    return (m or 0) * MI_PER_M

def m_to_ft(m):
    return (m or 0) * FT_PER_M

def pace_min_per_mile(distance_m, duration_s):
    mi = m_to_mi(distance_m)
    if not mi or mi <= 0 or not duration_s:
        return None
    return (duration_s / mi) / 60

def fmt_pace_mmss(min_per_mi):
    if min_per_mi is None:
        return "—"
    total_sec = round(min_per_mi * 60)
    m, s = divmod(total_sec, 60)
    return f"{m}:{s:02d}"

def safe_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None

def safe_method_call(obj, method_name, *args, **kwargs):
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
# Parsing + classification
# =====================================================================
def parse_run(a):
    try:
        dt = datetime.strptime(a["startTimeLocal"][:10], "%Y-%m-%d").date()
    except Exception:
        return None
    distance_m = a.get("distance") or 0
    duration_s = a.get("duration") or 0
    return {
        "id": a.get("activityId"),
        "name": a.get("activityName") or "Run",
        "date": dt,
        "dateLabel": dt.strftime("%b %-d"),
        "distance_m": distance_m,
        "duration_s": duration_s,
        "distMi": round(m_to_mi(distance_m), 2),
        "durMin": round(duration_s / 60, 1),
        "paceMinMi": pace_min_per_mile(distance_m, duration_s),
        "avgHr": a.get("averageHR"),
        "maxHr": a.get("maxHR"),
        "elevGainFt": round(m_to_ft(a.get("elevationGain"))) if a.get("elevationGain") is not None else None,
        "avgCadence": a.get("averageRunningCadenceInStepsPerMinute"),
        "maxCadence": a.get("maxRunningCadenceInStepsPerMinute"),
        "location": a.get("locationName"),
        "type": None,
    }

def week_start(d):
    return d - timedelta(days=d.weekday())

def classify_types(runs_asc):
    # Name-based hints first (workout titles from Garmin/the watch usually carry these).
    for r in runs_asc:
        n = r["name"].lower()
        if "stride" in n:
            r["type"] = "Strides"
        elif "tempo" in n:
            r["type"] = "Tempo"
        elif "interval" in n or "speed" in n:
            r["type"] = "Speed"
        elif "benchmark" in n or "time trial" in n:
            r["type"] = "Benchmark"
    # The single longest untyped run each week, if it's a meaningful distance,
    # is treated as that week's Long Run.
    by_week = {}
    for r in runs_asc:
        by_week.setdefault(week_start(r["date"]), []).append(r)
    for wk, rs in by_week.items():
        untyped = [r for r in rs if r["type"] is None]
        if not untyped:
            continue
        longest = max(untyped, key=lambda r: r["distMi"])
        if longest["distMi"] >= 4:
            longest["type"] = "Long Run"
    for r in runs_asc:
        if r["type"] is None:
            r["type"] = "Easy Run"
    return runs_asc

# =====================================================================
# Weekly aggregation
# =====================================================================
def build_weekly(runs_asc):
    buckets = {}
    for r in runs_asc:
        wk = week_start(r["date"])
        b = buckets.setdefault(wk, {"miles": 0.0, "runs": 0, "longRunMiles": 0.0})
        b["miles"] += r["distMi"]
        b["runs"] += 1
        if r["type"] == "Long Run":
            b["longRunMiles"] = max(b["longRunMiles"], r["distMi"])
    weeks = []
    for wk in sorted(buckets):
        b = buckets[wk]
        weeks.append({
            "weekStart": wk.isoformat(),
            "label": wk.strftime("%b %-d"),
            "miles": round(b["miles"], 1),
            "runs": b["runs"],
            "longRunMiles": round(b["longRunMiles"], 1),
        })
    return weeks

def compute_acwr(runs_asc, today):
    # Acute:Chronic Workload Ratio, computed straight from logged mileage — no
    # dependency on any Garmin load-balance endpoint, so this number is exact.
    def miles_in(days_back):
        lo = today - timedelta(days=days_back - 1)
        return sum(r["distMi"] for r in runs_asc if lo <= r["date"] <= today)
    acute = miles_in(7)
    chronic_weekly_avg = miles_in(28) / 4
    if chronic_weekly_avg <= 0:
        return None
    return round(acute / chronic_weekly_avg, 2)

# =====================================================================
# Effort-zone / load-mix split (self-computed 80/20-style breakdown —
# doesn't depend on Garmin's training-load-balance endpoint, which isn't
# available in the installed library)
# =====================================================================
def effort_zone(run, easy_baseline):
    if run["type"] in ("Speed", "Strides", "Benchmark"):
        return "hard"
    if run["type"] == "Tempo":
        return "moderate"
    pace = run["paceMinMi"]
    if pace is None or not easy_baseline:
        return "easy"
    ratio = pace / easy_baseline
    if ratio <= 0.80:
        return "hard"
    if ratio <= 0.92:
        return "moderate"
    return "easy"

def build_load_mix(runs_asc, today, window_days=28):
    window = [r for r in runs_asc if (today - r["date"]).days < window_days]
    if not window:
        return None
    easy_paces = [r["paceMinMi"] for r in runs_asc if r["type"] in ("Easy Run", "Long Run") and r["paceMinMi"]]
    easy_baseline = sorted(easy_paces)[len(easy_paces) // 2] if easy_paces else None
    mins = {"easy": 0.0, "moderate": 0.0, "hard": 0.0}
    for r in window:
        mins[effort_zone(r, easy_baseline)] += r["durMin"]
    total = sum(mins.values()) or 1
    return {
        "easyMin": round(mins["easy"]), "moderateMin": round(mins["moderate"]), "hardMin": round(mins["hard"]),
        "easyPct": round(mins["easy"] / total * 100), "moderatePct": round(mins["moderate"] / total * 100),
        "hardPct": round(mins["hard"] / total * 100),
    }

# =====================================================================
# Plan vs. actual — compares TRAINING_PLAN (above) against what Garmin
# actually recorded, week by week. Scoped to running days only (Mon/Wed/Sat
# in this plan) since that's all the script fetches from Garmin.
# =====================================================================
def build_plan_comparison(runs_asc, today):
    out = []
    for i, wk in enumerate(TRAINING_PLAN):
        week_start = PLAN_START + timedelta(weeks=i)
        week_end = week_start + timedelta(days=6)
        is_future = week_start > today
        week_runs = [r for r in runs_asc if week_start <= r["date"] <= week_end]
        actual_mi = round(sum(r["distMi"] for r in week_runs), 1) if (week_runs or not is_future) else None
        actual_long = round(max((r["distMi"] for r in week_runs), default=0.0), 1) if (week_runs or not is_future) else None

        sessions_out = {}
        for day_key, offset in (("mon", 0), ("wed", 2), ("sat", 5)):
            planned = wk["sessions"].get(day_key)
            if not planned:
                continue
            target_date = week_start + timedelta(days=offset)
            match = None
            if not is_future:
                # nearest actual run within a day of the planned date — real
                # schedules slip by a day without it meaning the session was skipped
                candidates = [r for r in runs_asc if abs((r["date"] - target_date).days) <= 1]
                if candidates:
                    match = min(candidates, key=lambda r: abs((r["date"] - target_date).days))
            sessions_out[day_key] = {
                "type": planned["type"], "label": planned["label"], "targetMi": planned["targetMi"],
                "date": target_date.isoformat(),
                "actualMi": round(match["distMi"], 2) if match else None,
                "actualPace": match["paceMinMi"] if match else None,
                "matched": bool(match),
            }

        weekly_target = wk["weeklyTargetMi"]
        adherence_pct = round(actual_mi / weekly_target * 100) if (actual_mi is not None and weekly_target) else None
        if is_future:
            status = "upcoming"
        elif adherence_pct is None:
            status = "no-data"
        elif adherence_pct >= 85:
            status = "on-track"
        elif adherence_pct >= 60:
            status = "behind"
        else:
            status = "well-behind"

        out.append({
            "weekStart": week_start.isoformat(), "weekLabel": week_start.strftime("%b %-d"),
            "phase": wk["phase"], "plannedMi": weekly_target, "actualMi": actual_mi,
            "adherencePct": adherence_pct, "plannedLongRun": wk["longRunTargetMi"], "actualLongRun": actual_long,
            "status": status, "sessions": sessions_out,
        })
    return out

# =====================================================================
# Aerobic efficiency trend — speed-per-heartbeat on easy-effort runs (Easy Run
# + Long Run types only, so quality days don't distort it). Rising over time
# means you're covering more ground per heartbeat at the same easy effort —
# a genuine aerobic-fitness signal computed entirely from your own logged
# data, independent of Garmin's VO2 max estimate or a guessed field name.
# =====================================================================
def build_efficiency_trend(runs_asc):
    out = []
    for r in runs_asc:
        if r["type"] not in ("Easy Run", "Long Run"):
            continue
        if not r["paceMinMi"] or not r["avgHr"] or r["distMi"] < 1.5:
            continue
        mph = 60 / r["paceMinMi"]
        ef = round(mph / r["avgHr"] * 1000, 2)  # arbitrary but consistent scale — only the trend matters
        out.append({"date": r["date"].isoformat(), "ef": ef, "distMi": r["distMi"], "avgHr": r["avgHr"]})
    out.sort(key=lambda p: p["date"])
    return out

# =====================================================================
# Race countdown + daily recommendation
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

def build_recommendation(readiness, hrv_today_status, acwr, rhr_today, rhr_baseline, sleep_hours, phase, days_left):
    notes = []
    flags_caution, flags_good = [], []

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

    if hrv_today_status:
        st = str(hrv_today_status).upper()
        if st in ("UNBALANCED", "LOW", "POOR"):
            flags_caution.append("hrv")
        elif st == "BALANCED":
            flags_good.append("hrv")
        notes.append(f"HRV status: {str(hrv_today_status).title()}.")

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
# Best-effort parsers for newer Garmin endpoints — every one degrades to
# None rather than raising if a field name doesn't match this account's data.
# =====================================================================
def parse_readiness(raw):
    item = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, dict) else None)
    if not item:
        return None
    score, level = dig(item, "score"), dig(item, "level")
    if score is None and level is None:
        return None
    out = {"score": score, "level": level}
    rec = dig(item, "recoveryTimeFactorPercent") or dig(item, "recoveryTime")
    load = dig(item, "acwrFactorPercent") or dig(item, "loadFactorPercent")
    if rec is not None:
        out["recoveryPercent"] = rec
    if load is not None:
        out["loadFactorPercent"] = load
    return out

def parse_hrv_point(raw, d):
    summary = dig(raw, "hrvSummary") if isinstance(raw, dict) else None
    if not summary and isinstance(raw, dict) and "status" in raw:
        summary = raw
    if not summary:
        return None
    val = dig(summary, "lastNightAvg") or dig(summary, "weeklyAvg")
    status = dig(summary, "status")
    if val is None:
        return None
    return {"date": d.isoformat(), "hrv": val, "status": status or "—"}

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
        if isinstance(v, (int, float)):
            entries.append({"date": d.isoformat(), "vo2": v})
    entries.sort(key=lambda t: t["date"])
    return entries

def parse_route(details):
    # GPS polyline shape isn't confirmed against a real typed schema (unlike the
    # activity-summary fields), so this tries a few shapes seen in the wild and
    # degrades to None — no route, not a crash — if none match this account's data.
    if not isinstance(details, dict):
        return None
    candidates = None
    for path in (
        ("geoPolylineDTO", "polyline"),
        ("polyline",),
        ("activityDetailMetrics",),
    ):
        val = dig(details, *path)
        if isinstance(val, list) and val:
            candidates = val
            break
    if not candidates:
        return None
    pts = []
    for p in candidates:
        lat = lon = None
        if isinstance(p, dict):
            lat = p.get("lat") or p.get("latitude")
            lon = p.get("lon") or p.get("lng") or p.get("longitude")
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            lat, lon = p[0], p[1]
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and (lat or lon):
            pts.append([round(lat, 6), round(lon, 6)])
    if len(pts) < 2:
        return None
    if len(pts) > 150:
        step = len(pts) / 150
        pts = [pts[int(i * step)] for i in range(150)]
    return pts

def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000  # meters
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lmb = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lmb / 2) ** 2
    return 2 * r * math.asin(min(1, math.sqrt(a)))

ELEV_BIN_MI = 0.25  # quarter-mile — the fixed resolution of the elevation trace

def _finalize_elevation_profile(pts, max_points):
    # Bins the raw altitude trace into fixed-width quarter-mile buckets, rather
    # than just thinning out every Nth raw sample. Binning by DISTANCE (instead
    # of by index) means each point in the resulting profile means the same
    # thing on every run — "the average altitude across the Nth quarter mile" —
    # regardless of how densely or unevenly Garmin happened to sample GPS/
    # altitude that day, and averaging the raw points that fall in each bin
    # smooths a bit of altimeter/GPS jitter along the way as a side benefit.
    pts = [(d, e) for d, e in pts if isinstance(d, (int, float)) and isinstance(e, (int, float))]
    if len(pts) < 6:
        return None
    pts.sort(key=lambda t: t[0])
    total_m = pts[-1][0]
    if total_m <= 0:
        return None
    total_mi = total_m * MI_PER_M
    n_bins = max(1, math.ceil(total_mi / ELEV_BIN_MI))
    n_bins = min(n_bins, max_points)  # defensive cap, not a normal-use downsample
    bucket_elevs = [[] for _ in range(n_bins)]
    for d, e in pts:
        idx = min(n_bins - 1, int((d * MI_PER_M) / ELEV_BIN_MI))
        bucket_elevs[idx].append(e)
    out = []
    for i, elevs in enumerate(bucket_elevs):
        if not elevs:
            continue  # sparse data left this quarter-mile with no raw samples — skip rather than fabricate one
        bin_center_mi = min((i + 0.5) * ELEV_BIN_MI, total_mi)
        out.append({"distMi": round(bin_center_mi, 3), "elevFt": round(sum(elevs) / len(elevs) * FT_PER_M, 1)})
    if len(out) < 4:
        return None
    return out

def parse_elevation_profile(details, max_points=150):
    # A continuous altitude trace (sampled every few seconds, not once per mile) —
    # this is what turns the Long Run Splits elevation panel from one point per
    # mile into an actual rolling-terrain shape. Distinct from parse_route (lat/lon
    # only, for the map) and from build_splits' per-lap elevationGain (one number
    # per mile). Like the GPS route parser, the exact field shape isn't confirmed
    # against a real account, so this tries a couple of known Garmin layouts and
    # returns None — the chart falls back to the per-mile view — if neither matches.
    if not isinstance(details, dict):
        return None

    # Shape 1: activityDetailMetrics + metricDescriptors — the time-series API
    # Garmin Connect's own activity page graphs against. Each row's "metrics" list
    # is aligned by index to metricDescriptors' declared key order; look for a
    # distance-like key and an elevation/altitude-like key.
    descriptors = dig(details, "metricDescriptors")
    rows = dig(details, "activityDetailMetrics")
    if isinstance(descriptors, list) and isinstance(rows, list) and descriptors and rows:
        dist_idx = elev_idx = None
        for d in descriptors:
            if not isinstance(d, dict):
                continue
            key = str(d.get("key", "")).lower()
            idx = d.get("metricsIndex")
            if idx is None:
                continue
            if dist_idx is None and "distance" in key:
                dist_idx = idx
            if elev_idx is None and ("elevation" in key or "altitude" in key):
                elev_idx = idx
        if dist_idx is not None and elev_idx is not None:
            pts = []
            for row in rows:
                vals = row.get("metrics") if isinstance(row, dict) else None
                if not isinstance(vals, list) or len(vals) <= max(dist_idx, elev_idx):
                    continue
                pts.append((vals[dist_idx], vals[elev_idx]))
            profile = _finalize_elevation_profile(pts, max_points)
            if profile:
                return profile

    # Shape 2: geoPolylineDTO.polyline points carrying their own altitude field —
    # some accounts return elevation alongside each lat/lon rather than (or beside)
    # Shape 1. These points don't come with a distance field, so distance is
    # accumulated from consecutive lat/lon pairs via the haversine formula.
    poly = dig(details, "geoPolylineDTO", "polyline") or dig(details, "polyline")
    if isinstance(poly, list) and poly:
        pts = []
        cum_dist = 0.0
        prev = None
        for p in poly:
            if not isinstance(p, dict):
                prev = None
                continue
            lat = p.get("lat") or p.get("latitude")
            lon = p.get("lon") or p.get("lng") or p.get("longitude")
            alt = p.get("altitude") or p.get("alt") or p.get("elevation")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)) or not isinstance(alt, (int, float)):
                prev = None
                continue
            if prev is not None:
                cum_dist += haversine_m(prev[0], prev[1], lat, lon)
            prev = (lat, lon)
            pts.append((cum_dist, alt))
        profile = _finalize_elevation_profile(pts, max_points)
        if profile:
            return profile

    return None

# =====================================================================
# Coach-voice insight generators — deterministic pattern detectors, not a
# live LLM call, so these run for free inside the GitHub Action every time.
# =====================================================================
def insight_volume_trend(weeks):
    if len(weeks) < 6:
        return None
    early = weeks[:4]
    recent = weeks[-4:]
    early_avg = sum(w["miles"] for w in early) / len(early)
    recent_avg = sum(w["miles"] for w in recent) / len(recent)
    if early_avg <= 0:
        return None
    pct = (recent_avg - early_avg) / early_avg * 100
    if recent_avg > early_avg:
        tone = "good" if pct <= 120 else "watch"
        verb = "a steady, controlled build" if pct <= 80 else "a fast ramp — worth keeping an eye on injury risk"
        return {"type": tone, "icon": "VOLUME",
                "html": f"Weekly mileage has grown from an average of <b>{early_avg:.1f} mi</b> earlier in this window to <b>{recent_avg:.1f} mi</b> over the last month — {verb}."}
    return {"type": "watch", "icon": "VOLUME",
            "html": f"Weekly mileage has dropped from an average of <b>{early_avg:.1f} mi</b> earlier in this window to <b>{recent_avg:.1f} mi</b> recently — worth checking whether that's planned recovery or lost consistency."}

def insight_bonk(long_runs_ordered):
    for run, splits in long_runs_ordered[:3]:
        if len(splits) < 4:
            continue
        mid = len(splits) // 2
        front, back = splits[:mid], splits[mid:]
        front_pace = sum(s["pace"] for s in front) / len(front)
        back_pace = sum(s["pace"] for s in back) / len(back)
        front_hrs = [s["avgHr"] for s in front if s["avgHr"]]
        back_hrs = [s["avgHr"] for s in back if s["avgHr"]]
        if not front_pace or not front_hrs or not back_hrs:
            continue
        front_hr, back_hr = sum(front_hrs) / len(front_hrs), sum(back_hrs) / len(back_hrs)
        pace_fade_pct = (back_pace - front_pace) / front_pace * 100
        hr_drop = front_hr - back_hr
        if pace_fade_pct >= 15 and hr_drop >= 4:
            return {"type": "flag", "icon": "BONK",
                    "html": (f"The {run['dateLabel']} {run['name']} shows a fade: pace held near "
                             f"{fmt_pace_mmss(front_pace)}/mi through the first half, then slowed to "
                             f"{fmt_pace_mmss(back_pace)}/mi in the second half while heart rate dropped "
                             f"{hr_drop:.0f} bpm — the signature of a glycogen bonk or fueling/heat issue, "
                             f"not a fitness problem. Worth fueling earlier on runs this length.")}
    return None

def insight_terrain(runs_asc, long_run_splits_by_id, today):
    candidates = [r for r in runs_asc if r["distMi"] >= 3 and r["elevGainFt"] is not None and (today - r["date"]).days <= 45]
    if len(candidates) < 4:
        return None
    per_mi = sorted(r["elevGainFt"] / r["distMi"] for r in candidates if r["distMi"] > 0)
    median = per_mi[len(per_mi) // 2] or 20
    paces = sorted(r["paceMinMi"] for r in candidates if r["paceMinMi"])
    typical_pace = paces[len(paces) // 2] if paces else None
    recent_first = sorted(candidates, key=lambda r: r["date"], reverse=True)
    for r in recent_first[:8]:
        rate = r["elevGainFt"] / r["distMi"] if r["distMi"] else 0
        if rate >= median * 2.2 and rate >= 60 and typical_pace and r["paceMinMi"] and r["paceMinMi"] > typical_pace * 1.1:
            extra = ""
            splits = long_run_splits_by_id.get(str(r["id"]))
            if splits:
                steepest = max(splits["splits"], key=lambda s: s.get("elevGainFt") or 0)
                if steepest.get("elevGainFt"):
                    unit = "Mile" if splits.get("mileBased", True) else "Lap"
                    extra = f" {unit} {steepest['mile']} alone carried {steepest['elevGainFt']}ft of that gain and slowed to {fmt_pace_mmss(steepest['pace'])}/mi."
            return {"type": "watch", "icon": "TERRAIN",
                    "html": (f"The {r['dateLabel']} {r['name']} run came in noticeably slower than usual: "
                             f"{fmt_pace_mmss(r['paceMinMi'])}/mi against a typical {fmt_pace_mmss(typical_pace)}/mi, "
                             f"with {r['elevGainFt']}ft of gain over {r['distMi']:.1f} mi "
                             f"({rate:.0f} ft/mi vs a {median:.0f} ft/mi baseline).{extra}")}
    return None

def insight_load_mix(load_mix):
    if not load_mix:
        return None
    quality_pct = load_mix["moderatePct"] + load_mix["hardPct"]
    if quality_pct < 8:
        return {"type": "watch", "icon": "LOAD MIX",
                "html": (f"Training over the last 4 weeks has been almost entirely easy effort — "
                          f"{load_mix['easyPct']}% easy vs {quality_pct}% moderate/hard. A well-established guideline "
                          f"(the \"80/20\" split) targets roughly 15–25% moderate-or-harder — some tempo or speed work "
                          f"would round this out.")}
    if quality_pct > 35:
        return {"type": "flag", "icon": "LOAD MIX",
                "html": (f"Quality volume is running high: {quality_pct}% of the last 4 weeks at moderate-or-harder "
                          f"effort, against a typical 15–25% target. That's a lot of hard running relative to your easy "
                          f"base — consider whether some of it should shift to easy.")}
    return {"type": "good", "icon": "LOAD MIX",
            "html": (f"Effort mix over the last 4 weeks — {load_mix['easyPct']}% easy, {load_mix['moderatePct']}% "
                      f"moderate, {load_mix['hardPct']}% hard — sits inside the typical 15–25% quality-volume range.")}

def insight_vo2(vo2_series):
    pts = [p for p in vo2_series if isinstance(p.get("vo2"), (int, float))]
    if len(pts) < 2:
        return None
    first, last = pts[0], pts[-1]
    diff = last["vo2"] - first["vo2"]
    if abs(diff) < 0.5:
        return {"type": "watch", "icon": "VO2 MAX",
                "html": f"VO2 max has held flat at <b>{last['vo2']:.0f} ml/kg/min</b> across this window — normal early in a build, and usually the first metric to move once tempo/speed work lands."}
    tone = "good" if diff > 0 else "watch"
    verb = "up" if diff > 0 else "down"
    return {"type": tone, "icon": "VO2 MAX",
            "html": f"VO2 max is {verb} from {first['vo2']:.0f} to <b>{last['vo2']:.0f} ml/kg/min</b> since {first['date']}."}

def insight_hrv(hrv_series):
    pts = [p for p in hrv_series if isinstance(p.get("hrv"), (int, float))]
    if len(pts) < 3:
        return None
    first, last = pts[0], pts[-1]
    diff = last["hrv"] - first["hrv"]
    unbalanced = sum(1 for p in pts if str(p.get("status", "")).upper() in ("UNBALANCED", "LOW", "POOR"))
    tone = "good" if diff >= 0 else "watch"
    return {"type": tone, "icon": "HRV",
            "html": (f"HRV has moved from {first['hrv']}ms to <b>{last['hrv']}ms</b> over this window"
                      f"{', with ' + str(unbalanced) + ' unbalanced reading(s) along the way' if unbalanced else ''} — "
                      f"{'recovery trending the right direction as training continues' if diff >= 0 else 'worth watching alongside sleep and training load'}.")}

def insight_acwr(acwr):
    if acwr is None:
        return None
    if acwr > 1.3:
        return {"type": "watch", "icon": "ACWR",
                "html": f"Acute:chronic workload ratio is <b>{acwr:.2f}</b> — the last 7 days trained meaningfully harder than your recent average. Values above ~1.3 carry more injury risk; keep an eye on how legs and tendons feel."}
    if acwr < 0.8:
        return {"type": "watch", "icon": "ACWR",
                "html": f"Acute:chronic workload ratio is <b>{acwr:.2f}</b> — the last 7 days trained noticeably lighter than the trailing month. Normal after a cutback or travel week, but a signal to rebuild gradually rather than jump straight back to peak volume."}
    return {"type": "good", "icon": "ACWR",
            "html": f"Acute:chronic workload ratio is <b>{acwr:.2f}</b> — a sustainable range, meaning recent training load matches what your body's adapted to."}

def insight_readiness_flag(readiness, hrv_today, sleep_hours):
    if not readiness or not readiness.get("level"):
        return None
    lvl = str(readiness["level"]).upper()
    if lvl not in ("LOW", "VERY_LOW"):
        return None
    parts = []
    if isinstance(sleep_hours, (int, float)):
        parts.append(f"sleep of {sleep_hours}h")
    if hrv_today and str(hrv_today.get("status", "")).upper() in ("UNBALANCED", "LOW", "POOR"):
        parts.append(f"an unbalanced HRV reading ({hrv_today.get('hrv')}ms)")
    driver = " and ".join(parts) if parts else "today's recovery metrics"
    return {"type": "flag", "icon": "READINESS",
            "html": f"Today's training readiness came in at <b>{readiness.get('score', '—')}/100 ({lvl.title()})</b>, driven largely by {driver}. Worth prioritizing recovery before the next hard session."}

def insight_efficiency(ef_series):
    if len(ef_series) < 8:
        return None
    early = ef_series[:len(ef_series) // 3] or ef_series[:1]
    recent = ef_series[-len(ef_series) // 3:] or ef_series[-1:]
    early_avg = sum(p["ef"] for p in early) / len(early)
    recent_avg = sum(p["ef"] for p in recent) / len(recent)
    if early_avg <= 0:
        return None
    pct = (recent_avg - early_avg) / early_avg * 100
    if abs(pct) < 3:
        return {"type": "watch", "icon": "EFFICIENCY",
                "html": f"Aerobic efficiency on easy/long runs has held roughly flat across this window ({early_avg:.2f} → {recent_avg:.2f}) — normal if you've mostly been holding steady mileage; this is usually one of the first numbers to move once a build phase adds consistent easy volume."}
    if pct > 0:
        return {"type": "good", "icon": "EFFICIENCY",
                "html": f"Aerobic efficiency on easy/long runs is up <b>{pct:.0f}%</b> across this window (speed per heartbeat, {early_avg:.2f} → {recent_avg:.2f}) — you're covering more ground at the same easy effort, a genuine aerobic-fitness gain independent of any single fast workout."}
    return {"type": "watch", "icon": "EFFICIENCY",
            "html": f"Aerobic efficiency on easy/long runs is down <b>{abs(pct):.0f}%</b> across this window (speed per heartbeat, {early_avg:.2f} → {recent_avg:.2f}) — worth a look alongside heat, fatigue, or a recent volume jump before assuming fitness is regressing."}

def insight_plan_adherence(plan_comparison, today):
    completed = [w for w in plan_comparison if w["status"] not in ("upcoming", "no-data")]
    if not completed:
        return None
    last_two = completed[-2:]
    behind = [w for w in last_two if w["status"] in ("behind", "well-behind")]
    if len(behind) == len(last_two) and len(last_two) >= 1:
        worst = min(last_two, key=lambda w: w["adherencePct"] or 0)
        return {"type": "flag", "icon": "PLAN",
                "html": f"The last {len(last_two)} week(s) have run under your plan's target mileage — week of {worst['weekLabel']} hit {worst['adherencePct']}% of its {worst['plannedMi']:.1f}mi target. One light week is normal; two in a row is worth a deliberate call on whether to make it up or adjust the plan rather than letting it drift."}
    last = completed[-1]
    if last["status"] == "on-track":
        return {"type": "good", "icon": "PLAN",
                "html": f"Week of {last['weekLabel']} ({last['phase']}) hit {last['adherencePct']}% of its planned {last['plannedMi']:.1f}mi, with a {last['actualLongRun']:.1f}mi long run against a {last['plannedLongRun']:.1f}mi target — on track with the plan."}
    return None

def build_insights(weeks, long_runs_ordered, runs_asc, long_run_splits_by_id, today, load_mix, vo2_series, hrv_series, acwr, readiness, hrv_today, ef_series, plan_comparison):
    generators = [
        lambda: insight_plan_adherence(plan_comparison, today),
        lambda: insight_volume_trend(weeks),
        lambda: insight_bonk(long_runs_ordered),
        lambda: insight_terrain(runs_asc, long_run_splits_by_id, today),
        lambda: insight_load_mix(load_mix),
        lambda: insight_efficiency(ef_series),
        lambda: insight_vo2(vo2_series),
        lambda: insight_hrv(hrv_series),
        lambda: insight_acwr(acwr),
        lambda: insight_readiness_flag(readiness, hrv_today, None),
    ]
    out = []
    for g in generators:
        try:
            r = g()
        except Exception:
            r = None
        if r:
            out.append(r)
    if not out:
        out.append({"type": "watch", "icon": "DATA",
                     "html": "Not enough run history yet to generate insights — check back after a few more runs."})
    return out

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
        raw_activities = [a for a in raw_activities if "running" in (dig(a, "activityType", "typeKey", default="") or "")]

    runs = [r for r in (parse_run(a) for a in (raw_activities or [])) if r and r["date"] >= start_history]
    runs_asc = sorted(runs, key=lambda r: r["date"])
    runs_desc = sorted(runs, key=lambda r: r["date"], reverse=True)
    classify_types(runs_asc)  # mutates in place; runs_desc holds the same dict objects

    weeks = build_weekly(runs_asc)
    acwr = compute_acwr(runs_asc, today)
    load_mix = build_load_mix(runs_asc, today)

    # ---- Per-run detail (mile splits + best-effort GPS route) for the click-to-
    # expand modal, covering the N most recent runs. Bounded to keep the daily
    # sync's API-call count and page payload reasonable — older runs still show
    # their summary stats when clicked, just not lap-by-lap detail.
    def build_splits(activity_id):
        splits_raw = safe_call(client.get_activity_splits, activity_id)
        laps = dig(splits_raw, "lapDTOs", default=[]) or []
        out = []
        for i, lap in enumerate(laps, start=1):
            lap_dist_m = lap.get("distance")
            lap_dist_mi = m_to_mi(lap_dist_m) if lap_dist_m is not None else None
            out.append({
                "mile": i,
                "distMi": round(lap_dist_mi, 2) if lap_dist_mi is not None else None,
                "pace": pace_min_per_mile(lap_dist_m, lap.get("duration")) or 0,
                "avgHr": lap.get("averageHR"),
                "maxHr": lap.get("maxHR"),
                "elevGainFt": round(m_to_ft(lap.get("elevationGain"))) if lap.get("elevationGain") is not None else 0,
                "cadence": lap.get("averageRunningCadenceInStepsPerMinute"),
            })
        # Garmin only auto-laps at each full mile on runs where mile-autolap was
        # the active lap trigger. A structured workout (interval reps, tempo
        # segments) instead gets one lap per interval/recovery segment — usually
        # well under a mile, and wildly different from each other — and treating
        # those as "Mile 1, Mile 2, ..." mislabels a 400-800m rep as a finished
        # mile and badly distorts the pace/HR scale plotted next to it. Rather
        # than trust the run's Tempo/Speed/Long-Run label (which is itself a
        # name-based guess), this looks at the lap DISTANCES actually returned:
        # if most of them cluster near 1.00mi, it's real per-mile autolaps;
        # otherwise it's a structured workout and gets labeled "Lap N" (with its
        # real distance shown) instead of a misleading "Mile N".
        checkable = [s["distMi"] for s in out[:-1] if s["distMi"] is not None] if len(out) > 1 else []
        if not checkable:
            checkable = [s["distMi"] for s in out if s["distMi"] is not None]
        mile_based = bool(checkable) and (sum(1 for d in checkable if 0.85 <= d <= 1.15) / len(checkable)) >= 0.6

        if mile_based:
            # The trailing lap is usually whatever partial distance was left when
            # the run ended, not a finished mile (see note above) — a few seconds
            # of GPS wobble over 0.02mi can compute as a 24:00/mi "mile" that
            # skews the whole chart's scale. Drop it, down to 1 lap minimum.
            while len(out) > 1 and out[-1]["distMi"] is not None and out[-1]["distMi"] < 0.9:
                out.pop()
        # For a structured workout, every lap (work rep AND recovery jog) is real,
        # correctly-accounted-for distance — nothing here is a "leftover partial
        # mile," so nothing gets dropped; it's just labeled and charted as laps.
        return out, mile_based

    detail_candidates = runs_desc[:DETAIL_RUN_COUNT]
    run_details = {}
    for i, r in enumerate(detail_candidates):
        splits, mile_based = build_splits(r["id"])
        route = None
        elev_profile = None
        if i < ROUTE_RUN_COUNT:
            details_raw = safe_method_call(client, "get_activity_details", r["id"])
            route = parse_route(details_raw)
            elev_profile = parse_elevation_profile(details_raw)
        if splits or route:
            run_details[str(r["id"])] = {"splits": splits, "route": route, "elevProfile": elev_profile, "mileBased": mile_based}

    # ---- Long run splits panel (mile-by-mile, for the N most recent long runs) —
    # reuses the detail fetch above when the long run falls inside that window.
    long_run_candidates = [r for r in runs_desc if r["type"] == "Long Run"][:LONG_RUN_COUNT]
    long_runs_data = {}
    long_runs_ordered = []  # [(run, splits_list)], most recent first — used by insight detectors
    for r in long_run_candidates:
        rid = str(r["id"])
        cached = run_details.get(rid)
        splits, mile_based = (cached["splits"], cached["mileBased"]) if cached else build_splits(r["id"])
        if splits:
            long_runs_data[rid] = {
                "label": f"{r['dateLabel']} — {r['name']} ({r['distMi']:.1f}mi)",
                "splits": splits,
                "mileBased": mile_based,
                "elevProfile": cached.get("elevProfile") if cached else None,
            }
            long_runs_ordered.append((r, splits))

    # ---- Today's health snapshot ----
    sleep_data = safe_call(client.get_sleep_data, str(today))
    sleep_seconds = dig(sleep_data, "dailySleepDTO", "sleepTimeSeconds")
    sleep_hours = round(sleep_seconds / 3600, 1) if isinstance(sleep_seconds, (int, float)) else None

    rhr_data = safe_call(client.get_rhr_day, str(today))
    resting_hr_today = dig(rhr_data, "allMetrics", "metricsMap", "WELLNESS_RESTING_HEART_RATE", 0, "value")

    rhr_baseline_series = []
    for i in range(1, 8):
        d = today - timedelta(days=i)
        day_rhr = safe_call(client.get_rhr_day, str(d))
        v = dig(day_rhr, "allMetrics", "metricsMap", "WELLNESS_RESTING_HEART_RATE", 0, "value")
        if isinstance(v, (int, float)):
            rhr_baseline_series.append(v)
    rhr_baseline = sum(rhr_baseline_series) / len(rhr_baseline_series) if rhr_baseline_series else None

    stress_data = safe_call(client.get_all_day_stress, str(today))
    avg_stress = dig(stress_data, "avgStressLevel")

    status = safe_method_call(client, "get_training_status", str(today))
    vo2max_today = dig(status, "vo2_max_precise") or dig(status, "vo2_max")
    training_feedback = dig(status, "training_status_feedback")
    if vo2max_today is None:
        max_metrics = safe_call(client.get_max_metrics, str(today))
        vo2max_today = dig(max_metrics, 0, "generic", "vo2MaxPreciseValue") if isinstance(max_metrics, list) else None
    if training_feedback:
        training_feedback = str(training_feedback).replace("_", " ").title()

    # ---- VO2 trend (best-effort; sampled fallback if the range endpoint is unavailable) ----
    vo2_trend_raw = safe_method_call(client, "get_max_metrics_range", (today - timedelta(days=VO2_TREND_DAYS)).isoformat(), today.isoformat())
    vo2_series = parse_vo2_trend(vo2_trend_raw)
    if not vo2_series:
        vo2_series = []
        for d_ago in (VO2_TREND_DAYS, 90, 60, 30, 14, 0):
            d = today - timedelta(days=d_ago)
            m = safe_call(client.get_max_metrics, str(d))
            v = dig(m, 0, "generic", "vo2MaxPreciseValue") if isinstance(m, list) else None
            if isinstance(v, (int, float)):
                vo2_series.append({"date": d.isoformat(), "vo2": v})

    # ---- HRV: today + a sampled trend series (best-effort) ----
    hrv_today = parse_hrv_point(safe_method_call(client, "get_hrv_data", str(today)), today)
    hrv_series = []
    for d_ago in range(0, HRV_SAMPLE_DAYS, HRV_SAMPLE_STEP):
        d = today - timedelta(days=d_ago)
        point = parse_hrv_point(safe_method_call(client, "get_hrv_data", str(d)), d)
        if point:
            hrv_series.append(point)
    hrv_series.sort(key=lambda p: p["date"])

    # ---- Recovery: training readiness, body battery (best-effort) ----
    readiness = parse_readiness(safe_method_call(client, "get_training_readiness", str(today)))
    body_battery_now = parse_body_battery_now(safe_method_call(client, "get_body_battery", today.isoformat(), today.isoformat()))

    # ---- Fitness trend: race predictions + endurance/hill score (best-effort) ----
    race_pred = parse_race_predictions(safe_method_call(client, "get_race_predictions"))
    score_window_start = (today - timedelta(days=27)).isoformat()
    endurance_score = parse_score(safe_method_call(client, "get_endurance_score", score_window_start, today.isoformat()))
    hill_score = parse_score(safe_method_call(client, "get_hill_score", score_window_start, today.isoformat()))

    # ---- Race countdown + recommendation ----
    phase, days_left = race_phase(today, RACE_DATE)
    recommendation = build_recommendation(
        readiness, hrv_today.get("status") if hrv_today else None, acwr,
        resting_hr_today, rhr_baseline, sleep_hours, phase, days_left
    )

    # ---- Plan vs. actual + aerobic efficiency trend ----
    plan_comparison = build_plan_comparison(runs_asc, today)
    efficiency_trend = build_efficiency_trend(runs_asc)

    # ---- Coach-voice insights ----
    insights = build_insights(
        weeks, long_runs_ordered, runs_asc, long_runs_data, today,
        load_mix, vo2_series, hrv_series, acwr, readiness, hrv_today,
        efficiency_trend, plan_comparison
    )

    data = {
        "meta": {
            "lastSynced": today.isoformat(),
            "raceDate": RACE_DATE.isoformat(),
            "raceName": RACE_NAME,
            "daysLeft": days_left,
            "weeksLeft": round(days_left / 7, 1),
            "phase": phase,
            "syncRangeStart": start_history.isoformat(),
            "syncRangeEnd": today.isoformat(),
            "detailRunCount": DETAIL_RUN_COUNT,
            "cartoApiKey": CARTO_API_KEY,
        },
        "recommendation": recommendation,
        "runs": [{k: v for k, v in r.items() if k not in ("distance_m", "duration_s")} for r in runs_desc],
        "weekly": weeks,
        "longRuns": long_runs_data,
        "runDetails": run_details,
        "planComparison": plan_comparison,
        "efficiencyTrend": efficiency_trend,
        "vo2max": vo2_series,
        "vo2maxToday": vo2max_today,
        "hrv": hrv_series,
        "hrvToday": hrv_today,
        "trainingReadiness": readiness,
        "trainingStatusFeedback": training_feedback,
        "bodyBattery": body_battery_now,
        "acwr": acwr,
        "loadMix": load_mix,
        "insights": insights,
        "racePredictions": race_pred,
        "enduranceScore": endurance_score,
        "hillScore": hill_score,
        "restingHr": {"today": resting_hr_today, "baseline": round(rhr_baseline, 1) if rhr_baseline else None},
        "sleepHours": sleep_hours,
        "avgStress": avg_stress,
    }
    # runs["date"] holds a python date object — swap for its iso string before dumping
    for r in data["runs"]:
        r["date"] = r["date"].isoformat()

    html = HTML_SHELL.replace("__TITLE__", f"Training Console — {RACE_NAME}") \
                      .replace("__CSS__", CSS) \
                      .replace("__DATA_JSON__", json.dumps(data)) \
                      .replace("__JS__", JS)

    with open("index.html", "w") as f:
        f.write(html)
    print("Dashboard generated successfully.")


HTML_SHELL = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin="">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
<style>__CSS__</style>
</head>
<body>
<div class="console-header">
  <div class="wrap">
    <div class="header-row">
      <div class="brand-block">
        <div>
          <span class="brand-eyebrow">Training Console</span>
          <h1 id="hero-title">Build → Race</h1>
        </div>
      </div>
      <div class="sync-badge"><span class="sync-dot"></span><span id="sync-text">Synced from Garmin —</span></div>
    </div>
    <div class="countdown-strip" id="countdown-strip"></div>
  </div>
</div>
<div class="wrap">
  <div id="boot-errors" class="boot-errors" style="display:none;"></div>
  <div class="stat-strip" id="hero-stats"></div>

  <section>
    <div class="panel rec-panel" id="rec-panel"></div>
  </section>

  <section>
    <div class="section-head">
      <div class="section-title"><span class="section-index">01</span> Weekly Volume &amp; Training Load</div>
      <div class="section-note">Mileage by week against your long run distance and weekly run count.</div>
    </div>
    <div class="panel">
      <div class="chart-box tall"><div id="chart-volume" class="svg-chart"></div></div>
      <div class="legend-row">
        <div class="legend-item"><span class="legend-swatch" style="background:var(--amber)"></span>Weekly miles</div>
        <div class="legend-item"><span class="legend-swatch" style="background:var(--blue)"></span>Long run distance</div>
        <div class="legend-item"><span class="legend-swatch" style="background:var(--teal); border-radius:50%;"></span>Runs per week</div>
      </div>
    </div>
  </section>

  <section>
    <div class="section-head">
      <div class="section-title"><span class="section-index">02</span> Pace Progression</div>
      <div class="section-note">Every run's average pace, colored by workout type, with a 5-run rolling average.</div>
    </div>
    <div class="panel">
      <div class="chart-box tall"><div id="chart-pace" class="svg-chart"></div></div>
      <div class="legend-row" id="pace-legend"></div>
    </div>
  </section>

  <section>
    <div class="section-head">
      <div class="section-title"><span class="section-index">03</span> Plan vs. Actual</div>
      <div class="section-note" id="plan-note">Weekly mileage against your training plan.</div>
    </div>
    <div class="panel">
      <div class="chart-box tall"><div id="chart-plan" class="svg-chart"></div></div>
      <div class="legend-row">
        <div class="legend-item"><span class="legend-swatch" style="background:var(--text-dim)"></span>Planned miles</div>
        <div class="legend-item"><span class="legend-swatch" style="background:var(--amber)"></span>Actual miles</div>
      </div>
    </div>
    <div class="panel" style="margin-top:16px;">
      <div class="table-scroll">
        <table id="plan-table">
          <thead>
            <tr>
              <th>Week</th><th>Phase</th><th>Planned</th><th>Actual</th><th>Adherence</th><th>Long run — plan → actual</th><th>Status</th>
            </tr>
          </thead>
          <tbody id="plan-table-body"></tbody>
        </table>
      </div>
    </div>
  </section>

  <section>
    <div class="section-head">
      <div class="section-title"><span class="section-index">04</span> What The Data Is Saying</div>
      <div class="section-note">Rule-based pattern detection — not a live model call — so it runs free on every sync.</div>
    </div>
    <div id="insights" style="display:flex; flex-direction:column; gap:10px;"></div>
  </section>

  <section>
    <div class="section-head">
      <div class="section-title"><span class="section-index">05</span> Recovery &amp; Readiness</div>
      <div class="section-note">Today's readiness, HRV trend, and how training effort has split across intensity bands.</div>
    </div>
    <div class="panel-triple">
      <div class="panel">
        <div class="stat-label">Training Readiness — Today</div>
        <div class="dial-row" style="margin-top:10px;">
          <div>
            <div class="dial-num" id="readiness-score">—</div>
            <div class="dial-label">out of 100</div>
          </div>
          <div>
            <span class="badge" id="readiness-badge">—</span>
          </div>
        </div>
        <div style="margin-top:18px; padding-top:16px; border-top:1px solid var(--border-soft);">
          <div class="stat-label">Training Status</div>
          <div style="display:flex; align-items:baseline; gap:8px; margin-top:8px; flex-wrap:wrap;">
            <span class="badge good" id="training-status-badge">—</span>
            <span class="dial-label" id="training-acwr"></span>
          </div>
        </div>
      </div>
      <div class="panel">
        <div class="stat-label">HRV Trend</div>
        <div class="chart-box" style="height:190px; margin-top:10px;"><div id="chart-hrv" class="svg-chart"></div></div>
      </div>
      <div class="panel">
        <div class="stat-label">Effort Mix — Last 4 Weeks</div>
        <div class="balance-bars" id="balance-bars"></div>
      </div>
    </div>
  </section>

  <section>
    <div class="section-head">
      <div class="section-title"><span class="section-index">06</span> Fitness Trend</div>
      <div class="section-note">Garmin's race-time predictions and fitness scores from current training data.</div>
    </div>
    <div class="panel-split">
      <div class="panel">
        <div class="predict-list" id="predict-list"></div>
      </div>
      <div class="panel">
        <div class="score-row" id="score-row"></div>
        <div class="chart-box" style="height:150px; margin-top:16px;"><div id="chart-vo2" class="svg-chart"></div></div>
        <div class="chart-caption">VO2 max trend</div>
      </div>
    </div>
    <div class="panel" style="margin-top:16px;">
      <div class="stat-label">Aerobic Efficiency — Easy &amp; Long Runs</div>
      <div class="chart-box" style="height:190px; margin-top:10px;"><div id="chart-efficiency" class="svg-chart"></div></div>
      <div class="chart-caption">Speed per heartbeat, rising = more efficient. A better read on aerobic fitness than pace alone, since it's not thrown off by hot days or hills.</div>
    </div>
  </section>

  <section>
    <div class="section-head">
      <div class="section-title"><span class="section-index">07</span> Long Run Splits</div>
      <div class="section-note">Mile-by-mile pace, heart rate and elevation for each long run this cycle.</div>
    </div>
    <div class="panel" id="splits-panel">
      <div class="tab-row" id="split-tabs"></div>
      <div class="split-meta" id="split-meta"></div>
      <div class="chart-box tall"><div id="chart-splits" class="svg-chart"></div></div>
      <div class="legend-row" id="splits-legend"></div>
    </div>
  </section>

  <section>
    <div class="section-head">
      <div class="section-title"><span class="section-index">08</span> Full Run Log</div>
      <div class="section-note" id="table-note">Click a column to sort · click a row for splits, cadence, HR and route.</div>
    </div>
    <div class="panel">
      <div class="table-controls">
        <select id="filter-type"><option value="">All types</option></select>
        <input type="text" id="filter-search" placeholder="search run name…">
        <span class="dial-label" id="table-count" style="margin-left:auto;"></span>
      </div>
      <div class="table-scroll">
        <table id="run-table">
          <thead>
            <tr>
              <th data-key="date">Date</th>
              <th data-key="name">Run</th>
              <th data-key="type">Type</th>
              <th data-key="distMi">Dist</th>
              <th data-key="durMin">Time</th>
              <th data-key="paceMinMi">Pace</th>
              <th data-key="avgHr">Avg HR</th>
              <th data-key="maxHr">Max HR</th>
              <th data-key="elevGainFt">Elev+</th>
            </tr>
          </thead>
          <tbody id="run-table-body"></tbody>
        </table>
      </div>
    </div>
  </section>

  <footer>
    <div class="update-note"><b>Keeping this current:</b> synced from Garmin through <span id="footer-sync-date"></span> · runs daily via GitHub Actions.</div>
    <div>Source: Garmin Connect</div>
  </footer>
</div>
<div id="run-modal" class="modal-overlay" style="display:none;">
  <div class="modal-panel">
    <button class="modal-close" id="modal-close" aria-label="Close">&times;</button>
    <div id="modal-body"></div>
  </div>
</div>
<div id="chart-zoom-modal" class="modal-overlay chart-zoom-overlay" style="display:none;">
  <div class="chart-zoom-panel">
    <div class="chart-zoom-toolbar">
      <span class="chart-zoom-title" id="chart-zoom-title"></span>
      <div class="chart-zoom-controls">
        <button type="button" id="chart-zoom-out" aria-label="Zoom out">−</button>
        <span id="chart-zoom-level">100%</span>
        <button type="button" id="chart-zoom-in" aria-label="Zoom in">+</button>
        <button type="button" id="chart-zoom-reset" aria-label="Reset zoom">⟲</button>
        <button type="button" id="chart-zoom-close" class="chart-zoom-close-btn" aria-label="Close">&times;</button>
      </div>
    </div>
    <div class="chart-zoom-viewport" id="chart-zoom-viewport">
      <div class="chart-zoom-stage" id="chart-zoom-stage">
        <div class="chart-box" id="chart-zoom-box"><div id="chart-zoom-target" class="svg-chart"></div></div>
      </div>
    </div>
    <div class="chart-zoom-hint">Scroll or pinch to widen the x-axis · drag to pan · double-click to reset</div>
  </div>
</div>
<div id="chart-tooltip"></div>
<script>const DATA = __DATA_JSON__;</script>
<script>__JS__</script>
</body>
</html>
"""

CSS = r"""
:root{
  --bg: #12151a; --bg-panel: #1a1e24; --bg-raised: #20252c; --bg-inset: #0d1013;
  --border: #2a3038; --border-soft: #22262d;
  --text: #e7e9ec; --text-muted: #8b95a1; --text-dim: #5c6570;
  --amber: #e3a857; --amber-dim: #4a3d28;
  --teal: #5fa8a0; --teal-dim: #24393a;
  --clay: #c1614a; --clay-dim: #3c2620;
  --blue: #6690c4; --blue-dim: #232f42;
  --font-display: 'IBM Plex Sans', 'Segoe UI', system-ui, sans-serif;
  --font-body: 'IBM Plex Sans', -apple-system, 'Segoe UI', system-ui, sans-serif;
  --font-mono: 'IBM Plex Mono', 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
}
*{ box-sizing:border-box; margin:0; padding:0; }
body{ background:var(--bg); color:var(--text); font-family:var(--font-body); line-height:1.5; -webkit-font-smoothing:antialiased; padding:0 0 64px; }
::selection{ background:var(--amber); color:#12151a; }
.wrap{ max-width:1180px; margin:0 auto; padding:0 24px; }
.console-header{ border-bottom:1px solid var(--border); background: radial-gradient(ellipse 900px 300px at 15% -20%, rgba(227,168,87,0.10), transparent), var(--bg); padding:28px 0 22px; }
.header-row{ display:flex; justify-content:space-between; align-items:flex-start; gap:24px; flex-wrap:wrap; }
.brand-eyebrow{ font-family:var(--font-mono); font-size:11px; letter-spacing:0.14em; color:var(--amber); text-transform:uppercase; display:block; margin-bottom:6px; }
h1{ font-family:var(--font-display); font-weight:700; font-size:clamp(21px,5.5vw,30px); letter-spacing:-0.01em; text-wrap:balance; }
.sync-badge{ font-family:var(--font-mono); font-size:12px; color:var(--text-muted); display:flex; align-items:center; gap:8px; padding:8px 12px; border:1px solid var(--border); border-radius:6px; background:var(--bg-panel); white-space:nowrap; }
.sync-dot{ width:7px; height:7px; border-radius:50%; background:var(--teal); box-shadow:0 0 8px var(--teal); flex-shrink:0; }
.countdown-strip{ margin-top:22px; display:flex; border:1px solid var(--border); border-radius:10px; overflow:hidden; background:var(--bg-panel); flex-wrap:wrap; }
.countdown-cell{ flex:1; padding:16px 20px; border-right:1px solid var(--border-soft); display:flex; flex-direction:column; gap:4px; min-width:130px; }
.countdown-cell:last-child{ border-right:none; }
.cc-label{ font-size:11px; text-transform:uppercase; letter-spacing:0.08em; color:var(--text-dim); font-family:var(--font-mono); }
.cc-value{ font-family:var(--font-mono); font-size:clamp(17px,4.5vw,24px); font-weight:600; color:var(--text); }
.cc-value.accent{ color:var(--amber); }
.cc-sub{ font-size:12px; color:var(--text-muted); }
.boot-errors{ margin-top:16px; padding:12px 16px; border:1px solid var(--clay); background:var(--clay-dim); border-radius:8px; font-family:var(--font-mono); font-size:12px; color:var(--clay); }
.stat-strip{ display:grid; grid-template-columns:repeat(5,1fr); gap:1px; background:var(--border); border:1px solid var(--border); border-radius:10px; overflow:hidden; margin-top:28px; }
.stat-cell{ background:var(--bg-panel); padding:18px 18px 16px; }
.stat-label{ font-size:11px; text-transform:uppercase; letter-spacing:0.07em; color:var(--text-dim); font-family:var(--font-mono); margin-bottom:8px; }
.stat-value{ font-family:var(--font-mono); font-size:clamp(19px,4.4vw,26px); font-weight:600; font-variant-numeric:tabular-nums; }
.stat-unit{ font-size:13px; color:var(--text-muted); font-weight:400; margin-left:3px; }
.stat-delta{ font-size:12px; margin-top:5px; color:var(--text-muted); }
.stat-delta.up{ color:var(--teal); }
.stat-delta.warn{ color:var(--clay); }
section{ margin-top:44px; }
.section-head{ display:flex; justify-content:space-between; align-items:baseline; margin-bottom:16px; gap:16px; flex-wrap:wrap; }
.section-title{ font-family:var(--font-display); font-weight:600; font-size:clamp(16px,3.6vw,19px); display:flex; align-items:center; gap:10px; }
.section-index{ font-family:var(--font-mono); color:var(--amber); font-size:13px; }
.section-note{ font-size:13px; color:var(--text-muted); max-width:440px; text-align:right; }
.panel{ background:var(--bg-panel); border:1px solid var(--border); border-radius:12px; padding:22px; }
.panel-split{ display:grid; grid-template-columns:1.4fr 1fr; gap:16px; }
.panel-triple{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }
@media (max-width:860px){ .panel-split, .panel-triple{ grid-template-columns:1fr; } .stat-strip{ grid-template-columns:repeat(2,1fr); } }
@media (max-width:640px){
  .wrap{ padding:0 14px; }
  .console-header{ padding:20px 0 16px; }
  .panel{ padding:15px; }
  .section-note{ text-align:left; max-width:none; }
  .stat-cell{ padding:14px 14px 12px; }
  .countdown-cell{ padding:12px 14px; min-width:100px; }
  .chart-box{ height:220px; }
  .chart-box.tall{ height:260px; }
  .modal-panel{ padding:18px; }
  .route-map{ height:220px; }
  .modal-splits-table th, .modal-splits-table td{ padding:7px 6px; font-size:11.5px; }
}
.chart-box{ position:relative; height:260px; }
.chart-box.tall{ height:320px; }
.svg-chart{ width:100%; height:100%; }
.svg-chart svg{ width:100%; height:100%; display:block; overflow:visible; }
.svg-chart text{ font-family:var(--font-mono); fill:var(--text-dim); font-size:10px; }
.svg-chart .axis-line{ stroke:var(--border); stroke-width:1; }
.svg-chart .grid-line{ stroke:var(--border-soft); stroke-width:1; }
.svg-chart .data-point{ cursor:pointer; }
.svg-chart .data-point:hover{ filter:brightness(1.3); }
#chart-tooltip{ position:fixed; pointer-events:none; z-index:999; background:var(--bg-raised); border:1px solid var(--border); border-radius:6px; padding:8px 11px; font-family:var(--font-mono); font-size:12px; color:var(--text); box-shadow:0 8px 20px rgba(0,0,0,0.4); display:none; max-width:220px; line-height:1.5; }
#chart-tooltip .tt-title{ font-family:var(--font-body); font-weight:600; color:var(--text); margin-bottom:3px; font-size:12.5px; }
#chart-tooltip .tt-row{ color:var(--text-muted); }
#chart-tooltip .tt-row b{ color:var(--text); font-weight:500; }
.legend-row{ display:flex; gap:18px; flex-wrap:wrap; margin-top:14px; font-size:12px; color:var(--text-muted); }
.legend-item{ display:flex; align-items:center; gap:6px; }
.legend-swatch{ width:10px; height:10px; border-radius:2px; }
.chart-caption{ font-family:var(--font-mono); font-size:0.64rem; color:var(--text-dim); margin-top:6px; text-align:center; }
.insight-card{ background:var(--bg-raised); border:1px solid var(--border-soft); border-radius:10px; padding:16px 18px; display:flex; gap:12px; align-items:flex-start; }
.insight-icon{ font-family:var(--font-mono); font-size:11px; padding:3px 7px; border-radius:4px; flex-shrink:0; margin-top:2px; white-space:nowrap; }
.insight-icon.good{ background:var(--teal-dim); color:var(--teal); }
.insight-icon.watch{ background:var(--amber-dim); color:var(--amber); }
.insight-icon.flag{ background:var(--clay-dim); color:var(--clay); }
.insight-text{ font-size:13.5px; color:var(--text); line-height:1.55; }
.insight-text b{ color:var(--text); font-weight:600; }
.dial-row{ display:flex; gap:22px; align-items:center; }
.dial-num{ font-family:var(--font-mono); font-size:clamp(24px,6vw,34px); font-weight:600; }
.dial-label{ font-size:12px; color:var(--text-muted); margin-top:2px; }
.badge{ display:inline-block; font-family:var(--font-mono); font-size:11px; padding:3px 8px; border-radius:20px; text-transform:uppercase; letter-spacing:0.05em; }
.badge.high, .badge.good{ background:var(--teal-dim); color:var(--teal); }
.badge.moderate{ background:var(--amber-dim); color:var(--amber); }
.badge.low, .badge.low-warn{ background:var(--clay-dim); color:var(--clay); }
.badge.upcoming, .badge.no-data{ background:var(--bg-inset); color:var(--text-dim); }
.plan-week-cell{ font-family:var(--font-mono); font-size:12.5px; }
.plan-week-cell .phase-lbl{ display:block; font-size:10.5px; color:var(--text-dim); margin-top:1px; }
.balance-bars{ display:flex; flex-direction:column; gap:14px; margin-top:6px; }
.balance-row{ display:grid; grid-template-columns:74px 1fr 44px; gap:10px; align-items:center; }
.balance-name{ font-size:12px; color:var(--text-muted); }
.balance-track{ height:8px; background:var(--bg-inset); border-radius:4px; position:relative; overflow:visible; }
.balance-target{ position:absolute; top:-3px; bottom:-3px; border-left:1px dashed var(--text-dim); border-right:1px dashed var(--text-dim); }
.balance-fill{ height:100%; border-radius:4px; }
.balance-val{ font-family:var(--font-mono); font-size:12px; text-align:right; color:var(--text-muted); }
.rec-panel{ border-left:4px solid var(--amber); }
.rec-panel.tone-good{ border-left-color:var(--teal); }
.rec-panel.tone-caution{ border-left-color:var(--clay); }
.rec-head{ display:flex; justify-content:space-between; align-items:baseline; margin-bottom:10px; flex-wrap:wrap; gap:8px; }
.rec-eyebrow{ font-family:var(--font-mono); font-size:11px; letter-spacing:0.1em; text-transform:uppercase; color:var(--text-dim); }
.rec-headline{ font-family:var(--font-display); font-size:clamp(16px,3.8vw,19px); font-weight:600; margin-bottom:10px; text-wrap:balance; }
.tone-good .rec-headline{ color:var(--teal); }
.tone-caution .rec-headline{ color:var(--clay); }
.rec-notes{ list-style:none; display:flex; flex-direction:column; gap:6px; }
.rec-notes li{ font-size:13.5px; color:var(--text-muted); line-height:1.5; padding-left:14px; position:relative; }
.rec-notes li::before{ content:""; position:absolute; left:0; top:0.55em; width:5px; height:5px; border-radius:50%; background:var(--amber); }
.tone-good .rec-notes li::before{ background:var(--teal); }
.tone-caution .rec-notes li::before{ background:var(--clay); }
.rec-disclaimer{ font-size:11px; color:var(--text-dim); margin-top:12px; font-style:italic; }
.predict-list{ display:flex; flex-direction:column; gap:2px; }
.predict-row{ display:flex; justify-content:space-between; align-items:center; padding:10px 12px; border-bottom:1px solid var(--border-soft); font-family:var(--font-mono); font-size:14px; }
.predict-row.highlight{ background:var(--amber-dim); border-radius:6px; font-weight:600; border-bottom-color:transparent; }
.predict-label{ color:var(--text-muted); font-family:var(--font-body); text-transform:uppercase; font-size:11px; letter-spacing:0.05em; }
.predict-row.highlight .predict-label{ color:var(--amber); }
.score-row{ display:flex; gap:26px; }
.score-item b{ font-family:var(--font-mono); font-size:1.3rem; font-variant-numeric:tabular-nums; }
.score-item span{ display:block; font-family:var(--font-body); font-size:0.65rem; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.05em; margin-top:2px; }
.tab-row{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:18px; }
.tab-btn{ font-family:var(--font-mono); font-size:12px; padding:8px 12px; border-radius:6px; border:1px solid var(--border); background:var(--bg-raised); color:var(--text-muted); cursor:pointer; transition:all .15s ease; }
.tab-btn:hover{ color:var(--text); border-color:var(--text-dim); }
.tab-btn.active{ background:var(--amber-dim); color:var(--amber); border-color:var(--amber); }
.split-meta{ display:flex; gap:26px; margin-bottom:16px; flex-wrap:wrap; }
.split-meta-item .val{ font-family:var(--font-mono); font-size:18px; }
.table-controls{ display:flex; gap:10px; margin-bottom:14px; flex-wrap:wrap; align-items:center; }
select, input[type=text]{ font-family:var(--font-mono); font-size:12px; background:var(--bg-raised); color:var(--text); border:1px solid var(--border); border-radius:6px; padding:8px 10px; outline:none; }
select:focus, input:focus{ border-color:var(--amber); }
table{ width:100%; border-collapse:collapse; font-size:13px; }
thead th{ text-align:left; font-family:var(--font-mono); font-size:11px; text-transform:uppercase; letter-spacing:0.05em; color:var(--text-dim); font-weight:500; padding:10px 12px; border-bottom:1px solid var(--border); cursor:pointer; user-select:none; white-space:nowrap; }
thead th:hover{ color:var(--text-muted); }
thead th.sorted{ color:var(--amber); }
tbody td{ padding:10px 12px; border-bottom:1px solid var(--border-soft); font-family:var(--font-mono); white-space:nowrap; }
tbody td.name-cell{ font-family:var(--font-body); white-space:normal; }
tbody tr:hover{ background:var(--bg-raised); }
.type-pill{ font-family:var(--font-body); font-size:11px; padding:2px 8px; border-radius:20px; display:inline-block; }
.type-pill.Long-Run{ background:var(--blue-dim); color:var(--blue); }
.type-pill.Easy-Run{ background:var(--bg-inset); color:var(--text-muted); }
.type-pill.Tempo{ background:var(--amber-dim); color:var(--amber); }
.type-pill.Speed{ background:var(--clay-dim); color:var(--clay); }
.type-pill.Benchmark{ background:var(--teal-dim); color:var(--teal); }
.type-pill.Strides{ background:var(--bg-inset); color:var(--text-dim); }
.table-scroll{ overflow-x:auto; }
tbody tr.run-row{ cursor:pointer; }
footer{ margin-top:56px; padding-top:22px; border-top:1px solid var(--border); display:flex; justify-content:space-between; gap:20px; flex-wrap:wrap; font-size:12.5px; color:var(--text-dim); }
footer .update-note{ max-width:560px; }
footer .update-note b{ color:var(--text-muted); }
.empty{ color:var(--text-dim); font-size:0.85rem; }

.modal-overlay{ position:fixed; inset:0; background:rgba(13,16,19,0.72); backdrop-filter:blur(2px); z-index:1000; display:flex; align-items:flex-start; justify-content:center; padding:40px 16px; overflow-y:auto; }
.modal-panel{ background:var(--bg-panel); border:1px solid var(--border); border-radius:14px; max-width:760px; width:100%; padding:24px; position:relative; margin-bottom:40px; }
.modal-close{ position:absolute; top:14px; right:14px; background:var(--bg-raised); border:1px solid var(--border); color:var(--text-muted); width:32px; height:32px; border-radius:8px; font-size:18px; cursor:pointer; line-height:1; }
.modal-close:hover{ color:var(--text); border-color:var(--text-dim); }
.modal-title{ font-family:var(--font-display); font-weight:700; font-size:clamp(18px,3.8vw,22px); margin-bottom:4px; padding-right:40px; text-wrap:balance; }
.modal-sub{ font-family:var(--font-mono); font-size:12px; color:var(--text-muted); margin-bottom:18px; }
.modal-stats{ display:grid; grid-template-columns:repeat(auto-fit,minmax(88px,1fr)); gap:1px; background:var(--border); border:1px solid var(--border); border-radius:10px; overflow:hidden; margin-bottom:22px; }
.modal-stat{ background:var(--bg-raised); padding:12px 14px; }
.modal-stat .stat-label{ margin-bottom:6px; }
.modal-stat .stat-value{ font-size:clamp(15px,3.6vw,18px); }
.modal-section-title{ font-family:var(--font-mono); font-size:11px; text-transform:uppercase; letter-spacing:0.08em; color:var(--text-dim); margin:22px 0 10px; }
.route-map{ height:280px; border-radius:10px; overflow:hidden; border:1px solid var(--border-soft); background:var(--bg-inset); }
.route-map .empty{ padding:16px; }
/* Recolor the stock OSM tiles to sit inside the dark console instead of
   dropping a bright white rectangle into the page. Only applied to the plain-
   OSM fallback (no CARTO key configured) — CARTO Voyager is already a light,
   considered basemap and doesn't need forcing into the dark theme. */
.route-map.osm-fallback .leaflet-tile-pane{ filter:invert(1) hue-rotate(180deg) brightness(0.92) contrast(0.9) saturate(0.7); }
.route-map .leaflet-control-zoom a{ background:var(--bg-raised); color:var(--text); border-color:var(--border) !important; }
.route-map .leaflet-control-zoom a:hover{ background:var(--bg-panel); }
.route-map .leaflet-control-attribution{ background:rgba(13,16,19,0.72); color:var(--text-dim); }
.route-map .leaflet-control-attribution a{ color:var(--text-muted); }
.route-legend{ display:flex; gap:16px; margin-top:8px; font-size:11px; color:var(--text-muted); }
.modal-splits-table{ width:100%; border-collapse:collapse; font-size:12.5px; }
.modal-splits-table th{ text-align:left; font-family:var(--font-mono); font-size:10.5px; text-transform:uppercase; letter-spacing:0.05em; color:var(--text-dim); font-weight:500; padding:8px 10px; border-bottom:1px solid var(--border); }
.modal-splits-table td{ padding:8px 10px; border-bottom:1px solid var(--border-soft); font-family:var(--font-mono); }

.chart-expand-btn{ position:absolute; top:8px; right:8px; width:26px; height:26px; display:flex; align-items:center; justify-content:center; background:var(--bg-raised); border:1px solid var(--border); border-radius:6px; color:var(--text-dim); font-size:13px; line-height:1; cursor:pointer; opacity:0.55; transition:opacity .15s, color .15s, border-color .15s; z-index:2; }
.chart-expand-btn:hover, .chart-expand-btn:focus-visible{ opacity:1; color:var(--text); border-color:var(--text-dim); }
.chart-zoom-overlay{ align-items:center; z-index:1200; }
.chart-zoom-panel{ background:var(--bg-panel); border:1px solid var(--border); border-radius:14px; width:min(96vw,1140px); height:min(90vh,740px); padding:14px 16px 12px; display:flex; flex-direction:column; margin:0; }
.chart-zoom-toolbar{ display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
.chart-zoom-title{ font-family:var(--font-display); font-weight:700; font-size:15px; text-wrap:balance; }
.chart-zoom-controls{ display:flex; align-items:center; gap:6px; flex-shrink:0; }
.chart-zoom-controls button{ width:30px; height:30px; display:flex; align-items:center; justify-content:center; background:var(--bg-raised); border:1px solid var(--border); border-radius:7px; color:var(--text-muted); font-size:16px; line-height:1; cursor:pointer; }
.chart-zoom-controls button:hover{ color:var(--text); border-color:var(--text-dim); }
.chart-zoom-controls button.chart-zoom-close-btn{ font-size:19px; margin-left:6px; }
#chart-zoom-level{ font-family:var(--font-mono); font-size:11px; color:var(--text-dim); min-width:38px; text-align:center; }
.chart-zoom-viewport{ flex:1; margin-top:10px; position:relative; overflow-x:auto; overflow-y:hidden; border:1px solid var(--border-soft); border-radius:10px; background:var(--bg-inset); touch-action:pan-x; cursor:default; }
.chart-zoom-viewport.pannable{ cursor:grab; }
.chart-zoom-viewport.panning{ cursor:grabbing; }
.chart-zoom-stage{ width:100%; height:100%; }
#chart-zoom-box{ width:100%; height:100%; }
.chart-zoom-hint{ margin-top:8px; font-size:11px; color:var(--text-dim); text-align:center; }
"""

JS = r"""
function paceStr(min){ if(min==null) return '—'; const m=Math.floor(min), s=Math.round((min-m)*60); return `${m}:${s.toString().padStart(2,'0')}`; }
function durStr(min){ const t=Math.round(min*60), h=Math.floor(t/3600), m=Math.floor((t%3600)/60), s=t%60; return h>0?`${h}:${m.toString().padStart(2,'0')}:${s.toString().padStart(2,'0')}`:`${m}:${s.toString().padStart(2,'0')}`; }
function fmtDate(d){ return new Date(d+'T12:00:00').toLocaleDateString('en-US',{month:'short',day:'numeric'}); }
const TYPE_COLORS = {'Long Run':'#6690c4','Easy Run':'#8b95a1','Tempo':'#e3a857','Speed':'#c1614a','Benchmark':'#5fa8a0','Strides':'#5c6570'};
function safe(name, fn){ try{ fn(); } catch(e){ console.error('Section failed:', name, e); const el=document.getElementById('boot-errors'); if(el){ el.style.display='block'; el.innerHTML += `<div>Section "${name}" failed: ${e.message}</div>`; } } }
const SVGNS='http://www.w3.org/2000/svg';
function el(tag, attrs){ const e=document.createElementNS(SVGNS,tag); for(const k in attrs) e.setAttribute(k, attrs[k]); return e; }
function niceTicks(min,max,count){ if(min===max){min-=1;max+=1;} const range=max-min, rough=range/count, mag=Math.pow(10,Math.floor(Math.log10(rough))), norm=rough/mag; let step; if(norm<1.5) step=mag; else if(norm<3) step=2*mag; else if(norm<7) step=5*mag; else step=10*mag; const niceMin=Math.floor(min/step)*step, niceMax=Math.ceil(max/step)*step; const ticks=[]; for(let v=niceMin;v<=niceMax+step*0.001;v+=step) ticks.push(Math.round(v*1000)/1000); return ticks; }
const tooltip = document.getElementById('chart-tooltip');
function showTooltip(evt, html){ tooltip.innerHTML=html; tooltip.style.display='block'; positionTooltip(evt); }
function positionTooltip(evt){ const pad=14; let x=evt.clientX+pad, y=evt.clientY+pad; const tw=tooltip.offsetWidth||180, th=tooltip.offsetHeight||60; if(x+tw>window.innerWidth-10) x=evt.clientX-tw-pad; if(y+th>window.innerHeight-10) y=evt.clientY-th-pad; tooltip.style.left=x+'px'; tooltip.style.top=y+'px'; }
function hideTooltip(){ tooltip.style.display='none'; }
// Chart internal coordinate size is derived from the container's ACTUAL rendered
// pixel size (not a fixed design size stretched to fit). Previously every chart
// used a hardcoded viewBox with preserveAspectRatio="none", which non-uniformly
// stretched the SVG to fill whatever box CSS gave it — fine near the design's
// own aspect ratio, but visibly warped text and dots on narrow phone widths
// where the real aspect ratio diverges a lot. Matching W/H to the real box means
// there's no stretch to begin with, so nothing distorts at any viewport size.
function chartSize(container, fallbackW, fallbackH){
  const rect = container.getBoundingClientRect();
  const w = Math.max(Math.round(rect.width) || fallbackW, 220);
  const h = Math.max(Math.round(rect.height) || fallbackH, 140);
  return {w, h};
}
// Which indices get an x-axis label, figured from the ACTUAL plot width (which
// now varies by device, see chartSize above) rather than a label-count guess
// tuned for one fixed desktop width. Always includes the last point (usually
// the most recent/interesting one), but swaps it in for — rather than adds it
// next to — the nearest regularly-spaced label when the two would land closer
// than minGapPx apart and collide.
function labelIndices(n, plotWidthPx, minGapPx){
  if(n<=1) return new Set([0]);
  const perIdx = plotWidthPx/n;
  const step = Math.max(1, Math.ceil(minGapPx/perIdx));
  const idxs=[];
  for(let i=0;i<n;i+=step) idxs.push(i);
  if(!idxs.length) idxs.push(0);
  const last=idxs[idxs.length-1];
  if(last!==n-1){
    if((n-1-last)*perIdx >= minGapPx) idxs.push(n-1);
    else idxs[idxs.length-1]=n-1;
  }
  return new Set(idxs);
}
// labelIndices' minGapPx was previously a flat guess (34px, 26px, ...) that
// didn't account for how wide the actual label text renders — fine for short
// labels, but a real problem for 5-6 character date labels ("Aug 25"), where
// two labels could sit far enough apart to pass the guessed gap yet still
// visually collide, especially on narrow phone widths where every pixel is
// scarcer. This measures the actual widest label in THIS chart's own font via
// a canvas (cheap, no DOM attach needed) so the gap always matches reality.
let _labelMeasureCtx=null;
function widestLabelPx(strs, padPx){
  if(!_labelMeasureCtx) _labelMeasureCtx=document.createElement('canvas').getContext('2d');
  _labelMeasureCtx.font = "10px 'IBM Plex Mono','SF Mono','Cascadia Code',Consolas,monospace";
  const w = Math.max(0, ...strs.map(s=>_labelMeasureCtx.measureText(String(s)).width));
  return w + (padPx||8);
}

// --- Click-to-expand / zoom for every chart -------------------------------
// Every chart is rendered by calling one of the renderXChart(containerId, ...)
// functions above, and those functions size themselves from whatever element
// containerId points at (see chartSize()). That means the SAME render call
// can be replayed into a different, larger container to get a bigger version
// of the exact chart already on screen — no separate "zoom" chart type needed.
// registerChart() remembers how to replay a chart (its render fn + args) keyed
// by its normal container id, so the expand button can ask for a fresh, larger
// render on demand rather than trying to CSS-scale the small original (which
// would just blow up the same small SVG and blur it).
const CHART_REGISTRY = {};
function registerChart(containerId, title, renderFn, ...args){
  CHART_REGISTRY[containerId] = { title, render: (targetId) => renderFn(targetId, ...args) };
  renderFn(containerId, ...args);
}

function attachChartExpandButtons(root){
  (root || document).querySelectorAll('.chart-box').forEach(box => {
    if(box.id === 'chart-zoom-box') return; // the zoom viewer's own chart box
    if(box.querySelector('.chart-expand-btn')) return; // already wired up
    const svgDiv = box.querySelector('.svg-chart');
    if(!svgDiv || !svgDiv.id) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chart-expand-btn';
    btn.title = 'Expand & zoom';
    btn.setAttribute('aria-label', 'Expand chart for a larger, zoomable view');
    btn.innerHTML = '⤢';
    btn.addEventListener('click', e => { e.stopPropagation(); openChartZoom(svgDiv.id); });
    box.appendChild(btn);
  });
}

// Zooming used to CSS-scale a fixed-size picture of the chart uniformly in
// both directions — a blunt magnifying glass over the same pixels, aspect
// ratio unchanged. Every chart already measures its own container's real
// pixel size and draws to fit it (see chartSize() above), so instead of
// scaling a picture, zooming here makes the chart itself wider and asks it
// to redraw: bars/points/ticks get real extra room and gain actual detail
// (closer-spaced labels resolve, bars separate) rather than the same shapes
// blown up and blurred. The vertical axis is deliberately left alone —
// height always matches the viewport — so the chart's aspect ratio stretches
// wide as you zoom in, instead of the whole picture growing uniformly.
let ZOOM = { scale:1, chartId:null };
function chartZoomEls(){
  return {
    modal: document.getElementById('chart-zoom-modal'),
    viewport: document.getElementById('chart-zoom-viewport'),
    stage: document.getElementById('chart-zoom-stage'),
    title: document.getElementById('chart-zoom-title'),
    level: document.getElementById('chart-zoom-level'),
  };
}
function clampZoomScale(s){ return Math.min(6, Math.max(1, s)); }

// Re-render is coalesced to one per animation frame so rapid wheel/pinch
// input doesn't trigger a full chart rebuild (all points, all listeners) on
// every event.
let _zoomRenderScheduled = false;
function scheduleZoomRerender(){
  if(_zoomRenderScheduled) return;
  _zoomRenderScheduled = true;
  requestAnimationFrame(() => {
    _zoomRenderScheduled = false;
    const entry = CHART_REGISTRY[ZOOM.chartId];
    if(!entry) return;
    const container = document.getElementById('chart-zoom-target');
    if(container) container.innerHTML = '';
    entry.render('chart-zoom-target');
  });
}
// anchor (optional): {anchorPx, frac} — keeps the same horizontal fraction of
// the chart under anchorPx (a cursor position or pinch midpoint, relative to
// the viewport) as the width changes, so zooming feels centered on where you
// pointed rather than snapping back to the left edge.
function applyZoom(anchor){
  const {viewport, stage, level} = chartZoomEls();
  const vpW = viewport.clientWidth || 1;
  const targetW = Math.round(vpW * ZOOM.scale);
  stage.style.width = targetW + 'px';
  viewport.scrollLeft = anchor ? (anchor.frac*targetW - anchor.anchorPx) : 0;
  level.textContent = Math.round(ZOOM.scale*100) + '%';
  viewport.classList.toggle('pannable', ZOOM.scale > 1);
  scheduleZoomRerender();
}
function resetZoom(){ ZOOM.scale=1; applyZoom(); }
function zoomBy(factor, atVpX){
  const {viewport} = chartZoomEls();
  const rect = viewport.getBoundingClientRect();
  const anchorPx = atVpX!=null ? atVpX : rect.width/2;
  const oldW = Math.max(1, viewport.scrollWidth);
  const frac = (viewport.scrollLeft + anchorPx) / oldW;
  ZOOM.scale = clampZoomScale(ZOOM.scale*factor);
  applyZoom({ anchorPx, frac });
}
function openChartZoom(chartId){
  const entry = CHART_REGISTRY[chartId];
  if(!entry) return;
  const {modal, title} = chartZoomEls();
  title.textContent = entry.title || '';
  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';
  ZOOM = { scale:1, chartId };
  // Wait for the modal to finish laying out (two frames, to be safe across
  // browsers) so the viewport has its real size before applyZoom measures it.
  requestAnimationFrame(() => requestAnimationFrame(() => applyZoom()));
}
function closeChartZoom(){
  document.getElementById('chart-zoom-modal').style.display = 'none';
  document.body.style.overflow = '';
  ZOOM.chartId = null;
}
safe('chart zoom modal', function(){
  const {modal, viewport} = chartZoomEls();
  document.getElementById('chart-zoom-close').addEventListener('click', closeChartZoom);
  document.getElementById('chart-zoom-in').addEventListener('click', () => zoomBy(1.5));
  document.getElementById('chart-zoom-out').addEventListener('click', () => zoomBy(1/1.5));
  document.getElementById('chart-zoom-reset').addEventListener('click', resetZoom);
  modal.addEventListener('click', e => { if(e.target===modal) closeChartZoom(); });
  document.addEventListener('keydown', e => { if(e.key==='Escape' && modal.style.display!=='none') closeChartZoom(); });
  viewport.addEventListener('dblclick', resetZoom);

  // Vertical wheel motion (a mouse wheel, or a trackpad's vertical two-finger
  // scroll) zooms. Horizontal motion is left alone so a trackpad's horizontal
  // swipe pans by scrolling the viewport natively — no custom code needed.
  viewport.addEventListener('wheel', e => {
    if(Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
    e.preventDefault();
    const rect = viewport.getBoundingClientRect();
    zoomBy(Math.pow(1.0016, -e.deltaY), e.clientX-rect.left);
  }, {passive:false});

  // Click-drag panning for a plain mouse (trackpad swipes and touch already
  // pan via the viewport's native horizontal scrolling).
  let dragging=false, dragStartX=0, startScrollLeft=0;
  viewport.addEventListener('mousedown', e => {
    if(ZOOM.scale<=1) return;
    dragging=true; dragStartX=e.clientX; startScrollLeft=viewport.scrollLeft;
    viewport.classList.add('panning');
  });
  window.addEventListener('mousemove', e => {
    if(!dragging) return;
    viewport.scrollLeft = startScrollLeft - (e.clientX-dragStartX);
  });
  window.addEventListener('mouseup', () => { dragging=false; viewport.classList.remove('panning'); });

  // Two-finger pinch zooms; a single finger pans natively via the viewport's
  // own horizontal scrolling (touch-action:pan-x in the CSS above).
  let pinch=null;
  viewport.addEventListener('touchstart', e => {
    if(e.touches.length===2){
      const [a,b]=e.touches;
      const rect=viewport.getBoundingClientRect();
      pinch={ dist:Math.hypot(b.clientX-a.clientX,b.clientY-a.clientY), scale:ZOOM.scale, midX:(a.clientX+b.clientX)/2-rect.left };
    } else {
      pinch=null;
    }
  }, {passive:true});
  viewport.addEventListener('touchmove', e => {
    if(!pinch || e.touches.length!==2) return;
    e.preventDefault();
    const [a,b]=e.touches;
    const dist=Math.hypot(b.clientX-a.clientX,b.clientY-a.clientY);
    const oldW=Math.max(1, viewport.scrollWidth);
    const frac=(viewport.scrollLeft + pinch.midX) / oldW;
    ZOOM.scale=clampZoomScale(pinch.scale*(dist/pinch.dist));
    applyZoom({ anchorPx: pinch.midX, frac });
  }, {passive:false});
  viewport.addEventListener('touchend', () => { pinch=null; });
});

function renderVolumeChart(containerId, weekly){
  const container=document.getElementById(containerId); container.innerHTML='';
  if(!weekly.length){ container.innerHTML="<p class='empty'>No weekly data yet.</p>"; return; }
  const {w:W,h:H}=chartSize(container,720,300), M={top:26,right:40,bottom:34,left:42};
  const plotW=W-M.left-M.right, plotH=H-M.top-M.bottom;
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,preserveAspectRatio:'none'});
  const maxMiles=Math.max(...weekly.map(w=>w.miles),1);
  const yTicks=niceTicks(0,maxMiles,5), yMax=yTicks[yTicks.length-1];
  const yScale=v=>M.top+plotH-(v/yMax)*plotH;
  const runsMax=Math.max(6,...weekly.map(w=>w.runs));
  const y1Scale=v=>M.top+plotH-(v/runsMax)*plotH;
  const n=weekly.length, bandW=plotW/n, xCenter=i=>M.left+bandW*i+bandW/2;
  const volLabels=labelIndices(n, plotW, widestLabelPx(weekly.map(w=>w.label)));
  yTicks.forEach(t=>{ svg.appendChild(el('line',{class:'grid-line',x1:M.left,x2:W-M.right,y1:yScale(t),y2:yScale(t)})); const lbl=el('text',{x:M.left-8,y:yScale(t)+3,'text-anchor':'end'}); lbl.textContent=t; svg.appendChild(lbl); });
  const yTitle=el('text',{x:10,y:12}); yTitle.textContent='miles'; svg.appendChild(yTitle);
  const y1Title=el('text',{x:W-M.right,y:12,'text-anchor':'end'}); y1Title.textContent='runs/wk'; svg.appendChild(y1Title);
  const milesBarW=bandW*0.44, lrBarW=bandW*0.24;
  weekly.forEach((w,i)=>{
    const cx=xCenter(i), mBarX=cx-milesBarW-2, mBarY=yScale(w.miles);
    const mBar=el('rect',{class:'data-point',x:mBarX,y:mBarY,width:milesBarW,height:(M.top+plotH)-mBarY,fill:'#e3a857',rx:2});
    mBar.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">Week of ${w.label}</div><div class="tt-row">Miles: <b>${w.miles.toFixed(1)}</b></div><div class="tt-row">Runs: <b>${w.runs}</b></div>${w.longRunMiles?`<div class="tt-row">Long run: <b>${w.longRunMiles.toFixed(1)}mi</b></div>`:''}`));
    mBar.addEventListener('mousemove',positionTooltip); mBar.addEventListener('mouseleave',hideTooltip);
    svg.appendChild(mBar);
    if(w.longRunMiles){
      const lrBarX=cx+2, lrBarY=yScale(w.longRunMiles);
      const lrBar=el('rect',{class:'data-point',x:lrBarX,y:lrBarY,width:lrBarW,height:(M.top+plotH)-lrBarY,fill:'#6690c4',rx:2});
      lrBar.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">Week of ${w.label}</div><div class="tt-row">Long run: <b>${w.longRunMiles.toFixed(1)}mi</b></div>`));
      lrBar.addEventListener('mousemove',positionTooltip); lrBar.addEventListener('mouseleave',hideTooltip);
      svg.appendChild(lrBar);
    }
    if(volLabels.has(i)){ const xl=el('text',{x:cx,y:H-M.bottom+16,'text-anchor':'middle'}); xl.textContent=w.label; svg.appendChild(xl); }
  });
  let linePath=''; weekly.forEach((w,i)=>{ const x=xCenter(i), y=y1Scale(w.runs); linePath+=(i===0?'M':'L')+x+','+y+' '; });
  svg.appendChild(el('path',{d:linePath.trim(),fill:'none',stroke:'#5fa8a0','stroke-width':2}));
  weekly.forEach((w,i)=>{ const c=el('circle',{class:'data-point',cx:xCenter(i),cy:y1Scale(w.runs),r:3.5,fill:'#5fa8a0'}); c.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">Week of ${w.label}</div><div class="tt-row">Runs: <b>${w.runs}</b></div>`)); c.addEventListener('mousemove',positionTooltip); c.addEventListener('mouseleave',hideTooltip); svg.appendChild(c); });
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:M.left,y1:M.top,y2:M.top+plotH}));
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:W-M.right,y1:M.top+plotH,y2:M.top+plotH}));
  container.appendChild(svg);
}

function renderPlanChart(containerId, planWeeks){
  const container=document.getElementById(containerId); container.innerHTML='';
  if(!planWeeks || !planWeeks.length){ container.innerHTML="<p class='empty'>No training plan loaded.</p>"; return; }
  const {w:W,h:H}=chartSize(container,720,300), M={top:26,right:20,bottom:34,left:42};
  const plotW=W-M.left-M.right, plotH=H-M.top-M.bottom;
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,preserveAspectRatio:'none'});
  const maxMiles=Math.max(...planWeeks.map(w=>Math.max(w.plannedMi||0, w.actualMi||0)),1);
  const yTicks=niceTicks(0,maxMiles,5), yMax=yTicks[yTicks.length-1];
  const yScale=v=>M.top+plotH-(v/yMax)*plotH;
  const n=planWeeks.length, bandW=plotW/n, xCenter=i=>M.left+bandW*i+bandW/2;
  const wkLabels=labelIndices(n, plotW, widestLabelPx(planWeeks.map(w=>w.weekLabel)));
  yTicks.forEach(t=>{ svg.appendChild(el('line',{class:'grid-line',x1:M.left,x2:W-M.right,y1:yScale(t),y2:yScale(t)})); const lbl=el('text',{x:M.left-8,y:yScale(t)+3,'text-anchor':'end'}); lbl.textContent=t; svg.appendChild(lbl); });
  const yTitle=el('text',{x:10,y:12}); yTitle.textContent='miles'; svg.appendChild(yTitle);
  const plannedBarW=bandW*0.34, actualBarW=bandW*0.34;
  planWeeks.forEach((w,i)=>{
    const cx=xCenter(i);
    const pBarX=cx-plannedBarW-2, pBarY=yScale(w.plannedMi||0);
    const pBar=el('rect',{class:'data-point',x:pBarX,y:pBarY,width:plannedBarW,height:(M.top+plotH)-pBarY,fill:'#8b95a1',rx:2});
    pBar.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">Week of ${w.weekLabel}</div><div class="tt-row">${w.phase}</div><div class="tt-row">Planned: <b>${(w.plannedMi||0).toFixed(1)}mi</b></div>`));
    pBar.addEventListener('mousemove',positionTooltip); pBar.addEventListener('mouseleave',hideTooltip);
    svg.appendChild(pBar);
    if(w.actualMi!=null){
      const aBarX=cx+2, aBarY=yScale(w.actualMi);
      const aBar=el('rect',{class:'data-point',x:aBarX,y:aBarY,width:actualBarW,height:(M.top+plotH)-aBarY,fill:'#e3a857',rx:2});
      aBar.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">Week of ${w.weekLabel}</div><div class="tt-row">${w.phase}</div><div class="tt-row">Actual: <b>${w.actualMi.toFixed(1)}mi</b></div>${w.adherencePct!=null?`<div class="tt-row">Adherence: <b>${w.adherencePct}%</b></div>`:''}`));
      aBar.addEventListener('mousemove',positionTooltip); aBar.addEventListener('mouseleave',hideTooltip);
      svg.appendChild(aBar);
    }
    if(wkLabels.has(i)){ const xl=el('text',{x:cx,y:H-M.bottom+16,'text-anchor':'middle'}); xl.textContent=w.weekLabel; svg.appendChild(xl); }
  });
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:M.left,y1:M.top,y2:M.top+plotH}));
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:W-M.right,y1:M.top+plotH,y2:M.top+plotH}));
  container.appendChild(svg);
}

function renderPaceChart(containerId, runsAsc){
  const container=document.getElementById(containerId); container.innerHTML='';
  const runs=runsAsc.filter(r=>r.paceMinMi);
  if(runs.length<2){ container.innerHTML="<p class='empty'>Not enough paced runs yet.</p>"; return; }
  const {w:W,h:H}=chartSize(container,720,300), M={top:26,right:20,bottom:34,left:50};
  const plotW=W-M.left-M.right, plotH=H-M.top-M.bottom;
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,preserveAspectRatio:'none'});
  const rolling=runs.map((r,i)=>{ const w=runs.slice(Math.max(0,i-4),i+1); return w.reduce((s,x)=>s+x.paceMinMi,0)/w.length; });
  const paces=runs.map(r=>r.paceMinMi);
  const minP=Math.min(...paces)-0.6, maxP=Math.max(...paces)+0.6;
  const yTicks=niceTicks(minP,maxP,5);
  const yScale=v=>M.top+((v-yTicks[0])/(yTicks[yTicks.length-1]-yTicks[0]))*plotH;
  const n=runs.length, xScale=i=>n<=1?M.left+plotW/2:M.left+(i/(n-1))*plotW;
  yTicks.forEach(t=>{ const y=yScale(t); svg.appendChild(el('line',{class:'grid-line',x1:M.left,x2:W-M.right,y1:y,y2:y})); const lbl=el('text',{x:M.left-8,y:y+3,'text-anchor':'end'}); lbl.textContent=paceStr(t); svg.appendChild(lbl); });
  const yTitle=el('text',{x:6,y:12}); yTitle.textContent='min/mile'; svg.appendChild(yTitle);
  const paceLabels=labelIndices(n, plotW, widestLabelPx(runs.map(r=>r.dateLabel)));
  runs.forEach((r,i)=>{ if(paceLabels.has(i)){ const xl=el('text',{x:xScale(i),y:H-M.bottom+16,'text-anchor':'middle'}); xl.textContent=r.dateLabel; svg.appendChild(xl); } });
  let path=''; runs.forEach((r,i)=>{ path+=(i===0?'M':'L')+xScale(i)+','+yScale(rolling[i])+' '; });
  svg.appendChild(el('path',{d:path.trim(),fill:'none',stroke:'#e7e9ec','stroke-width':1.5,'stroke-dasharray':'4,3'}));
  runs.forEach((r,i)=>{ const c=el('circle',{class:'data-point',cx:xScale(i),cy:yScale(r.paceMinMi),r:5,fill:TYPE_COLORS[r.type]||'#8b95a1'}); c.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">${r.name}</div><div class="tt-row">${fmtDate(r.date)} · ${r.type}</div><div class="tt-row">Pace: <b>${paceStr(r.paceMinMi)}/mi</b></div><div class="tt-row">Dist: <b>${r.distMi.toFixed(1)}mi</b></div>`)); c.addEventListener('mousemove',positionTooltip); c.addEventListener('mouseleave',hideTooltip); svg.appendChild(c); });
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:M.left,y1:M.top,y2:M.top+plotH}));
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:W-M.right,y1:M.top+plotH,y2:M.top+plotH}));
  container.appendChild(svg);
}

function renderSeriesChart(containerId, series, valueKey, color){
  const container=document.getElementById(containerId); container.innerHTML='';
  const pts=series.filter(p=>typeof p[valueKey]==='number');
  if(pts.length<2){ container.innerHTML="<p class='empty'>Not enough data yet.</p>"; return; }
  const {w:W,h:H}=chartSize(container,420,190), M={top:12,right:12,bottom:26,left:32};
  const plotW=W-M.left-M.right, plotH=H-M.top-M.bottom;
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,preserveAspectRatio:'none'});
  const vals=pts.map(p=>p[valueKey]);
  const yTicks=niceTicks(Math.min(...vals)-1,Math.max(...vals)+1,4);
  const yMin=yTicks[0], yMax=yTicks[yTicks.length-1];
  const yScale=v=>M.top+plotH-((v-yMin)/(yMax-yMin))*plotH;
  const n=pts.length, xScale=i=>n<=1?M.left+plotW/2:M.left+(i/(n-1))*plotW;
  yTicks.forEach(t=>{ const y=yScale(t); svg.appendChild(el('line',{class:'grid-line',x1:M.left,x2:W-M.right,y1:y,y2:y})); const lbl=el('text',{x:M.left-6,y:y+3,'text-anchor':'end'}); lbl.textContent=t; svg.appendChild(lbl); });
  const seriesLabels=labelIndices(n, plotW, widestLabelPx(pts.map(p=>fmtDate(p.date))));
  pts.forEach((p,i)=>{ if(seriesLabels.has(i)){ const xl=el('text',{x:xScale(i),y:H-M.bottom+14,'text-anchor':'middle'}); xl.textContent=fmtDate(p.date); svg.appendChild(xl); } });
  let linePath='', areaPath='';
  pts.forEach((p,i)=>{ const x=xScale(i), y=yScale(p[valueKey]); linePath+=(i===0?'M':'L')+x+','+y+' '; areaPath+=(i===0?'M':'L')+x+','+y+' '; });
  areaPath+=`L${xScale(n-1)},${M.top+plotH} L${xScale(0)},${M.top+plotH} Z`;
  svg.appendChild(el('path',{d:areaPath,fill:color+'22',stroke:'none'}));
  svg.appendChild(el('path',{d:linePath.trim(),fill:'none',stroke:color,'stroke-width':2}));
  pts.forEach((p,i)=>{ const c=el('circle',{class:'data-point',cx:xScale(i),cy:yScale(p[valueKey]),r:3,fill:color,opacity:0.9}); const extra = p.status? `<div class="tt-row">${p.status}</div>` : ''; c.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">${fmtDate(p.date)}</div><div class="tt-row">${p[valueKey]}</div>${extra}`)); c.addEventListener('mousemove',positionTooltip); c.addEventListener('mouseleave',hideTooltip); svg.appendChild(c); });
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:M.left,y1:M.top,y2:M.top+plotH}));
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:W-M.right,y1:M.top+plotH,y2:M.top+plotH}));
  container.appendChild(svg);
}

function renderSplitsChart(containerId, splits, legendId, elevProfile, mileBased){
  if(mileBased===undefined) mileBased=true; // older cached data with no flag — assume the common case
  const container=document.getElementById(containerId); container.innerHTML='';
  if(!splits.length){ container.innerHTML="<p class='empty'>No splits for this run.</p>"; if(legendId){ const lg=document.getElementById(legendId); if(lg) lg.innerHTML=''; } return; }
  const {w:W,h:H}=chartSize(container,720,320), M={top:28,right:20,bottom:34,left:50};
  const plotW=W-M.left-M.right, plotH=H-M.top-M.bottom;
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,preserveAspectRatio:'none'});
  const n=splits.length;
  const labelWord = mileBased ? 'Mile' : 'Lap';
  // Each split's REAL distance (falling back to 1mi/lap if Garmin didn't return
  // one) rather than assuming every lap is exactly one mile wide. That fallback
  // assumption used to be baked into distScale itself — harmless for real
  // per-mile autolaps (each lap really is ~1mi), but for a structured workout
  // (interval reps + recovery jogs of very different lengths) it drew every lap
  // as an equal-width "mile," which is exactly what mislabeled a 400m rep as a
  // finished mile. distScale/xCenter now work off actual cumulative distance,
  // so a short rep gets a proportionally narrow slice and a long recovery jog
  // gets a proportionally wide one — correct either way, not just for miles.
  const lapDist = splits.map(s=>(s.distMi!=null && s.distMi>0) ? s.distMi : 1);
  const cum=[0]; lapDist.forEach(d=>cum.push(cum[cum.length-1]+d));
  const totalDist = cum[n] || n;
  const distScale=d=>M.left+(Math.min(Math.max(d,0),totalDist)/totalDist)*plotW;
  const xCenter=i=>distScale((cum[i]+cum[i+1])/2);
  // Label spacing measured off each split's REAL x position (not an assumed
  // even index-spacing) since splits are no longer necessarily equal-width —
  // a run with several short, tightly-clustered reps needs fewer visible
  // labels than its raw split count would suggest under an even-spacing guess.
  const splitLabelMinGap = widestLabelPx(splits.map(s=>labelWord.slice(0,mileBased?2:3)+' '+s.mile));
  const labelIdx=[]; let lastLabelX=-Infinity;
  for(let i=0;i<n;i++){ const x=xCenter(i); if(x-lastLabelX>=splitLabelMinGap){ labelIdx.push(i); lastLabelX=x; } }
  if(labelIdx[labelIdx.length-1]!==n-1){
    const lastX=xCenter(n-1);
    if(lastX-lastLabelX>=splitLabelMinGap) labelIdx.push(n-1); else labelIdx[labelIdx.length-1]=n-1;
  }
  const mileLabels=new Set(labelIdx);
  const paces=splits.map(s=>s.pace).filter(p=>p>0);
  const paceMin=Math.min(...paces)-0.4, paceMax=Math.max(...paces)+0.4;
  const paceTop=M.top, paceBottom=M.top+plotH;
  const yPace=v=>paceTop+((v-paceMin)/(paceMax-paceMin))*(paceBottom-paceTop);
  const hrs=splits.map(s=>s.avgHr).filter(h=>h);
  const hrTicks = hrs.length ? niceTicks(Math.min(...hrs)-5,Math.max(...hrs)+5,4) : [0,1];
  const hrMin=hrTicks[0], hrMax=hrTicks[hrTicks.length-1];
  const yHr=v=>M.top+plotH-((v-hrMin)/(hrMax-hrMin))*plotH;
  const baseline=M.top+plotH;
  const elevCapPx=plotH*0.34;
  const paceTicks=niceTicks(paceMin,paceMax,5);
  paceTicks.forEach(t=>{ const y=yPace(t); if(y<M.top-1||y>M.top+plotH+1) return; svg.appendChild(el('line',{class:'grid-line',x1:M.left,x2:W-M.right,y1:y,y2:y})); const lbl=el('text',{x:M.left-8,y:y+3,'text-anchor':'end'}); lbl.textContent=paceStr(t); svg.appendChild(lbl); });
  const yTitle=el('text',{x:6,y:12}); yTitle.textContent='min/mi'; svg.appendChild(yTitle);
  const y1Title=el('text',{x:W-M.right,y:12,'text-anchor':'end'}); y1Title.textContent='bpm'; svg.appendChild(y1Title);

  // ---- Elevation: a sub-mile altitude trace when Garmin returned one for this run,
  // a per-mile gain line otherwise (same shape as before, just filled instead of
  // barred). Either way it's drawn first so pace/HR sit visually on top of it.
  const elevColor='#7c8a9e';
  const hasProfile = Array.isArray(elevProfile) && elevProfile.length>=6;
  let elevPts;
  if(hasProfile){
    const alts=elevProfile.map(p=>p.elevFt);
    const altMin=Math.min(...alts), altMax=Math.max(...alts), range=(altMax-altMin)||1;
    elevPts = elevProfile.map(p=>[distScale(p.distMi), baseline-((p.elevFt-altMin)/range)*elevCapPx]);
  } else {
    const maxGain=Math.max(...splits.map(s=>s.elevGainFt||0),1);
    elevPts = splits.map((s,i)=>[xCenter(i), baseline-((s.elevGainFt||0)/maxGain)*elevCapPx]);
  }
  const elevLine = elevPts.reduce((d,p,i)=>d+(i===0?'M':'L')+p[0]+','+p[1]+' ','');
  const elevArea = elevLine + `L${elevPts[elevPts.length-1][0]},${baseline} L${elevPts[0][0]},${baseline} Z`;
  svg.appendChild(el('path',{d:elevArea, fill:elevColor+'2e', stroke:'none'}));
  svg.appendChild(el('path',{d:elevLine, fill:'none', stroke:elevColor, 'stroke-width':hasProfile?1.3:1.5, 'stroke-linejoin':'round'}));

  // One hover target + one x-axis label per split regardless of which elevation
  // resolution is showing — the tooltip always reports that split's actual gain.
  // Each split's tooltip title includes its real distance for a "Lap" (not a
  // "Mile") since "Lap 3" alone doesn't tell you it was a 0.52mi rep.
  const splitTitle = s => mileBased ? `${labelWord} ${s.mile}` : `${labelWord} ${s.mile}${s.distMi!=null?` · ${s.distMi.toFixed(2)}mi`:''}`;
  splits.forEach((s,i)=>{
    const x0=distScale(cum[i]), x1=distScale(cum[i+1]);
    const hit=el('rect',{x:x0,y:M.top,width:Math.max(x1-x0,1),height:plotH,fill:'transparent'});
    hit.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">${splitTitle(s)}</div><div class="tt-row">Elevation gain: <b>+${s.elevGainFt||0}ft</b></div>`));
    hit.addEventListener('mousemove',positionTooltip); hit.addEventListener('mouseleave',hideTooltip);
    svg.appendChild(hit);
    if(mileLabels.has(i)){ const xl=el('text',{x:xCenter(i),y:H-M.bottom+16,'text-anchor':'middle'}); xl.textContent=(mileBased?'Mi ':'Lap ')+s.mile; svg.appendChild(xl); }
  });
  let pacePath=''; splits.forEach((s,i)=>{ pacePath+=(i===0?'M':'L')+xCenter(i)+','+yPace(s.pace)+' '; });
  svg.appendChild(el('path',{d:pacePath.trim(),fill:'none',stroke:'#e3a857','stroke-width':2.5}));
  splits.forEach((s,i)=>{ const c=el('circle',{class:'data-point',cx:xCenter(i),cy:yPace(s.pace),r:4.5,fill:'#e3a857'}); c.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">${splitTitle(s)}</div><div class="tt-row">Pace: <b>${paceStr(s.pace)}/mi</b></div>`)); c.addEventListener('mousemove',positionTooltip); c.addEventListener('mouseleave',hideTooltip); svg.appendChild(c); });
  if(hrs.length){
    let hrPath=''; splits.forEach((s,i)=>{ hrPath+=(i===0?'M':'L')+xCenter(i)+','+yHr(s.avgHr||hrMin)+' '; });
    svg.appendChild(el('path',{d:hrPath.trim(),fill:'none',stroke:'#c1614a','stroke-width':2.5}));
    splits.forEach((s,i)=>{ if(!s.avgHr) return; const c=el('circle',{class:'data-point',cx:xCenter(i),cy:yHr(s.avgHr),r:4.5,fill:'#c1614a'}); c.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">${splitTitle(s)}</div><div class="tt-row">Avg HR: <b>${s.avgHr} bpm</b></div>${s.maxHr?`<div class="tt-row">Max HR: <b>${s.maxHr} bpm</b></div>`:''}`)); c.addEventListener('mousemove',positionTooltip); c.addEventListener('mouseleave',hideTooltip); svg.appendChild(c); });
  }
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:M.left,y1:M.top,y2:M.top+plotH}));
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:W-M.right,y1:M.top+plotH,y2:M.top+plotH}));
  container.appendChild(svg);
  // Legend lives in its own HTML row (not SVG text) so it wraps naturally on
  // narrow screens instead of colliding with the axis titles at a fixed pixel spot.
  if(legendId){
    const lg=document.getElementById(legendId);
    if(lg){
      const legendItems=[{c:'#e3a857',t:'Pace'},{c:'#c1614a',t:'Avg HR'},{c:elevColor,t:hasProfile?'Elevation':'Elevation gain'}];
      lg.innerHTML = legendItems.map(it=>`<div class="legend-item"><span class="legend-swatch" style="background:${it.c}"></span>${it.t}</div>`).join('');
    }
  }
}

// One real Leaflet map per open modal, keyed by container id, so a reopened
// modal (or a resize while one's open) can find and clean up/resize the
// right instance instead of leaking map objects every time a run is clicked.
let ROUTE_MAP_INSTANCES={};
function renderRouteMap(containerId, points){
  const container=document.getElementById(containerId);
  if(!container) return;
  if(!points || points.length<2){ container.innerHTML="<p class='empty'>No GPS route available for this run.</p>"; return; }
  if(typeof L==='undefined'){ container.innerHTML="<p class='empty'>Map failed to load — check your internet connection and reopen this run.</p>"; return; }
  if(ROUTE_MAP_INSTANCES[containerId]){ try{ ROUTE_MAP_INSTANCES[containerId].remove(); }catch(e){} delete ROUTE_MAP_INSTANCES[containerId]; }
  container.innerHTML='';
  const latlngs=points.map(p=>[p[0],p[1]]);
  const map=L.map(container,{scrollWheelZoom:false});
  // CARTO Voyager (a Google Maps–style basemap) when a key is configured; plain
  // OSM tiles otherwise, so the map still works out of the box before anyone
  // sets one up. The dark-console recolor filter below was built to force OSM's
  // stark white default into this theme — Voyager is already a considered,
  // muted light basemap, so it renders as-is and the filter only applies to
  // the OSM fallback (see the CSS: .route-map.osm-fallback).
  const cartoKey = DATA.meta.cartoApiKey;
  container.classList.toggle('osm-fallback', !cartoKey);
  if(cartoKey){
    L.tileLayer(`https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png?key=${cartoKey}`,{
      maxZoom:20,
      attribution:'&copy; <a href="https://carto.com/attributions" target="_blank" rel="noopener">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'
    }).addTo(map);
  } else {
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{
      maxZoom:19,
      attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'
    }).addTo(map);
  }
  const line=L.polyline(latlngs,{color:'#e3a857',weight:4,opacity:0.95,lineJoin:'round',lineCap:'round'}).addTo(map);
  L.circleMarker(latlngs[0],{radius:6,color:'#12151a',weight:2,fillColor:'#5fa8a0',fillOpacity:1}).addTo(map).bindTooltip('Start');
  L.circleMarker(latlngs[latlngs.length-1],{radius:6,color:'#12151a',weight:2,fillColor:'#c1614a',fillOpacity:1}).addTo(map).bindTooltip('Finish');
  map.fitBounds(line.getBounds(),{padding:[18,18]});
  ROUTE_MAP_INSTANCES[containerId]=map;
}

// Re-drawn on resize so the "match the container's real pixel size" fix above
// actually keeps charts crisp as the viewport changes (rotation, window resize,
// devtools panel toggling) instead of only getting it right on first paint.
let RUNS_ASC=null, ACTIVE_SPLIT_ID=null;
function redrawCharts(){
  safe('redraw volume', ()=>registerChart('chart-volume', 'Weekly Volume & Training Load', renderVolumeChart, DATA.weekly));
  safe('redraw plan', ()=>registerChart('chart-plan', 'Plan vs. Actual', renderPlanChart, DATA.planComparison));
  safe('redraw pace', ()=>{ if(RUNS_ASC) registerChart('chart-pace', 'Pace Progression', renderPaceChart, RUNS_ASC); });
  safe('redraw hrv', ()=>registerChart('chart-hrv', 'HRV Trend', renderSeriesChart, DATA.hrv, 'hrv', '#5fa8a0'));
  safe('redraw vo2', ()=>registerChart('chart-vo2', 'VO2 Max Trend', renderSeriesChart, DATA.vo2max, 'vo2', '#e3a857'));
  safe('redraw efficiency', ()=>registerChart('chart-efficiency', 'Aerobic Efficiency — Easy & Long Runs', renderSeriesChart, DATA.efficiencyTrend, 'ef', '#5fa8a0'));
  safe('redraw splits', ()=>{ if(ACTIVE_SPLIT_ID && DATA.longRuns[ACTIVE_SPLIT_ID]){ const lr=DATA.longRuns[ACTIVE_SPLIT_ID]; registerChart('chart-splits', `Long Run Splits — ${lr.label}`, renderSplitsChart, lr.splits, 'splits-legend', lr.elevProfile, lr.mileBased); } });
  Object.values(ROUTE_MAP_INSTANCES).forEach(m=>{ try{ m.invalidateSize(); }catch(e){} });
  if(ZOOM.chartId && CHART_REGISTRY[ZOOM.chartId] && document.getElementById('chart-zoom-modal').style.display!=='none'){
    resetZoom(); // also schedules a re-render at the viewport's new size
  }
}
let _resizeTimer;
window.addEventListener('resize', ()=>{ clearTimeout(_resizeTimer); _resizeTimer=setTimeout(redrawCharts, 180); });

safe('header', function(){
  const m = DATA.meta;
  document.getElementById('hero-title').textContent = `Build → ${m.raceName}`;
  document.getElementById('sync-text').textContent = `Synced from Garmin — through ${fmtDate(m.lastSynced)}`;
  document.getElementById('footer-sync-date').textContent = fmtDate(m.lastSynced);
  const cells = [
    { label:'Race Day', value: fmtDate(m.raceDate), sub: m.raceName },
    { label:'Time to Race', value: m.daysLeft>=0?`${m.daysLeft}d`:'—', sub: m.daysLeft>=0?`${m.weeksLeft} weeks`:'race complete', accent:true },
    { label:'Phase', value: m.phase, sub: '' },
  ];
  document.getElementById('countdown-strip').innerHTML = cells.map(c=>`
    <div class="countdown-cell"><span class="cc-label">${c.label}</span><span class="cc-value ${c.accent?'accent':''}">${c.value}</span><span class="cc-sub">${c.sub}</span></div>
  `).join('');
});

safe('hero stats', function(){
  const runs = DATA.runs;
  const last28 = runs.filter(r => (new Date(DATA.meta.lastSynced)-new Date(r.date))/86400000 <= 28);
  const prev28 = runs.filter(r => { const d=(new Date(DATA.meta.lastSynced)-new Date(r.date))/86400000; return d>28 && d<=56; });
  const totalMi = runs.reduce((s,r)=>s+r.distMi,0);
  const last28Mi = last28.reduce((s,r)=>s+r.distMi,0);
  const prev28Mi = prev28.reduce((s,r)=>s+r.distMi,0);
  const mileageDelta = prev28Mi>0 ? Math.round(((last28Mi-prev28Mi)/prev28Mi)*100) : null;
  const longest = runs.length ? runs.reduce((max,r)=>r.distMi>max.distMi?r:max, runs[0]) : null;
  const vo2Pts = DATA.vo2max;
  const vo2 = DATA.vo2maxToday;
  const vo2First = vo2Pts.length ? vo2Pts[0].vo2 : null;
  const r = DATA.trainingReadiness;
  const stats = [
    { label:'Total Distance', value: totalMi.toFixed(1), unit:'mi', delta: `${runs.length} runs · this window` },
    { label:'Last 28 Days', value: last28Mi.toFixed(1), unit:'mi', delta: mileageDelta==null?`${last28.length} runs`:`${mileageDelta>0?'+':''}${mileageDelta}% vs prior 28d`, deltaClass: mileageDelta>0?'up':(mileageDelta<0?'warn':'') },
    { label:'Longest Run', value: longest?longest.distMi.toFixed(1):'—', unit:'mi', delta: longest?`${fmtDate(longest.date)} · ${paceStr(longest.paceMinMi)}/mi`:'' },
    { label:'VO2 Max', value: vo2!=null?vo2.toFixed(0):'—', unit:'ml/kg/min', delta: (vo2!=null&&vo2First!=null)?(vo2>vo2First?`up from ${vo2First.toFixed(0)}`:(vo2<vo2First?`down from ${vo2First.toFixed(0)}`:'holding steady')):'', deltaClass:(vo2!=null&&vo2First!=null)?(vo2>vo2First?'up':(vo2<vo2First?'warn':'')):'' },
    { label:'Readiness Today', value: r&&r.score!=null?r.score:'—', unit:'/100', delta: r&&r.level?String(r.level).replace(/_/g,' ').toLowerCase():'—', deltaClass: r&&r.score!=null?(r.score>=70?'up':(r.score<50?'warn':'')):'' },
  ];
  document.getElementById('hero-stats').innerHTML = stats.map(s=>`
    <div class="stat-cell"><div class="stat-label">${s.label}</div><div class="stat-value">${s.value}<span class="stat-unit">${s.unit}</span></div><div class="stat-delta ${s.deltaClass||''}">${s.delta}</div></div>
  `).join('');
});

safe('recommendation panel', function(){
  const rec = DATA.recommendation;
  const notesHtml = rec.notes.map(n=>`<li>${n}</li>`).join('');
  document.getElementById('rec-panel').className = `panel rec-panel tone-${rec.tone}`;
  document.getElementById('rec-panel').innerHTML = `
    <div class="rec-head"><span class="rec-eyebrow">Today's Call</span></div>
    <div class="rec-headline">${rec.headline}</div>
    <ul class="rec-notes">${notesHtml}</ul>
    <div class="rec-disclaimer">Generated from your Garmin metrics — not a substitute for how you actually feel or a coach's judgment.</div>
  `;
});

safe('weekly volume chart', function(){ registerChart('chart-volume', 'Weekly Volume & Training Load', renderVolumeChart, DATA.weekly); });

safe('plan vs actual', function(){
  const plan = DATA.planComparison || [];
  registerChart('chart-plan', 'Plan vs. Actual', renderPlanChart, plan);
  const STATUS_BADGE = { 'on-track':'good', 'behind':'moderate', 'well-behind':'low-warn', 'upcoming':'upcoming', 'no-data':'no-data' };
  const STATUS_LABEL = { 'on-track':'On Track', 'behind':'Behind', 'well-behind':'Well Behind', 'upcoming':'Upcoming', 'no-data':'No Data' };
  document.getElementById('plan-table-body').innerHTML = plan.map(w=>{
    const badgeClass = STATUS_BADGE[w.status] || 'no-data';
    const badgeLabel = STATUS_LABEL[w.status] || w.status;
    const actual = w.actualMi!=null ? `${w.actualMi.toFixed(1)}mi` : '—';
    const adherence = w.adherencePct!=null ? `${w.adherencePct}%` : '—';
    const longRun = w.actualLongRun!=null ? `${w.plannedLongRun.toFixed(1)} → ${w.actualLongRun.toFixed(1)}mi` : `${w.plannedLongRun.toFixed(1)}mi`;
    return `<tr>
      <td class="plan-week-cell">${w.weekLabel}<span class="phase-lbl">${w.phase}</span></td>
      <td>${w.phase}</td>
      <td>${w.plannedMi.toFixed(1)}mi</td>
      <td>${actual}</td>
      <td>${adherence}</td>
      <td>${longRun}</td>
      <td><span class="badge ${badgeClass}">${badgeLabel}</span></td>
    </tr>`;
  }).join('');
});

safe('pace progression chart', function(){
  const runsAsc = [...DATA.runs].sort((a,b)=> new Date(a.date)-new Date(b.date));
  RUNS_ASC = runsAsc;
  registerChart('chart-pace', 'Pace Progression', renderPaceChart, runsAsc);
  const types = [...new Set(DATA.runs.map(r=>r.type))];
  document.getElementById('pace-legend').innerHTML = types.map(t=>`<div class="legend-item"><span class="legend-swatch" style="background:${TYPE_COLORS[t]}"></span>${t}</div>`).join('') + `<div class="legend-item"><span class="legend-swatch" style="background:#e7e9ec"></span>5-run rolling avg</div>`;
});

safe('insights', function(){
  document.getElementById('insights').innerHTML = DATA.insights.map(i=>`
    <div class="insight-card"><span class="insight-icon ${i.type}">${i.icon}</span><div class="insight-text">${i.html}</div></div>
  `).join('');
});

safe('recovery panel', function(){
  const r = DATA.trainingReadiness;
  document.getElementById('readiness-score').textContent = r&&r.score!=null ? r.score : '—';
  const level = r&&r.level ? String(r.level) : null;
  const levelClass = level==='HIGH' ? 'high' : (level==='MODERATE' ? 'moderate' : (level ? 'low-warn' : ''));
  document.getElementById('readiness-badge').outerHTML = `<span class="badge ${levelClass}" id="readiness-badge">${level ? level.replace(/_/g,' ') : '—'}</span>`;
  document.getElementById('training-status-badge').textContent = DATA.trainingStatusFeedback || '—';
  document.getElementById('training-acwr').textContent = DATA.acwr!=null ? `ACWR ${DATA.acwr.toFixed(2)}` : '';
  registerChart('chart-hrv', 'HRV Trend', renderSeriesChart, DATA.hrv, 'hrv', '#5fa8a0');
  const mix = DATA.loadMix;
  if(mix){
    const rows = [
      { name:'Easy', pct:mix.easyPct, min:mix.easyMin, color:'#8b95a1', targetMin:65, targetMax:85 },
      { name:'Moderate', pct:mix.moderatePct, min:mix.moderateMin, color:'#e3a857', targetMin:5, targetMax:20 },
      { name:'Hard', pct:mix.hardPct, min:mix.hardMin, color:'#c1614a', targetMin:5, targetMax:15 },
    ];
    document.getElementById('balance-bars').innerHTML = rows.map(r=>`
      <div class="balance-row">
        <div class="balance-name">${r.name}</div>
        <div class="balance-track">
          <div class="balance-target" style="left:${r.targetMin}%; width:${r.targetMax-r.targetMin}%;"></div>
          <div class="balance-fill" style="width:${Math.min(100,r.pct)}%; background:${r.color};"></div>
        </div>
        <div class="balance-val">${r.pct}%</div>
      </div>`).join('') + `<div class="dial-label" style="margin-top:2px;">dashed = general 80/20-style target range · ${mix.easyMin+mix.moderateMin+mix.hardMin} min over last 4 weeks</div>`;
  } else {
    document.getElementById('balance-bars').innerHTML = "<p class='empty'>Not enough recent runs to compute an effort mix.</p>";
  }
});

safe('fitness trend', function(){
  const rp = DATA.racePredictions;
  function row(label, sec, hi){ return `<div class="predict-row ${hi?'highlight':''}"><span class="predict-label">${label}</span><span>${sec!=null?durStr(sec/60):'—'}</span></div>`; }
  document.getElementById('predict-list').innerHTML = rp
    ? row('5K', rp['5K']) + row('10K', rp['10K']) + row('Half Marathon', rp['Half Marathon'], true) + row('Marathon', rp['Marathon'])
    : "<p class='empty'>Race predictions aren't available from Garmin right now.</p>";
  document.getElementById('score-row').innerHTML = `
    <div class="score-item"><b>${DATA.enduranceScore!=null?DATA.enduranceScore:'—'}</b><span>Endurance Score</span></div>
    <div class="score-item"><b>${DATA.hillScore!=null?DATA.hillScore:'—'}</b><span>Hill Score</span></div>
  `;
  registerChart('chart-vo2', 'VO2 Max Trend', renderSeriesChart, DATA.vo2max, 'vo2', '#e3a857');
  registerChart('chart-efficiency', 'Aerobic Efficiency — Easy & Long Runs', renderSeriesChart, DATA.efficiencyTrend, 'ef', '#5fa8a0');
});

safe('long run splits', function(){
  const longRuns = DATA.longRuns;
  const ids = Object.keys(longRuns);
  if(!ids.length){ document.getElementById('splits-panel').innerHTML = "<p class='empty'>No long runs with lap data in this window yet.</p>"; return; }
  function renderSplit(id){
    const lr = longRuns[id];
    ACTIVE_SPLIT_ID = id;
    document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active', b.dataset.id===id));
    const paced = lr.splits.filter(s=>s.pace>0);
    const avgPace = paced.length ? paced.reduce((s,x)=>s+x.pace,0)/paced.length : null;
    const hrs = lr.splits.filter(s=>s.avgHr).map(s=>s.avgHr);
    const avgHr = hrs.length ? Math.round(hrs.reduce((s,x)=>s+x,0)/hrs.length) : null;
    const totalGain = lr.splits.reduce((s,x)=>s+(x.elevGainFt||0),0);
    const fastest = paced.length ? paced.reduce((min,x)=>x.pace<min.pace?x:min, paced[0]) : null;
    const slowest = paced.length ? paced.reduce((max,x)=>x.pace>max.pace?x:max, paced[0]) : null;
    document.getElementById('split-meta').innerHTML = `
      <div class="split-meta-item"><div class="stat-label">Avg Pace</div><div class="val">${avgPace?paceStr(avgPace)+'/mi':'—'}</div></div>
      <div class="split-meta-item"><div class="stat-label">Avg HR</div><div class="val">${avgHr??'—'} bpm</div></div>
      <div class="split-meta-item"><div class="stat-label">Elev Gain</div><div class="val">${totalGain} ft</div></div>
      <div class="split-meta-item"><div class="stat-label">Fastest / Slowest Mile</div><div class="val">${fastest?paceStr(fastest.pace):'—'} → ${slowest?paceStr(slowest.pace):'—'}</div></div>
    `;
    registerChart('chart-splits', `Long Run Splits — ${lr.label}`, renderSplitsChart, lr.splits, 'splits-legend', lr.elevProfile, lr.mileBased);
  }
  document.getElementById('split-tabs').innerHTML = ids.map(id=>`<button class="tab-btn" data-id="${id}">${longRuns[id].label}</button>`).join('');
  document.querySelectorAll('.tab-btn').forEach(b=>b.addEventListener('click',()=>renderSplit(b.dataset.id)));
  renderSplit(ids[0]);
});

safe('run table', function(){
  let sortKey='date', sortDir=-1, filterType='', filterSearch='';
  const types = [...new Set(DATA.runs.map(r=>r.type))];
  document.getElementById('filter-type').innerHTML += types.map(t=>`<option value="${t}">${t}</option>`).join('');
  function render(){
    let rows = DATA.runs.filter(r => (!filterType||r.type===filterType) && (!filterSearch||r.name.toLowerCase().includes(filterSearch.toLowerCase())));
    rows.sort((a,b)=>{ const av=a[sortKey], bv=b[sortKey]; if(typeof av==='string') return av.localeCompare(bv)*sortDir; return ((av??0)-(bv??0))*sortDir; });
    document.getElementById('table-count').textContent = `${rows.length} of ${DATA.runs.length} runs`;
    document.getElementById('run-table-body').innerHTML = rows.map(r=>`
      <tr class="run-row" data-id="${r.id}" title="View splits, cadence, HR and route">
        <td>${fmtDate(r.date)}</td>
        <td class="name-cell">${r.name}</td>
        <td><span class="type-pill ${r.type.replace(/\s/g,'-')}">${r.type}</span></td>
        <td>${r.distMi.toFixed(2)} mi</td>
        <td>${durStr(r.durMin)}</td>
        <td>${paceStr(r.paceMinMi)}/mi</td>
        <td>${r.avgHr??'—'}</td>
        <td>${r.maxHr??'—'}</td>
        <td>+${r.elevGainFt??0}ft</td>
      </tr>`).join('');
    document.querySelectorAll('thead th').forEach(th=>th.classList.toggle('sorted', th.dataset.key===sortKey));
  }
  document.querySelectorAll('thead th').forEach(th=>{ th.addEventListener('click',()=>{ const key=th.dataset.key; if(sortKey===key){sortDir*=-1;} else {sortKey=key; sortDir = key==='date'?-1:1;} render(); }); });
  document.getElementById('filter-type').addEventListener('change', e=>{ filterType=e.target.value; render(); });
  document.getElementById('filter-search').addEventListener('input', e=>{ filterSearch=e.target.value; render(); });
  document.getElementById('run-table-body').addEventListener('click', e=>{
    const tr = e.target.closest('tr[data-id]');
    if(tr) openRunModal(tr.dataset.id);
  });
  document.getElementById('table-note').textContent = `All ${DATA.runs.length} runs, past ${Math.round((new Date(DATA.meta.syncRangeEnd)-new Date(DATA.meta.syncRangeStart))/86400000/30)} months. Click a column to sort, click a row for details.`;
  render();
});

safe('run detail modal', function(){
  const modal = document.getElementById('run-modal');
  const body = document.getElementById('modal-body');
  function closeModal(){
    modal.style.display='none';
    document.body.style.overflow='';
    Object.keys(ROUTE_MAP_INSTANCES).forEach(id=>{ try{ ROUTE_MAP_INSTANCES[id].remove(); }catch(e){} delete ROUTE_MAP_INSTANCES[id]; });
  }
  document.getElementById('modal-close').addEventListener('click', closeModal);
  modal.addEventListener('click', e=>{ if(e.target===modal) closeModal(); });
  document.addEventListener('keydown', e=>{ if(e.key==='Escape' && modal.style.display!=='none') closeModal(); });

  window.openRunModal = function(id){
    const run = DATA.runs.find(r=>String(r.id)===String(id));
    if(!run) return;
    const detail = (DATA.runDetails && DATA.runDetails[String(id)]) || {};
    const splits = detail.splits || [];
    const route = detail.route || null;
    const elevProfile = detail.elevProfile || null;
    const cap = DATA.meta.detailRunCount;
    // Tempo/interval workouts get one lap per rep+recovery segment, not one per
    // mile — build_splits() figures out which kind of run this is from the lap
    // data itself, so the modal just reads that flag rather than guessing again.
    const mileBased = detail.mileBased !== false;
    const splitsSectionTitle = mileBased ? 'Mile Splits' : 'Lap Splits';
    const splitsFirstCol = mileBased ? 'Mile' : 'Lap';

    const stat = (label, value, unit) => `<div class="modal-stat"><div class="stat-label">${label}</div><div class="stat-value">${value}${unit?`<span class="stat-unit">${unit}</span>`:''}</div></div>`;
    let html = `
      <div class="modal-title">${run.name}</div>
      <div class="modal-sub">${fmtDate(run.date)} · <span class="type-pill ${run.type.replace(/\s/g,'-')}">${run.type}</span>${run.location?` · ${run.location}`:''}</div>
      <div class="modal-stats">
        ${stat('Distance', run.distMi.toFixed(2), 'mi')}
        ${stat('Time', durStr(run.durMin))}
        ${stat('Pace', paceStr(run.paceMinMi), '/mi')}
        ${stat('Avg HR', run.avgHr??'—', run.avgHr?'bpm':'')}
        ${stat('Max HR', run.maxHr??'—', run.maxHr?'bpm':'')}
        ${stat('Cadence', run.avgCadence?Math.round(run.avgCadence):'—', run.avgCadence?'spm':'')}
        ${stat('Elev Gain', '+'+(run.elevGainFt??0), 'ft')}
      </div>
      <div class="modal-section-title">Route</div>
      ${route && route.length>1 ? `<div class="route-map" id="modal-route"></div><div class="route-legend"><span><span style="color:#5fa8a0;">●</span> Start</span><span><span style="color:#c1614a;">●</span> Finish</span></div>` : `<p class="empty">No GPS route available for this run.</p>`}
      <div class="modal-section-title">${splitsSectionTitle}</div>
      ${splits.length ? `
        <div class="chart-box" style="height:220px;"><div id="modal-splits-chart" class="svg-chart"></div></div>
        <div class="legend-row" id="modal-splits-legend"></div>
        <div class="table-scroll" style="margin-top:12px;"><table class="modal-splits-table"><thead><tr><th>${splitsFirstCol}</th><th>Pace</th><th>Avg HR</th><th>Cadence</th><th>Elev+</th></tr></thead><tbody>
        ${splits.map(s=>`<tr><td>${mileBased?s.mile:(s.mile+(s.distMi!=null?` · ${s.distMi.toFixed(2)}mi`:''))}</td><td>${paceStr(s.pace)}/mi</td><td>${s.avgHr??'—'}</td><td>${s.cadence?Math.round(s.cadence):'—'}</td><td>+${s.elevGainFt||0}ft</td></tr>`).join('')}
        </tbody></table></div>`
        : `<p class="empty">Lap-by-lap detail isn't available for this run${cap?` (kept for the most recent ${cap} runs only, to keep the daily sync reasonably fast).`:'.'}</p>`}
    `;
    body.innerHTML = html;
    modal.style.display='flex';
    document.body.style.overflow='hidden';
    if(route && route.length>1) renderRouteMap('modal-route', route);
    if(splits.length) registerChart('modal-splits-chart', `${splitsSectionTitle} — ${run.name}`, renderSplitsChart, splits, 'modal-splits-legend', elevProfile, mileBased);
    attachChartExpandButtons(body);
  };
});

safe('chart expand buttons', function(){ attachChartExpandButtons(document); });
"""

if __name__ == "__main__":
    main()
