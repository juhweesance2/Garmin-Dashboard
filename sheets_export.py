"""
sheets_export.py  (v2) - Garmin -> private Google Sheet, built for analysis in Gemini.

Pulls directly from Garmin (not just the dashboard payload), so it can backfill
history and capture the fine-grained data: GPS track, elevation trace, per-lap
workout structure, sleep/HRV/readiness history, race predictions, training status.

TWO MODES
  Daily sync (called from update_dashboard.py main(), twice a day via update.yml):
      export_to_sheets(payload, garmin=api)
    Re-fetches the last DAILY_LOOKBACK_DAYS days (catches late watch syncs), and
    fetches laps + GPS/telemetry only for runs not already in the sheet.
  One-time backfill (backfill.yml, or locally):
      python sheets_export.py --backfill 120
    Same pipeline over 120 days. Safe to re-run: every tab is keyed, nothing
    duplicates, and runs already in Laps/Track are skipped.

TABS
  Runs          one row per run (upsert by activity_id)
  Daily         one row per day of recovery/fitness metrics (upsert by date)
  Laps          one row per lap, labeled Mile / Warm Up / Interval N / Recovery N / Cool Down
  Track         GPS + elevation + HR + pace + cadence every TRACK_BIN_MI miles, per run
  Weekly        computed from the full Runs tab each sync
  PlanVsActual  from the dashboard payload (your 13-week plan lives in update_dashboard.py)
  Dictionary    what every tab/column means, with units - point Gemini at this first

Garmin field names are best-effort (same caveat as the dashboard): any field
that doesn't parse stays blank instead of breaking the sync. Upserts MERGE, so
a blank in a later sync never erases a value captured earlier.
"""
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

# ------------------------------------------------------------------ config
LOCAL_TZ = ZoneInfo("America/Los_Angeles")  # Actions runs in UTC; dates use this zone
DAILY_LOOKBACK_DAYS = 3        # daily sync re-checks this many recent days
TRACK_BIN_MI = 0.05            # Track resolution (~80 m); 13.1 mi run ~ 262 rows
CALL_PAUSE_SEC = 0.35          # politeness delay between Garmin calls
RUN_TYPE_KEYS = ("running",)   # matches running, trail_running, treadmill_running, track_running
RACE_NAME = "Monterey Bay Half Marathon"   # keep in sync with update_dashboard.py
RACE_DATE = "2026-11-08"

M_PER_MI, FT_PER_M = 1609.344, 3.28084

RUNS_HEADER = [
    "activity_id", "date", "start_time", "name", "type", "type_source",
    "distance_mi", "duration_min", "moving_min", "pace_min_per_mi", "avg_hr", "max_hr",
    "cadence_spm", "stride_length_m", "elev_gain_ft", "elev_loss_ft",
    "training_load", "aerobic_te", "anaerobic_te", "te_label",
    "avg_power_w", "vert_osc_cm", "ground_contact_ms", "calories",
    "hr_z1_min", "hr_z2_min", "hr_z3_min", "hr_z4_min", "hr_z5_min",
    "aero_efficiency", "location", "start_lat", "start_lon", "last_synced"]
DAILY_HEADER = [
    "date", "training_readiness", "readiness_level",
    "hrv_ms", "hrv_weekly_avg_ms", "hrv_status", "resting_hr",
    "sleep_hours", "deep_sleep_h", "light_sleep_h", "rem_sleep_h", "awake_h", "sleep_score",
    "sleep_respiration", "body_battery_high", "body_battery_low", "avg_stress", "steps",
    "active_calories", "vo2max", "training_status", "acute_load", "chronic_load", "load_ratio",
    "pred_5k_min", "pred_10k_min", "pred_half_min", "pred_marathon_min",
    "endurance_score", "hill_score", "last_synced"]
LAPS_HEADER = [
    "activity_id", "date", "lap_index", "lap_type", "lap_label", "distance_mi",
    "duration_min", "pace_min_per_mi", "avg_hr", "max_hr", "cadence_spm", "elev_gain_ft"]
TRACK_HEADER = [
    "activity_id", "date", "point_index", "elapsed_min", "distance_mi", "lat", "lon",
    "elevation_ft", "grade_pct", "hr", "pace_min_per_mi", "cadence_spm"]
WEEKLY_HEADER = [
    "week_start", "miles", "runs", "long_run_mi", "total_min", "avg_pace_min_per_mi",
    "training_load", "load_ratio", "easy_runs", "quality_runs", "avg_aero_efficiency_easy"]
