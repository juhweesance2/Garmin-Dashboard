import json
import math
import os
import statistics
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

DASHBOARD_EDIT_TOKEN = os.environ.get("DASHBOARD_EDIT_TOKEN", "")  # optional, new in v16 — a fine-grained
                                                        # GitHub token, scoped to ONLY this repo with
                                                        # Contents: Read-and-write and nothing else, that lets
                                                        # the dashboard's own JavaScript commit edits (checked-
                                                        # off workouts, day notes, drag-and-drop reschedules)
                                                        # straight into manual_data.json in this repo. Same
                                                        # client-side-visible-in-page-source trade-off as the
                                                        # CARTO key above, but with higher stakes since it's a
                                                        # write credential, not a read-only maps key — see the
                                                        # v16 setup guide section for the exact scoping steps.
                                                        # When this is empty, the edit controls still render but
                                                        # show "editing isn't set up yet" instead of saving.

MANUAL_DATA_PATH = "manual_data.json"  # v16 — the small hand-edited-from-the-browser data file this script
                                        # reads back in on every sync: manually-checked-off non-Garmin
                                        # workouts, day notes, and drag-and-drop schedule swaps. Written
                                        # directly by the dashboard's own JS via the GitHub API, not by this
                                        # script — this script only ever reads it.

RACE_DATE = date(2026, 11, 8)
RACE_NAME = "Monterey Bay Half Marathon"
# v15 — revised Sep 29, 2026. The original sub-2:14 goal assumed a training
# block that didn't fully happen (see GOAL_REASSESSMENT below): roughly four
# weeks of easy-only running with no tempo/interval structure actually
# executed, surfaced by a Sep 29 tempo diagnostic that broke down after
# 1.25mi continuous. Garmin's own race predictor (2:28:45 as of Sep 29) and
# VO2 max trend (43-44, genuinely up from 41-42) both point to a real but
# more modest fitness gain. GOAL_TIME_SEC is kept as the midpoint of the
# revised range for any single-number use; the low/high pair is what's
# actually shown on the dashboard.
GOAL_TIME_LOW_SEC = 2 * 3600 + 25 * 60     # 2:25:00 — fast end of revised goal
GOAL_TIME_HIGH_SEC = 2 * 3600 + 30 * 60    # 2:30:00 — slow end of revised goal
GOAL_TIME_SEC = (GOAL_TIME_LOW_SEC + GOAL_TIME_HIGH_SEC) // 2
GOAL_PACE_LOW_MIN_MI = 11 + 4 / 60         # 11:04/mi — pace at the fast end
GOAL_PACE_HIGH_MIN_MI = 11 + 27 / 60       # 11:27/mi — pace at the slow end
GOAL_PACE_MIN_MI = (GOAL_PACE_LOW_MIN_MI + GOAL_PACE_HIGH_MIN_MI) / 2
PRIOR_PR_SEC = 2 * 3600 + 18 * 60 + 46     # 2:18:46 — prior PR, unchanged by this revision

MI_PER_M = 1 / 1609.344
FT_PER_M = 3.28084

# =====================================================================
# Goal reassessment — the reasoning behind the Sep 29 goal revision, shown
# verbatim-ish on the dashboard as its own panel so the "why" travels with
# the plan instead of living only in a commit message. Kept as a simple list
# of {label, text} findings, same shape the plan itself used.
# =====================================================================
GOAL_REASSESSMENT = {
    "updated": "2026-09-29",
    "priorGoal": "sub-2:14:00",
    "revisedGoalLabel": "2:25:00–2:30:00",
    "revisedPaceLabel": "11:04–11:27/mi",
    "findings": [
        {"label": "Race predictor", "text": "Garmin's current estimate (Sep 29) is 2:28:45 for the half marathon, based on VO2 max. This is a ceiling estimate from raw aerobic capacity — it doesn't account for race-specific threshold conditioning, which has been largely absent."},
        {"label": "VO2 max trend", "text": "Sitting at 43–44, genuinely up from the 41–42 plateau earlier in the cycle — real aerobic progress has happened."},
        {"label": "Training gap", "text": "Every logged run for roughly four weeks prior to Sep 29 was easy-pace only (11:55–13:22/mi) — no tempo or interval structure actually executed, despite being in the calendar. The aerobic engine improved; the specific skill of sustaining faster paces did not get trained."},
        {"label": "Sep 29 diagnostic", "text": "First real tempo effort in weeks: 1.25mi continuous @ 10:54/mi, HR 158 — clean, controlled, right on target. Then broke down into a run/walk pattern for the remainder (fatigue, not pain). Established current sustainable continuous tempo: ~1.25mi @ 10:50–11:00/mi."},
        {"label": "Revised goal", "text": "Sub-2:14 is not realistic with 40 days left and this training gap. 2:25–2:30 matches the predictor, respects the real tempo-duration data from Sep 29, and leaves room for a strong final few weeks without overreaching."},
        {"label": "Injury status", "text": "The Aug calf/Achilles-adjacent strain is resolved — no pain in the Sep 29 session, fatigue only. The next few tempo/interval sessions are still a gradual rebuild, not a return to full intensity."},
    ],
}

# =====================================================================
# Training plan — the real, 7-day/week Monterey Bay Half plan, rebuilt
# Sep 29, 2026 off the reassessment above. Unlike the old 3-day plan, every
# day of the week has a prescribed session, but Garmin only gets pulled for
# running activities, so each session carries a "trackable" flag: a running
# session (Intervals/Tempo/Long Run/Easy/Race) can be matched against a real
# Garmin run; a non-running session (Rest/Cross Training/Strength) can't and
# is just displayed as planned.
#
# targetMi on a trackable day is a close ESTIMATE built from the session's
# written structure (warmup + main set + between-rep recovery jog, converted
# to miles and rounded to the nearest 0.25mi) — not a value Garmin or the
# plan states directly, except where the plan already gives a plain mileage
# figure (every long run, and race day). Treat targetMi as a target band,
# not a number a real GPS distance needs to match exactly.
# =====================================================================
PLAN_START = date(2026, 9, 28)  # Monday of Week 1 (the week of the Sep 29 reassessment)

TRACKABLE_TYPES = {"Intervals", "Tempo", "Long Run", "Easy", "Race"}

