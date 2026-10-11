import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from aquapulse.engine import Network, evaluate, SITES


def _card(net, sid):
    return next(s for s in net.overview()["sites"] if s["id"] == sid)


def test_baseline_network_is_mostly_safe():
    net = Network()
    k = net.overview()["kpis"]
    assert k["sites"] == len(SITES)
    assert k["avg_score"] > 70


def test_contamination_is_unsafe_and_capped():
    net = Network()
    net.inject("tri-01", "contamination")
    net.tick()
    e = _card(net, "tri-01")["eval"]
    assert e["verdict"] == "UNSAFE"
    assert e["score"] <= 59


def test_leak_detected_from_flow_and_pressure():
    net = Network()
    net.inject("erd-01", "leak")
    net.tick()
    assert _card(net, "erd-01")["eval"]["leak"] >= 50


def test_chlorine_failure_drops_residual():
    net = Network()
    net.inject("cbe-01", "chlorine_drop")
    net.tick()
    assert _card(net, "cbe-01")["eval"]["verdict"] == "UNSAFE"


def test_scenario_expires():
    net = Network()
    net.inject("kkd-01", "flood")
    for _ in range(6):
        net.tick()
    assert _card(net, "kkd-01")["scenario"] is None


def test_csv_ingest_accepts_valid_and_rejects_bad_rows():
    net = Network()
    csv_text = (
        "site_id,timestamp,ph,turbidity,do,chlorine,ecoli\n"
        "chn-02,2026-10-11T09:00:00+05:30,7.1,0.5,5.8,0.35,0\n"
        "nope,2026-10-11T09:00:00+05:30,7.1,0.5,5.8,0.35,0\n"
        "mdu-01,not-a-date,7.1,0.5,5.8,0.35,0\n"
    )
    r = net.ingest_csv(csv_text)
    assert r["accepted"] == 1
    assert r["rejected_count"] == 2


def test_json_device_ingest_scores_reading():
    net = Network()
    r = net.ingest_json([{"site_id": "vlr-01", "ph": 7.0, "turbidity": 0.4, "do": 6.5,
                          "chlorine": 0.3, "ecoli": 0}])
    assert r["accepted"] == 1 and r["rejected_count"] == 0


def test_live_rainfall_raises_outbreak_risk():
    from aquapulse.engine import SITES
    meta = SITES[0]
    r = Network().sites[meta["id"]]["history"][-1]
    dry = evaluate(meta, r, {"rain_24h": 0.0})["outbreak"]
    wet = evaluate(meta, r, {"rain_24h": 30.0, "river_trend": "rising"})["outbreak"]
    assert wet > dry
