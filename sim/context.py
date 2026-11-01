"""Real OSM surface context and Google footprint/height enrichment."""
import csv
import json
import math
from functools import lru_cache
from datetime import datetime, timezone

import numpy as np
import rasterio
import requests
from pyproj import Transformer
from rasterio.features import geometry_mask, rasterize
from rasterio.windows import from_bounds, transform as window_transform
from shapely import from_wkt, make_valid, STRtree
from shapely.geometry import box, mapping, shape

from sim.config import AOI, CRS, DERIVED, OVERPASS_URLS, RAW, WEB_DATA
from sim.data import osm_geometry, write_json
from sim.geometry import transform_xy as transform

ROAD_CLASSES = {"motorway": 20, "trunk": 18, "primary": 15, "secondary": 12, "tertiary": 9, "unclassified": 6, "residential": 5}


def download_context():
    path = RAW / "osm_context.json"
    if path.is_file():
        return
    west, south, east, north = AOI
    bbox = f"{south},{west},{north},{east}"
    selectors = ['["natural"="water"]', '["waterway"="riverbank"]', '["water"="river"]', '["landuse"="forest"]', '["landuse"="grass"]', '["leisure"="park"]', '["natural"="wood"]']
    query = '[out:json][timeout:180];(' + ''.join(f'{kind}{selector}({bbox});' for selector in selectors for kind in ('way', 'relation')) + ');out geom;'
    failures = []
    for endpoint in OVERPASS_URLS:
        try:
            print(f"Fetching real OSM water, roads and green areas from {endpoint}", flush=True)
            response = requests.get(endpoint, params={"data": query}, headers={"User-Agent": "Otec local terrain prototype", "Accept": "application/json"}, timeout=(20, 240))
            response.raise_for_status()
            payload = response.json()
            if payload.get("remark") or "elements" not in payload:
                raise ValueError(payload.get("remark", "No elements"))
            payload["otec_source"] = endpoint
            write_json(path, payload)
            return
        except (requests.RequestException, ValueError) as exc:
            failures.append(str(exc))
    raise RuntimeError("OSM context download failed: " + "; ".join(failures))


def prepare_context():
    download_context()
    data = json.loads((RAW / "osm_context.json").read_text())
    roads_raw = json.loads((RAW / "osm_aoi.json").read_text())
    data["elements"] += [e for e in roads_raw["elements"] if e.get("tags", {}).get("highway")]
    groups = {name: [] for name in ("water", "green", "roads")}
    clip = box(*AOI)
    for element in data["elements"]:
        if element["type"] == "node":
            continue
        tags = element.get("tags", {})
        water = tags.get("natural") == "water" or tags.get("waterway") == "riverbank" or tags.get("water") == "river"
        green = tags.get("landuse") in ("forest", "grass") or tags.get("leisure") == "park" or tags.get("natural") == "wood"
        road_class = tags.get("highway", "").removesuffix("_link")
        bridge = tags.get("bridge", "no") not in ("no", "false", "0")
        rail = tags.get("railway") in ("rail", "subway", "light_rail") and bridge
        road = road_class in ROAD_CLASSES or rail
        for key, include in (("water", water), ("green", green), ("roads", road)):
            if not include:
                continue
            geom = osm_geometry(element, polygon=key != "roads")
            if geom is None or geom.is_empty:
                continue
            geom = geom.intersection(clip)
            if geom.is_empty or (key != "roads" and geom.geom_type not in ("Polygon", "MultiPolygon")):
                continue
            identifier = f'{element["type"]}/{element["id"]}'
            properties = {"osm_id": identifier, "source_url": f"https://www.openstreetmap.org/{identifier}", "name": tags.get("name"), "highway": tags.get("highway"), "bridge": bridge, "railway": tags.get("railway"), "tunnel": tags.get("tunnel", "no") not in ("no", "false", "0"), "width_m": ROAD_CLASSES.get(road_class, 7)}
            groups[key].append({"type": "Feature", "properties": properties, "geometry": mapping(geom)})
    for key, features in groups.items():
        write_json(DERIVED / f"osm_{key}.geojson", {"type": "FeatureCollection", "features": features})
    write_json(DERIVED / "context_provenance.json", {"source": "OpenStreetMap contributors", "license": "ODbL", "source_url": data.get("otec_source"), "timestamp": data.get("osm3s", {}).get("timestamp_osm_base"), "counts": {k: len(v) for k, v in groups.items()}, "bridge_ways": sum(f["properties"]["bridge"] for f in groups["roads"]), "road_widths": "Cartographic widths by OSM road class, not surveyed carriageway widths. Bridges retain mapped geometry; no invented piers or clearance."})
    print({k: len(v) for k, v in groups.items()}, flush=True)


@lru_cache(maxsize=4)
def height_projector(crs):
    return Transformer.from_crs(4326, crs, always_xy=True).transform


def median_height(dataset, geometry):
    """Median of valid 2023 height pixel centres inside the actual footprint."""
    projected = transform(height_projector(dataset.crs.to_string()), geometry)
    window = from_bounds(*projected.bounds, transform=dataset.transform)
    col, row = math.floor(window.col_off), math.floor(window.row_off)
    right, bottom = math.ceil(window.col_off + window.width), math.ceil(window.row_off + window.height)
    if right <= 0 or bottom <= 0 or col >= dataset.width or row >= dataset.height:
        return None
    col, row, right, bottom = max(0, col), max(0, row), min(dataset.width, right), min(dataset.height, bottom)
    window = rasterio.windows.Window(col, row, right-col, bottom-row)
    values = dataset.read(1, window=window, masked=True)
    inside = geometry_mask([mapping(projected)], out_shape=values.shape, transform=window_transform(window, dataset.transform), invert=True)
    valid = inside & ~np.ma.getmaskarray(values) & np.isfinite(values.data) & (values.data > 0) & (values.data <= 100)
    return round(float(np.median(values.data[valid])), 3) if valid.any() else None


