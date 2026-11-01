"""Checks real exports and isolated in-memory math; creates no demo flood outputs."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
from fastapi.testclient import TestClient

from sim.api import app
from sim.config import DERIVED, WEB_DATA
from sim.replay import connected_water, hourly_stages, overlap
from sim import export_web
from sim.context import median_height
from shapely.geometry import Polygon, shape
from rasterio.io import MemoryFile
from rasterio.transform import from_origin


class PipelineTests(unittest.TestCase):
    def test_exported_heights_round_trip(self):
        with rasterio.open(DERIVED / "ground_10m.tif") as source:
            metadata = json.loads((WEB_DATA / "terrain.json").read_text())
            values = np.fromfile(WEB_DATA / "terrain.bin", dtype="<f4").reshape(source.shape)
            np.testing.assert_array_equal(values, source.read(1))
            self.assertEqual(metadata["origin"], [source.transform.c, source.transform.f])
            self.assertEqual(source.crs.to_epsg(), 32643)
            self.assertEqual((source.transform.a, source.transform.e), (10, -10))
            self.assertTrue(np.isfinite(values).all())
            for name in ("dem_10m.tif", "surface_10m.tif", "embankment_mask.tif", "building_mask.tif", "aoi_mask.tif"):
                with rasterio.open(DERIVED / name) as other:
                    self.assertEqual(other.shape, source.shape)
                    self.assertEqual(other.crs, source.crs)
                    self.assertEqual(other.transform, source.transform)
            with rasterio.open(DERIVED / "surface_10m.tif") as surface:
                self.assertTrue(np.all(surface.read(1) >= values))

    def test_unknown_buildings_stay_unknown(self):
        path = DERIVED / "buildings_merged.geojson"
        source = json.loads((path if path.is_file() else DERIVED / "osm_buildings.geojson").read_text())
        exported = json.loads((WEB_DATA / "buildings.json").read_text())
        by_id = {(f["properties"].get("osm_id") or f["properties"].get("id")): f["properties"]["height"] for f in source["features"]}
        self.assertGreater(len(by_id), 0)
        for building in exported["buildings"]:
            self.assertEqual(building["height"], by_id[building["id"]])
            self.assertTrue(np.isfinite(building["ground_height_m"]))

    def test_real_mapped_context_contract(self):
        metadata = json.loads((WEB_DATA / "terrain.json").read_text())
        landcover = np.fromfile(WEB_DATA / "landcover.bin", dtype="uint8")
        context = json.loads((WEB_DATA / "context.json").read_text())
        self.assertEqual(landcover.size, metadata["width"] * metadata["height"])
        self.assertEqual(int(np.count_nonzero(landcover & 1)), context["water_cells"])
        self.assertEqual(int(np.count_nonzero(landcover & 2)), context["green_cells"])
        self.assertGreater(context["water_cells"], 0)
        self.assertGreater(context["green_cells"], 0)
        self.assertTrue(any(road["bridge"] for road in context["roads"]))
        self.assertTrue(all(road["width_m"] > 0 and len(road["points"]) >= 2 for road in context["roads"]))
        self.assertEqual(context["water_label"], "Real: OpenStreetMap river outline")

    def test_footprint_median_excludes_holes_and_missing_pixels(self):
        # Isolated building-height reducer check; never written to the project data.
        values = np.array([[2, 4, 6, 8], [10, 100, 100, 12], [14, 100, 100, 16], [-9999, 18, 20, 22]], dtype="float32")
        polygon = Polygon([(0,0),(4,0),(4,4),(0,4)], holes=[[(1,1),(3,1),(3,3),(1,3)]])
        with MemoryFile() as memory:
            with memory.open(driver="GTiff", width=4, height=4, count=1, dtype="float32", crs="EPSG:4326", transform=from_origin(0,4,1,1), nodata=-9999) as raster:
                raster.write(values,1)
                self.assertEqual(median_height(raster, polygon), 12)

    def test_connectivity_and_barrier(self):
        surface = np.zeros((7, 7)); seed = np.zeros_like(surface, dtype=bool); seed[3, 0] = True
        blocked = np.zeros_like(seed); blocked[:, 3] = True; valid = np.ones_like(seed)
        closed = connected_water(surface, seed, 1, blocked, valid)
        self.assertTrue(closed[:, :3].all()); self.assertFalse(closed[:, 4:].any())
        blocked[3, 3] = False
        opened = connected_water(surface, seed, 1, blocked, valid)
        self.assertTrue(opened[:, 4:].all())
        np.testing.assert_array_equal(opened, connected_water(surface, seed, 1, blocked, valid))
        self.assertFalse(connected_water(surface, seed, -1, blocked, valid).any())

    def test_stage_interpolation_does_not_extrapolate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stage.csv"
            path.write_text("time_ist,level_m,source_url,verified\n2023-07-13T08:00:00+05:30,208.0,https://example.invalid/test,true\n2023-07-13T10:00:00+05:30,209.0,https://example.invalid/test,true\n")
            frames = hourly_stages(path)
        self.assertEqual(len(frames), 3)
        self.assertEqual([r["interpolated"] for r in frames], [False, True, False])
        self.assertEqual(frames[1]["level_m"], 208.5)

    def test_metrics_math_and_missing_truth(self):
        predicted = np.array([[1, 1], [0, 0]], dtype=bool)
        truth = np.array([[1, 0], [1, 0]], dtype=bool)
        scores = overlap(predicted, truth, np.ones_like(truth))
        self.assertAlmostEqual(scores["iou"], 1/3)
        self.assertEqual(scores["precision"], .5)
        self.assertEqual(scores["recall"], .5)
        self.assertIsNone(overlap(truth & False, truth & False, truth | True)["iou"])

    def test_new_export_path_changes_signature_and_invalid_files_are_not_truth(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch.object(export_web, "DERIVED", base), patch.object(export_web, "RUNS", base / "runs"):
                before = export_web.inputs_signature()
                (base / "s1_water_before.tif").write_bytes(b"incomplete export, not a raster")
                self.assertNotEqual(before, export_web.inputs_signature())
                with rasterio.open(DERIVED / "ground_10m.tif") as reference:
                    result = export_web.export_mask("s1_water_before", reference, {})
                self.assertFalse(result["available"])
                self.assertIn("Cannot read", result["reason"])

    def test_api_refuses_missing_inputs_and_invalid_edit(self):
        client = TestClient(app)
        with patch("sim.api.requirements", return_value=["s1_water_before.tif"]):
            response = client.post("/run", json={"edits": []})
        self.assertEqual(response.status_code, 409)
        self.assertIn("s1_water_before.tif", response.json()["detail"])
        self.assertEqual(client.post("/run", json={"edits": [{"type": "debris", "coordinates": [[0, 0], [1, 1]], "height_m": 2}]}).status_code, 422)
        self.assertEqual(client.get("/runs/not_a_known_run/metrics").status_code, 404)


if __name__ == "__main__": unittest.main()