PLAN_HEADER = [
    "week", "phase", "week_start", "planned_mi", "actual_mi", "adherence_pct",
    "planned_long_mi", "actual_long_mi", "status"]

_EMPTY = (None, "", "—", "-")


# ------------------------------------------------------------ value helpers
def _pick(d, *names, default=None):
    if not isinstance(d, dict):
        return default
    for n in names:
        if n in d and d[n] not in _EMPTY:
            return d[n]
    return default


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _find(o, *keys):
    """First non-empty value for any of `keys`, anywhere in a nested response.
    Garmin nests the same field at different depths across accounts/devices."""
    for d in _walk(o):
        for k in keys:
            if k in d and d[k] not in _EMPTY:
                return d[k]
    return None


def _num(v, nd=3):
    if isinstance(v, dict):
        v = _pick(v, "value", "lastNightAvg", "score", "overallScore", "avg")
    if v in _EMPTY or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        r = round(v, nd)
        return int(r) if r.is_integer() else r
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    if not m:
        return None
    f = round(float(m.group()), nd)
    return int(f) if f.is_integer() else f


def _mul(v, k, nd=2):
    v = _num(v)
    return None if v is None else round(v * k, nd)


def _clock_to_minutes(s):
    parts = [float(p) for p in re.findall(r"\d+(?:\.\d+)?", str(s).split("/")[0])]
    if len(parts) == 3:
        return round(parts[0] * 60 + parts[1] + parts[2] / 60, 3)
    if len(parts) == 2:
        return round(parts[0] + parts[1] / 60, 3)
    return round(parts[0], 3) if parts else None


def _pace_from_speed(mps):
    mps = _num(mps)
    return round(M_PER_MI / mps / 60, 3) if mps and mps > 0.3 else None


def _date(v):
    if v in _EMPTY:
        return None
    if isinstance(v, (int, float)) and v > 1e11:
        return datetime.fromtimestamp(v / 1000, LOCAL_TZ).date().isoformat()
    m = re.match(r"\d{4}-\d{2}-\d{2}", str(v))
    return m.group() if m else None


def _norm(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return "" if v is None else str(v).strip()


def _cell(v):
    return "" if v is None else v


def _haversine_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


# ------------------------------------------------------------ garmin calls
_err_counts = {}


def _call(api, name, *args, **kw):
    """Call a garminconnect method if it exists; None on any failure. Backs off on 429."""
    fn = getattr(api, name, None)
    if fn is None:
        return None
    for attempt in range(3):
        try:
            out = fn(*args, **kw)
            time.sleep(CALL_PAUSE_SEC)
            return out
        except Exception as e:
            msg = str(e)
            if "429" in msg or "Too Many" in msg:
                wait = 60 * (attempt + 1)
                print(f"[garmin] rate limited on {name}; waiting {wait}s")
                time.sleep(wait)
                continue
            _err_counts[name] = _err_counts.get(name, 0) + 1
            if _err_counts[name] <= 3:
                print(f"[garmin] {name} failed: {type(e).__name__}: {msg[:150]}")
            return None
    return None


def _login():
    from garminconnect import Garmin
    api = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
    api.login()
    return api


# ------------------------------------------------------------ daily metrics
def _dated_values(o, keys):
    """{date: value} from any nested response: dicts carrying calendarDate/date,
    or maps keyed by date strings (e.g. endurance score's groupMap)."""
    out = {}
    for d in _walk(o):
        dt = _date(_pick(d, "calendarDate", "date", "startDate"))
        if dt:
            v = _num(_pick(d, *keys))
            if v is not None:
                out.setdefault(dt, v)
        for k, v in d.items():
            if isinstance(v, dict) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(k)):
                n = _num(_pick(v, *keys))
                if n is not None:
                    out.setdefault(str(k), n)
    return out


