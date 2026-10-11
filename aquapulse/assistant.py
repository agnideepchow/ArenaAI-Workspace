"""AI assistant: Google Gemini, grounded in the live AquaPulse network snapshot.

The Gemini key is read on the server only and never sent to the browser.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config


class AssistantError(RuntimeError):
    pass


SYSTEM_PROMPT = """You are AquaPulse Assistant, an expert in drinking-water safety, sanitation and
public health for Tamil Nadu, India. You answer questions about the AquaPulse dashboard.

Rules:
- Ground every factual claim in the NETWORK SNAPSHOT below. Quote values exactly. Never invent readings.
- If something is not in the snapshot, say so and state what data or sensor would be needed.
- Be concise and practical for field operators: short paragraphs or bullets, plain language.
- For unsafe water, advise boiling or treating before use and notifying the local health officer.
- Standards: BIS IS 10500 and WHO guidelines (pH 6.5-8.5; turbidity 1 NTU ideal, 5 NTU limit;
  free residual chlorine at least 0.2 mg/L; E. coli 0 CFU/100 mL).
- Safety index, outbreak risk and leak index are heuristic early-warning scores, not lab results.
- Sensor readings are simulated until a device feed is connected. Weather and river data are live."""


def is_configured() -> bool:
    return bool(config.get("GEMINI_API_KEY"))


def build_snapshot(overview: dict) -> str:
    lines = [f"Time (IST): {overview['now']}", f"Network KPIs: {overview['kpis']}"]
    feed = overview.get("feed", {})
    lines.append(f"Live feed: ok={feed.get('ok')} updated={feed.get('updated')} error={feed.get('error')}")
    for s in overview["sites"]:
        e, p, live = s["eval"], s["eval"]["params"], s.get("live") or {}
        lines.append(
            f"- {s['name']} | {s['district']} | source: {s['source']} | population {s['population']} | "
            f"verdict {e['verdict']} | safety {e['score']}/100 | outbreak risk {e['outbreak']} | leak index {e['leak']} | "
            f"pH {p['ph']['value']} | turbidity {p['turbidity']['value']} NTU | DO {p['do']['value']} mg/L | "
            f"chlorine {p['chlorine']['value']} mg/L | E. coli {p['ecoli']['value']} CFU/100mL | "
            f"flow {p['flow']['value']} m3/h | pressure {p['pressure']['value']} bar | "
            f"live temp {live.get('temperature')} C | live rain 24h {live.get('rain_24h')} mm | "
            f"river discharge {live.get('river_discharge')} m3/s ({live.get('river_trend')}) | "
            f"active scenario {s['scenario']['label'] if s.get('scenario') else 'none'}")
    if overview["alerts"]:
        lines.append("Recent alerts:")
        for a in overview["alerts"][:10]:
            lines.append(f"- [{a['severity']}] {a['timestamp']} {a['site_name']}: {a['title']} ({a['message']})")
    else:
        lines.append("Recent alerts: none")
    return "\n".join(lines)


def ask(messages: list[dict], snapshot: str) -> str:
    key = config.get("GEMINI_API_KEY")
    if not key:
        raise AssistantError("GEMINI_API_KEY is not set. Add it to the .env file and restart the server.")
    model = config.get("GEMINI_MODEL", "gemini-2.5-flash")

    contents = []
    for m in messages[-12:]:
        role = "model" if m.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": str(m.get("content", ""))[:4000]}]})
    while contents and contents[0]["role"] != "user":
        contents.pop(0)
    if not contents:
        raise AssistantError("Empty question.")

    body = {
        "systemInstruction": {"parts": [{"text": f"{SYSTEM_PROMPT}\n\nNETWORK SNAPSHOT:\n{snapshot}"}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 900},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:300]
        raise AssistantError(f"Gemini returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise AssistantError(f"Could not reach Gemini: {exc.reason}") from exc

    try:
        text = "".join(part.get("text", "") for part in data["candidates"][0]["content"]["parts"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise AssistantError("Gemini returned no answer (it may have been blocked by a safety filter).") from exc
    if not text:
        raise AssistantError("Gemini returned an empty answer.")
    return text
