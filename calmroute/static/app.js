/* Calm Route frontend — vanilla JS + Leaflet. */

const RANK_COLORS = ["#2e7d52", "#3a7ab8", "#b58a2e", "#8a5fb0", "#b0483a", "#5a6b75"];

const form = document.getElementById("route-form");
const statusBox = document.getElementById("status");
const warningsBox = document.getElementById("warnings");
const results = document.getElementById("results");
const cardsBox = document.getElementById("cards");
const bufferInput = document.getElementById("buffer");
const bufferOut = document.getElementById("buffer-out");

bufferInput.addEventListener("input", () => { bufferOut.textContent = bufferInput.value; });

let map = null;
let routeLayers = {};

/* Decode an encoded polyline (precision 5) to [lat, lng] pairs. */
function decodePolyline(str) {
  let index = 0, lat = 0, lng = 0;
  const coords = [];
  while (index < str.length) {
    for (const which of [0, 1]) {
      let shift = 0, result = 0, byte;
      do {
        byte = str.charCodeAt(index++) - 63;
        result |= (byte & 0x1f) << shift;
        shift += 5;
      } while (byte >= 0x20);
      const delta = (result & 1) ? ~(result >> 1) : (result >> 1);
      if (which === 0) lat += delta; else lng += delta;
    }
    coords.push([lat / 1e5, lng / 1e5]);
  }
  return coords;
}

function fmtDuration(s) {
  const m = Math.round(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}

function fmtMiles(m) {
  return (m / 1609.34).toFixed(1) + " mi";
}

function showStatus(text, isError = false) {
  statusBox.hidden = !text;
  statusBox.textContent = text || "";
  statusBox.classList.toggle("error", isError);
}

function highlight(routeId) {
  for (const [id, layer] of Object.entries(routeLayers)) {
    const active = id === routeId;
    layer.setStyle({ weight: active ? 7 : 4, opacity: active ? 1.0 : 0.55 });
    if (active) layer.bringToFront();
  }
  document.querySelectorAll(".card").forEach((c) => {
    c.classList.toggle("active", c.dataset.routeId === routeId);
  });
}

function barColor(v) {
  return v > 66 ? "#b0483a" : v > 33 ? "#b58a2e" : "#2e7d52";
}

function renderCards(routes, winterActive) {
  cardsBox.innerHTML = "";
  routes.forEach((r, i) => {
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.routeId = r.id;

    const delta = r.delta_vs_fastest_s > 0 ? ` · +${Math.round(r.delta_vs_fastest_s / 60)} min vs fastest` : "";
    const badges =
      (r.is_fastest ? '<span class="badge">Fastest</span>' : "") +
      (r.closed ? '<span class="badge closed">Road closed</span>' : "");

    const layerRows = [
      ["Crashes", r.subscores.crash],
      ...(winterActive ? [["Ice", r.subscores.ice]] : []),
      ["Construction", r.subscores.construction],
    ]
      .map(
        ([label, v]) => `
        <div class="bar-row">
          <span>${label}</span>
          <div class="bar"><span style="width:${v}%;background:${barColor(v)}"></span></div>
          <span>${Math.round(v)}</span>
        </div>`
      )
      .join("");

    card.innerHTML = `
      <div class="head">
        <span class="name" style="color:${RANK_COLORS[i % RANK_COLORS.length]}">&#9632; ${r.label}${badges}</span>
        <span class="score" title="Safety score (higher = calmer)">${r.safety_score}</span>
      </div>
      <div class="meta">${fmtDuration(r.duration_s)} · ${fmtMiles(r.distance_m)}${delta}</div>
      <div class="bars">${layerRows}</div>
      <ul class="why">${r.explanations.map((e) => `<li>${e}</li>`).join("")}</ul>
    `;
    card.addEventListener("click", () => highlight(r.id));
    cardsBox.appendChild(card);
  });
}

function renderMap(routes) {
  if (!map) {
    map = L.map("map");
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
  }
  Object.values(routeLayers).forEach((l) => map.removeLayer(l));
  routeLayers = {};

  const group = [];
  routes.forEach((r, i) => {
    const line = L.polyline(decodePolyline(r.geometry), {
      color: RANK_COLORS[i % RANK_COLORS.length],
      weight: 4,
      opacity: 0.55,
      dashArray: r.is_fastest ? "8 6" : null,
    }).addTo(map);
    line.on("click", () => highlight(r.id));
    routeLayers[r.id] = line;
    group.push(line);
  });
  map.fitBounds(L.featureGroup(group).getBounds().pad(0.15));
}

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const go = document.getElementById("go");
  go.disabled = true;
  warningsBox.hidden = true;
  showStatus("Geocoding, routing, and scoring… this takes a few seconds.");

  try {
    const resp = await fetch("/api/routes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        origin: document.getElementById("origin").value,
        destination: document.getElementById("destination").value,
        buffer_minutes: parseInt(bufferInput.value, 10),
      }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showStatus(data.error || `Request failed (${resp.status})`, true);
      return;
    }
    showStatus("");
    if (data.warnings && data.warnings.length) {
      warningsBox.hidden = false;
      warningsBox.textContent = data.warnings.join(" ");
    }
    results.hidden = false;
    renderMap(data.routes);
    renderCards(data.routes, data.conditions.winter_active);
    map.invalidateSize();
    renderMap(data.routes);          // re-fit after the grid becomes visible
    if (data.routes.length) highlight(data.routes[0].id);
  } catch (err) {
    showStatus(`Something went wrong: ${err.message}`, true);
  } finally {
    go.disabled = false;
  }
});