def fetch_daily(api, days, stamp):
    rows = {}
    for i, d in enumerate(days):
        ds = d.isoformat()
        stats = _call(api, "get_stats", ds)
        sleep = _call(api, "get_sleep_data", ds)
        hrv = _call(api, "get_hrv_data", ds)
        ready = _call(api, "get_training_readiness", ds)
        mm = _call(api, "get_max_metrics", ds)
        ts = _call(api, "get_training_status", ds)
        secs = lambda k: _mul(_find(sleep, k), 1 / 3600)
        rows[ds] = {
            "date": ds,
            "training_readiness": _num(_find(ready, "score")),
            "readiness_level": _find(ready, "level"),
            "hrv_ms": _num(_find(hrv, "lastNightAvg")),
            "hrv_weekly_avg_ms": _num(_find(hrv, "weeklyAvg")),
            "hrv_status": _find(hrv, "status"),
            "resting_hr": _num(_find(stats, "restingHeartRate")),
            "sleep_hours": secs("sleepTimeSeconds"),
            "deep_sleep_h": secs("deepSleepSeconds"),
            "light_sleep_h": secs("lightSleepSeconds"),
            "rem_sleep_h": secs("remSleepSeconds"),
            "awake_h": secs("awakeSleepSeconds"),
            "sleep_score": _num(_find(_find(sleep, "sleepScores"), "overall")),
            "sleep_respiration": _num(_find(sleep, "averageRespirationValue")),
            "body_battery_high": _num(_find(stats, "bodyBatteryHighestValue")),
            "body_battery_low": _num(_find(stats, "bodyBatteryLowestValue")),
            "avg_stress": _num(_find(stats, "averageStressLevel")),
            "steps": _num(_find(stats, "totalSteps")),
            "active_calories": _num(_find(stats, "activeKilocalories")),
            "vo2max": _num(_find(mm, "vo2MaxPreciseValue", "vo2MaxValue")),
            "training_status": _find(ts, "trainingStatusFeedbackPhrase"),
            "acute_load": _num(_find(ts, "dailyTrainingLoadAcute")),
            "chronic_load": _num(_find(ts, "dailyTrainingLoadChronic")),
            "load_ratio": _num(_find(ts, "dailyAcuteChronicWorkloadRatio")),
            "last_synced": stamp,
        }
        if (i + 1) % 15 == 0:
            print(f"[daily] {i + 1}/{len(days)} days fetched")

    start, end = min(days).isoformat(), max(days).isoformat()
    preds = _call(api, "get_race_predictions", start, end, "daily")
    pred_keys = {"pred_5k_min": "time5K", "pred_10k_min": "time10K",
                 "pred_half_min": "timeHalfMarathon", "pred_marathon_min": "timeMarathon"}
    got_any = False
    for col, key in pred_keys.items():
        for ds, secs_v in _dated_values(preds, (key,)).items():
            if ds in rows:
                rows[ds][col] = round(secs_v / 60, 2)
                got_any = True
    if not got_any:  # history not supported -> today's prediction only
        cur = _call(api, "get_race_predictions")
        today = max(days).isoformat()
        for col, key in pred_keys.items():
            v = _num(_find(cur, key))
            if v is not None and today in rows:
                rows[today][col] = round(v / 60, 2)

    for col, name in (("endurance_score", "get_endurance_score"), ("hill_score", "get_hill_score")):
        res = _call(api, name, start, end)
        for ds, v in _dated_values(res, ("overallScore", "groupAverage")).items():
            if ds in rows:
                rows[ds][col] = v
    return list(rows.values())


# ------------------------------------------------------------ runs
def _is_run(a):
    tk = str(_find(a.get("activityType") or {}, "typeKey") or "")
    return any(k in tk for k in RUN_TYPE_KEYS)


def fetch_run_summaries(api, start, end):
    acts = _call(api, "get_activities_by_date", start.isoformat(), end.isoformat()) or []
    return [a for a in acts if isinstance(a, dict) and _is_run(a) and a.get("activityId")]


def run_row(a, stamp, zones=None):
    dist_mi = _mul(a.get("distance"), 1 / M_PER_MI, 3)
    avg_hr = _num(a.get("averageHR"))
    speed = _num(a.get("averageSpeed"))
    cad = _num(_pick(a, "averageRunningCadenceInStepsPerMinute", "averageRunCadence"))
    row = {
        "activity_id": str(a["activityId"]),
        "date": _date(a.get("startTimeLocal")),
        "start_time": str(a.get("startTimeLocal") or "")[11:16] or None,
        "name": a.get("activityName"),
        "distance_mi": dist_mi,
        "duration_min": _mul(a.get("duration"), 1 / 60),
        "moving_min": _mul(a.get("movingDuration"), 1 / 60),
        "pace_min_per_mi": _pace_from_speed(speed),
        "avg_hr": avg_hr,
        "max_hr": _num(a.get("maxHR")),
        "cadence_spm": cad,
        "stride_length_m": _mul(a.get("avgStrideLength"), 0.01),
        "elev_gain_ft": _mul(a.get("elevationGain"), FT_PER_M, 0),
        "elev_loss_ft": _mul(a.get("elevationLoss"), FT_PER_M, 0),
        "training_load": _num(a.get("activityTrainingLoad"), 1),
        "aerobic_te": _num(a.get("aerobicTrainingEffect"), 1),
        "anaerobic_te": _num(a.get("anaerobicTrainingEffect"), 1),
        "te_label": a.get("trainingEffectLabel"),
        "avg_power_w": _num(a.get("avgPower"), 0),
        "vert_osc_cm": _num(a.get("avgVerticalOscillation"), 1),
        "ground_contact_ms": _num(a.get("avgGroundContactTime"), 0),
        "calories": _num(a.get("calories"), 0),
        "location": a.get("locationName"),
        "start_lat": _num(a.get("startLatitude"), 5),
        "start_lon": _num(a.get("startLongitude"), 5),
        "last_synced": stamp,
    }
    for z in range(1, 6):
        v = a.get(f"hrTimeInZone_{z}")
        if v is None and zones:
            v = zones.get(z)
        row[f"hr_z{z}_min"] = _mul(v, 1 / 60, 1)
    # Speed per heartbeat: mph / avg HR x 1000. Higher = more ground at the same effort.
    # Same idea as the dashboard's metric; the scale may differ, the trend is what matters.
    if speed and avg_hr:
        row["aero_efficiency"] = round(speed * 2.236936 / avg_hr * 1000, 2)
    return row


