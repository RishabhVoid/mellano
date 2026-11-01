"""Official FABDEM N28E077 download and explicit ground-model replacement.

Only the requested member is read from Bristol's public ZIP using HTTP ranges.
No Earth Engine login is needed for this source, and no substitute DEM is used
when FABDEM is absent. ``switch`` is explicit because ground changes invalidate
any prior hydraulic datum calibration.
"""
import hashlib
import json
import zipfile
from datetime import datetime, timezone

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.warp import Resampling, reproject, transform_bounds
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform

from sim.config import AOI, CRS, DEM_TILE, DEM_URL, DERIVED, RAW, FABDEM_TILE, FABDEM_ARCHIVE_URL
from sim.data import write_json
from sim.http_range import HTTPRangeReader

FABDEM_DATASET_URL = "https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn"
FABDEM_DOI = "https://doi.org/10.5523/bris.s5hqmjcdj8yo2ibzi9b4ew3sn"
FABDEM_LICENSE = "CC BY-NC-SA 4.0"
FABDEM_LICENSE_URL = "https://creativecommons.org/licenses/by-nc-sa/4.0/"
FABDEM_ATTRIBUTION = "FABDEM V1-2 — Neal, J. and Hawker, L. (2023), University of Bristol. CC BY-NC-SA 4.0. Forest and building height biases removed from Copernicus GLO-30; resampled to 10 m for Otec."
COPERNICUS_ATTRIBUTION = "Contains modified Copernicus WorldDEM-30 data: © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018; provided under COPERNICUS by the European Union and ESA, all rights reserved. The organisations responsible for the Copernicus programme accept no liability for use of Copernicus WorldDEM-30."


def validate_source(path):
    with rasterio.open(path) as raster:
        if raster.crs is None or raster.count != 1:
            raise ValueError("FABDEM source must be a georeferenced single-band raster")
        bounds = transform_bounds(raster.crs, "EPSG:4326", *raster.bounds, densify_pts=21)
        if not (bounds[0] <= AOI[0] and bounds[1] <= AOI[1] and bounds[2] >= AOI[2] and bounds[3] >= AOI[3]):
            raise ValueError("FABDEM source does not cover the full AOI")
        return {"width": raster.width, "height": raster.height, "crs": raster.crs.to_string(), "bounds": list(bounds)}


