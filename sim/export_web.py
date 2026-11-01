"""Export real grids to a small, explicit offline viewer contract.

terrain.bin: little-endian float32, north-to-south rows, west-to-east columns.
Origin is the northwest pixel corner in EPSG:32643. Local coordinates are
[east metres, south metres]; terrain samples are pixel centres.
"""
import json
import fcntl
import math
import re
import shutil
import threading
from datetime import datetime, timezone
from contextlib import contextmanager

import numpy as np
import rasterio
from rasterio.features import shapes, rasterize
from rasterio.warp import Resampling, reproject
from pyproj import Transformer
from shapely.geometry import shape, mapping

from sim.config import CELL_SIZE, CRS, DERIVED, MASK_NAMES, RUNS, SCENARIOS, WEB_DATA
from sim.data import write_json
from sim.geometry import transform_xy as transform

_lock = threading.Lock()
_last_signature = None
_local_projector = Transformer.from_crs(4326, CRS, always_xy=True)


@contextmanager
def export_lock():
    """Serialize API watcher/CLI writers across both threads and processes."""
    with _lock:
        WEB_DATA.mkdir(parents=True, exist_ok=True)
        with (WEB_DATA / ".export.lock").open("a") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def write_binary(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    values.tofile(temp)
    temp.replace(path)


def local_geometry(geometry, origin):
    projected = transform(_local_projector.transform, shape(geometry))
    return transform(lambda x, y, z=None: (np.asarray(x) - origin[0], origin[1] - np.asarray(y)), projected)


def run_ids():
    extra = read_json(RUNS / "index.json", {"run_ids": []}).get("run_ids", [])
    return list(dict.fromkeys([*SCENARIOS, *(r for r in extra if re.fullmatch(r"[a-z0-9_-]{1,80}", r) and "env" not in r)]))


def inputs_signature():
    paths = [DERIVED / n for n in ("ground_10m.tif", "surface_10m.tif", "building_mask.tif", "aoi_mask.tif", "embankment_mask.tif", "buildings_merged.geojson", "building_sources.json", "osm_water.geojson", "osm_green.geojson", "osm_roads.geojson", "context_provenance.json", "osm_buildings.geojson", "osm_embankments.geojson", "provenance.json", "s1_meta.json", "reported_flooded.geojson", "freshness.geojson", "stage_2023.csv", "datum_offset.json", *(n + ".tif" for n in MASK_NAMES))]
    paths += [RUNS / r / n for r in run_ids() for n in ("manifest.json", "metrics.json")]
    return [(str(p), p.stat().st_mtime_ns, p.stat().st_size) if p.is_file() else (str(p), None, None) for p in paths]


def raster_on_grid(path, reference):
    with rasterio.open(path) as source:
        if source.crs is None:
            raise ValueError(f"{path.name}: missing CRS")
        values = np.full(reference.shape, np.nan, dtype=np.float32)
        reproject(source.read(1, masked=True).astype('float32').filled(np.nan), values,
                  src_transform=source.transform, src_crs=source.crs, src_nodata=np.nan,
                  dst_transform=reference.transform, dst_crs=reference.crs, dst_nodata=np.nan,
                  resampling=Resampling.nearest)
        return values, source.tags()


def sample_height(ground, x, y):
    row = min(ground.shape[0]-1, max(0, int(y / CELL_SIZE)))
    col = min(ground.shape[1]-1, max(0, int(x / CELL_SIZE)))
    return float(ground[row, col])


def export_terrain(reference, ground):
    origin = [reference.transform.c, reference.transform.f]
    provenance = read_json(DERIVED / "provenance.json", {})
    metadata = {
        "version": 1, "width": reference.width, "height": reference.height,
        "cell_size_m": CELL_SIZE, "origin": origin, "crs": CRS,
        "min_height_m": float(ground.min()), "max_height_m": float(ground.max()),
        "extent_m": [reference.width * CELL_SIZE, reference.height * CELL_SIZE],
        "dtype": "float32", "byte_order": "little-endian", "order": "row-major north-to-south",
        "sample_location": "pixel centres; origin is northwest corner",
        "local_axes": ["east metres", "south metres"], "binary_url": "/data/terrain.bin",
        "render_stride": 3, "source": provenance,
    }
    write_binary(WEB_DATA / "terrain.bin", ground.astype("<f4"))
    write_json(WEB_DATA / "terrain.json", metadata)
    features = read_json(DERIVED / "buildings_merged.geojson") or read_json(DERIVED / "osm_buildings.geojson", {"features": []})
    features = features["features"]
    buildings = []
    for feature in features:
        geom = local_geometry(feature["geometry"], origin)
        polygons = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        for polygon in polygons:
            if polygon.geom_type != "Polygon":
                continue
            point = polygon.representative_point()
            rings = [[[round(x, 2), round(y, 2)] for x, y in ring.coords] for ring in [polygon.exterior, *polygon.interiors]]
            properties = feature["properties"]
            buildings.append({"id": properties.get("osm_id") or properties.get("id"), "footprint_source": properties.get("footprint_source", "OSM"), "footprint": rings, "height": properties.get("height"), "height_source": properties.get("height_source"), "ground_height_m": sample_height(ground, point.x, point.y)})
    write_json(WEB_DATA / "buildings.json", {"origin": origin, "crs": CRS, "local_axes": metadata["local_axes"], "source": "OpenStreetMap contributors / Google Open Buildings · ODbL", "summary": read_json(DERIVED / "building_sources.json", {}), "buildings": buildings})
    embankments = read_json(DERIVED / "osm_embankments.geojson", {"features": []})
    lines = []
    for feature in embankments["features"]:
        geom = local_geometry(feature["geometry"], origin)
        for line in list(geom.geoms) if hasattr(geom, "geoms") else [geom]:
            if line.geom_type == "LineString":
                lines.append([[round(x, 2), round(y, 2)] for x, y in line.coords])
    write_json(WEB_DATA / "embankments.json", {"lines": lines, "source": "OpenStreetMap mapped dykes / embankments", "count": len(lines)})
    export_context(reference, origin)
    return metadata


def export_context(reference, origin):
    projector = Transformer.from_crs(4326, CRS, always_xy=True).transform
    landcover = np.zeros(reference.shape, dtype="uint8")
    for name, bit in (("water", 1), ("green", 2)):
        features = read_json(DERIVED / f"osm_{name}.geojson", {"features": []})["features"]
        polygons = [(transform(projector, shape(f["geometry"])), bit) for f in features]
        if polygons:
            landcover |= rasterize(polygons, out_shape=reference.shape, transform=reference.transform, dtype="uint8")
    write_binary(WEB_DATA / "landcover.bin", landcover)
    roads = []
    for feature in read_json(DERIVED / "osm_roads.geojson", {"features": []})["features"]:
        geometry = local_geometry(feature["geometry"], origin)
        properties = feature["properties"]
        if not properties.get("width_m") or properties.get("tunnel"):
            continue
        for line in list(geometry.geoms) if hasattr(geometry, "geoms") else [geometry]:
            if line.geom_type == "LineString":
                roads.append({**properties, "points": [[round(x, 2), round(y, 2)] for x,y in line.coords]})
    write_json(WEB_DATA / "context.json", {"source": "OpenStreetMap contributors · ODbL", "water_label": "Real: OpenStreetMap river outline", "landcover_url": "/data/landcover.bin", "landcover_bits": {"water": 1, "green": 2}, "water_cells": int(np.count_nonzero(landcover & 1)), "green_cells": int(np.count_nonzero(landcover & 2)), "roads": roads, "provenance": read_json(DERIVED / "context_provenance.json", {})})


def export_mask(name, reference, dates):
    path = DERIVED / f"{name}.tif"
    if not path.is_file():
        return {"available": False, "reason": f"Waiting for {path.name}"}
    try:
        values, tags = raster_on_grid(path, reference)
        wet = (np.isfinite(values) & (values > 0)).astype("uint8")
        polygons = []
        origin = (reference.transform.c, reference.transform.f)
        for geo, value in shapes(wet, mask=wet.astype(bool), transform=reference.transform):
            if value != 1:
                continue
            geom = shape(geo).simplify(CELL_SIZE * 0.4, preserve_topology=True)
            local = transform(lambda x, y, z=None: (np.asarray(x)-origin[0], origin[1]-np.asarray(y)), geom)
            polygons.append(mapping(local))
        date_key = {"s1_water_before": "before", "s1_water_recession": "recession"}.get(name, "peak")
        date = dates.get(f"{date_key}_dates") or tags.get("acquisition_date") or tags.get("TIFFTAG_DATETIME")
        if isinstance(date, list):
            date = ", ".join(date)
        payload = {"source": "Sentinel-1", "label": f"Real: Sentinel-1, {date or 'date not supplied'}", "date": date, "geometries": polygons, "wet_cells": int(wet.sum()), "area_km2": float(wet.sum() * CELL_SIZE**2 / 1e6)}
        write_json(WEB_DATA / f"{name}.json", payload)
        return {"available": True, "url": f"/data/{name}.json", "label": payload["label"], "date_missing": not bool(date)}
    except (OSError, ValueError, rasterio.errors.RasterioError) as exc:
        return {"available": False, "reason": f"Cannot read {path.name}: {exc}"}


def export_features(name, origin):
    path = DERIVED / f"{name}.geojson"
    if not path.is_file():
        return {"available": False, "reason": f"Waiting for {path.name}"}
    try:
        data = read_json(path)
        features = []
        for feature in data["features"]:
            geom = local_geometry(feature["geometry"], origin)
            features.append({"type": "Feature", "geometry": mapping(geom), "properties": feature.get("properties", {})})
        write_json(WEB_DATA / f"{name}.json", {"type": "FeatureCollection", "features": features})
        return {"available": True, "url": f"/data/{name}.json"}
    except (ValueError, KeyError, TypeError) as exc:
        return {"available": False, "reason": str(exc)}


def export_run(run_id, reference, ground):
    directory = RUNS / run_id
    manifest = read_json(directory / "manifest.json")
    if not manifest:
        return {"available": False, "reason": "No computed simulation run"}
    output = WEB_DATA / "runs" / run_id
    output.mkdir(parents=True, exist_ok=True)
    frames = []
    stride = 3
    for frame in manifest.get("frames", []):
        filename = frame["depth_file"]
        if not re.fullmatch(r"depth_[0-9T_-]+\.tif", filename):
            raise ValueError("Invalid depth filename in manifest")
        source = directory / filename
        destination = output / filename.replace(".tif", ".bin")
        if not destination.is_file() or destination.stat().st_mtime_ns < source.stat().st_mtime_ns:
            depth, _ = raster_on_grid(source, reference)
            write_binary(destination, np.where(np.isfinite(depth) & (depth > 0), depth, 0)[::stride, ::stride].astype("<f4"))
        frames.append({**frame, "url": f"/data/runs/{run_id}/{destination.name}"})
    metadata = {**manifest, "frames": frames, "render_stride": stride, "width": math.ceil(reference.width / stride), "height": math.ceil(reference.height / stride), "cell_size_m": CELL_SIZE * stride, "encoding": "little-endian float32 depth metres", "metrics_url": None}
    if (directory / "metrics.json").is_file():
        shutil.copyfile(directory / "metrics.json", output / "metrics.json")
        metadata["metrics_url"] = f"/data/runs/{run_id}/metrics.json"
    write_json(output / "manifest.json", metadata)
    return {"available": bool(frames), "url": f"/data/runs/{run_id}/manifest.json", "calibrated": manifest.get("calibrated", False)}


def export_all(force=False):
    global _last_signature
    with export_lock():
        signature = inputs_signature()
        if not force and signature == _last_signature and (WEB_DATA / "catalog.json").is_file():
            return read_json(WEB_DATA / "catalog.json")
        catalog = {"version": 1, "updated_at": datetime.now(timezone.utc).isoformat(), "terrain": None, "masks": {}, "runs": {}, "stage": None}
        ground_path = DERIVED / "ground_10m.tif"
        if not ground_path.is_file():
            catalog["reason"] = "Run make data grid export to prepare real terrain."
            write_json(WEB_DATA / "catalog.json", catalog)
            return catalog
        with rasterio.open(ground_path) as reference:
            ground = reference.read(1)
            terrain_inputs = [ground_path, *(DERIVED / name for name in ("buildings_merged.geojson", "building_sources.json", "osm_buildings.geojson", "osm_embankments.geojson", "provenance.json", "osm_water.geojson", "osm_green.geojson", "osm_roads.geojson", "context_provenance.json"))]
            if force or not (WEB_DATA / "terrain.json").is_file() or any(p.is_file() and p.stat().st_mtime_ns > (WEB_DATA / "terrain.json").stat().st_mtime_ns for p in terrain_inputs):
                metadata = export_terrain(reference, ground)
            else:
                metadata = read_json(WEB_DATA / "terrain.json")
            catalog["terrain"] = "/data/terrain.json"
            dates = read_json(DERIVED / "s1_meta.json", {})
            for name in MASK_NAMES:
                catalog["masks"][name] = export_mask(name, reference, dates)
            catalog["reported"] = export_features("reported_flooded", metadata["origin"])
            catalog["freshness"] = export_features("freshness", metadata["origin"])
            for run_id in run_ids():
                try:
                    catalog["runs"][run_id] = export_run(run_id, reference, ground)
                except (ValueError, OSError, rasterio.errors.RasterioError, KeyError) as exc:
                    catalog["runs"][run_id] = {"available": False, "reason": str(exc)}
            stage_path = DERIVED / "stage_2023.csv"
            if stage_path.is_file():
                import csv
                rows = list(csv.DictReader(stage_path.open()))
                write_json(WEB_DATA / "stage.json", {"rows": rows})
                catalog["stage"] = "/data/stage.json"
            catalog["replay_requirements"] = [name + ".tif" for name in ("s1_water_before", "s1_water_peak") if not catalog["masks"][name]["available"]]
            if not (DERIVED / "datum_offset.json").is_file():
                catalog["replay_requirements"].append("datum calibration (including a sourced pre-flood gauge level)")
            if not stage_path.is_file():
                catalog["replay_requirements"].append("verified stage_2023.csv")
            from sim.dynamic import requirements as dynamic_requirements
            catalog["dynamic_requirements"] = dynamic_requirements()
            from sim.relative import requirements as relative_requirements
            catalog["relative_requirements"] = relative_requirements()
        write_json(WEB_DATA / "catalog.json", catalog)
        _last_signature = signature
        return catalog


if __name__ == "__main__":
    result = export_all(force=True)
    print(json.dumps(result, indent=2))
