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


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def public_config():
    # Geoapify tile key is required by the browser map; the Gemini key never leaves the server.
    return jsonify(geoapify_key=config.get("GEOAPIFY_API_KEY"),
                   assistant_ready=assistant.is_configured(),
                   assistant_model=config.get("GEMINI_MODEL", "gemini-2.5-flash"))


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
        return jsonify(net.overview())


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
        return jsonify(net.overview())


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


@app.post("/api/chat")
def chat():
    body = request.get_json(silent=True) or {}
    messages = body.get("messages") or []
    if not messages:
        return jsonify(error="no messages"), 400
    if not assistant.is_configured():
        return jsonify(error="The AI assistant is not configured yet. Add GEMINI_API_KEY to the .env file "
                             "and restart the server."), 503
    with lock:
        _with_live()
        snapshot = assistant.build_snapshot({**net.overview(), "feed": feed.status()})
    try:
        return jsonify(reply=assistant.ask(messages, snapshot))
    except assistant.AssistantError as exc:
        return jsonify(error=str(exc)), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