TRAINING_PLAN = [
    {"phase": "Reintroduction", "longRunTargetMi": 8.0, "weeklyTargetMi": 11.25, "sessions": {
        "mon": {"type": "Rest", "title": "(Past) Skipped", "detail": "Intervals not run this week — shifted to accommodate schedule.", "targetMi": 0},
        "tue": {"type": "Tempo", "title": "Tempo diagnostic (done)", "detail": "1mi WU + 1.25mi @ 10:54/mi (clean) + run/walk breakdown. Established current sustainable tempo distance.", "targetMi": 3.25},
        "wed": {"type": "Rest", "title": "Rest", "detail": "Legs recovering from Tuesday's effort — no strength or hard run.", "targetMi": 0},
        "thu": {"type": "Cross Training", "title": "Cross-training", "detail": "25–30min easy bike/pool/elliptical.", "targetMi": 0},
        "fri": {"type": "Strength — Light", "title": "Strength · Light", "detail": "Spanish squats 4×30-40s, decline squats 2×10/leg, glute bridges 2×12/leg, band walks 2×15.", "targetMi": 0},
        "sat": {"type": "Long Run", "title": "Long run", "detail": "8mi easy, no pace push — continuing the reintroduction.", "targetMi": 8.0},
        "sun": {"type": "Cross Training", "title": "Cross-training", "detail": "25min very easy.", "targetMi": 0}}},
    {"phase": "Rebuild", "longRunTargetMi": 9.0, "weeklyTargetMi": 16.25, "sessions": {
        "mon": {"type": "Intervals", "title": "Intervals (conservative)", "detail": "1mi WU @ 12:00-12:30/mi + 4×400m @ 2:15 (9:00/mi), 400m jog + 1mi CD @ 12:00-12:30/mi.", "targetMi": 3.75},
        "tue": {"type": "Strength — Heavy", "title": "Strength · Heavy", "detail": "Trap bar/leg press 3×6-8, Bulgarian splits 3×8/leg, Nordics 3×6, calf raises 2×15.", "targetMi": 0},
        "wed": {"type": "Tempo", "title": "Tempo (building)", "detail": "1mi WU @ 12:00-12:30/mi + 1.5mi continuous @ 10:50/mi + 1mi CD @ 12:00-12:30/mi — up from Sep 29's proven 1.25mi.", "targetMi": 3.5},
        "thu": {"type": "Cross Training", "title": "Cross-training", "detail": "30min easy bike/pool/elliptical.", "targetMi": 0},
        "fri": {"type": "Strength — Light", "title": "Strength · Light", "detail": "Spanish squats 4×30-40s, decline squats 2×10/leg, glute bridges 2×12/leg, band walks 2×15.", "targetMi": 0},
        "sat": {"type": "Long Run", "title": "Long run", "detail": "9mi, last 1mi @ 11:30/mi — gentle pace touch, not a push.", "targetMi": 9.0},
        "sun": {"type": "Cross Training", "title": "Cross-training", "detail": "25min very easy.", "targetMi": 0}}},
    {"phase": "Rebuild", "longRunTargetMi": 11.0, "weeklyTargetMi": 19.0, "sessions": {
        "mon": {"type": "Intervals", "title": "Intervals", "detail": "1mi WU @ 12:00-12:30/mi + 5×400m @ 2:10 (8:40/mi), 400m jog + 1mi CD @ 12:00-12:30/mi.", "targetMi": 4.25},
        "tue": {"type": "Strength — Heavy", "title": "Strength · Heavy", "detail": "Trap bar/leg press 3×6-8, Bulgarian splits 3×8/leg, Nordics 3×6, calf raises 2×15.", "targetMi": 0},
        "wed": {"type": "Tempo", "title": "Tempo (building)", "detail": "1mi WU @ 12:00-12:30/mi + 1.75mi continuous @ 10:45/mi + 1mi CD @ 12:00-12:30/mi.", "targetMi": 3.75},
        "thu": {"type": "Cross Training", "title": "Cross-training", "detail": "30min easy bike/pool/elliptical.", "targetMi": 0},
        "fri": {"type": "Strength — Light", "title": "Strength · Light", "detail": "Spanish squats 4×30-40s, decline squats 2×10/leg, glute bridges 2×12/leg, band walks 2×15.", "targetMi": 0},
        "sat": {"type": "Long Run", "title": "Peak long run", "detail": "11mi, last 2mi @ 11:00-11:15/mi (goal-pace-adjacent) — key checkpoint.", "targetMi": 11.0},
        "sun": {"type": "Cross Training", "title": "Cross-training", "detail": "25min very easy.", "targetMi": 0}}},
    {"phase": "Taper begins", "longRunTargetMi": 8.0, "weeklyTargetMi": 15.75, "sessions": {
        "mon": {"type": "Intervals", "title": "Intervals (reduced)", "detail": "1mi WU @ 12:00-12:30/mi + 4×400m @ 2:10 (8:40/mi) + 1mi CD @ 12:00-12:30/mi.", "targetMi": 3.75},
        "tue": {"type": "Strength — Heavy", "title": "Strength · Moderate", "detail": "Trap bar/leg press 3×6, Bulgarian splits 2×8/leg, calf raises 2×12 — trimming volume.", "targetMi": 0},
        "wed": {"type": "Tempo", "title": "Tempo (goal pace)", "detail": "1mi WU @ 12:00-12:30/mi + 2mi continuous @ 10:40/mi + 1mi CD @ 12:00-12:30/mi.", "targetMi": 4.0},
        "thu": {"type": "Cross Training", "title": "Cross-training", "detail": "25min easy.", "targetMi": 0},
        "fri": {"type": "Strength — Light", "title": "Strength · Light", "detail": "Spanish squats 3×30s, decline squats 2×8/leg, glute bridges 2×10/leg.", "targetMi": 0},
        "sat": {"type": "Long Run", "title": "Long run", "detail": "8mi easy, last 1mi @ goal pace.", "targetMi": 8.0},
        "sun": {"type": "Cross Training", "title": "Cross-training", "detail": "20min very easy.", "targetMi": 0}}},
    {"phase": "Deep taper", "longRunTargetMi": 5.0, "weeklyTargetMi": 11.25, "sessions": {
        "mon": {"type": "Easy", "title": "Sharpeners", "detail": "1mi WU + 4×300m fast + 1mi CD — very light.", "targetMi": 2.75},
        "tue": {"type": "Rest", "title": "Strength · Mobility only", "detail": "Light movement patterns — no loaded lifting.", "targetMi": 0},
        "wed": {"type": "Tempo", "title": "Short tempo", "detail": "1mi WU + 1.5mi @ 10:45/mi + 1mi CD.", "targetMi": 3.5},
        "thu": {"type": "Rest", "title": "Rest", "detail": "Full rest, or 20min very easy.", "targetMi": 0},
        "fri": {"type": "Rest", "title": "Rest", "detail": "Full rest.", "targetMi": 0},
        "sat": {"type": "Easy", "title": "Shakeout", "detail": "5mi easy.", "targetMi": 5.0},
        "sun": {"type": "Rest", "title": "Rest", "detail": "Full rest.", "targetMi": 0}}},
    {"phase": "Race Week", "longRunTargetMi": 3.0, "weeklyTargetMi": 6.75, "sessions": {
        "mon": {"type": "Easy", "title": "Easy run", "detail": "3mi easy @ 12:00/mi.", "targetMi": 3.0},
        "tue": {"type": "Rest", "title": "Rest", "detail": "Full rest.", "targetMi": 0},
        "wed": {"type": "Easy", "title": "Easy + strides", "detail": "2mi easy + 4×20s strides.", "targetMi": 2.25},
        "thu": {"type": "Rest", "title": "Rest or shakeout", "detail": "20min very easy, or full rest.", "targetMi": 0},
        "fri": {"type": "Rest", "title": "Rest", "detail": "Full rest.", "targetMi": 0},
        "sat": {"type": "Easy", "title": "Shakeout + strides", "detail": "1-2mi very easy + 4×20s strides. Lay out race kit.", "targetMi": 1.5},
        "sun": {"type": "Race", "title": "MONTEREY BAY HALF", "detail": "13.1mi · Goal: 2:25–2:30 (11:04–11:27/mi avg).", "targetMi": 13.1}}},
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
# actually recorded, week by week. Every day of the week gets a session (see
# TRAINING_PLAN above), but only "trackable" types (TRACKABLE_TYPES) can be
# matched against a real Garmin run — a Rest/Cross Training/Strength day is
# shown as planned with no attempt to match it to anything.
#
# Race day is trackable (so a finished race shows up like any other run) but
# is deliberately excluded from the week's actualMi/adherence math: folding
# 13.1 race miles into a taper week's "adherence" would make a deliberately
# light week look like a blowout, which is the opposite of useful. It's
# still reported per-day (and the week carries its own raceDayMi/
# raceDayActualMi fields) — just not in the weekly mileage total.
# =====================================================================
DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# =====================================================================
# v16 — manual_data.json: the hand-edited layer written directly by the
# dashboard's own JS (via the GitHub Contents API — see the v16 setup guide
# section and the GitHubStore JS module). This script only ever reads it, on
# every sync, so a browser edit (a checked-off strength session, a day note,
# a drag-and-drop swap) made at any point survives the next nightly rebuild
# instead of being silently overwritten. A missing or malformed file is
# treated as "nothing edited yet" rather than a sync failure — this file not
# existing is the normal, expected state until the first edit is ever made.
# =====================================================================
def load_manual_data():
    default = {"scheduleOverrides": {}, "manualLogs": {}, "notes": {}}
    try:
        with open(MANUAL_DATA_PATH, "r") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        return default
    if not isinstance(raw, dict):
        return default
    return {
        "scheduleOverrides": raw.get("scheduleOverrides") if isinstance(raw.get("scheduleOverrides"), dict) else {},
        "manualLogs": raw.get("manualLogs") if isinstance(raw.get("manualLogs"), dict) else {},
        "notes": raw.get("notes") if isinstance(raw.get("notes"), dict) else {},
    }

def build_plan_comparison(runs_asc, today):
    out = []
    for i, wk in enumerate(TRAINING_PLAN):
        week_start = PLAN_START + timedelta(weeks=i)
        week_end = week_start + timedelta(days=6)
        is_future = week_start > today
        week_runs = [r for r in runs_asc if week_start <= r["date"] <= week_end]

        sessions_out = {}
        race_match_id = None
        race_day_mi = None
        race_day_actual_mi = None
        for offset, day_key in enumerate(DAY_KEYS):
            planned = wk["sessions"].get(day_key)
            if not planned:
                continue
            target_date = week_start + timedelta(days=offset)
            trackable = planned["type"] in TRACKABLE_TYPES
            day_is_future = target_date > today
            match = None
            if trackable and not day_is_future:
                # nearest actual run within a day of the planned date — real
                # schedules slip by a day without it meaning the session was skipped
                candidates = [r for r in runs_asc if abs((r["date"] - target_date).days) <= 1]
                if candidates:
                    match = min(candidates, key=lambda r: abs((r["date"] - target_date).days))
            if not trackable:
                day_status = "not-tracked"
            elif day_is_future:
                day_status = "upcoming"
            elif match:
                day_status = "done"
            else:
                day_status = "missed"
            sessions_out[day_key] = {
                "type": planned["type"], "title": planned["title"], "detail": planned["detail"],
                "targetMi": planned["targetMi"], "date": target_date.isoformat(),
                "trackable": trackable, "dayStatus": day_status,
                "actualMi": round(match["distMi"], 2) if match else None,
                "actualPace": match["paceMinMi"] if match else None,
                "matched": bool(match),
            }
            if planned["type"] == "Race":
                race_day_mi = planned["targetMi"]
                if match:
                    race_match_id = match.get("id")
                    race_day_actual_mi = round(match["distMi"], 2)

        # Exclude the matched race-day run (if any) from the week's own mileage
        # total — see module docstring above.
        mileage_runs = [r for r in week_runs if race_match_id is None or r.get("id") != race_match_id]
        actual_mi = round(sum(r["distMi"] for r in mileage_runs), 1) if (mileage_runs or not is_future) else None
        actual_long = round(max((r["distMi"] for r in mileage_runs), default=0.0), 1) if (mileage_runs or not is_future) else None

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
            "weekStart": week_start.isoformat(), "weekEnd": week_end.isoformat(), "weekLabel": week_start.strftime("%b %-d"),
            "phase": wk["phase"], "plannedMi": weekly_target, "actualMi": actual_mi,
            "adherencePct": adherence_pct, "plannedLongRun": wk["longRunTargetMi"], "actualLongRun": actual_long,
            "raceDayMi": race_day_mi, "raceDayActualMi": race_day_actual_mi,
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
# v11: a fine-grained, TIME-and-DISTANCE-indexed sample stream — the same
# activityDetailMetrics rows parse_elevation_profile reads, additionally
# carrying elapsed time and an instantaneous pace derived from consecutive
# samples' own distance/time deltas (not a dedicated "speed" field, which
# isn't confirmed to exist on every account — deriving it from distance and
# timestamp only depends on fields already confirmed present for the
# elevation trace). Feeds two things: a half-mile-resolution upgrade to a
# mile-based run's splits chart, and a time-elapsed view of a structured
# workout's laps. Like parse_elevation_profile and parse_route, the exact
# field layout isn't confirmed against a real account — this returns None,
# and both features fall back to their v10 behavior, if the shape doesn't
# match or there isn't enough data to work with.
#
# v14 fix: consecutive samples can report almost no distance change over a
# real time gap — not because the runner stopped, but because Garmin's
# distance field updates in coarse, uneven steps (GPS/accelerometer fusion
# noise), so two samples a second or two apart sometimes show a sub-meter
# delta even while genuinely moving. Deriving a pace from that tiny distance
# over a real time gap produces an absurd instantaneous "pace" (anywhere from
# wildly slow to a nonsense triple-digit min/mi spike) that doesn't reflect
# anything real. MIN_DIST_DELTA_M and PACE_CEILING_MIN_MI below require a
# minimum meaningful distance before a pace is derived at all, and reject the
# rare remaining outlier by a plain sanity ceiling — well past even a walked
# recovery break — rather than clamping it to a wrong-but-plausible number. A
# skipped sample just leaves a gap in the fine curve instead of a garbled
# spike; every consumer downstream already treats a missing/falsy pace as
# "no data for this point," not as zero.
# =====================================================================
MIN_DIST_DELTA_M = 3.0
PACE_CEILING_MIN_MI = 30.0

def parse_fine_stream(details, max_points=400):
    if not isinstance(details, dict):
        return None
    descriptors = dig(details, "metricDescriptors")
    rows = dig(details, "activityDetailMetrics")
    if not (isinstance(descriptors, list) and isinstance(rows, list) and descriptors and rows):
        return None
    dist_idx = time_idx = elev_idx = hr_idx = None
    for d in descriptors:
        if not isinstance(d, dict):
            continue
        key = str(d.get("key", "")).lower()
        idx = d.get("metricsIndex")
        if idx is None:
            continue
        if dist_idx is None and "distance" in key:
            dist_idx = idx
        if time_idx is None and "timestamp" in key:
            time_idx = idx
        if elev_idx is None and ("elevation" in key or "altitude" in key):
            elev_idx = idx
        if hr_idx is None and "heartrate" in key:
            hr_idx = idx
    if dist_idx is None or time_idx is None:
        return None  # pace needs both distance and elapsed time — nothing to derive it from otherwise
    known = [i for i in (dist_idx, time_idx, elev_idx, hr_idx) if i is not None]
    need = max(known)
    raw = []
    for row in rows:
        vals = row.get("metrics") if isinstance(row, dict) else None
        if not isinstance(vals, list) or len(vals) <= need:
            continue
        d_m, t_raw = vals[dist_idx], vals[time_idx]
        if not isinstance(d_m, (int, float)) or not isinstance(t_raw, (int, float)):
            continue
        e_ft = m_to_ft(vals[elev_idx]) if elev_idx is not None and isinstance(vals[elev_idx], (int, float)) else None
        hr = vals[hr_idx] if hr_idx is not None and isinstance(vals[hr_idx], (int, float)) else None
        raw.append((t_raw, d_m, e_ft, hr))
    if len(raw) < 8:
        return None
    raw.sort(key=lambda r: r[0])
    t0 = raw[0][0]
    out = []
    prev = None
    for t_raw, d_m, e_ft, hr in raw:
        t = t_raw - t0
        pace = None
        if prev is not None:
            dt, dd = t - prev[0], d_m - prev[1]
            # require a real, meaningful distance delta before deriving an
            # instantaneous pace from it, and reject anything that still comes
            # out absurd — see the v14 note above.
            if dt > 0 and dd > MIN_DIST_DELTA_M:
                candidate = (dt / 60) / (dd * MI_PER_M)
                if candidate <= PACE_CEILING_MIN_MI:
                    pace = candidate
        out.append({"t": t, "d": d_m, "elevFt": e_ft, "hr": hr, "paceMinMi": pace})
        prev = (t, d_m)
    if len(out) > max_points * 2:
        step = len(out) / (max_points * 2)
        out = [out[int(i * step)] for i in range(max_points * 2)]
    return out if len(out) >= 8 else None

PACE_BIN_MI = 0.5  # half-mile — the fixed resolution of the v11 upgraded splits chart

def _finalize_pace_profile(pts, bin_mi=PACE_BIN_MI):
    # Bins the fine stream above into fixed-width buckets by distance, same
    # approach as _finalize_elevation_profile, but averaging pace/HR (and a
    # local elevation-gain figure) per bucket instead of just altitude —
    # giving the splits chart real sub-mile detail instead of one point per
    # whole Garmin autolap, from data that's already being fetched for the
    # elevation trace (no extra API call).
    pts = [p for p in pts if isinstance(p.get("d"), (int, float))]
    if len(pts) < 8:
        return None
    pts.sort(key=lambda p: p["d"])
    total_m = pts[-1]["d"]
    if total_m <= 0:
        return None
    total_mi = total_m * MI_PER_M
    if total_mi < bin_mi * 1.5:
        return None  # too short a run for sub-mile bins to add anything real
    n_bins = max(1, math.ceil(total_mi / bin_mi))
    buckets = [[] for _ in range(n_bins)]
    for p in pts:
        idx = min(n_bins - 1, int((p["d"] * MI_PER_M) / bin_mi))
        buckets[idx].append(p)
    out = []
    for i, b in enumerate(buckets):
        paces = [p["paceMinMi"] for p in b if p.get("paceMinMi")]
        if len(b) < 2 or not paces:
            continue
        hrs = [p["hr"] for p in b if p.get("hr")]
        elevs = [p["elevFt"] for p in b if isinstance(p.get("elevFt"), (int, float))]
        gain = sum(max(0.0, elevs[j] - elevs[j - 1]) for j in range(1, len(elevs)))
        end_mi = min((i + 1) * bin_mi, total_mi)
        out.append({
            "mile": f"{end_mi:g}",
            "distMi": round(min(bin_mi, total_mi - i * bin_mi), 3),
            "pace": round(sum(paces) / len(paces), 2),
            "avgHr": round(sum(hrs) / len(hrs)) if hrs else None,
            "maxHr": None,
            "elevGainFt": round(gain) if elevs else 0,
            "cadence": None,
        })
    # a genuinely short leftover final bin (well under a full bucket width) is
    # the same "unfinished mile" situation v9 already trims for whole-mile
    # splits — drop it here too, scaled to the finer bin width.
    if len(out) > 1 and out[-1]["distMi"] < bin_mi * 0.6:
        out.pop()
    return out if len(out) >= 4 else None

def label_interval_laps(out):
    # Classifies a structured workout's already-built lap list as Warm Up /
    # Interval N / Recovery N / Cool Down, using the DATA (each lap's own
    # distance and pace) rather than assuming a fixed alternating pattern —
    # a workout that doesn't strictly alternate (a ladder, back-to-back reps
    # with no jog between) still classifies sensibly. A first or last lap is
    # only called Warm Up / Cool Down when it's meaningfully longer than the
    # interior reps (a real warm-up/cool-down mile, not just the first rep);
    # the interior laps split into Interval vs. Recovery by comparing each
    # one's pace against the workout's own median interior pace, so it's
    # calibrated to how hard THIS workout actually was, not a fixed number.
    n = len(out)
    if n == 0:
        return out

    def dist_of(s):
        return s["distMi"] if s["distMi"] is not None else 0.0

    # fold a tiny trailing sliver (a GPS-stop artifact, not a real segment)
    # into the lap before it rather than giving it its own nonsensical row.
    if n >= 2 and dist_of(out[-1]) < 0.12 and dist_of(out[-1]) < dist_of(out[-2]) * 0.3:
        last = out.pop()
        prev = out[-1]
        merged_dist = dist_of(prev) + dist_of(last)
        merged_dur = (prev.get("durationSec") or 0) + (last.get("durationSec") or 0)
        prev["distMi"] = round(merged_dist, 2)
        prev["durationSec"] = merged_dur
        if merged_dist > 0 and merged_dur > 0:
            prev["pace"] = round((merged_dur / 60) / merged_dist, 2)
        prev["elevGainFt"] = (prev.get("elevGainFt") or 0) + (last.get("elevGainFt") or 0)
        n = len(out)

    dists = [dist_of(s) for s in out]
    interior = dists[1:-1] if n > 2 else []
    interior_median = statistics.median(interior) if interior else 0
    has_warmup = n > 2 and interior_median > 0 and dists[0] >= max(0.5, interior_median * 1.6)
    has_cooldown = n > 2 and interior_median > 0 and dists[-1] >= max(0.5, interior_median * 1.6)
    if has_warmup:
        out[0]["mile"] = "Warm Up"
    if has_cooldown:
        out[-1]["mile"] = "Cool Down"

    body_lo = 1 if has_warmup else 0
    body_hi = (n - 2) if has_cooldown else (n - 1)
    body_idx = [i for i in range(body_lo, body_hi + 1) if out[i]["pace"]]
    if body_idx:
        threshold = statistics.median([out[i]["pace"] for i in body_idx])
        interval_n = recovery_n = 0
        for i in body_idx:
            if out[i]["pace"] <= threshold:
                interval_n += 1
                out[i]["mile"] = f"Interval {interval_n}"
            else:
                recovery_n += 1
                out[i]["mile"] = f"Recovery {recovery_n}"
    return out

def build_interval_timeline(labeled_out, fine_pts):
    # Turns a structured workout's labeled laps + the fine time/pace stream
    # above into the data a time-elapsed chart needs: a "band" per segment
    # (its label, its real start/end in elapsed time, its average pace/HR)
    # sized by how long it actually lasted, plus the fine pace/HR curve
    # itself so the chart can show a rep's shape, not just its average.
    # Caveat worth knowing: band boundaries come from the lap-splits endpoint
    # and the fine curve comes from the activity-details endpoint — two
    # different Garmin calls lined up by elapsed time, so on some accounts
    # they may drift slightly out of sync at a boundary rather than being
    # pixel-perfect.
    if not fine_pts:
        return None
    valid = [p for p in fine_pts if isinstance(p.get("t"), (int, float)) and p.get("paceMinMi")]
    if len(valid) < 8:
        return None
    valid.sort(key=lambda p: p["t"])
    bands = []
    t_cursor = 0.0
    for s in labeled_out:
        dur = s.get("durationSec") or 0
        if dur <= 0:
            continue
        bands.append({
            "label": s["mile"],
            "start": round(t_cursor, 1),
            "end": round(t_cursor + dur, 1),
            "durSec": round(dur, 1),
            "pace": s["pace"],
            "avgHr": s.get("avgHr"),
        })
        t_cursor += dur
    if not bands:
        return None
    fine = [{"t": round(p["t"], 1), "pace": round(p["paceMinMi"], 2), "hr": round(p["hr"]) if p.get("hr") else None} for p in valid]
    return {"totalTime": round(t_cursor, 1), "bands": bands, "fine": fine}

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
        # v14 fix: a lap's pace can genuinely be missing (None) now that
        # build_splits no longer fakes a 0 for it — filter those out before
        # averaging, and guard against either half coming up empty, rather
        # than letting a None slip into the sum and raise.
        front_paces = [s["pace"] for s in front if s.get("pace")]
        back_paces = [s["pace"] for s in back if s.get("pace")]
        front_hrs = [s["avgHr"] for s in front if s["avgHr"]]
        back_hrs = [s["avgHr"] for s in back if s["avgHr"]]
        if not front_paces or not back_paces or not front_hrs or not back_hrs:
            continue
        front_pace = sum(front_paces) / len(front_paces)
        back_pace = sum(back_paces) / len(back_paces)
        if not front_pace:
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
                    # A mile-based split's "mile" field is a bare number ("3") and
                    # needs the "Mile" prefix; a structured workout's is already a
                    # self-describing label ("Interval 3") and reads fine alone.
                    mile_based_flag = splits.get("mileBased", True)
                    label = f"Mile {steepest['mile']}" if mile_based_flag else str(steepest['mile'])
                    extra = f" {label} alone carried {steepest['elevGainFt']}ft of that gain and slowed to {fmt_pace_mmss(steepest['pace'])}/mi."
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
    manual_data = load_manual_data()  # v16 — checked-off workouts, notes, schedule swaps from the browser

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
    def build_splits(activity_id, fine_pts=None):
        splits_raw = safe_call(client.get_activity_splits, activity_id)
        laps = dig(splits_raw, "lapDTOs", default=[]) or []
        out = []
        for i, lap in enumerate(laps, start=1):
            lap_dist_m = lap.get("distance")
            lap_dist_mi = m_to_mi(lap_dist_m) if lap_dist_m is not None else None
            out.append({
                "mile": i,
                "distMi": round(lap_dist_mi, 2) if lap_dist_mi is not None else None,
                # v14 fix: this used to fall back to 0 when a lap's pace couldn't be
                # computed (missing/zero distance or duration) — 0 min/mi isn't "no
                # data," it's a nonsense pace that then plotted and averaged as if it
                # were real (a lap line dropping to the axis floor, a tooltip reading
                # "0:00/mi", classification thresholds getting skewed by a fake
                # fastest-ever lap). Leaving it as None instead means every consumer
                # (the chart, the table, the interval classifier) treats it as a genuine
                # gap — a "—" in the table, a break in the line, excluded from any
                # average — rather than a garbled number.
                "pace": pace_min_per_mile(lap_dist_m, lap.get("duration")),
                "avgHr": lap.get("averageHR"),
                "maxHr": lap.get("maxHR"),
                "elevGainFt": round(m_to_ft(lap.get("elevationGain"))) if lap.get("elevationGain") is not None else 0,
                "cadence": lap.get("averageRunningCadenceInStepsPerMinute"),
                "durationSec": lap.get("duration"),
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
        # otherwise it's a structured workout.
        checkable = [s["distMi"] for s in out[:-1] if s["distMi"] is not None] if len(out) > 1 else []
        if not checkable:
            checkable = [s["distMi"] for s in out if s["distMi"] is not None]
        mile_based = bool(checkable) and (sum(1 for d in checkable if 0.85 <= d <= 1.15) / len(checkable)) >= 0.6

        time_series = None
        if mile_based:
            # The trailing lap is usually whatever partial distance was left when
            # the run ended, not a finished mile (see note above) — a few seconds
            # of GPS wobble over 0.02mi can compute as a 24:00/mi "mile" that
            # skews the whole chart's scale. Drop it, down to 1 lap minimum.
            while len(out) > 1 and out[-1]["distMi"] is not None and out[-1]["distMi"] < 0.9:
                out.pop()
            # v11: upgrade from one point per whole mile to one point per half
            # mile when the fine-grained stream (same one the elevation trace
            # reads) is available and covers this run — real extra terrain/pace
            # detail, not just more dots. Falls back to the whole-mile array
            # above otherwise.
            if fine_pts:
                upgraded = _finalize_pace_profile(fine_pts)
                if upgraded:
                    out = upgraded
        else:
            # For a structured workout, every lap (work rep AND recovery jog) is
            # real, correctly-accounted-for distance — nothing here is a
            # "leftover partial mile," so nothing gets dropped, just labeled by
            # what it actually was (see label_interval_laps). When the fine
            # stream is also available, additionally build the time-elapsed
            # view (see build_interval_timeline) — each segment's width is how
            # long it actually lasted, with real within-segment pace/HR detail.
            out = label_interval_laps(out)
            if fine_pts:
                time_series = build_interval_timeline(out, fine_pts)
        return out, mile_based, time_series

    detail_candidates = runs_desc[:DETAIL_RUN_COUNT]
    run_details = {}
    for i, r in enumerate(detail_candidates):
        route = None
        elev_profile = None
        fine_pts = None
        if i < ROUTE_RUN_COUNT:
            details_raw = safe_method_call(client, "get_activity_details", r["id"])
            route = parse_route(details_raw)
            elev_profile = parse_elevation_profile(details_raw)
            fine_pts = parse_fine_stream(details_raw)
        splits, mile_based, time_series = build_splits(r["id"], fine_pts)
        if splits or route:
            run_details[str(r["id"])] = {"splits": splits, "route": route, "elevProfile": elev_profile, "mileBased": mile_based, "timeSeries": time_series}

    # ---- Long run splits panel (mile-by-mile, for the N most recent long runs) —
    # reuses the detail fetch above when the long run falls inside that window.
    long_run_candidates = [r for r in runs_desc if r["type"] == "Long Run"][:LONG_RUN_COUNT]
    long_runs_data = {}
    long_runs_ordered = []  # [(run, splits_list)], most recent first — used by insight detectors
    for r in long_run_candidates:
        rid = str(r["id"])
        cached = run_details.get(rid)
        splits, mile_based, time_series = (cached["splits"], cached["mileBased"], cached["timeSeries"]) if cached else build_splits(r["id"])
        if splits:
            long_runs_data[rid] = {
                "label": f"{r['dateLabel']} — {r['name']} ({r['distMi']:.1f}mi)",
                "splits": splits,
                "mileBased": mile_based,
                "elevProfile": cached.get("elevProfile") if cached else None,
                "timeSeries": time_series,
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
            "githubWriteToken": DASHBOARD_EDIT_TOKEN,
            "goalLabel": GOAL_REASSESSMENT["revisedGoalLabel"],
            "goalPaceLabel": GOAL_REASSESSMENT["revisedPaceLabel"],
            "priorGoalLabel": GOAL_REASSESSMENT["priorGoal"],
            "priorPrSec": PRIOR_PR_SEC,
            "planStart": PLAN_START.isoformat(),
        },
        "goalReassessment": GOAL_REASSESSMENT,
        "recommendation": recommendation,
        "runs": [{k: v for k, v in r.items() if k not in ("distance_m", "duration_s")} for r in runs_desc],
        "weekly": weeks,
        "longRuns": long_runs_data,
        "runDetails": run_details,
        "planComparison": plan_comparison,
        # v16 — the raw, un-swapped plan (exactly as TRAINING_PLAN defines it) plus whatever's been
        # hand-edited from the browser. The client recomputes each day's trackable/done/missed status
        # live from these (same algorithm as build_plan_comparison above, kept in JS so a same-session
        # edit shows immediately without waiting for tomorrow's sync) — planComparison's own per-day
        # sessions above are left exactly as before (pre-v16) and are NOT consulted for this panel.
        "rawTrainingPlan": [{"phase": wk["phase"], "sessions": wk["sessions"]} for wk in TRAINING_PLAN],
        "trackableTypes": sorted(TRACKABLE_TYPES),
        "manualData": manual_data,
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
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Titillium+Web:wght@600;700;900&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
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
      <div class="header-actions">
        <div class="sync-badge"><span class="sync-dot"></span><span id="sync-text">Synced from Garmin —</span></div>
        <button type="button" class="theme-toggle" id="theme-toggle" aria-label="Toggle light/dark theme" title="Toggle light/dark theme">&#9789;</button>
      </div>
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

  <section id="goal-reassessment-section" style="display:none;">
    <div class="section-head">
      <div class="section-title">Goal Reassessment</div>
      <div class="section-note" id="goal-updated-note"></div>
    </div>
    <div class="panel goal-panel" id="goal-panel"></div>
  </section>

  <section id="nav-today">
    <div class="section-head">
      <div class="section-title">This Week's Plan</div>
      <div class="section-note">Every day of the current training week, matched against what Garmin actually recorded. Drag a day card onto another to swap sessions, tap a card for instructions and notes.</div>
    </div>
    <div class="panel" id="this-week-panel"></div>
  </section>

  <section id="nav-training">
    <div class="section-head">
      <div class="section-title"><span class="section-index">01</span> Plan vs. Actual</div>
      <div class="section-note" id="plan-note">Weekly mileage against your training plan.</div>
    </div>
    <div class="panel">
      <div class="chart-box tall"><div id="chart-plan" class="svg-chart"></div></div>
      <div class="legend-row">
        <div class="legend-item"><span class="legend-swatch" style="background:var(--text-dim)"></span>Planned miles</div>
        <div class="legend-item"><span class="legend-swatch" style="background:var(--amber)"></span>Actual miles</div>
      </div>
      <div class="phase-legend-row" id="phase-legend"></div>
    </div>
    <div class="panel plan-table-wrap" style="margin-top:16px;">
      <details class="plan-expand" open>
      <summary>Full plan, all weeks</summary>
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
      </details>
    </div>
  </section>

  <section>
    <div class="section-head">
      <div class="section-title"><span class="section-index">02</span> Weekly Volume &amp; Training Load</div>
      <div class="section-note">Mileage by week against your long run distance and weekly run count.</div>
    </div>
    <div class="panel">
      <div class="chart-box tall"><div id="chart-volume" class="svg-chart"></div></div>
      <div class="legend-row">
        <div class="legend-item"><span class="legend-swatch" style="background:var(--amber)"></span>Weekly miles</div>
        <div class="legend-item"><span class="legend-swatch" style="background:var(--blue)"></span>Long run distance</div>
        <div class="legend-item"><span class="legend-swatch" style="background:var(--teal); border-radius:50%;"></span>Runs per week</div>
        <div class="legend-item">★ Peak week, all-time</div>
      </div>
    </div>
  </section>

  <section>
    <div class="section-head">
      <div class="section-title"><span class="section-index">03</span> Pace Progression</div>
      <div class="section-note">Every run's average pace, colored by workout type, with a 5-run rolling average.</div>
    </div>
    <div class="panel">
      <div class="chart-box tall"><div id="chart-pace" class="svg-chart"></div></div>
      <div class="legend-row" id="pace-legend"></div>
    </div>
  </section>

  <section>
    <div class="section-head">
      <div class="section-title"><span class="section-index">04</span> What The Data Is Saying</div>
      <div class="section-note">Rule-based pattern detection — not a live model call — so it runs free on every sync.</div>
    </div>
    <div id="insights" style="display:flex; flex-direction:column; gap:10px;"></div>
  </section>

  <section id="nav-recovery">
    <div class="section-head">
      <div class="section-title"><span class="section-index">05</span> Recovery &amp; Readiness</div>
      <div class="section-note">Today's readiness, HRV trend, and how training effort has split across intensity bands.</div>
    </div>
    <div class="panel-triple">
      <div class="panel">
        <div class="stat-label"><span class="label-with-tip">Training Readiness — Today<button type="button" class="info-tip-btn" data-info-key="readiness">i</button></span></div>
        <div class="readiness-ring-row">
          <svg class="readiness-ring" viewBox="0 0 110 110" width="104" height="104">
            <circle cx="55" cy="55" r="46" fill="none" class="ring-track" stroke-width="10"/>
            <circle cx="55" cy="55" r="46" fill="none" class="ring-fill" id="readiness-ring-fill" stroke-width="10" stroke-linecap="round" transform="rotate(-90 55 55)"/>
            <text x="55" y="51" text-anchor="middle" class="ring-number" id="readiness-score">—</text>
            <text x="55" y="68" text-anchor="middle" class="ring-caption">/ 100</text>
          </svg>
          <div>
            <span class="badge" id="readiness-badge">—</span>
          </div>
        </div>
        <div style="margin-top:18px; padding-top:16px; border-top:1px solid var(--border-soft);">
          <div class="stat-label">Training Status</div>
          <div style="display:flex; align-items:baseline; gap:8px; margin-top:8px; flex-wrap:wrap;">
            <span class="badge good" id="training-status-badge">—</span>
            <span class="dial-label" id="training-acwr"></span>
            <button type="button" class="info-tip-btn" data-info-key="acwr">i</button>
          </div>
        </div>
      </div>
      <div class="panel">
        <div class="stat-label"><span class="label-with-tip">HRV Trend<button type="button" class="info-tip-btn" data-info-key="hrv">i</button></span></div>
        <div class="chart-box" style="height:190px; margin-top:10px;"><div id="chart-hrv" class="svg-chart"></div></div>
      </div>
      <div class="panel">
        <div class="stat-label"><span class="label-with-tip">Effort Mix — Last 4 Weeks<button type="button" class="info-tip-btn" data-info-key="effortmix">i</button></span></div>
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
      <div class="stat-label"><span class="label-with-tip">Aerobic Efficiency — Easy &amp; Long Runs<button type="button" class="info-tip-btn" data-info-key="efficiency">i</button></span></div>
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

  <section id="nav-runs">
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
<nav class="mobile-tabbar" id="mobile-tabbar">
  <button type="button" data-target="nav-today">Today</button>
  <button type="button" data-target="nav-training">Training</button>
  <button type="button" data-target="nav-recovery">Recovery</button>
  <button type="button" data-target="nav-runs">Runs</button>
</nav>
<div id="run-modal" class="modal-overlay" style="display:none;">
  <div class="modal-panel">
    <button class="modal-close" id="modal-close" aria-label="Close">&times;</button>
    <div class="modal-nav-row">
      <button type="button" class="modal-nav-btn" id="modal-prev">&larr; Prev</button>
      <button type="button" class="modal-nav-btn" id="modal-next">Next &rarr;</button>
    </div>
    <div class="modal-ministrip" id="modal-ministrip"></div>
    <div id="modal-body"></div>
  </div>
</div>
<div id="day-modal" class="modal-overlay" style="display:none;">
  <div class="modal-panel day-modal-panel">
    <button class="modal-close" id="day-modal-close" aria-label="Close">&times;</button>
    <div id="day-modal-body"></div>
  </div>
</div>
<div id="chart-zoom-modal" class="modal-overlay chart-zoom-overlay" style="display:none;">
  <div class="chart-zoom-panel">
    <div class="chart-zoom-toolbar">
      <span class="chart-zoom-title" id="chart-zoom-title"></span>
      <button type="button" id="chart-zoom-close" class="chart-zoom-close-btn" aria-label="Close">&times;</button>
    </div>
    <div id="chart-zoom-toolbar-slot"></div>
    <div class="chart-box" id="chart-zoom-box"></div>
    <div class="chart-zoom-hint">Scroll/pinch to zoom · drag to pan · double-click or double-tap to reset</div>
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
  --bg: #0E141C; --bg-panel: #182230; --bg-raised: #1F2C3D; --bg-inset: #0B1017;
  --border: rgba(230,237,245,0.10); --border-soft: rgba(230,237,245,0.06);
  --text: #E6EDF5; --text-muted: #7E8EA3; --text-dim: #57636F;
  --amber: #00B4E0; --amber-dim: #0B3245;
  --teal: #2FD480; --teal-dim: #123B2C;
  --clay: #FF5A64; --clay-dim: #401A1C;
  --blue: #45D6B0; --blue-dim: #123832;
  --warn: #FFB020; --warn-dim: #3D2E0E;
  --font-display: 'Titillium Web', 'Arial Narrow', sans-serif;
  --font-body: 'IBM Plex Sans', -apple-system, 'Segoe UI', system-ui, sans-serif;
  --font-mono: 'IBM Plex Mono', 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
}
/* v15 — light theme. Same token names, a parallel light palette, so every
   component below (all written against var(--bg) etc., never a literal hex)
   repaints automatically. Toggled by data-theme="light" on <html>, set by
   the theme button in the header and remembered per-browser. */
[data-theme="light"]{
  --bg: #F3F5F8; --bg-panel: #FFFFFF; --bg-raised: #EAEFF4; --bg-inset: #E3E9EF;
  --border: rgba(14,20,28,0.12); --border-soft: rgba(14,20,28,0.07);
  --text: #121922; --text-muted: #51606F; --text-dim: #8996A3;
  --amber: #0091B8; --amber-dim: #D8EFF6;
  --teal: #1C9A62; --teal-dim: #DCF4E8;
  --clay: #D43B45; --clay-dim: #FBE1E2;
  --blue: #1E9E80; --blue-dim: #DCF3EC;
  --warn: #B3780E; --warn-dim: #FBEBD2;
}
[data-theme="light"] ::selection{ background:var(--amber); color:#FFFFFF; }
[data-theme="light"] .route-map.osm-fallback .leaflet-tile-pane{ filter:none; }
[data-theme="light"] .console-header{ background: radial-gradient(ellipse 900px 300px at 15% -20%, rgba(0,145,184,0.08), transparent), var(--bg); }
[data-theme="light"] #chart-tooltip{ box-shadow:0 8px 20px rgba(20,30,40,0.14); }
[data-theme="light"] .modal-overlay{ background:rgba(20,28,36,0.45); }
*{ box-sizing:border-box; margin:0; padding:0; }
body{ background:var(--bg); color:var(--text); font-family:var(--font-body); line-height:1.5; -webkit-font-smoothing:antialiased; padding:0 0 64px; }
::selection{ background:var(--amber); color:#0E141C; }
.wrap{ max-width:1180px; margin:0 auto; padding:0 24px; }
.console-header{ border-bottom:1px solid var(--border); background: radial-gradient(ellipse 900px 300px at 15% -20%, rgba(0,180,224,0.12), transparent), var(--bg); padding:28px 0 22px; }
.header-row{ display:flex; justify-content:space-between; align-items:flex-start; gap:24px; flex-wrap:wrap; }
.brand-eyebrow{ font-family:var(--font-mono); font-size:11px; letter-spacing:0.14em; color:var(--amber); text-transform:uppercase; display:block; margin-bottom:6px; }
h1{ font-family:var(--font-display); font-weight:900; font-size:clamp(20px,5.5vw,28px); letter-spacing:0.01em; text-transform:uppercase; text-wrap:balance; }
.sync-badge{ font-family:var(--font-mono); font-size:12px; color:var(--text-muted); display:flex; align-items:center; gap:8px; padding:8px 12px; border:1px solid var(--border); border-radius:6px; background:var(--bg-panel); white-space:nowrap; }
.sync-dot{ width:7px; height:7px; border-radius:50%; background:var(--teal); box-shadow:0 0 8px var(--teal); flex-shrink:0; }
.countdown-strip{ margin-top:22px; display:flex; border:1px solid var(--border); border-radius:6px; overflow:hidden; background:var(--bg-panel); flex-wrap:wrap; }
.countdown-cell{ flex:1; padding:16px 20px; border-right:1px solid var(--border-soft); display:flex; flex-direction:column; gap:4px; min-width:130px; }
.countdown-cell:last-child{ border-right:none; }
.cc-label{ font-size:11px; text-transform:uppercase; letter-spacing:0.08em; color:var(--text-dim); font-family:var(--font-mono); }
.cc-value{ font-family:var(--font-mono); font-size:clamp(17px,4.5vw,24px); font-weight:600; color:var(--text); }
.cc-value.accent{ color:var(--amber); }
.cc-sub{ font-size:12px; color:var(--text-muted); }
.boot-errors{ margin-top:16px; padding:12px 16px; border:1px solid var(--clay); background:var(--clay-dim); border-radius:4px; font-family:var(--font-mono); font-size:12px; color:var(--clay); }
.stat-strip{ display:grid; grid-template-columns:repeat(5,1fr); gap:1px; background:var(--border); border:1px solid var(--border); border-radius:6px; overflow:hidden; margin-top:28px; }
.stat-cell{ background:var(--bg-panel); padding:18px 18px 16px; }
.stat-label{ font-size:11px; text-transform:uppercase; letter-spacing:0.07em; color:var(--text-dim); font-family:var(--font-mono); margin-bottom:8px; }
.stat-value{ font-family:var(--font-mono); font-size:clamp(19px,4.4vw,26px); font-weight:600; font-variant-numeric:tabular-nums; }
.stat-unit{ font-size:13px; color:var(--text-muted); font-weight:400; margin-left:3px; }
.stat-delta{ font-size:12px; margin-top:5px; color:var(--text-muted); }
.stat-delta.up{ color:var(--teal); }
.stat-delta.warn{ color:var(--clay); }
section{ margin-top:44px; }
.section-head{ display:flex; justify-content:space-between; align-items:baseline; margin-bottom:16px; gap:16px; flex-wrap:wrap; }
.section-title{ font-family:var(--font-display); font-weight:700; font-size:clamp(15px,3.6vw,18px); text-transform:uppercase; letter-spacing:0.04em; display:flex; align-items:center; gap:10px; }
.section-index{ font-family:var(--font-mono); color:var(--amber); font-size:13px; }
.section-note{ font-size:13px; color:var(--text-muted); max-width:440px; text-align:right; }
.panel{ background:var(--bg-panel); border:1px solid var(--border); border-radius:6px; padding:20px; }
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
.svg-chart{ width:100%; height:100%; cursor:default; touch-action:none; }
.svg-chart.zoomed{ cursor:grab; }
.svg-chart.panning{ cursor:grabbing; }
.svg-chart svg{ width:100%; height:100%; display:block; overflow:visible; }
.svg-chart text{ font-family:var(--font-mono); fill:var(--text-dim); font-size:10px; }
.svg-chart .axis-line{ stroke:var(--border); stroke-width:1; }
.svg-chart .grid-line{ stroke:var(--border-soft); stroke-width:1; }
.svg-chart .data-point{ cursor:pointer; }
.svg-chart .data-point:hover{ filter:brightness(1.3); }
.chart-toolbar{ display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; gap:10px; flex-wrap:wrap; }
.zoom-range{ font-family:var(--font-mono); font-size:11px; color:var(--text-dim); }
.zoom-reset-btn{ font-family:var(--font-mono); font-size:11px; color:var(--text-dim); background:var(--bg-raised); border:1px solid var(--border); border-radius:6px; padding:4px 9px; cursor:pointer; opacity:0; pointer-events:none; transition:opacity .15s, color .15s, border-color .15s; }
.zoom-reset-btn.show{ opacity:1; pointer-events:auto; }
.zoom-reset-btn:hover{ color:var(--text); border-color:var(--text-dim); }
#chart-tooltip{ position:fixed; pointer-events:none; z-index:999; background:var(--bg-raised); border:1px solid var(--border); border-radius:6px; padding:8px 11px; font-family:var(--font-mono); font-size:12px; color:var(--text); box-shadow:0 8px 20px rgba(0,0,0,0.4); display:none; max-width:220px; line-height:1.5; }
#chart-tooltip .tt-title{ font-family:var(--font-body); font-weight:600; color:var(--text); margin-bottom:3px; font-size:12.5px; }
#chart-tooltip .tt-row{ color:var(--text-muted); }
#chart-tooltip .tt-row b{ color:var(--text); font-weight:500; }
.legend-row{ display:flex; gap:18px; flex-wrap:wrap; margin-top:14px; font-size:12px; color:var(--text-muted); }
.legend-item{ display:flex; align-items:center; gap:6px; }
.legend-swatch{ width:10px; height:10px; border-radius:2px; }
.chart-caption{ font-family:var(--font-mono); font-size:0.64rem; color:var(--text-dim); margin-top:6px; text-align:center; }
.insight-card{ background:var(--bg-raised); border:1px solid var(--border-soft); border-radius:6px; padding:16px 18px; display:flex; gap:12px; align-items:flex-start; }
.insight-icon{ font-family:var(--font-mono); font-size:11px; padding:3px 7px; border-radius:4px; flex-shrink:0; margin-top:2px; white-space:nowrap; }
.insight-icon.good{ background:var(--teal-dim); color:var(--teal); }
.insight-icon.watch{ background:var(--warn-dim); color:var(--warn); }
.insight-icon.flag{ background:var(--clay-dim); color:var(--clay); }
.insight-text{ font-size:13.5px; color:var(--text); line-height:1.55; }
.insight-text b{ color:var(--text); font-weight:600; }
.readiness-ring-row{ display:flex; gap:20px; align-items:center; margin-top:10px; }
.readiness-ring .ring-track{ stroke:var(--border-soft); }
.readiness-ring .ring-fill{ stroke:var(--amber); transition:stroke-dasharray .3s ease; }
.readiness-ring .ring-number{ font-family:var(--font-mono); font-size:22px; font-weight:600; fill:var(--text); }
.readiness-ring .ring-caption{ font-family:var(--font-body); font-size:9px; fill:var(--text-dim); text-transform:uppercase; letter-spacing:0.06em; }
.dial-row{ display:flex; gap:22px; align-items:center; }
.dial-label{ font-size:12px; color:var(--text-muted); margin-top:2px; }
.badge{ display:inline-block; font-family:var(--font-display); font-weight:700; font-size:10.5px; padding:3px 9px; border-radius:3px; text-transform:uppercase; letter-spacing:0.05em; }
.badge.high, .badge.good{ background:var(--teal-dim); color:var(--teal); }
.badge.moderate{ background:var(--warn-dim); color:var(--warn); }
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
.type-pill{ font-family:var(--font-display); font-weight:700; font-size:10.5px; text-transform:uppercase; letter-spacing:0.02em; padding:2px 8px; border-radius:3px; display:inline-block; }
.type-pill.Long-Run{ background:var(--blue-dim); color:var(--blue); }
.type-pill.Easy-Run{ background:var(--bg-inset); color:var(--text-muted); }
.type-pill.Tempo{ background:rgba(255,176,32,0.14); color:#FFB020; }
.type-pill.Speed{ background:var(--clay-dim); color:var(--clay); }
.type-pill.Benchmark{ background:rgba(155,140,255,0.14); color:#9B8CFF; }
.type-pill.Strides{ background:var(--bg-inset); color:var(--text-dim); }
.table-scroll{ overflow-x:auto; }
tbody tr.run-row{ cursor:pointer; }
footer{ margin-top:56px; padding-top:22px; border-top:1px solid var(--border); display:flex; justify-content:space-between; gap:20px; flex-wrap:wrap; font-size:12.5px; color:var(--text-dim); }
footer .update-note{ max-width:560px; }
footer .update-note b{ color:var(--text-muted); }
.empty{ color:var(--text-dim); font-size:0.85rem; }

.modal-overlay{ position:fixed; inset:0; background:rgba(13,16,19,0.72); backdrop-filter:blur(2px); z-index:1000; display:flex; align-items:flex-start; justify-content:center; padding:40px 16px; overflow-y:auto; }
.modal-panel{ background:var(--bg-panel); border:1px solid var(--border); border-radius:8px; max-width:760px; width:100%; padding:24px; position:relative; margin-bottom:40px; }
.modal-close{ position:absolute; top:14px; right:14px; background:var(--bg-raised); border:1px solid var(--border); color:var(--text-muted); width:32px; height:32px; border-radius:8px; font-size:18px; cursor:pointer; line-height:1; }
.modal-close:hover{ color:var(--text); border-color:var(--text-dim); }
.modal-title{ font-family:var(--font-display); font-weight:700; font-size:clamp(18px,3.8vw,22px); margin-bottom:4px; padding-right:40px; text-wrap:balance; }
.modal-sub{ font-family:var(--font-mono); font-size:12px; color:var(--text-muted); margin-bottom:18px; }
.modal-stats{ display:grid; grid-template-columns:repeat(auto-fit,minmax(88px,1fr)); gap:1px; background:var(--border); border:1px solid var(--border); border-radius:6px; overflow:hidden; margin-bottom:22px; }
.modal-stat{ background:var(--bg-raised); padding:12px 14px; }
.modal-stat .stat-label{ margin-bottom:6px; }
.modal-stat .stat-value{ font-size:clamp(15px,3.6vw,18px); }
.modal-section-title{ font-family:var(--font-mono); font-size:11px; text-transform:uppercase; letter-spacing:0.08em; color:var(--text-dim); margin:22px 0 10px; }
.route-map{ height:280px; border-radius:6px; overflow:hidden; border:1px solid var(--border-soft); background:var(--bg-inset); }
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
.route-tile-warning{ display:flex; gap:8px; align-items:flex-start; margin-top:8px; padding:8px 10px; border-radius:6px; background:var(--bg-raised); border:1px solid var(--clay); color:var(--text-muted); font-size:12px; line-height:1.4; }
.route-legend{ display:flex; gap:16px; margin-top:8px; font-size:11px; color:var(--text-muted); }
.modal-splits-table{ width:100%; border-collapse:collapse; font-size:12.5px; }
.modal-splits-table th{ text-align:left; font-family:var(--font-mono); font-size:10.5px; text-transform:uppercase; letter-spacing:0.05em; color:var(--text-dim); font-weight:500; padding:8px 10px; border-bottom:1px solid var(--border); }
.modal-splits-table td{ padding:8px 10px; border-bottom:1px solid var(--border-soft); font-family:var(--font-mono); }

.chart-expand-btn{ position:absolute; top:8px; right:8px; width:26px; height:26px; display:flex; align-items:center; justify-content:center; background:var(--bg-raised); border:1px solid var(--border); border-radius:6px; color:var(--text-dim); font-size:13px; line-height:1; cursor:pointer; opacity:0.55; transition:opacity .15s, color .15s, border-color .15s; z-index:2; }
.chart-expand-btn:hover, .chart-expand-btn:focus-visible{ opacity:1; color:var(--text); border-color:var(--text-dim); }
.chart-zoom-overlay{ align-items:center; z-index:1200; }
.chart-zoom-panel{ background:var(--bg-panel); border:1px solid var(--border); border-radius:6px; width:min(96vw,1140px); max-height:92vh; padding:14px 16px 12px; display:flex; flex-direction:column; margin:0; overflow-y:auto; }
.chart-zoom-toolbar{ display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
.chart-zoom-title{ font-family:var(--font-display); font-weight:700; font-size:15px; text-wrap:balance; }
.chart-zoom-close-btn{ width:30px; height:30px; display:flex; align-items:center; justify-content:center; background:var(--bg-raised); border:1px solid var(--border); border-radius:7px; color:var(--text-muted); font-size:19px; line-height:1; cursor:pointer; flex-shrink:0; }
.chart-zoom-close-btn:hover{ color:var(--text); border-color:var(--text-dim); }
#chart-zoom-toolbar-slot{ margin-top:10px; }
#chart-zoom-toolbar-slot .chart-toolbar{ margin-bottom:0; }
#chart-zoom-box{ margin-top:8px; height:min(68vh,560px); border:1px solid var(--border-soft); border-radius:4px; background:var(--bg-inset); }
.chart-zoom-hint{ margin-top:8px; font-size:11px; color:var(--text-dim); text-align:center; }

/* ---- v15: theme toggle ---- */
.theme-toggle{ font-family:var(--font-mono); font-size:16px; width:36px; height:36px; display:flex; align-items:center; justify-content:center; background:var(--bg-panel); border:1px solid var(--border); border-radius:6px; color:var(--text-muted); cursor:pointer; flex-shrink:0; transition:color .15s, border-color .15s; }
.theme-toggle:hover{ color:var(--text); border-color:var(--text-dim); }
.header-actions{ display:flex; gap:10px; align-items:center; }

/* ---- v15: info-tap glossary ---- */
.info-tip-btn{ display:inline-flex; align-items:center; justify-content:center; width:16px; height:16px; border-radius:50%; background:var(--bg-inset); color:var(--text-dim); font-family:var(--font-mono); font-size:10px; font-weight:600; border:1px solid var(--border); cursor:pointer; margin-left:5px; flex-shrink:0; line-height:1; }
.info-tip-btn:hover, .info-tip-btn.open{ color:var(--amber); border-color:var(--amber); }
.info-tip-pop{ position:absolute; z-index:60; max-width:240px; background:var(--bg-raised); border:1px solid var(--border); border-radius:6px; padding:10px 12px; font-size:12px; line-height:1.5; color:var(--text-muted); box-shadow:0 10px 24px rgba(0,0,0,0.3); display:none; }
.info-tip-pop.show{ display:block; }
.info-tip-pop b{ color:var(--text); }
.label-with-tip{ display:inline-flex; align-items:center; position:relative; }

/* ---- v15: This Week's Plan panel ---- */
.week-recap{ display:flex; flex-wrap:wrap; gap:18px; align-items:baseline; margin-bottom:16px; font-size:12.5px; color:var(--text-muted); }
.week-recap b{ color:var(--text); font-family:var(--font-mono); }
.week-days{ display:grid; grid-template-columns:repeat(7,1fr); gap:8px; }
@media (max-width:760px){ .week-days{ grid-template-columns:repeat(2,1fr); } }
.week-day-card{ border:1px solid var(--border); border-radius:6px; padding:10px 10px 11px; background:var(--bg-raised); display:flex; flex-direction:column; gap:5px; min-height:112px; position:relative; cursor:pointer; touch-action:pan-y; user-select:none; transition:border-color .12s, box-shadow .12s, opacity .12s; }
.week-day-card.is-today{ border-color:var(--amber); box-shadow:0 0 0 1px var(--amber) inset; }
.week-day-card .wd-name{ font-family:var(--font-mono); font-size:10px; letter-spacing:0.06em; text-transform:uppercase; color:var(--text-dim); display:flex; justify-content:space-between; align-items:center; }
.week-day-card .wd-today-chip{ font-family:var(--font-mono); font-size:8.5px; background:var(--amber); color:#fff; padding:1px 5px; border-radius:20px; letter-spacing:0.04em; }
.week-day-card .wd-type{ display:inline-block; align-self:flex-start; font-family:var(--font-display); font-weight:700; font-size:9.5px; text-transform:uppercase; letter-spacing:0.02em; padding:2px 7px; border-radius:3px; color:#fff; }
.week-day-card .wd-title{ font-size:11.5px; font-weight:600; line-height:1.25; }
.week-day-card .wd-sub{ font-size:10.5px; color:var(--text-muted); line-height:1.3; margin-top:auto; }
.week-day-card.status-done{ opacity:0.72; }
.week-day-card .wd-status-icon{ position:absolute; top:8px; right:8px; font-size:11px; }
.week-day-card .wd-note-dot{ position:absolute; top:9px; right:26px; font-size:10px; color:var(--amber); }
.week-day-card .wd-swapped-tag{ font-size:9px; color:var(--text-dim); font-family:var(--font-mono); }
.week-day-card .wd-check-row{ display:flex; align-items:center; gap:6px; font-size:10px; font-family:var(--font-mono); color:var(--text-muted); margin-top:2px; }
.week-day-card .wd-check-row input{ width:13px; height:13px; accent-color:var(--amber); cursor:pointer; }
.week-day-card.drag-dragging{ opacity:0.35; }
.week-day-card.drag-over{ border-color:var(--amber); box-shadow:0 0 0 2px var(--amber) inset; }
.drag-ghost{ position:fixed; z-index:2000; pointer-events:none; padding:8px 12px; border-radius:6px; background:var(--bg-raised); border:1px solid var(--amber); box-shadow:0 8px 24px rgba(0,0,0,0.4); font-size:11px; font-family:var(--font-display); font-weight:700; color:var(--text); opacity:0.92; transform:translate(-50%,-140%); }

.week-nav-row{ display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:14px; flex-wrap:wrap; }
.week-nav-btn{ background:var(--bg-raised); border:1px solid var(--border); color:var(--text-muted); font-family:var(--font-mono); font-size:11px; padding:6px 11px; border-radius:6px; cursor:pointer; }
.week-nav-btn:hover:not(:disabled){ color:var(--text); border-color:var(--text-dim); }
.week-nav-btn:disabled{ opacity:0.35; cursor:default; }
.week-nav-label{ font-family:var(--font-mono); font-size:11.5px; color:var(--text-dim); text-align:center; flex:1; min-width:140px; }
.week-nav-jump{ font-family:var(--font-mono); font-size:10.5px; color:var(--amber); background:none; border:none; cursor:pointer; text-decoration:underline; padding:0; }
.week-save-status{ font-family:var(--font-mono); font-size:10.5px; color:var(--text-dim); display:flex; align-items:center; gap:5px; }
.week-save-status.is-saving{ color:var(--amber); }
.week-save-status.is-error{ color:var(--clay); cursor:pointer; text-decoration:underline; }
.week-reset-link{ font-family:var(--font-mono); font-size:10px; color:var(--text-dim); background:none; border:none; text-decoration:underline; cursor:pointer; padding:0; margin-top:6px; align-self:flex-start; }

.day-modal-panel{ max-width:480px; }
.day-modal-type{ display:inline-block; font-family:var(--font-display); font-weight:700; font-size:10.5px; text-transform:uppercase; letter-spacing:0.02em; padding:3px 9px; border-radius:4px; color:#fff; margin-bottom:10px; }
.day-modal-title{ font-family:var(--font-display); font-weight:700; font-size:18px; margin-bottom:4px; text-wrap:balance; }
.day-modal-date{ font-family:var(--font-mono); font-size:11.5px; color:var(--text-dim); margin-bottom:16px; }
.day-modal-detail{ font-size:13px; line-height:1.5; color:var(--text-muted); background:var(--bg-inset); border:1px solid var(--border-soft); border-radius:6px; padding:12px 14px; margin-bottom:16px; }
.day-modal-actual{ font-size:12.5px; margin-bottom:16px; }
.day-modal-swap-row{ display:flex; align-items:center; gap:8px; margin-bottom:16px; font-size:11.5px; }
.day-modal-swap-row select{ background:var(--bg-raised); border:1px solid var(--border); color:var(--text); font-family:var(--font-mono); font-size:11.5px; padding:5px 8px; border-radius:5px; }
.day-modal-notes textarea{ width:100%; min-height:90px; background:var(--bg-inset); border:1px solid var(--border-soft); border-radius:6px; color:var(--text); font-family:var(--font-body); font-size:13px; padding:10px 12px; resize:vertical; }
.day-modal-notes-footer{ display:flex; align-items:center; justify-content:space-between; gap:10px; margin-top:8px; }
.day-modal-save-btn{ background:var(--amber); color:#1a1200; border:none; font-family:var(--font-display); font-weight:700; font-size:11.5px; padding:7px 16px; border-radius:6px; cursor:pointer; }
.day-modal-save-btn:disabled{ opacity:0.5; cursor:default; }
.plan-table-wrap .section-note-inline{ font-size:12px; color:var(--text-dim); margin-bottom:10px; }
details.plan-expand{ margin-top:14px; }
details.plan-expand > summary{ cursor:pointer; font-family:var(--font-mono); font-size:12px; color:var(--text-muted); padding:6px 0; list-style:none; }
details.plan-expand > summary::-webkit-details-marker{ display:none; }
details.plan-expand > summary::before{ content:"\25B8  "; color:var(--amber); }
details.plan-expand[open] > summary::before{ content:"\25BE  "; }
.phase-chip{ display:inline-flex; align-items:center; gap:5px; font-family:var(--font-mono); font-size:10.5px; color:var(--text-muted); }
.phase-chip .dot{ width:8px; height:8px; border-radius:2px; display:inline-block; }
.phase-legend-row{ display:flex; flex-wrap:wrap; gap:14px; margin-top:10px; }

/* ---- v15: goal reassessment panel ---- */
.goal-panel .goal-head{ display:flex; justify-content:space-between; align-items:baseline; gap:12px; flex-wrap:wrap; margin-bottom:6px; }
.goal-range{ font-family:var(--font-mono); font-size:clamp(18px,4vw,22px); font-weight:600; color:var(--amber); }
.goal-prior{ font-size:12px; color:var(--text-dim); text-decoration:line-through; }
.goal-findings{ margin-top:14px; display:flex; flex-direction:column; gap:10px; }
.goal-finding{ display:grid; grid-template-columns:120px 1fr; gap:14px; padding-top:10px; border-top:1px solid var(--border-soft); }
.goal-finding:first-child{ border-top:none; padding-top:0; }
.goal-finding .gf-label{ font-family:var(--font-mono); font-size:11px; color:var(--text-dim); padding-top:1px; }
.goal-finding .gf-text{ font-size:12.5px; color:var(--text-muted); line-height:1.55; }
@media (max-width:640px){ .goal-finding{ grid-template-columns:1fr; gap:3px; } }

/* ---- v15: mobile quick-nav ---- */
.mobile-tabbar{ display:none; }
@media (max-width:760px){
  .mobile-tabbar{ display:flex; position:fixed; left:0; right:0; bottom:0; z-index:500; background:var(--bg-panel); border-top:1px solid var(--border); padding:6px 4px calc(6px + env(safe-area-inset-bottom)); }
  .mobile-tabbar button{ flex:1; background:none; border:none; color:var(--text-dim); font-family:var(--font-mono); font-size:10px; text-transform:uppercase; letter-spacing:0.04em; padding:6px 2px; cursor:pointer; }
  .mobile-tabbar button.active{ color:var(--amber); }
  body{ padding-bottom:60px; }
}

/* ---- v15: run-detail modal upgrades ---- */
.modal-nav-row{ display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; padding-right:40px; } /* v15 hotfix: reserves the same 40px .modal-title already reserves (see its own comment-free but matching padding-right above) so the right-aligned Next button never sits under the absolutely-positioned close button */
.modal-nav-btn{ font-family:var(--font-mono); font-size:11px; background:var(--bg-raised); border:1px solid var(--border); color:var(--text-muted); border-radius:6px; padding:6px 10px; cursor:pointer; display:flex; align-items:center; gap:6px; }
.modal-nav-btn:hover:not(:disabled){ color:var(--text); border-color:var(--text-dim); }
.modal-nav-btn:disabled{ opacity:0.35; cursor:default; }
.modal-ministrip{ position:sticky; top:0; z-index:5; margin:0 -24px 0; padding:0 24px; background:var(--bg-panel); display:flex; gap:16px; overflow-x:auto; max-height:0; opacity:0; transition:max-height .18s ease, opacity .18s ease, padding .18s ease, border-color .18s ease; border-bottom:1px solid transparent; }
.modal-ministrip.scrolled{ max-height:54px; opacity:1; padding:10px 24px; border-bottom-color:var(--border); }
.modal-ministrip .ms-item{ font-family:var(--font-mono); font-size:11.5px; color:var(--text-muted); white-space:nowrap; }
.modal-ministrip .ms-item b{ color:var(--text); }
.plan-tie-in{ display:flex; gap:10px; align-items:flex-start; background:var(--bg-raised); border:1px solid var(--border-soft); border-radius:6px; padding:12px 14px; margin-bottom:16px; font-size:12.5px; color:var(--text-muted); line-height:1.5; }
.plan-tie-in b{ color:var(--text); }
.insight-banner{ display:flex; gap:10px; align-items:flex-start; border-radius:6px; padding:11px 14px; margin-bottom:16px; font-size:12.5px; line-height:1.5; }
.insight-banner.tone-good{ background:var(--teal-dim); color:var(--teal); }
.insight-banner.tone-watch{ background:var(--warn-dim); color:var(--warn); }
.insight-banner b{ color:inherit; }
.route-map-wrap{ position:relative; }
.route-hover-readout{ position:absolute; top:8px; left:8px; z-index:450; background:var(--bg-panel); border:1px solid var(--border); border-radius:6px; padding:5px 9px; font-family:var(--font-mono); font-size:11px; color:var(--text-muted); pointer-events:none; opacity:0; transition:opacity .1s; }
.route-hover-readout.show{ opacity:1; }
.splits-hover-dot{ position:absolute; top:0; width:9px; height:9px; margin-left:-4.5px; margin-top:-4.5px; border-radius:50%; background:var(--warn); border:2px solid var(--bg-panel); pointer-events:none; opacity:0; z-index:4; }
.splits-hover-dot.show{ opacity:1; }
.route-pace-legend{ display:flex; align-items:center; gap:8px; margin-top:8px; font-size:11px; color:var(--text-muted); }
.route-pace-legend .ramp{ width:90px; height:8px; border-radius:4px; background:linear-gradient(90deg, #00B4E0, #FFB020); }
"""

JS = r"""
function paceStr(min){ if(min==null) return '—'; const m=Math.floor(min), s=Math.round((min-m)*60); return `${m}:${s.toString().padStart(2,'0')}`; }
function durStr(min){ const t=Math.round(min*60), h=Math.floor(t/3600), m=Math.floor((t%3600)/60), s=t%60; return h>0?`${h}:${m.toString().padStart(2,'0')}:${s.toString().padStart(2,'0')}`:`${m}:${s.toString().padStart(2,'0')}`; }
function fmtDate(d){ return new Date(d+'T12:00:00').toLocaleDateString('en-US',{month:'short',day:'numeric'}); }
const TYPE_COLORS = {'Long Run':'#45D6B0','Easy Run':'#7E8EA3','Tempo':'#FFB020','Speed':'#FF5A64','Benchmark':'#9B8CFF','Strides':'#57636F'};
// Sequential ramp for the Weekly Volume bars — one hue, light→dark, so the
// tallest (peak-mileage) week reads as deepest and darkest rather than every
// bar being a flat, identically-saturated block (see the dataviz guidance on
// sequential-for-magnitude encodings).
const VOL_RAMP = ['#0F3D4D','#0F4757','#14627A','#0D7FA0','#0093BC','#00A0CC','#00B4E0'];
function rampColor(frac, ramp){ const i=Math.round(Math.max(0,Math.min(1,frac))*(ramp.length-1)); return ramp[i]; }
// v15 — great-circle distance in miles, same formula as the Python haversine_m,
// used client-side to build a cumulative-distance array along a route's GPS
// points (route points and mile splits come from two separately-sampled
// Garmin streams, so matching them is a "same fraction of total distance"
// approximation, not an exact index correspondence — see renderRouteMap).
function haversineMi(lat1,lon1,lat2,lon2){
  const R=3958.8, toRad=d=>d*Math.PI/180;
  const dLat=toRad(lat2-lat1), dLon=toRad(lon2-lon1);
  const a=Math.sin(dLat/2)**2 + Math.cos(toRad(lat1))*Math.cos(toRad(lat2))*Math.sin(dLon/2)**2;
  return 2*R*Math.asin(Math.sqrt(Math.min(1,a)));
}
// Fast (cyan) → slow (amber) two-hue gradient for pace-colored route segments —
// a diverging-style encoding around the run's own pace range, not an absolute
// pace scale, so it stays legible whether the run was a 7:30 tempo or a
// 12:30 recovery jog.
function paceToColor(pace, minPace, maxPace){
  if(maxPace<=minPace) return '#00B4E0';
  const f = Math.max(0, Math.min(1, (pace-minPace)/(maxPace-minPace)));
  const lerp=(a,b,t)=>Math.round(a+(b-a)*t);
  const c1=[0,180,224], c2=[255,176,32]; // #00B4E0 -> #FFB020
  return `rgb(${lerp(c1[0],c2[0],f)},${lerp(c1[1],c2[1],f)},${lerp(c1[2],c2[2],f)})`;
}
function safe(name, fn){ try{ fn(); } catch(e){ console.error('Section failed:', name, e); const el=document.getElementById('boot-errors'); if(el){ el.style.display='block'; el.innerHTML += `<div>Section "${name}" failed: ${e.message}</div>`; } } }

// v15 — fixed categorical colors for the TRAINING_PLAN session types (distinct
// from TYPE_COLORS above, which classifies REAL Garmin runs) and for the
// training-plan phases, used by the This Week panel and the Plan vs. Actual
// chart's phase shading. Assigned in a fixed order, never cycled/generated.
const PLAN_TYPE_COLORS = {
  'Intervals':'#00B4E0', 'Tempo':'#FFB020', 'Long Run':'#45D6B0', 'Easy':'#2FD480', 'Race':'#FF5A64',
  'Rest':'#57636F', 'Cross Training':'#9B8CFF', 'Strength — Heavy':'#C97E6B', 'Strength — Light':'#D9A68C',
};
const PHASE_COLORS = {
  'Reintroduction':'#57636F', 'Rebuild':'#2FD480', 'Taper begins':'#FFB020', 'Deep taper':'#9B8CFF', 'Race Week':'#FF5A64',
};
function planTypeColor(t){ return PLAN_TYPE_COLORS[t] || '#57636F'; }
function phaseColor(p){ return PHASE_COLORS[p] || '#57636F'; }
const DAY_ORDER = ['mon','tue','wed','thu','fri','sat','sun'];
const DAY_NAMES = {mon:'Mon',tue:'Tue',wed:'Wed',thu:'Thu',fri:'Fri',sat:'Sat',sun:'Sun'};

// v15 — light/dark theme toggle. Persisted per-browser via localStorage
// (wrapped in try/catch: a private window or blocked storage just falls back
// to the default dark theme every load rather than erroring).
safe('theme toggle', function(){
  const root = document.documentElement;
  const btn = document.getElementById('theme-toggle');
  let saved = null;
  try{ saved = localStorage.getItem('garmin-dashboard-theme'); }catch(e){}
  if(saved === 'light' || saved === 'dark') root.dataset.theme = saved;
  function current(){ return root.dataset.theme === 'light' ? 'light' : 'dark'; }
  function paintIcon(){ btn.textContent = current()==='light' ? '☀' : '☽'; }
  paintIcon();
  btn.addEventListener('click', ()=>{
    const next = current()==='light' ? 'dark' : 'light';
    root.dataset.theme = next;
    try{ localStorage.setItem('garmin-dashboard-theme', next); }catch(e){}
    paintIcon();
    redrawCharts();
  });
});

// v15 — info-tap glossary. One shared popover element, repositioned under
// whichever (i) button was clicked; closes on outside click, Escape, or a
// second click on the same button. Kept intentionally short — a sentence or
// two, not a full explainer — since it's a tap-to-glance, not a reading task.
const INFO_TIPS = {
  readiness: 'Garmin’s blend of HRV status, sleep, recovery time and recent training load into a single 0–100 score for how ready your body is for a hard effort today.',
  acwr: 'Acute:Chronic Workload Ratio — this week’s mileage against your rolling 4-week average. Below ~0.8 often means detraining; above ~1.5 is a classic injury-risk spike.',
  hrv: 'Heart rate variability overnight. A dip below your personal baseline is one of the earlier signs of accumulated fatigue or illness, often before perceived effort changes.',
  effortmix: 'The share of recent training minutes spent easy vs. moderate vs. hard. The dashed band is a general 80/20-style target — most endurance runners do best heavily weighted easy.',
  efficiency: 'Speed per heartbeat on easy/long runs — distance covered per beat of average HR. Rising over time is a sign of aerobic fitness gains that pace alone can hide (pace is thrown off by heat, hills, wind; this mostly isn’t).',
};
safe('info tips', function(){
  let openBtn = null;
  const pop = document.createElement('div');
  pop.className = 'info-tip-pop';
  document.body.appendChild(pop);
  function closeTip(){ pop.classList.remove('show'); if(openBtn) openBtn.classList.remove('open'); openBtn=null; }
  document.addEventListener('click', e=>{
    const btn = e.target.closest('.info-tip-btn');
    if(!btn){ if(!e.target.closest('.info-tip-pop')) closeTip(); return; }
    e.stopPropagation();
    if(openBtn === btn){ closeTip(); return; }
    closeTip();
    const text = INFO_TIPS[btn.dataset.infoKey] || '';
    pop.innerHTML = text;
    pop.classList.add('show');
    openBtn = btn;
    const r = btn.getBoundingClientRect();
    const pw = 240;
    let left = r.left + window.scrollX - pw/2 + r.width/2;
    left = Math.max(10, Math.min(left, window.scrollX + window.innerWidth - pw - 10));
    pop.style.left = left + 'px';
    pop.style.top = (r.bottom + window.scrollY + 6) + 'px';
  });
  document.addEventListener('keydown', e=>{ if(e.key==='Escape') closeTip(); });
  // No scroll-close listener here: the popover is position:absolute against
  // the document (not fixed), so it scrolls naturally with the page and
  // stays anchored near its button — and a scroll-to-close listener raced
  // against the browser's own scroll-element-into-view behavior on the very
  // click that opens it (a focus/click on an off-screen button scrolls first),
  // closing the popover the instant it opened.
});

// v15 — mobile quick-nav. Only visible under the CSS breakpoint, but harmless
// (just hidden) on desktop, so it's always wired up rather than conditionally
// built. Highlights whichever tracked section is currently most in view.
safe('mobile tabbar', function(){
  const bar = document.getElementById('mobile-tabbar');
  if(!bar) return;
  const buttons = [...bar.querySelectorAll('button')];
  buttons.forEach(b=>{
    b.addEventListener('click', ()=>{
      const target = document.getElementById(b.dataset.target);
      if(target) target.scrollIntoView({behavior:'smooth', block:'start'});
    });
  });
  const sections = buttons.map(b=>document.getElementById(b.dataset.target)).filter(Boolean);
  if(!sections.length || typeof IntersectionObserver==='undefined') return;
  const byId = {}; buttons.forEach(b=>{ byId[b.dataset.target]=b; });
  const observer = new IntersectionObserver(entries=>{
    entries.forEach(entry=>{
      if(entry.isIntersecting){
        buttons.forEach(b=>b.classList.remove('active'));
        const b = byId[entry.target.id]; if(b) b.classList.add('active');
      }
    });
  }, {rootMargin:'-20% 0px -70% 0px'});
  sections.forEach(s=>observer.observe(s));
});
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

// --- Windowed zoom/pan for every chart (v13) -------------------------------
// v9-v11 zoomed by giving a chart more PIXELS (a wider canvas, same data, same
// scale) — useful, but not what "zoom" means in the Garmin Connect app, where
// pinching narrows the visible date range and BOTH axes rescale to what's
// actually on screen. This is that: each chart owns a "view window" (a
// start/end pair in data-index units — or elapsed-seconds units for the
// interval chart), scroll/pinch narrows or widens that window around the
// pointer, drag pans it once zoomed in, and every render recomputes the
// x-scale AND the y-scale from only the points inside the window — so zooming
// into three weeks of a six-month pace history doesn't just spread the same
// flat line wider, it reveals the day-to-day swings that were compressed
// against a full-range y-axis. Double-click/tap or the "Reset zoom" pill
// snaps back to the full range.
//
// CHART_INSTANCES holds one live entry per chart container id: its current
// view window, its data, and which render function draws it. registerZoomChart
// is the entry point every chart (re-)registers through; it reuses the
// existing instance (keeping the current zoom) when the SAME DOM node and the
// SAME data array are being redrawn (e.g. a window resize), and starts a
// fresh full-view instance when either the container is a brand new element
// (e.g. the run-detail modal was reopened for a different run) or the data
// reference changed (e.g. switching Long Run Splits tabs to a different run).
const CHART_INSTANCES = {};

function ensureChartChrome(containerId){
  const svgDiv = document.getElementById(containerId);
  if(!svgDiv) return;
  const box = svgDiv.closest('.chart-box');
  if(!box || box.id === 'chart-zoom-box') return; // the zoom modal's own chart box gets no chrome of its own
  if(!box.querySelector('.chart-expand-btn')){
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chart-expand-btn';
    btn.title = 'Expand for a larger view';
    btn.setAttribute('aria-label', 'Expand chart for a larger view');
    btn.innerHTML = '⤢';
    btn.addEventListener('click', e => { e.stopPropagation(); openChartZoom(containerId); });
    box.appendChild(btn);
  }
  const prevSib = box.previousElementSibling;
  if(!prevSib || !prevSib.classList || !prevSib.classList.contains('chart-toolbar')){
    const toolbar = document.createElement('div');
    toolbar.className = 'chart-toolbar';
    toolbar.innerHTML = `<span class="zoom-range" id="${containerId}-range"></span><button type="button" class="zoom-reset-btn" id="${containerId}-reset">Reset zoom</button>`;
    box.parentNode.insertBefore(toolbar, box);
    document.getElementById(containerId+'-reset').addEventListener('click', () => { const inst=CHART_INSTANCES[containerId]; if(inst) inst.reset(); });
  }
}

// One zoom/pan engine shared by every chart. `container` is the actual
// .svg-chart div the chart draws into and the element all gesture listeners
// bind to — since that element's identity never changes across re-renders
// (only its children get replaced), the listeners attached here stay valid
// for the life of the chart, including while it's temporarily reparented into
// the expand modal (see openChartZoom below).
function makeZoomChart({containerId, container}){
  let view = {start:0, end:1};
  let dragging=false, dragStartX=0, dragStartView=null, pinch=null, raf=null, lastTapT=0;

  function self(){ return CHART_INSTANCES[containerId]; }
  function domN(){ return Math.max(1, (self() && self().domainSize) || 1); }
  function minW(){ const mw = self() && self().minWindow; return Math.min(domN(), Math.max(0.001, mw || 1)); }
  function clampView(v){
    let width = Math.max(minW(), Math.min(domN(), v.end-v.start));
    let start=v.start, end=v.start+width;
    if(start<0){ start=0; end=width; }
    if(end>domN()){ end=domN(); start=end-width; }
    return {start,end};
  }
  function plotGeom(){
    const {w:W} = chartSize(container,720,300);
    const M = container._plotMargins || {left:0,right:0};
    return { plotW: Math.max(1,W-M.left-M.right), M };
  }
  function pxToIndex(px){
    const {plotW,M} = plotGeom();
    const frac = Math.max(0, Math.min(1, (px-M.left)/plotW));
    return view.start + frac*(view.end-view.start);
  }
  function scheduleRender(){
    if(raf) return;
    raf = requestAnimationFrame(() => { raf=null; doRender(); });
  }
  function doRender(){
    const inst = self();
    if(!inst) return;
    container.innerHTML='';
    // v15 hotfix: this fires inside requestAnimationFrame (see scheduleRender
    // below), one tick after the code that called registerZoomChart/reset()
    // has already returned — so a try/catch wrapped around THAT caller (as
    // openRunModal's route/splits calls now have) never sees an exception
    // thrown in here; it becomes a silent uncaught rejection in a detached
    // callback, and since container.innerHTML was already cleared above, the
    // chart is left permanently blank with nothing on the page to explain why
    // — only a console error nobody's watching for. This is the shared
    // render path for EVERY zoomable chart (all 6 main dashboard charts, Long
    // Run Splits, and the run-detail modal's own splits/interval chart), so
    // catching it here hardens all of them at once, not just one.
    try{
      inst.renderFn(container, inst.data, view);
    }catch(e){
      console.error('Chart render failed:', containerId, e);
      container.innerHTML = `<p class="empty">This chart failed to render — ${e.message}. Check the browser console (F12 → Console) for the full error.</p>`;
    }
    const zoomed = (view.end-view.start) < domN()-1e-6;
    container.classList.toggle('zoomed', zoomed);
    const rangeEl = document.getElementById(containerId+'-range');
    const resetBtn = document.getElementById(containerId+'-reset');
    if(rangeEl) rangeEl.textContent = inst.rangeFmt ? inst.rangeFmt(inst.data, view, zoomed) : '';
    if(resetBtn) resetBtn.classList.toggle('show', zoomed);
  }
  function zoomBy(factor, atPx){
    const centerIdx = atPx!=null ? pxToIndex(atPx) : (view.start+view.end)/2;
    const newWidth = (view.end-view.start)*factor;
    const leftFrac = (centerIdx-view.start)/((view.end-view.start)||1);
    const start = centerIdx-leftFrac*newWidth;
    view = clampView({start, end:start+newWidth});
    scheduleRender();
  }
  function panByIndex(deltaIdx){ view = clampView({start:view.start+deltaIdx, end:view.end+deltaIdx}); scheduleRender(); }
  function reset(){ view = {start:0, end:domN()}; scheduleRender(); }
  function redraw(){ view = clampView(view); scheduleRender(); }

  // Vertical wheel/trackpad motion zooms (matches the v9-v11 convention this
  // dashboard already trained users on); horizontal motion pans once zoomed.
  container.addEventListener('wheel', e => {
    if(Math.abs(e.deltaY) <= Math.abs(e.deltaX)){
      if(view.end-view.start < domN()-1e-6){
        e.preventDefault();
        const {plotW} = plotGeom();
        panByIndex((e.deltaX/plotW)*(view.end-view.start));
      }
      return;
    }
    e.preventDefault();
    const rect = container.getBoundingClientRect();
    zoomBy(Math.pow(1.0016, e.deltaY), e.clientX-rect.left);
  }, {passive:false});

  container.addEventListener('dblclick', reset);

  container.addEventListener('mousedown', e => {
    if(e.button!==0) return;
    dragging=true; dragStartX=e.clientX; dragStartView={...view};
    container.classList.add('panning');
  });
  window.addEventListener('mousemove', e => {
    if(!dragging) return;
    const {plotW} = plotGeom();
    const deltaIdx = -((e.clientX-dragStartX)/plotW)*(dragStartView.end-dragStartView.start);
    view = clampView({start:dragStartView.start+deltaIdx, end:dragStartView.end+deltaIdx});
    scheduleRender();
  });
  window.addEventListener('mouseup', () => { if(dragging){ dragging=false; container.classList.remove('panning'); } });

  // Two-finger pinch zooms, anchored at the pinch midpoint; a single finger
  // drags to pan, and a quick double-tap resets — the same gestures as the
  // Garmin Connect app's own chart zoom.
  container.addEventListener('touchstart', e => {
    if(e.touches.length===2){
      const [a,b]=e.touches, rect=container.getBoundingClientRect();
      pinch = { dist:Math.hypot(b.clientX-a.clientX,b.clientY-a.clientY), view:{...view}, midX:(a.clientX+b.clientX)/2-rect.left };
      dragging=false;
    } else if(e.touches.length===1){
      const now=Date.now();
      if(now-lastTapT<320){ reset(); lastTapT=0; return; }
      lastTapT=now;
      dragging=true; dragStartX=e.touches[0].clientX; dragStartView={...view};
    }
  }, {passive:true});
  container.addEventListener('touchmove', e => {
    if(pinch && e.touches.length===2){
      e.preventDefault();
      const [a,b]=e.touches;
      const dist=Math.hypot(b.clientX-a.clientX,b.clientY-a.clientY);
      const {plotW,M} = plotGeom();
      const frac = Math.max(0, Math.min(1, (pinch.midX-M.left)/plotW));
      const centerIdx = pinch.view.start + frac*(pinch.view.end-pinch.view.start);
      const newWidth = (pinch.view.end-pinch.view.start)*(pinch.dist/dist);
      const leftFrac = (centerIdx-pinch.view.start)/((pinch.view.end-pinch.view.start)||1);
      const start = centerIdx-leftFrac*newWidth;
      view = clampView({start, end:start+newWidth});
      scheduleRender();
    } else if(dragging && e.touches.length===1){
      e.preventDefault();
      const {plotW} = plotGeom();
      const deltaIdx = -((e.touches[0].clientX-dragStartX)/plotW)*(dragStartView.end-dragStartView.start);
      view = clampView({start:dragStartView.start+deltaIdx, end:dragStartView.end+deltaIdx});
      scheduleRender();
    }
  }, {passive:false});
  container.addEventListener('touchend', e => { if(e.touches.length<2) pinch=null; if(e.touches.length===0) dragging=false; });

  return { containerId, container, reset, redraw, get view(){ return view; } };
}

// Every chart (re-)registers through here. `opts.data` is compared by
// REFERENCE against what's already registered for this container id to
// decide whether this is "the same chart, redraw it" (e.g. a resize — keep
// the current zoom) or "genuinely different data" (e.g. a different run's
// splits — reset to the full view). Callers that recompute a filtered/sorted
// array on every call (the pace and series charts) cache that array once so
// repeated registrations pass the SAME reference and don't spuriously reset —
// see PACED_RUNS_ASC / HRV_PTS / VO2_PTS / EF_PTS below.
function registerZoomChart(containerId, opts){
  const container = document.getElementById(containerId);
  if(!container) return null;
  const prev = CHART_INSTANCES[containerId];
  if(prev && prev.container === container){
    const sameData = prev.data === opts.data;
    Object.assign(prev, { title:opts.title, data:opts.data, domainSize:opts.domainSize, minWindow:opts.minWindow, renderFn:opts.render, rangeFmt:opts.rangeFmt });
    ensureChartChrome(containerId);
    sameData ? prev.redraw() : prev.reset();
    return prev;
  }
  const inst = makeZoomChart({containerId, container});
  Object.assign(inst, { title:opts.title, data:opts.data, domainSize:opts.domainSize, minWindow:opts.minWindow, renderFn:opts.render, rangeFmt:opts.rangeFmt });
  CHART_INSTANCES[containerId] = inst;
  ensureChartChrome(containerId);
  inst.reset();
  return inst;
}

// The ⤢ button no longer opens a copy of the chart at a bigger CSS scale —
// it reparents the chart's OWN .svg-chart div (with its live zoom state and
// listeners intact) into the modal's larger box, then moves it back on close.
// Nothing is re-rendered from scratch and nothing needs to be kept in sync;
// it's literally the same element, just temporarily somewhere bigger.
let CHART_ZOOM_STATE = null;
function openChartZoom(chartId){
  const inst = CHART_INSTANCES[chartId];
  const svgDiv = document.getElementById(chartId);
  if(!inst || !svgDiv) return;
  const originalBox = svgDiv.closest('.chart-box');
  const toolbar = (originalBox && originalBox.previousElementSibling && originalBox.previousElementSibling.classList.contains('chart-toolbar')) ? originalBox.previousElementSibling : null;
  const modal = document.getElementById('chart-zoom-modal');
  document.getElementById('chart-zoom-title').textContent = inst.title || '';
  CHART_ZOOM_STATE = {
    chartId, svgDiv, svgParent: svgDiv.parentNode, svgNext: svgDiv.nextSibling,
    toolbar, toolbarParent: toolbar ? toolbar.parentNode : null, toolbarNext: toolbar ? toolbar.nextSibling : null,
  };
  if(toolbar) document.getElementById('chart-zoom-toolbar-slot').appendChild(toolbar);
  document.getElementById('chart-zoom-box').appendChild(svgDiv);
  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';
  // Two frames so the modal has finished laying out (real size available)
  // before the chart redraws to fit its new, larger box.
  requestAnimationFrame(() => requestAnimationFrame(() => inst.redraw()));
}
function closeChartZoom(){
  if(!CHART_ZOOM_STATE) return;
  const {chartId, svgDiv, svgParent, svgNext, toolbar, toolbarParent, toolbarNext} = CHART_ZOOM_STATE;
  if(svgParent) svgParent.insertBefore(svgDiv, svgNext);
  if(toolbar && toolbarParent) toolbarParent.insertBefore(toolbar, toolbarNext);
  document.getElementById('chart-zoom-modal').style.display = 'none';
  document.body.style.overflow = '';
  CHART_ZOOM_STATE = null;
  const inst = CHART_INSTANCES[chartId];
  if(inst) requestAnimationFrame(() => inst.redraw());
}
safe('chart zoom modal', function(){
  document.getElementById('chart-zoom-close').addEventListener('click', closeChartZoom);
  document.getElementById('chart-zoom-modal').addEventListener('click', e => { if(e.target.id==='chart-zoom-modal') closeChartZoom(); });
  document.addEventListener('keydown', e => { if(e.key==='Escape' && CHART_ZOOM_STATE) closeChartZoom(); });
});

// Each renderXWindow(container, data, view) draws only the slice of `data`
// inside `view` (a {start,end} pair of fractional indices), with the y-axis
// rescaled to that slice's own min/max — the "genuinely zoomed" behavior
// described above. `view.start`/`view.end` span the full [0, data.length-1]
// range at rest, so these render exactly like the old un-windowed versions
// when nothing is zoomed.
function renderVolumeWindow(container, weekly, view){
  if(!weekly.length){ container.innerHTML="<p class='empty'>No weekly data yet.</p>"; return; }
  const {w:W,h:H}=chartSize(container,720,300), M={top:26,right:40,bottom:34,left:42};
  container._plotMargins = M;
  const plotW=W-M.left-M.right, plotH=H-M.top-M.bottom;
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,preserveAspectRatio:'none'});
  const n=weekly.length;
  const lo=Math.max(0,Math.floor(view.start)), hi=Math.min(n-1,Math.ceil(view.end));
  const visibleIdx=[]; for(let i=lo;i<=hi;i++) visibleIdx.push(i);
  const maxMiles=Math.max(...visibleIdx.map(i=>weekly[i].miles),1);
  const yTicks=niceTicks(0,maxMiles,5), yMax=yTicks[yTicks.length-1];
  const yScale=v=>M.top+plotH-(v/yMax)*plotH;
  const runsMax=Math.max(6,...visibleIdx.map(i=>weekly[i].runs));
  const y1Scale=v=>M.top+plotH-(v/runsMax)*plotH;
  const bandW=plotW/(view.end-view.start), xCenter=i=>M.left+(i-view.start)*bandW+bandW/2;
  const volLabels=labelIndices(visibleIdx.length, plotW, widestLabelPx(visibleIdx.map(i=>weekly[i].label)));
  yTicks.forEach(t=>{ svg.appendChild(el('line',{class:'grid-line',x1:M.left,x2:W-M.right,y1:yScale(t),y2:yScale(t)})); const lbl=el('text',{x:M.left-8,y:yScale(t)+3,'text-anchor':'end'}); lbl.textContent=t; svg.appendChild(lbl); });
  const yTitle=el('text',{x:10,y:12}); yTitle.textContent='miles'; svg.appendChild(yTitle);
  const y1Title=el('text',{x:W-M.right,y:12,'text-anchor':'end'}); y1Title.textContent='runs/wk'; svg.appendChild(y1Title);
  const milesBarW=Math.min(bandW*0.44,70), lrBarW=Math.min(bandW*0.24,36);
  const clipId='vol-clip-'+Math.random().toString(36).slice(2);
  const clip=el('clipPath',{id:clipId}); clip.appendChild(el('rect',{x:M.left,y:M.top,width:plotW,height:plotH})); svg.appendChild(clip);
  // v15 — PR badge: the single highest-mileage week in the ENTIRE history
  // (not just the visible window), so panning/zooming never moves which bar
  // wears the badge. Only drawn when that week happens to be on screen.
  let peakIdx=-1, peakMiles=-1;
  weekly.forEach((w,i)=>{ if(w.miles>peakMiles){ peakMiles=w.miles; peakIdx=i; } });
  visibleIdx.forEach((i,k)=>{
    const w=weekly[i], cx=xCenter(i), mBarX=cx-milesBarW-2, mBarY=yScale(w.miles);
    const mBar=el('rect',{class:'data-point',x:mBarX,y:mBarY,width:milesBarW,height:(M.top+plotH)-mBarY,fill:rampColor(w.miles/maxMiles,VOL_RAMP),rx:2});
    mBar.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">Week of ${w.label}</div><div class="tt-row">Miles: <b>${w.miles.toFixed(1)}</b></div><div class="tt-row">Runs: <b>${w.runs}</b></div>${w.longRunMiles?`<div class="tt-row">Long run: <b>${w.longRunMiles.toFixed(1)}mi</b></div>`:''}${i===peakIdx?'<div class="tt-row" style="color:#FFB020;">★ Peak week, all-time</div>':''}`));
    mBar.addEventListener('mousemove',positionTooltip); mBar.addEventListener('mouseleave',hideTooltip);
    svg.appendChild(mBar);
    if(w.longRunMiles){
      const lrBarX=cx+2, lrBarY=yScale(w.longRunMiles);
      const lrBar=el('rect',{class:'data-point',x:lrBarX,y:lrBarY,width:lrBarW,height:(M.top+plotH)-lrBarY,fill:'#45D6B0',rx:2});
      lrBar.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">Week of ${w.label}</div><div class="tt-row">Long run: <b>${w.longRunMiles.toFixed(1)}mi</b></div>`));
      lrBar.addEventListener('mousemove',positionTooltip); lrBar.addEventListener('mouseleave',hideTooltip);
      svg.appendChild(lrBar);
    }
    if(i===peakIdx && peakMiles>0){
      const star=el('text',{x:cx,y:mBarY-8,'text-anchor':'middle'});
      star.style.fill='#FFB020'; star.style.fontSize='13px'; star.textContent='★';
      svg.appendChild(star);
    }
    if(volLabels.has(k)){ const xl=el('text',{x:cx,y:H-M.bottom+16,'text-anchor':'middle'}); xl.textContent=w.label; svg.appendChild(xl); }
  });
  let linePath=''; visibleIdx.forEach((i,k)=>{ const x=xCenter(i), y=y1Scale(weekly[i].runs); linePath+=(k===0?'M':'L')+x+','+y+' '; });
  svg.appendChild(el('path',{d:linePath.trim(),fill:'none',stroke:'#2FD480','stroke-width':2,'clip-path':`url(#${clipId})`}));
  visibleIdx.forEach(i=>{ const w=weekly[i]; const c=el('circle',{class:'data-point',cx:xCenter(i),cy:y1Scale(w.runs),r:3.5,fill:'#2FD480'}); c.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">Week of ${w.label}</div><div class="tt-row">Runs: <b>${w.runs}</b></div>`)); c.addEventListener('mousemove',positionTooltip); c.addEventListener('mouseleave',hideTooltip); svg.appendChild(c); });
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:M.left,y1:M.top,y2:M.top+plotH}));
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:W-M.right,y1:M.top+plotH,y2:M.top+plotH}));
  container.appendChild(svg);
}
function registerVolumeChart(containerId, title, weekly){
  registerZoomChart(containerId, {
    title, data:weekly, domainSize:weekly.length, minWindow:Math.min(weekly.length,4), render:renderVolumeWindow,
    rangeFmt:(data,view,zoomed)=>{ if(!data.length) return ''; const lo=Math.max(0,Math.round(view.start)), hi=Math.min(data.length-1,Math.round(view.end)); return zoomed ? `${data[lo].label} – ${data[hi].label}` : `Full history · ${data.length} weeks`; }
  });
}

function renderPlanWindow(container, planWeeks, view){
  if(!planWeeks || !planWeeks.length){ container.innerHTML="<p class='empty'>No training plan loaded.</p>"; return; }
  const {w:W,h:H}=chartSize(container,720,300), M={top:26,right:20,bottom:34,left:42};
  container._plotMargins = M;
  const plotW=W-M.left-M.right, plotH=H-M.top-M.bottom;
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,preserveAspectRatio:'none'});
  const n=planWeeks.length;
  const lo=Math.max(0,Math.floor(view.start)), hi=Math.min(n-1,Math.ceil(view.end));
  const visibleIdx=[]; for(let i=lo;i<=hi;i++) visibleIdx.push(i);
  const maxMiles=Math.max(...visibleIdx.map(i=>Math.max(planWeeks[i].plannedMi||0, planWeeks[i].actualMi||0)),1);
  const yTicks=niceTicks(0,maxMiles,5), yMax=yTicks[yTicks.length-1];
  const yScale=v=>M.top+plotH-(v/yMax)*plotH;
  const bandW=plotW/(view.end-view.start), xCenter=i=>M.left+(i-view.start)*bandW+bandW/2;
  const wkLabels=labelIndices(visibleIdx.length, plotW, widestLabelPx(visibleIdx.map(i=>planWeeks[i].weekLabel)));
  // v15 — phase shading: a faint background band per training phase, grouped
  // across consecutive same-phase weeks so a multi-week phase reads as one
  // band rather than one tint per bar. Drawn first so the planned/actual
  // bars sit visually on top of it. Low opacity — identity here is a
  // secondary encoding, the bars still carry the primary data.
  let bandStartK=0;
  for(let k=0;k<=visibleIdx.length;k++){
    const samePhase = k<visibleIdx.length && planWeeks[visibleIdx[k]].phase===planWeeks[visibleIdx[bandStartK]].phase;
    if(!samePhase){
      const i0=visibleIdx[bandStartK], i1=visibleIdx[k-1];
      const x0=M.left+(i0-view.start)*bandW, x1=M.left+(i1+1-view.start)*bandW;
      const phase=planWeeks[i0].phase;
      svg.appendChild(el('rect',{x:x0,y:M.top,width:Math.max(x1-x0,0),height:plotH,fill:phaseColor(phase),'fill-opacity':0.09}));
      if((x1-x0)>36){
        const lbl=el('text',{x:(x0+x1)/2,y:M.top+12,'text-anchor':'middle'});
        lbl.style.fill=phaseColor(phase); lbl.style.fontWeight='600'; lbl.style.fontSize='9.5px'; lbl.style.textTransform='uppercase'; lbl.style.letterSpacing='0.04em';
        lbl.textContent=phase;
        svg.appendChild(lbl);
      }
      bandStartK=k;
    }
  }
  yTicks.forEach(t=>{ svg.appendChild(el('line',{class:'grid-line',x1:M.left,x2:W-M.right,y1:yScale(t),y2:yScale(t)})); const lbl=el('text',{x:M.left-8,y:yScale(t)+3,'text-anchor':'end'}); lbl.textContent=t; svg.appendChild(lbl); });
  const yTitle=el('text',{x:10,y:12}); yTitle.textContent='miles'; svg.appendChild(yTitle);
  const plannedBarW=Math.min(bandW*0.34,60), actualBarW=Math.min(bandW*0.34,60);
  // v15 — race-day marker: TRAINING_PLAN's last week is always race week (see
  // the Python plan constant), so flag it with a dashed line + flag glyph
  // rather than trying to place a day-precise marker on a weekly-bucketed axis.
  const raceWeekIdx = planWeeks.length-1;
  visibleIdx.forEach((i,k)=>{
    const w=planWeeks[i], cx=xCenter(i);
    const pBarX=cx-plannedBarW-2, pBarY=yScale(w.plannedMi||0);
    const pBar=el('rect',{class:'data-point',x:pBarX,y:pBarY,width:plannedBarW,height:(M.top+plotH)-pBarY,fill:'#7E8EA3',rx:2});
    pBar.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">Week of ${w.weekLabel}</div><div class="tt-row">${w.phase}</div><div class="tt-row">Planned: <b>${(w.plannedMi||0).toFixed(1)}mi</b></div>`));
    pBar.addEventListener('mousemove',positionTooltip); pBar.addEventListener('mouseleave',hideTooltip);
    svg.appendChild(pBar);
    if(w.actualMi!=null){
      const aBarX=cx+2, aBarY=yScale(w.actualMi);
      const aBar=el('rect',{class:'data-point',x:aBarX,y:aBarY,width:actualBarW,height:(M.top+plotH)-aBarY,fill:'#00B4E0',rx:2});
      aBar.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">Week of ${w.weekLabel}</div><div class="tt-row">${w.phase}</div><div class="tt-row">Actual: <b>${w.actualMi.toFixed(1)}mi</b></div>${w.adherencePct!=null?`<div class="tt-row">Adherence: <b>${w.adherencePct}%</b></div>`:''}`));
      aBar.addEventListener('mousemove',positionTooltip); aBar.addEventListener('mouseleave',hideTooltip);
      svg.appendChild(aBar);
    }
    if(i===raceWeekIdx){
      const flag=el('text',{x:cx,y:M.top-10,'text-anchor':'middle'});
      flag.style.fontSize='13px'; flag.textContent='🏁';
      flag.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">Race Day</div><div class="tt-row">${w.weekLabel} — ${DATA.meta.raceName}</div>`));
      flag.addEventListener('mousemove',positionTooltip); flag.addEventListener('mouseleave',hideTooltip);
      svg.appendChild(flag);
      svg.appendChild(el('line',{x1:cx,x2:cx,y1:M.top,y2:M.top+plotH,stroke:'#FF5A64','stroke-width':1.5,'stroke-dasharray':'3,3'}));
    }
    if(wkLabels.has(k)){ const xl=el('text',{x:cx,y:H-M.bottom+16,'text-anchor':'middle'}); xl.textContent=w.weekLabel; svg.appendChild(xl); }
  });
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:M.left,y1:M.top,y2:M.top+plotH}));
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:W-M.right,y1:M.top+plotH,y2:M.top+plotH}));
  container.appendChild(svg);
}
function registerPlanChart(containerId, title, planWeeks){
  registerZoomChart(containerId, {
    title, data:planWeeks, domainSize:planWeeks.length, minWindow:Math.min(planWeeks.length,4), render:renderPlanWindow,
    rangeFmt:(data,view,zoomed)=>{ if(!data.length) return ''; const lo=Math.max(0,Math.round(view.start)), hi=Math.min(data.length-1,Math.round(view.end)); return zoomed ? `${data[lo].weekLabel} – ${data[hi].weekLabel}` : `Full plan · ${data.length} weeks`; }
  });
}