def _hr_zones(api, aid):
    res = _call(api, "get_activity_hr_in_timezones", aid)
    out = {}
    for z in res or []:
        if isinstance(z, dict) and z.get("zoneNumber") is not None:
            out[int(z["zoneNumber"])] = z.get("secsInZone")
    return out


# ------------------------------------------------------------ laps
_INTENSITY = {"WARMUP": "Warm Up", "COOLDOWN": "Cool Down", "RECOVERY": "Recovery",
              "REST": "Recovery", "INTERVAL": "Interval"}


def build_laps(aid, date_s, splits):
    laps = [l for l in (_find(splits, "lapDTOs") or []) if isinstance(l, dict)]
    rows = []
    for i, l in enumerate(laps, start=1):
        rows.append({
            "activity_id": aid, "date": date_s, "lap_index": i,
            "distance_mi": _mul(l.get("distance"), 1 / M_PER_MI, 3),
            "duration_min": _mul(l.get("duration"), 1 / 60, 2),
            "pace_min_per_mi": _pace_from_speed(l.get("averageSpeed")),
            "avg_hr": _num(l.get("averageHR")), "max_hr": _num(l.get("maxHR")),
            "cadence_spm": _num(_pick(l, "averageRunCadence", "averageRunningCadenceInStepsPerMinute"), 0),
            "elev_gain_ft": _mul(l.get("elevationGain"), FT_PER_M, 0),
            "_intensity": str(l.get("intensityType") or "").upper(),
        })
    _classify_laps(rows)
    for r in rows:
        r.pop("_intensity", None)
    return rows


def _classify_laps(rows):
    if not rows:
        return
    dists = [r["distance_mi"] or 0 for r in rows]
    body = dists[:-1] if len(dists) > 1 else dists
    if sum(1 for d in body if 0.9 <= d <= 1.1) >= 0.7 * len(body):   # per-mile autolaps
        for r in rows:
            full = (r["distance_mi"] or 0) >= 0.9
            r["lap_type"] = "Mile" if full else "Partial"
            r["lap_label"] = f"Mile {r['lap_index']}" if full else f"Final {r['distance_mi']} mi"
        return
    # Structured workout: trust the watch's intensity tags if it recorded them...
    tagged = [_INTENSITY.get(r["_intensity"]) for r in rows]
    if sum(t is not None for t in tagged) >= len(rows) / 2:
        types = [t or "Interval" for t in tagged]
    else:  # ...otherwise infer from the run's own laps (same approach as the dashboard)
        interior = rows[1:-1] if len(rows) > 3 else rows
        med_d = median([r["distance_mi"] or 0 for r in interior]) or 0
        paces = [r["pace_min_per_mi"] for r in interior if r["pace_min_per_mi"]]
        med_p = median(paces) if paces else None
        types = []
        for j, r in enumerate(rows):
            d, p = r["distance_mi"] or 0, r["pace_min_per_mi"]
            if j == 0 and len(rows) > 3 and d > 1.5 * med_d:
                types.append("Warm Up")
            elif j == len(rows) - 1 and len(rows) > 3 and d > 1.5 * med_d:
                types.append("Cool Down")
            elif p and med_p and p <= med_p:
                types.append("Interval")
            else:
                types.append("Recovery")
    counts = {}
    for r, t in zip(rows, types):
        r["lap_type"] = t
        if t in ("Interval", "Recovery"):
            counts[t] = counts.get(t, 0) + 1
            r["lap_label"] = f"{t} {counts[t]}"
        else:
            r["lap_label"] = t


