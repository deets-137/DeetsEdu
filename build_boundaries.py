"""
build_boundaries.py — Turn raw TIGER shapefiles into map-ready artifacts.

Reads the per-state school-district cartographic boundary zips pulled by
`pull_data.py tiger` and writes to ./data/processed/:

    districts.topojson   all school districts, one topology, quantized +
                         topology-preserving simplification (browser payload)
    states.topojson      state outlines for the zoomed-out view
    districts.parquet    same districts with full-precision geometry
                         (GeoParquet) — the server's point-in-polygon index
    zip_centroids.json   ZCTA -> [lon, lat] from the Census gazetteer,
                         for zip-code search (gazetteer zip is downloaded
                         to data/raw/gazetteer/ on first run)

Geometry only — no achievement stats; those come from a separate build.

Usage:
    pip install geopandas topojson pyogrio
    python build_boundaries.py
"""

import io
import json
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

from pull_data import DATA, TIGER_SD_TYPES, download

RAW = DATA / "tiger"
OUT = Path(__file__).parent / "data" / "processed"

# Continental-US app: skip territories (AS/GU/MP/PR/VI).
TERRITORY_FIPS = {"60", "66", "69", "72", "78"}

SDTYPE_LABEL = {"unsd": "unified", "elsd": "elementary", "scsd": "secondary"}

# Census gazetteer: ZCTA (zip-code area) centroids, 2019 to match boundaries.
GAZ_URL = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
           "2019_Gazetteer/2019_Gaz_zcta_national.zip")

# TopoJSON tuning: quantization grid + simplification tolerance in degrees
# (~0.0005 deg = ~50 m — below what 1:500k generalization resolves, so this
# mostly strips redundant vertices rather than visibly changing shapes).
QUANTIZE = 1e6
SIMPLIFY_DEG = 0.0005


def load_districts() -> gpd.GeoDataFrame:
    frames = []
    for sdtype, fips_list in TIGER_SD_TYPES.items():
        for fips in fips_list:
            if fips in TERRITORY_FIPS:
                continue
            path = RAW / f"cb_2019_{fips}_{sdtype}_500k.zip"
            g = gpd.read_file(path)[["GEOID", "NAME", "STATEFP", "geometry"]]
            g["sdtype"] = SDTYPE_LABEL[sdtype]
            frames.append(g)
    gdf = pd.concat(frames, ignore_index=True)
    # Placeholder polygons for areas no district claims — not clickable things.
    gdf = gdf[gdf["NAME"] != "School District Not Defined"].reset_index(drop=True)
    return gdf.to_crs(epsg=4326)


def write_topojson(gdf: gpd.GeoDataFrame, name: str, dest: Path) -> None:
    import topojson

    topo = topojson.Topology(
        gdf, object_name=name, prequantize=QUANTIZE, toposimplify=SIMPLIFY_DEG
    )
    dest.write_text(topo.to_json(), encoding="utf-8")
    print(f"  {dest.name}: {dest.stat().st_size / 1e6:.1f} MB, {len(gdf):,} features")


def build_zip_centroids() -> None:
    gaz_dir = DATA / "gazetteer"
    gaz_dir.mkdir(parents=True, exist_ok=True)
    gaz_zip = gaz_dir / "2019_Gaz_zcta_national.zip"
    download(GAZ_URL, gaz_zip)
    with zipfile.ZipFile(gaz_zip) as z:
        raw = z.read(z.namelist()[0])
    df = pd.read_csv(io.BytesIO(raw), sep="\t", dtype={"GEOID": str})
    df.columns = [c.strip() for c in df.columns]  # gazetteer pads last header
    centroids = {
        row.GEOID: [round(row.INTPTLONG, 5), round(row.INTPTLAT, 5)]
        for row in df.itertuples()
    }
    dest = OUT / "zip_centroids.json"
    dest.write_text(json.dumps(centroids), encoding="utf-8")
    print(f"  {dest.name}: {dest.stat().st_size / 1e6:.1f} MB, "
          f"{len(centroids):,} zips")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    print("districts: reading state shapefiles...")
    districts = load_districts()
    by_type = districts["sdtype"].value_counts().to_dict()
    print(f"  {len(districts):,} districts loaded {by_type}")

    print("districts: writing GeoParquet (server point-in-polygon index)...")
    districts.to_parquet(OUT / "districts.parquet", index=False)
    print(f"  districts.parquet: "
          f"{(OUT / 'districts.parquet').stat().st_size / 1e6:.1f} MB")

    print("districts: building TopoJSON (takes a few minutes)...")
    write_topojson(districts, "districts", OUT / "districts.topojson")

    print("states: building TopoJSON...")
    states = gpd.read_file(RAW / "cb_2019_us_state_5m.zip")
    states = states[~states["STATEFP"].isin(TERRITORY_FIPS)]
    states = states[["GEOID", "STUSPS", "NAME", "geometry"]].to_crs(epsg=4326)
    write_topojson(states, "states", OUT / "states.topojson")

    print("zip centroids: from Census gazetteer...")
    build_zip_centroids()

    print("\nDone. Artifacts in", OUT)


if __name__ == "__main__":
    main()