function renderPaceWindow(container, runs, view){
  if(runs.length<2){ container.innerHTML="<p class='empty'>Not enough paced runs yet.</p>"; return; }
  const {w:W,h:H}=chartSize(container,720,300), M={top:26,right:20,bottom:34,left:50};
  container._plotMargins = M;
  const plotW=W-M.left-M.right, plotH=H-M.top-M.bottom;
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,preserveAspectRatio:'none'});
  const n=runs.length;
  const lo=Math.max(0,Math.floor(view.start)), hi=Math.min(n-1,Math.ceil(view.end));
  const visibleIdx=[]; for(let i=lo;i<=hi;i++) visibleIdx.push(i);
  // The rolling average is computed over the FULL series (up to 4 runs before
  // each point) so the trend line at the left edge of a zoomed window still
  // reflects real prior history instead of restarting from whatever's on screen.
  const rolling=runs.map((r,i)=>{ const w=runs.slice(Math.max(0,i-4),i+1); return w.reduce((s,x)=>s+x.paceMinMi,0)/w.length; });
  const paces=visibleIdx.map(i=>runs[i].paceMinMi);
  const minP=Math.min(...paces)-0.6, maxP=Math.max(...paces)+0.6;
  const yTicks=niceTicks(minP,maxP,5);
  const yScale=v=>M.top+((v-yTicks[0])/(yTicks[yTicks.length-1]-yTicks[0]))*plotH;
  const xScale=i=>M.left+((i-view.start)/(view.end-view.start))*plotW;
  yTicks.forEach(t=>{ const y=yScale(t); svg.appendChild(el('line',{class:'grid-line',x1:M.left,x2:W-M.right,y1:y,y2:y})); const lbl=el('text',{x:M.left-8,y:y+3,'text-anchor':'end'}); lbl.textContent=paceStr(t); svg.appendChild(lbl); });
  const yTitle=el('text',{x:6,y:12}); yTitle.textContent='min/mile'; svg.appendChild(yTitle);
  const paceLabels=labelIndices(visibleIdx.length, plotW, widestLabelPx(visibleIdx.map(i=>runs[i].dateLabel)));
  visibleIdx.forEach((i,k)=>{ if(paceLabels.has(k)){ const xl=el('text',{x:xScale(i),y:H-M.bottom+16,'text-anchor':'middle'}); xl.textContent=runs[i].dateLabel; svg.appendChild(xl); } });
  const clipId='pace-clip-'+Math.random().toString(36).slice(2);
  const clip=el('clipPath',{id:clipId}); clip.appendChild(el('rect',{x:M.left,y:M.top,width:plotW,height:plotH})); svg.appendChild(clip);
  let path=''; visibleIdx.forEach((i,k)=>{ path+=(k===0?'M':'L')+xScale(i)+','+yScale(rolling[i])+' '; });
  svg.appendChild(el('path',{d:path.trim(),fill:'none',stroke:'#E6EDF5','stroke-width':1.5,'stroke-dasharray':'4,3','clip-path':`url(#${clipId})`}));
  visibleIdx.forEach(i=>{ const r=runs[i]; const c=el('circle',{class:'data-point',cx:xScale(i),cy:yScale(r.paceMinMi),r:5,fill:TYPE_COLORS[r.type]||'#7E8EA3'}); c.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">${r.name}</div><div class="tt-row">${fmtDate(r.date)} · ${r.type}</div><div class="tt-row">Pace: <b>${paceStr(r.paceMinMi)}/mi</b></div><div class="tt-row">Dist: <b>${r.distMi.toFixed(1)}mi</b></div>`)); c.addEventListener('mousemove',positionTooltip); c.addEventListener('mouseleave',hideTooltip); svg.appendChild(c); });
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:M.left,y1:M.top,y2:M.top+plotH}));
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:W-M.right,y1:M.top+plotH,y2:M.top+plotH}));
  container.appendChild(svg);
}
function registerPaceChart(containerId, title, pacedRuns){
  registerZoomChart(containerId, {
    title, data:pacedRuns, domainSize:pacedRuns.length, minWindow:Math.min(pacedRuns.length,5), render:renderPaceWindow,
    rangeFmt:(data,view,zoomed)=>{ if(!data.length) return ''; const lo=Math.max(0,Math.round(view.start)), hi=Math.min(data.length-1,Math.round(view.end)); return zoomed ? `${fmtDate(data[lo].date)} – ${fmtDate(data[hi].date)} · ${hi-lo+1} runs` : `Full history · ${data.length} runs`; }
  });
}

