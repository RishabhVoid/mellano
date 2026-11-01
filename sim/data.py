"""Download public source data and build the aligned 10 m simulation grid."""
import hashlib
import json
import math
import re
from datetime import datetime, timezone

import numpy as np
import requests
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform_bounds
from shapely.geometry import LineString, Polygon, box, mapping, shape
from shapely.ops import polygonize, transform, unary_union
from shapely import make_valid
from pyproj import Transformer

from sim.config import AOI, CELL_SIZE, CRS, DEM_TILE, DEM_URL, DERIVED, FABDEM_TILE, OVERPASS_URLS, RAW


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    temp.replace(path)


def download():
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"{DEM_TILE}.tif"
    if not path.exists():
        print("Downloading public Copernicus GLO-30 tile…", flush=True)
        with requests.get(DEM_URL, stream=True, timeout=(20, 180)) as response:
            response.raise_for_status()
            temporary = path.with_suffix(".part")
            with temporary.open("wb") as stream:
                for chunk in response.iter_content(1024 * 1024):
                    stream.write(chunk)
            temporary.replace(path)
    osm_path = RAW / "osm_aoi.json"
    if not osm_path.exists():
        west, south, east, north = AOI
        bbox = f"{south},{west},{north},{east}"
        query = f'[out:json][timeout:180];(way["building"]({bbox});relation["building"]({bbox});way["man_made"="dyke"]({bbox});way["embankment"="yes"]({bbox});way["highway"]({bbox});way["waterway"="river"]({bbox});way["water"="river"]({bbox});relation["water"="river"]({bbox});way["waterway"="riverbank"]({bbox});relation["waterway"="riverbank"]({bbox}););out geom;'
        failures = []
        for endpoint in OVERPASS_URLS:
            try:
                print(f"Fetching OSM footprints, embankments, roads and river from {endpoint}…", flush=True)
                response = requests.get(endpoint, params={"data": query}, headers={"User-Agent": "Otec local terrain prototype", "Accept": "application/json"}, timeout=(20, 240))
                response.raise_for_status()
                data = response.json()
                if data.get("remark") or "elements" not in data:
                    raise ValueError(data.get("remark", "No OSM elements returned"))
                data["otec_source"] = endpoint
                write_json(osm_path, data)
                break
            except (requests.RequestException, ValueError) as exc:
                failures.append(f"{endpoint}: {exc}")
        else:
            raise RuntimeError("All Overpass endpoints failed: " + "; ".join(failures))
    from sim.fabdem import download as download_fabdem
    download_fabdem()
    print("Public input files ready.", flush=True)


def osm_geometry(element, polygon=True):
    if element["type"] == "way":
        coords = [(p["lon"], p["lat"]) for p in element.get("geometry", [])]
        if len(coords) < (4 if polygon else 2):
            return None
        if polygon and coords[0] != coords[-1]:
            return None
        return make_valid(Polygon(coords)) if polygon else LineString(coords)
    outer, inner = [], []
    for member in element.get("members", []):
        coords = [(p["lon"], p["lat"]) for p in member.get("geometry", [])]
        if len(coords) > 1:
            (inner if member.get("role") == "inner" else outer).append(LineString(coords))
    if not outer:
        return None
    result = unary_union(list(polygonize(unary_union(outer))))
    if inner:
        result = result.difference(unary_union(list(polygonize(unary_union(inner)))))
    return make_valid(result)


def height_from_tags(tags):
    value = tags.get("height", "").strip().lower()
    if value:
        match = re.fullmatch(r"([0-9]+(?:[.,][0-9]+)?)\s*(m|metres|meters|ft|feet|')?", value)
        if match:
            metres = float(match[1].replace(",", ".")) * (0.3048 if match[2] in ("ft", "feet", "'") else 1)
            if 0 < metres <= 500:
                return metres, "OSM height"
    try:
        levels = float(tags.get("building:levels", ""))
        if 0 < levels <= 150:
            return levels * 3.0, "Estimated: OSM building:levels × 3 m"
    except ValueError:
        pass
    return None, "Height not mapped"


