"""Deterministic connected-stage replay. Requires real truth and explicit datum calibration.

The July 2023 replay/score stays static; dynamic what-ifs live in dynamic.py.
No runs or scores are created when required scientific inputs are missing.
"""
import csv
import json
import hashlib
import time
from datetime import datetime, timedelta

import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy import ndimage
from shapely.geometry import LineString, Polygon, shape
from pyproj import Transformer

from sim.config import CELL_SIZE, CRS, DERIVED, RUNS
from sim.data import write_json
from sim.export_web import raster_on_grid, read_json
from sim.geometry import transform_xy as transform


def requirements():
    required = ("ground_10m.tif", "surface_10m.tif", "aoi_mask.tif", "building_mask.tif", "embankment_mask.tif", "s1_water_before.tif", "s1_water_peak.tif", "stage_2023.csv", "datum_offset.json")
    missing = [name for name in required if not (DERIVED / name).is_file()]
    if not missing:
        calibration = read_json(DERIVED / "datum_offset.json", {})
        if calibration.get("calibrated") is not True or not isinstance(calibration.get("offset_m"), (int, float)) or not np.isfinite(calibration["offset_m"]):
            missing.append("a valid calibrated datum_offset.json")
    return missing


def hourly_stages(path):
    with path.open() as stream:
        rows = [row for row in csv.DictReader(stream) if row.get("verified", "").lower() == "true"]
    if len(rows) < 2:
        raise ValueError("At least two verified, cited river stages are required")
    records = []
    for row in rows:
        stamp, height = datetime.fromisoformat(row["time_ist"]), float(row["level_m"])
        uncertainty = float(row.get("time_uncertainty_minutes") or 0)
        source = row["source_url"]
        if stamp.tzinfo is None or not source or not np.isfinite(height) or not np.isfinite(uncertainty) or uncertainty < 0:
            raise ValueError("Every verified stage needs a timezone, finite level, valid time uncertainty and source URL")
        alternatives = json.loads(row.get("time_alternatives_ist") or "[]") or [stamp.isoformat()]
        if not isinstance(alternatives, list) or any(datetime.fromisoformat(value).tzinfo is None for value in alternatives):
            raise ValueError("Alternative gauge timestamps must be timezone-aware")
        records.append({"time": stamp, "level_m": height, "source_url": source, "time_uncertainty_minutes": uncertainty,
                        "time_alternatives_ist": alternatives, "notes": row.get("notes", "")})
    records.sort(key=lambda row: row["time"])
    times = np.array([row["time"].timestamp() for row in records])
    if len(np.unique(times)) != len(times):
        raise ValueError("Duplicate stage timestamps are ambiguous")
    result = []
    moment = records[0]["time"]
    while moment <= records[-1]["time"]:
        stamp = moment.timestamp()
        at = int(np.searchsorted(times, stamp))
        exact = at < len(times) and times[at] == stamp
        bounds = [records[at]] if exact else [records[at-1], records[at]]
        uncertainty = max(row["time_uncertainty_minutes"] for row in bounds)
        notes = list(dict.fromkeys(row["notes"] for row in bounds if row["notes"]))
        result.append({"time_ist": moment.isoformat(), "level_m": float(np.interp(stamp, times, [row["level_m"] for row in records])),
                       "interpolated": not exact, "stage_kind": ("Reported gauge stage" if exact else "Interpolated gauge stage") + (" · source time uncertain" if uncertainty else ""),
                       "source_urls": list(dict.fromkeys(row["source_url"] for row in bounds)),
                       "source_times_ist": [row["time"].isoformat() for row in bounds],
                       "time_uncertain": bool(uncertainty), "time_uncertainty_minutes": uncertainty,
                       "source_time_alternatives_ist": [row["time_alternatives_ist"] for row in bounds],
                       "interpolation_gap_hours": 0 if exact else (bounds[1]["time"]-bounds[0]["time"]).total_seconds()/3600,
                       "stage_notes": notes})
        moment += timedelta(hours=1)
    return result


def connected_water(surface, seed, level, blocked, valid):
    eligible = (surface < level) & ~blocked & valid & np.isfinite(surface)
    return ndimage.binary_propagation(seed & eligible, structure=np.ones((3, 3), dtype=bool), mask=eligible)


def main_river(mask):
    labels, count = ndimage.label(mask, structure=np.ones((3, 3), dtype=bool))
    if not count:
        raise ValueError("The real pre-flood mask has no wet cells in the AOI")
    sizes = np.bincount(labels.ravel()); sizes[0] = 0
    return labels == sizes.argmax()