# ------------------------------------------------------------ GPS / telemetry
def _samples(details):
    desc = _find(details, "metricDescriptors") or []
    idx = {d.get("key"): d.get("metricsIndex") for d in desc if isinstance(d, dict)}
    out = []
    for m in _find(details, "activityDetailMetrics") or []:
        vals = m.get("metrics") if isinstance(m, dict) else None
        if not vals:
            continue

        def g(*keys):
            for k in keys:
                i = idx.get(k)
                if i is not None and i < len(vals) and vals[i] is not None:
                    return vals[i]
            return None
        cad = g("directDoubleCadence")
        if cad is None:
            c1 = g("directRunCadence")
            cad = c1 * 2 if c1 is not None and c1 < 120 else c1
        out.append({"t": g("sumElapsedDuration", "sumDuration", "sumMovingDuration"),
                    "ts": g("directTimestamp"), "d": g("sumDistance"),
                    "lat": g("directLatitude"), "lon": g("directLongitude"),
                    "elev": g("directElevation", "directAltitude"),
                    "hr": g("directHeartRate"), "cad": cad})
    if not any(s["lat"] is not None for s in out):       # fall back to the route polyline
        poly = _find(details, "polyline") or []
        pts = [{"t": None, "ts": p.get("time"), "d": p.get("distanceInMeters"), "lat": p.get("lat"),
                "lon": p.get("lon"), "elev": p.get("altitude"), "hr": None, "cad": None}
               for p in poly if isinstance(p, dict) and p.get("lat") is not None]
        if pts and not out:
            out = pts
        elif pts:  # telemetry has no GPS: attach nearest-by-fraction polyline point
            for i, s in enumerate(out):
                p = pts[min(len(pts) - 1, round(i * (len(pts) - 1) / max(1, len(out) - 1)))]
                s["lat"], s["lon"] = p["lat"], p["lon"]
    # fill elapsed time / distance when missing
    t0 = next((s["ts"] for s in out if s["ts"]), None)
    prev, acc = None, 0.0
    for s in out:
        if s["t"] is None and s["ts"] and t0:
            s["t"] = (s["ts"] - t0) / 1000
        if s["d"] is None and s["lat"] is not None:
            if prev is not None:
                acc += _haversine_m((prev["lat"], prev["lon"]), (s["lat"], s["lon"]))
            s["d"] = acc
        if s["lat"] is not None:
            prev = s
    return [s for s in out if s["d"] is not None]


def build_track(aid, date_s, details):
    samples = sorted(_samples(details), key=lambda s: s["d"])
    if not samples:
        return []
    bins = {}
    for s in samples:
        bins.setdefault(int(s["d"] / M_PER_MI / TRACK_BIN_MI), []).append(s)
    rows, prev = [], None
    for k in sorted(bins):
        b = bins[k]
        last = b[-1]

        def avg(key, b=b):
            vals = [x[key] for x in b if x[key] is not None]
            return sum(vals) / len(vals) if vals else None
        with_gps = [x for x in b if x["lat"] is not None]
        g = with_gps[-1] if with_gps else None
        elev_ft = _mul(avg("elev"), FT_PER_M, 1)
        d_mi = last["d"] / M_PER_MI
        row = {"activity_id": aid, "date": date_s, "point_index": len(rows) + 1,
               "elapsed_min": _mul(last["t"], 1 / 60, 3), "distance_mi": round(d_mi, 3),
               "lat": round(g["lat"], 6) if g else None, "lon": round(g["lon"], 6) if g else None,
               "elevation_ft": elev_ft, "hr": _num(avg("hr"), 0), "cadence_spm": _num(avg("cad"), 0)}
        if prev:
            dd = d_mi - prev["distance_mi"]
            if dd > 0.005 and row["elapsed_min"] is not None and prev["elapsed_min"] is not None:
                p = (row["elapsed_min"] - prev["elapsed_min"]) / dd
                row["pace_min_per_mi"] = round(p, 3) if 3 < p < 30 else None
            if dd > 0.005 and elev_ft is not None and prev["elevation_ft"] is not None:
                row["grade_pct"] = round((elev_ft - prev["elevation_ft"]) / (dd * 5280) * 100, 1)
        rows.append(row)
        prev = row
    return rows


