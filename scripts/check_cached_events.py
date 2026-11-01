"""Read-only validation of published, source-current product event playback.

No model imports, network calls, workers, data generation or directory traversal.
Only allowlisted files explicitly named by the product catalog/manifests are read.
"""
from __future__ import annotations

import argparse
from array import array
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
MODULES = {"flood", "breach", "quake", "fire", "chain"}
ASSET = re.compile(r"/data/(?:buildings\.json|product/runs/product_[0-9a-f]{16}/(?:manifest\.json|(?:metrics|response)(?:_[0-9]{4})?\.json|(?:depth|severity|building_states)_[0-9]{4}\.bin))\Z")
SEVERITY = {0, 1, 2, 3, 4, 255}
STATES = {0, 1, 2, 3, 4, 5, 6, 255}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def asset_path(public: Path, url: str) -> Path:
    require(isinstance(url, str) and ASSET.fullmatch(url), f"Unapproved asset URL: {url!r}")
    path = public / url.removeprefix("/data/")
    cursor = public
    require(not cursor.is_symlink(), "Public data root must not be a symlink")
    for part in path.relative_to(public).parts:
        cursor = cursor / part
        require(not cursor.is_symlink(), f"Symlink is not an audit input: {url}")
    require(path.is_file(), f"Missing asset: {url}")
    return path


def read_json(path: Path):
    def invalid(value):
        raise ValueError(f"Non-finite JSON value in {path.name}: {value}")
    def finite_float(value):
        number = float(value)
        return number if math.isfinite(number) else invalid(value)
    return json.loads(path.read_text(), parse_constant=invalid, parse_float=finite_float)


