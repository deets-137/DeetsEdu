"""
app.py — DeetsEdu map server.

Serves the MapLibre frontend plus a small API over the boundary artifacts
produced by build_boundaries.py:

    /api/districts.topojson   district boundaries (browser payload)
    /api/states.topojson      state outlines
    /api/locate?lon=&lat=     districts containing a point (pick-list source)
    /api/search?q=            zip (local gazetteer) or address (Census
                              geocoder proxy) -> candidate locations

Run:
    pip install fastapi uvicorn geopandas
    uvicorn app:app --reload
"""

import json
import re
from pathlib import Path

import geopandas as gpd
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from shapely.geometry import Point
from shapely.strtree import STRtree

PROCESSED = Path(__file__).parent / "data" / "processed"
STATIC = Path(__file__).parent / "static"

CENSUS_GEOCODER = ("https://geocoding.geo.census.gov/geocoder/locations/"
                   "onelineaddress")

FIPS_STATE = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
    "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
    "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY",
}

# Pick-list ordering: the district most people mean first.
SDTYPE_ORDER = {"unified": 0, "elementary": 1, "secondary": 2}

app = FastAPI(title="DeetsEdu map")

# Loaded once at startup; ~13k polygons is fine to keep in memory.
_districts: gpd.GeoDataFrame | None = None
_tree: STRtree | None = None
_zip_centroids: dict[str, list[float]] = {}


@app.on_event("startup")
def load_indexes() -> None:
    global _districts, _tree, _zip_centroids
    pq = PROCESSED / "districts.parquet"
    if pq.exists():
        _districts = gpd.read_parquet(pq)
        _tree = STRtree(_districts.geometry.values)
    zips = PROCESSED / "zip_centroids.json"
    if zips.exists():
        _zip_centroids = json.loads(zips.read_text(encoding="utf-8"))


def _district_info(row) -> dict:
    return {
        "leaid": row.GEOID,
        "name": row.NAME,
        "state": FIPS_STATE.get(row.STATEFP, row.STATEFP),
        "sdtype": row.sdtype,
    }


@app.get("/api/districts.topojson")
def districts_topojson() -> FileResponse:
    path = PROCESSED / "districts.topojson"
    if not path.exists():
        raise HTTPException(503, "Boundaries not built — run build_boundaries.py")
    return FileResponse(path, media_type="application/json")


@app.get("/api/states.topojson")
def states_topojson() -> FileResponse:
    path = PROCESSED / "states.topojson"
    if not path.exists():
        raise HTTPException(503, "Boundaries not built — run build_boundaries.py")
    return FileResponse(path, media_type="application/json")


@app.get("/api/locate")
def locate(lon: float, lat: float) -> dict:
    """All districts whose polygon contains the point."""
    if _tree is None:
        raise HTTPException(503, "Boundaries not built — run build_boundaries.py")
    hits = _tree.query(Point(lon, lat), predicate="intersects")
    rows = [_districts.iloc[i] for i in hits]
    rows.sort(key=lambda r: SDTYPE_ORDER.get(r.sdtype, 9))
    return {"districts": [_district_info(r) for r in rows]}


@app.get("/api/search")
def search(q: str) -> dict:
    """Zip code -> local gazetteer centroid; anything else -> Census geocoder."""
    q = q.strip()
    if not q:
        return {"results": []}

    if re.fullmatch(r"\d{5}(-\d{4})?", q):
        zip5 = q[:5]
        loc = _zip_centroids.get(zip5)
        if not loc:
            return {"results": []}
        return {"results": [
            {"label": f"ZIP {zip5}", "lon": loc[0], "lat": loc[1], "kind": "zip"}
        ]}

    try:
        resp = requests.get(
            CENSUS_GEOCODER,
            params={"address": q, "benchmark": "Public_AR_Current",
                    "format": "json"},
            timeout=15,
        )
        resp.raise_for_status()
        matches = resp.json().get("result", {}).get("addressMatches", [])
    except requests.RequestException as e:
        raise HTTPException(502, f"Census geocoder unavailable: {e}")

    return {"results": [
        {
            "label": m["matchedAddress"],
            "lon": m["coordinates"]["x"],
            "lat": m["coordinates"]["y"],
            "kind": "address",
        }
        for m in matches[:5]
    ]}


# Mounted last so /api/* wins; html=True serves index.html at /.
app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