function renderSeriesWindow(container, pts, view, valueKey, color){
  if(pts.length<2){ container.innerHTML="<p class='empty'>Not enough data yet.</p>"; return; }
  const {w:W,h:H}=chartSize(container,420,190), M={top:12,right:12,bottom:26,left:32};
  container._plotMargins = M;
  const plotW=W-M.left-M.right, plotH=H-M.top-M.bottom;
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,preserveAspectRatio:'none'});
  const n=pts.length;
  const lo=Math.max(0,Math.floor(view.start)), hi=Math.min(n-1,Math.ceil(view.end));
  const visibleIdx=[]; for(let i=lo;i<=hi;i++) visibleIdx.push(i);
  const vals=visibleIdx.map(i=>pts[i][valueKey]);
  const yTicks=niceTicks(Math.min(...vals)-1,Math.max(...vals)+1,4);
  const yMin=yTicks[0], yMax=yTicks[yTicks.length-1];
  const yScale=v=>M.top+plotH-((v-yMin)/(yMax-yMin))*plotH;
  const xScale=i=>M.left+((i-view.start)/(view.end-view.start))*plotW;
  yTicks.forEach(t=>{ const y=yScale(t); svg.appendChild(el('line',{class:'grid-line',x1:M.left,x2:W-M.right,y1:y,y2:y})); const lbl=el('text',{x:M.left-6,y:y+3,'text-anchor':'end'}); lbl.textContent=t; svg.appendChild(lbl); });
  const seriesLabels=labelIndices(visibleIdx.length, plotW, widestLabelPx(visibleIdx.map(i=>fmtDate(pts[i].date))));
  visibleIdx.forEach((i,k)=>{ if(seriesLabels.has(k)){ const xl=el('text',{x:xScale(i),y:H-M.bottom+14,'text-anchor':'middle'}); xl.textContent=fmtDate(pts[i].date); svg.appendChild(xl); } });
  const clipId='series-clip-'+Math.random().toString(36).slice(2);
  const clip=el('clipPath',{id:clipId}); clip.appendChild(el('rect',{x:M.left,y:M.top,width:plotW,height:plotH})); svg.appendChild(clip);
  let linePath='', areaPath='';
  visibleIdx.forEach((i,k)=>{ const x=xScale(i), y=yScale(pts[i][valueKey]); linePath+=(k===0?'M':'L')+x+','+y+' '; areaPath+=(k===0?'M':'L')+x+','+y+' '; });
  areaPath+=`L${xScale(visibleIdx[visibleIdx.length-1])},${M.top+plotH} L${xScale(visibleIdx[0])},${M.top+plotH} Z`;
  svg.appendChild(el('path',{d:areaPath,fill:color+'22',stroke:'none','clip-path':`url(#${clipId})`}));
  svg.appendChild(el('path',{d:linePath.trim(),fill:'none',stroke:color,'stroke-width':2,'clip-path':`url(#${clipId})`}));
  visibleIdx.forEach(i=>{ const p=pts[i]; const c=el('circle',{class:'data-point',cx:xScale(i),cy:yScale(p[valueKey]),r:3,fill:color,opacity:0.9}); const extra = p.status? `<div class="tt-row">${p.status}</div>` : ''; c.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">${fmtDate(p.date)}</div><div class="tt-row">${p[valueKey]}</div>${extra}`)); c.addEventListener('mousemove',positionTooltip); c.addEventListener('mouseleave',hideTooltip); svg.appendChild(c); });
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:M.left,y1:M.top,y2:M.top+plotH}));
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:W-M.right,y1:M.top+plotH,y2:M.top+plotH}));
  container.appendChild(svg);
}
function registerSeriesChart(containerId, title, pts, valueKey, color){
  registerZoomChart(containerId, {
    title, data:pts, domainSize:pts.length, minWindow:Math.min(pts.length,5),
    render:(container,data,view)=>renderSeriesWindow(container,data,view,valueKey,color),
    rangeFmt:(data,view,zoomed)=>{ if(!data.length) return ''; const lo=Math.max(0,Math.round(view.start)), hi=Math.min(data.length-1,Math.round(view.end)); return zoomed ? `${fmtDate(data[lo].date)} – ${fmtDate(data[hi].date)}` : `Full history · ${data.length} points`; }
  });
}

