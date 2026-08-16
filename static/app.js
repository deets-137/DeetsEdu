/* DeetsEdu map — satellite basemap, district boundaries, click/search to select. */

const SDTYPE_LABEL = {
  unified: "Unified (K–12)",
  elementary: "Elementary district",
  secondary: "Secondary district",
};

const map = new maplibregl.Map({
  container: "map",
  center: [-96.9, 38.5],
  zoom: 4,
  minZoom: 3,
  maxZoom: 17,
  style: {
    version: 8,
    sources: {
      satellite: {
        type: "raster",
        tiles: [
          "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        ],
        tileSize: 256,
        attribution:
          "Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community",
      },
    },
    layers: [
      { id: "satellite", type: "raster", source: "satellite" },
    ],
  },
});

map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

let hoveredId = null;
let selectedId = null;
let searchMarker = null;

// ------------------------------------------------------------ data loading
async function fetchTopo(url, objectName) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${url}: HTTP ${resp.status}`);
  const topo = await resp.json();
  return topojson.feature(topo, topo.objects[objectName]);
}

map.on("load", async () => {
  const [districts, states] = await Promise.all([
    fetchTopo("/api/districts.topojson", "districts"),
    fetchTopo("/api/states.topojson", "states"),
  ]);

  map.addSource("districts", {
    type: "geojson",
    data: districts,
    promoteId: "GEOID",
  });
  map.addSource("states", { type: "geojson", data: states, promoteId: "GEOID" });

  // Invisible fill = click/hover target. Secondary districts overlap
  // elementary ones, so all three types stay queryable here even though
  // only unified/elementary get outlines drawn.
  map.addLayer({
    id: "districts-fill",
    type: "fill",
    source: "districts",
    minzoom: 5,
    paint: {
      "fill-color": "#ffffff",
      "fill-opacity": [
        "case",
        ["boolean", ["feature-state", "selected"], false], 0.10,
        ["boolean", ["feature-state", "hover"], false], 0.08,
        0,
      ],
    },
  });

  map.addLayer({
    id: "districts-line",
    type: "line",
    source: "districts",
    minzoom: 5,
    filter: ["!=", ["get", "sdtype"], "secondary"],
    paint: {
      "line-color": "#ffffff",
      "line-opacity": 0.55,
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.4, 9, 1.1, 13, 2],
    },
  });

  map.addLayer({
    id: "districts-selected-line",
    type: "line",
    source: "districts",
    minzoom: 5,
    paint: {
      "line-color": "#ffd54a",
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1.5, 10, 3.5],
      "line-opacity": [
        "case", ["boolean", ["feature-state", "selected"], false], 1, 0,
      ],
    },
  });

  map.addLayer({
    id: "states-line",
    type: "line",
    source: "states",
    paint: {
      "line-color": "#ffffff",
      "line-opacity": ["interpolate", ["linear"], ["zoom"], 4, 0.9, 8, 0.4],
      "line-width": ["interpolate", ["linear"], ["zoom"], 3, 1, 8, 2],
    },
  });

  document.getElementById("loading").classList.add("hidden");

  map.on("mousemove", "districts-fill", (e) => {
    map.getCanvas().style.cursor = "pointer";
    const id = e.features[0].id;
    if (id === hoveredId) return;
    setHover(id);
  });
  map.on("mouseleave", "districts-fill", () => {
    map.getCanvas().style.cursor = "";
    setHover(null);
  });

  map.on("click", (e) => {
    const feats = map.queryRenderedFeatures(e.point, { layers: ["districts-fill"] });
    if (feats.length === 0) {
      // National zoom: click a state to dive in.
      if (map.getZoom() < 5) {
        const st = map.queryRenderedFeatures(e.point, { layers: ["states-line"] });
        // states-line only hits on the outline itself; use locate-by-fit instead
        map.easeTo({ center: e.lngLat, zoom: 6.2, duration: 900 });
      }
      return;
    }
    const districts = dedupe(feats.map(featureInfo));
    if (districts.length === 1) {
      selectDistrict(districts[0]);
    } else {
      showPickList(districts, "Districts serving this spot");
    }
  });
});

function featureInfo(f) {
  return {
    leaid: f.properties.GEOID,
    name: f.properties.NAME,
    state: f.properties.STATEFP,
    sdtype: f.properties.sdtype,
  };
}

function dedupe(list) {
  const seen = new Set();
  const order = { unified: 0, elementary: 1, secondary: 2 };
  return list
    .filter((d) => !seen.has(d.leaid) && seen.add(d.leaid))
    .sort((a, b) => (order[a.sdtype] ?? 9) - (order[b.sdtype] ?? 9));
}

function setHover(id) {
  if (hoveredId !== null) {
    map.setFeatureState({ source: "districts", id: hoveredId }, { hover: false });
  }
  hoveredId = id;
  if (id !== null) {
    map.setFeatureState({ source: "districts", id }, { hover: true });
  }
}

function setSelected(id) {
  if (selectedId !== null) {
    map.setFeatureState({ source: "districts", id: selectedId }, { selected: false });
  }
  selectedId = id;
  if (id !== null) {
    map.setFeatureState({ source: "districts", id }, { selected: true });
  }
}

// ------------------------------------------------------------ selection UI
const panel = document.getElementById("panel");
const panelBody = document.getElementById("panel-body");
document.getElementById("panel-close").addEventListener("click", () => {
  panel.classList.add("hidden");
  setSelected(null);
});

function selectDistrict(d, { zoomTo = false } = {}) {
  setSelected(d.leaid);
  panelBody.innerHTML = `
    <h2>${escapeHtml(d.name)}</h2>
    <div class="subtitle">${SDTYPE_LABEL[d.sdtype] ?? d.sdtype}</div>
    <div class="placeholder">
      Achievement history for this district is being processed and will
      appear here — level vs. the national average, the 2009&ndash;2019 trend,
      pandemic loss &amp; recovery, and equity gaps.
    </div>
    <div class="meta">NCES district ID (leaid): ${escapeHtml(d.leaid)}</div>
  `;
  panel.classList.remove("hidden");
  if (zoomTo) zoomToDistrict(d.leaid);
}

function zoomToDistrict(leaid) {
  const src = map.getSource("districts");
  if (!src || !src._data) return;
  const feat = src._data.features.find((f) => f.properties.GEOID === leaid);
  if (!feat) return;
  map.fitBounds(geomBounds(feat.geometry), { padding: 60, maxZoom: 11, duration: 900 });
}

function geomBounds(geom) {
  let minX = 180, minY = 90, maxX = -180, maxY = -90;
  const scan = (coords) => {
    if (typeof coords[0] === "number") {
      minX = Math.min(minX, coords[0]);
      maxX = Math.max(maxX, coords[0]);
      minY = Math.min(minY, coords[1]);
      maxY = Math.max(maxY, coords[1]);
    } else coords.forEach(scan);
  };
  scan(geom.coordinates);
  return [[minX, minY], [maxX, maxY]];
}

function showPickList(districts, title) {
  panelBody.innerHTML = `
    <p class="picklist-title">${escapeHtml(title)}:</p>
    <ul class="picklist">
      ${districts
        .map(
          (d, i) => `
        <li data-i="${i}">
          ${escapeHtml(d.name)}
          <span class="tag">${SDTYPE_LABEL[d.sdtype] ?? d.sdtype}</span>
        </li>`
        )
        .join("")}
    </ul>
  `;
  panel.classList.remove("hidden");
  panelBody.querySelectorAll("li").forEach((li) => {
    li.addEventListener("click", () =>
      selectDistrict(districts[Number(li.dataset.i)], { zoomTo: true })
    );
  });
}

// ------------------------------------------------------------ search
const searchForm = document.getElementById("search-form");
const searchInput = document.getElementById("search-input");
const searchResults = document.getElementById("search-results");

searchForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = searchInput.value.trim();
  if (!q) return;
  showSearchMessage("Searching…");
  try {
    const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    if (!resp.ok) throw new Error((await resp.json()).detail ?? resp.status);
    const { results } = await resp.json();
    if (results.length === 0) {
      showSearchMessage("No match found. Try a 5-digit zip or a fuller address.");
    } else if (results.length === 1) {
      searchResults.classList.add("hidden");
      goToLocation(results[0]);
    } else {
      showSearchChoices(results);
    }
  } catch (err) {
    showSearchMessage(`Search failed: ${err.message}`);
  }
});

function showSearchMessage(text) {
  searchResults.innerHTML = `<li class="muted">${escapeHtml(text)}</li>`;
  searchResults.classList.remove("hidden");
}

function showSearchChoices(results) {
  searchResults.innerHTML = results
    .map((r, i) => `<li data-i="${i}">${escapeHtml(r.label)}</li>`)
    .join("");
  searchResults.classList.remove("hidden");
  searchResults.querySelectorAll("li").forEach((li) => {
    li.addEventListener("click", () => {
      searchResults.classList.add("hidden");
      goToLocation(results[Number(li.dataset.i)]);
    });
  });
}

async function goToLocation(loc) {
  if (searchMarker) searchMarker.remove();
  searchMarker = new maplibregl.Marker({ color: "#ffd54a" })
    .setLngLat([loc.lon, loc.lat])
    .addTo(map);
  map.flyTo({ center: [loc.lon, loc.lat], zoom: 9.5, duration: 1400 });

  try {
    const resp = await fetch(`/api/locate?lon=${loc.lon}&lat=${loc.lat}`);
    const { districts } = await resp.json();
    if (districts.length === 0) {
      showPickList([], "");
      panelBody.innerHTML = `<p class="picklist-title">No school district found
        at ${escapeHtml(loc.label)} — zips are matched by their center point,
        so try clicking your neighborhood on the map.</p>`;
    } else if (districts.length === 1) {
      selectDistrict(districts[0]);
    } else {
      showPickList(districts, `Districts serving ${loc.label}`);
    }
  } catch {
    /* locate is best-effort; the user can still click the map */
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
