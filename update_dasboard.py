import os
from datetime import datetime, timedelta
from garminconnect import Garmin

EMAIL = os.environ["GARMIN_EMAIL"]
PASSWORD = os.environ["GARMIN_PASSWORD"]

client = Garmin(EMAIL, PASSWORD)
client.login()

today = datetime.now().date()
start_90 = today - timedelta(days=90)
start_30 = today - timedelta(days=30)
# VO2 max endpoint is unreliable beyond ~60 day windows
start_vo2 = today - timedelta(days=55)

# ---- Runs (last 90 days) ----
activities = client.get_activities(0, 100)
runs = [a for a in activities if "running" in a.get("activityType", {}).get("typeKey", "")]

run_rows = ""
weekly_volume = {}
for r in runs:
    date_str = r.get("startTimeLocal", "")[:10]
    distance_km = round((r.get("distance") or 0) / 1000, 2)
    duration_min = round((r.get("duration") or 0) / 60, 1)
    avg_hr = r.get("averageHR", "—")
    elev_gain = r.get("elevationGain", "—")
    name = r.get("activityName", "Run")

    run_rows += f"<tr><td>{date_str}</td><td>{name}</td><td>{distance_km} km</td><td>{duration_min} min</td><td>{avg_hr}</td><td>{elev_gain}</td></tr>\n"

    try:
        week_key = datetime.strptime(date_str, "%Y-%m-%d").isocalendar()[1]
        weekly_volume[week_key] = weekly_volume.get(week_key, 0) + distance_km
    except Exception:
        pass

weekly_rows = ""
for week, km in sorted(weekly_volume.items()):
    weekly_rows += f"<tr><td>Week {week}</td><td>{round(km, 1)} km</td></tr>\n"

# ---- Splits for most recent run ----
splits_html = "<p>No recent run found.</p>"
if runs:
    latest_id = runs[0]["activityId"]
    try:
        splits = client.get_activity_splits(latest_id)
        laps = splits.get("lapDTOs", [])
        rows = ""
        for i, lap in enumerate(laps, start=1):
            pace = lap.get("averageSpeed")
            pace_str = f"{round(1000 / pace / 60, 2)} min/km" if pace else "—"
            rows += f"<tr><td>{i}</td><td>{pace_str}</td><td>{lap.get('averageHR', '—')}</td></tr>\n"
        splits_html = f"<table><tr><th>Lap</th><th>Pace</th><th>Avg HR</th></tr>{rows}</table>"
    except Exception as e:
        splits_html = f"<p>Could not load splits: {e}</p>"

# ---- Health metrics (last 30 days) ----
def safe_call(fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        return f"Unavailable ({e})"

sleep_data = safe_call(client.get_sleep_data, str(today))
rhr_data = safe_call(client.get_rhr_day, str(today))
stress_data = safe_call(client.get_all_day_stress, str(today))
vo2_data = safe_call(client.get_max_metrics, str(today))

def extract(d, *keys, default="—"):
    try:
        for k in keys:
            d = d[k]
        return d
    except Exception:
        return default

sleep_hours = extract(sleep_data, "dailySleepDTO", "sleepTimeSeconds")
sleep_hours = round(sleep_hours / 3600, 1) if isinstance(sleep_hours, (int, float)) else sleep_hours

resting_hr = extract(rhr_data, "allMetrics", "metricsMap", "WELLNESS_RESTING_HEART_RATE", 0, "value")
avg_stress = extract(stress_data, "avgStressLevel")
vo2max = extract(vo2_data, 0, "generic", "vo2MaxPreciseValue") if isinstance(vo2_data, list) else "—"

# ---- Build the HTML page ----
html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Training Dashboard</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 20px; background: #fafafa; color: #222; }}
  h1 {{ font-size: 1.4em; }}
  h2 {{ font-size: 1.1em; margin-top: 2em; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee; font-size: 0.9em; }}
  .metric-row {{ display: flex; gap: 16px; flex-wrap: wrap; margin-top: 10px; }}
  .metric-box {{ background: white; border: 1px solid #ddd; border-radius: 8px; padding: 12px 16px; min-width: 120px; }}
  .metric-box .value {{ font-size: 1.4em; font-weight: 600; }}
  .metric-box .label {{ font-size: 0.8em; color: #777; }}
  .updated {{ color: #888; font-size: 0.8em; margin-top: 4px; }}
</style>
</head>
<body>
<h1>Training Dashboard</h1>
<div class="updated">Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}</div>

<h2>Today's Health Snapshot</h2>
<div class="metric-row">
  <div class="metric-box"><div class="value">{sleep_hours}</div><div class="label">Sleep (hrs)</div></div>
  <div class="metric-box"><div class="value">{resting_hr}</div><div class="label">Resting HR</div></div>
  <div class="metric-box"><div class="value">{avg_stress}</div><div class="label">Avg Stress</div></div>
  <div class="metric-box"><div class="value">{vo2max}</div><div class="label">VO2 Max</div></div>
</div>

<h2>Most Recent Run — Splits</h2>
{splits_html}

<h2>Weekly Volume (last ~13 weeks)</h2>
<table><tr><th>Week</th><th>Distance</th></tr>{weekly_rows}</table>

<h2>Run Log (last 90 days)</h2>
<table>
<tr><th>Date</th><th>Name</th><th>Distance</th><th>Duration</th><th>Avg HR</th><th>Elev Gain</th></tr>
{run_rows}
</table>

</body>
</html>
"""

with open("index.html", "w") as f:
    f.write(html)

print("Dashboard generated successfully.")
