"""AquaPulse web server: dashboard, JSON API, live feed and AI assistant."""
import threading

from flask import Flask, jsonify, render_template, request

from aquapulse import assistant, config
from aquapulse.engine import SCENARIOS, SITES, Network
from aquapulse.live import LiveFeed

app = Flask(__name__, static_folder="static", template_folder="templates")
net = Network()
feed = LiveFeed(SITES, ttl=600)
lock = threading.Lock()

threading.Thread(target=feed.loop, daemon=True).start()


def _with_live():
    """Push the latest live feed into the engine (cheap, lock-protected)."""
    net.set_live({s["id"]: feed.site(s["id"]) for s in SITES if feed.site(s["id"])})


def _browser_fallback_enabled() -> bool:
    return config.get("GEMINI_BROWSER_FALLBACK", "true").lower() == "true"


def _snapshot_text() -> str:
    _with_live()
    return assistant.build_snapshot({**net.overview(), "feed": feed.status()})


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def public_config():
    # The Geoapify key is needed by the browser to load map tiles.
    # The Gemini key is only exposed when the browser fallback is enabled in .env.
    key = config.get("GEMINI_API_KEY")
    return jsonify(
        geoapify_key=config.get("GEOAPIFY_API_KEY"),
        assistant_ready=assistant.is_configured(),
        assistant_model=config.get("GEMINI_MODEL", "gemini-2.5-flash"),
        gemini_browser_key=key if (key and _browser_fallback_enabled()) else "",
    )


@app.get("/api/overview")
def overview():
    with lock:
        _with_live()
        data = net.overview()
        data["feed"] = feed.status()
        return jsonify(data)


@app.get("/api/site/<site_id>")
def site(site_id):
    try:
        with lock:
            _with_live()
            return jsonify(net.site_detail(site_id))
    except KeyError as exc:
        return jsonify(error=str(exc)), 404


@app.post("/api/tick")
def tick():
    with lock:
        net.tick()
        _with_live()
        data = net.overview()
        data["feed"] = feed.status()
        return jsonify(data)


@app.post("/api/inject")
def inject():
    body = request.get_json(silent=True) or {}
    try:
        with lock:
            net.inject(body.get("site_id", ""), body.get("scenario", ""))
            return jsonify(ok=True, scenarios=list(SCENARIOS))
    except KeyError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/reset")
def reset():
    with lock:
        net.reset()
        _with_live()
        data = net.overview()
        data["feed"] = feed.status()
        return jsonify(data)


@app.post("/api/upload")
def upload():
    file = request.files.get("file")
    if not file:
        return jsonify(error="no file uploaded"), 400
    try:
        text = file.read().decode("utf-8-sig")
        with lock:
            return jsonify(net.ingest_csv(text))
    except (ValueError, UnicodeDecodeError) as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/ingest")
def ingest_device():
    """Real sensors / SCADA push readings here as JSON (one object or a list)."""
    token = config.get("INGEST_TOKEN")
    if token and request.headers.get("X-Api-Key") != token:
        return jsonify(error="invalid or missing X-Api-Key"), 401
    body = request.get_json(silent=True)
    if body is None:
        return jsonify(error="expected a JSON object or list"), 400
    items = body if isinstance(body, list) else [body]
    with lock:
        return jsonify(net.ingest_json(items))


@app.post("/api/live/push")
def live_push():
    """Browser relays Open-Meteo data when the server cannot reach it."""
    body = request.get_json(silent=True) or {}
    data = body.get("sites") or {}
    if not isinstance(data, dict):
        return jsonify(error="expected {sites: {site_id: {...}}}"), 400
    feed.set_external(data)
    return jsonify(feed.status())


@app.get("/api/chat-context")
def chat_context():
    """System prompt + live snapshot, used when the browser calls Gemini directly."""
    with lock:
        snapshot = _snapshot_text()
    return jsonify(system=assistant.system_text(snapshot), snapshot=snapshot,
                   model=config.get("GEMINI_MODEL", "gemini-2.5-flash"))


@app.post("/api/chat")
def chat():
    body = request.get_json(silent=True) or {}
    messages = body.get("messages") or []
    if not messages:
        return jsonify(error="no messages"), 400
    if not assistant.is_configured():
        return jsonify(error="The AI assistant is not configured. Add GEMINI_API_KEY to .env and restart."), 503
    with lock:
        snapshot = _snapshot_text()
    try:
        return jsonify(reply=assistant.ask(messages, snapshot))
    except assistant.AssistantError as exc:
        return jsonify(error=str(exc), unreachable=exc.unreachable), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
