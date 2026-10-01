name: Update Garmin Dashboard

on:
  schedule:
    - cron: '0 11 * * *'   # runs daily at 11:00 UTC — adjust to your preferred time
  workflow_dispatch:        # adds a manual "Run workflow" button

permissions:
  contents: write

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Pull in any browser-saved edits
        run: |
          git config user.name "github-actions"
          git config user.email "actions@github.com"
          # v16: checkboxes/notes/drag-and-drop swaps commit straight to
          # manual_data.json via the GitHub API, independently of this
          # workflow — so this pulls in any edit saved since this job's
          # checkout BEFORE the script below writes anything, while the
          # working tree is still clean. (A pull --rebase refuses to run once
          # there are local uncommitted changes, which is exactly what the
          # update script is about to create — so this has to happen first,
          # not right before the final commit.) This also means the page the
          # script builds today already reflects any edit saved since the
          # last sync, instead of needing a second sync to pick it up.
          git pull --rebase origin main
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install garminconnect
      - name: Run update script
        env:
          GARMIN_EMAIL: ${{ secrets.GARMIN_EMAIL }}
          GARMIN_PASSWORD: ${{ secrets.GARMIN_PASSWORD }}
          CARTO_API_KEY: ${{ secrets.CARTO_API_KEY }}   # optional, added in v10 — see Part A, Step 4. Safe to
                                                          # leave this line in even if you skip that step; an
                                                          # empty secret just means the map falls back to plain
                                                          # OpenStreetMap tiles.
          DASHBOARD_EDIT_TOKEN: ${{ secrets.DASHBOARD_EDIT_TOKEN }}   # optional, added in v16 — see Part A,
                                                          # Step 5. Safe to leave this line in even if you skip
                                                          # that step; an empty secret just means the editing
                                                          # controls show "not set up" instead of saving.
        run: python update_dashboard.py
      - name: Commit updated dashboard
        run: |
          git add index.html
          git commit -m "Auto-update dashboard" || echo "No changes to commit"
          git push