# ------------------------------------------------------------ derived tables
_TYPE_WORDS = [("Tempo", ("tempo", "threshold")),
               ("Speed", ("interval", "speed", "repeat", "800", "400", "track", "fartlek")),
               ("Strides", ("stride",)),
               ("Benchmark", ("benchmark", "time trial", "race"))]


def classify_types(runs):
    """Fills type for rows without a dashboard-provided type, using the same rule
    as the dashboard: name keywords, else the week's longest run = Long, else Easy."""
    weeks = {}
    for r in runs:
        if r.get("date"):
            ws = _week_start(r["date"])
            weeks.setdefault(ws, []).append(r)
    for r in runs:
        if r.get("type_source") == "dashboard" and r.get("type"):
            continue
        name = str(r.get("name") or "").lower()
        t = next((label for label, words in _TYPE_WORDS if any(w in name for w in words)), None)
        if not t and r.get("date"):
            wk = weeks.get(_week_start(r["date"]), [])
            longest = max(wk, key=lambda x: _num(x.get("distance_mi")) or 0)
            t = "Long" if longest is r and (_num(r.get("distance_mi")) or 0) >= 5 else "Easy"
        r["type"], r["type_source"] = t or "Easy", "auto"


def _week_start(ds):
    d = date.fromisoformat(str(ds)[:10])
    return (d - timedelta(days=d.weekday())).isoformat()


def build_weekly(runs):
    weeks = {}
    for r in runs:
        if r.get("date"):
            weeks.setdefault(_week_start(r["date"]), []).append(r)
    out, loads = [], []
    for ws in sorted(weeks):
        rs = weeks[ws]
        miles = sum(_num(r.get("distance_mi")) or 0 for r in rs)
        mins = sum(_num(r.get("duration_min")) or 0 for r in rs)
        load = sum(_num(r.get("training_load")) or 0 for r in rs)
        prev4 = loads[-4:]
        easy = [r for r in rs if r.get("type") in ("Easy", "Long")]
        eff = [_num(r.get("aero_efficiency")) for r in easy if _num(r.get("aero_efficiency"))]
        out.append({
            "week_start": ws, "miles": round(miles, 2), "runs": len(rs),
            "long_run_mi": max((_num(r.get("distance_mi")) or 0 for r in rs), default=None),
            "total_min": round(mins, 1),
            "avg_pace_min_per_mi": round(mins / miles, 3) if miles else None,
            "training_load": round(load, 1),
            "load_ratio": round(load / (sum(prev4) / len(prev4)), 2) if len(prev4) == 4 and sum(prev4) else None,
            "easy_runs": len(easy), "quality_runs": len(rs) - len(easy),
            "avg_aero_efficiency_easy": round(sum(eff) / len(eff), 2) if eff else None,
        })
        loads.append(load)
    return out


def build_plan(payload):
    plan = _pick(payload or {}, "planVsActual", "plan", "planComparison", default=[]) or []
    if isinstance(plan, dict):
        plan = _pick(plan, "weeks", "rows", default=[]) or []
    return [{
        "week": _num(_pick(p, "week", "weekNum", "weekNumber")),
        "phase": _pick(p, "phase"),
        "week_start": _date(_pick(p, "weekStart", "start", "date")),
        "planned_mi": _num(_pick(p, "plannedMi", "planned", "plannedMiles")),
        "actual_mi": _num(_pick(p, "actualMi", "actual", "actualMiles")),
        "adherence_pct": _num(_pick(p, "adherencePct", "adherence")),
        "planned_long_mi": _num(_pick(p, "plannedLongMi", "plannedLong")),
        "actual_long_mi": _num(_pick(p, "actualLongMi", "actualLong")),
        "status": _pick(p, "status"),
    } for p in plan if isinstance(p, dict)]


def _dashboard_types(payload):
    out = {}
    for r in _pick(payload or {}, "runs", "runLog", "allRuns", default=[]) or []:
        aid, t = _pick(r, "id", "activityId"), _pick(r, "type", "runType")
        if aid is not None and t:
            out[_norm(aid)] = t
    return out


