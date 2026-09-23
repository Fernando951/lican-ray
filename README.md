# Huerto Lican Ray

Dashboard: https://fernando951.github.io/lican-ray/

- `index.html` — the dashboard. Reads state.json live from a secret Gist on every load. Logic in JS.
- `build_digest.py` — computes `digest.json` and `alerts.json` (separate Python copy of the same logic;
  change both together).
- `digest.json` / `alerts.json` — read by the weekly digest and daily alert scheduled tasks.
