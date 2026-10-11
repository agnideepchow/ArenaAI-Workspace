/* AquaPulse dashboard logic */
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const COLORS = { SAFE: "#34d399", CAUTION: "#fbbf24", UNSAFE: "#f43f5e" };
const RING_C = 2 * Math.PI * 34;

let map, markers = {}, chart = null, selectedId = null, liveTimer = null, scenarios = {}, lastSites = [];
const fmtTime = (iso) => new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {} });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 2800);
}

/* ---------- map ---------- */
let appConfig = { geoapify_key: "", assistant_ready: false, assistant_model: "" };
async function initMap() {
  try { appConfig = await api("/api/config"); } catch (e) { console.warn(e); }
  map = L.map("map", { zoomControl: false, scrollWheelZoom: false }).setView([10.6, 78.4], 7);
  if (appConfig.geoapify_key) {
    L.tileLayer(`https://maps.geoapify.com/v1/tile/dark-matter/{z}/{x}/{y}.png?apiKey=${encodeURIComponent(appConfig.geoapify_key)}`, {
      maxZoom: 20,
      attribution: '&copy; <a href="https://www.geoapify.com/">Geoapify</a> &copy; <a href="https://openmaptiles.org/">OpenMapTiles</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
  } else {
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", { maxZoom: 18 }).addTo(map);
  }
  L.control.zoom({ position: "bottomright" }).addTo(map);
}
function markerIcon(verdict, selected) {
  return L.divIcon({
    className: "",
    html: `<div class="mk mk-${verdict.toLowerCase()} ${selected ? "sel" : ""}"><i></i></div>`,
    iconSize: [22, 22], iconAnchor: [11, 11],
  });
}
function updateMarkers(sites) {
  sites.forEach((s) => {
    const v = s.eval.verdict;
    const popup = `<b>${esc(s.name)}</b><br>${esc(s.eval.verdict_label)}<br>Safety ${s.eval.score}/100`;
    if (!markers[s.id]) {
      markers[s.id] = L.marker([s.lat, s.lon], { icon: markerIcon(v, false) }).addTo(map).bindPopup(popup);
      markers[s.id].on("click", () => selectSite(s.id));
    } else {
      markers[s.id].setIcon(markerIcon(v, s.id === selectedId)).setPopupContent(popup);
    }
  });
}

/* ---------- overview ---------- */
function ringSvg(score, verdict) {
  const off = RING_C * (1 - Math.max(0, Math.min(100, score)) / 100);
  return `<div class="ring"><svg viewBox="0 0 80 80">
    <circle class="track" cx="40" cy="40" r="34"/>
    <circle class="bar" cx="40" cy="40" r="34" stroke="${COLORS[verdict]}" stroke-dasharray="${RING_C}" stroke-dashoffset="${off}" style="filter:drop-shadow(0 0 6px ${COLORS[verdict]}55)"/>
  </svg><div class="num">${Math.round(score)}<small>index</small></div></div>`;
}
function sparkSvg(points, verdict) {
  if (!points.length) return "";
  const w = 240, h = 42, min = 0, max = 100;
  const step = w / Math.max(1, points.length - 1);
  const d = points.map((p, i) => `${(i * step).toFixed(1)},${(h - ((p - min) / (max - min)) * (h - 6) - 3).toFixed(1)}`).join(" ");
  const id = "g" + Math.random().toString(36).slice(2, 8);
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${COLORS[verdict]}" stop-opacity=".35"/><stop offset="1" stop-color="${COLORS[verdict]}" stop-opacity="0"/></linearGradient></defs>
    <polygon points="0,${h} ${d} ${w},${h}" fill="url(#${id})"/>
    <polyline points="${d}" fill="none" stroke="${COLORS[verdict]}" stroke-width="2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
  </svg>`;
}

function renderKpis(k) {
  const cards = [
    { label: "Sources online", val: k.sites, sub: "Live sensor nodes", accent: "#22d3ee" },
    { label: "Avg safety index", val: k.avg_score, sub: "Network-wide, latest reading", accent: "#34d399" },
    { label: "Unsafe sources", val: k.unsafe, sub: `${k.caution} on caution`, accent: "#f43f5e" },
    { label: "Population at risk", val: k.population_at_risk.toLocaleString("en-IN"), sub: "Served by non-safe sources", accent: "#fbbf24" },
    { label: "Leak · outbreak alerts", val: `${k.leak_alerts} · ${k.outbreak_high}`, sub: "Active high-severity flags", accent: "#818cf8" },
  ];
  $("#kpis").innerHTML = cards.map((c) => `<div class="glass kpi" style="--accent:${c.accent}">
    <div class="label">${esc(c.label)}</div><div class="val">${esc(c.val)}</div><div class="sub">${esc(c.sub)}</div></div>`).join("");
}

function renderAlerts(alerts) {
  $("#alertCount").textContent = alerts.length;
  if (!alerts.length) { $("#alerts").innerHTML = `<li class="empty-state">No alerts. The network is calm.</li>`; return; }
  $("#alerts").innerHTML = alerts.map((a) => `<li class="alert ${a.severity}" data-id="${esc(a.site_id)}">
    <i class="sev"></i><div><b>${esc(a.title)}</b> <time>${esc(fmtTime(a.timestamp))}</time>
    <span>${esc(a.site_name)} — ${esc(a.message)}</span></div></li>`).join("");
  document.querySelectorAll(".alert").forEach((el) => el.addEventListener("click", () => {
    selectSite(el.dataset.id);
    $("#detail").scrollIntoView({ behavior: "smooth", block: "start" });
  }));
}

function renderFeed(feed) {
  const chip = $("#feedChip");
  if (!feed) return;
  chip.classList.toggle("ok", !!feed.ok);
  chip.classList.toggle("err", !feed.ok);
  const time = feed.updated ? new Date(feed.updated).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" }) : "";
  chip.querySelector("span").textContent = feed.ok ? `Live · Open-Meteo · ${time}` : "Live feed offline · using simulation";
  chip.title = feed.ok ? feed.source : (feed.error || "Live feed unavailable");
}
function liveLine(l) {
  if (!l) return "";
  const parts = [];
  if (l.temperature != null) parts.push(`🌡 ${Math.round(l.temperature)}°C`);
  if (l.rain_24h != null) parts.push(`🌧 ${l.rain_24h} mm / 24h`);
  if (l.river_trend === "rising") parts.push("🌊 river rising");
  return parts.length ? `<div class="live-line">${parts.join(" · ")}</div>` : "";
}

function renderSites(sites) {
  $("#siteGrid").innerHTML = sites.map((s) => {
    const e = s.eval;
    const sc = s.scenario ? `<div class="scenario-tag">⚠ ${esc(s.scenario.label)} · ${s.scenario.ticks_left} h left</div>` : "";
    return `<article class="glass site ${s.id === selectedId ? "active" : ""}" data-id="${esc(s.id)}" style="${s.scenario ? "padding-bottom:34px" : ""}">
      <div class="site-top">
        <div><h3>${esc(s.name)}</h3><div class="district">${esc(s.source)} · ${s.population.toLocaleString("en-IN")} people</div></div>
        ${ringSvg(e.score, e.verdict)}
      </div>
      <div style="margin-top:10px"><span class="verdict ${e.verdict}">${esc(e.verdict_label)}</span></div>
      <div class="site-metrics">
        <div class="mini"><span>Outbreak risk</span><b style="color:${e.outbreak >= 60 ? "#f43f5e" : e.outbreak >= 35 ? "#fbbf24" : "#e6f1ff"}">${Math.round(e.outbreak)}</b></div>
        <div class="mini"><span>Leak index</span><b style="color:${e.leak >= 50 ? "#f43f5e" : e.leak >= 25 ? "#fbbf24" : "#e6f1ff"}">${Math.round(e.leak)}</b></div>
      </div>
      ${sparkSvg(s.sparkline, e.verdict)}
      ${liveLine(s.live)}
      ${sc}
    </article>`;
  }).join("");
  document.querySelectorAll(".site").forEach((el) => el.addEventListener("click", () => selectSite(el.dataset.id)));
}

async function loadOverview() {
  const data = await api("/api/overview");
  scenarios = data.scenarios;
  lastSites = data.sites;
  renderFeed(data.feed);
  renderKpis(data.kpis);
  updateMarkers(data.sites);
  renderAlerts(data.alerts);
  renderSites(data.sites);
  if (!map._fitted) {
    map.fitBounds(data.sites.map((s) => [s.lat, s.lon]), { padding: [40, 40] });
    map._fitted = true;
  }
  if (selectedId) await selectSite(selectedId, true);
}

/* ---------- detail ---------- */
function paramTile(p) {
  const shown = p.value;
  return `<div class="param ${p.status}"><span>${esc(p.label)}</span><b>${esc(shown)}</b><small>${esc(p.unit)}</small></div>`;
}
function meter(label, value, level, color) {
  return `<div class="meter"><div class="row"><span>${esc(label)}</span><b>${Math.round(value)}/100 · ${esc(level)}</b></div>
    <div class="bar-track"><div class="bar-fill" style="width:${Math.min(100, value)}%;background:${color}"></div></div></div>`;
}
function meterColor(v) { return v >= 60 ? "#f43f5e" : v >= 35 ? "#fbbf24" : "#34d399"; }

async function selectSite(id, silent = false) {
  selectedId = id;
  document.querySelectorAll(".site").forEach((el) => el.classList.toggle("active", el.dataset.id === id));
  const data = await api(`/api/site/${encodeURIComponent(id)}`);
  const s = data.site, e = data.latest.eval;
  const scenBtns = Object.entries(scenarios).map(([key, sc]) =>
    `<button class="btn" data-scen="${esc(key)}" title="${esc(sc.desc)}">${esc(sc.label)}</button>`).join("");

  $("#detail").innerHTML = `
    <div class="d-left">
      <div class="d-head"><span class="verdict ${e.verdict}">${esc(e.verdict_label)}</span>
        <h2 style="margin-top:12px">${esc(s.name)}</h2>
        <p class="muted">${esc(s.district)} · ${esc(s.source)} · ${s.population.toLocaleString("en-IN")} people · ${esc(fmtTime(data.latest.latest_timestamp))}</p></div>
      <div class="hero">${ringSvg(e.score, e.verdict)}
        <div><div class="muted">Safety index</div><div style="font-size:26px;font-weight:800">${Math.round(e.score)}<span class="muted" style="font-size:14px">/100</span></div></div></div>
      ${meter("Outbreak risk", e.outbreak, e.outbreak_level, meterColor(e.outbreak))}
      ${meter("Leak index", e.leak, e.leak_level, meterColor(e.leak))}
      <div>
        <div class="muted" style="margin-bottom:8px">Break something on purpose (demo)</div>
        <div class="scen">${scenBtns}</div>
        <p class="scen-note" style="margin-top:8px">${s.scenario ? `Active: <b>${esc(scenarios[s.scenario]?.label || s.scenario)}</b>. Advance time to watch it unfold.` : "Inject a scenario, then advance time to see the network respond."}</p>
      </div>
    </div>
    <div class="d-right">
      <div class="chart-head"><h3>Safety trend · last 72 h</h3><span class="muted">Chlorine &amp; turbidity overlay</span></div>
      <div class="chart-wrap"><canvas id="trend"></canvas></div>
      <div><h3 style="margin-bottom:12px">Sensor parameters <span class="live-tag" style="color:#fbbf24;background:rgba(251,191,36,.1);border-color:rgba(251,191,36,.3)">SIMULATED</span></h3>
        <div class="params">${Object.values(e.params).filter((p) => p.status !== "info").map(paramTile).join("")}</div></div>
      ${data.live ? `<div><h3>Live conditions <span class="live-tag">LIVE · OPEN-METEO</span></h3><div class="live-tiles">
        ${[["Temperature", data.live.temperature, "°C"], ["Rain now", data.live.rain_now, "mm"], ["Rain 24 h", data.live.rain_24h, "mm"],
           ["River discharge", data.live.river_discharge, "m³/s"]].map(([l, v, u]) =>
          `<div class="param info"><span>${l}</span><b>${v == null ? "–" : esc(v)}</b><small>${u}${l === "River discharge" && data.live.river_trend ? " · " + esc(data.live.river_trend) : ""}</small></div>`).join("")}
        </div></div>` : ""}
      <div><h3 style="margin-bottom:12px">AI guidance</h3><div class="insights">${e.insights.map((i, n) =>
        `<div class="insight ${i.level}" style="animation-delay:${n * 0.08}s"><i></i><span>${esc(i.text)}</span></div>`).join("")}</div></div>
    </div>`;

  document.querySelectorAll("[data-scen]").forEach((b) => b.addEventListener("click", () => inject(id, b.dataset.scen)));
  drawChart(data.series);
  if (lastSites.length) updateMarkers(lastSites); // keep the selected marker highlighted
}

function drawChart(series) {
  const labels = series.map((p) => new Date(p.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" }));
  const ctx = $("#trend");
  if (chart) chart.destroy();
  const grad = ctx.getContext("2d").createLinearGradient(0, 0, 0, 260);
  grad.addColorStop(0, "rgba(34,211,238,.35)"); grad.addColorStop(1, "rgba(34,211,238,0)");
  chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Safety index", data: series.map((p) => p.score), borderColor: "#22d3ee", backgroundColor: grad, fill: true, tension: .35, pointRadius: 0, borderWidth: 2.5, yAxisID: "y" },
        { label: "Chlorine (mg/L)", data: series.map((p) => p.chlorine), borderColor: "#818cf8", borderDash: [5, 4], tension: .3, pointRadius: 0, borderWidth: 1.6, yAxisID: "y2" },
        { label: "Turbidity (NTU)", data: series.map((p) => p.turbidity), borderColor: "#fbbf24", tension: .3, pointRadius: 0, borderWidth: 1.6, yAxisID: "y2" },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 600 }, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: "#8ba3c1", boxWidth: 12, usePointStyle: true } },
        tooltip: { backgroundColor: "#0b1626", borderColor: "rgba(148,197,255,.2)", borderWidth: 1, titleColor: "#e6f1ff", bodyColor: "#cbd5e1", padding: 10 } },
      scales: {
        x: { ticks: { color: "#64748b", maxTicksLimit: 8 }, grid: { color: "rgba(148,197,255,.06)" } },
        y: { min: 0, max: 100, ticks: { color: "#64748b" }, grid: { color: "rgba(148,197,255,.06)" } },
        y2: { position: "right", ticks: { color: "#64748b" }, grid: { display: false } },
      },
    },
  });
}

/* ---------- actions ---------- */
async function inject(siteId, scenario) {
  await api("/api/inject", { method: "POST", body: JSON.stringify({ site_id: siteId, scenario }) });
  toast(`Injected: ${scenarios[scenario].label}. Advancing time…`);
  await tick();
}
async function tick() {
  const data = await api("/api/tick", { method: "POST" });
  lastSites = data.sites;
  renderKpis(data.kpis); updateMarkers(data.sites); renderAlerts(data.alerts); renderSites(data.sites);
  if (selectedId) await selectSite(selectedId, true);
}
async function reset() {
  stopLive();
  $("#liveToggle").checked = false;
  const data = await api("/api/reset", { method: "POST" });
  Object.values(markers).forEach((m) => map.removeLayer(m));
  markers = {}; lastSites = data.sites; map._fitted = false; selectedId = null; chart && chart.destroy(); chart = null;
  $("#detail").innerHTML = `<div class="empty"><div class="empty-orb"></div><h2>Select a water source</h2><p class="muted">Network reset to a clean baseline.</p></div>`;
  renderKpis(data.kpis); updateMarkers(data.sites); renderAlerts(data.alerts); renderSites(data.sites);
  map.fitBounds(data.sites.map((s) => [s.lat, s.lon]), { padding: [40, 40] }); map._fitted = true;
  toast("Network reset");
}
function startLive() { liveTimer = setInterval(() => tick().catch(console.error), 3500); }
function stopLive() { clearInterval(liveTimer); liveTimer = null; }

async function uploadFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  $("#uploadResult").innerHTML = `<p class="muted">Analysing ${esc(file.name)}…</p>`;
  try {
    const r = await api("/api/upload", { method: "POST", body: fd });
    const counts = r.analysis.reduce((a, x) => ((a[x.verdict] = (a[x.verdict] || 0) + 1), a), {});
    $("#uploadResult").innerHTML = `
      <div class="summary">
        <span class="chip">✓ ${r.accepted} accepted</span>
        ${r.rejected_count ? `<span class="chip err">✕ ${r.rejected_count} rejected</span>` : ""}
        ${Object.entries(counts).map(([k, v]) => `<span class="verdict ${k}">${v} ${k.toLowerCase()}</span>`).join("")}
      </div>
      ${r.analysis.length ? `<div style="max-height:260px;overflow:auto"><table><thead><tr><th>Source</th><th>Time</th><th>Index</th><th>Verdict</th><th>Top finding</th></tr></thead><tbody>
        ${r.analysis.map((x) => `<tr><td>${esc(x.site_name)}</td><td><small>${esc(fmtTime(x.timestamp))}</small></td><td><b>${Math.round(x.score)}</b></td>
        <td><span class="verdict ${x.verdict}">${x.verdict}</span></td><td><small>${esc(x.top_issue)}</small></td></tr>`).join("")}
      </tbody></table></div>` : ""}
      ${r.rejected.length ? `<p class="muted err" style="margin-top:8px">${r.rejected.map(esc).join("<br>")}</p>` : ""}`;
    await tick();
    toast(`Merged ${r.accepted} readings into ${r.sites_updated.length} source(s)`);
  } catch (err) {
    $("#uploadResult").innerHTML = `<p class="err">Upload failed: ${esc(err.message)}</p>`;
  }
}

/* ---------- boot ---------- */
function startClock() {
  const tickClock = () => ($("#clock").textContent = new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "Asia/Kolkata" }) + " IST");
  tickClock(); setInterval(tickClock, 1000);
}

/* ---------- AI assistant ---------- */
const chatHistory = [];
function fmtMarkdown(text) {
  const lines = esc(text).split("\n");
  let html = "", inList = false;
  for (const raw of lines) {
    const line = raw.trim();
    const bullet = /^[-*•]\s+(.*)/.exec(line);
    if (bullet) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${bold(bullet[1])}</li>`;
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      if (line) html += `<p style="margin:0 0 6px">${bold(line)}</p>`;
    }
  }
  if (inList) html += "</ul>";
  return html;
}
function bold(s) { return s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>"); }
function addMsg(role, text, extraClass = "") {
  const el = document.createElement("div");
  el.className = `msg ${role} ${extraClass}`;
  el.innerHTML = role === "bot" || role === "error" ? (text.startsWith("<") ? text : fmtMarkdown(text)) : esc(text);
  $("#chatBody").appendChild(el);
  $("#chatBody").scrollTop = $("#chatBody").scrollHeight;
  return el;
}
function openChat(open) {
  $("#chatPanel").classList.toggle("open", open);
  $("#chatPanel").setAttribute("aria-hidden", String(!open));
  if (open) setTimeout(() => $("#chatText").focus(), 250);
}
async function sendChat(text) {
  text = text.trim();
  if (!text) return;
  $("#chatSuggest").style.display = "none";
  addMsg("user", text);
  chatHistory.push({ role: "user", content: text });
  $("#chatText").value = "";
  const typing = addMsg("bot", `<span class="typing"><i></i><i></i><i></i></span>`);
  $("#chatSend").disabled = true;
  try {
    const r = await api("/api/chat", { method: "POST", body: JSON.stringify({ messages: chatHistory }) });
    typing.remove();
    addMsg("bot", r.reply);
    chatHistory.push({ role: "assistant", content: r.reply });
  } catch (err) {
    typing.remove();
    addMsg("error", err.message, "error");
    chatHistory.pop();
  } finally {
    $("#chatSend").disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  startClock();
  $("#chatFab").addEventListener("click", () => openChat(!$("#chatPanel").classList.contains("open")));
  $("#chatClose").addEventListener("click", () => openChat(false));
  $("#chatForm").addEventListener("submit", (e) => { e.preventDefault(); sendChat($("#chatText").value); });
  document.querySelectorAll("#chatSuggest button").forEach((b) => b.addEventListener("click", () => sendChat(b.textContent)));
  addMsg("bot", appConfigReadyMessage());
  await initMap();
  $("#tickBtn").addEventListener("click", () => tick().then(() => toast("Advanced 1 simulated hour")).catch((e) => toast(e.message)));
  $("#resetBtn").addEventListener("click", () => reset().catch((e) => toast(e.message)));
  $("#liveToggle").addEventListener("change", (ev) => (ev.target.checked ? startLive() : stopLive()));

  const dz = $("#dropzone"), fi = $("#fileInput");
  fi.addEventListener("change", () => fi.files[0] && uploadFile(fi.files[0]));
  ["dragenter", "dragover"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", (e) => e.dataTransfer.files[0] && uploadFile(e.dataTransfer.files[0]));

  if (!appConfig.assistant_ready) {
    const note = $("#chatBody .msg");
    if (note) note.innerHTML = fmtMarkdown("The assistant needs a **Gemini API key**. Add `GEMINI_API_KEY` to the `.env` file, then restart the server.");
  }
  try { await loadOverview(); }
  catch (e) { toast("Could not load data: " + e.message); }
});

function appConfigReadyMessage() {
  return "Hi! I'm the AquaPulse Assistant. Ask me about any source, alert or live condition. I answer from the current dashboard data.";
}
