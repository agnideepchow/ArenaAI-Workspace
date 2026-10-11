"""AquaPulse analytics engine.

Pure-Python simulation + analytics for a network of drinking-water sources.
Thresholds approximate BIS IS 10500 / WHO drinking-water guidance. Outbreak and
leak indices are transparent heuristics meant for early warning, not diagnosis.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
import random
from collections import deque

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
HISTORY_LEN = 240        # readings kept per site (10 simulated days)
BACKFILL_HOURS = 72      # history generated at start-up

SITES = [
    dict(id="chn-01", name="Chennai · Kilpauk Reservoir", district="Chennai", lat=13.0827, lon=80.2707,
         population=85000, source="Surface reservoir", ph=7.4, turb=1.2, do=7.2, cl=0.60, ecoli_p=0.010, temp=29, flow=420, pressure=3.1),
    dict(id="chn-02", name="Chennai · Velachery Borewell Cluster", district="Chennai", lat=12.9815, lon=80.2180,
         population=120000, source="Groundwater", ph=7.1, turb=0.5, do=5.8, cl=0.35, ecoli_p=0.020, temp=28, flow=300, pressure=2.6),
    dict(id="mdu-01", name="Madurai · Vaigai Intake", district="Madurai", lat=9.9252, lon=78.1198,
         population=210000, source="River intake", ph=7.6, turb=2.1, do=6.4, cl=0.50, ecoli_p=0.015, temp=30, flow=380, pressure=2.8),
    dict(id="cbe-01", name="Coimbatore · Siruvani Feed", district="Coimbatore", lat=11.0168, lon=76.9558,
         population=160000, source="Hill reservoir", ph=7.2, turb=0.8, do=7.8, cl=0.70, ecoli_p=0.005, temp=27, flow=350, pressure=3.0),
    dict(id="tri-01", name="Tiruchirappalli · Kaveri Intake", district="Tiruchirappalli", lat=10.7905, lon=78.7047,
         population=150000, source="River intake", ph=7.5, turb=1.8, do=6.9, cl=0.55, ecoli_p=0.015, temp=30, flow=360, pressure=2.7),
    dict(id="slm-01", name="Salem · Shevapet Tank", district="Salem", lat=11.6643, lon=78.1460,
         population=110000, source="Lake tank", ph=7.3, turb=2.4, do=6.1, cl=0.45, ecoli_p=0.020, temp=29, flow=260, pressure=2.5),
    dict(id="vlr-01", name="Vellore · Palar Well Field", district="Vellore", lat=12.9165, lon=79.1325,
         population=90000, source="Groundwater", ph=7.0, turb=0.4, do=5.5, cl=0.30, ecoli_p=0.010, temp=28, flow=220, pressure=2.4),
    dict(id="tjv-01", name="Thanjavur · Cauvery Delta Node", district="Thanjavur", lat=10.7870, lon=79.1378,
         population=75000, source="Canal", ph=7.7, turb=3.0, do=6.0, cl=0.40, ecoli_p=0.030, temp=31, flow=200, pressure=2.2),
    dict(id="tvl-01", name="Tirunelveli · Thamirabarani Intake", district="Tirunelveli", lat=8.7139, lon=77.7567,
         population=130000, source="River intake", ph=7.4, turb=1.5, do=6.6, cl=0.50, ecoli_p=0.015, temp=30, flow=280, pressure=2.6),
    dict(id="erd-01", name="Erode · Bhavani Confluence", district="Erode", lat=11.3410, lon=77.7172,
         population=140000, source="River intake", ph=7.3, turb=1.9, do=6.3, cl=0.50, ecoli_p=0.015, temp=29, flow=300, pressure=2.7),
    dict(id="cdl-01", name="Cuddalore · Coastal Aquifer", district="Cuddalore", lat=11.7480, lon=79.7714,
         population=95000, source="Coastal aquifer", ph=7.0, turb=0.9, do=5.2, cl=0.25, ecoli_p=0.025, temp=30, flow=180, pressure=2.3),
    dict(id="kkd-01", name="Kanyakumari · Island Tank", district="Kanyakumari", lat=8.0883, lon=77.5385,
         population=40000, source="Rainwater tank", ph=6.9, turb=1.1, do=6.8, cl=0.60, ecoli_p=0.020, temp=30, flow=120, pressure=2.0),
]

SCENARIOS = {
    "contamination": {"label": "Sewage contamination", "ticks": 8,
                      "desc": "Sewage seeps into the source: E. coli spikes, turbidity rises, chlorine is consumed."},
    "chlorine_drop": {"label": "Chlorine failure", "ticks": 8,
                      "desc": "Dosing pump fails: free chlorine residual collapses."},
    "leak": {"label": "Pipe leak", "ticks": 10,
             "desc": "Burst main: flow runs above the normal diurnal pattern while pressure sags."},
    "flood": {"label": "Monsoon flood", "ticks": 6,
              "desc": "Heavy rain washes runoff into the intake: turbidity and E. coli surge."},
}

# status bands: good = SAFE, warn = CAUTION, otherwise BAD
BANDS = {
    "ph": dict(label="pH", unit="", good=(6.5, 8.5), warn=(6.2, 8.9)),
    "turbidity": dict(label="Turbidity", unit="NTU", good=(0, 1.0), warn=(0, 5.0)),
    "do": dict(label="Dissolved O₂", unit="mg/L", good=(6.0, 99), warn=(5.0, 99)),
    "chlorine": dict(label="Free chlorine", unit="mg/L", good=(0.2, 1.0), warn=(0.1, 1.5)),
    "ecoli": dict(label="E. coli", unit="CFU/100 mL", good=(0, 0), warn=(0, 0)),
}


def now_ist() -> dt.datetime:
    return dt.datetime.now(IST).replace(minute=0, second=0, microsecond=0)


def parse_ts(value: str) -> dt.datetime:
    ts = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)
    return ts.astimezone(IST)


def expected_values(meta: dict, ts: dt.datetime) -> dict:
    """Deterministic diurnal baseline: flow low at night, pressure high at night."""
    h = ts.hour + ts.minute / 60
    phase = math.sin((h - 9) / 24 * 2 * math.pi)  # peaks ~15:00, troughs ~03:00
    return {
        "temperature": meta["temp"] + 2.5 * phase,
        "flow": meta["flow"] * (1 + 0.35 * phase),
        "pressure": meta["pressure"] - 0.25 * phase,
    }


def make_reading(meta: dict, ts: dt.datetime, rng: random.Random) -> dict:
    exp = expected_values(meta, ts)
    rain = rng.expovariate(1 / 4) if rng.random() < 0.18 else 0.0  # NE-monsoon showers
    return {
        "timestamp": ts.isoformat(),
        "ph": round(meta["ph"] + rng.gauss(0, 0.08), 2),
        "turbidity": round(max(0.05, meta["turb"] + rng.gauss(0, 0.25)), 2),
        "do": round(max(0.0, meta["do"] + rng.gauss(0, 0.3)), 2),
        "chlorine": round(max(0.0, meta["cl"] + rng.gauss(0, 0.03)), 3),
        "ecoli": 0 if rng.random() > meta["ecoli_p"] else rng.randint(1, 4),
        "temperature": round(exp["temperature"] + rng.gauss(0, 0.3), 1),
        "flow": round(max(0.0, exp["flow"] * (1 + rng.gauss(0, 0.03))), 1),
        "pressure": round(exp["pressure"] + rng.gauss(0, 0.04), 2),
        "rainfall": round(rain, 1),
    }


def apply_scenario(name: str, r: dict, rng: random.Random) -> None:
    if name == "contamination":
        r["ecoli"] = rng.randint(30, 400)
        r["turbidity"] = round(r["turbidity"] + 5.0 + rng.random() * 2, 2)
        r["chlorine"] = round(min(r["chlorine"], 0.05), 3)
        r["do"] = round(max(0.0, r["do"] - 2.0), 2)
    elif name == "chlorine_drop":
        r["chlorine"] = round(rng.uniform(0.0, 0.08), 3)
    elif name == "leak":
        r["flow"] = round(r["flow"] * 1.30, 1)
        r["pressure"] = round(r["pressure"] - 0.7, 2)
    elif name == "flood":
        r["rainfall"] = round(rng.uniform(45, 90), 1)
        r["turbidity"] = round(r["turbidity"] + 14, 2)
        r["ecoli"] = rng.randint(50, 500)
        r["ph"] = round(r["ph"] - 0.4, 2)


def _status(key: str, value: float) -> str:
    band = BANDS[key]
    lo, hi = band["good"]
    if lo <= value <= hi:
        return "ok"
    lo, hi = band["warn"]
    if lo <= value <= hi:
        return "warn"
    return "bad"


def evaluate(meta: dict, r: dict) -> dict:
    """Score a single reading. Returns safety index, verdict, risks and explanations."""
    # --- Safety index (100 = pristine) ---
    penalty = 0.0
    ph = r["ph"]
    penalty += min(30, max(0.0, 6.5 - ph, ph - 8.5) * 60)
    penalty += min(30, max(0.0, r["turbidity"] - 1.0) * 6)
    penalty += min(15, max(0.0, 5.0 - r["do"]) * 5)
    cl = r["chlorine"]
    if cl < 0.2:
        penalty += min(35, (0.2 - cl) * 250)
    elif cl > 1.0:
        penalty += min(10, (cl - 1.0) * 10)
    if r["ecoli"] > 0:
        penalty += min(50, 20 + math.log10(r["ecoli"] + 1) * 10)
    score = max(0.0, 100.0 - penalty)
    if r["ecoli"] > 0:
        score = min(score, 59.0)

    if r["ecoli"] > 0 or cl < 0.1 or score < 60:
        verdict, verdict_label = "UNSAFE", "Unsafe · do not drink untreated"
    elif score < 80:
        verdict, verdict_label = "CAUTION", "Caution · treat before use"
    else:
        verdict, verdict_label = "SAFE", "Safe to drink"

    # --- Sanitation / outbreak risk (0-100, heuristic) ---
    ecoli_t = min(45.0, math.log10(r["ecoli"] + 1) * 22)
    cl_t = min(20.0, max(0.0, 0.2 - cl) * 100)
    turb_t = min(15.0, max(0.0, r["turbidity"] - 1.0) * 2.5)
    pop_t = min(10.0, math.log10(meta["population"]) / 6 * 10)
    rain_t = min(10.0, r.get("rainfall", 0.0))
    outbreak = round(min(100.0, ecoli_t + cl_t + turb_t + pop_t + rain_t), 1)

    # --- Leak index (flow vs expected diurnal pattern + pressure sag) ---
    ts = parse_ts(r["timestamp"])
    exp = expected_values(meta, ts)
    flow_ratio = r["flow"] / exp["flow"] if exp["flow"] else 1.0
    p_drop = max(0.0, exp["pressure"] - r["pressure"])
    leak = round(min(100.0, max(0.0, (flow_ratio - 1) * 180 + p_drop * 45)), 1)

    params = {}
    for key in ("ph", "turbidity", "do", "chlorine", "ecoli"):
        params[key] = {"label": BANDS[key]["label"], "unit": BANDS[key]["unit"],
                       "value": r[key], "status": _status(key, r[key])}
    params["temperature"] = {"label": "Temperature", "unit": "°C", "value": r["temperature"], "status": "info"}
    params["flow"] = {"label": "Flow", "unit": "m³/h", "value": r["flow"], "status": "info"}
    params["pressure"] = {"label": "Pressure", "unit": "bar", "value": r["pressure"], "status": "info"}

    insights = []
    if r["ecoli"] > 0:
        insights.append(dict(level="critical", text=(
            f"E. coli detected ({r['ecoli']} CFU/100 mL). Faecal contamination is present: issue a boil-water "
            "advisory, inspect the source and retest within 24 h.")))
    if cl < 0.1:
        insights.append(dict(level="critical", text=(
            f"Free chlorine is {cl:.2f} mg/L, almost no disinfection residual. Check the dosing pump and "
            "contact tank immediately.")))
    elif cl < 0.2:
        insights.append(dict(level="warn", text=(
            f"Free chlorine is {cl:.2f} mg/L, below the 0.2 mg/L residual target. Adjust dosing.")))
    if r["turbidity"] > 5:
        insights.append(dict(level="critical", text=(
            f"Turbidity {r['turbidity']} NTU is far above the 5 NTU limit. Suspect runoff or sewage. "
            "Check filters and intake screens.")))
    elif r["turbidity"] > 1:
        insights.append(dict(level="warn", text=(
            f"Turbidity {r['turbidity']} NTU is above the 1 NTU ideal. Inspect filtration.")))
    if r["do"] < 5:
        insights.append(dict(level="warn", text=(
            f"Dissolved oxygen {r['do']} mg/L is low, which suggests organic load decomposing in the source.")))
    if ph < 6.5 or ph > 8.5:
        insights.append(dict(level="warn", text=f"pH {ph} is outside the 6.5–8.5 drinking range."))
    if leak >= 50:
        insights.append(dict(level="critical", text=(
            f"Flow is {round((flow_ratio - 1) * 100)}% above the expected pattern and pressure is down "
            f"{p_drop:.2f} bar. This is consistent with a burst main. Dispatch a crew to verify.")))
    elif leak >= 25:
        insights.append(dict(level="warn", text=(
            f"Leak index {leak:.0f}/100. Watch night-flow for the next few hours.")))
    if outbreak >= 60:
        insights.append(dict(level="critical", text=(
            f"Outbreak risk is HIGH ({outbreak:.0f}/100) for {meta['population']:,} people. "
            "Notify the District Health Officer.")))
    elif outbreak >= 35:
        insights.append(dict(level="warn", text=(
            f"Outbreak risk is elevated ({outbreak:.0f}/100). Increase residual monitoring.")))
    if not insights:
        insights.append(dict(level="ok", text="All measured parameters are inside safe limits. Keep routine sampling."))

    return {
        "score": round(score, 1),
        "verdict": verdict,
        "verdict_label": verdict_label,
        "outbreak": outbreak,
        "outbreak_level": "high" if outbreak >= 60 else "elevated" if outbreak >= 35 else "low",
        "leak": leak,
        "leak_level": "high" if leak >= 50 else "watch" if leak >= 25 else "normal",
        "params": params,
        "insights": insights,
    }


class Network:
    """Holds the simulated sensor network and serves analytics views."""

    def __init__(self, seed: int = 7):
        self.seed = seed
        self.reset()

    # ---- state management -------------------------------------------------
    def reset(self) -> None:
        self.rng = random.Random(self.seed)
        self.now = now_ist()
        self.sites: dict[str, dict] = {}
        for meta in SITES:
            self.sites[meta["id"]] = {"meta": meta, "history": deque(maxlen=HISTORY_LEN),
                                      "scenario": None, "ticks_left": 0}
        start = self.now - dt.timedelta(hours=BACKFILL_HOURS - 1)
        for h in range(BACKFILL_HOURS):
            ts = start + dt.timedelta(hours=h)
            for st in self.sites.values():
                st["history"].append(make_reading(st["meta"], ts, self.rng))

    def _new_reading(self, st: dict, ts: dt.datetime) -> dict:
        r = make_reading(st["meta"], ts, self.rng)
        if st["scenario"]:
            apply_scenario(st["scenario"], r, self.rng)
        return r

    def tick(self) -> None:
        """Advance the whole network by one simulated hour."""
        self.now += dt.timedelta(hours=1)
        for st in self.sites.values():
            st["history"].append(self._new_reading(st, self.now))
            if st["scenario"]:
                st["ticks_left"] -= 1
                if st["ticks_left"] <= 0:
                    st["scenario"], st["ticks_left"] = None, 0

    def inject(self, site_id: str, scenario: str) -> None:
        if site_id not in self.sites:
            raise KeyError(f"unknown site {site_id}")
        if scenario not in SCENARIOS:
            raise KeyError(f"unknown scenario {scenario}")
        st = self.sites[site_id]
        st["scenario"] = scenario
        st["ticks_left"] = SCENARIOS[scenario]["ticks"]

    # ---- views ------------------------------------------------------------
    def _latest(self, st: dict) -> dict:
        r = st["history"][-1]
        return evaluate(st["meta"], r)

    def _site_card(self, st: dict) -> dict:
        meta = st["meta"]
        latest = self._latest(st)
        spark = [evaluate(meta, r)["score"] for r in list(st["history"])[-24:]]
        scen = st["scenario"]
        return {**meta, "eval": latest, "sparkline": spark,
                "scenario": {"id": scen, "label": SCENARIOS[scen]["label"],
                             "ticks_left": st["ticks_left"]} if scen else None,
                "latest_timestamp": st["history"][-1]["timestamp"]}

    def alerts(self, limit: int = 14) -> list[dict]:
        out = []
        for st in self.sites.values():
            meta = st["meta"]
            prev = None
            for r in list(st["history"])[-72:]:
                ev = evaluate(meta, r)
                flags = {
                    "ecoli": r["ecoli"] > 0 and (prev is None or prev["ecoli"] == 0),
                    "chlorine": r["chlorine"] < 0.1 and (prev is None or prev["chlorine"] >= 0.1),
                    "leak": ev["leak"] >= 50 and (prev is None or prev["_leak"] < 50),
                    "outbreak": ev["outbreak"] >= 60 and (prev is None or prev["_outbreak"] < 60),
                    "turbidity": r["turbidity"] > 5 and (prev is None or prev["turbidity"] <= 5),
                }
                msgs = {
                    "ecoli": ("critical", "Faecal contamination", f"E. coli {r['ecoli']} CFU/100 mL"),
                    "chlorine": ("high", "Chlorine residual lost", f"Free chlorine {r['chlorine']:.2f} mg/L"),
                    "leak": ("high", "Leak suspected", f"Leak index {ev['leak']:.0f}/100, pressure {r['pressure']} bar"),
                    "outbreak": ("critical", "Outbreak risk high", f"Risk {ev['outbreak']:.0f}/100"),
                    "turbidity": ("medium", "Turbidity spike", f"Turbidity {r['turbidity']} NTU"),
                }
                for key, on in flags.items():
                    if on:
                        sev, title, msg = msgs[key]
                        out.append({"site_id": meta["id"], "site_name": meta["name"], "severity": sev,
                                    "title": title, "message": msg, "timestamp": r["timestamp"]})
                r_copy = dict(r)
                r_copy["_leak"], r_copy["_outbreak"] = ev["leak"], ev["outbreak"]
                prev = r_copy
        out.sort(key=lambda a: a["timestamp"], reverse=True)
        return out[:limit]

    def overview(self) -> dict:
        cards = [self._site_card(st) for st in self.sites.values()]
        latest = [c["eval"]["score"] for c in cards]
        unsafe = [c for c in cards if c["eval"]["verdict"] == "UNSAFE"]
        at_risk = [c for c in cards if c["eval"]["verdict"] != "SAFE"]
        kpis = {
            "sites": len(cards),
            "avg_score": round(sum(latest) / len(latest), 1),
            "unsafe": len(unsafe),
            "caution": sum(1 for c in cards if c["eval"]["verdict"] == "CAUTION"),
            "population_at_risk": sum(c["population"] for c in at_risk),
            "leak_alerts": sum(1 for c in cards if c["eval"]["leak"] >= 50),
            "outbreak_high": sum(1 for c in cards if c["eval"]["outbreak"] >= 60),
        }
        return {"now": self.now.isoformat(), "kpis": kpis, "sites": cards,
                "alerts": self.alerts(), "scenarios": SCENARIOS}

    def site_detail(self, site_id: str) -> dict:
        if site_id not in self.sites:
            raise KeyError(f"unknown site {site_id}")
        st = self.sites[site_id]
        meta = st["meta"]
        series = []
        for r in list(st["history"])[-72:]:
            ev = evaluate(meta, r)
            series.append({"timestamp": r["timestamp"], "score": ev["score"], "chlorine": r["chlorine"],
                           "turbidity": r["turbidity"], "ecoli": r["ecoli"], "leak": ev["leak"],
                           "outbreak": ev["outbreak"], "flow": r["flow"], "pressure": r["pressure"]})
        return {"site": {**meta, "scenario": st["scenario"]}, "latest": self._site_card(st),
                "series": series}

    # ---- CSV ingest -------------------------------------------------------
    REQUIRED = ("site_id", "timestamp", "ph", "turbidity", "do", "chlorine", "ecoli")
    OPTIONAL = ("temperature", "flow", "pressure", "rainfall")

    def ingest_csv(self, text: str) -> dict:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        fields = {f.strip().lower(): f for f in reader.fieldnames if f}
        missing = [c for c in self.REQUIRED if c not in fields]
        if missing:
            raise ValueError(f"missing required columns: {', '.join(missing)}")

        accepted: list[tuple[str, dict]] = []
        rejected: list[str] = []
        for line_no, raw in enumerate(reader, start=2):
            row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
            site_id = row.get("site_id", "")
            if site_id not in self.sites:
                rejected.append(f"line {line_no}: unknown site_id '{site_id}'")
                continue
            try:
                ts = parse_ts(row["timestamp"])
                r = {"timestamp": ts.isoformat()}
                for key in ("ph", "turbidity", "do", "chlorine"):
                    r[key] = float(row[key])
                r["ecoli"] = int(float(row["ecoli"]))
                meta = self.sites[site_id]["meta"]
                exp = expected_values(meta, ts)
                r["temperature"] = float(row["temperature"]) if row.get("temperature") else round(exp["temperature"], 1)
                r["flow"] = float(row["flow"]) if row.get("flow") else round(exp["flow"], 1)
                r["pressure"] = float(row["pressure"]) if row.get("pressure") else round(exp["pressure"], 2)
                r["rainfall"] = float(row["rainfall"]) if row.get("rainfall") else 0.0
            except (ValueError, KeyError) as exc:
                rejected.append(f"line {line_no}: {exc}")
                continue
            accepted.append((site_id, r))

        analysis = []
        touched = set()
        for site_id, r in accepted:
            st = self.sites[site_id]
            st["history"].append(r)  # merged below in timestamp order
            touched.add(site_id)
        for site_id in touched:
            st = self.sites[site_id]
            ordered = sorted(st["history"], key=lambda x: parse_ts(x["timestamp"]))
            st["history"] = deque(ordered[-HISTORY_LEN:], maxlen=HISTORY_LEN)
        for site_id, r in accepted[:200]:
            ev = evaluate(self.sites[site_id]["meta"], r)
            analysis.append({"site_id": site_id, "site_name": self.sites[site_id]["meta"]["name"],
                             "timestamp": r["timestamp"], "score": ev["score"], "verdict": ev["verdict"],
                             "outbreak": ev["outbreak"], "leak": ev["leak"],
                             "top_issue": ev["insights"][0]["text"]})
        return {"accepted": len(accepted), "rejected": rejected[:20], "rejected_count": len(rejected),
                "sites_updated": sorted(touched), "analysis": analysis}