let SPLITS_SYNC_TARGETS = {};
// v15 hotfix — a click PINS the synced point instead of only previewing it on
// hover: keyed by syncId, holds the fraction-of-total-distance that was
// explicitly clicked (on either the chart or the map), or null when nothing
// is pinned. A pinned point ignores the normal hover-driven .clear() calls
// from both sides, so it stays put after the pointer moves away — mouse users
// get hover-as-before plus a way to hold a point in place, and touch users
// (who have no real hover) get a point that sticks after a tap. Clicking the
// SAME already-pinned point again un-pins it.
let SYNC_PINNED = {};

// =====================================================================
// v16 — This Week's Plan: week browsing, manual completion, notes, and
// drag-and-drop day swaps, all persisted back to GitHub via GitHubStore
// below. The day-level status/matching logic in computeWeekView()
// deliberately duplicates Python's build_plan_comparison day-status
// algorithm rather than reusing DATA.planComparison's own per-day
// `sessions` field — this is now the one place responsible for what's
// displayed, so a same-session edit (a checkbox, a swap) shows instantly
// without waiting for tomorrow's sync, and there's no second copy of the
// logic to drift out of sync with this one. The WEEK-LEVEL fields
// (plannedMi/actualMi/adherencePct/status) are untouched by any of this —
// overrides only change which workout displays on which day, never the
// mileage math, so they're read straight from DATA.planComparison as-is.
// =====================================================================
let MANUAL_DATA = DATA.manualData || {};
if(!MANUAL_DATA.scheduleOverrides) MANUAL_DATA.scheduleOverrides = {};
if(!MANUAL_DATA.manualLogs) MANUAL_DATA.manualLogs = {};
if(!MANUAL_DATA.notes) MANUAL_DATA.notes = {};
const TRACKABLE_TYPES_JS = new Set(DATA.trackableTypes || ['Intervals','Tempo','Long Run','Easy','Race']);
let WEEK_VIEW_IDX = null;
let _ALL_RUNS_CACHE = null;
function allRunsAsc(){
  if(!_ALL_RUNS_CACHE) _ALL_RUNS_CACHE = [...DATA.runs].sort((a,b)=> a.date < b.date ? -1 : a.date > b.date ? 1 : 0);
  return _ALL_RUNS_CACHE;
}
function isoAddDays(iso, n){
  const d = new Date(iso+'T12:00:00');
  d.setDate(d.getDate()+n);
  return d.toISOString().slice(0,10);
}
function daysBetweenIso(a, b){
  return Math.round((new Date(b+'T12:00:00') - new Date(a+'T12:00:00'))/86400000);
}

function computeWeekView(weekIdx){
  const meta = (DATA.planComparison||[])[weekIdx];
  const raw = (DATA.rawTrainingPlan||[])[weekIdx];
  if(!meta || !raw) return null;
  const weekStartIso = meta.weekStart;
  const overrides = MANUAL_DATA.scheduleOverrides[weekStartIso] || {};
  const todayIso = DATA.meta.lastSynced;
  const runs = allRunsAsc();
  const sessions = {};
  DAY_ORDER.forEach((dk, offset) => {
    const sourceKey = overrides[dk] || dk;
    const planned = raw.sessions[sourceKey];
    if(!planned) return;
    const targetDate = isoAddDays(weekStartIso, offset);
    const trackable = TRACKABLE_TYPES_JS.has(planned.type);
    const dayIsFuture = targetDate > todayIso;
    let match = null;
    if(trackable && !dayIsFuture){
      const candidates = runs.filter(r => Math.abs(daysBetweenIso(r.date, targetDate)) <= 1);
      if(candidates.length){
        match = candidates.reduce((best,r)=> Math.abs(daysBetweenIso(r.date,targetDate)) < Math.abs(daysBetweenIso(best.date,targetDate)) ? r : best);
      }
    }
    let dayStatus = !trackable ? 'not-tracked' : (dayIsFuture ? 'upcoming' : (match ? 'done' : 'missed'));
    const manualEntry = MANUAL_DATA.manualLogs[targetDate];
    const manualDone = !!(manualEntry && manualEntry.done);
    if(manualDone && !dayIsFuture) dayStatus = 'done';
    sessions[dk] = {
      type: planned.type, title: planned.title, detail: planned.detail, targetMi: planned.targetMi,
      date: targetDate, trackable, dayStatus, dayIsFuture,
      actualMi: match ? Math.round(match.distMi*100)/100 : null,
      actualPace: match ? match.paceMinMi : null,
      matched: !!match, manualDone,
      note: MANUAL_DATA.notes[targetDate] || '',
      swapped: sourceKey !== dk, sourceKey,
    };
  });
  return { weekIdx, weekStartIso, weekEnd: meta.weekEnd, phase: meta.phase, plannedMi: meta.plannedMi,
    actualMi: meta.actualMi, adherencePct: meta.adherencePct, raceDayMi: meta.raceDayMi, sessions };
}