def enrich_buildings():
    source = json.loads((DERIVED / "osm_buildings.geojson").read_text())
    features = source["features"]
    project = Transformer.from_crs(4326, CRS, always_xy=True).transform
    osm_geometries = [transform(project, shape(f["geometry"])) for f in features]
    tree = STRtree(osm_geometries)
    for feature in features:
        feature["properties"]["footprint_source"] = "OSM"
    candidates = RAW / "open_buildings_v3_aoi.csv"
    total, skipped = 0, 0
    geojson_path = RAW / "open_buildings_v3_aoi.geojson"
    google_available = candidates.is_file() or geojson_path.is_file()
    if google_available:
        if candidates.is_file():
            with candidates.open() as stream:
                records = list(csv.DictReader(stream))
        else:
            records = [{**f.get("properties", {}), "geometry": f["geometry"]} for f in json.loads(geojson_path.read_text())["features"]]
        for index, row in enumerate(records):
            if float(row["confidence"]) < .75:
                continue
            raw = from_wkt(row["geometry"]) if isinstance(row["geometry"], str) else shape(row["geometry"])
            geom = make_valid(raw).intersection(box(*AOI))
            if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            total += 1
            projected = transform(project, geom)
            if len(tree.query(projected.buffer(2), predicate="intersects")):
                skipped += 1
                continue
            features.append({"type": "Feature", "geometry": mapping(geom), "properties": {"id": "google/" + str(row.get("full_plus_code", index)), "footprint_source": "Open Buildings", "source_url": "https://sites.research.google/gr/open-buildings/", "confidence": float(row["confidence"]), "height": None, "height_source": "2023 height not available"}})
    height_path = DERIVED / "open_buildings_height_2023.tif"
    sampled = 0
    if height_path.is_file():
        with rasterio.open(height_path) as heights:
            for index, feature in enumerate(features):
                value = median_height(heights, shape(feature["geometry"]))
                props = feature["properties"]
                props["osm_height_m"] = props.get("height") if props["footprint_source"] == "OSM" else None
                if value is not None:
                    props["height"] = value
                    props["height_source"] = "Google Open Buildings 2.5D Temporal v1, 2023; footprint median"
                    sampled += 1
                if index and index % 20000 == 0:
                    print(f"Sampled heights for {index:,} footprints", flush=True)
    counts = {name: sum(f["properties"]["footprint_source"] == name for f in features) for name in ("OSM", "Open Buildings")}
    known_by_footprint = {name: 0 for name in counts}
    google_height_by_footprint = {name: 0 for name in counts}
    known_by_measurement = {}
    for feature in features:
        props = feature["properties"]
        if props.get("height") is None:
            continue
        known_by_footprint[props["footprint_source"]] += 1
        height_source = props.get("height_source", "Source not supplied")
        known_by_measurement[height_source] = known_by_measurement.get(height_source, 0) + 1
        if height_source.startswith("Google Open Buildings 2.5D Temporal"):
            google_height_by_footprint[props["footprint_source"]] += 1
    stats = {"counts": counts, "total": len(features), "height_2023_count": sampled, "height_known": sum(f["properties"].get("height") is not None for f in features), "height_missing": sum(f["properties"].get("height") is None for f in features), "google_available": google_available, "height_available": height_path.is_file(), "google_candidates": total, "google_overlapping_osm": skipped, "deduplication": "Keep OSM; reject Google footprint if it intersects any OSM footprint buffered by 2 m.", "confidence_min": .75, "height_source": "GOOGLE/Research/open-buildings-temporal/v1, building_height, 2023", "height_sampling": "Median of valid positive, presence-masked pixel centres per footprint; OSM fallback when no valid Google pixels; otherwise null.", "updated_at": datetime.now(timezone.utc).isoformat()}
    stats.update(height_known_by_footprint_source=known_by_footprint,
                 height_2023_by_footprint_source=google_height_by_footprint,
                 height_known_by_measurement_source=known_by_measurement)
    write_json(DERIVED / "buildings_merged.geojson", {"type": "FeatureCollection", "features": features})
    write_json(DERIVED / "building_sources.json", stats)
    # Keep later simulation footprints/surface consistent with the visible model.
    with rasterio.open(DERIVED / "ground_10m.tif") as ground:
        profile = ground.profile.copy()
        projected = [(transform(project, shape(f["geometry"])), f["properties"].get("height")) for f in features]
        occupied = rasterize([(g, 1) for g,h in projected], out_shape=ground.shape, transform=ground.transform, all_touched=True, dtype="uint8")
        known = sorted(((g,h) for g,h in projected if h is not None), key=lambda item: item[1])
        addition = rasterize(known, out_shape=ground.shape, transform=ground.transform, all_touched=True, dtype="float32") if known else np.zeros(ground.shape, dtype="float32")
        for name, values in (("building_mask.tif", occupied), ("surface_10m.tif", ground.read(1) + addition)):
            profile.update(dtype=values.dtype, nodata=None)
            output = DERIVED / name
            temporary = output.with_suffix(".tmp.tif")
            with rasterio.open(temporary, "w", **profile) as dst: dst.write(values, 1)
            temporary.replace(output)
    print(stats, flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=("osm", "buildings"))
    args = parser.parse_args()
    prepare_context() if args.step == "osm" else enrich_buildings()