def overlap(predicted, truth, valid):
    true_positive = int(np.count_nonzero(predicted & truth & valid))
    false_positive = int(np.count_nonzero(predicted & ~truth & valid))
    false_negative = int(np.count_nonzero(~predicted & truth & valid))
    return {"iou": true_positive / (true_positive + false_positive + false_negative) if true_positive + false_positive + false_negative else None,
            "precision": true_positive / (true_positive + false_positive) if true_positive + false_positive else None,
            "recall": true_positive / (true_positive + false_negative) if true_positive + false_negative else None}


def satellite_overlap(predicted, observations, valid):
    observed = np.isfinite(observations) & valid
    return {**overlap(predicted, observed & (observations > 0), observed),
            "satellite_evaluated_area_km2": float(observed.sum()*CELL_SIZE**2/1e6),
            "satellite_valid_fraction_of_aoi": float(observed.sum()/valid.sum()) if valid.any() else None}


def calibrate():
    meta = read_json(DERIVED / "s1_meta.json", {})
    if not isinstance(meta.get("pre_flood_stage_m"), (float, int)) or not meta.get("pre_flood_stage_source_url") or not meta.get("before_dates"):
        raise ValueError("s1_meta.json must include before_dates, pre_flood_stage_m and pre_flood_stage_source_url. Do not use a July flood-peak stage to calibrate a pre-flood mask.")
    gap = meta.get("pre_flood_stage_gap_hours", 0)
    if not isinstance(gap, (int, float)) or not np.isfinite(gap) or gap < 0:
        raise ValueError("Pre-flood stage temporal gap must be a finite nonnegative number")
    if gap and meta.get("pre_flood_stage_gap_authorized") is not True:
        raise ValueError("A non-time-matched pre-flood stage needs explicit authorization and gap metadata")
    provenance = read_json(DERIVED / "provenance.json", {})
    if "FABDEM" not in str(provenance.get("terrain", {}).get("source", "")).upper():
        raise ValueError("Datum calibration requires the current FABDEM ground")
    inputs = {name: hashlib.sha256((DERIVED / name).read_bytes()).hexdigest() for name in ("ground_10m.tif", "surface_10m.tif", "building_mask.tif", "aoi_mask.tif", "s1_water_before.tif", "s1_meta.json")}
    existing = read_json(DERIVED / "datum_offset.json", {})
    if existing.get("calibrated") is True and existing.get("input_sha256") == inputs:
        return existing
    started = time.perf_counter()
    with rasterio.open(DERIVED / "ground_10m.tif") as ref:
        truth, _ = raster_on_grid(DERIVED / "s1_water_before.tif", ref)
        with rasterio.open(DERIVED / "surface_10m.tif") as src: surface = src.read(1)
        with rasterio.open(DERIVED / "building_mask.tif") as src: blocked = src.read(1).astype(bool)
        with rasterio.open(DERIVED / "aoi_mask.tif") as src: valid = src.read(1).astype(bool)
        observed = np.isfinite(truth) & valid
        truth = observed & (truth > 0)
        seed = main_river(truth)
        curve = []
        for offset in np.linspace(-5, 5, 101):
            water = connected_water(surface, seed, meta["pre_flood_stage_m"] + offset, blocked, valid)
            curve.append({"offset_m": round(float(offset), 1), "iou": overlap(water, truth, observed)["iou"]})
    best = max(curve, key=lambda point: (point["iou"] if point["iou"] is not None else -1, -abs(point["offset_m"])))
    if not best["iou"]:
        raise ValueError("No positive pre-flood overlap within the specified datum search; check the inputs instead of accepting a meaningless calibration")
    payload = {"calibrated": True, "offset_m": best["offset_m"], "before_iou": best["iou"], "curve": curve, "pre_flood_stage_m": meta["pre_flood_stage_m"], "pre_flood_stage_source_url": meta["pre_flood_stage_source_url"], "before_dates": meta["before_dates"], "decided_by": "Otec deterministic IoU search", "at_search_boundary": abs(best["offset_m"]) == 5}
    payload.update({key: value for key, value in meta.items() if key.startswith("pre_flood_stage_") or key in ("pre_flood_matched_acquisition", "pre_flood_acquisition_gaps_hours")})
    payload.update(input_sha256=inputs, runtime_s=time.perf_counter()-started,
                   calibration_caveat="Spatial pre-flood mask fit with a closest sourced gauge proxy; the acquisition-time gap is not corrected or hidden." if gap else "Spatial pre-flood mask fit; not independent hydraulic validation.",
                   mask_classification=meta.get("classification"))
    payload["satellite_evaluated_area_km2"] = float(observed.sum()*CELL_SIZE**2/1e6)
    write_json(DERIVED / "datum_offset.json", payload)
    return payload