function swapDays(weekIdx, dayA, dayB){
  const meta = (DATA.planComparison||[])[weekIdx];
  if(!meta || dayA===dayB) return;
  const weekStartIso = meta.weekStart;
  const overrides = MANUAL_DATA.scheduleOverrides[weekStartIso] || {};
  const currentA = overrides[dayA] || dayA;
  const currentB = overrides[dayB] || dayB;
  const next = {...overrides};
  if(currentB === dayA) delete next[dayA]; else next[dayA] = currentB;
  if(currentA === dayB) delete next[dayB]; else next[dayB] = currentA;
  Object.keys(next).forEach(k=>{ if(next[k]===k) delete next[k]; });
  if(Object.keys(next).length) MANUAL_DATA.scheduleOverrides[weekStartIso] = next;
  else delete MANUAL_DATA.scheduleOverrides[weekStartIso];
  renderThisWeekPanel();
  GitHubStore.save(d=>{
    d.scheduleOverrides = d.scheduleOverrides || {};
    if(Object.keys(next).length) d.scheduleOverrides[weekStartIso] = next;
    else delete d.scheduleOverrides[weekStartIso];
    return d;
  }, `Swap ${dayA} and ${dayB} for week of ${weekStartIso}`).catch(()=>{});
}

function resetWeekSchedule(weekStartIso){
  delete MANUAL_DATA.scheduleOverrides[weekStartIso];
  renderThisWeekPanel();
  GitHubStore.save(d=>{ d.scheduleOverrides = d.scheduleOverrides||{}; delete d.scheduleOverrides[weekStartIso]; return d; }, `Reset schedule for week of ${weekStartIso}`).catch(()=>{});
}

// ---- GitHub write-back (v16) — see the setup guide's v16 section. Repo
// owner/name are read from the page's own URL (the standard
// https://OWNER.github.io/REPO/ GitHub Pages shape) rather than hardcoded,
// so there's nothing to configure beyond the token itself. ----
const GitHubStore = (function(){
  const FILE_PATH = 'manual_data.json';
  let cachedSha = null;
  let queue = Promise.resolve();

  function ownerRepo(){
    const host = location.hostname;
    const owner = host.endsWith('.github.io') ? host.slice(0, -('.github.io'.length)) : host;
    const seg = (location.pathname.split('/').filter(Boolean))[0];
    const repo = seg || (owner + '.github.io');
    return {owner, repo};
  }
  function token(){ return (DATA.meta && DATA.meta.githubWriteToken) || ''; }
  function apiUrl(){ const {owner,repo} = ownerRepo(); return `https://api.github.com/repos/${owner}/${repo}/contents/${FILE_PATH}`; }
  function b64encode(str){ return btoa(unescape(encodeURIComponent(str))); }
  function b64decode(str){ return decodeURIComponent(escape(atob(str.replace(/\n/g,'')))); }

  async function readCurrent(){
    const res = await fetch(apiUrl(), { headers: { 'Authorization': `Bearer ${token()}`, 'Accept':'application/vnd.github+json' } });
    if(res.status === 404){ cachedSha = null; return {scheduleOverrides:{}, manualLogs:{}, notes:{}}; }
    if(!res.ok) throw new Error('GitHub read failed: '+res.status);
    const body = await res.json();
    cachedSha = body.sha;
    try{
      const parsed = JSON.parse(b64decode(body.content));
      return { scheduleOverrides: parsed.scheduleOverrides||{}, manualLogs: parsed.manualLogs||{}, notes: parsed.notes||{} };
    }catch(e){ return {scheduleOverrides:{}, manualLogs:{}, notes:{}}; }
  }

  async function writeOnce(mutate, message){
    const current = await readCurrent();
    const next = mutate(JSON.parse(JSON.stringify(current)));
    const payload = { message, content: b64encode(JSON.stringify(next, null, 2)) };
    if(cachedSha) payload.sha = cachedSha;
    const res = await fetch(apiUrl(), {
      method: 'PUT',
      headers: { 'Authorization': `Bearer ${token()}`, 'Accept':'application/vnd.github+json', 'Content-Type':'application/json' },
      body: JSON.stringify(payload),
    });
    if(res.status === 409 || res.status === 422){ const e = new Error('conflict'); e.retry = true; throw e; }
    if(!res.ok){ const e = new Error('GitHub write failed: '+res.status); throw e; }
    const body = await res.json();
    cachedSha = body.content && body.content.sha;
  }

  async function doSave(mutate, message){
    setSaveStatus('saving');
    let attempt = 0, lastErr = null;
    while(attempt < 3){
      try{ await writeOnce(mutate, message); setSaveStatus('saved'); return true; }
      catch(e){ lastErr = e; attempt++; if(!(e && e.retry) || attempt >= 3) break; }
    }
    setSaveStatus('error');
    _lastFailedSave = {mutate, message};
    throw lastErr;
  }

  function save(mutate, message){
    if(!token()){ setSaveStatus('no-token'); return Promise.reject(new Error('no token configured')); }
    queue = queue.then(() => doSave(mutate, message), () => doSave(mutate, message));
    return queue;
  }

  return { save, ownerRepo };
})();

let _lastFailedSave = null;
let _saveStatusTimer = null;
function setSaveStatus(state){
  const el = document.getElementById('week-save-status');
  if(!el) return;
  el.classList.remove('is-saving','is-error');
  clearTimeout(_saveStatusTimer);
  if(state==='saving'){ el.textContent = 'Saving…'; el.classList.add('is-saving'); }
  else if(state==='saved'){ el.textContent = 'All changes saved'; _saveStatusTimer = setTimeout(()=>{ if(el.textContent==='All changes saved') el.textContent=''; }, 4000); }
  else if(state==='error'){ el.textContent = "Couldn't save — tap to retry"; el.classList.add('is-error'); }
  else if(state==='no-token'){ el.textContent = 'Editing not set up — see setup guide'; }
}

function wireDayCardDrag(panel, weekIdx, view){
  let dragState = null;
  let ghostEl = null;
  let overTarget = null;

  function cleanup(){
    if(dragState && dragState.longPressTimer) clearTimeout(dragState.longPressTimer);
    if(dragState) dragState.card.classList.remove('drag-dragging');
    if(overTarget) overTarget.classList.remove('drag-over');
    if(ghostEl){ ghostEl.remove(); ghostEl = null; }
    overTarget = null;
    dragState = null;
  }
  function moveGhost(x,y){ if(ghostEl){ ghostEl.style.left = x+'px'; ghostEl.style.top = y+'px'; } }
  function startDrag(card, dayKey, x, y){
    dragState.dragging = true;
    card.classList.add('drag-dragging');
    const s = view.sessions[dayKey];
    ghostEl = document.createElement('div');
    ghostEl.className = 'drag-ghost';
    ghostEl.textContent = s ? s.title : DAY_NAMES[dayKey];
    document.body.appendChild(ghostEl);
    moveGhost(x, y);
  }

  panel.querySelectorAll('.week-day-card').forEach(card=>{
    const dayKey = card.getAttribute('data-day-key');
    card.addEventListener('pointerdown', (e)=>{
      if(e.target.closest('.wd-check-row')) return;
      if(e.pointerType === 'mouse' && e.button !== 0) return;
      cleanup();
      // Capture the pointer on this specific card so pointermove/pointerup keep
      // targeting it even once the cursor/finger moves over a different card —
      // without this, each card's own listener only sees events while the
      // pointer is still physically inside its bounding box, which breaks
      // dragging the instant you cross into a neighboring card.
      try{ card.setPointerCapture(e.pointerId); }catch(err){}
      dragState = { card, dayKey, startX:e.clientX, startY:e.clientY, lastX:e.clientX, lastY:e.clientY, dragging:false, pointerType:e.pointerType };
      if(e.pointerType !== 'mouse'){
        dragState.longPressTimer = setTimeout(()=>{
          if(dragState && dragState.card===card && !dragState.dragging){
            const dx = Math.abs(dragState.lastX - dragState.startX), dy = Math.abs(dragState.lastY - dragState.startY);
            if(dx < 10 && dy < 10) startDrag(card, dayKey, dragState.lastX, dragState.lastY);
          }
        }, 420);
      }
    });
    card.addEventListener('pointermove', (e)=>{
      if(!dragState || dragState.card !== card) return;
      dragState.lastX = e.clientX; dragState.lastY = e.clientY;
      if(!dragState.dragging){
        if(dragState.pointerType === 'mouse'){
          const dx = Math.abs(e.clientX - dragState.startX), dy = Math.abs(e.clientY - dragState.startY);
          if(dx > 6 || dy > 6) startDrag(card, dragState.dayKey, e.clientX, e.clientY);
        }
        return;
      }
      e.preventDefault();
      moveGhost(e.clientX, e.clientY);
      const el = document.elementFromPoint(e.clientX, e.clientY);
      const targetCard = el ? el.closest('.week-day-card') : null;
      if(overTarget && overTarget !== targetCard){ overTarget.classList.remove('drag-over'); overTarget = null; }
      if(targetCard && targetCard !== dragState.card && panel.contains(targetCard)){ targetCard.classList.add('drag-over'); overTarget = targetCard; }
    }, {passive:false});
    const finish = ()=>{
      if(!dragState || dragState.card !== card) return;
      if(dragState.dragging && overTarget){
        const targetKey = overTarget.getAttribute('data-day-key');
        card.dataset.justDragged = '1';
        swapDays(weekIdx, dayKey, targetKey);
      }
      cleanup();
    };
    card.addEventListener('pointerup', finish);
    card.addEventListener('pointercancel', cleanup);
  });
}

function openDayModal(weekIdx, dayKey){
  const view = computeWeekView(weekIdx);
  if(!view) return;
  const s = view.sessions[dayKey];
  if(!s) return;
  const body = document.getElementById('day-modal-body');
  let actualHtml = '';
  if(s.trackable && s.dayStatus==='done' && s.actualMi!=null){
    actualHtml = `<div class="day-modal-actual">Actual: <b>${s.actualMi.toFixed(2)}mi</b>${s.actualPace?` @ ${paceStr(s.actualPace)}/mi`:''}</div>`;
  } else if(s.manualDone){
    actualHtml = `<div class="day-modal-actual" style="color:var(--amber)">Marked done manually.</div>`;
  } else if(s.dayStatus==='missed'){
    actualHtml = `<div class="day-modal-actual" style="color:var(--clay)">No matching Garmin activity found within a day of this date.</div>`;
  }
  const swapOptions = DAY_ORDER.filter(d=>d!==dayKey).map(d=>{
    const other = view.sessions[d];
    return `<option value="${d}">${DAY_NAMES[d]} — ${other?other.title:'—'}</option>`;
  }).join('');
  const showCheckbox = !s.dayIsFuture && s.type !== 'Race' && (s.dayStatus!=='upcoming');

  body.innerHTML = `
    <span class="day-modal-type" style="background:${planTypeColor(s.type)}">${s.type}</span>
    <div class="day-modal-title">${s.title}</div>
    <div class="day-modal-date">${DAY_NAMES[dayKey]} &middot; ${fmtDate(s.date)}${s.swapped?' &middot; swapped from the original plan':''}</div>
    ${actualHtml}
    <div class="day-modal-detail">${s.detail}</div>
    ${showCheckbox?`<label class="wd-check-row" style="margin-bottom:16px;"><input type="checkbox" id="day-modal-check" ${s.manualDone?'checked':''}> Mark this day done</label>`:''}
    <div class="day-modal-swap-row">
      <span>Swap with:</span>
      <select id="day-modal-swap-select"><option value="">Choose a day…</option>${swapOptions}</select>
    </div>
    <div class="day-modal-notes">
      <div class="modal-section-title" style="margin-top:0;">Notes</div>
      <textarea id="day-modal-notes-text" placeholder="Anything worth remembering about this session…">${s.note}</textarea>
      <div class="day-modal-notes-footer">
        <span class="week-save-status" id="day-modal-save-status"></span>
        <button type="button" class="day-modal-save-btn" id="day-modal-save-btn">Save note</button>
      </div>
    </div>
  `;
  document.getElementById('day-modal').style.display = 'flex';

  const checkEl = document.getElementById('day-modal-check');
  if(checkEl) checkEl.addEventListener('change', (e)=>{
    const checked = e.target.checked;
    MANUAL_DATA.manualLogs[s.date] = MANUAL_DATA.manualLogs[s.date] || {};
    MANUAL_DATA.manualLogs[s.date].done = checked;
    renderThisWeekPanel();
    openDayModal(weekIdx, dayKey); // refresh the modal's own "actual" text/status to match
    GitHubStore.save(d=>{ d.manualLogs = d.manualLogs||{}; d.manualLogs[s.date] = d.manualLogs[s.date]||{}; d.manualLogs[s.date].done = checked; return d; }, `Mark ${s.date} ${checked?'done':'not done'}`).catch(()=>{});
  });
  document.getElementById('day-modal-swap-select').addEventListener('change', (e)=>{
    const otherKey = e.target.value;
    if(!otherKey) return;
    swapDays(weekIdx, dayKey, otherKey);
    closeDayModal();
  });
  const saveBtn = document.getElementById('day-modal-save-btn');
  const statusEl = document.getElementById('day-modal-save-status');
  saveBtn.addEventListener('click', ()=>{
    const text = document.getElementById('day-modal-notes-text').value;
    MANUAL_DATA.notes[s.date] = text;
    saveBtn.disabled = true;
    statusEl.textContent = 'Saving…'; statusEl.classList.remove('is-error'); statusEl.classList.add('is-saving');
    GitHubStore.save(d=>{ d.notes = d.notes||{}; d.notes[s.date] = text; return d; }, `Update note for ${s.date}`)
      .then(()=>{ statusEl.textContent='Saved ✓'; statusEl.classList.remove('is-saving'); saveBtn.disabled=false; renderThisWeekPanel(); })
      .catch(()=>{ statusEl.textContent="Couldn't save — try again"; statusEl.classList.remove('is-saving'); statusEl.classList.add('is-error'); saveBtn.disabled=false; });
  });
}
function closeDayModal(){ const m = document.getElementById('day-modal'); if(m) m.style.display='none'; }

function renderThisWeekPanel(){
  const plan = DATA.planComparison || [];
  const panel = document.getElementById('this-week-panel');
  if(!panel) return;
  if(!plan.length){ panel.innerHTML = "<p class='empty'>No training plan configured.</p>"; return; }
  const todayIso = DATA.meta.lastSynced;
  const currentIdx = plan.findIndex(w => w.weekStart <= todayIso && todayIso <= w.weekEnd);
  const idx = Math.max(0, Math.min(WEEK_VIEW_IDX==null ? (currentIdx===-1?0:currentIdx) : WEEK_VIEW_IDX, plan.length-1));
  WEEK_VIEW_IDX = idx;
  const view = computeWeekView(idx);
  if(!view){ panel.innerHTML = "<p class='empty'>No active plan week right now — see the full plan below.</p>"; return; }
  const prevWeekMeta = idx>0 ? plan[idx-1] : null;
  const isCurrent = idx === currentIdx;

  const recapBits = [`<span>Week ${idx+1} of ${plan.length} &middot; <b>${view.phase}</b></span>`];
  if(view.adherencePct!=null) recapBits.push(`<span>${view.adherencePct}% of weekly target${isCurrent?' so far':''} (<b>${(view.actualMi||0).toFixed(1)}</b> / ${view.plannedMi.toFixed(1)}mi)</span>`);
  else recapBits.push(`<span>${currentIdx!==-1 && idx > currentIdx ? 'Upcoming' : 'No running logged'}</span>`);
  if(prevWeekMeta && prevWeekMeta.actualMi!=null && view.actualMi!=null){
    const delta = view.actualMi - prevWeekMeta.actualMi;
    recapBits.push(`<span>${delta>=0?'+':''}${delta.toFixed(1)}mi vs prior week (<b>${prevWeekMeta.actualMi.toFixed(1)}mi</b>)</span>`);
  }
  if(view.raceDayMi) recapBits.push(`<span>Race day this week 🏁</span>`);

  const hasOverrides = !!(MANUAL_DATA.scheduleOverrides[view.weekStartIso] && Object.keys(MANUAL_DATA.scheduleOverrides[view.weekStartIso]).length);

  const navRow = `<div class="week-nav-row">
    <button type="button" class="week-nav-btn" id="week-nav-prev" ${idx<=0?'disabled':''}>&larr; Prev week</button>
    <div class="week-nav-label">${fmtDate(view.weekStartIso)} – ${fmtDate(view.weekEnd)}${!isCurrent?` &middot; <button type="button" class="week-nav-jump" id="week-nav-jump">Jump to this week</button>`:''}</div>
    <button type="button" class="week-nav-btn" id="week-nav-next" ${idx>=plan.length-1?'disabled':''}>Next week &rarr;</button>
  </div>`;

  const dayCards = DAY_ORDER.map(dk=>{
    const s = view.sessions[dk];
    if(!s) return '';
    const isToday = s.date === todayIso;
    let sub = s.detail;
    if(s.trackable && s.dayStatus==='done' && s.actualMi!=null){
      sub = `Planned ${s.targetMi?s.targetMi.toFixed(2)+'mi':'—'} &rarr; <b>${s.actualMi.toFixed(2)}mi</b>${s.actualPace?` @ ${paceStr(s.actualPace)}/mi`:''}`;
    } else if(s.manualDone && !s.trackable){
      sub = `${s.detail} — <span style="color:var(--amber)">logged manually</span>`;
    }
    const icon = s.dayStatus==='done' ? '✓' : (s.dayStatus==='missed' ? '!' : (s.type==='Race' ? '🏁' : ''));
    const showCheckbox = !s.dayIsFuture && s.type !== 'Race' && (s.dayStatus==='not-tracked' || s.dayStatus==='missed' || s.manualDone);
    const checkboxHtml = showCheckbox ? `<label class="wd-check-row" onclick="event.stopPropagation()">
        <input type="checkbox" data-day-check="${dk}" data-date="${s.date}" ${s.manualDone?'checked':''}> Mark done
      </label>` : '';
    return `<div class="week-day-card ${isToday?'is-today':''} status-${s.dayStatus}" data-day-key="${dk}">
      ${icon?`<span class="wd-status-icon">${icon}</span>`:''}
      ${s.note?`<span class="wd-note-dot" title="Has a note">&#9998;</span>`:''}
      <div class="wd-name">${DAY_NAMES[dk]} &middot; ${fmtDate(s.date)}${isToday?'<span class="wd-today-chip">TODAY</span>':''}</div>
      <span class="wd-type" style="background:${planTypeColor(s.type)}">${s.type}</span>
      <div class="wd-title">${s.title}</div>
      <div class="wd-sub">${sub}</div>
      ${s.swapped?`<span class="wd-swapped-tag">swapped</span>`:''}
      ${checkboxHtml}
    </div>`;
  }).join('');

  const resetLink = hasOverrides ? `<button type="button" class="week-reset-link" id="week-reset-schedule">↺ Reset this week's schedule to the original plan</button>` : '';

  panel.innerHTML = `${navRow}<div class="week-recap">${recapBits.join('')}</div><div class="week-days">${dayCards}</div>${resetLink}
    <div style="display:flex; justify-content:flex-end; margin-top:10px;"><span class="week-save-status" id="week-save-status"></span></div>`;

  const prevBtn = document.getElementById('week-nav-prev');
  const nextBtn = document.getElementById('week-nav-next');
  if(prevBtn) prevBtn.addEventListener('click', ()=>{ WEEK_VIEW_IDX = Math.max(0, idx-1); renderThisWeekPanel(); });
  if(nextBtn) nextBtn.addEventListener('click', ()=>{ WEEK_VIEW_IDX = Math.min(plan.length-1, idx+1); renderThisWeekPanel(); });
  const jumpBtn = document.getElementById('week-nav-jump');
  if(jumpBtn) jumpBtn.addEventListener('click', ()=>{ WEEK_VIEW_IDX = currentIdx === -1 ? 0 : currentIdx; renderThisWeekPanel(); });
  const resetBtn = document.getElementById('week-reset-schedule');
  if(resetBtn) resetBtn.addEventListener('click', ()=> resetWeekSchedule(view.weekStartIso));

  panel.querySelectorAll('[data-day-check]').forEach(cb=>{
    cb.addEventListener('change', (e)=>{
      const date = e.target.getAttribute('data-date');
      const checked = e.target.checked;
      MANUAL_DATA.manualLogs[date] = MANUAL_DATA.manualLogs[date] || {};
      MANUAL_DATA.manualLogs[date].done = checked;
      renderThisWeekPanel();
      GitHubStore.save(d=>{ d.manualLogs = d.manualLogs||{}; d.manualLogs[date] = d.manualLogs[date]||{}; d.manualLogs[date].done = checked; return d; }, `Mark ${date} ${checked?'done':'not done'}`).catch(()=>{});
    });
  });

  panel.querySelectorAll('.week-day-card').forEach(card=>{
    card.addEventListener('click', ()=>{
      if(card.dataset.justDragged === '1'){ card.dataset.justDragged = '0'; return; }
      openDayModal(idx, card.getAttribute('data-day-key'));
    });
  });

  wireDayCardDrag(panel, idx, view);
}

function renderSplitsWindow(container, splits, view, legendId, elevProfile, mileBased, syncId){
  if(mileBased===undefined) mileBased=true; // older cached data with no flag — assume the common case
  if(!splits.length){ container.innerHTML="<p class='empty'>No splits for this run.</p>"; if(legendId){ const lg=document.getElementById(legendId); if(lg) lg.innerHTML=''; } return; }
  const {w:W,h:H}=chartSize(container,720,320), M={top:28,right:20,bottom:34,left:50};
  container._plotMargins = M;
  const plotW=W-M.left-M.right, plotH=H-M.top-M.bottom;
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,preserveAspectRatio:'none'});
  const n=splits.length;
  const labelWord = mileBased ? 'Mile' : 'Lap';
  // Each split's REAL distance (falling back to 1mi/lap if Garmin didn't return
  // one) rather than assuming every lap is exactly one mile wide — see the v10
  // changelog. cum[] is the FULL run's cumulative distance at each split
  // boundary; distScale below maps only the currently-visible WINDOW of that
  // distance range to the plot width, so zooming into a few splits spreads
  // just their distance across the full chart instead of leaving them
  // compressed against the whole run's span.
  const lapDist = splits.map(s=>(s.distMi!=null && s.distMi>0) ? s.distMi : 1);
  const cum=[0]; lapDist.forEach(d=>cum.push(cum[cum.length-1]+d));

  const lo=Math.max(0,Math.floor(view.start)), hi=Math.min(n-1,Math.ceil(view.end));
  const visibleIdx=[]; for(let i=lo;i<=hi;i++) visibleIdx.push(i);
  const winStartDist=cum[lo], winEndDist=cum[hi+1], winSpan=Math.max(winEndDist-winStartDist,0.001);
  const distScale=d=>M.left+((Math.min(Math.max(d,winStartDist),winEndDist)-winStartDist)/winSpan)*plotW;
  const xCenter=i=>distScale((cum[i]+cum[i+1])/2);

  const isSemanticLabel = s => !/^[0-9.]+$/.test(String(s.mile));
  const splitLabelMinGap = widestLabelPx(visibleIdx.map(i=>{ const s=splits[i]; return isSemanticLabel(s) ? s.mile : labelWord.slice(0,mileBased?2:3)+' '+s.mile; }));
  const labelIdx=[]; let lastLabelX=-Infinity;
  visibleIdx.forEach(i=>{ const x=xCenter(i); if(x-lastLabelX>=splitLabelMinGap){ labelIdx.push(i); lastLabelX=x; } });
  if(visibleIdx.length){
    const lastI=visibleIdx[visibleIdx.length-1];
    if(labelIdx[labelIdx.length-1]!==lastI){
      const lastX=xCenter(lastI);
      if(lastX-lastLabelX>=splitLabelMinGap) labelIdx.push(lastI); else if(labelIdx.length) labelIdx[labelIdx.length-1]=lastI; else labelIdx.push(lastI);
    }
  }
  const mileLabels=new Set(labelIdx);
  const paces=visibleIdx.map(i=>splits[i].pace).filter(p=>p>0);
  const paceMin=(paces.length?Math.min(...paces):0)-0.4, paceMax=(paces.length?Math.max(...paces):1)+0.4;
  const yPace=v=>M.top+((v-paceMin)/(paceMax-paceMin))*plotH;
  const hrs=visibleIdx.map(i=>splits[i].avgHr).filter(h=>h);
  const hrTicks = hrs.length ? niceTicks(Math.min(...hrs)-5,Math.max(...hrs)+5,4) : [0,1];
  const hrMin=hrTicks[0], hrMax=hrTicks[hrTicks.length-1];
  const yHr=v=>M.top+plotH-((v-hrMin)/(hrMax-hrMin))*plotH;
  const baseline=M.top+plotH;
  const elevCapPx=plotH*0.34;
  const paceTicks=niceTicks(paceMin,paceMax,5);
  paceTicks.forEach(t=>{ const y=yPace(t); if(y<M.top-1||y>M.top+plotH+1) return; svg.appendChild(el('line',{class:'grid-line',x1:M.left,x2:W-M.right,y1:y,y2:y})); const lbl=el('text',{x:M.left-8,y:y+3,'text-anchor':'end'}); lbl.textContent=paceStr(t); svg.appendChild(lbl); });
  const yTitle=el('text',{x:6,y:12}); yTitle.textContent='min/mi'; svg.appendChild(yTitle);
  const y1Title=el('text',{x:W-M.right,y:12,'text-anchor':'end'}); y1Title.textContent='bpm'; svg.appendChild(y1Title);

  const clipId='splits-clip-'+Math.random().toString(36).slice(2);
  const clip=el('clipPath',{id:clipId}); clip.appendChild(el('rect',{x:M.left,y:M.top,width:plotW,height:plotH})); svg.appendChild(clip);

  // ---- Elevation: a sub-mile altitude trace when Garmin returned one for this run,
  // a per-mile gain line otherwise. Either way it's drawn first so pace/HR sit
  // visually on top of it, and rescaled to just the points inside the window.
  const elevColor='#5B7A99';
  const hasProfile = Array.isArray(elevProfile) && elevProfile.length>=6;
  let elevPts;
  if(hasProfile){
    const visProfile = elevProfile.filter(p=>p.distMi>=winStartDist-0.001 && p.distMi<=winEndDist+0.001);
    const usable = visProfile.length>=2 ? visProfile : elevProfile;
    const alts=usable.map(p=>p.elevFt);
    const altMin=Math.min(...alts), altMax=Math.max(...alts), range=(altMax-altMin)||1;
    elevPts = usable.map(p=>[distScale(p.distMi), baseline-((p.elevFt-altMin)/range)*elevCapPx]);
  } else {
    const maxGain=Math.max(...visibleIdx.map(i=>splits[i].elevGainFt||0),1);
    elevPts = visibleIdx.map(i=>[xCenter(i), baseline-((splits[i].elevGainFt||0)/maxGain)*elevCapPx]);
  }
  if(elevPts.length){
    const elevLine = elevPts.reduce((d,p,i)=>d+(i===0?'M':'L')+p[0]+','+p[1]+' ','');
    const elevArea = elevLine + `L${elevPts[elevPts.length-1][0]},${baseline} L${elevPts[0][0]},${baseline} Z`;
    svg.appendChild(el('path',{d:elevArea, fill:elevColor+'2e', stroke:'none', 'clip-path':`url(#${clipId})`}));
    svg.appendChild(el('path',{d:elevLine, fill:'none', stroke:elevColor, 'stroke-width':hasProfile?1.3:1.5, 'stroke-linejoin':'round', 'clip-path':`url(#${clipId})`}));
  }

  // One hover target + one x-axis label per visible split. Each split's
  // tooltip title includes its real distance for a "Lap" (not a "Mile") since
  // "Lap 3" alone doesn't tell you it was a 0.52mi rep.
  const splitTitle = s => { const word = isSemanticLabel(s) ? s.mile : `${labelWord} ${s.mile}`; return mileBased ? word : `${word}${s.distMi!=null?` · ${s.distMi.toFixed(2)}mi`:''}`; };
  visibleIdx.forEach(i=>{
    const s=splits[i], x0=distScale(cum[i]), x1=distScale(cum[i+1]);
    const frac = ((cum[i]+cum[i+1])/2)/(cum[cum.length-1]||1);
    const hit=el('rect',{x:x0,y:M.top,width:Math.max(x1-x0,1),height:plotH,fill:'transparent'});
    hit.addEventListener('mouseenter',e=>{ showTooltip(e,`<div class="tt-title">${splitTitle(s)}</div><div class="tt-row">Elevation gain: <b>+${s.elevGainFt||0}ft</b></div>`); if(syncId && ROUTE_SYNC_TARGETS[syncId]) ROUTE_SYNC_TARGETS[syncId].setFraction(frac); });
    hit.addEventListener('mousemove',positionTooltip);
    // A pinned point (see SYNC_PINNED doc comment) ignores the hover-driven
    // clear — only an explicit click (below) or clicking elsewhere moves it.
    hit.addEventListener('mouseleave',()=>{ hideTooltip(); if(syncId && ROUTE_SYNC_TARGETS[syncId] && SYNC_PINNED[syncId]!==frac) ROUTE_SYNC_TARGETS[syncId].clear(); });
    hit.addEventListener('click',()=>{
      if(!syncId || !ROUTE_SYNC_TARGETS[syncId]) return;
      if(SYNC_PINNED[syncId]===frac){ SYNC_PINNED[syncId]=null; ROUTE_SYNC_TARGETS[syncId].clear(); }
      else { SYNC_PINNED[syncId]=frac; ROUTE_SYNC_TARGETS[syncId].setFraction(frac); }
    });
    svg.appendChild(hit);
    if(mileLabels.has(i)){ const xl=el('text',{x:xCenter(i),y:H-M.bottom+16,'text-anchor':'middle'}); xl.textContent = isSemanticLabel(s) ? s.mile : (mileBased?'Mi ':'Lap ')+s.mile; svg.appendChild(xl); }
  });
  // v14 fix: a split's pace can genuinely be missing (null, from a lap whose
  // distance/duration didn't support computing one) — treating that as "0"
  // used to plot a fake dot at the pace-0 axis floor and draw the line
  // straight down to it (the same garbled-value bug, resurfacing at the
  // rendering layer even after the Python side stopped faking a 0). Only
  // plot points with a real pace, and break the line rather than bridging
  // straight across a gap, so a missing lap shows as a genuine gap instead
  // of a fabricated value.
  let pacePath=''; let pacePenDown=false;
  visibleIdx.forEach(i=>{
    const p=splits[i].pace;
    if(p==null){ pacePenDown=false; return; }
    pacePath+=(pacePenDown?'L':'M')+xCenter(i)+','+yPace(p)+' ';
    pacePenDown=true;
  });
  svg.appendChild(el('path',{d:pacePath.trim(),fill:'none',stroke:'#00B4E0','stroke-width':2.5,'clip-path':`url(#${clipId})`}));
  visibleIdx.forEach(i=>{ const s=splits[i]; if(s.pace==null) return; const c=el('circle',{class:'data-point',cx:xCenter(i),cy:yPace(s.pace),r:4.5,fill:'#00B4E0'}); c.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">${splitTitle(s)}</div><div class="tt-row">Pace: <b>${paceStr(s.pace)}/mi</b></div>`)); c.addEventListener('mousemove',positionTooltip); c.addEventListener('mouseleave',hideTooltip); svg.appendChild(c); });
  if(hrs.length){
    // same gap-instead-of-fake-zero treatment as the pace line above: a lap
    // with no recorded HR falls back to the axis floor otherwise, which reads
    // as an impossible "0 bpm" dip rather than genuinely missing data.
    let hrPath=''; let hrPenDown=false;
    visibleIdx.forEach(i=>{
      const h=splits[i].avgHr;
      if(!h){ hrPenDown=false; return; }
      hrPath+=(hrPenDown?'L':'M')+xCenter(i)+','+yHr(h)+' ';
      hrPenDown=true;
    });
    svg.appendChild(el('path',{d:hrPath.trim(),fill:'none',stroke:'#FF5A64','stroke-width':2.5,'clip-path':`url(#${clipId})`}));
    visibleIdx.forEach(i=>{ const s=splits[i]; if(!s.avgHr) return; const c=el('circle',{class:'data-point',cx:xCenter(i),cy:yHr(s.avgHr),r:4.5,fill:'#FF5A64'}); c.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">${splitTitle(s)}</div><div class="tt-row">Avg HR: <b>${s.avgHr} bpm</b></div>${s.maxHr?`<div class="tt-row">Max HR: <b>${s.maxHr} bpm</b></div>`:''}`)); c.addEventListener('mousemove',positionTooltip); c.addEventListener('mouseleave',hideTooltip); svg.appendChild(c); });
  }
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:M.left,y1:M.top,y2:M.top+plotH}));
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:W-M.right,y1:M.top+plotH,y2:M.top+plotH}));
  container.appendChild(svg);
  // v15 — synced hover target: a small absolutely-positioned dot (not SVG, so
  // it survives independently of the next svg redraw) that the route map's
  // own hover handler can move along this chart's x-axis, matched by
  // "same fraction of total run distance" between the two datasets. Each
  // split's own hover rect (above) drives the map the other direction — see
  // renderRouteMap. cumTotal/distScale close over the CURRENT zoom window, so
  // a fraction that's panned/zoomed out of view is simply not shown.
  if(syncId){
    container.style.position = container.style.position || 'relative';
    const dot = document.createElement('div');
    dot.className = 'splits-hover-dot';
    container.appendChild(dot);
    const cumTotal = cum[cum.length-1] || 1;
    SPLITS_SYNC_TARGETS[syncId] = {
      setFraction(frac){
        const d = frac*cumTotal;
        if(d < winStartDist-0.001 || d > winEndDist+0.001){ dot.classList.remove('show'); return; }
        dot.style.left = distScale(d)+'px';
        dot.style.top = (M.top+plotH*0.5)+'px';
        dot.classList.add('show');
      },
      clear(){ dot.classList.remove('show'); }
    };
  }
  // Legend lives in its own HTML row (not SVG text) so it wraps naturally on
  // narrow screens instead of colliding with the axis titles at a fixed pixel spot.
  if(legendId){
    const lg=document.getElementById(legendId);
    if(lg){
      const legendItems=[{c:'#00B4E0',t:'Pace'},{c:'#FF5A64',t:'Avg HR'},{c:elevColor,t:hasProfile?'Elevation':'Elevation gain'}];
      lg.innerHTML = legendItems.map(it=>`<div class="legend-item"><span class="legend-swatch" style="background:${it.c}"></span>${it.t}</div>`).join('');
    }
  }
}