def digest(path: Path):
    with path.open("rb") as stream:
        result = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def local_urls(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from local_urls(item)
    elif isinstance(value, list):
        for item in value:
            yield from local_urls(item)
    elif isinstance(value, str) and value.startswith("/data/"):
        yield value


def binary(path: Path, kind: str, count: int):
    raw = path.read_bytes()
    expected = count * (4 if kind == "depth" else 2 if kind == "building_states" else 1)
    require(len(raw) == expected, f"{path.name}: {len(raw)} bytes, expected {expected}")
    if kind == "depth":
        values = array("f")
        values.frombytes(raw)
        if sys.byteorder != "little":
            values.byteswap()
        require(all(math.isfinite(v) and v >= 0 for v in values), f"{path.name}: invalid depth")
    elif kind == "building_states":
        require(set(raw[::2]) <= SEVERITY and set(raw[1::2]) <= STATES, f"{path.name}: invalid building codes")
    else:
        require(set(raw) <= SEVERITY, f"{path.name}: invalid severity codes")
    return raw


def metric_summary(metrics):
    names = ("time_s", "affected_area_km2", "buildings_per_band", "buildings_unknown",
             "roads_closed", "people_affected", "people_in_known_cells",
             "affected_population_coverage", "burning_buildings", "burnt_buildings", "response")
    return {name: metrics[name] for name in names if name in metrics}


def validate_response(response, expected_time):
    require(response["time_s"] == expected_time, "Response time differs from frame")
    units = response["units"]
    require(response["metrics"]["units_dispatched"] == len(units), "Response unit count mismatch")
    require(all(count <= 1 for count in Counter(u["type"] for u in units).values()), "More than one assumed unit of a type")
    require(response["metrics"]["stations_cut_off"] == sum(bool(s["cut_off"]) for s in response["stations"]), "Station cut-off count mismatch")
    physical_edges = {edge.rsplit(":", 1)[0] for edge in response["closed_edge_ids"]}
    require(response["metrics"]["roads_closed"] == len(physical_edges), "Closed road count mismatch")
    for unit in units:
        require(unit.get("modelled") is True, "Unit lacks modelled label")
        path, times = unit["path"], unit["path_times_s"]
        require(len(path) == len(times) and len(path) > 0, "Route shape/timing mismatch")
        require(all(len(point) == 2 and all(math.isfinite(v) for v in point) for point in path), "Invalid route coordinates")
        require(times[0] == 0 and all(math.isfinite(v) and v >= 0 for v in times), "Invalid elapsed route times")
        require(all(a <= b for a, b in zip(times, times[1:])), "Route times are not monotonic")
        require(math.isfinite(unit["eta_s"]) and unit["eta_s"] >= 0, "Invalid ETA")
        require(math.isfinite(unit["departure_time_s"]), "Invalid departure time")


def audit_run(public, record, index):
    manifest_path = asset_path(public, record["manifest_url"])
    manifest = read_json(manifest_path)
    run_id = record["run_id"]
    for key in ("run_id", "preset_id", "module"):
        require(manifest[key] == record[key], f"{run_id}: catalog/manifest {key} differs")
    require(manifest["schema_version"] == 1 and manifest["hypothetical"] is True, "Unexpected model schema/label")
    for key in ("count", "sha256"):
        require(manifest["building_index"][key] == index[key], f"{run_id}: building index {key} differs")
    require(manifest["building_states"]["stride"] == 2, "Unsupported building stride")
    require(manifest["building_states"]["count"] == index["count"], "Building state count differs")
    cells = manifest["grid"]["width"] * manifest["grid"]["height"]
    require(cells > 0 and manifest["grid"]["cell_size_m"] > 0, "Invalid grid")
    urls = set(local_urls(manifest)) | {record["manifest_url"]}
    assets = {url: asset_path(public, url) for url in urls}
    frames = manifest["frames"]
    require(len(frames) > 1, f"{run_id}: no playback sequence")
    times = [f["time_s"] for f in frames]
    require(all(math.isfinite(t) and t >= 0 for t in times), "Invalid simulated times")
    require(all(a < b for a, b in zip(times, times[1:])), "Frame times do not increase")
    endpoints = {}
    metric_endpoints = []
    for i, frame in enumerate(frames):
        for kind in ("depth", "severity", "building_states"):
            key = kind + "_url"
            if key not in frame:
                continue
            raw = binary(assets[frame[key]], kind, index["count"] if kind == "building_states" else cells)
            if i in (0, len(frames) - 1):
                endpoints.setdefault(kind, {})[i] = raw
        metrics = read_json(assets[frame["metrics_url"]])
        response = read_json(assets[frame["response_url"]])
        require(metrics["run_id"] == run_id and metrics["time_s"] == frame["time_s"], "Frame metrics identity mismatch")
        require(len(metrics["buildings_per_band"]) == 5, "Expected five building bands")
        require(sum(metrics["buildings_per_band"]) + metrics["buildings_unknown"] == index["unique_entity_count"], "Unique building count mismatch")
        raw_states = binary(assets[frame["building_states_url"]], "building_states", index["count"])
        require([raw_states[::2].count(band) for band in range(5)] == metrics["building_parts_per_band"], "Building metrics differ from binary states")
        validate_response(response, frame["time_s"])
        require(metrics["response"] == response["metrics"], "Metrics and response disagree")
        coverage = metrics.get("affected_population_coverage")
        if coverage is not None and coverage < 1 - 1e-12:
            require(metrics["people_affected"] is None, "Partial population coverage misrepresented as complete")
        if i in (0, len(frames) - 1):
            metric_endpoints.append(metric_summary(metrics))
    summary = read_json(assets[manifest["metrics_url"]])
    final_response = read_json(assets[manifest["response_url"]])
    validate_response(final_response, times[-1])
    require(summary["run_id"] == run_id and summary["frames"] == len(frames), "Summary identity/count differs")
    changes = {}
    for kind, values in endpoints.items():
        first, last = values.get(0), values.get(len(frames) - 1)
        if first is None or last is None:
            changes[kind] = {"changed_records": None, "present_at_start": first is not None, "present_at_end": last is not None}
            continue
        step = 4 if kind == "depth" else 2 if kind == "building_states" else 1
        changed = sum(first[i:i+step] != last[i:i+step] for i in range(0, len(first), step))
        changes[kind] = {"changed_records": changed, "first_sha256": hashlib.sha256(first).hexdigest(), "last_sha256": hashlib.sha256(last).hexdigest()}
    require(any(change["changed_records"] for change in changes.values()), f"{run_id}: identical start/end event")
    return {"run_id": run_id, "preset_id": record["preset_id"], "module": record["module"],
            "passed": True, "frames": len(frames), "simulation_start_s": times[0], "simulation_end_s": times[-1],
            "generation_runtime_s": manifest["runtime_s"], "grid": manifest["grid"],
            "referenced_assets_checked": len(assets), "run_asset_bytes": sum(p.stat().st_size for p in assets.values() if p != public / "buildings.json"),
            "first": metric_endpoints[0], "last": metric_endpoints[-1], "summary": metric_summary(summary),
            "start_end_changes": changes, "scenario": manifest["scenario"], "labels": manifest["labels"],
            "terrain_edit": manifest.get("terrain_edit"), "counterfactual_comparison": summary.get("counterfactual_comparison")}


def audit(public: Path):
    started = time.perf_counter()
    catalog_path = public / "product" / "catalog.json"
    require(not catalog_path.is_symlink(), "Catalog must not be a symlink")
    catalog = read_json(catalog_path)
    index = catalog["building_index"]
    require(digest(asset_path(public, index["url"])) == index["sha256"], "Published buildings SHA256 differs from index")
    current = [r for r in catalog["runs"] if r.get("source_current") is True and r["module"] in MODULES]
    require({r["module"] for r in current} == MODULES, "A required cached event module is absent")
    results = []
    for record in current:
        try:
            results.append(audit_run(public, record, index))
        except (ValueError, KeyError, OSError, TypeError) as error:
            results.append({"run_id": record["run_id"], "module": record["module"], "passed": False, "error": str(error)})
    comparisons = []
    baseline = next((r for r in results if r["passed"] and r["preset_id"] == "river_plus_3m"), None)
    cut = next((r for r in results if r["passed"] and r["preset_id"] == "breach_near_ito"), None)
    if baseline and cut and baseline["grid"] == cut["grid"] and baseline["simulation_end_s"] == cut["simulation_end_s"]:
        end_frames = []
        for run in (baseline, cut):
            manifest = read_json(asset_path(public, f"/data/product/runs/{run['run_id']}/manifest.json"))
            end_frames.append(manifest["frames"][-1])
        depths = []
        for frame in end_frames:
            values = array("f")
            values.frombytes(asset_path(public, frame["depth_url"]).read_bytes())
            if sys.byteorder != "little":
                values.byteswap()
            depths.append(values)
        differences = [abs(a-b) for a, b in zip(*depths)]
        comparisons.append({"baseline_run_id": baseline["run_id"], "cut_run_id": cut["run_id"],
                            "scope": "Independent comparison of exported 40m maximum-depth viewer cells, not native 10m model cells",
                            "changed_depth_cells_gt_1mm": sum(v > .001 for v in differences),
                            "maximum_abs_depth_difference_m": max(differences),
                            "affected_area_difference_km2": cut["last"]["affected_area_km2"] - baseline["last"]["affected_area_km2"]})
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "scope": "Published source-current cached playback files; no simulation or empirical model validation",
            "catalog_sha256": digest(catalog_path), "building_index_sha256_verified": index["sha256"],
            "passed": all(r["passed"] for r in results), "runs": results, "comparisons": comparisons, "audit_runtime_s": time.perf_counter() - started}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-data", type=Path, default=ROOT / "web/public/data")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.public_data)
    if args.output:
        require(".env" not in args.output.name.lower() and not args.output.name.lower().endswith("env"), "Protected output name")
        require(not args.output.is_symlink(), "Output must not be a symlink")
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for run in report["runs"]:
        print(f"{run['module']}: {'PASS' if run['passed'] else 'FAIL'} {run['run_id']} " + (f"{run['frames']} frames, t={run['simulation_start_s']:g}..{run['simulation_end_s']:g}s, {run['referenced_assets_checked']} assets" if run["passed"] else run["error"]))
    print(f"Audit {'passed' if report['passed'] else 'failed'} in {report['audit_runtime_s']:.2f}s; no models or workers started.")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
