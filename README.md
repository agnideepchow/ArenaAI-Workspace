# 💧 AquaPulse — Water Safety & Sanitation Command Center

**Track 2 · Code to Clean Water & Sanitation (SDG 6)**
Water Quality Monitoring & Analytics · Smart Water Management · Sanitation & Public Health

AquaPulse watches a network of drinking-water sources across Tamil Nadu, scores every reading, and turns raw sensor numbers into clear actions for operators and health officers.

## Features
- **Live network map** (dark-mode Leaflet) with safe / caution / unsafe markers.
- **Safety index (0–100)** per source, from pH, turbidity, dissolved O₂, chlorine residual, and E. coli, against BIS IS 10500 / WHO limits.
- **Outbreak-risk index**: faecal load, lost chlorine residual, turbidity, population exposed, and rainfall.
- **Leak index**: flow compared with the expected day/night demand curve, plus pressure sag. This is the Smart Water Management piece.
- **AI-style guidance** that explains *why* a source is flagged and what to do next.
- **Alert feed** of contamination, chlorine loss, leaks, outbreak risk, and turbidity spikes.
- **Break-it-on-purpose demo mode**: inject sewage contamination, chlorine failure, a pipe leak, or a monsoon flood, then advance time and watch the network respond.
- **Bulk CSV analysis**: drag in field readings to score them and merge them into the network.
- **Live mode** streams a new simulated hour every few seconds.

## Run it
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py          # http://localhost:8000
python -m pytest -q tests
```

## CSV format
Required columns: `site_id, timestamp, ph, turbidity, do, chlorine, ecoli`
Optional: `temperature, flow, pressure, rainfall`. See `static/sample_readings.csv`.

## Project layout
```
aquapulse/engine.py   analytics engine: simulation, scoring, alerts, CSV ingest
app.py                Flask server + JSON API
templates/index.html  dashboard markup
static/style.css      glassmorphism theme
static/app.js         map, charts, live updates, uploads
tests/                engine tests
```

## Honest notes
- Sensor data is **simulated** from realistic Tamil Nadu baselines so the demo is repeatable. The engine accepts real readings through CSV.
- Indices are transparent heuristics for early warning. They do not replace accredited lab testing or clinical diagnosis.