// v11 — a structured workout's splits chart, when the fine-grained time/pace
// stream is available for it (see build_interval_timeline in the Python
// above): the x-axis is elapsed TIME rather than distance, so each segment's
// width is literally how long it lasted (a recovery jog can end up nearly as
// wide as the interval before it, even though it covered half the ground),
// and the pace/HR lines are drawn from many samples across the segment
// instead of one averaged dot per lap — so a rep's actual shape (going out
// fast and fading, easing down through a recovery jog) is visible instead of
// hidden inside a lap average. Falls back to the distance-based
// renderSplitsChart above (via registerSplitsChart below) whenever this data
// isn't there for a given run.
function segKindColor(label){
  if(label==='Warm Up' || label==='Cool Down') return '#57636F';
  if(label.startsWith('Interval')) return '#00B4E0';
  if(label.startsWith('Recovery')) return '#45D6B0';
  return '#57636F';
}
function fmtElapsed(sec){
  sec = Math.max(0, Math.round(sec));
  const m = Math.floor(sec/60), s = sec%60;
  return `${m}:${s.toString().padStart(2,'0')}`;
}
function renderIntervalTimeWindow(container, timeSeries, view, legendId){
  const bands = timeSeries && timeSeries.bands, fine = timeSeries && timeSeries.fine;
  if(!bands || !bands.length || !fine || !fine.length){ container.innerHTML="<p class='empty'>No splits for this run.</p>"; if(legendId){ const lg=document.getElementById(legendId); if(lg) lg.innerHTML=''; } return; }
  const {w:W,h:H}=chartSize(container,760,320), M={top:30,right:20,bottom:34,left:50};
  container._plotMargins = M;
  const plotW=W-M.left-M.right, plotH=H-M.top-M.bottom;
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,preserveAspectRatio:'none'});
  const totalTime = bands[bands.length-1].end;
  const winStart=Math.max(0,view.start), winEnd=Math.min(totalTime,view.end), winSpan=Math.max(winEnd-winStart,0.001);
  const xScale = t => M.left + ((Math.min(Math.max(t,winStart),winEnd)-winStart)/winSpan)*plotW;

  const visFine = fine.filter(p=>p.t>=winStart-0.001 && p.t<=winEnd+0.001);
  const finePts = visFine.length>=2 ? visFine : fine;
  const paces = finePts.map(p=>p.pace).filter(p=>p>0);
  const paceMin=(paces.length?Math.min(...paces):0)-0.4, paceMax=(paces.length?Math.max(...paces):1)+0.4;
  const yPace = v => M.top + ((v-paceMin)/(paceMax-paceMin))*plotH;
  const hrsAll = finePts.map(p=>p.hr).filter(h=>h);
  const hasHr = hrsAll.length>0;
  const hrTicks = hasHr ? niceTicks(Math.min(...hrsAll)-5, Math.max(...hrsAll)+5, 4) : [0,1];
  const hrMin=hrTicks[0], hrMax=hrTicks[hrTicks.length-1];
  const yHr = v => M.top+plotH-((v-hrMin)/(hrMax-hrMin))*plotH;

  const clipId='ivl-clip-'+Math.random().toString(36).slice(2);
  const clip=el('clipPath',{id:clipId}); clip.appendChild(el('rect',{x:M.left,y:M.top,width:plotW,height:plotH})); svg.appendChild(clip);

  bands.forEach(b=>{
    if(b.end<winStart || b.start>winEnd) return; // band entirely outside the visible window
    const x0=xScale(b.start), x1=xScale(b.end);
    const color=segKindColor(b.label);
    svg.appendChild(el('rect',{x:x0,y:M.top,width:Math.max(x1-x0,0.5),height:plotH,fill:color+'1c','clip-path':`url(#${clipId})`}));
    const hit=el('rect',{x:x0,y:M.top,width:Math.max(x1-x0,1),height:plotH,fill:'transparent'});
    hit.addEventListener('mouseenter',e=>showTooltip(e,`<div class="tt-title">${b.label}</div><div class="tt-row">Held for <b>${fmtElapsed(b.durSec)}</b></div><div class="tt-row">Pace: <b>${paceStr(b.pace)}/mi</b></div>${b.avgHr?`<div class="tt-row">Avg HR: <b>${b.avgHr} bpm</b></div>`:''}`));
    hit.addEventListener('mousemove',positionTooltip); hit.addEventListener('mouseleave',hideTooltip);
    svg.appendChild(hit);
    const full = (b.label==='Warm Up'||b.label==='Cool Down');
    const short = full ? b.label : b.label.replace(/^(Interval|Recovery) /,'');
    const text = (x1-x0) > (full?60:20) ? short : null;
    if(text){
      const cx=(x0+x1)/2;
      const estW = text.length*6.4+10;
      svg.appendChild(el('rect',{x:cx-estW/2,y:M.top+3,width:estW,height:15,rx:3,fill:'#0B1017',"fill-opacity":0.72}));
      const lbl=el('text',{x:cx,y:M.top+14,'text-anchor':'middle'});
      lbl.style.fill = color; lbl.style.fontWeight = '600'; lbl.textContent=text;
      svg.appendChild(lbl);
    }
    if(b.end>=winStart && b.end<=winEnd) svg.appendChild(el('line',{x1:x1,x2:x1,y1:M.top,y2:M.top+plotH,stroke:'var(--border-soft)','stroke-width':1}));
  });

  const paceTicks=niceTicks(paceMin,paceMax,5);
  paceTicks.forEach(t=>{ const y=yPace(t); if(y<M.top-1||y>M.top+plotH+1) return; svg.appendChild(el('line',{class:'grid-line',x1:M.left,x2:W-M.right,y1:y,y2:y})); const lbl=el('text',{x:M.left-8,y:y+3,'text-anchor':'end'}); lbl.textContent=paceStr(t); svg.appendChild(lbl); });
  const yTitle=el('text',{x:6,y:12}); yTitle.textContent='min/mi'; svg.appendChild(yTitle);
  const y1Title=el('text',{x:W-M.right,y:12,'text-anchor':'end'}); y1Title.textContent='bpm'; svg.appendChild(y1Title);

  const timeTickCount = Math.max(4, Math.min(10, Math.round(winSpan/300)));
  niceTicks(winStart, winEnd, timeTickCount).forEach(t=>{
    if(t<winStart-0.001||t>winEnd+0.001) return;
    const lbl=el('text',{x:xScale(t),y:H-M.bottom+16,'text-anchor':'middle'});
    lbl.textContent=fmtElapsed(t);
    svg.appendChild(lbl);
  });

  let pacePath=''; finePts.forEach((p,i)=>{ pacePath+=(i===0?'M':'L')+xScale(p.t)+','+yPace(p.pace)+' '; });
  svg.appendChild(el('path',{d:pacePath.trim(),fill:'none',stroke:'#00B4E0','stroke-width':2,'clip-path':`url(#${clipId})`}));
  if(hasHr){
    let hrPath=''; let hrPenDown=false;
    finePts.forEach(p=>{
      if(!p.hr){ hrPenDown=false; return; }
      hrPath+=(hrPenDown?'L':'M')+xScale(p.t)+','+yHr(p.hr)+' ';
      hrPenDown=true;
    });
    svg.appendChild(el('path',{d:hrPath.trim(),fill:'none',stroke:'#FF5A64','stroke-width':2,'clip-path':`url(#${clipId})`}));
  }

  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:M.left,y1:M.top,y2:M.top+plotH}));
  svg.appendChild(el('line',{class:'axis-line',x1:M.left,x2:W-M.right,y1:M.top+plotH,y2:M.top+plotH}));
  container.appendChild(svg);

  if(legendId){
    const lg=document.getElementById(legendId);
    if(lg){
      const items=[{c:'#00B4E0',t:'Pace'}];
      if(hasHr) items.push({c:'#FF5A64',t:'Avg HR'});
      items.push({c:'#57636F',t:'Warm up / Cool down'},{c:'#00B4E0',t:'Interval'},{c:'#45D6B0',t:'Recovery'});
      lg.innerHTML = items.map(it=>`<div class="legend-item"><span class="legend-swatch" style="background:${it.c}"></span>${it.t}</div>`).join('');
    }
  }
}

// Picks the right splits renderer for a run: the v11 time-elapsed chart when
// a structured workout has the fine-grained stream available, the regular
// distance-based chart otherwise (including every mile-based run, and a
// structured workout whose fine stream wasn't available this sync). Either
// way it registers through registerZoomChart so the chart is windowed/pannable
// like every other chart — for the time chart the "index" domain is elapsed
// seconds rather than a point count.
function registerSplitsChart(containerId, title, splits, legendId, elevProfile, mileBased, timeSeries, syncId){
  if(!mileBased && timeSeries){
    const totalTime = timeSeries.bands[timeSeries.bands.length-1].end;
    registerZoomChart(containerId, {
      title, data:timeSeries, domainSize:totalTime, minWindow:Math.min(totalTime, Math.max(30, totalTime*0.08)),
      render:(container,data,view)=>renderIntervalTimeWindow(container,data,view,legendId),
      rangeFmt:(data,view,zoomed)=>zoomed ? `${fmtElapsed(view.start)} – ${fmtElapsed(view.end)}` : `Full run · ${fmtElapsed(totalTime)}`
    });
  } else {
    registerZoomChart(containerId, {
      title, data:splits, domainSize:splits.length, minWindow:Math.min(splits.length,3),
      render:(container,data,view)=>renderSplitsWindow(container,data,view,legendId,elevProfile,mileBased,syncId),
      rangeFmt:(data,view,zoomed)=>{
        if(!data.length) return '';
        const lo=Math.max(0,Math.round(view.start)), hi=Math.min(data.length-1,Math.round(view.end));
        const lbl=i=>{ const s=data[i]; return /^[0-9.]+$/.test(String(s.mile)) ? (mileBased?'Mi ':'Lap ')+s.mile : s.mile; };
        return zoomed ? `${lbl(lo)} – ${lbl(hi)}` : `Full run · ${data.length} ${mileBased?'splits':'segments'}`;
      }
    });
  }
}

