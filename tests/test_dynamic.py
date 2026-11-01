"""Isolated numerical fixtures only; never publish fabricated flood rasters."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin

from sim.dynamic import DynamicConfig, VirtualPipes, compute_dynamic_run


class DynamicTests(unittest.TestCase):
    def config(self, **changes):
        return DynamicConfig(**{"cell_size_m": 10, "dt_s": .1, "duration_s": 20,
                               "output_interval_s": 1, **changes})

    def assert_balanced(self, solver):
        self.assertLess(solver.water_balance()["relative_residual"], 1e-12)
        self.assertGreaterEqual(float(solver.depth.min()), 0)
        self.assertTrue(np.isfinite(solver.depth).all())

    def test_dry_grid_stays_exactly_dry(self):
        bed = np.arange(81, dtype=float).reshape(9, 9) / 10
        solver = VirtualPipes(bed, np.zeros_like(bed), config=self.config())
        for _ in range(100): solver.step()
        np.testing.assert_array_equal(solver.depth, 0)
        np.testing.assert_array_equal(solver.qx, 0)
        np.testing.assert_array_equal(solver.qy, 0)
        self.assert_balanced(solver)

    def test_lake_at_rest_across_wet_dry_topography(self):
        bed = np.tile(np.linspace(0, 2, 17), (13, 1))
        initial = np.maximum(1-bed, 0)
        solver = VirtualPipes(bed, initial, config=self.config())
        for _ in range(200): solver.step()
        np.testing.assert_array_equal(solver.depth, initial)
        np.testing.assert_array_equal(solver.qx, 0)
        self.assert_balanced(solver)

    def test_closed_basin_conserves_and_spreads_one_face_per_step(self):
        initial = np.zeros((15, 15)); initial[7, 7] = 2
        solver = VirtualPipes(np.zeros_like(initial), initial, config=self.config())
        solver.step()
        wet = set(map(tuple, np.argwhere(solver.depth > 0)))
        self.assertEqual(wet, {(7, 7), (6, 7), (8, 7), (7, 6), (7, 8)})
        self.assertEqual(solver.depth[7, 9], 0)
        solver.step()
        self.assertGreater(solver.depth[7, 9], 0)
        for _ in range(498): solver.step()
        self.assert_balanced(solver)
        self.assertAlmostEqual(solver.volume_m3, 200, places=10)

    def test_obstacle_blocks_until_a_real_open_face_exists(self):
        bed = np.zeros((9, 13)); initial = np.zeros_like(bed); initial[:, :4] = 1
        blocked = np.zeros_like(bed, bool); blocked[:, 6] = True
        sealed = VirtualPipes(bed, initial, blocked=blocked, config=self.config())
        blocked[4, 6] = False
        breached = VirtualPipes(bed, initial, blocked=blocked, config=self.config())
        for _ in range(300):
            sealed.step(); breached.step()
        self.assertEqual(float(sealed.depth[:, 7:].sum()), 0)
        self.assertGreater(float(breached.depth[:, 7:].sum()), 0)
        self.assert_balanced(sealed); self.assert_balanced(breached)

    def test_explicit_reservoir_addition_and_removal_are_accounted(self):
        bed = np.zeros((9, 11)); boundary = np.zeros_like(bed, bool); boundary[:, 0] = True
        solver = VirtualPipes(bed, np.zeros_like(bed), boundary=boundary, config=self.config())
        for step in range(200): solver.step(stage_m=min(1, step / 100))
        self.assertGreater(solver.boundary_added_m3, 0)
        self.assertGreater(solver.depth[:, 1:].sum(), 0)
        solver.step(stage_m=0)
        self.assertGreater(solver.boundary_removed_m3, 0)
        self.assert_balanced(solver)

    def test_outflow_limiter_prevents_overdraining(self):
        bed = np.zeros((5, 5)); initial = np.zeros_like(bed); initial[2, 2] = .01
        solver = VirtualPipes(bed, initial, config=self.config(manning_n=0))
        # An oversized inherited flux exercises donor-volume limiting independently
        # of the gravity-wave condition; this is never a production initialization.
        solver.qx[2, 2] = 100; solver.qy[2, 2] = 100
        solver.step()
        self.assertEqual(solver.limited_steps, 1)
        self.assert_balanced(solver)

    def test_repeated_runs_are_bitwise_deterministic(self):
        bed = np.tile(np.linspace(.3, 0, 21), (11, 1))
        initial = np.zeros_like(bed); initial[4:7, 2:5] = 1
        one, two = [VirtualPipes(bed, initial, config=self.config()) for _ in range(2)]
        for _ in range(150): one.step(); two.step()
        np.testing.assert_array_equal(one.depth, two.depth)
        np.testing.assert_array_equal(one.qx, two.qx)
        np.testing.assert_array_equal(one.qy, two.qy)
        self.assertEqual(one.water_balance(), two.water_balance())

    def test_fixed_step_fails_before_unstable_transport(self):
        solver = VirtualPipes(np.zeros((3, 3)), np.ones((3, 3)),
                              config=self.config(dt_s=2, output_interval_s=2))
        with self.assertRaisesRegex(ValueError, "smaller fixed dt"):
            solver.step()
        self.assertEqual(solver.steps, 0)
        np.testing.assert_array_equal(solver.depth, 1)

    def test_no_missing_input_run_or_static_replay_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("sim.dynamic.RUNS", Path(directory)), patch("sim.dynamic.requirements", return_value=["real Sentinel-1 mask"]):
                with self.assertRaisesRegex(ValueError, "Missing real dynamic inputs"):
                    compute_dynamic_run("river_plus_1m", river_plus_m=1)
                with self.assertRaisesRegex(ValueError, "may not overwrite"):
                    compute_dynamic_run("replay2023")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_temporary_run_contract_frames_labels_balance_and_cache(self):
        # This small mathematical fixture lives only in a temporary directory.
        # It exercises the writer, never the real AOI or project run directories.
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); derived = base / "data" / "derived"; runs = base / "runs"
            derived.mkdir(parents=True); replay = runs / "replay2023"; replay.mkdir(parents=True)
            zero = np.zeros((5, 9), dtype="float32"); initial = zero.copy(); initial[:, 0] = 1
            profile = dict(driver="GTiff", width=9, height=5, count=1, dtype="float32", crs="EPSG:32643", transform=from_origin(0, 50, 10, 10))
            for path, values in [(derived / name, values) for name, values in (
                ("ground_10m.tif", zero), ("surface_10m.tif", zero), ("building_mask.tif", zero),
                ("embankment_mask.tif", zero), ("aoi_mask.tif", zero+1), ("s1_water_before.tif", initial))
            ] + [(replay / "depth_20230101T000000.tif", initial)]:
                with rasterio.open(path, "w", **profile) as target: target.write(values, 1)
            (derived / "datum_offset.json").write_text(json.dumps({"offset_m": 0}))
            (derived / "stage_2023.csv").write_text("isolated mathematical fixture; not an observed stage")
            (replay / "manifest.json").write_text(json.dumps({"frames": [{"level_m": 1, "time_ist": "2023-01-01T00:00:00+05:30", "depth_file": "depth_20230101T000000.tif"}]}))
            with patch("sim.dynamic.RUNS", runs), patch("sim.dynamic.DERIVED", derived), patch("sim.dynamic.requirements", return_value=[]):
                cfg = self.config(duration_s=2, output_interval_s=.5, river_ramp_s=1)
                compute_dynamic_run("river_plus_1m", river_plus_m=1, config=cfg)
                output = runs / "river_plus_1m"
                manifest = json.loads((output / "manifest.json").read_text())
                metrics = json.loads((output / "metrics.json").read_text())
                self.assertEqual(len(manifest["frames"]), 5)
                self.assertEqual(len({f["depth_file"] for f in manifest["frames"]}), 5)
                self.assertTrue(all("simulated" in f["stage_kind"] for f in manifest["frames"]))
                self.assertFalse(manifest["calibrated"])
                self.assertIsNone(metrics["iou"])
                self.assertIsNone(metrics["buildings_wet"])
                self.assertLess(metrics["water_balance"]["relative_residual"], 1e-12)
                self.assertGreater(metrics["runtime_s"], 0)
                with rasterio.open(output / manifest["frames"][-1]["depth_file"]) as source:
                    final = source.read(1)
                self.assertGreater(final[:, 1:].sum(), 0)
                written = (output / "manifest.json").stat().st_mtime_ns
                compute_dynamic_run("river_plus_1m", river_plus_m=1, config=cfg)
                self.assertEqual((output / "manifest.json").stat().st_mtime_ns, written)


if __name__ == "__main__":
    unittest.main()