DICTIONARY = [
    ("About", "", f"Garmin running data for training toward the {RACE_NAME} on {RACE_DATE}. Synced twice daily. Pace is decimal minutes per mile (9.5 = 9:30/mi). Distances in miles, elevation in feet, dates are Pacific time. Blank = not recorded."),
    ("Runs", "activity_id", "Garmin activity ID; joins Runs to Laps and Track."),
    ("Runs", "type", "Easy / Long / Tempo / Speed / Strides / Benchmark. type_source=dashboard means the dashboard classified it; auto means name keywords or longest-run-of-week rule."),
    ("Runs", "pace_min_per_mi", "Average pace, decimal minutes per mile."),
    ("Runs", "training_load / aerobic_te / anaerobic_te", "Garmin's EPOC-based load and 0-5 training effect scores."),
    ("Runs", "hr_z1_min..hr_z5_min", "Minutes in each heart-rate zone. Z1-Z2 = easy, Z3 = moderate, Z4-Z5 = hard (80/20 guideline compares these)."),
    ("Runs", "aero_efficiency", "Speed per heartbeat: mph / avg HR x 1000. Rising on Easy/Long runs = aerobic fitness improving."),
    ("Runs", "stride_length_m / vert_osc_cm / ground_contact_ms / avg_power_w", "Running dynamics; blank if the watch/sensor didn't record them."),
    ("Daily", "training_readiness", "Garmin 0-100 readiness score for that morning."),
    ("Daily", "hrv_ms / hrv_weekly_avg_ms / hrv_status", "Overnight HRV average, 7-day average, and Garmin's status (BALANCED/UNBALANCED/LOW)."),
    ("Daily", "sleep_* ", "Sleep duration and stages in hours; sleep_score 0-100; sleep_respiration breaths/min."),
    ("Daily", "acute_load / chronic_load / load_ratio", "Garmin 7-day acute vs 28-day chronic load; ratio 0.8-1.3 is the usual productive range, >1.5 is a spike."),
    ("Daily", "pred_*_min", "Garmin race-time predictions in minutes (pred_half_min 125.5 = 2:05:30)."),
    ("Laps", "lap_type / lap_label", "Mile (per-mile autolap) / Partial (final leftover) for steady runs; Warm Up / Interval N / Recovery N / Cool Down for structured workouts."),
    ("Track", "", f"One point every {TRACK_BIN_MI} mi per run: GPS lat/lon (route map), elevation_ft + grade_pct (terrain profile), hr, pace, cadence (within-run analysis). elapsed_min is time since start; use it for interval time charts."),
    ("Weekly", "", "Computed from Runs. load_ratio = week's load / avg of previous 4 weeks. quality_runs = Tempo/Speed/Strides/Benchmark."),
    ("PlanVsActual", "", "The 13-week training plan vs. what was logged, from the dashboard. Only running days (Mon/Wed/Sat) are tracked."),
]


# ------------------------------------------------------------ sheet I/O
def _worksheet(sh, title, header):
    import gspread
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=200, cols=len(header))
        ws.update(range_name="A1", values=[header], value_input_option="RAW")
        ws.freeze(rows=1)
        return ws


def _read_dicts(ws):
    vals = ws.get_all_values(value_render_option="UNFORMATTED_VALUE")
    if not vals:
        return []
    hdr = [str(h) for h in vals[0]]
    return [{hdr[i]: row[i] for i in range(min(len(hdr), len(row)))}
            for row in vals[1:] if any(c not in ("", None) for c in row)]


def _write(ws, header, dict_rows):
    from gspread.utils import rowcol_to_a1
    data = [header] + [[_cell(d.get(c)) for c in header] for d in dict_rows]
    need_r, need_c = max(len(data), 2), len(header)
    if ws.row_count < need_r or ws.col_count < need_c:
        ws.resize(rows=max(ws.row_count, need_r + 50), cols=max(ws.col_count, need_c))
    ws.update(range_name="A1", values=data, value_input_option="RAW")
    if ws.row_count > len(data):
        ws.batch_clear([f"A{len(data) + 1}:{rowcol_to_a1(ws.row_count, max(need_c, ws.col_count))}"])


def _append(ws, header, rows):
    data = [[_cell(r.get(c)) for c in header] for r in rows]
    for i in range(0, len(data), 5000):
        ws.append_rows(data[i:i + 5000], value_input_option="RAW",
                       insert_data_option="INSERT_ROWS", table_range="A1")


def _ids_in(ws):
    try:
        return {_norm(v) for v in ws.col_values(1)[1:] if v not in ("", None)}
    except Exception:
        return set()


def _merge(existing, new, key_cols):
    table = {}
    for d in existing:
        k = tuple(_norm(d.get(c)) for c in key_cols)
        if all(k):
            table[k] = d
    for d in new:
        k = tuple(_norm(d.get(c)) for c in key_cols)
        if not all(k):
            continue
        merged = dict(table.get(k, {}))
        merged.update({c: v for c, v in d.items() if v not in _EMPTY})
        table[k] = merged
    return list(table.values())