def build_grid():
    DERIVED.mkdir(parents=True, exist_ok=True)
    source = RAW / FABDEM_TILE
    comparison_source = RAW / f"{DEM_TILE}.tif"
    if not source.exists() or not (RAW / "osm_aoi.json").exists():
        raise RuntimeError("Run make data first. FABDEM is required for ground; Copernicus is comparison only.")
    west, south, east, north = transform_bounds("EPSG:4326", CRS, *AOI, densify_pts=21)
    west, south = math.floor(west / CELL_SIZE) * CELL_SIZE, math.floor(south / CELL_SIZE) * CELL_SIZE
    east, north = math.ceil(east / CELL_SIZE) * CELL_SIZE, math.ceil(north / CELL_SIZE) * CELL_SIZE
    width, height = int(round((east-west)/CELL_SIZE)), int(round((north-south)/CELL_SIZE))
    affine = from_origin(west, north, CELL_SIZE, CELL_SIZE)
    ground = np.full((height, width), -9999, dtype=np.float32)
    with rasterio.open(source) as src:
        reproject(rasterio.band(src, 1), ground, src_transform=src.transform, src_crs=src.crs, src_nodata=src.nodata, dst_transform=affine, dst_crs=CRS, dst_nodata=-9999, resampling=Resampling.bilinear, num_threads=2)
    if not np.isfinite(ground).all() or np.any(ground == -9999):
        raise ValueError("DEM does not fully cover the target grid")
    comparison = np.full((height, width), -9999, dtype=np.float32)
    if not comparison_source.is_file():
        raise RuntimeError("Copernicus comparison source is missing; run make data.")
    with rasterio.open(comparison_source) as src:
        reproject(rasterio.band(src, 1), comparison, src_transform=src.transform, src_crs=src.crs, src_nodata=src.nodata, dst_transform=affine, dst_crs=CRS, dst_nodata=-9999, resampling=Resampling.bilinear, num_threads=2)
    if not np.isfinite(comparison).all() or np.any(comparison == -9999):
        raise ValueError("Copernicus comparison does not fully cover the target grid")
    profile = dict(driver="GTiff", width=width, height=height, count=1, crs=CRS, transform=affine, compress="deflate", tiled=True)
    def save(name, values):
        with rasterio.open(DERIVED / name, "w", **profile, dtype=values.dtype, nodata=None) as dst:
            dst.write(values, 1)
    project = Transformer.from_crs(4326, CRS, always_xy=True).transform
    clip = box(*AOI)
    aoi_projected = transform(project, clip)
    aoi_mask = rasterize([(aoi_projected, 1)], out_shape=ground.shape, transform=affine, dtype="uint8")
    data = json.loads((RAW / "osm_aoi.json").read_text())
    buildings, embankments, roads, river = [], [], [], []
    # Relation members are skipped as standalone buildings to avoid duplicate footprints.
    building_members = {m["ref"] for e in data["elements"] if e["type"] == "relation" and e.get("tags", {}).get("building") for m in e.get("members", []) if m["type"] == "way"}
    for element in data["elements"]:
        tags = element.get("tags", {})
        osm_id = f'{element["type"]}/{element["id"]}'
        props = {"osm_id": osm_id, "source_url": f"https://www.openstreetmap.org/{osm_id}"}
        groups = []
        if tags.get("building") and tags["building"] != "no" and not (element["type"] == "way" and element["id"] in building_members):
            building_height, height_source = height_from_tags(tags)
            groups.append((buildings, True, {**props, "height": building_height, "height_source": height_source, "name": tags.get("name")}))
        if tags.get("man_made") == "dyke" or tags.get("embankment") == "yes":
            groups.append((embankments, False, props))
        if tags.get("highway"):
            groups.append((roads, False, {**props, "highway": tags["highway"]}))
        if tags.get("water") == "river" or tags.get("waterway") == "riverbank":
            groups.append((river, True, {**props, "name": tags.get("name")}))
        elif tags.get("waterway") == "river":
            groups.append((river, False, {**props, "name": tags.get("name"), "geometry_role": "centerline"}))
        for collection, polygon, properties in groups:
            geom = osm_geometry(element, polygon)
            if geom is None or geom.is_empty:
                continue
            geom = geom.intersection(clip)
            if geom.is_empty:
                continue
            if polygon and geom.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            collection.append({"type": "Feature", "properties": properties, "geometry": mapping(geom)})
    for name, features in (("osm_buildings", buildings), ("osm_embankments", embankments), ("osm_roads", roads), ("osm_river", river)):
        write_json(DERIVED / f"{name}.geojson", {"type": "FeatureCollection", "features": features})
    def burn(features, buffer=0, value=lambda f: 1, dtype="uint8"):
        shapes = [(transform(project, shape(f["geometry"])).buffer(buffer) if buffer else transform(project, shape(f["geometry"])), value(f)) for f in features]
        return rasterize(shapes, out_shape=ground.shape, transform=affine, all_touched=True, dtype=dtype) if shapes else np.zeros(ground.shape, dtype=dtype)
    known = sorted((b for b in buildings if b["properties"]["height"] is not None), key=lambda b: b["properties"]["height"])
    surface = ground + burn(known, value=lambda f: f["properties"]["height"], dtype="float32")
    for name, array in (("dem_10m.tif", ground), ("ground_10m.tif", ground), ("copernicus_comparison_10m.tif", comparison), ("surface_10m.tif", surface), ("aoi_mask.tif", aoi_mask), ("building_mask.tif", burn(buildings)), ("embankment_mask.tif", burn(embankments, CELL_SIZE / 2)), ("road_mask.tif", burn(roads, CELL_SIZE / 2))):
        save(name, array)
    from sim.fabdem import source_metadata
    write_json(DERIVED / "provenance.json", {
        "aoi": AOI, "crs": CRS, "cell_size_m": CELL_SIZE, "created_at": datetime.now(timezone.utc).isoformat(),
        "terrain": source_metadata(source),
        "terrain_comparison": {"source": "Copernicus DEM GLO-30", "source_url": DEM_URL, "file": "copernicus_comparison_10m.tif", "use": "Comparison only; never used as simulation ground"},
        "surface_definition": "FABDEM ground + building height raster, added exactly once",
        "osm": {"source": "OpenStreetMap contributors", "source_url": data.get("otec_source"), "license": "ODbL", "timestamp": data.get("osm3s", {}).get("timestamp_osm_base"), "buildings": len(buildings), "height_known": len(known), "height_missing": len(buildings)-len(known), "embankment_ways": len(embankments), "road_ways": len(roads)},
        "grid": {"width": width, "height": height, "origin": [west, north], "min_height_m": float(ground.min()), "max_height_m": float(ground.max()), "aoi_area_km2": float(aoi_mask.sum() * CELL_SIZE**2 / 1e6)},
        "unknown_buildings": "Keep height null; footprint only in viewer, footprint blocks simulation. No synthetic heights.",
        "embankments": "Only mapped OSM ways. Crest height is the surface raster; no invented crest uplift. Missing mapping is not evidence of absence.",
    })
    print(f"Built {width} × {height} grid; {len(buildings)} buildings, {len(known)} with height; {len(embankments)} embankment ways.", flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=("download", "grid"))
    args = parser.parse_args()
    download() if args.step == "download" else build_grid()