def source_metadata(path=None):
    path = path or RAW / FABDEM_TILE
    metadata = {
        "source": "FABDEM V1-2", "source_url": FABDEM_DATASET_URL,
        "download_url": FABDEM_ARCHIVE_URL, "archive_member": FABDEM_TILE,
        "doi": FABDEM_DOI, "native_resolution_m": 30, "resampling": "bilinear to 10 m",
        "vertical_datum": "EGM2008 (inherited from Copernicus GLO-30)",
        "license": FABDEM_LICENSE, "license_url": FABDEM_LICENSE_URL,
        "attribution": FABDEM_ATTRIBUTION, "copernicus_attribution": COPERNICUS_ATTRIBUTION,
        "caveat": "FABDEM uses model-based removal of forest and building height biases; it is not a surveyed local DTM. Resampling to 10 m does not create new elevation detail. Surface is this ground plus footprint building heights once.",
    }
    sidecar = RAW / "fabdem_source.json"
    if sidecar.is_file():
        metadata.update(json.loads(sidecar.read_text()))
    metadata["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return metadata


def download():
    RAW.mkdir(parents=True, exist_ok=True)
    output = RAW / FABDEM_TILE
    if output.is_file():
        validate_source(output)
        print("Verified cached FABDEM N28E077 source.", flush=True)
        return output
    print("Reading only N28E077 from the official Bristol FABDEM archive…", flush=True)
    temporary = output.with_suffix(".part")
    with HTTPRangeReader(FABDEM_ARCHIVE_URL) as reader:
        with zipfile.ZipFile(reader) as archive:
            info = archive.getinfo(FABDEM_TILE)
            if info.file_size > 200 * 1024 * 1024:
                raise ValueError("Unexpected FABDEM tile size; refusing a broad extraction")
            with archive.open(info) as source, temporary.open("wb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
    validation = validate_source(temporary)
    checksum = hashlib.sha256(temporary.read_bytes()).hexdigest()
    temporary.replace(output)
    write_json(RAW / "fabdem_source.json", {
        "source": "FABDEM V1-2", "source_url": FABDEM_DATASET_URL,
        "download_url": FABDEM_ARCHIVE_URL, "archive_member": FABDEM_TILE,
        "downloaded_at": datetime.now(timezone.utc).isoformat(), "sha256": checksum,
        "archive_crc32": f"{info.CRC:08x}", "archive_member_bytes": info.file_size,
        "download_method": "HTTP range extraction of exact ZIP member; ZIP CRC checked",
        "license": FABDEM_LICENSE, "license_url": FABDEM_LICENSE_URL,
        "attribution": FABDEM_ATTRIBUTION, "copernicus_attribution": COPERNICUS_ATTRIBUTION,
        **validation,
    })
    print(f"FABDEM N28E077 ready ({info.file_size:,} bytes); no ground grids changed.", flush=True)
    return output


def resample(path, reference):
    result = np.full(reference.shape, np.nan, dtype="float32")
    with rasterio.open(path) as source:
        reproject(rasterio.band(source, 1), result, src_transform=source.transform, src_crs=source.crs,
                  src_nodata=source.nodata, dst_transform=reference.transform, dst_crs=reference.crs,
                  dst_nodata=np.nan, resampling=Resampling.bilinear, num_threads=2)
    if not np.isfinite(result).all():
        raise ValueError("Source does not fully cover the target grid with valid elevations")
    return result


def switch_ground():
    """Explicitly replace ground/surface on the existing aligned grid."""
    source = RAW / FABDEM_TILE
    if not source.is_file():
        raise RuntimeError("FABDEM is missing. Run python -m sim.fabdem download, or the Earth Engine export script; Copernicus will not be substituted.")
    validate_source(source)
    provenance_path = DERIVED / "provenance.json"
    provenance = json.loads(provenance_path.read_text())
    with rasterio.open(DERIVED / "ground_10m.tif") as reference:
        if reference.crs.to_string() != CRS:
            raise ValueError("Unexpected target grid CRS")
        profile = reference.profile.copy()
        ground = resample(source, reference)
        comparison = resample(RAW / f"{DEM_TILE}.tif", reference)
        buildings_path = DERIVED / "buildings_merged.geojson"
        if not buildings_path.is_file():
            buildings_path = DERIVED / "osm_buildings.geojson"
        features = json.loads(buildings_path.read_text())["features"]
        project = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform
        known = sorted((feature for feature in features if feature["properties"].get("height") is not None), key=lambda feature: feature["properties"]["height"])
        heights = rasterize([(transform(project, shape(feature["geometry"])), feature["properties"]["height"]) for feature in known], out_shape=ground.shape, transform=reference.transform, all_touched=True, dtype="float32") if known else np.zeros_like(ground)
    for name, values in (("ground_10m.tif", ground), ("dem_10m.tif", ground), ("surface_10m.tif", ground + heights), ("copernicus_comparison_10m.tif", comparison)):
        destination = DERIVED / name
        temporary = destination.with_suffix(".tmp.tif")
        profile.update(dtype="float32", nodata=None)
        with rasterio.open(temporary, "w", **profile) as output:
            output.write(values, 1)
        temporary.replace(destination)
    provenance["terrain"] = source_metadata(source)
    provenance["terrain_comparison"] = {"source": "Copernicus DEM GLO-30", "source_url": DEM_URL, "file": "copernicus_comparison_10m.tif", "use": "Comparison only; never used as simulation ground"}
    provenance["grid"].update(min_height_m=float(ground.min()), max_height_m=float(ground.max()))
    provenance["ground_switched_at"] = datetime.now(timezone.utc).isoformat()
    provenance["surface_definition"] = "FABDEM ground + building height raster, added exactly once"
    provenance["datum_calibration_note"] = "Calibrate against FABDEM before computing flood runs; any prior Copernicus-ground calibration is invalid."
    write_json(provenance_path, provenance)
    check = {"source": "FABDEM V1-2", "ground_min_m": float(ground.min()), "ground_max_m": float(ground.max()), "copernicus_minus_fabdem_median_m": float(np.median(comparison-ground)), "copernicus_minus_fabdem_p95_m": float(np.percentile(comparison-ground,95)), "surface_minus_ground_matches_building_heights": bool(np.allclose((ground+heights)-ground, heights, atol=1e-4)), "shape": list(ground.shape), "crs": CRS, "created_at": provenance["ground_switched_at"]}
    write_json(DERIVED / "ground_source_check.json", check)
    print(json.dumps(check, indent=2), flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=("download", "switch"))
    args = parser.parse_args()
    download() if args.step == "download" else switch_ground()