# ------------------------------------------------------------ main pipeline
def sync(api, sh, days_back, payload=None):
    now = datetime.now(LOCAL_TZ)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    today = now.date()
    days = [today - timedelta(days=i) for i in range(days_back)]
    print(f"[sheets] syncing {days_back} day(s): {min(days)} .. {today}")

    # 1. Daily metrics
    daily = fetch_daily(api, days, stamp)
    ws_daily = _worksheet(sh, "Daily", DAILY_HEADER)
    merged_daily = sorted(_merge(_read_dicts(ws_daily), daily, ["date"]), key=lambda d: _norm(d.get("date")))
    _write(ws_daily, DAILY_HEADER, merged_daily)
    print(f"[sheets] Daily: {len(merged_daily)} rows")

    # 2. Runs
    summaries = fetch_run_summaries(api, min(days), today)
    ws_laps = _worksheet(sh, "Laps", LAPS_HEADER)
    ws_track = _worksheet(sh, "Track", TRACK_HEADER)
    have_laps, have_track = _ids_in(ws_laps), _ids_in(ws_track)
    new_runs, lap_buf, track_buf = [], [], []
    for n, a in enumerate(summaries, start=1):
        aid = str(a["activityId"])
        needs_detail = aid not in have_laps or aid not in have_track
        zones = _hr_zones(api, aid) if needs_detail and a.get("hrTimeInZone_1") is None else None
        row = run_row(a, stamp, zones)
        new_runs.append(row)
        if aid not in have_laps:
            lap_buf += build_laps(aid, row["date"], _call(api, "get_activity_splits", aid))
        if aid not in have_track:
            track_buf += build_track(aid, row["date"], _call(api, "get_activity_details", aid, 2000, 4000))
        if len(lap_buf) + len(track_buf) > 3000 or n == len(summaries):  # flush so progress survives a failure
            if lap_buf:
                _append(ws_laps, LAPS_HEADER, lap_buf)
            if track_buf:
                _append(ws_track, TRACK_HEADER, track_buf)
            print(f"[sheets] runs {n}/{len(summaries)}: +{len(lap_buf)} lap rows, +{len(track_buf)} track rows")
            lap_buf, track_buf = [], []

    dash_types = _dashboard_types(payload)
    for r in new_runs:
        if r["activity_id"] in dash_types:
            r["type"], r["type_source"] = dash_types[r["activity_id"]], "dashboard"
    ws_runs = _worksheet(sh, "Runs", RUNS_HEADER)
    all_runs = _merge(_read_dicts(ws_runs), new_runs, ["activity_id"])
    classify_types(all_runs)
    all_runs.sort(key=lambda d: (_norm(d.get("date")), _norm(d.get("start_time"))))
    _write(ws_runs, RUNS_HEADER, all_runs)
    print(f"[sheets] Runs: {len(all_runs)} rows ({len(new_runs)} fetched this sync)")

    # 3. Derived tabs
    weekly = build_weekly(all_runs)
    _write(_worksheet(sh, "Weekly", WEEKLY_HEADER), WEEKLY_HEADER, weekly)
    plan = build_plan(payload)
    if plan:
        _write(_worksheet(sh, "PlanVsActual", PLAN_HEADER), PLAN_HEADER, plan)
    dict_rows = [{"tab": t, "column": c, "meaning": m} for t, c, m in DICTIONARY]
    _write(_worksheet(sh, "Dictionary", ["tab", "column", "meaning"]), ["tab", "column", "meaning"], dict_rows)
    print(f"[sheets] Weekly: {len(weekly)} rows, PlanVsActual: {len(plan)} rows")
    if _err_counts:
        print(f"[sheets] Garmin calls that failed (fields left blank): {_err_counts}")


def _open_sheet():
    import gspread
    return gspread.service_account_from_dict(json.loads(os.environ["GOOGLE_SA_JSON"])).open_by_key(
        os.environ["SHEET_ID"].strip())


def export_to_sheets(payload=None, garmin=None):
    """Daily-sync entry point. Never raises, so it can't break the dashboard."""
    try:
        if not os.environ.get("GOOGLE_SA_JSON", "").strip() or not os.environ.get("SHEET_ID", "").strip():
            print("[sheets] GOOGLE_SA_JSON / SHEET_ID not set; skipping export")
            return
        sync(garmin or _login(), _open_sheet(), DAILY_LOOKBACK_DAYS, payload)
    except Exception as e:
        print(f"[sheets] export failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        n = int(sys.argv[sys.argv.index("--backfill") + 1])
        sync(_login(), _open_sheet(), n, None)
    else:
        sys.exit("usage: python sheets_export.py --backfill 120")
