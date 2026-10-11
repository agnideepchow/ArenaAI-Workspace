"""AquaPulse web server: serves the dashboard and a small JSON API."""
import threading

from flask import Flask, jsonify, render_template, request

from aquapulse.engine import SCENARIOS, Network

app = Flask(__name__, static_folder="static", template_folder="templates")
net = Network()
lock = threading.Lock()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/overview")
def overview():
    with lock:
        return jsonify(net.overview())


@app.get("/api/site/<site_id>")
def site(site_id):
    try:
        with lock:
            return jsonify(net.site_detail(site_id))
    except KeyError as exc:
        return jsonify(error=str(exc)), 404


@app.post("/api/tick")
def tick():
    with lock:
        net.tick()
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
        return jsonify(net.overview())


@app.post("/api/upload")
def upload():
    file = request.files.get("file")
    if not file:
        return jsonify(error="no file uploaded"), 400
    try:
        text = file.read().decode("utf-8-sig")
        with lock:
            result = net.ingest_csv(text)
        return jsonify(result)
    except (ValueError, UnicodeDecodeError) as exc:
        return jsonify(error=str(exc)), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