def compute_run(run_id="replay2023", edits=None, river_plus_m=0.0):
    missing = requirements()
    if missing:
        raise ValueError("Missing real replay inputs: " + ", ".join(missing))
    calibration = read_json(DERIVED / "datum_offset.json")
    stages = hourly_stages(DERIVED / "stage_2023.csv")
    directory = RUNS / run_id
    started = time.perf_counter()
    input_names = ("ground_10m.tif", "surface_10m.tif", "aoi_mask.tif", "building_mask.tif", "embankment_mask.tif", "s1_water_before.tif", "s1_water_peak.tif", "stage_2023.csv", "datum_offset.json", "s1_meta.json", "buildings_merged.geojson", "osm_buildings.geojson", "osm_roads.geojson")
    inputs = {name: hashlib.sha256((DERIVED / name).read_bytes()).hexdigest() for name in input_names if (DERIVED / name).is_file()}
    configuration_hash = hashlib.sha256(json.dumps({"inputs": inputs, "edits": edits or [], "river_plus_m": river_plus_m, "version": 2}, sort_keys=True).encode()).hexdigest()
    existing = read_json(directory / "manifest.json", {})
    if existing.get("configuration_hash") == configuration_hash and existing.get("frames") and (directory / "metrics.json").is_file() and all((directory / frame["depth_file"]).is_file() for frame in existing["frames"]):
        return run_id
    revision = str(int(configuration_hash[:12], 16))
    with rasterio.open(DERIVED / "ground_10m.tif") as ref:
        ground = ref.read(1); profile = ref.profile.copy()
        with rasterio.open(DERIVED / "surface_10m.tif") as src: surface = src.read(1)
        with rasterio.open(DERIVED / "building_mask.tif") as src: blocked = src.read(1).astype(bool)
        with rasterio.open(DERIVED / "aoi_mask.tif") as src: valid = src.read(1).astype(bool)
        with rasterio.open(DERIVED / "embankment_mask.tif") as src: banks = src.read(1).astype(bool)
        before, _ = raster_on_grid(DERIVED / "s1_water_before.tif", ref)
        peak, _ = raster_on_grid(DERIVED / "s1_water_peak.tif", ref)
        seed = main_river(np.isfinite(before) & (before > 0) & valid)
        origin = (ref.transform.c, ref.transform.f)
        for edit in edits or []:
            coords = [(origin[0] + p[0], origin[1] - p[1]) for p in edit["coordinates"]]
            geometry = LineString(coords).buffer(CELL_SIZE) if edit["type"] == "breach" else Polygon(coords)
            if not geometry.is_valid:
                raise ValueError("Edit geometry is invalid or self-intersecting")
            cells = rasterize([(geometry, 1)], out_shape=ground.shape, transform=ref.transform, all_touched=True, dtype="uint8").astype(bool) & valid
            if not cells.any(): raise ValueError("Edit does not intersect the AOI")
            if edit["type"] == "breach":
                if not (cells & banks).any(): raise ValueError("Breach must cross a mapped embankment")
                # Explicit simulated excavation: lower the intersected bank to the local minimum, never claim it is observed.
                cut = cells & banks
                surface[cut] = ndimage.minimum_filter(ground, size=7)[cut]
            else:
                surface[cells] += edit["height_m"]
        directory.mkdir(parents=True, exist_ok=True)
        profile.update(dtype="float32", nodata=0, compress="deflate", count=1)
        frames = []; peak_depth = None; maximum_stage = max(s["level_m"] for s in stages)
        for index, stage in enumerate(stages):
            level = stage["level_m"] + calibration["offset_m"] + river_plus_m
            wet = connected_water(surface, seed, level, blocked, valid)
            depth = np.where(wet, np.maximum(0, level-ground), 0).astype("float32")
            moment = datetime.fromisoformat(stage["time_ist"])
            filename = f'depth_{moment.strftime("%Y%m%dT%H%M%S")}_{revision}.tif'
            temporary = directory / (filename + ".tmp")
            with rasterio.open(temporary, "w", **profile) as dst: dst.write(depth, 1)
            temporary.replace(directory / filename)
            frames.append({**stage, "level_m": stage["level_m"] + river_plus_m, "water_surface_m": level, "depth_file": filename})
            if stage["level_m"] == maximum_stage and peak_depth is None: peak_depth = depth
            if index % 24 == 0 or index+1 == len(stages):
                print(f"Static replay frame {index+1}/{len(stages)}: {stage['time_ist']} ({stage['stage_kind']})", flush=True)
        assert peak_depth is not None
        wet = peak_depth > 0
        observed_peak = np.isfinite(peak) & valid
        result = {**satellite_overlap(wet, peak, valid), "flooded_area_km2": float(wet.sum()*CELL_SIZE**2/1e6), "buildings_wet": None, "roads_closed": None, "reported_places_hits": None, "model": "static 8-neighbour connected-stage fill", "calibrated": True, "score_time_ist": frames[next(i for i,s in enumerate(stages) if s["level_m"] == maximum_stage)]["time_ist"], "truth_dates": read_json(DERIVED / "s1_meta.json", {}).get("peak_dates"), "score_caveat": "Compares the highest sourced hourly stage to the supplied peak mask over finite observed satellite pixels only; NoData is excluded. Inspect acquisition dates before interpreting the score; this is not time-matched validation."}
        project = Transformer.from_crs(4326, CRS, always_xy=True).transform
        # Buildings block flow, so exposure is measured on adjacent cells, not impermeable roofs.
        adjacent = ndimage.maximum_filter(peak_depth, size=3)
        for filename, field, threshold in ((("buildings_merged.geojson" if (DERIVED / "buildings_merged.geojson").is_file() else "osm_buildings.geojson"), "buildings_wet", 0.5), ("osm_roads.geojson", "roads_closed", 0.3)):
            features = read_json(DERIVED / filename, {"features": []})["features"]
            count = 0
            for feature in features:
                geom = transform(project, shape(feature["geometry"]))
                minx, miny, maxx, maxy = geom.bounds
                r0, c0 = ref.index(minx, maxy); r1, c1 = ref.index(maxx, miny)
                r0, c0 = max(0, r0-1), max(0, c0-1); r1, c1 = min(ref.height, r1+2), min(ref.width, c1+2)
                if r1 <= r0 or c1 <= c0: continue
                window_transform = ref.window_transform(rasterio.windows.Window(c0, r0, c1-c0, r1-r0))
                cells = rasterize([(geom, 1)], out_shape=(r1-r0, c1-c0), transform=window_transform, all_touched=True, dtype="uint8").astype(bool)
                field_depth = adjacent if field == "buildings_wet" else peak_depth
                count += int(np.any(field_depth[r0:r1, c0:c1][cells] > threshold))
            result[field] = count
        result["roads_closed_definition"] = "Count of mapped OSM highway ways with at least one cell deeper than 0.3 m; not a routing or official closure claim."
        result["buildings_wet_definition"] = "Mapped footprints touching an adjacent cell deeper than 0.5 m."
        score_frame = next(frame for frame in frames if frame["level_m"] == maximum_stage + river_plus_m)
        result.update(runtime_s=time.perf_counter()-started, input_sha256=inputs,
                      satellite_evaluated_area_km2=float(observed_peak.sum()*CELL_SIZE**2/1e6),
                      satellite_valid_fraction_of_aoi=float(observed_peak.sum()/valid.sum()) if valid.any() else None,
                      observed_stage_count=sum(not frame["interpolated"] for frame in frames),
                      interpolated_stage_count=sum(frame["interpolated"] for frame in frames),
                      score_time_uncertainty_minutes=score_frame["time_uncertainty_minutes"],
                      score_time_alternatives_ist=score_frame["source_time_alternatives_ist"],
                      pre_flood_stage_gap_hours=calibration.get("pre_flood_stage_gap_hours", 0),
                      calibration_caveat=calibration.get("calibration_caveat"),
                      mask_classification=read_json(DERIVED / "s1_meta.json", {}).get("classification"))
        if score_frame["time_uncertain"]:
            result["score_caveat"] += " The published peak timestamp conflicts between 19:00 and 20:00 IST; 19:00 is the selected anchor with one-hour uncertainty."
        if result["pre_flood_stage_gap_hours"]:
            result["score_caveat"] += f" Datum fitting uses a gauge reading {result['pre_flood_stage_gap_hours']:.3f} hours from the nearest pre-flood acquisition; this temporal mismatch is not corrected."
        write_json(directory / "metrics.json", result)
        write_json(directory / "manifest.json", {"run_id": run_id, "calibrated": True, "model": result["model"], "frames": frames, "edits": edits or [], "river_plus_m": river_plus_m,
                   "configuration_hash": configuration_hash, "input_sha256": inputs,
                   "stage_policy": "Published gauge anchors; every other hourly frame is labelled interpolated. Peak19:00/20:00 timestamp ambiguity is retained.",
                   "calibration_caveat": result["calibration_caveat"], "pre_flood_stage_gap_hours": result["pre_flood_stage_gap_hours"]})
    return run_id


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=("calibrate", "replay")); args = parser.parse_args()
    print(calibrate() if args.command == "calibrate" else compute_run())
