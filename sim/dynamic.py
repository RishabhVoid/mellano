"""Deterministic dynamic what-if routing; static replay/scoring stays in replay.py.

Virtual pipes carry a signed discharge per unit width across each shared face.
Pressure gradients and local inertia follow the storage-cell shallow-water
approximation of Bates et al. (2010), DOI 10.1016/j.jhydrol.2010.03.027. Manning
drag is semi-implicit; a donor-volume limiter prevents negative depths. Convective
momentum, turbulence, drainage, rainfall and channel bathymetry are not modelled.
This is a local-inertial shallow-water approximation, not a validated forecast.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import platform
import re
import time

import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy import ndimage
from shapely.geometry import LineString, Polygon, shape
from shapely.ops import unary_union
from sim.geometry import transform_xy as transform
from pyproj import Transformer

from sim.config import CELL_SIZE, CRS, DERIVED, RUNS
from sim.data import write_json
from sim.export_web import raster_on_grid, read_json
from sim.replay import main_river, requirements as replay_requirements

MODEL = "dynamic virtual-pipe local-inertial shallow water"
MODEL_VERSION = 1
GRAVITY = 9.80665
REFERENCES = [
    "https://doi.org/10.1016/j.jhydrol.2010.03.027",
    "https://www-evasion.imag.fr/Publications/2007/MDH07/",
]


@dataclass(frozen=True)
class DynamicConfig:
    cell_size_m: float = CELL_SIZE
    dt_s: float = 0.25
    duration_s: float = 1800.0
    output_interval_s: float = 30.0
    manning_n: float = 0.04
    max_courant: float = 0.45
    river_ramp_s: float = 300.0

    def validate(self):
        if any(not np.isfinite(v) for v in asdict(self).values()):
            raise ValueError("Dynamic parameters must be finite")
        if min(self.cell_size_m, self.dt_s, self.duration_s, self.output_interval_s, self.river_ramp_s) <= 0:
            raise ValueError("Cell size and time parameters must be positive")
        if not 0 <= self.manning_n <= 1 or not 0 < self.max_courant <= 0.5:
            raise ValueError("Manning n must be in [0,1] and the gravity-wave Courant limit in (0,0.5]")
        for interval in (self.duration_s, self.output_interval_s):
            if not np.isclose(interval / self.dt_s, round(interval / self.dt_s), rtol=0, atol=1e-8):
                raise ValueError("Duration and output interval must be integer multiples of the fixed time step")


class VirtualPipes:
    """Closed-domain finite-volume water routing with optional stage reservoirs.

    Reservoir cells are explicit external sources/sinks. Their net volume change
    is recorded separately from internal conservative face transport. Invalid or
    blocked cells and the outer raster boundary are impermeable.
    """
    def __init__(self, bed, depth, valid=None, blocked=None, boundary=None, config=None):
        self.config = config or DynamicConfig()
        self.config.validate()
        self.bed = np.asarray(bed, dtype=np.float64).copy()
        self.depth = np.asarray(depth, dtype=np.float64).copy()
        if self.bed.ndim != 2 or min(self.bed.shape) < 2 or self.depth.shape != self.bed.shape:
            raise ValueError("Bed/depth must be matching 2D grids of at least 2 × 2 cells")
        self.valid = np.ones(self.bed.shape, bool) if valid is None else np.asarray(valid, bool).copy()
        blocked = np.zeros(self.bed.shape, bool) if blocked is None else np.asarray(blocked, bool)
        self.boundary = np.zeros(self.bed.shape, bool) if boundary is None else np.asarray(boundary, bool).copy()
        if any(a.shape != self.bed.shape for a in (self.valid, blocked, self.boundary)):
            raise ValueError("All masks must match the bed grid")
        self.valid &= ~blocked
        self.boundary &= self.valid
        if not np.isfinite(self.bed[self.valid]).all() or not np.isfinite(self.depth).all() or np.any(self.depth < 0):
            raise ValueError("Active terrain/depth must be finite and initial depth nonnegative")
        if np.any(self.depth[~self.valid] > 0):
            raise ValueError("Initial water may not occupy blocked or invalid cells")
        # Invalid-cell elevations never participate in a valid face.
        self.bed[~self.valid] = 0
        self.qx = np.zeros((self.bed.shape[0], self.bed.shape[1] - 1), dtype=np.float64)
        self.qy = np.zeros((self.bed.shape[0] - 1, self.bed.shape[1]), dtype=np.float64)
        self.open_x = self.valid[:, :-1] & self.valid[:, 1:]
        self.open_y = self.valid[:-1, :] & self.valid[1:, :]
        self.outgoing = np.zeros_like(self.depth)
        self.scale = np.ones_like(self.depth)
        self.initial_volume_m3 = self.volume_m3
        self.boundary_added_m3 = 0.0
        self.boundary_removed_m3 = 0.0
        self.roundoff_added_m3 = 0.0
        self.limited_steps = 0
        self.steps = 0
        self.maximum_courant = 0.0

    @property
    def volume_m3(self):
        return float(self.depth.sum(dtype=np.float64) * self.config.cell_size_m ** 2)

    def impose_stage(self, stage_m):
        if not np.isfinite(stage_m):
            raise ValueError("Boundary stage must be finite")
        if not self.boundary.any():
            return
        desired = np.maximum(0.0, stage_m - self.bed[self.boundary])
        difference = (desired - self.depth[self.boundary]) * self.config.cell_size_m ** 2
        self.boundary_added_m3 += float(difference[difference > 0].sum(dtype=np.float64))
        self.boundary_removed_m3 += float(-difference[difference < 0].sum(dtype=np.float64))
        self.depth[self.boundary] = desired

    def _flux(self, q, eta_a, eta_b, bed_a, bed_b, open_faces):
        flow_depth = np.maximum(0.0, np.maximum(eta_a, eta_b) - np.maximum(bed_a, bed_b))
        wet = open_faces & (flow_depth > 1e-10)
        safe_depth = np.maximum(flow_depth, 1e-8)
        cfg = self.config
        numerator = q - cfg.dt_s * GRAVITY * flow_depth * (eta_b - eta_a) / cfg.cell_size_m
        drag = 1 + cfg.dt_s * GRAVITY * cfg.manning_n ** 2 * np.abs(q) / safe_depth ** (7 / 3)
        np.divide(numerator, drag, out=q)
        q[~wet] = 0.0

    def step(self, stage_m=None):
        if stage_m is not None:
            self.impose_stage(stage_m)
        cfg = self.config
        courant = cfg.dt_s * math.sqrt(GRAVITY * float(self.depth.max())) / cfg.cell_size_m
        self.maximum_courant = max(self.maximum_courant, courant)
        if courant > cfg.max_courant + 1e-12:
            raise ValueError(f"Fixed dt={cfg.dt_s:g}s violates the gravity-wave Courant limit ({courant:.4f} > {cfg.max_courant:g}); choose a smaller fixed dt before rerunning")
        eta = self.bed + self.depth
        self._flux(self.qx, eta[:, :-1], eta[:, 1:], self.bed[:, :-1], self.bed[:, 1:], self.open_x)
        self._flux(self.qy, eta[:-1, :], eta[1:, :], self.bed[:-1, :], self.bed[1:, :], self.open_y)
        self.outgoing.fill(0)
        self.outgoing[:, :-1] += np.maximum(self.qx, 0)
        self.outgoing[:, 1:] += np.maximum(-self.qx, 0)
        self.outgoing[:-1, :] += np.maximum(self.qy, 0)
        self.outgoing[1:, :] += np.maximum(-self.qy, 0)
        self.scale.fill(1)
        np.divide(self.depth * cfg.cell_size_m, cfg.dt_s * self.outgoing, out=self.scale, where=self.outgoing > 0)
        np.minimum(self.scale, 1, out=self.scale)
        self.limited_steps += int(np.any(self.scale < 1))
        self.qx *= np.where(self.qx >= 0, self.scale[:, :-1], self.scale[:, 1:])
        self.qy *= np.where(self.qy >= 0, self.scale[:-1, :], self.scale[1:, :])
        change = cfg.dt_s / cfg.cell_size_m
        self.depth[:, :-1] -= change * self.qx
        self.depth[:, 1:] += change * self.qx
        self.depth[:-1, :] -= change * self.qy
        self.depth[1:, :] += change * self.qy
        minimum = float(self.depth.min())
        if minimum < -1e-9 or not np.isfinite(self.depth).all():
            raise FloatingPointError("Dynamic routing produced an invalid water depth")
        negative = self.depth < 0
        self.roundoff_added_m3 += float(-self.depth[negative].sum(dtype=np.float64) * cfg.cell_size_m ** 2)
        self.depth[negative] = 0
        if stage_m is not None:
            self.impose_stage(stage_m)
        self.steps += 1
        return self.depth

    def water_balance(self):
        expected = self.initial_volume_m3 + self.boundary_added_m3 - self.boundary_removed_m3 + self.roundoff_added_m3
        residual = self.volume_m3 - expected
        return {"initial_volume_m3": self.initial_volume_m3, "final_volume_m3": self.volume_m3,
                "boundary_added_m3": self.boundary_added_m3, "boundary_removed_m3": self.boundary_removed_m3,
                "roundoff_added_m3": self.roundoff_added_m3, "residual_m3": residual,
                "relative_residual": abs(residual) / max(1.0, abs(expected)),
                "fixed_dt_s": self.config.dt_s, "steps": self.steps,
                "maximum_gravity_wave_courant": self.maximum_courant, "limited_steps": self.limited_steps}


def requirements():
    """Read exact known paths only; never publish empty stand-in runs."""
    missing = replay_requirements()
    provenance = read_json(DERIVED / "provenance.json", {})
    if "FABDEM" not in str(provenance.get("terrain", {}).get("source", "")).upper():
        missing.append("FABDEM ground provenance")
    replay_path = RUNS / "replay2023" / "manifest.json"
    replay = read_json(replay_path, {})
    if replay.get("calibrated") is not True or not replay.get("frames") or replay.get("model") != "static 8-neighbour connected-stage fill" or replay.get("edits") or replay.get("river_plus_m", 0) != 0:
        missing.append("completed calibrated static runs/replay2023/manifest.json")
    if replay_path.is_file():
        sources = [DERIVED / name for name in ("ground_10m.tif", "surface_10m.tif", "building_mask.tif", "aoi_mask.tif", "embankment_mask.tif", "s1_water_before.tif", "s1_water_peak.tif", "s1_meta.json", "datum_offset.json", "stage_2023.csv")]
        if any(path.is_file() and path.stat().st_mtime_ns > replay_path.stat().st_mtime_ns for path in sources):
            missing.append("static replay recomputed after the current terrain/building/calibration inputs")
    calibration = DERIVED / "datum_offset.json"
    calibration_inputs = [DERIVED / name for name in ("ground_10m.tif", "surface_10m.tif", "building_mask.tif", "aoi_mask.tif", "s1_water_before.tif", "s1_meta.json")]
    if calibration.is_file() and any(path.is_file() and path.stat().st_mtime_ns > calibration.stat().st_mtime_ns for path in calibration_inputs):
        missing.append("datum calibration recomputed on current FABDEM/building/pre-flood inputs")
    return list(dict.fromkeys(missing))


def scenario_definitions():
    """Concrete hypothetical edits derived from mapped ITO/Ring Road features."""
    roads = read_json(DERIVED / "osm_roads.geojson", {"features": []})["features"]
    banks = read_json(DERIVED / "osm_embankments.geojson", {"features": []})["features"]
    project = Transformer.from_crs(4326, CRS, always_xy=True).transform
    candidates = [(feature, transform(project, shape(feature["geometry"]))) for feature in roads if feature.get("properties", {}).get("name") == "ITO Flyover"]
    if not candidates or not banks:
        raise ValueError("Mapped ITO Flyover and embankment features are required to choose reproducible hypothetical edits")
    anchor = unary_union([geometry for _, geometry in candidates]).centroid
    mapped_banks = [(feature, transform(project, shape(feature["geometry"]))) for feature in banks]
    bank_feature, bank = min(mapped_banks, key=lambda item: (item[1].distance(anchor), item[0]["properties"]["osm_id"]))
    if bank.geom_type != "LineString":
        raise ValueError("The nearest mapped embankment is not a single line; choose a breach explicitly")
    station = bank.project(anchor)
    centre = bank.interpolate(station)
    a, b = bank.interpolate(max(0, station-10)), bank.interpolate(min(bank.length, station+10))
    direction = np.array([b.x-a.x, b.y-a.y]); length = np.linalg.norm(direction)
    if length == 0: raise ValueError("Mapped embankment has no usable tangent")
    normal = np.array([-direction[1], direction[0]]) / length
    breach = [[centre.x-normal[0]*40, centre.y-normal[1]*40], [centre.x+normal[0]*40, centre.y+normal[1]*40]]
    ring_roads = [(feature, transform(project, shape(feature["geometry"]))) for feature in roads if feature.get("properties", {}).get("name") == "Ring Road"]
    if not ring_roads: raise ValueError("Mapped Ring Road is required for the debris scenario")
    road_feature, road = min(ring_roads, key=lambda item: (item[1].distance(anchor), item[0]["properties"]["osm_id"]))
    if road.geom_type != "LineString": raise ValueError("Select a simple Ring Road segment for the debris scenario")
    station = road.project(anchor)
    centre = road.interpolate(station)
    a, b = road.interpolate(max(0, station-10)), road.interpolate(min(road.length, station+10))
    direction = np.array([b.x-a.x, b.y-a.y]); length = np.linalg.norm(direction)
    if length == 0: raise ValueError("Mapped road has no usable tangent")
    direction /= length
    debris = LineString([[centre.x-direction[0]*40, centre.y-direction[1]*40], [centre.x+direction[0]*40, centre.y+direction[1]*40]]).buffer(12, cap_style="flat")
    with rasterio.open(DERIVED / "ground_10m.tif") as source:
        origin = source.transform.c, source.transform.f
    def local(points): return [[round(x-origin[0], 3), round(origin[1]-y, 3)] for x,y in points]
    return {"version": 1, "simulated": True, "decided_by": "Codex, following user-authorized what-if scenarios", "config": asdict(DynamicConfig()),
            "base_frame_selector": "highest stage in the calibrated static replay", "scenarios": [
                {"run_id": "breach_near_ito", "edits": [{"type": "breach", "coordinates": local(breach)}], "river_plus_m": 0,
                 "source_feature": bank_feature["properties"], "assumption": "Simulated 20 m-wide rasterized cut across the nearest mapped embankment to mapped ITO Flyover, lowered to a 7-cell local ground minimum; not an observed breach."},
                {"run_id": "debris_ring_road", "edits": [{"type": "debris", "coordinates": local(debris.exterior.coords), "height_m": 2}], "river_plus_m": 0,
                 "source_feature": road_feature["properties"], "assumption": "Simulated 80 m × 24 m, 2 m-high debris patch on mapped Ring Road nearest mapped ITO Flyover; not an observed obstruction."},
                {"run_id": "river_plus_1m", "edits": [], "river_plus_m": 1,
                 "assumption": "Simulated +1 m stage rise over 300 seconds, then held; baseline comes from the sourced static replay."},
            ]}


def _apply_edits(surface, ground, banks, valid, transform_grid, edits):
    bed = surface.astype("float64", copy=True)
    changed = np.zeros(ground.shape, bool)
    origin = transform_grid.c, transform_grid.f
    for edit in edits:
        if edit.get("type") not in ("breach", "debris"):
            raise ValueError("Only breach and debris edits are supported")
        points = np.asarray(edit.get("coordinates", []), dtype="float64")
        if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
            raise ValueError("Edit coordinates must be finite local-metre pairs")
        coords = [(origin[0]+x, origin[1]-y) for x,y in points]
        if len(coords) < (2 if edit["type"] == "breach" else 3):
            raise ValueError("A breach needs two points; a debris polygon needs three")
        geometry = LineString(coords).buffer(CELL_SIZE) if edit["type"] == "breach" else Polygon(coords)
        if not geometry.is_valid or geometry.is_empty:
            raise ValueError("Edit geometry is empty or invalid")
        cells = rasterize([(geometry, 1)], out_shape=ground.shape, transform=transform_grid, all_touched=True, dtype="uint8").astype(bool) & valid
        if not cells.any(): raise ValueError("Edit does not intersect the AOI")
        if edit["type"] == "breach":
            cells &= banks
            if not cells.any(): raise ValueError("Breach must intersect mapped embankment cells")
            bed[cells] = ndimage.minimum_filter(ground, size=7)[cells]
        else:
            height = float(edit.get("height_m", 0))
            if not np.isfinite(height) or not 0 < height <= 30: raise ValueError("Debris height must be in (0,30] metres")
            bed[cells] += height
        changed |= cells
    return bed, changed


def _exposure(depth, ref):
    """Computed exposure only. Counterfactuals get no satellite-validation score."""
    project = Transformer.from_crs(4326, CRS, always_xy=True).transform
    adjacent = ndimage.maximum_filter(depth, size=3)
    result = {}
    building_file = "buildings_merged.geojson" if (DERIVED / "buildings_merged.geojson").is_file() else "osm_buildings.geojson"
    for filename, field, threshold in ((building_file, "buildings_wet", .5), ("osm_roads.geojson", "roads_closed", .3)):
        path = DERIVED / filename
        if not path.is_file():
            result[field] = None
            continue
        count = 0
        for feature in read_json(path)["features"]:
            geometry = transform(project, shape(feature["geometry"]))
            minx,miny,maxx,maxy = geometry.bounds
            r0,c0 = ref.index(minx,maxy); r1,c1 = ref.index(maxx,miny)
            r0,c0 = max(0,r0-1),max(0,c0-1); r1,c1 = min(ref.height,r1+2),min(ref.width,c1+2)
            if r1 <= r0 or c1 <= c0: continue
            values = adjacent if field == "buildings_wet" else depth
            if float(np.max(values[r0:r1,c0:c1])) <= threshold: continue
            window = rasterio.windows.Window(c0,r0,c1-c0,r1-r0)
            cells = rasterize([(geometry,1)], out_shape=(r1-r0,c1-c0), transform=ref.window_transform(window), all_touched=True, dtype="uint8").astype(bool)
            values = adjacent if field == "buildings_wet" else depth
            count += int(np.any(values[r0:r1,c0:c1][cells] > threshold))
        result[field] = count
    return result


def compute_dynamic_run(run_id, edits=None, river_plus_m=0.0, config=None):
    if run_id not in ("breach_near_ito", "debris_ring_road", "river_plus_1m") and not re.fullmatch(r"edit_[a-f0-9]{16}", run_id):
        raise ValueError("Dynamic runs may not overwrite the static replay or normal/drone outputs")
    missing = requirements()
    if missing: raise ValueError("Missing real dynamic inputs: " + ", ".join(missing))
    if not np.isfinite(river_plus_m) or not 0 <= river_plus_m <= 5:
        raise ValueError("Hypothetical stage rise must be in [0,5] metres")
    cfg = config or DynamicConfig()
    cfg.validate()
    if cfg.cell_size_m != CELL_SIZE: raise ValueError("Dynamic grid must match the 10 m analysis grid")
    started = time.perf_counter()
    replay = read_json(RUNS / "replay2023" / "manifest.json")
    frame = max(replay["frames"], key=lambda item: float(item["level_m"]))
    filename = frame.get("depth_file", "")
    if not re.fullmatch(r"depth_[0-9T_-]+\.tif", filename): raise ValueError("Static replay depth filename is invalid")
    time_zero = datetime.fromisoformat(frame["time_ist"])
    if time_zero.tzinfo is None: raise ValueError("Initial replay frame needs a timezone")
    calibration = read_json(DERIVED / "datum_offset.json")
    base_stage = float(frame["level_m"])
    offset = float(calibration["offset_m"])
    directory = RUNS / run_id
    source_paths = [DERIVED / name for name in ("ground_10m.tif", "surface_10m.tif", "aoi_mask.tif", "building_mask.tif", "embankment_mask.tif", "s1_water_before.tif", "datum_offset.json", "stage_2023.csv")]
    source_paths += [RUNS / "replay2023" / "manifest.json", RUNS / "replay2023" / filename]
    checksums = {str(path.relative_to(DERIVED.parent.parent)): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    configuration = {"version": MODEL_VERSION, "config": asdict(cfg), "edits": edits or [], "river_plus_m": river_plus_m, "inputs": checksums}
    config_hash = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
    existing = read_json(directory / "manifest.json", {})
    if existing.get("configuration_hash") == config_hash and (directory / "metrics.json").is_file(): return run_id
    revision = str(int(config_hash[:12], 16))
    with rasterio.open(DERIVED / "ground_10m.tif") as ref:
        ground, profile = ref.read(1), ref.profile.copy()
        def read_aligned(path):
            with rasterio.open(path) as source:
                if source.shape != ref.shape or source.transform != ref.transform or source.crs != ref.crs:
                    raise ValueError(f"{path.name} is not aligned to the ground grid")
                return source.read(1)
        surface = read_aligned(DERIVED / "surface_10m.tif")
        valid = read_aligned(DERIVED / "aoi_mask.tif").astype(bool)
        blocked = read_aligned(DERIVED / "building_mask.tif").astype(bool)
        banks = read_aligned(DERIVED / "embankment_mask.tif").astype(bool)
        initial = read_aligned(RUNS / "replay2023" / filename).astype("float64")
        before, _ = raster_on_grid(DERIVED / "s1_water_before.tif", ref)
        seed = main_river(np.isfinite(before) & (before > 0) & valid)
        bed, changed = _apply_edits(surface, ground, banks, valid, ref.transform, edits or [])
        initial = np.maximum(0, initial - np.maximum(0, surface-ground))
        initial[~valid | blocked] = 0
        # Preserve liquid volume when hypothetical debris lifts its bed. A breach
        # starts dry where the static initial state was dry, then receives flow.
        reservoir = seed & valid & ~blocked & ~changed
        if not reservoir.any() or not initial.any():
            raise ValueError("The real replay/mask does not define a wet river initialization")
        solver = VirtualPipes(bed, initial, valid, blocked, reservoir, cfg)
        solver.impose_stage(base_stage + offset)
        directory.mkdir(parents=True, exist_ok=True)
        profile.update(dtype="float32", count=1, nodata=0, compress="deflate")
        frames, maximum = [], np.zeros_like(ground, dtype="float32")
        write_seconds = 0.0
        def save_frame(step):
            nonlocal write_seconds
            output_started = time.perf_counter()
            elapsed = step * cfg.dt_s
            rise = river_plus_m * min(1, elapsed / cfg.river_ramp_s)
            depth = np.where(solver.depth > 1e-6, np.maximum(0, solver.bed + solver.depth-ground), 0).astype("float32")
            np.maximum(maximum, depth, out=maximum)
            timestamp = time_zero + timedelta(seconds=elapsed)
            name = f'depth_{timestamp.strftime("%Y%m%dT%H%M%S")}_{step}_{revision}.tif'
            temporary = directory / (name + ".tmp")
            with rasterio.open(temporary, "w", **profile) as target: target.write(depth, 1)
            temporary.replace(directory / name)
            frames.append({"time_ist": timestamp.isoformat(), "elapsed_s": elapsed, "level_m": base_stage+rise,
                           "water_surface_m": base_stage+offset+rise, "interpolated": False,
                           "stage_kind": "simulated stage boundary: held sourced baseline" + (" + hypothetical ramp" if river_plus_m else ""),
                           "depth_file": name})
            write_seconds += time.perf_counter()-output_started
        save_frame(0)
        output_steps = round(cfg.output_interval_s/cfg.dt_s)
        steps = round(cfg.duration_s/cfg.dt_s)
        initial_write_seconds = write_seconds
        simulation_started = time.perf_counter()
        for step in range(1, steps+1):
            rise = river_plus_m * min(1, step*cfg.dt_s/cfg.river_ramp_s)
            solver.step(base_stage+offset+rise)
            if step % output_steps == 0 or step == steps: save_frame(step)
        simulation_seconds = time.perf_counter()-simulation_started-(write_seconds-initial_write_seconds)
        balance = solver.water_balance()
        if balance["relative_residual"] > 1e-8:
            raise FloatingPointError("Water balance failed; no completed run manifest will be published")
        result = {"model": MODEL, "model_version": MODEL_VERSION, "simulated": True, "hydraulically_validated": False,
                  "iou": None, "precision": None, "recall": None, "score_caveat": "Counterfactual scenario; satellite overlap is scored only for the unchanged static 2023 replay.",
                  "flooded_area_km2": float(np.count_nonzero(maximum > .01)*CELL_SIZE**2/1e6),
                  **_exposure(maximum, ref), "reported_places_hits": None, "water_balance": balance,
                  "simulation_wall_time_s": max(0, simulation_seconds), "output_write_wall_time_s": write_seconds,
                  "runtime_s": time.perf_counter()-started, "runtime_host": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()},
                  "roads_closed_definition": "Mapped road ways touching simulated depth >0.3 m; not official road closure reports.",
                  "buildings_wet_definition": "Mapped footprints touching an adjacent simulated depth >0.5 m.",
                  "area_definition": "Maximum over saved scenario frames, depth >0.01 m above original ground.",
                  "parameters": asdict(cfg), "parameters_caveat": "Manning n and hypothetical edits are explicit modelling assumptions, not observed/calibrated hydraulic parameters."}
        write_json(directory / "metrics.json", result)
        write_json(directory / "manifest.json", {"run_id": run_id, "calibrated": False, "datum_calibrated": True,
                   "model": MODEL, "model_version": MODEL_VERSION, "frames": frames, "edits": edits or [], "river_plus_m": river_plus_m,
                   "configuration_hash": config_hash, "input_sha256": checksums, "parameters": asdict(cfg),
                   "initial_condition": {"run_id": "replay2023", "depth_file": filename, "time_ist": frame["time_ist"], "level_m": base_stage},
                   "boundary_condition": "All real pre-flood main-river mask cells, excluding edited/blocked cells, held at sourced baseline stage plus optional simulated ramp; AOI edges closed.",
                   "time_basis": "Hypothetical elapsed time after the chosen static replay frame, not an observed hydrograph.",
                   "limitations": "Local-inertial shallow-water approximation: no advective momentum, surveyed bathymetry, drainage, rainfall or calibrated roughness. Breach wave arrival times are not validated forecasts.",
                   "references": REFERENCES, "created_at": datetime.now(timezone.utc).isoformat()})
    return run_id


def precompute(config=None):
    definitions = scenario_definitions()
    if config is not None:
        config.validate()
        definitions["config"] = asdict(config)
    write_json(DERIVED / "what_if_scenarios.json", definitions)
    missing = requirements()
    if missing: raise ValueError("Scenario definitions saved, but no flood runs created. Missing real inputs: " + ", ".join(missing))
    results = []
    for scenario in definitions["scenarios"]:
        run_id = compute_dynamic_run(scenario["run_id"], scenario["edits"], scenario["river_plus_m"], config)
        metrics = read_json(RUNS / run_id / "metrics.json")
        results.append({"run_id": run_id, "runtime_s": metrics["runtime_s"], "water_balance": metrics["water_balance"]})
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Real-input dynamic what-if scenarios; never replaces the static replay")
    parser.add_argument("command", choices=("precompute", "definitions", "check"))
    args = parser.parse_args()
    try:
        if args.command == "definitions":
            payload = scenario_definitions(); write_json(DERIVED / "what_if_scenarios.json", payload)
            print("Saved simulated scenario definitions derived from real mapped features.")
        elif args.command == "check":
            missing = requirements(); print(json.dumps({"ready": not missing, "missing": missing}, indent=2))
        else: print(json.dumps(precompute(), indent=2))
    except (ValueError, OSError, FloatingPointError) as exc: parser.exit(2, str(exc) + "\n")
