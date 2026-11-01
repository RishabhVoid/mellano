"""Small isolated contract regressions; no production artifacts are generated."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

from scripts.check_cached_events import asset_path, audit_run, binary, read_json


class CachedEventAuditTests(unittest.TestCase):
    def test_unapproved_paths_are_rejected_before_filesystem_access(self):
        for url in ("/data/../private.json", "/data/.env", "/data/product/runs/product_a/manifest.json", "https://example.com/a.bin"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                asset_path(Path("/not-inspected"), url)

    def test_binary_size_and_nonfinite_depth(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "depth_0000.bin"
            path.write_bytes(struct.pack("<f", float("nan")))
            with self.assertRaisesRegex(ValueError, "invalid depth"):
                binary(path, "depth", 1)
            with self.assertRaisesRegex(ValueError, "expected 8"):
                binary(path, "depth", 2)

    def test_invalid_building_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "building_states_0000.bin"
            path.write_bytes(bytes([0, 7]))
            with self.assertRaisesRegex(ValueError, "invalid building codes"):
                binary(path, "building_states", 1)

    def test_json_rejects_nonfinite_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"
            for value in ("NaN", "1e309"):
                path.write_text('{"area": ' + value + '}')
                with self.assertRaisesRegex(ValueError, "Non-finite"):
                    read_json(path)

    def test_chain_may_introduce_depth_after_earthquake(self):
        with tempfile.TemporaryDirectory() as directory:
            public = Path(directory)
            run_id = "product_" + "a" * 16
            folder = public / "product" / "runs" / run_id
            folder.mkdir(parents=True)
            base = "/data/product/runs/" + run_id + "/"
            index = {"count": 1, "unique_entity_count": 1, "sha256": "fixture"}
            frames = []
            response_metrics = {"units_dispatched": 0, "stations_cut_off": 0, "roads_closed": 0}
            for i in range(2):
                frame = {"time_s": i * 10}
                for kind in ("severity", "building_states"):
                    name = f"{kind}_{i:04d}.bin"
                    frame[kind + "_url"] = base + name
                    (folder / name).write_bytes(bytes([i] * (2 if kind == "building_states" else 1)))
                metrics = {"run_id": run_id, "time_s": frame["time_s"], "buildings_per_band": [int(j == i) for j in range(5)], "building_parts_per_band": [int(j == i) for j in range(5)], "buildings_unknown": 0, "response": response_metrics}
                response = {"time_s": frame["time_s"], "units": [], "stations": [], "closed_edge_ids": [], "metrics": response_metrics}
                for kind, value in (("metrics", metrics), ("response", response)):
                    name = f"{kind}_{i:04d}.json"
                    frame[kind + "_url"] = base + name
                    (folder / name).write_text(json.dumps(value))
                if i:
                    frame["depth_url"] = base + "depth_0001.bin"
                    (folder / "depth_0001.bin").write_bytes(struct.pack("<f", 0.5))
                frames.append(frame)
            (folder / "metrics.json").write_text(json.dumps({"run_id": run_id, "frames": 2}))
            (folder / "response.json").write_text(json.dumps(response))
            manifest = {"run_id": run_id, "preset_id": "fixture", "module": "chain", "schema_version": 1, "hypothetical": True, "building_index": index, "building_states": {"stride": 2, "count": 1}, "grid": {"width": 1, "height": 1, "cell_size_m": 40}, "frames": frames, "metrics_url": base + "metrics.json", "response_url": base + "response.json", "runtime_s": 0, "scenario": {}, "labels": {}}
            (folder / "manifest.json").write_text(json.dumps(manifest))
            result = audit_run(public, {"run_id": run_id, "preset_id": "fixture", "module": "chain", "manifest_url": base + "manifest.json"}, index)
            self.assertTrue(result["passed"])
            self.assertEqual(result["start_end_changes"]["depth"], {"changed_records": None, "present_at_start": False, "present_at_end": True})
            self.assertEqual(result["start_end_changes"]["severity"]["changed_records"], 1)


if __name__ == "__main__":
    unittest.main()