// One real Leaflet map per open modal, keyed by container id, so a reopened
// modal (or a resize while one's open) can find and clean up/resize the
// right instance instead of leaking map objects every time a run is clicked.
let ROUTE_MAP_INSTANCES={};
// v15 — the MAP side of the route↔splits sync (see SPLITS_SYNC_TARGETS and
// its doc comment above): keyed by the same syncId, exposes setFraction(frac)
// so a split's hover can move a marker along the route.
let ROUTE_SYNC_TARGETS={};
function renderRouteMap(containerId, points, opts){
  opts = opts || {};
  const container=document.getElementById(containerId);
  if(!container) return;
  if(!points || points.length<2){ container.innerHTML="<p class='empty'>No GPS route available for this run.</p>"; return; }
  if(typeof L==='undefined'){ container.innerHTML="<p class='empty'>Map failed to load — check your internet connection and reopen this run.</p>"; return; }
  if(ROUTE_MAP_INSTANCES[containerId]){ try{ ROUTE_MAP_INSTANCES[containerId].remove(); }catch(e){} delete ROUTE_MAP_INSTANCES[containerId]; }
  if(opts.syncId) delete ROUTE_SYNC_TARGETS[opts.syncId];
  container.innerHTML='';
  const latlngs=points.map(p=>[p[0],p[1]]);
  const map=L.map(container,{scrollWheelZoom:false});
  // v16 hotfix: Leaflet's own documented guidance for a map created inside an
  // element that was just made visible (exactly our case — the modal goes
  // from display:none to display:flex moments before this runs) is to call
  // invalidateSize() once before relying on the map's computed size for
  // anything. A map's first getSize() call should measure the live DOM fresh
  // regardless, so this may not be THE fix for the "t.min"/"reading 'min'"
  // crash reported from production — I can't confirm that from here, since
  // this sandbox has no path to real Leaflet or a real browser against the
  // live site (every CDN/tile host and even the plain npm/pip registries are
  // blocked by this environment's egress policy, confirmed while debugging
  // this). It's cheap, harmless, and Leaflet's own recommended practice for
  // this exact "map inside a modal" shape, so it stays in regardless.
  try{ map.invalidateSize(); }catch(e){}
  // CARTO Voyager (a Google Maps–style basemap) when a key is configured; plain
  // OSM tiles otherwise, so the map still works out of the box before anyone
  // sets one up. The dark-console recolor filter below was built to force OSM's
  // stark white default into this theme — Voyager is already a considered,
  // muted light basemap, so it renders as-is and the filter only applies to
  // the OSM fallback (see the CSS: .route-map.osm-fallback).
  const cartoKey = DATA.meta.cartoApiKey;
  container.classList.toggle('osm-fallback', !cartoKey);
  const tileLayer = cartoKey
    ? L.tileLayer(`https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png?key=${cartoKey}`,{
        maxZoom:20,
        attribution:'&copy; <a href="https://carto.com/attributions" target="_blank" rel="noopener">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'
      })
    : L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{
        maxZoom:19,
        attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'
      });
  tileLayer.addTo(map);
  // v16 hotfix: until now, the ONLY "map isn't working" state this code could
  // detect was `typeof L==='undefined'` — Leaflet's own script tag failing to
  // load. But Leaflet loading fine and then every individual map TILE request
  // failing (an ad blocker or privacy extension flagging tile.openstreetmap.org
  // or the CARTO CDN, a captive portal, a corporate network blocking image
  // CDNs) is at least as common in the field, and it left no visible trace at
  // all — just a flat grey box with working-looking zoom controls, which reads
  // as "broken" with nothing in the UI, console error, or this script's own
  // try/catch to explain it (there's nothing to throw — Leaflet considers a
  // failed tile request a normal, recoverable event, not an error condition).
  // No mock Leaflet shim can surface this either, by construction: it never
  // makes a real network request to fail in the first place. So this is
  // detected the only way it can be: give the tile layer a few seconds to
  // prove at least ONE tile loaded, and if every attempt so far has errored
  // and none has succeeded, say so directly instead of leaving a silent blank
  // box — this never fires for an ordinary slow connection, since it only
  // triggers on actual errors, not merely the absence of a 'load' event yet.
  let tilesLoaded = 0, tileErrors = 0;
  tileLayer.on('tileload', () => { tilesLoaded++; });
  tileLayer.on('tileerror', () => { tileErrors++; });
  setTimeout(() => {
    if(tilesLoaded === 0 && tileErrors > 0 && container.isConnected){
      const warn = document.createElement('div');
      warn.className = 'route-tile-warning';
      warn.innerHTML = '⚠<div>Map tiles aren\'t loading — this is almost always an ad blocker, privacy extension, or network filter blocking the map image service, not a dashboard bug. The route data itself (splits, pace, elevation) below is unaffected.</div>';
      container.insertAdjacentElement('afterend', warn);
    }
  }, 4000);

  // v15 — pace-colored route: split the polyline into one short segment per
  // pair of consecutive GPS points, colored by the pace of whichever mile
  // split that stretch of the route falls into. Route points and mile splits
  // are two independently-sampled Garmin streams with no shared index, so
  // the match is "this point is N% of the way along the route, so look at
  // the split N% of the way along the splits" — an approximation, not an
  // exact correspondence, but a good one for a visual color cue. Falls back
  // to a flat single-color line (the pre-v15 look) whenever there isn't
  // enough pace variation to make coloring meaningful, or splits aren't
  // mile-based (a structured workout's "miles" aren't comparable distances).
  const splits = opts.splits;
  const paced = (opts.mileBased!==false && splits) ? splits.filter(s=>s.pace>0) : [];
  let ptCum=null, totalPtDist=0, splitCum=null, totalSplitDist=0, minPace=0, maxPace=0;
  const canColor = paced.length>=2;
  if(canColor){
    ptCum=[0];
    for(let i=1;i<points.length;i++) ptCum.push(ptCum[i-1]+haversineMi(points[i-1][0],points[i-1][1],points[i][0],points[i][1]));
    totalPtDist = ptCum[ptCum.length-1] || 1;
    const lapDist = splits.map(s=>(s.distMi!=null && s.distMi>0) ? s.distMi : 1);
    splitCum=[0]; lapDist.forEach(d=>splitCum.push(splitCum[splitCum.length-1]+d));
    totalSplitDist = splitCum[splitCum.length-1] || 1;
    minPace=Math.min(...paced.map(s=>s.pace)); maxPace=Math.max(...paced.map(s=>s.pace));
  }
  function splitPaceAt(frac){
    const target = frac*totalSplitDist;
    let j=0; while(j<splitCum.length-2 && splitCum[j+1]<target) j++;
    const p = splits[j] ? splits[j].pace : null;
    return (p>0) ? p : null;
  }
  if(canColor && maxPace>minPace+0.05){
    const segGroup = L.layerGroup().addTo(map);
    const readout = opts.readoutId ? document.getElementById(opts.readoutId) : null;
    for(let i=0;i<points.length-1;i++){
      const midFrac = ((ptCum[i]+ptCum[i+1])/2)/totalPtDist;
      const pace = splitPaceAt(midFrac);
      const color = pace!=null ? paceToColor(pace, minPace, maxPace) : '#7E8EA3';
      // Purely visual — not interactive. A real GPS stream samples every 1-3
      // seconds, so a single point-to-point segment is often just a few
      // screen pixels long; hit-testing the hover directly against this thin
      // (4.5px) stroke made the sync nearly impossible to trigger with a real
      // mouse/finger, which is almost certainly why it read as "not working"
      // rather than genuinely broken. interactive:false hands all pointer
      // events to the wide invisible hit line below instead, the same
      // thin-visible/wide-invisible split already used for the splits chart's
      // own hover targets (see the hit rects in renderSplitsWindow).
      L.polyline([latlngs[i],latlngs[i+1]], {color, weight:4.5, opacity:0.95, lineCap:'round', interactive:false}).addTo(segGroup);
      if(opts.syncId){
        const hit = L.polyline([latlngs[i],latlngs[i+1]], {opacity:0, weight:22, lineCap:'round'}).addTo(segGroup);
        const show = () => {
          if(SPLITS_SYNC_TARGETS[opts.syncId]) SPLITS_SYNC_TARGETS[opts.syncId].setFraction(midFrac);
          if(readout){ readout.textContent = pace!=null ? `${paceStr(pace)}/mi` : '—'; readout.classList.add('show'); }
        };
        hit.on('mouseover', show);
        // A pinned point (see SYNC_PINNED doc comment, above renderSplitsWindow)
        // ignores the hover-driven clear here too — same click-to-hold behavior
        // as the splits chart's own hit rects, and the one that actually makes
        // this usable on a touch device, which has no real hover at all.
        hit.on('mouseout', ()=>{
          if(SYNC_PINNED[opts.syncId]===midFrac) return;
          if(SPLITS_SYNC_TARGETS[opts.syncId]) SPLITS_SYNC_TARGETS[opts.syncId].clear();
          if(readout) readout.classList.remove('show');
        });
        hit.on('click', ()=>{
          if(SYNC_PINNED[opts.syncId]===midFrac){
            SYNC_PINNED[opts.syncId]=null;
            if(SPLITS_SYNC_TARGETS[opts.syncId]) SPLITS_SYNC_TARGETS[opts.syncId].clear();
            if(readout) readout.classList.remove('show');
          } else {
            SYNC_PINNED[opts.syncId]=midFrac;
            show();
          }
        });
      }
    }
  } else {
    L.polyline(latlngs,{color:'#00B4E0',weight:4,opacity:0.95,lineJoin:'round',lineCap:'round'}).addTo(map);
  }
  L.circleMarker(latlngs[0],{radius:6,color:'#0E141C',weight:2,fillColor:'#2FD480',fillOpacity:1}).addTo(map).bindTooltip('Start');
  L.circleMarker(latlngs[latlngs.length-1],{radius:6,color:'#0E141C',weight:2,fillColor:'#FF5A64',fillOpacity:1}).addTo(map).bindTooltip('Finish');
  // v15 hotfix, UPDATE for v16: the v15 fix below (building bounds via
  // L.latLngBounds() instead of an unattached polyline's own .getBounds())
  // was written on the theory that the earlier approach was the problem. It
  // shipped, and the exact same failure came back in production anyway —
  // "Route map failed to render — Cannot read properties of undefined
  // (reading 'min')" — meaning v15's theory of the cause was wrong, or
  // incomplete, and this call can still throw from inside Leaflet's own
  // internals for a reason not yet confirmed. No mock Leaflet can prove or
  // disprove this either way, since it stubs the whole bounds/zoom
  // calculation rather than running Leaflet's real math — and this sandbox
  // has no path to the real library or a real browser to reproduce it
  // directly (every CDN, and even the plain npm/pip package registries, are
  // blocked by its egress policy). So: fitBounds is now wrapped. If it throws
  // again, instead of losing the whole map, fall back to manually centering
  // and zooming from the raw lat/lng values ourselves — a looser frame than a
  // perfectly tight fitBounds, but a working map beats an error message.
  try{
    map.fitBounds(L.latLngBounds(latlngs), {padding:[18,18]});
  }catch(boundsErr){
    console.error('fitBounds failed, falling back to a manual center/zoom:', boundsErr);
    let minLat=Infinity,maxLat=-Infinity,minLon=Infinity,maxLon=-Infinity,sumLat=0,sumLon=0;
    latlngs.forEach(([lat,lon])=>{
      sumLat+=lat; sumLon+=lon;
      if(lat<minLat)minLat=lat; if(lat>maxLat)maxLat=lat;
      if(lon<minLon)minLon=lon; if(lon>maxLon)maxLon=lon;
    });
    const centerLat=sumLat/latlngs.length, centerLon=sumLon/latlngs.length;
    const spanDeg=Math.max(maxLat-minLat, maxLon-minLon, 0.0008);
    // Rough degree-span -> zoom mapping (each zoom level roughly halves the
    // visible span); clamped to a sane range rather than trusting the formula
    // at the extremes.
    const zoom=Math.max(3, Math.min(17, Math.round(14 - Math.log2(spanDeg/0.01))));
    map.setView([centerLat, centerLon], zoom);
  }

  // The map side of the sync: a hover dot driven by the SPLITS chart (see
  // SPLITS_SYNC_TARGETS doc comment), positioned at the route point nearest
  // the target fraction of total route distance.
  if(opts.syncId && canColor){
    const hoverMarker = L.circleMarker(latlngs[0],{radius:7,color:'#0E141C',weight:2,fillColor:'#FFB020',opacity:0,fillOpacity:0}).addTo(map);
    ROUTE_SYNC_TARGETS[opts.syncId] = {
      setFraction(frac){
        const targetDist = Math.max(0,Math.min(1,frac))*totalPtDist;
        let j=0; while(j<ptCum.length-1 && ptCum[j]<targetDist) j++;
        hoverMarker.setLatLng(latlngs[j]);
        hoverMarker.setStyle({opacity:1, fillOpacity:1});
      },
      clear(){ hoverMarker.setStyle({opacity:0, fillOpacity:0}); }
    };
  }
  ROUTE_MAP_INSTANCES[containerId]=map;
}

// Re-drawn on resize so the "match the container's real pixel size" fix above
// actually keeps charts crisp as the viewport changes (rotation, window resize,
// devtools panel toggling) instead of only getting it right on first paint.
// RUNS_ASC/PACED_RUNS_ASC and HRV_PTS/VO2_PTS/EF_PTS are computed once (see
// the init blocks below) and reused here rather than recomputed on every
// resize, so registerZoomChart sees the SAME array reference each time and
// correctly treats a resize as "redraw at current zoom" rather than "new
// data, reset the zoom" — see registerZoomChart's doc comment above.
let RUNS_ASC=null, PACED_RUNS_ASC=null, ACTIVE_SPLIT_ID=null, HRV_PTS=null, VO2_PTS=null, EF_PTS=null, CURRENT_RUN_ID=null;
function redrawCharts(){
  safe('redraw volume', ()=>registerVolumeChart('chart-volume', 'Weekly Volume & Training Load', DATA.weekly));
  safe('redraw plan', ()=>registerPlanChart('chart-plan', 'Plan vs. Actual', DATA.planComparison));
  safe('redraw pace', ()=>{ if(PACED_RUNS_ASC) registerPaceChart('chart-pace', 'Pace Progression', PACED_RUNS_ASC); });
  safe('redraw hrv', ()=>{ if(HRV_PTS) registerSeriesChart('chart-hrv', 'HRV Trend', HRV_PTS, 'hrv', '#2FD480'); });
  safe('redraw vo2', ()=>{ if(VO2_PTS) registerSeriesChart('chart-vo2', 'VO2 Max Trend', VO2_PTS, 'vo2', '#00B4E0'); });
  safe('redraw efficiency', ()=>{ if(EF_PTS) registerSeriesChart('chart-efficiency', 'Aerobic Efficiency — Easy & Long Runs', EF_PTS, 'ef', '#2FD480'); });
  safe('redraw splits', ()=>{ if(ACTIVE_SPLIT_ID && DATA.longRuns[ACTIVE_SPLIT_ID]){ const lr=DATA.longRuns[ACTIVE_SPLIT_ID]; registerSplitsChart('chart-splits', `Long Run Splits — ${lr.label}`, lr.splits, 'splits-legend', lr.elevProfile, lr.mileBased, lr.timeSeries); } });
  Object.values(ROUTE_MAP_INSTANCES).forEach(m=>{ try{ m.invalidateSize(); }catch(e){} });
  // If a chart is currently expanded in the zoom modal, its container was
  // already redrawn above IF it's one of the 6 main charts — but the run-detail
  // modal's own splits chart isn't tracked by this function, so redraw
  // whichever chart is actually in the modal directly, unconditionally.
  if(CHART_ZOOM_STATE){ const inst=CHART_INSTANCES[CHART_ZOOM_STATE.chartId]; if(inst) inst.redraw(); }
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

safe('goal reassessment panel', function(){
  const g = DATA.goalReassessment;
  const section = document.getElementById('goal-reassessment-section');
  if(!g || !g.findings || !g.findings.length) return;
  section.style.display = '';
  document.getElementById('goal-updated-note').textContent = `Reassessed ${fmtDate(g.updated)}`;
  document.getElementById('goal-panel').innerHTML = `
    <div class="goal-head">
      <div><span class="goal-prior">${g.priorGoal}</span> &rarr; <span class="goal-range">${g.revisedGoalLabel}</span></div>
      <div class="dial-label">${g.revisedPaceLabel} avg pace &middot; PR ${durStr(DATA.meta.priorPrSec/60)} unchanged</div>
    </div>
    <div class="goal-findings">${g.findings.map(f=>`<div class="goal-finding"><div class="gf-label">${f.label}</div><div class="gf-text">${f.text}</div></div>`).join('')}</div>
  `;
});

safe('this week panel', function(){
  renderThisWeekPanel();
});

safe('day modal wiring', function(){
  const dayModal = document.getElementById('day-modal');
  if(!dayModal) return;
  document.getElementById('day-modal-close').addEventListener('click', closeDayModal);
  dayModal.addEventListener('click', e=>{ if(e.target===dayModal) closeDayModal(); });
  document.addEventListener('keydown', e=>{ if(e.key==='Escape' && dayModal.style.display!=='none') closeDayModal(); });
  document.addEventListener('click', e=>{
    const el = e.target.closest('#week-save-status.is-error');
    if(el && _lastFailedSave){ GitHubStore.save(_lastFailedSave.mutate, _lastFailedSave.message).catch(()=>{}); }
  });
});

safe('weekly volume chart', function(){ registerVolumeChart('chart-volume', 'Weekly Volume & Training Load', DATA.weekly); });

safe('plan vs actual', function(){
  const plan = DATA.planComparison || [];
  registerPlanChart('chart-plan', 'Plan vs. Actual', plan);
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
  const phases = [...new Set(plan.map(w=>w.phase))];
  document.getElementById('phase-legend').innerHTML = phases.map(p=>`<span class="phase-chip"><span class="dot" style="background:${phaseColor(p)}"></span>${p}</span>`).join('') + `<span class="phase-chip">🏁 Race day</span>`;
});

safe('pace progression chart', function(){
  const runsAsc = [...DATA.runs].sort((a,b)=> new Date(a.date)-new Date(b.date));
  RUNS_ASC = runsAsc;
  PACED_RUNS_ASC = runsAsc.filter(r=>r.paceMinMi);
  registerPaceChart('chart-pace', 'Pace Progression', PACED_RUNS_ASC);
  const types = [...new Set(DATA.runs.map(r=>r.type))];
  document.getElementById('pace-legend').innerHTML = types.map(t=>`<div class="legend-item"><span class="legend-swatch" style="background:${TYPE_COLORS[t]}"></span>${t}</div>`).join('') + `<div class="legend-item"><span class="legend-swatch" style="background:#E6EDF5"></span>5-run rolling avg</div>`;
});

safe('insights', function(){
  document.getElementById('insights').innerHTML = DATA.insights.map(i=>`
    <div class="insight-card"><span class="insight-icon ${i.type}">${i.icon}</span><div class="insight-text">${i.html}</div></div>
  `).join('');
});

safe('recovery panel', function(){
  const r = DATA.trainingReadiness;
  document.getElementById('readiness-score').textContent = r&&r.score!=null ? r.score : '—';
  const RING_C = 2*Math.PI*46;
  const ringFrac = (r&&r.score!=null) ? Math.max(0,Math.min(100,r.score))/100 : 0;
  document.getElementById('readiness-ring-fill').setAttribute('stroke-dasharray', (ringFrac*RING_C).toFixed(1)+' '+RING_C.toFixed(1));
  const level = r&&r.level ? String(r.level) : null;
  const levelClass = level==='HIGH' ? 'high' : (level==='MODERATE' ? 'moderate' : (level ? 'low-warn' : ''));
  document.getElementById('readiness-badge').outerHTML = `<span class="badge ${levelClass}" id="readiness-badge">${level ? level.replace(/_/g,' ') : '—'}</span>`;
  document.getElementById('training-status-badge').textContent = DATA.trainingStatusFeedback || '—';
  document.getElementById('training-acwr').textContent = DATA.acwr!=null ? `ACWR ${DATA.acwr.toFixed(2)}` : '';
  HRV_PTS = DATA.hrv.filter(p=>typeof p.hrv==='number');
  registerSeriesChart('chart-hrv', 'HRV Trend', HRV_PTS, 'hrv', '#2FD480');
  const mix = DATA.loadMix;
  if(mix){
    const rows = [
      { name:'Easy', pct:mix.easyPct, min:mix.easyMin, color:'#7E8EA3', targetMin:65, targetMax:85 },
      { name:'Moderate', pct:mix.moderatePct, min:mix.moderateMin, color:'#00B4E0', targetMin:5, targetMax:20 },
      { name:'Hard', pct:mix.hardPct, min:mix.hardMin, color:'#FF5A64', targetMin:5, targetMax:15 },
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
  VO2_PTS = DATA.vo2max.filter(p=>typeof p.vo2==='number');
  EF_PTS = DATA.efficiencyTrend.filter(p=>typeof p.ef==='number');
  registerSeriesChart('chart-vo2', 'VO2 Max Trend', VO2_PTS, 'vo2', '#00B4E0');
  registerSeriesChart('chart-efficiency', 'Aerobic Efficiency — Easy & Long Runs', EF_PTS, 'ef', '#2FD480');
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
    registerSplitsChart('chart-splits', `Long Run Splits — ${lr.label}`, lr.splits, 'splits-legend', lr.elevProfile, lr.mileBased, lr.timeSeries);
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

// v15 — finds the planned session (if any) within a day of a given run date,
// restricted to trackable types, for the modal's "planned vs. actual" tie-in.
// Same ±1-day tolerance the Python side uses for its own matching, so a run
// logged a day off its scheduled slot still ties back to the right session.
function findPlannedSession(dateStr){
  const d = new Date(dateStr+'T12:00:00');
  let best=null, bestDiff=1.001;
  (DATA.planComparison||[]).forEach(wk=>{
    Object.values(wk.sessions||{}).forEach(s=>{
      if(!s.trackable) return;
      const diff = Math.abs((d-new Date(s.date+'T12:00:00'))/86400000);
      if(diff<=bestDiff){ bestDiff=diff; best=s; }
    });
  });
  return best;
}
// v15 — a plain-language negative/positive split read for mile-based runs:
// compares the average pace of the first half of splits to the second half.
// Skipped for structured workouts (intervals/tempo segments aren't a
// meaningful "first half vs second half" comparison the way mile splits are).
function splitInsight(splits, mileBased){
  if(!mileBased || splits.length<4) return null;
  const paced = splits.filter(s=>s.pace>0);
  if(paced.length<4) return null;
  const mid = Math.floor(splits.length/2);
  const firstHalf = splits.slice(0,mid).filter(s=>s.pace>0);
  const secondHalf = splits.slice(mid).filter(s=>s.pace>0);
  if(!firstHalf.length || !secondHalf.length) return null;
  const avg = arr => arr.reduce((s,x)=>s+x.pace,0)/arr.length;
  const firstAvg = avg(firstHalf), secondAvg = avg(secondHalf);
  const diffSec = Math.round((firstAvg-secondAvg)*60); // positive = 2nd half faster
  if(Math.abs(diffSec)<8) return {tone:'good', text:`Even pacing — first and second half within a few seconds per mile of each other.`};
  if(diffSec>0) return {tone:'good', text:`Negative split — the second half ran <b>${diffSec}s/mi</b> faster than the first.`};
  return {tone:'watch', text:`Positive split — the second half ran <b>${Math.abs(diffSec)}s/mi</b> slower than the first.`};
}

safe('run detail modal', function(){
  const modal = document.getElementById('run-modal');
  const body = document.getElementById('modal-body');
  const ministrip = document.getElementById('modal-ministrip');
  const prevBtn = document.getElementById('modal-prev');
  const nextBtn = document.getElementById('modal-next');
  let navList = null;
  function getNavList(){
    if(!navList) navList = [...DATA.runs].sort((a,b)=> new Date(b.date)-new Date(a.date));
    return navList;
  }
  function closeModal(){
    modal.style.display='none';
    document.body.style.overflow='';
    Object.keys(ROUTE_MAP_INSTANCES).forEach(id=>{ try{ ROUTE_MAP_INSTANCES[id].remove(); }catch(e){} delete ROUTE_MAP_INSTANCES[id]; });
  }
  document.getElementById('modal-close').addEventListener('click', closeModal);
  modal.addEventListener('click', e=>{ if(e.target===modal) closeModal(); });
  document.addEventListener('keydown', e=>{ if(e.key==='Escape' && modal.style.display!=='none') closeModal(); });
  modal.addEventListener('scroll', ()=>{ ministrip.classList.toggle('scrolled', modal.scrollTop>40); });
  prevBtn.addEventListener('click', ()=>{ const list=getNavList(); const i=list.findIndex(r=>String(r.id)===String(CURRENT_RUN_ID)); if(i>=0 && i<list.length-1) window.openRunModal(list[i+1].id); });
  nextBtn.addEventListener('click', ()=>{ const list=getNavList(); const i=list.findIndex(r=>String(r.id)===String(CURRENT_RUN_ID)); if(i>0) window.openRunModal(list[i-1].id); });

  window.openRunModal = function(id){
    const run = DATA.runs.find(r=>String(r.id)===String(id));
    if(!run) return;
    CURRENT_RUN_ID = run.id;
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
    const splitsFirstCol = mileBased ? 'Mile' : 'Segment';
    const planned = findPlannedSession(run.date);
    const insight = splitInsight(splits, mileBased);

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
      ${planned ? `<div class="plan-tie-in">📋<div><b>Planned: ${planned.title}</b> — ${planned.detail}${planned.targetMi?` <span class="dial-label">(~${planned.targetMi.toFixed(2)}mi target)</span>`:''}</div></div>` : ''}
      ${insight ? `<div class="insight-banner tone-${insight.tone}">${insight.tone==='good'?'✓':'⚠'}<div>${insight.text}</div></div>` : ''}
      <div class="modal-section-title">Route</div>
      ${route && route.length>1 ? `<div class="route-map-wrap"><div class="route-map" id="modal-route"></div><div class="route-hover-readout" id="modal-route-readout"></div></div><div class="route-legend"><span><span style="color:#2FD480;">●</span> Start</span><span><span style="color:#FF5A64;">●</span> Finish</span></div><div class="route-pace-legend" id="modal-route-pace-legend"></div>` : `<p class="empty">No GPS route available for this run.</p>`}
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
    modal.scrollTop = 0;
    ministrip.classList.remove('scrolled');
    ministrip.innerHTML = `<span class="ms-item"><b>${run.distMi.toFixed(2)}mi</b></span><span class="ms-item"><b>${paceStr(run.paceMinMi)}/mi</b></span><span class="ms-item"><b>${durStr(run.durMin)}</b></span>${run.avgHr?`<span class="ms-item"><b>${run.avgHr}</b> bpm</span>`:''}`;
    document.body.style.overflow='hidden';
    const navListNow = getNavList(); const navIdx = navListNow.findIndex(r=>String(r.id)===String(id));
    prevBtn.disabled = !(navIdx>=0 && navIdx<navListNow.length-1);
    nextBtn.disabled = !(navIdx>0);
    const syncId = (route && route.length>1 && splits.length) ? 'modal' : null;
    SYNC_PINNED[syncId] = null; // a pin from a previously-viewed run should never carry into this one
    // Each render call is isolated in its own try/catch, with the real error
    // message written directly into that section instead of swallowed. Before
    // this, a failure anywhere inside renderRouteMap (which runs first) would
    // throw out of openRunModal entirely — openRunModal isn't wrapped by the
    // page's usual safe() guard, since it's a closure assigned once and then
    // invoked later from a click handler, outside that original try/catch —
    // silently skipping registerSplitsChart right along with it and leaving
    // BOTH the route and the splits/pace/HR/elevation chart blank with
    // nothing in the UI to say why. Now a bug in one leaves a visible message
    // in its own section and the OTHER section still renders normally.
    if(route && route.length>1){
      try{
        renderRouteMap('modal-route', route, {splits, mileBased, syncId, readoutId:'modal-route-readout'});
      }catch(e){
        console.error('Route map failed to render:', e);
        const el = document.getElementById('modal-route');
        if(el) el.innerHTML = `<p class="empty">Route map failed to render — ${e.message}. Check the browser console (F12 → Console) for the full error.</p>`;
      }
    }
    if(splits.length){
      try{
        registerSplitsChart('modal-splits-chart', `${splitsSectionTitle} — ${run.name}`, splits, 'modal-splits-legend', elevProfile, mileBased, detail.timeSeries, syncId);
      }catch(e){
        console.error('Splits chart failed to render:', e);
        const el = document.getElementById('modal-splits-chart');
        if(el) el.innerHTML = '';
        const box = el ? el.closest('.chart-box') : null;
        if(box) box.insertAdjacentHTML('afterend', `<p class="empty">${splitsSectionTitle} chart failed to render — ${e.message}. Check the browser console (F12 → Console) for the full error.</p>`);
      }
    }
    try{
      const paceLegendEl = document.getElementById('modal-route-pace-legend');
      if(paceLegendEl){
        const paced = mileBased ? splits.filter(s=>s.pace>0) : [];
        paceLegendEl.innerHTML = paced.length>=2 ? `Faster <span class="ramp"></span> Slower — colored by mile pace` : '';
      }
    }catch(e){ console.error('Pace legend failed:', e); }
  };
});
"""

if __name__ == "__main__":
    main()
