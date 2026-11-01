"""Gauge interpolation/provenance fixtures never publish project flood data."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

from sim.replay import calibrate, hourly_stages, satellite_overlap
from sim.stages import audited_rows, nearest_preflood_stage


class StageTests(unittest.TestCase):
    def test_satellite_nodata_is_not_observed_dry_land(self):
        observations = np.array([[1,np.nan],[0,np.nan]])
        predicted = np.array([[True,True],[False,True]])
        scores = satellite_overlap(predicted, observations, np.ones((2,2), bool))
        self.assertEqual(scores["iou"], 1)
        self.assertEqual(scores["precision"], 1)
        self.assertEqual(scores["recall"], 1)
        self.assertEqual(scores["satellite_valid_fraction_of_aoi"], .5)
        self.assertAlmostEqual(scores["satellite_evaluated_area_km2"], .0002)

    def interpolate(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stage.csv"
            keys = list(dict.fromkeys(key for row in rows for key in row))
            with path.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=keys); writer.writeheader(); writer.writerows(rows)
            return hourly_stages(path)

    def test_actual_audit_retains_ten_anchors_and_peak_ambiguity(self):
        rows = audited_rows(); frames = self.interpolate(rows)
        self.assertEqual(len(rows), 10)
        self.assertEqual(len(frames), 248)
        self.assertEqual(sum(frame["interpolated"] for frame in frames), 238)
        self.assertTrue(all("Interpolated" in frame["stage_kind"] for frame in frames if frame["interpolated"]))
        peak = next(frame for frame in frames if frame["time_ist"] == "2023-07-13T19:00:00+05:30")
        self.assertFalse(peak["interpolated"])
        self.assertEqual(peak["level_m"], 208.66)
        self.assertEqual(peak["time_uncertainty_minutes"], 60)
        self.assertEqual(peak["source_time_alternatives_ist"][0][-1], "2023-07-13T20:00:00+05:30")

    def test_uncertainty_only_propagates_to_adjacent_interpolated_intervals(self):
        frames = self.interpolate(audited_rows())
        by_time = {frame["time_ist"]: frame for frame in frames}
        self.assertFalse(by_time["2023-07-13T08:00:00+05:30"]["time_uncertain"])
        self.assertTrue(by_time["2023-07-13T09:00:00+05:30"]["time_uncertain"])
        self.assertTrue(by_time["2023-07-14T04:00:00+05:30"]["time_uncertain"])
        self.assertFalse(by_time["2023-07-14T05:00:00+05:30"]["time_uncertain"])
        self.assertEqual(len(by_time["2023-07-13T09:00:00+05:30"]["source_times_ist"]), 2)

    def test_unverified_values_do_not_become_anchors(self):
        rows = [{"time_ist": "2023-07-09T11:00:00+05:30", "level_m": 10, "source_url": "https://example.invalid/test", "verified": "true"},
                {"time_ist": "2023-07-09T12:00:00+05:30", "level_m": 999, "source_url": "https://example.invalid/forecast", "verified": "false"},
                {"time_ist": "2023-07-09T13:00:00+05:30", "level_m": 12, "source_url": "https://example.invalid/test", "verified": "true"}]
        frames = self.interpolate(rows)
        self.assertEqual([frame["level_m"] for frame in frames], [10,11,12])
        self.assertTrue(frames[1]["interpolated"])
        self.assertEqual(frames[1]["interpolation_gap_hours"], 2)

    def test_duplicate_observation_times_refused(self):
        row = audited_rows()[0]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.interpolate([row, row])

    def test_nearest_preflood_reading_uses_actual_acquisition_and_keeps_gap(self):
        early = nearest_preflood_stage({"before_dates": ["2023-07-04T12:55:20Z"]}, audited_rows())
        self.assertEqual(early["pre_flood_stage_m"], 203.14)
        self.assertEqual(early["pre_flood_stage_time_ist"], "2023-07-09T11:00:00+05:30")
        self.assertAlmostEqual(early["pre_flood_stage_gap_hours"], 112.577778)
        self.assertIn("not a time-matched", early["pre_flood_stage_kind"])
        later = nearest_preflood_stage({"before_dates": ["2023-07-12T06:00:00Z"]}, audited_rows())
        self.assertEqual(later["pre_flood_stage_m"], 207.55)
        self.assertAlmostEqual(later["pre_flood_stage_gap_hours"], 1.5)

    def test_date_only_acquisition_cannot_gain_an_invented_hour(self):
        with self.assertRaisesRegex(ValueError, "full timezone-aware"):
            nearest_preflood_stage({"before_dates": ["2023-07-04"]}, audited_rows())

    def test_time_gap_calibration_requires_explicit_authorization(self):
        meta = {"pre_flood_stage_m": 1, "pre_flood_stage_source_url": "https://example.invalid/test", "before_dates": ["2023-07-04T12:00:00Z"], "pre_flood_stage_gap_hours": 1}
        with patch("sim.replay.read_json", return_value=meta):
            with self.assertRaisesRegex(ValueError, "explicit authorization"):
                calibrate()


if __name__ == "__main__": unittest.main()
